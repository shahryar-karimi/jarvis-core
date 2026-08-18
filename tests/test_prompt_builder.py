from datetime import datetime, timezone
from pathlib import Path

from app.application.prompt_builder import PromptBuilder
from app.core.config import Settings
from app.domain.memory import MemoryCategory, MemoryEntry


def test_prompt_builder_includes_identity_memory_and_base_prompt(tmp_path: Path) -> None:
    prompt = tmp_path / "base.txt"
    prompt.write_text("BASE RULES", encoding="utf-8")
    settings = Settings(
        _env_file=None,
        assistant_name="JARVIS",
        user_name="Test User",
        database_url="sqlite+aiosqlite:///:memory:",
    )
    builder = PromptBuilder(settings, prompt)
    memories = [
        MemoryEntry(
            category=MemoryCategory.PREFERENCES,
            key="backend_language",
            value="Python",
            updated_at=datetime.now(timezone.utc),
        )
    ]

    result = builder.build(memories, datetime(2026, 8, 18, 12, tzinfo=timezone.utc))

    assert "Your name is JARVIS" in result
    assert "Backend Language: Python" in result
    assert "BASE RULES" in result
