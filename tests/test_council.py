from __future__ import annotations

import random

import pytest
from pydantic import ValidationError

from ai_council.council import (
    CouncilRunner,
    Seat,
    Version,
    choose_final,
    line_diff,
    parse_review,
    rotate,
)
from ai_council.providers import TurnKind

from .conftest import ScriptedProvider, make_config

NAMES = {"p1": "正確さ重視", "p2": "わかりやすさ重視", "p3": "実用性重視"}


def make_seats(cfg, provider):
    return [Seat(f"p{i}", p, provider, "m") for i, p in enumerate(cfg.participants, 1)]


async def run(cfg, provider, seed=0):
    runner = CouncilRunner(cfg, make_seats(cfg, provider), random.Random(seed))
    events = [e async for e in runner.run()]
    return runner, events


def of_type(events, kind):
    return [e for e in events if e["type"] == kind]


def approve(_req=None):
    return "【意見】\nよい原稿です。\n【判断】\n承認"


def revise(text):
    return f"【意見】\n直しました。\n【判断】\n修正\n【修正版】\n{text}"


def drafter_of(events):
    return [e for e in of_type(events, "lottery")][-1]["drafter_id"]


# ---- 全体の流れ ---------------------------------------------------------------


async def test_full_flow_with_mock(mock_provider):
    cfg = make_config(rounds=2)
    runner, events = await run(cfg, mock_provider)

    assert events[0]["type"] == "session_start"
    assert events[-1] == {"type": "session_end", "status": "completed", "message": ""}
    phases = [e["phase"] for e in of_type(events, "phase")]
    assert phases == ["planning", "draft", "revision", "revision", "final"]

    # 全員が方針の議論に参加し、初稿は1人だけが書く
    starts = of_type(events, "turn_start")
    assert sorted(e["speaker_id"] for e in starts if e["kind"] == "planning") == ["p1", "p2", "p3"]
    assert [e["speaker_id"] for e in starts if e["kind"] == "draft"] == [drafter_of(events)]

    # デモAIはラウンド1で全員が修正し、ラウンド2で全員が承認する
    versions = of_type(events, "version")
    assert [v["version"] for v in versions] == [1, 2, 3, 4]
    assert of_type(events, "consensus") == [{"type": "consensus", "version": 4}]
    final = of_type(events, "final")[0]
    assert final["unanimous"] is True and final["version"] == 4
    assert final["approvals"] == ["p1", "p2", "p3"]
    assert final["text"] == runner.versions[-1].text
    # 第4版を作った本人は、ラウンド2で自分の版を見直す番を省略する
    assert [s["speaker_id"] for s in of_type(events, "skip")] == [versions[-1]["author_id"]]
    assert len(of_type(events, "review")) == 5

    # 発言の断片を連結すると、turn_endの本文と一致する
    for end in of_type(events, "turn_end"):
        deltas = "".join(e["text"] for e in events if e["type"] == "delta" and e["turn_id"] == end["turn_id"])
        assert end["ok"] and deltas.strip() == end["text"]


async def test_revisions_carry_diffs():
    provider = ScriptedProvider(
        {
            TurnKind.DRAFT: "1行目\n2行目",
            TurnKind.REVISION: lambda req: revise("1行目\n2行目（改善）\n3行目") if req.version == 1 else approve(),
        }
    )
    cfg = make_config(provider="scripted", rounds=2)
    _, events = await run(cfg, provider)

    v2 = of_type(events, "version")[1]
    assert v2["diff"] == [
        {"op": "same", "text": "1行目"},
        {"op": "del", "text": "2行目"},
        {"op": "add", "text": "2行目（改善）"},
        {"op": "add", "text": "3行目"},
    ]
    assert (v2["added"], v2["removed"]) == (2, 1)


# ---- 対等性 -------------------------------------------------------------------


async def test_all_participants_get_identical_instructions_apart_from_identity():
    provider = ScriptedProvider({TurnKind.REVISION: approve})
    cfg = make_config(provider="scripted", rounds=1)
    await run(cfg, provider)

    systems = {r.speaker_name: r.system for r in provider.requests}
    assert len(systems) == 3
    normalized = {
        "\n".join(
            line
            for line in s.replace(name, "<名前>").splitlines()
            if not line.startswith(("ほかの参加者", "あなたの観点"))
        )
        for name, s in systems.items()
    }
    assert len(normalized) == 1  # 名前と観点以外は全員まったく同じ指示
    assert "リーダーや編集長はおらず" in next(iter(normalized))


async def test_drafter_is_chosen_by_lottery():
    provider = ScriptedProvider({TurnKind.REVISION: approve})
    cfg = make_config(provider="scripted", rounds=1)
    drafters = set()
    for seed in range(20):
        _, events = await run(cfg, provider, seed=seed)
        drafters.add(drafter_of(events))
    assert drafters == {"p1", "p2", "p3"}  # 誰が初稿を書くかは、くじ引きで決まる


