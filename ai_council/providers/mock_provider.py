"""デモ用のAI。APIキーなしで討論の流れを試すために、定型文を組み立てて返す。"""

from __future__ import annotations

import asyncio
import zlib
from collections.abc import AsyncIterator

from ..config import Settings
from .base import Provider, ProviderStatus, TurnKind, TurnRequest

_RATINGS = ["賛成", "条件付き賛成", "賛成", "反対"]
_RATING_REASONS = {
    "賛成": "懸念点にも配慮されており、現実的に進められる案だと考えます。",
    "条件付き賛成": "方向性には賛成ですが、見直しの基準を数字で明記することを条件とします。",
    "反対": "影響を受ける人々への対策が、まだ具体的になっていないと考えます。",
}

_PROPOSAL_FOCUS = [
    ("小さな範囲で試行し、効果を検証してから広げる", "試行の範囲と期間"),
    ("影響を受ける人々の声を聞く場を設けたうえで進める", "意見を集める方法"),
    ("費用と効果の目標を数字で定め、定期的に見直す", "目標とする数字"),
]

_ARGUMENT_POINTS = [
    "短期的な効果だけでなく、5年後、10年後にどのような影響が残るかを考える必要があります",
    "影響を受ける人々の立場の違いを丁寧に見ていくことが欠かせません",
    "費用と効果のバランスを、できるだけ具体的な数字で比べるべきです",
    "小さく試して検証しながら広げる、段階的な進め方が現実的です",
    "うまくいかなかったときに引き返せる仕組みを最初から用意しておくべきです",
]


def _pick(seed: str, items: list[str], offset: int = 0) -> str:
    return items[(zlib.crc32(seed.encode()) + offset) % len(items)]


class MockProvider(Provider):
    id = "mock"
    label = "デモ"

    def __init__(self, settings: Settings) -> None:
        self._delay = settings.mock_delay

    async def status(self) -> ProviderStatus:
        return ProviderStatus(True, "", ["demo"], "demo")

    async def stream(self, req: TurnRequest) -> AsyncIterator[str]:
        text = _compose(req)
        step = 3
        for i in range(0, len(text), step):
            if self._delay:
                await asyncio.sleep(self._delay)
            yield text[i : i + step]


def _compose(req: TurnRequest) -> str:
    topic = req.topic
    # 立場の説明は最初の一文だけを引用する（例：「議題に前向きな立場」）
    stance = (req.stance.split("。")[0] if req.stance else "") or "特に立場を決めず、自分の考えで論じる"
    others = req.others or ["ほかの参加者"]
    other = _pick(req.speaker_name + str(req.round), others)

    if req.kind == TurnKind.ARGUMENT:
        point = _pick(req.speaker_name, _ARGUMENT_POINTS, req.round)
        if req.round <= 1:
            text = (
                f"私は「{stance}」という立場から発言します。\n\n"
                f"「{topic}」を考えるうえで、{point}。"
                "議論が理想論だけで終わらないよう、現実の制約も踏まえて考えたいと思います。"
            )
        else:
            text = (
                f"{other}さんのご意見には、うなずける部分が多いと感じました。"
                "ただ、前提となっている条件については、もう少し慎重に確かめる必要があると思います。\n\n"
                f"私の「{stance}」という立場から付け加えると、{point}。"
                "この点を押さえれば、お互いの主張はかなり近づけるのではないでしょうか。"
            )
        if req.asks_continue_vote:
            # 第1ラウンドでは続行、それ以降は参加者によって判断が分かれる
            stop = req.round >= 2 and zlib.crc32(req.speaker_name.encode()) % 2 == 0
            text += "\n\n【終了】" if stop else "\n\n【継続】"
        return text

    if req.kind == TurnKind.PROPOSAL:
        action, issue = _PROPOSAL_FOCUS[zlib.crc32(req.speaker_name.encode()) % len(_PROPOSAL_FOCUS)]
        return (
            f"**結論**\n「{topic}」については、{action}。\n\n"
            "**合意できる点**\n"
            "- 一律に決めず、状況に応じて判断すること\n"
            f"- 「{stance}」という観点も含め、複数の立場の懸念に応えること\n\n"
            "**残る相違点**\n"
            "- どこまでのリスクを許容するか\n\n"
            "**今後の課題**\n"
            f"- {issue}を具体的に決めること\n\n"
            "（これはデモAIによる定型の案です。実際のAIを使うには、APIキーを設定してください。）"
        )

    # EVALUATION
    lines = []
    for no in req.proposal_numbers:
        rating = _pick(f"{req.speaker_name}-{no}", _RATINGS)
        lines.append(f"案{no}：【{rating}】{_RATING_REASONS[rating]}")
    return "\n".join(lines)
