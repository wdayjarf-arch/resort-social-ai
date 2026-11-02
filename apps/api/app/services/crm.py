"""CRM helpers: conservative extraction, contact normalization, segments and vCards."""
import csv, io, json, re
import httpx
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr
from uuid import UUID
from sqlalchemy import select, or_
from sqlalchemy.orm import Session
from app.db.models.crm import CustomerLead

PHONE_RE = re.compile(r"(?<!\w)(\+?\d[\d\s().-]{6,}\d)(?!\w)")
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
INTERESTS = {"villa": "villas", "فيلا": "villas", "pool": "pools", "مسبح": "pools", "corporate": "corporate", "شركة": "corporate", "event": "events", "حفلة": "events", "مناسبة": "events"}

def normalize_phone(raw: str | None, default_region: str = "YE") -> str | None:
    if not raw: return None
    digits = re.sub(r"\D", "", raw)
    if raw.strip().startswith("+") and 8 <= len(digits) <= 15: return "+" + digits
    # Yemen numbers only; avoid guessing other countries from local numbers.
    if default_region == "YE":
        if digits.startswith("00967"): digits = digits[2:]
        if digits.startswith("967") and 11 <= len(digits) <= 15: return "+" + digits
        if digits.startswith("0") and len(digits) in (9, 10): digits = "967" + digits[1:]
        elif len(digits) in (9, 10) and digits.startswith(("7", "1")): digits = "967" + digits
        if digits.startswith("967") and 11 <= len(digits) <= 15: return "+" + digits
    return None

def extract_contact_facts(text: str) -> dict:
    email = (EMAIL_RE.search(text).group(0).lower() if EMAIL_RE.search(text) else None)
    raw_phone = PHONE_RE.search(text).group(1) if PHONE_RE.search(text) else None
    interests = sorted({v for k, v in INTERESTS.items() if k in text.lower()})
    # Deliberately avoid inferring a name from arbitrary prose. A structured LLM extraction may supply it separately.
    return {"email": email, "phone_e164": normalize_phone(raw_phone), "resort_interests": interests}

def upsert_lead(db: Session, organization_id: UUID, channel: str, external_contact_id: str | None, facts: dict, contacted_at=None) -> CustomerLead:
    lead = None
    if external_contact_id:
        lead = db.scalar(select(CustomerLead).where(CustomerLead.organization_id == organization_id, CustomerLead.source_channel == channel, CustomerLead.external_contact_id == external_contact_id))
    if lead is None and facts.get("phone_e164"):
        lead = db.scalar(select(CustomerLead).where(CustomerLead.organization_id == organization_id, CustomerLead.phone_e164 == facts["phone_e164"]))
    if lead is None and facts.get("email"):
        lead = db.scalar(select(CustomerLead).where(CustomerLead.organization_id == organization_id, CustomerLead.email == facts["email"]))
    if lead is None:
        lead = CustomerLead(organization_id=organization_id, source_channel=channel, external_contact_id=external_contact_id)
        db.add(lead)
    for field in ("full_name", "phone_e164", "email"):
        value = facts.get(field)
        if value and not getattr(lead, field): setattr(lead, field, value)
    lead.resort_interests = sorted(set((lead.resort_interests or []) + facts.get("resort_interests", [])))
    if facts.get("booking_details"):
        history = lead.booking_history or []
        source_id = facts.get("_source_message_id")
        if not source_id or not any(item.get("source_message_id") == source_id for item in history):
            history.append({"details": str(facts["booking_details"])[:1000], "observed_at": datetime.now(timezone.utc).isoformat(), "source_message_id": source_id})
            lead.booking_history = history
        lead.lead_status = "hot"
    lead.last_contacted_at = contacted_at or datetime.now(timezone.utc)
    if contacted_at and (lead.last_inbound_at is None or contacted_at > lead.last_inbound_at): lead.last_inbound_at = contacted_at
    db.flush()
    return lead

