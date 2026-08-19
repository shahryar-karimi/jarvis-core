from datetime import datetime
from pathlib import Path

from app.core.config import Settings
from app.domain.memory import MemoryCategory, MemoryEntry


class PromptBuilder:
    """Builds provider-neutral context instead of letting providers own prompt logic."""

    _CATEGORY_TITLES = {
        MemoryCategory.PREFERENCES: "Preferences",
        MemoryCategory.PROJECTS: "Active Projects / Goals",
        MemoryCategory.RELATIONSHIPS: "People in their life",
        MemoryCategory.WISHES: "Wishes / Plans / Wants",
        MemoryCategory.NOTES: "Other notes",
    }

    def __init__(self, settings: Settings, base_prompt_path: Path) -> None:
        self._settings = settings
        self._base_prompt_path = base_prompt_path

    def build(self, memories: list[MemoryEntry], now: datetime | None = None) -> str:
        now = now or datetime.now().astimezone()
        parts = [
            self._time_context(now),
            self._identity_context(),
        ]

        memory_context = self._memory_context(memories)
        if memory_context:
            parts.append(memory_context)

        parts.append(self._base_prompt())
        return "\n\n".join(part.strip() for part in parts if part.strip())

    def _time_context(self, now: datetime) -> str:
        return (
            "[CURRENT DATE & TIME]\n"
            f"Right now it is: {now.strftime('%A, %B %d, %Y — %I:%M %p %Z')}\n"
            "Treat this as runtime context; do not guess dates when a tool can provide exact data."
        )

    def _identity_context(self) -> str:
        user_line = (
            f"Address the user as '{self._settings.user_name}' when natural."
            if self._settings.user_name
            else "Use a natural form of address appropriate to the user's language."
        )
        return f"[IDENTITY]\nYour name is {self._settings.assistant_name}.\n{user_line}"

    def _memory_context(self, memories: list[MemoryEntry]) -> str:
        if not memories:
            return ""

        grouped: dict[MemoryCategory, list[MemoryEntry]] = {}
        for memory in memories:
            grouped.setdefault(memory.category, []).append(memory)

        lines = ["[RELEVANT USER MEMORY — use naturally, never recite like a database]"]
        for entry in grouped.get(MemoryCategory.IDENTITY, []):
            lines.append(f"{self._label(entry.key)}: {entry.value}")

        for category, title in self._CATEGORY_TITLES.items():
            entries = grouped.get(category, [])
            if not entries:
                continue
            lines.extend(["", f"{title}:"])
            lines.extend(f"- {self._label(entry.key)}: {entry.value}" for entry in entries)

        return "\n".join(lines)

    def _base_prompt(self) -> str:
        return self._base_prompt_path.read_text(encoding="utf-8").strip()

    @staticmethod
    def _label(key: str) -> str:
        return key.replace("_", " ").strip().title()
