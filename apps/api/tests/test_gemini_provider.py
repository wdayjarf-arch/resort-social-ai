from types import SimpleNamespace

from pydantic import SecretStr

from app.ai import providers


class FakeResponse:
    status_code = 200
    is_success = True

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload

    def raise_for_status(self):
        return None


class FakeClient:
    last_request = None
    response_payload = {}

    def __init__(self, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, url, **kwargs):
        FakeClient.last_request = (url, kwargs)
        return FakeResponse(FakeClient.response_payload)


def gemini_settings():
    return SimpleNamespace(
        ai_provider="gemini",
        ai_model="gemini-3.8-flash",
        ai_embedding_model="gemini-embedding-2",
        gemini_api_key=SecretStr("test-key"),
        openai_api_key=None,
        anthropic_api_key=None,
    )


def test_gemini_generate_json(monkeypatch):
    monkeypatch.setattr(providers, "get_settings", gemini_settings)
    monkeypatch.setattr(providers.httpx, "Client", FakeClient)
    FakeClient.response_payload = {
        "steps": [{
            "type": "model_output",
            "content": [{
                "type": "text",
                "text": '{"tag":"استفسار حجز","confidence":0.9}'
            }]
        }]
    }

    result = providers.generate_json("system", "guest question")

    assert result == {"tag": "استفسار حجز", "confidence": 0.9}
    url, request = FakeClient.last_request
    assert url == "https://generativelanguage.googleapis.com/v1beta/interactions"
    assert request["headers"]["x-goog-api-key"] == "test-key"
    assert request["json"]["model"] == "gemini-3.8-flash"
    assert request["json"]["system_instruction"] == "system"
    assert request["json"]["input"] == "guest question"
    assert request["json"]["response_format"]["mime_type"] == "application/json"

def test_gemini_embedding_is_1536_dimensions_and_uses_retrieval_prefix(monkeypatch):
    monkeypatch.setattr(providers, "get_settings", gemini_settings)
    monkeypatch.setattr(providers.httpx, "Client", FakeClient)
    FakeClient.response_payload = {"embedding": {"values": [0.01] * 1536}}

    vector = providers.embed_text("booking policy", purpose="query")

    assert len(vector) == 1536
    url, request = FakeClient.last_request
    assert url.endswith("/models/gemini-embedding-2:embedContent")
    assert request["json"]["output_dimensionality"] == 1536
    assert request["json"]["content"]["parts"][0]["text"].startswith("task: question answering | query:")


def test_gemini_document_embedding_uses_title_prefix(monkeypatch):
    monkeypatch.setattr(providers, "get_settings", gemini_settings)
    monkeypatch.setattr(providers.httpx, "Client", FakeClient)
    FakeClient.response_payload = {"embedding": {"values": [0.01] * 1536}}

    providers.embed_text("pool access is open from 8 to 6", purpose="document", title="Pool hours")

    _url, request = FakeClient.last_request
    text = request["json"]["content"]["parts"][0]["text"]
    assert text.startswith("title: Pool hours | text:")
