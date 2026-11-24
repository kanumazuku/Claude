"""利用できるAIプロバイダーの一覧。新しいAIを追加するときはここに登録する。"""

from __future__ import annotations

from ..config import Settings
from .base import Provider, ProviderError, ProviderStatus, TurnKind, TurnRequest
from .claude_provider import ClaudeProvider
from .gemini_provider import GeminiProvider
from .mock_provider import MockProvider
from .ollama_provider import OllamaProvider
from .openai_provider import OpenAIProvider

__all__ = [
    "Provider",
    "ProviderError",
    "ProviderStatus",
    "TurnKind",
    "TurnRequest",
    "build_registry",
]


def build_registry(settings: Settings) -> dict[str, Provider]:
    providers: list[Provider] = [
        ClaudeProvider(settings),
        OpenAIProvider(settings),
        GeminiProvider(settings),
        OllamaProvider(settings),
        MockProvider(settings),
    ]
    return {p.id: p for p in providers}
