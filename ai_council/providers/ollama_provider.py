"""ローカルLLM（Ollama）プロバイダー。公式SDK ollama を使う。"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import ollama

from ..config import Settings
from .base import Provider, ProviderError, ProviderStatus, TurnRequest


class OllamaProvider(Provider):
    id = "ollama"
    label = "ローカルLLM"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def status(self) -> ProviderStatus:
        default = self._settings.ollama_model
        try:
            client = ollama.AsyncClient(host=self._settings.ollama_host, timeout=1.5)
            listed = await client.list()
        except Exception:  # noqa: BLE001 - 未起動・未インストールなど原因を問わず「使えない」とする
            return ProviderStatus(False, "Ollamaが起動していません", [default], default)

        installed = [m.model for m in listed.models if m.model]
        if not installed:
            return ProviderStatus(False, "Ollamaにモデルがありません（ollama pullで追加）", [default], default)
        if default not in installed and f"{default}:latest" not in installed:
            default = installed[0]
        return ProviderStatus(True, "", installed, default)

    async def stream(self, req: TurnRequest) -> AsyncIterator[str]:
        client = ollama.AsyncClient(host=self._settings.ollama_host)
        try:
            parts = await client.chat(
                model=req.model,
                messages=[
                    {"role": "system", "content": req.system},
                    {"role": "user", "content": req.prompt},
                ],
                stream=True,
            )
            async for part in parts:
                text = part.message.content
                if text:
                    yield text
        except ollama.ResponseError as e:
            if e.status_code == 404:
                raise ProviderError(
                    f"Ollamaにモデル「{req.model}」がありません。「ollama pull {req.model}」で追加してください。"
                ) from e
            raise ProviderError(f"Ollamaでエラーが発生しました：{e.error}") from e
        except (ConnectionError, httpx.HTTPError) as e:
            raise ProviderError("Ollamaに接続できません。Ollamaを起動してください。") from e
