"""Gemini（Google）プロバイダー。公式SDK google-genai を使う。"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

from google import genai
from google.genai import errors, types

from ..config import Settings, has_env
from .base import Provider, ProviderError, ProviderStatus, TurnRequest

_SUGGESTED_MODELS = ["gemini-3.8-flash", "gemini-3.5-flash", "gemini-3.1-pro-preview"]


class GeminiProvider(Provider):
    id = "gemini"
    label = "Gemini"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: genai.Client | None = None

    def _get_client(self) -> genai.Client:
        if self._client is None:
            key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
            self._client = genai.Client(api_key=key)
        return self._client

    async def status(self) -> ProviderStatus:
        models = list(dict.fromkeys([self._settings.gemini_model, *_SUGGESTED_MODELS]))
        if not has_env("GEMINI_API_KEY", "GOOGLE_API_KEY"):
            return ProviderStatus(False, "GEMINI_API_KEYが未設定です", models, self._settings.gemini_model)
        return ProviderStatus(True, "", models, self._settings.gemini_model)

    async def stream(self, req: TurnRequest) -> AsyncIterator[str]:
        client = self._get_client()
        config = types.GenerateContentConfig(
            system_instruction=req.system,
            max_output_tokens=req.max_tokens,
        )
        produced = False
        try:
            async for chunk in await client.aio.models.generate_content_stream(
                model=req.model, contents=req.prompt, config=config
            ):
                if chunk.text:
                    produced = True
                    yield chunk.text
        except errors.ClientError as e:
            if e.code in (401, 403):
                raise ProviderError("GeminiのAPIキーが無効か、権限がありません。") from e
            if e.code == 404:
                raise ProviderError(f"Geminiのモデル「{req.model}」が見つかりません。") from e
            if e.code == 429:
                raise ProviderError("Geminiの利用上限に達しました。少し待ってから再実行してください。") from e
            raise ProviderError(f"Geminiへのリクエストが不正です：{e.message}") from e
        except errors.APIError as e:
            raise ProviderError(f"Geminiのサーバーエラーです（{e.code}）。") from e
        except OSError as e:
            raise ProviderError("Geminiに接続できません。ネットワークを確認してください。") from e

        if not produced:
            raise ProviderError("Geminiから応答が得られませんでした（安全フィルターで止められた可能性があります）。")
