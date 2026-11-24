from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_council.debate import DebateRunner, Seat, parse_judgment, parse_vote
from ai_council.providers import TurnKind

from .conftest import ScriptedProvider, make_config


def seats(cfg, provider, model="m"):
    moderator = Seat("moderator", "moderator", cfg.moderator, provider, model)
    debaters = [Seat(f"d{i}", "debater", d, provider, model) for i, d in enumerate(cfg.debaters, 1)]
    return moderator, debaters


async def collect(runner):
    return [event async for event in runner.run()]


def turn_sequence(events, seats_by_id):
    return [(seats_by_id[e["speaker_id"]], e["kind"]) for e in events if e["type"] == "turn_start"]


async def test_full_debate_with_mock(mock_provider):
    cfg = make_config(rounds=2)
    moderator, debaters = seats(cfg, mock_provider)
    events = await collect(DebateRunner(cfg, moderator, debaters))

    assert events[0]["type"] == "debate_start"
    assert events[-1] == {"type": "debate_end", "status": "completed", "message": ""}

    names = {"moderator": "議長", "d1": "推進派", "d2": "慎重派"}
    assert turn_sequence(events, names) == [
        ("議長", "opening"),
        ("推進派", "argument"),
        ("慎重派", "argument"),
        ("議長", "interim"),
        ("推進派", "argument"),
        ("慎重派", "argument"),
        ("議長", "proposal"),
        ("推進派", "final"),
        ("慎重派", "final"),
        ("議長", "conclusion"),
    ]
    # 発言の断片を連結すると、turn_endの本文と一致する
    for end in (e for e in events if e["type"] == "turn_end"):
        deltas = "".join(e["text"] for e in events if e["type"] == "delta" and e["turn_id"] == end["turn_id"])
        assert end["ok"] and deltas.strip() == end["text"]

    tally = next(e for e in events if e["type"] == "tally")
    assert sum(tally["counts"].values()) == 2


async def test_moderator_can_end_rounds_early():
    provider = ScriptedProvider({TurnKind.INTERIM: "十分に議論できました。\n【判定】終了"})
    cfg = make_config(provider="scripted", rounds=4)
    moderator, debaters = seats(cfg, provider)
    events = await collect(DebateRunner(cfg, moderator, debaters))

    judgment = [e for e in events if e["type"] == "judgment"]
    assert judgment == [{"type": "judgment", "round": 1, "decision": "stop", "early_stop": True}]
    rounds = [e["round"] for e in events if e["type"] == "phase" and e["phase"] == "round"]
    assert rounds == [1]
    assert events[-1]["status"] == "completed"


async def test_early_stop_can_be_disabled():
    provider = ScriptedProvider({TurnKind.INTERIM: "【判定】終了"})
    cfg = make_config(provider="scripted", rounds=3, early_stop=False)
    moderator, debaters = seats(cfg, provider)
    events = await collect(DebateRunner(cfg, moderator, debaters))

    rounds = [e["round"] for e in events if e["type"] == "phase" and e["phase"] == "round"]
    assert rounds == [1, 2, 3]


async def test_failed_debater_is_skipped_and_others_continue():
    provider = ScriptedProvider(fail_for={"慎重派"})
    cfg = make_config(provider="scripted", rounds=1)
    moderator, debaters = seats(cfg, provider)
    runner = DebateRunner(cfg, moderator, debaters)
    events = await collect(runner)

    failed = [e for e in events if e["type"] == "turn_end" and not e["ok"]]
    assert len(failed) == 2  # 第1ラウンドと最終意見
    assert all("テスト用の失敗" in e["error"] for e in failed)
    assert events[-1]["status"] == "completed"
    # 失敗した発言は議事録に残らず、賛否の集計にも入らない
    assert "慎重派" not in {entry.speaker for entry in runner.transcript}
    tally = next(e for e in events if e["type"] == "tally")
    assert sum(tally["counts"].values()) == 1


async def test_moderator_failure_aborts_debate():
    provider = ScriptedProvider(fail_for={"議長"})
    cfg = make_config(provider="scripted")
    moderator, debaters = seats(cfg, provider)
    events = await collect(DebateRunner(cfg, moderator, debaters))

    assert events[-1]["type"] == "debate_end"
    assert events[-1]["status"] == "error"
    assert "司会" in events[-1]["message"]
    assert not any(e["type"] == "turn_start" and e["speaker_id"] != "moderator" for e in events)


async def test_later_speakers_see_earlier_statements():
    provider = ScriptedProvider({TurnKind.ARGUMENT: "週休3日は生産性を上げます。"})
    cfg = make_config(provider="scripted", rounds=1)
    moderator, debaters = seats(cfg, provider)
    await collect(DebateRunner(cfg, moderator, debaters))

    second = next(r for r in provider.requests if r.speaker_name == "慎重派" and r.kind == TurnKind.ARGUMENT)
    assert "【第1ラウンド】推進派" in second.prompt
    assert "週休3日は生産性を上げます。" in second.prompt
    assert "慎重" in second.system


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("整理します。\n【判定】継続", "continue"),
        ("整理します。\n【判定】終了", "stop"),
        ("【判定】終了と迷いましたが\n【判定】継続", "continue"),
        ("判定なし", "continue"),
    ],
)
def test_parse_judgment(text, expected):
    assert parse_judgment(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("【賛成】理由は…", "賛成"),
        ("【条件付き賛成】条件は…", "条件付き賛成"),
        ("私は【反対】です", "反対"),
        ("どちらとも言えません", "不明"),
    ],
)
def test_parse_vote(text, expected):
    assert parse_vote(text) == expected


def test_config_rejects_duplicate_names():
    with pytest.raises(ValidationError, match="重複"):
        make_config(debaters=[{"name": "議長", "provider": "mock"}, {"name": "B", "provider": "mock"}])


def test_config_requires_two_debaters():
    with pytest.raises(ValidationError):
        make_config(debaters=[{"name": "A", "provider": "mock"}])
