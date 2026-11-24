"""Claude（Anthropic）プロバイダー。公式SDK anthropic を使う。"""

from __future__ import annotations

from collections.abc import AsyncIterator

import anthropic

from ..config import Settings, has_env
from .base import Provider, ProviderError, ProviderStatus, TurnRequest

# 安全分類器が発言を辞退したとき、サーバー側で別モデルに引き継ぐ「fallbacks」に
# 対応しているモデル。これ以外のモデルではfallbacksを送らない。
_FALLBACK_MODELS = {
    "claude-opus-5-5",
    "claude-opus-5",
    "claude-sonnet-5-5",
    "claude-fable-5-2",
    "claude-fable-5-1",
}
_FALLBACK_BETA = "server-side-fallback-2026-07-01"

# effort（思考の深さ）を受け付けないモデル。
_NO_EFFORT_PREFIXES = ("claude-haiku-4", "claude-sonnet-4-5", "claude-3")

_SUGGESTED_MODELS = [
    "claude-opus-5-5",
    "claude-sonnet-5-5",
    "claude-haiku-5-5",
    "claude-fable-5-2",
]


class ClaudeProvider(Provider):
    id = "claude"
    label = "Claude"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: anthropic.AsyncAnthropic | None = None

    def _get_client(self) -> anthropic.AsyncAnthropic:
        if self._client is None:
            self._client = anthropic.AsyncAnthropic()
        return self._client

    async def status(self) -> ProviderStatus:
        models = _dedupe([self._settings.claude_model, *_SUGGESTED_MODELS])
        if not has_env("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
            return ProviderStatus(False, "ANTHROPIC_API_KEYが未設定です", models, self._settings.claude_model)
        return ProviderStatus(True, "", models, self._settings.claude_model)

    async def stream(self, req: TurnRequest) -> AsyncIterator[str]:
        client = self._get_client()
        params: dict = {
            "model": req.model,
            "max_tokens": req.max_tokens,
            "system": req.system,
            "messages": [{"role": "user", "content": req.prompt}],
        }
        if not req.model.startswith(_NO_EFFORT_PREFIXES):
            params["output_config"] = {"effort": self._settings.claude_effort}

        try:
            if req.model in _FALLBACK_MODELS:
                stream_cm = client.beta.messages.stream(
                    **params, betas=[_FALLBACK_BETA], fallbacks="default"
                )
            else:
                stream_cm = client.messages.stream(**params)

            async with stream_cm as stream:
                async for text in stream.text_stream:
                    yield text
                final = await stream.get_final_message()
        except anthropic.AuthenticationError as e:
            raise ProviderError("ClaudeのAPIキーが無効です。") from e
        except anthropic.PermissionDeniedError as e:
            raise ProviderError("このAPIキーでは指定のClaudeモデルを使えません。") from e
        except anthropic.NotFoundError as e:
            raise ProviderError(f"Claudeのモデル「{req.model}」が見つかりません。") from e
        except anthropic.RateLimitError as e:
            raise ProviderError("Claudeの利用上限に達しました。少し待ってから再実行してください。") from e
        except anthropic.BadRequestError as e:
            raise ProviderError(f"Claudeへのリクエストが不正です：{e.message}") from e
        except anthropic.APIStatusError as e:
            raise ProviderError(f"Claudeのサーバーエラーです（{e.status_code}）。") from e
        except anthropic.APIConnectionError as e:
            raise ProviderError("Claudeに接続できません。ネットワークを確認してください。") from e

        if final.stop_reason == "refusal":
            raise ProviderError("Claudeがこの発言を辞退しました。議題を変えて試してください。")


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(i for i in items if i))
