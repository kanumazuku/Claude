from __future__ import annotations

import json

from fastapi.testclient import TestClient

from ai_council.main import create_app
from ai_council.providers import ProviderStatus

from .conftest import ScriptedProvider


class UnavailableProvider(ScriptedProvider):
    id = "offline"
    label = "オフライン"

    async def status(self):
        return ProviderStatus(False, "APIキーが未設定です", ["x"], "x")


def client() -> TestClient:
    return TestClient(create_app({"scripted": ScriptedProvider(), "offline": UnavailableProvider()}))


def body(provider="scripted", **overrides):
    data = {
        "topic": "テストの議題",
        "rounds": 1,
        "participants": [{"name": "A", "provider": provider}, {"name": "B", "provider": provider}],
    }
    data.update(overrides)
    return data


def parse_sse(text: str) -> list[dict]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


def test_index_and_static_files():
    c = client()
    assert "AI合議室" in c.get("/").text
    assert c.get("/static/app.js").status_code == 200


def test_list_providers():
    data = client().get("/api/providers").json()
    by_id = {p["id"]: p for p in data}
    assert by_id["scripted"]["available"] is True
    assert by_id["offline"] == {
        "id": "offline",
        "label": "オフライン",
        "available": False,
        "reason": "APIキーが未設定です",
        "default_model": "x",
        "models": ["x"],
    }


def test_debate_streams_events_and_uses_default_model():
    with client().stream("POST", "/api/debate", json=body()) as res:
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("text/event-stream")
        events = parse_sse(res.read().decode())

    assert events[0]["type"] == "debate_start"
    participants = events[0]["participants"]
    assert [p["id"] for p in participants] == ["p1", "p2"]
    assert {p["model"] for p in participants} == {"scripted-1"}
    assert all("role" not in p for p in participants)  # 役割による区別はない
    assert events[-1]["type"] == "debate_end"
    assert events[-1]["status"] == "completed"


def test_unavailable_provider_is_rejected_up_front():
    res = client().post("/api/debate", json=body(provider="offline"))
    assert res.status_code == 400
    assert "APIキーが未設定です" in res.json()["detail"]


def test_unknown_provider_is_rejected():
    res = client().post("/api/debate", json=body(provider="nope"))
    assert res.status_code == 400


def test_invalid_config_is_rejected():
    res = client().post("/api/debate", json=body(topic="   "))
    assert res.status_code == 422
