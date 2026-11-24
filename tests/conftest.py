from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable

import pytest

from ai_council.config import load_settings
from ai_council.providers import Provider, ProviderError, ProviderStatus, TurnKind, TurnRequest
from ai_council.providers.mock_provider import MockProvider
from ai_council.schemas import DebateConfig


class ScriptedProvider(Provider):
    """テスト用のAI。発言の種類ごとに決まった文を返し、受け取った依頼を記録する。

    replyには文字列か、TurnRequestを受け取って文字列を返す関数を指定できる。
    """

    id = "scripted"
    label = "テスト"

    def __init__(
        self,
        replies: dict[TurnKind, str | Callable[[TurnRequest], str]] | None = None,
        fail_for: set[tuple[str, TurnKind]] | None = None,
        pause: float = 0,
    ) -> None:
        self.replies = replies or {}
        self.fail_for = fail_for or set()
        self.pause = pause
        self.requests: list[TurnRequest] = []

    async def status(self) -> ProviderStatus:
        return ProviderStatus(True, "", ["scripted-1"], "scripted-1")

    async def stream(self, req: TurnRequest) -> AsyncIterator[str]:
        self.requests.append(req)
        if (req.speaker_name, req.kind) in self.fail_for:
            raise ProviderError("テスト用の失敗です。")
        reply = self.replies.get(req.kind, f"{req.speaker_name}の{req.kind.value}の発言です。")
        text = reply(req) if callable(reply) else reply
        for i in range(0, len(text), 5):
            if self.pause:
                await asyncio.sleep(self.pause)
            yield text[i : i + 5]


@pytest.fixture
def mock_provider() -> MockProvider:
    provider = MockProvider(load_settings())
    provider._delay = 0
    return provider


def make_config(provider: str = "mock", names=("推進派", "慎重派", "現実派"), **overrides) -> DebateConfig:
    data = {
        "topic": "週休3日制を導入すべきか",
        "rounds": 2,
        "participants": [{"name": n, "provider": provider, "stance": f"{n}の立場"} for n in names],
    }
    data.update(overrides)
    return DebateConfig.model_validate(data)
