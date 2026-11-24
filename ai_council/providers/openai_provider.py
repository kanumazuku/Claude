"""GPT（OpenAI）プロバイダー。公式SDK openai のResponses APIを使う。"""

from __future__ import annotations

from collections.abc import AsyncIterator

import openai

from ..config import Settings, has_env
from .base import Provider, ProviderError, ProviderStatus, TurnRequest

_SUGGESTED_MODELS = ["gpt-5.5", "gpt-5.4-mini", "gpt-5.1"]


class OpenAIProvider(Provider):
    id = "openai"
    label = "GPT"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: openai.AsyncOpenAI | None = None

    def _get_client(self) -> openai.AsyncOpenAI:
        if self._client is None:
            self._client = openai.AsyncOpenAI()
        return self._client

    async def status(self) -> ProviderStatus:
        models = list(dict.fromkeys([self._settings.openai_model, *_SUGGESTED_MODELS]))
        if not has_env("OPENAI_API_KEY"):
            return ProviderStatus(False, "OPENAI_API_KEYが未設定です", models, self._settings.openai_model)
        return ProviderStatus(True, "", models, self._settings.openai_model)

    async def stream(self, req: TurnRequest) -> AsyncIterator[str]:
        client = self._get_client()
        refusal = ""
        try:
            events = await client.responses.create(
                model=req.model,
                instructions=req.system,
                input=req.prompt,
                max_output_tokens=req.max_tokens,
                stream=True,
            )
            async for event in events:
                if event.type == "response.output_text.delta":
                    yield event.delta
                elif event.type == "response.refusal.delta":
                    refusal += event.delta
                elif event.type == "response.failed":
                    error = event.response.error
                    raise ProviderError(f"GPTの応答生成に失敗しました：{error.message if error else '不明なエラー'}")
                elif event.type == "error":
                    raise ProviderError(f"GPTの応答生成に失敗しました：{event.message}")
        except openai.AuthenticationError as e:
            raise ProviderError("OpenAIのAPIキーが無効です。") from e
        except openai.NotFoundError as e:
            raise ProviderError(f"GPTのモデル「{req.model}」が見つかりません。") from e
        except openai.RateLimitError as e:
            raise ProviderError("OpenAIの利用上限に達しました。少し待ってから再実行してください。") from e
        except openai.BadRequestError as e:
            raise ProviderError(f"OpenAIへのリクエストが不正です：{e.message}") from e
        except openai.APIStatusError as e:
            raise ProviderError(f"OpenAIのサーバーエラーです（{e.status_code}）。") from e
        except openai.APIConnectionError as e:
            raise ProviderError("OpenAIに接続できません。ネットワークを確認してください。") from e

        if refusal:
            raise ProviderError(f"GPTがこの発言を辞退しました：{refusal}")
