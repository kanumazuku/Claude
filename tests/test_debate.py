from __future__ import annotations

import random

import pytest
from pydantic import ValidationError

from ai_council.debate import (
    DebateRunner,
    Seat,
    parse_continue_vote,
    parse_ratings,
    rotate,
    tally,
)
from ai_council.providers import TurnKind

from .conftest import ScriptedProvider, make_config


def make_seats(cfg, provider):
    return [Seat(f"p{i}", p, provider, "m") for i, p in enumerate(cfg.participants, 1)]


async def run(cfg, provider, seed=0):
    runner = DebateRunner(cfg, make_seats(cfg, provider), random.Random(seed))
    events = [e async for e in runner.run()]
    return runner, events


def of_type(events, kind):
    return [e for e in events if e["type"] == kind]


def rate_all(rating):
    """評価対象の案すべてに同じ評価を付けるテスト用の返答。"""
    return lambda req: "\n".join(f"案{n}：【{rating}】理由です。" for n in req.proposal_numbers)


# ---- 全体の流れ ---------------------------------------------------------------


async def test_full_debate_with_mock(mock_provider):
    cfg = make_config(rounds=2)
    _, events = await run(cfg, mock_provider)

    assert events[0]["type"] == "debate_start"
    assert events[-1] == {"type": "debate_end", "status": "completed", "message": ""}
    phases = [e["phase"] for e in of_type(events, "phase")]
    assert phases == ["round", "round", "proposal", "evaluation", "result"]

    # 全員がラウンドで2回、結論案と評価で1回ずつ発言する（特別な役割の人はいない）
    starts = of_type(events, "turn_start")
    for pid in ("p1", "p2", "p3"):
        kinds = [e["kind"] for e in starts if e["speaker_id"] == pid]
        assert kinds == ["argument", "argument", "proposal", "evaluation"]

    # 発言の断片を連結すると、turn_endの本文と一致する（同時進行の発言でも混ざらない）
    for end in of_type(events, "turn_end"):
        deltas = "".join(e["text"] for e in events if e["type"] == "delta" and e["turn_id"] == end["turn_id"])
        assert end["ok"] and deltas.strip() == end["text"]

    result = of_type(events, "result")[0]
    assert len(result["proposals"]) == 3
    assert result["adopted"] in {1, 2, 3}
    assert result["text"]


async def test_all_participants_get_identical_instructions_apart_from_identity():
    provider = ScriptedProvider()
    cfg = make_config(provider="scripted", rounds=1)
    await run(cfg, provider)

    systems = {r.speaker_name: r.system for r in provider.requests}
    normalized = {
        s.replace(name, "<名前>").replace(f"{name}の立場", "<立場>") for name, s in systems.items()
    }
    # 名前と立場以外は全員まったく同じ指示を受けている
    others_removed = {
        "\n".join(line for line in s.splitlines() if not line.startswith(("ほかの参加者", "あなたの立場")))
        for s in normalized
    }
    assert len(others_removed) == 1
    assert "司会" in next(iter(others_removed))  # 「司会や議長はいない」と明記している
    assert "議長はおらず" in next(iter(others_removed))


async def test_speaking_order_rotates_each_round():
    provider = ScriptedProvider()
    cfg = make_config(provider="scripted", rounds=3)
    _, events = await run(cfg, provider)

    orders = [e["order"] for e in of_type(events, "phase") if e["phase"] == "round"]
    assert orders == [["p1", "p2", "p3"], ["p2", "p3", "p1"], ["p3", "p1", "p2"]]
    spoken = [
        [e["speaker_id"] for e in of_type(events, "turn_start") if e.get("round") == r] for r in (1, 2, 3)
    ]
    assert spoken == orders


def test_rotate_gives_everyone_each_position():
    people = ["a", "b", "c"]
    firsts = {rotate(people, r)[0] for r in (1, 2, 3)}
    lasts = {rotate(people, r)[-1] for r in (1, 2, 3)}
    assert firsts == lasts == set(people)


# ---- 継続の多数決 -------------------------------------------------------------


async def test_majority_stop_ends_rounds_early():
    def reply(req):
        return "主張です。\n【終了】" if req.speaker_name in {"推進派", "慎重派"} else "主張です。\n【継続】"

    provider = ScriptedProvider({TurnKind.ARGUMENT: reply})
    cfg = make_config(provider="scripted", rounds=4)
    _, events = await run(cfg, provider)

    assert of_type(events, "round_decision") == [
        {"type": "round_decision", "round": 1, "continue": 1, "stop": 2, "decision": "stop", "early_stop": True}
    ]
    assert [e["round"] for e in of_type(events, "phase") if e["phase"] == "round"] == [1]
    assert len(of_type(events, "continue_vote")) == 3


