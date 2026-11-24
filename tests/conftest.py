from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from ai_council.config import load_settings
from ai_council.providers import Provider, ProviderError, ProviderStatus, TurnKind, TurnRequest
from ai_council.providers.mock_provider import MockProvider
from ai_council.schemas import DebateConfig


class ScriptedProvider(Provider):
    """テスト用のAI。発言の種類ごとに決まった文を返し、受け取った依頼を記録する。"""

    id = "scripted"
    label = "テスト"

    def __init__(self, replies: dict[TurnKind, str] | None = None, fail_for: set[str] | None = None) -> None:
        self.replies = replies or {}
        self.fail_for = fail_for or set()
        self.requests: list[TurnRequest] = []

    async def status(self) -> ProviderStatus:
        return ProviderStatus(True, "", ["scripted-1"], "scripted-1")

    async def stream(self, req: TurnRequest) -> AsyncIterator[str]:
        self.requests.append(req)
        if req.speaker_name in self.fail_for:
            raise ProviderError("テスト用の失敗です。")
        text = self.replies.get(req.kind, f"{req.speaker_name}の{req.kind.value}の発言です。")
        for i in range(0, len(text), 5):
            yield text[i : i + 5]


@pytest.fixture
def mock_provider() -> MockProvider:
    settings = load_settings()
    provider = MockProvider(settings)
    provider._delay = 0
    return provider


def make_config(provider: str = "mock", **overrides) -> DebateConfig:
    data = {
        "topic": "週休3日制を導入すべきか",
        "rounds": 2,
        "moderator": {"name": "議長", "provider": provider},
        "debaters": [
            {"name": "推進派", "provider": provider, "stance": "前向き"},
            {"name": "慎重派", "provider": provider, "stance": "慎重"},
        ],
    }
    data.update(overrides)
    return DebateConfig.model_validate(data)
