"""Small live smoke test for the configured Gemini text and embedding endpoints."""
from app.ai.providers import embed_text, generate_json
from app.core.config import get_settings


def main() -> None:
    settings = get_settings()
    if settings.ai_provider != "gemini":
        raise SystemExit(f"AI_PROVIDER must be 'gemini'; currently: {settings.ai_provider}")

    print(f"Testing Gemini model: {settings.ai_model}")
    result = generate_json(
        "Return a JSON object with one key named status and value exactly GEMINI_OK.",
        "Run the connection test.",
    )
    if result.get("status") != "GEMINI_OK":
        raise SystemExit(f"Text generation responded but did not match the test: {result}")
    print("Gemini text generation: OK")

    vector = embed_text("اختبار تضمين قاعدة معرفة المنتجع", purpose="query")
    if len(vector) != 1536:
        raise SystemExit(f"Unexpected embedding length: {len(vector)}")
    print("Gemini embeddings: OK (1536 dimensions)")
    print("Gemini connection test: PASSED")


if __name__ == "__main__":
    main()
