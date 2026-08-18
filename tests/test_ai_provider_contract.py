from app.domain.ai import AICapability


def test_fake_provider_declares_supported_capabilities(fake_provider) -> None:
    assert fake_provider.supports(AICapability.TEXT) is True
    assert fake_provider.supports(AICapability.TOOL_CALLING) is True
    assert fake_provider.supports(AICapability.EMBEDDINGS) is False
