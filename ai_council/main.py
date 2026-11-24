"""Webサーバー。画面（static/）の配信と、討論のリアルタイム配信（SSE）を担当する。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .config import load_settings
from .debate import DebateRunner, Seat
from .providers import Provider, ProviderStatus, build_registry
from .schemas import DebateConfig, ParticipantConfig

STATIC_DIR = Path(__file__).parent / "static"


def create_app(registry: dict[str, Provider] | None = None) -> FastAPI:
    settings = load_settings()
    providers = registry if registry is not None else build_registry(settings)

    app = FastAPI(title="AI合議室")
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    async def statuses() -> dict[str, ProviderStatus]:
        results = await asyncio.gather(*(p.status() for p in providers.values()))
        return dict(zip(providers.keys(), results))

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/providers")
    async def list_providers() -> list[dict]:
        found = await statuses()
        return [
            {
                "id": pid,
                "label": providers[pid].label,
                "available": st.available,
                "reason": st.reason,
                "default_model": st.default_model,
                "models": st.models,
            }
            for pid, st in found.items()
        ]

    @app.post("/api/debate")
    async def start_debate(cfg: DebateConfig) -> StreamingResponse:
        found = await statuses()

        def seat(seat_id: str, role: str, p: ParticipantConfig) -> Seat:
            provider = providers.get(p.provider)
            if provider is None:
                raise HTTPException(400, f"「{p.name}」のAI「{p.provider}」は存在しません。")
            st = found[p.provider]
            if not st.available:
                raise HTTPException(400, f"「{p.name}」に割り当てた{provider.label}は使えません：{st.reason}")
            return Seat(seat_id, role, p, provider, p.model or st.default_model)

        moderator = seat("moderator", "moderator", cfg.moderator)
        debaters = [seat(f"d{i}", "debater", d) for i, d in enumerate(cfg.debaters, start=1)]
        runner = DebateRunner(cfg, moderator, debaters)

        return StreamingResponse(
            _sse(runner.run()),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


async def _sse(events: AsyncIterator[dict]) -> AsyncIterator[str]:
    async for event in events:
        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


app = create_app()
