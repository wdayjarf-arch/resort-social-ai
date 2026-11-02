"""Conservative outbound campaign adapters. Requires explicit consent and human approval.

Credentials are read from environment. Never log message bodies or access tokens.
"""
from __future__ import annotations
import json, os, time
from datetime import datetime, timezone
from typing import Any
import httpx

class DeliveryError(RuntimeError):
    def __init__(self, code: str, message: str = "Provider delivery failed"):
        super().__init__(message); self.code = code

class BaseAdapter:
    def send(self, recipient: dict[str, Any], message: str, campaign: dict[str, Any]) -> str:
        raise NotImplementedError

class WhatsAppCloudAdapter(BaseAdapter):
    """Official WhatsApp template messages only; campaign.template must be JSON template config."""
    def send(self, recipient, message, campaign):
        token = os.getenv("WHATSAPP_ACCESS_TOKEN"); phone_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
        if not token or not phone_id: raise DeliveryError("provider_not_configured")
        try: cfg = json.loads(campaign["template"])
        except Exception: raise DeliveryError("whatsapp_template_config_required", "WhatsApp requires a JSON template configuration")
        name = cfg.get("name"); language = cfg.get("language", "en_US")
        if not isinstance(name, str) or not name: raise DeliveryError("whatsapp_template_name_required")
        to = recipient.get("phone_e164")
        if not to: raise DeliveryError("missing_phone")
        components = cfg.get("components") or []
        # If template config includes {{message}} as the first body parameter, populate it.
        for component in components:
            if component.get("type") == "body" and component.get("parameters") == [{"type":"text","text":"{{message}}"}]:
                component["parameters"] = [{"type":"text", "text": message[:1024]}]
        payload = {"messaging_product":"whatsapp", "to":to, "type":"template", "template":{"name":name,"language":{"code":language},"components":components}}
        return _post_message(f"https://graph.facebook.com/{os.getenv('META_API_VERSION','v25.0')}/{phone_id}/messages", token, payload, "messages")

class MetaDirectAdapter(BaseAdapter):
    """Messenger/Instagram sends are allowed only inside 24h since last inbound.
    Outside that window this adapter suppresses; message tags are not used for marketing.
    """
    def send(self, recipient, message, campaign):
        channel = campaign["channel"]
        token = os.getenv("META_CAMPAIGN_PAGE_ACCESS_TOKEN")
        if not token: raise DeliveryError("provider_not_configured")
        last_inbound = recipient.get("last_inbound_at")
        if not last_inbound: raise DeliveryError("outside_messaging_window")
        if last_inbound.tzinfo is None: last_inbound = last_inbound.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - last_inbound).total_seconds()
        if age < 0 or age > 24 * 3600: raise DeliveryError("outside_messaging_window")
        psid = recipient.get("external_contact_id")
        if not psid: raise DeliveryError("missing_recipient_id")
        payload = {"recipient":{"id":psid},"message":{"text":message[:2000]}}
        # Marketing messages never use tags to bypass the window. For event updates, use a separate
        # reviewed transactional workflow; only allow explicitly configured non-marketing tags.
        tag = (campaign.get("segment") or {}).get("messaging_tag")
        if tag:
            if channel != "messenger" or tag != "CONFIRMED_EVENT_UPDATE" or (campaign.get("segment") or {}).get("marketing", True):
                raise DeliveryError("messaging_tag_not_allowed_for_marketing")
            payload["messaging_type"] = "MESSAGE_TAG"; payload["tag"] = tag
        endpoint = "https://graph.facebook.com/" + os.getenv("META_API_VERSION", "v25.0") + "/me/messages"
        return _post_message(endpoint, token, payload, "message_id")

class TelegramBotAdapter(BaseAdapter):
    def send(self, recipient, message, campaign):
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        if not token: raise DeliveryError("provider_not_configured")
        chat_id = recipient.get("external_contact_id")
        if not chat_id: raise DeliveryError("missing_telegram_chat_id")
        return _post_message(f"https://api.telegram.org/bot{token}/sendMessage", None,
                             {"chat_id":chat_id,"text":message[:4096],"disable_web_page_preview":True}, "message_id", telegram=True)

def _post_message(url: str, token: str | None, payload: dict, result_key: str, telegram: bool=False) -> str:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        with httpx.Client(timeout=20) as client: response = client.post(url, headers=headers, json=payload)
        if response.status_code == 429: raise DeliveryError("rate_limited")
        if response.status_code >= 500: raise DeliveryError("provider_temporary_error")
        if response.status_code >= 400: raise DeliveryError(f"provider_http_{response.status_code}")
        data = response.json()
        if telegram and not data.get("ok"): raise DeliveryError("telegram_api_error")
        if telegram: return str((data.get("result") or {}).get(result_key, "unknown"))
        if result_key == "messages": return str((data.get("messages") or [{}])[0].get("id", "unknown"))
        return str(data.get(result_key) or "unknown")
    except httpx.TimeoutException as exc: raise DeliveryError("provider_timeout_ambiguous") from exc
    except httpx.HTTPError as exc: raise DeliveryError("provider_network_error") from exc

def adapter_for(channel: str) -> BaseAdapter:
    if channel == "whatsapp": return WhatsAppCloudAdapter()
    if channel in {"messenger", "instagram"}: return MetaDirectAdapter()
    if channel == "telegram": return TelegramBotAdapter()
    raise DeliveryError("unsupported_channel")

def throttle(key: str, min_interval: float) -> None:
    """Cross-worker minimum interval using Redis SET NX; bounded wait and no busy spin."""
    import redis
    from app.core.config import get_settings
    client = redis.Redis.from_url(get_settings().redis_url, decode_responses=True, socket_timeout=2)
    deadline = time.monotonic() + max(5.0, min_interval + 3.0)
    while True:
        now = time.time(); ok = client.set(key, str(now + min_interval), nx=True, ex=max(2, int(min_interval) + 2))
        if ok: return
        next_time = client.get(key)
        wait = max(0.05, min_interval if not next_time else float(next_time) - now)
        if time.monotonic() + wait > deadline: raise DeliveryError("throttle_timeout")
        time.sleep(min(wait, 1.0))
