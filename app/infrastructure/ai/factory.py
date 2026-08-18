from app.core.config import Settings
from app.domain.ai import AIProvider
from app.infrastructure.ai.gemini import GeminiProvider


class AIProviderConfigurationError(ValueError):
    """Raised when the selected provider cannot be composed."""


def create_ai_provider(settings: Settings) -> AIProvider:
    provider_name = settings.ai_provider.strip().lower()
    if provider_name == "gemini":
        if not settings.gemini_api_key:
            raise AIProviderConfigurationError(
                "Gemini is selected but JARVIS_GEMINI_API_KEY is not configured."
            )
        return GeminiProvider(settings.gemini_api_key, settings.gemini_model)

    raise AIProviderConfigurationError(
        f"AI provider '{provider_name}' is not implemented."
    )