def segment_leads(db: Session, organization_id: UUID, segment: dict) -> list[CustomerLead]:
    stmt = select(CustomerLead).where(CustomerLead.organization_id == organization_id)
    interest = segment.get("interest")
    if interest: stmt = stmt.where(CustomerLead.resort_interests.contains([interest]))
    status = segment.get("lead_status")
    if status in {"hot", "warm", "cold", "past_guest"}: stmt = stmt.where(CustomerLead.lead_status == status)
    min_score = segment.get("min_engagement_score")
    if min_score is not None: stmt = stmt.where(CustomerLead.engagement_score >= max(0, min(100, int(min_score))))
    inactive_days = segment.get("inactive_days")
    if inactive_days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, min(3650, int(inactive_days))))
        stmt = stmt.where(or_(CustomerLead.last_contacted_at.is_(None), CustomerLead.last_contacted_at < cutoff))
    return list(db.scalars(stmt.order_by(CustomerLead.updated_at.desc()).limit(5000)).all())

def export_vcf(leads: list[CustomerLead]) -> str:
    def esc(s): return str(s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")
    rows=[]
    for x in leads:
        rows += ["BEGIN:VCARD", "VERSION:3.0", f"FN:{esc(x.full_name or 'Resort Guest')}", f"N:{esc(x.full_name or 'Resort Guest')};;;;"]
        if x.phone_e164: rows.append(f"TEL;TYPE=CELL:{esc(x.phone_e164)}")
        if x.email: rows.append(f"EMAIL:{esc(x.email)}")
        rows += [f"NOTE:{esc('Resort Guest | Interests: ' + ', '.join(x.resort_interests or []))}", "END:VCARD"]
    return "\r\n".join(rows) + "\r\n"

class GooglePeopleAdapter:
    """Uses OAuth refresh token supplied by the resort admin; never logs tokens."""
    TOKEN_URL = "https://oauth2.googleapis.com/token"
    PEOPLE_URL = "https://people.googleapis.com/v1/people:createContact"
    def __init__(self, client_id: str, client_secret: str, refresh_token: str):
        self.client_id, self.client_secret, self.refresh_token = client_id, client_secret, refresh_token
    def _access_token(self) -> str:
        with httpx.Client(timeout=15) as client:
            r = client.post(self.TOKEN_URL, data={"client_id": self.client_id, "client_secret": self.client_secret, "refresh_token": self.refresh_token, "grant_type": "refresh_token"})
            r.raise_for_status(); return r.json()["access_token"]
    def create_contact(self, lead: CustomerLead) -> str:
        token = self._access_token()
        body = {"names": [{"givenName": lead.full_name or "Resort Guest"}], "biographies": [{"value": "Resort Guest"}], "userDefined": [{"key": "Label", "value": "Resort Guest"}, {"key": "Lead ID", "value": str(lead.id)}]}
        if lead.phone_e164: body["phoneNumbers"] = [{"value": lead.phone_e164, "type": "mobile"}]
        if lead.email: body["emailAddresses"] = [{"value": lead.email}]
        with httpx.Client(timeout=20) as client:
            r = client.post(self.PEOPLE_URL, headers={"Authorization": f"Bearer {token}"}, json=body); r.raise_for_status(); return r.json()["resourceName"]

def llm_extract_facts(text: str, api_key: str | None = None) -> dict:
    """Extract only explicitly stated facts through the configured AI provider.

    The optional api_key argument is retained for backwards compatibility; provider
    credentials are now read from server-side application settings only.
    """
    facts = extract_contact_facts(text)
    if not text.strip():
        return facts
    try:
        from app.ai.providers import generate_json
        parsed = generate_json(
            "Extract only facts explicitly stated in this guest message. Never infer missing contact details. "
            "Return JSON keys full_name, phone, email, booking_details, resort_interests "
            "(array chosen only from villas,pools,corporate,events,dining,spa,other). "
            "Values unknown must be null or [].",
            text[:5000],
        )
        if parsed.get("full_name") and isinstance(parsed["full_name"], str): facts["full_name"] = parsed["full_name"][:200]
        if parsed.get("phone"):
            p = normalize_phone(str(parsed["phone"])); facts["phone_e164"] = p or facts.get("phone_e164")
        if parsed.get("email") and EMAIL_RE.fullmatch(str(parsed["email"])): facts["email"] = str(parsed["email"]).lower()
        if parsed.get("booking_details"): facts["booking_details"] = str(parsed["booking_details"])[:1000]
        if isinstance(parsed.get("resort_interests"), list): facts["resort_interests"] = sorted(set(facts.get("resort_interests", [])) | (set(parsed["resort_interests"]) & {"villas","pools","corporate","events","dining","spa","other"}))
    except Exception:
        # Extraction is best-effort; never block inbox processing or log guest text/secrets.
        pass
    return facts
