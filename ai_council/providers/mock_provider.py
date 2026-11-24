"""デモ用のAI。APIキーなしで合議の流れを試すために、定型文を組み立てて返す。

修正ラウンド1では各自が自分の観点から1行を書き足して修正版を出し、
修正ラウンド2以降では承認する。これで「修正→全員の承認→完成」の流れが一通り見られる。
"""

from __future__ import annotations

import asyncio
import zlib
from collections.abc import AsyncIterator

from ..config import Settings
from .base import Provider, ProviderStatus, TurnKind, TurnRequest

_PLAN_POINTS = [
    "最初に結論を示し、そのあとで理由や詳しい説明を続ける構成",
    "読み手がすぐに行動に移せる、具体的な例や手順",
    "見落としやすい注意点や、よくある誤解への言及",
    "専門用語を避けた、誰にでも伝わる言葉づかい",
    "根拠となる考え方を示し、断定しすぎない書き方",
]


def _pick(seed: str, items: list[str], offset: int = 0) -> str:
    return items[(zlib.crc32(seed.encode()) + offset) % len(items)]


def _point(req: TurnRequest, offset: int = 0) -> str:
    """参加者ごとに違う観点の文を選ぶ（名前の並び順で割り振るので、5人までは重ならない）。"""
    everyone = sorted([*req.others, req.speaker_name])
    return _PLAN_POINTS[(everyone.index(req.speaker_name) + offset) % len(_PLAN_POINTS)]


class MockProvider(Provider):
    id = "mock"
    label = "デモ"

    def __init__(self, settings: Settings) -> None:
        self._delay = settings.mock_delay

    async def status(self) -> ProviderStatus:
        return ProviderStatus(True, "", ["demo"], "demo")

    async def stream(self, req: TurnRequest) -> AsyncIterator[str]:
        text = _compose(req)
        step = 4
        for i in range(0, len(text), step):
            if self._delay:
                await asyncio.sleep(self._delay)
            yield text[i : i + step]


def _short(text: str, limit: int = 30) -> str:
    one_line = " ".join(text.split())
    return one_line if len(one_line) <= limit else one_line[:limit] + "…"


def _compose(req: TurnRequest) -> str:
    # 観点の説明は最初の一文だけを使う（例：「事実関係と論理の正しさを重視する」）
    stance = (req.stance.split("。")[0] if req.stance else "") or "全体のバランス"
    others = req.others or ["ほかの参加者"]
    other = _pick(req.speaker_name, others)

    if req.kind == TurnKind.PLANNING:
        point = _point(req)
        return (
            f"「{stance}」という観点から意見を述べます。\n\n"
            f"このプロンプトには、{point}が欠かせないと考えます。"
            f"{other}さんの意見とも両立できるはずなので、構成の中でうまく組み合わせましょう。"
        )

    if req.kind == TurnKind.DRAFT:
        return (
            f"## {_short(req.user_prompt)}\n\n"
            "**要点**\n"
            "- まず結論として、目的をはっきりさせたうえで、小さく始めて確かめながら進めるのがよいでしょう。\n"
            "- 判断に迷ったときは、影響を受ける人の立場から考え直します。\n\n"
            "**詳しく**\n"
            "最初に目的と、うまくいったかどうかを判断する基準を決めます。"
            "次に、範囲を限定して試し、結果を見て進め方を調整します。\n\n"
            "**まとめ**\n"
            "目的、基準、小さな試行の3つをそろえることが、確実に前へ進むための近道です。\n\n"
            "（これはデモAIによる定型の回答です。実際のAIを使うには、APIキーを設定してください。）"
        )

    # REVISION
    if req.round >= 2:
        return (
            "【意見】\n"
            f"{other}さんの修正で、ほかの観点とのバランスがよくなりました。"
            f"「{stance}」という観点から見ても、このまま完成としてよい内容です。\n\n"
            "【判断】\n承認"
        )
    addition = f"- {stance}という観点から：{_point(req, 2)}も意識します。"
    lines = req.current_draft.splitlines()
    try:
        insert_at = next(i for i, line in enumerate(lines) if line.startswith("**詳しく**")) - 1
    except StopIteration:
        insert_at = len(lines)
    revised = "\n".join(lines[:insert_at] + [addition] + lines[insert_at:])
    return (
        "【意見】\n"
        f"全体の構成はわかりやすいと思います。ただ、「{stance}」という観点がまだ弱いので、"
        "要点に1行を加えました。ほかの部分は変えていません。\n\n"
        "【判断】\n修正\n\n"
        f"【修正版】\n{revised}"
    )