async def test_minority_stop_continues():
    def reply(req):
        return "主張です。\n【終了】" if req.speaker_name == "推進派" else "主張です。\n【継続】"

    provider = ScriptedProvider({TurnKind.ARGUMENT: reply})
    cfg = make_config(provider="scripted", rounds=3)
    _, events = await run(cfg, provider)

    assert [e["decision"] for e in of_type(events, "round_decision")] == ["continue", "continue"]
    assert [e["round"] for e in of_type(events, "phase") if e["phase"] == "round"] == [1, 2, 3]


async def test_tied_vote_continues():
    def reply(req):
        return "主張です。\n【終了】" if req.speaker_name == "A" else "主張です。\n【継続】"

    provider = ScriptedProvider({TurnKind.ARGUMENT: reply})
    cfg = make_config(provider="scripted", names=("A", "B"), rounds=2)
    _, events = await run(cfg, provider)
    assert of_type(events, "round_decision")[0]["decision"] == "continue"


async def test_early_stop_can_be_disabled():
    provider = ScriptedProvider({TurnKind.ARGUMENT: "主張です。\n【終了】"})
    cfg = make_config(provider="scripted", rounds=3, early_stop=False)
    _, events = await run(cfg, provider)
    assert [e["round"] for e in of_type(events, "phase") if e["phase"] == "round"] == [1, 2, 3]
    assert all(e["decision"] == "stop" and not e["early_stop"] for e in of_type(events, "round_decision"))


async def test_last_round_does_not_ask_for_vote():
    provider = ScriptedProvider()
    cfg = make_config(provider="scripted", rounds=2)
    await run(cfg, provider)
    asks = {(r.round, r.asks_continue_vote) for r in provider.requests if r.kind == TurnKind.ARGUMENT}
    assert asks == {(1, True), (2, False)}


# ---- 結論案と匿名評価 -----------------------------------------------------------


async def test_proposals_are_written_independently():
    provider = ScriptedProvider({TurnKind.PROPOSAL: lambda req: f"{req.speaker_name}の秘密の案"})
    cfg = make_config(provider="scripted", rounds=1)
    await run(cfg, provider)

    proposal_prompts = [r.prompt for r in provider.requests if r.kind == TurnKind.PROPOSAL]
    assert len(proposal_prompts) == 3
    assert len(set(proposal_prompts)) == 1  # 全員が同じ情報だけを見て書く
    assert "秘密の案" not in proposal_prompts[0]


async def test_proposals_run_concurrently():
    provider = ScriptedProvider(pause=0.001)
    cfg = make_config(provider="scripted", rounds=1)
    _, events = await run(cfg, provider)

    proposal_turns = {e["turn_id"] for e in of_type(events, "turn_start") if e["kind"] == "proposal"}
    deltas = [e["turn_id"] for e in of_type(events, "delta") if e["turn_id"] in proposal_turns]
    # 3人の発言が交互に届いている（1人ずつ順番に書いていれば、切り替わりは2回だけになる）
    switches = sum(1 for a, b in zip(deltas, deltas[1:]) if a != b)
    assert switches > len(proposal_turns) - 1


async def test_evaluation_is_anonymous_and_excludes_own_proposal():
    provider = ScriptedProvider(
        {
            TurnKind.PROPOSAL: "案の本文です。",
            TurnKind.EVALUATION: rate_all("賛成"),
        }
    )
    cfg = make_config(provider="scripted", rounds=1)
    _, events = await run(cfg, provider)

    numbers = {e["speaker_id"]: e["proposal_no"] for e in of_type(events, "turn_start") if e["kind"] == "proposal"}
    assert sorted(numbers.values()) == [1, 2, 3]
    names = {"p1": "推進派", "p2": "慎重派", "p3": "現実派"}
    for req in (r for r in provider.requests if r.kind == TurnKind.EVALUATION):
        own = numbers[next(pid for pid, n in names.items() if n == req.speaker_name)]
        assert own not in req.proposal_numbers
        assert sorted(req.proposal_numbers) == sorted(set(numbers.values()) - {own})
        proposals_part = req.prompt.split("# 結論案（作成者は伏せています）")[1].split("# あなたへの指示")[0]
        assert not any(name in proposals_part for name in names.values())  # 作成者名は伏せている


async def test_highest_support_is_adopted():
    def evaluate(req):
        # 全員が「推進派」の案だけを支持する
        return "\n".join(
            f"案{n}：【{'賛成' if n == favorite[0] else '反対'}】理由です。" for n in req.proposal_numbers
        )

    favorite: list[int] = []
    provider = ScriptedProvider({TurnKind.EVALUATION: evaluate})
    cfg = make_config(provider="scripted", rounds=1)
    runner = DebateRunner(cfg, make_seats(cfg, provider), random.Random(1))
    events = []
    async for e in runner.run():
        if e["type"] == "turn_start" and e["kind"] == "proposal" and e["speaker_id"] == "p1":
            favorite.append(e["proposal_no"])
        events.append(e)

    result = of_type(events, "result")[0]
    assert result["adopted"] == favorite[0]
    assert result["tie_break"] is False
    top = result["proposals"][0]
    assert top["author_id"] == "p1" and top["support"] == 100
    assert top["counts"]["賛成"] == 2  # 本人を除く2人が評価