async def test_failed_draft_is_retried_by_another_participant():
    provider = ScriptedProvider({TurnKind.REVISION: approve})
    cfg = make_config(provider="scripted", rounds=1)
    _, events = await run(cfg, provider, seed=3)
    first = drafter_of(events)

    provider = ScriptedProvider({TurnKind.REVISION: approve}, fail_for={(NAMES[first], TurnKind.DRAFT)})
    _, events = await run(cfg, provider, seed=3)
    lotteries = of_type(events, "lottery")
    assert lotteries[0]["drafter_id"] == first
    assert lotteries[1]["drafter_id"] != first and lotteries[1]["retry"] is True
    assert of_type(events, "version")[0]["author_id"] == lotteries[1]["drafter_id"]


async def test_drafter_reviews_last_and_order_rotates():
    provider = ScriptedProvider({TurnKind.REVISION: "【意見】\n気になる点があります。\n【判断】\n修正"})
    cfg = make_config(provider="scripted", rounds=3)
    _, events = await run(cfg, provider)

    drafter = drafter_of(events)
    orders = [e["order"] for e in of_type(events, "phase") if e["phase"] == "revision"]
    assert orders[0][-1] == drafter  # 初稿を書いた本人は、ほかの全員が見たあとに見る
    assert orders[1] == rotate(orders[0], 2)
    assert orders[2] == rotate(orders[0], 3)


def test_rotate_gives_everyone_each_position():
    people = ["a", "b", "c"]
    assert {rotate(people, r)[0] for r in (1, 2, 3)} == {rotate(people, r)[-1] for r in (1, 2, 3)} == set(people)


# ---- 承認と完成 ---------------------------------------------------------------


async def test_unanimous_approval_finishes_early():
    provider = ScriptedProvider({TurnKind.REVISION: approve})
    cfg = make_config(provider="scripted", rounds=3)
    _, events = await run(cfg, provider)

    # 初稿の担当者はすでに承認済みなので、残る2人が承認した時点で完成する
    reviews = of_type(events, "review")
    assert len(reviews) == 2
    assert [e["round"] for e in of_type(events, "phase") if e["phase"] == "revision"] == [1]
    final = of_type(events, "final")[0]
    assert final["unanimous"] is True and final["version"] == 1


async def test_revision_resets_approvals():
    def reply(req):
        if req.version == 1 and req.speaker_name == first_reviewer[0]:
            return revise("改善した原稿")
        return approve()

    first_reviewer: list[str] = []
    provider = ScriptedProvider({TurnKind.DRAFT: "最初の原稿", TurnKind.REVISION: reply})
    cfg = make_config(provider="scripted", rounds=3)
    runner = CouncilRunner(cfg, make_seats(cfg, provider), random.Random(0))
    events = []
    async for e in runner.run():
        if e["type"] == "phase" and e["phase"] == "revision" and not first_reviewer:
            first_reviewer.append(NAMES[e["order"][0]])
        events.append(e)

    reviews = of_type(events, "review")
    assert reviews[0]["decision"] == "revise"
    assert reviews[0]["approvals"] == [reviews[0]["speaker_id"]]  # 新しい版の承認は修正した本人だけから始まる
    final = of_type(events, "final")[0]
    assert final["version"] == 2 and final["unanimous"] is True
    assert final["text"] == "改善した原稿"
    # 第2版は、修正した本人以外の全員（初稿の担当者を含む）が改めて承認している
    reviser = reviews[0]["speaker_id"]
    approved_v2 = {r["speaker_id"] for r in reviews if r["reviewed_version"] == 2 and r["decision"] == "approve"}
    assert approved_v2 == {"p1", "p2", "p3"} - {reviser}
    assert drafter_of(events) in approved_v2


async def test_objection_blocks_consensus_and_most_approved_version_wins():
    def reply(req):
        if req.speaker_name == "実用性重視":
            return "【意見】\nまだ足りません。\n【判断】\n修正"  # 修正を求めるが、修正版は出さない
        return approve()

    provider = ScriptedProvider({TurnKind.REVISION: reply})
    cfg = make_config(provider="scripted", rounds=2)
    _, events = await run(cfg, provider)

    assert not of_type(events, "consensus")
    objections = [r for r in of_type(events, "review") if r["decision"] == "object"]
    assert objections and all(r["speaker_id"] == "p3" for r in objections)
    final = of_type(events, "final")[0]
    assert final["unanimous"] is False
    assert final["version"] == 1 and "p3" not in final["approvals"]


async def test_participants_who_already_approved_are_skipped():
    provider = ScriptedProvider({TurnKind.REVISION: "【意見】\n気になる点があります。\n【判断】\n修正"})
    cfg = make_config(provider="scripted", rounds=2)
    _, events = await run(cfg, provider)

    drafter = drafter_of(events)
    # 誰も修正版を出さないので第1版のまま。初稿の担当者は承認済みなので、毎ラウンド番を省略する
    assert [s["speaker_id"] for s in of_type(events, "skip")] == [drafter, drafter]
    assert drafter not in {r["speaker_id"] for r in of_type(events, "review")}
    assert len(of_type(events, "review")) == 4


