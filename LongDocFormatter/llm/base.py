from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseLanguageModel(ABC):
    @abstractmethod
    def chat_json(self, *, system: str, user: str, **kwargs: Any) -> dict[str, Any]:
        """Return dict with at least ``content`` key (JSON string)."""


class BaseMultimodalModel(ABC):
    @abstractmethod
    def chat_json(
        self,
        *,
        system: str,
        user: str,
        images: list[dict[str, Any]],
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Multimodal chat; same response shape as language model."""