async def test_tie_is_broken_by_lottery():
    provider = ScriptedProvider({TurnKind.EVALUATION: rate_all("賛成")})
    cfg = make_config(provider="scripted", rounds=1)
    adopted = set()
    for seed in range(20):
        _, events = await run(cfg, provider, seed=seed)
        result = of_type(events, "result")[0]
        assert result["tie_break"] is True
        assert result["proposals"][0]["no"] == result["adopted"]  # 採択した案が先頭に並ぶ
        adopted.add(next(p["author_id"] for p in result["proposals"] if p["no"] == result["adopted"]))
    assert adopted == {"p1", "p2", "p3"}  # くじ引きなので、誰の案も選ばれうる


async def test_no_adoption_when_everyone_opposes():
    provider = ScriptedProvider({TurnKind.EVALUATION: rate_all("反対")})
    cfg = make_config(provider="scripted", rounds=1)
    _, events = await run(cfg, provider)
    result = of_type(events, "result")[0]
    assert result["adopted"] is None and result["text"] == ""
    assert events[-1]["status"] == "completed"


# ---- 失敗への対応 -------------------------------------------------------------


async def test_failed_participant_is_skipped_and_others_continue():
    provider = ScriptedProvider(fail_for={("慎重派", TurnKind.ARGUMENT)})
    cfg = make_config(provider="scripted", rounds=1)
    runner, events = await run(cfg, provider)

    failed = [e for e in of_type(events, "turn_end") if not e["ok"]]
    assert len(failed) == 1 and "テスト用の失敗" in failed[0]["error"]
    assert "慎重派" not in {entry.speaker for entry in runner.transcript}
    assert events[-1]["status"] == "completed"


async def test_failed_proposal_is_left_out():
    provider = ScriptedProvider(
        {TurnKind.EVALUATION: rate_all("賛成")}, fail_for={("慎重派", TurnKind.PROPOSAL)}
    )
    cfg = make_config(provider="scripted", rounds=1)
    _, events = await run(cfg, provider)
    result = of_type(events, "result")[0]
    assert {p["author_id"] for p in result["proposals"]} == {"p1", "p3"}


async def test_round_with_no_statements_aborts():
    fail_all = {(n, TurnKind.ARGUMENT) for n in ("推進派", "慎重派", "現実派")}
    provider = ScriptedProvider(fail_for=fail_all)
    cfg = make_config(provider="scripted")
    _, events = await run(cfg, provider)
    assert events[-1]["status"] == "error"
    assert not of_type(events, "result")


async def test_later_speakers_see_earlier_statements():
    provider = ScriptedProvider({TurnKind.ARGUMENT: lambda req: f"{req.speaker_name}は生産性が上がると考えます。"})
    cfg = make_config(provider="scripted", rounds=1)
    await run(cfg, provider)

    second = next(r for r in provider.requests if r.speaker_name == "慎重派" and r.kind == TurnKind.ARGUMENT)
    assert "【第1ラウンド】推進派" in second.prompt
    assert "推進派は生産性が上がると考えます。" in second.prompt


# ---- 解析と集計 ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("主張です。\n【継続】", "continue"),
        ("主張です。\n【終了】", "stop"),
        ("【終了】と迷いましたが\n【継続】", "continue"),
        ("判断なし", "continue"),
    ],
)
def test_parse_continue_vote(text, expected):
    assert parse_continue_vote(text) == expected


def test_parse_ratings():
    text = "案1：【賛成】よい案です。\n案３：【条件付き賛成】数字が必要です。\n案2：【反対】自分の案\n案9：【賛成】"
    assert parse_ratings(text, [1, 3]) == {1: "賛成", 3: "条件付き賛成"}
    assert parse_ratings("評価できません", [1, 2]) == {1: "不明", 2: "不明"}


def test_tally_scores_and_excludes_self_rating():
    authors = {1: "a", 2: "b"}
    ratings = {
        "a": {1: "賛成", 2: "条件付き賛成"},  # 自分の案1への評価は数えない
        "b": {1: "反対"},
        "c": {1: "賛成", 2: "賛成"},
    }
    ranked, adopted, tie = tally(authors, ratings, random.Random(0))
    by_no = {s.no: s for s in ranked}
    assert (by_no[1].score, by_no[1].max_score) == (2, 4)
    assert (by_no[2].score, by_no[2].max_score) == (3, 4)
    assert adopted == 2 and tie is False


# ---- 設定 -------------------------------------------------------------------


def test_config_rejects_duplicate_names():
    with pytest.raises(ValidationError, match="重複"):
        make_config(names=("A", "A"))


def test_config_requires_two_participants():
    with pytest.raises(ValidationError):
        make_config(names=("A",))


def test_config_allows_up_to_six():
    make_config(names=tuple("ABCDEF"))
    with pytest.raises(ValidationError):
        make_config(names=tuple("ABCDEFG"))