async def test_unchanged_revision_counts_as_objection():
    provider = ScriptedProvider({TurnKind.DRAFT: "同じ原稿", TurnKind.REVISION: revise("同じ原稿")})
    cfg = make_config(provider="scripted", rounds=1)
    _, events = await run(cfg, provider)
    assert len(of_type(events, "version")) == 1
    assert {r["decision"] for r in of_type(events, "review")} == {"object"}


def test_choose_final_prefers_most_approved_then_newest():
    v1 = Version(1, "a", "一", {"a", "b"})
    v2 = Version(2, "c", "二", {"c"})
    assert choose_final([v1, v2], {"a", "b", "c"}) == (v1, False)
    v3 = Version(3, "b", "三", {"b", "a"})
    assert choose_final([v1, v2, v3], {"a", "b", "c"}) == (v3, False)
    v4 = Version(4, "a", "四", {"a", "b", "c"})
    assert choose_final([v1, v2, v3, v4], {"a", "b", "c"}) == (v4, True)


# ---- 議論の記録 ---------------------------------------------------------------


async def test_reviewers_see_current_draft_and_opinions_but_not_old_versions():
    def reply(req):
        if req.version == 1:
            return revise("第2版の本文")
        return approve()

    provider = ScriptedProvider({TurnKind.DRAFT: "第1版の本文", TurnKind.REVISION: reply})
    cfg = make_config(provider="scripted", rounds=2)
    await run(cfg, provider)

    later = [r for r in provider.requests if r.kind == TurnKind.REVISION and r.version == 2]
    assert later
    prompt = later[0].prompt
    assert "# 現在の原稿（第2版" in prompt and "第2版の本文" in prompt
    assert "第1版の本文" not in prompt  # 古い版の全文は渡さず、意見だけを残す
    assert "直しました。" in prompt


async def test_planning_statements_are_shared():
    provider = ScriptedProvider(
        {TurnKind.PLANNING: lambda req: f"{req.speaker_name}は結論から書くべきだと考えます。", TurnKind.REVISION: approve}
    )
    cfg = make_config(provider="scripted", rounds=1)
    await run(cfg, provider)

    second = [r for r in provider.requests if r.kind == TurnKind.PLANNING][1]
    assert "【方針の議論】正確さ重視" in second.prompt
    draft = next(r for r in provider.requests if r.kind == TurnKind.DRAFT)
    assert all(f"{n}は結論から書くべきだと考えます。" in draft.prompt for n in NAMES.values())


# ---- 失敗への対応 -------------------------------------------------------------


async def test_failed_reviewer_is_skipped():
    provider = ScriptedProvider({TurnKind.REVISION: approve}, fail_for={("実用性重視", TurnKind.REVISION)})
    cfg = make_config(provider="scripted", rounds=1)
    _, events = await run(cfg, provider, seed=1)
    assert any(not e["ok"] for e in of_type(events, "turn_end"))
    assert events[-1]["status"] == "completed"
    final = of_type(events, "final")[0]
    assert final["unanimous"] is (drafter_of(events) == "p3")


async def test_planning_with_no_statements_aborts():
    provider = ScriptedProvider(fail_for={(n, TurnKind.PLANNING) for n in NAMES.values()})
    cfg = make_config(provider="scripted")
    _, events = await run(cfg, provider)
    assert events[-1]["status"] == "error"
    assert not of_type(events, "final")


# ---- 解析 ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "opinion", "decision", "revised"),
    [
        ("【意見】\nよいです。\n【判断】\n承認", "よいです。", "approve", ""),
        ("【意見】\n直します。\n【判断】\n修正\n【修正版】\n新しい本文\n2行目", "直します。", "revise", "新しい本文\n2行目"),
        ("**【意見】**\n直します。\n\n**【判断】** 修正\n\n**【修正版】**\n本文", "直します。", "revise", "本文"),
        ("【意見】直します。【判断】「承認」", "直します。", "approve", ""),
        ("【意見】\n足りません。\n【修正版】\n本文", "足りません。", "revise", "本文"),
        ("【意見】\n足りません。", "足りません。", "unknown", ""),
        ("意見の見出しなし\n【判断】修正\n【修正版】本文が同じ行", "意見の見出しなし", "revise", "本文が同じ行"),
    ],
)
def test_parse_review(text, opinion, decision, revised):
    review = parse_review(text)
    assert (review.opinion, review.decision, review.revised) == (opinion, decision, revised)


def test_line_diff_handles_replacements():
    assert line_diff("a\nb\nc", "a\nB\nc") == [
        {"op": "same", "text": "a"},
        {"op": "del", "text": "b"},
        {"op": "add", "text": "B"},
        {"op": "same", "text": "c"},
    ]


# ---- 設定 -------------------------------------------------------------------


def test_config_rejects_blank_prompt():
    with pytest.raises(ValidationError, match="プロンプトを入力してください"):
        make_config(prompt="   ")


def test_config_rejects_duplicate_names():
    with pytest.raises(ValidationError, match="重複"):
        make_config(names=("A", "A"))


def test_config_allows_two_to_six_participants():
    make_config(names=("A", "B"))
    make_config(names=tuple("ABCDEF"))
    for names in [("A",), tuple("ABCDEFG")]:
        with pytest.raises(ValidationError):
            make_config(names=names)
