"""デモ用のAI。APIキーなしで討論の流れを試すために、定型文を組み立てて返す。"""

from __future__ import annotations

import asyncio
import zlib
from collections.abc import AsyncIterator

from ..config import Settings
from .base import Provider, ProviderStatus, TurnKind, TurnRequest

_VOTES = ["賛成", "条件付き賛成", "賛成", "反対"]

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

    if req.kind == TurnKind.OPENING:
        return (
            f"ただいまから「{topic}」について討論を始めます。\n\n"
            "今回は、次の3つの論点を中心に議論していきましょう。\n\n"
            "1. **目的**：そもそも何を実現したいのか\n"
            "2. **影響**：誰に、どのような良い影響と悪い影響があるのか\n"
            "3. **進め方**：実現するとしたら、どのような条件や手順が必要か\n\n"
            f"それでは第1ラウンドです。{'、'.join(others)}の順に、まずはご自身の立場を述べてください。"
        )

    if req.kind == TurnKind.ARGUMENT:
        point = _pick(req.speaker_name, _ARGUMENT_POINTS, req.round)
        if req.round <= 1:
            return (
                f"私は「{stance}」という立場から発言します。\n\n"
                f"「{topic}」を考えるうえで、{point}。"
                "議論が理想論だけで終わらないよう、現実の制約も踏まえて考えたいと思います。"
            )
        return (
            f"{other}さんのご意見には、うなずける部分が多いと感じました。"
            "ただ、前提となっている条件については、もう少し慎重に確かめる必要があると思います。\n\n"
            f"私の「{stance}」という立場から付け加えると、{point}。"
            "この点を押さえれば、お互いの主張はかなり近づけるのではないでしょうか。"
        )

    if req.kind == TurnKind.INTERIM:
        return (
            f"第{req.round}ラウンドを整理します。\n\n"
            "**合意しつつある点**\n"
            "- 一律に決めるのではなく、状況に応じた判断が必要だという認識は共有されています。\n\n"
            "**意見が分かれている点**\n"
            "- どこまでのリスクを許容するかについては、まだ隔たりがあります。\n\n"
            f"次のラウンドでは、「どのような条件がそろえば{topic}に前向きになれるか」に焦点を当ててください。\n\n"
            "【判定】継続"
        )

    if req.kind == TurnKind.PROPOSAL:
        return (
            "ここまでの議論を踏まえ、結論案を提示します。\n\n"
            f"**結論案**：「{topic}」については、目的と効果を検証できる範囲で段階的に進める。"
            "あわせて、影響を受ける人々への配慮と、うまくいかない場合に見直す仕組みを用意する。\n\n"
            "皆さん、この結論案に賛成か反対か、理由とあわせてお聞かせください。"
        )

    if req.kind == TurnKind.FINAL:
        vote = _pick(req.speaker_name, _VOTES)
        reasons = {
            "賛成": [
                "段階的に検証する進め方であれば、私が気にしていた点にも十分に応えられると考えます。",
                "議論を通じて懸念点が整理され、現実的な案にまとまったと感じます。",
            ],
            "条件付き賛成": ["方向性には賛成ですが、見直しの基準を具体的な数字で決めておくことを条件とします。"],
            "反対": ["議論は深まりましたが、懸念している影響への対策がまだ十分とは言えないと考えます。"],
        }
        return f"【{vote}】{_pick(req.speaker_name, reasons[vote], 1)}"

    # CONCLUSION
    return (
        "討論の結果をまとめます。\n\n"
        "**結論**\n"
        f"「{topic}」は、段階的に検証しながら進めることで合意しました。\n\n"
        "**合意した点**\n"
        "- 目的と効果を検証できる形で小さく始めること\n"
        "- 影響を受ける人々への配慮を欠かさないこと\n\n"
        "**残った相違点**\n"
        "- 許容できるリスクの大きさ、見直しの具体的な基準\n\n"
        "**今後の課題**\n"
        "- 見直しの基準を数字で定め、定期的に検証すること\n\n"
        "（これはデモAIによる定型の発言です。実際のAIを使うには、APIキーを設定してください。）"
    )
