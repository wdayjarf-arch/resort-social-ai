"""Timeout-bound LLM and embedding adapters for OpenAI, Anthropic, and Gemini.

API keys are read from server-side settings and are never logged or returned.
"""
import json
import time
from typing import Any, Literal

import httpx

from app.core.config import get_settings


class AIProviderError(RuntimeError):
    pass


def _gemini_error(response: httpx.Response, operation: str) -> AIProviderError:
    """Return a useful provider error without echoing request headers or secrets."""
    message = ""
    try:
        payload = response.json()
        message = str(payload.get("error", {}).get("message", ""))
    except (ValueError, AttributeError):
        pass
    suffix = f": {message[:500]}" if message else ""
    return AIProviderError(f"Gemini {operation} failed (HTTP {response.status_code}){suffix}")


def _gemini_key(settings) -> str:
    if not settings.gemini_api_key or not settings.gemini_api_key.get_secret_value().strip():
        raise AIProviderError("GEMINI_API_KEY is required when AI_PROVIDER=gemini")
    return settings.gemini_api_key.get_secret_value().strip()


def embed_text(
    text: str,
    *,
    purpose: Literal["query", "document"] = "query",
    title: str | None = None,
) -> list[float]:
    """Create a 1536-dimensional vector compatible with the existing pgvector schema.

    Gemini Embedding 2 uses task prefixes for retrieval rather than task_type.
    OpenAI and Anthropic configurations continue to use OpenAI embeddings as before.
    """
    settings = get_settings()
    try:
        with httpx.Client(timeout=25.0, follow_redirects=False) as client:
            if settings.ai_provider == "gemini":
                key = _gemini_key(settings)
                if purpose == "document":
                    prepared_text = f"title: {title or 'none'} | text: {text[:12000]}"
                else:
                    prepared_text = f"task: question answering | query: {text[:12000]}"
                response = client.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{settings.ai_embedding_model}:embedContent",
                    headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                    json={
                        "model": f"models/{settings.ai_embedding_model}",
                        "content": {"parts": [{"text": prepared_text}]},
                        "output_dimensionality": 1536,
                    },
                )
                if not response.is_success:
                    raise _gemini_error(response, "embedding")
                data = response.json()["embedding"]["values"]
            else:
                if not settings.openai_api_key:
                    raise AIProviderError("OPENAI_API_KEY is required for the configured embedding provider")
                response = client.post(
                    "https://api.openai.com/v1/embeddings",
                    headers={"Authorization": f"Bearer {settings.openai_api_key.get_secret_value()}", "Content-Type": "application/json"},
                    json={"model": settings.ai_embedding_model, "input": text[:12000]},
                )
                response.raise_for_status()
                data = response.json()["data"][0]["embedding"]
        if len(data) != 1536:
            raise AIProviderError("Embedding dimensions do not match configured vector schema (1536)")
        return [float(value) for value in data]
    except AIProviderError:
        raise
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
        raise AIProviderError("Embedding provider request failed") from exc


def generate_json(system_prompt: str, user_prompt: str) -> dict[str, Any]:
    """Generate a JSON object using the configured provider."""
    settings = get_settings()

    try:
        with httpx.Client(timeout=60.0, follow_redirects=False) as client:
            if settings.ai_provider == "anthropic":
                if not settings.anthropic_api_key:
                    raise AIProviderError(
                        "ANTHROPIC_API_KEY is required for Anthropic chat"
                    )

                response = client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": settings.anthropic_api_key.get_secret_value(),
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": settings.ai_model,
                        "max_tokens": 900,
                        "temperature": 0.2,
                        "system": system_prompt,
                        "messages": [{"role": "user", "content": user_prompt}],
                    },
                )
                response.raise_for_status()
                blocks = response.json().get("content", [])
                content = "\n".join(
                    str(b.get("text", "")) for b in blocks if b.get("type") == "text"
                )

            elif settings.ai_provider == "gemini":
                key = _gemini_key(settings)
                url = "https://generativelanguage.googleapis.com/v1beta/interactions"

                request_body = {
                    "model": settings.ai_model,
                    "system_instruction": system_prompt,
                    "input": user_prompt,
                    "store": False,
                    "response_format": {
                        "type": "text",
                        "mime_type": "application/json",
                    },
                }

                response = None
                for attempt in range(4):
                    response = client.post(
                        url,
                        headers={
                            "x-goog-api-key": key,
                            "Content-Type": "application/json",
                        },
                        json=request_body,
                    )

                    if response.status_code not in (429, 500, 502, 503, 504):
                        break

                    if attempt < 3:
                        retry_after = response.headers.get("Retry-After")
                        try:
                            delay = (
                                min(max(float(retry_after), 1), 30)
                                if retry_after
                                else 2 ** (attempt + 1)
                            )
                        except ValueError:
                            delay = 2 ** (attempt + 1)
                        time.sleep(delay)

                if not response.is_success:
                    raise _gemini_error(response, "generation")

                payload = response.json()
                content = payload.get("output_text", "")

                if not content:
                    text_parts = []
                    for step in payload.get("steps", []):
                        if step.get("type") == "model_output":
                            for part in step.get("content", []):
                                if part.get("type") == "text" and part.get("text"):
                                    text_parts.append(part["text"])
                    content = "\n".join(text_parts)

                if not content.strip():
                    raise AIProviderError("Gemini returned an empty structured response")

            else:
                if not settings.openai_api_key:
                    raise AIProviderError("OPENAI_API_KEY is required for OpenAI chat")

                response = client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={
                        "Authorization": "Bearer " + settings.openai_api_key.get_secret_value(),
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": settings.ai_model,
                        "temperature": 0.2,
                        "response_format": {"type": "json_object"},
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                    },
                )
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]

        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            raise AIProviderError("LLM returned a non-object response")
        return parsed

    except AIProviderError:
        raise
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
        raise AIProviderError(
            "Language model request or structured response failed"
        ) from exc
