"""討論エンジン。司会と討論者の発言順を管理し、画面に送るイベントを順に生成する。

討論の流れ：
1. 開会（司会が論点を提示）
2. ラウンド（討論者が順に発言）→ 司会の中間整理と継続判定（最終ラウンドの後を除く）
   司会が「【判定】終了」と判断した場合は、残りのラウンドを省略する
3. 結論案（司会）
4. 最終意見（討論者が結論案に賛否を表明）
5. 最終結論（司会）
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass

from .prompts import (
    VOTE_LABELS,
    Entry,
    debater_system,
    instruction,
    moderator_system,
    render_context,
)
from .providers import Provider, ProviderError, TurnKind, TurnRequest
from .schemas import DebateConfig, ParticipantConfig

logger = logging.getLogger(__name__)

MAX_TOKENS = 16000
_JUDGMENT_RE = re.compile(r"【判定】\s*(継続|終了)")
_VOTE_RE = re.compile(r"【(" + "|".join(sorted(VOTE_LABELS, key=len, reverse=True)) + r")】")


@dataclass
class Seat:
    """討論の席。参加者の設定と、使うAIをまとめたもの。"""

    id: str
    role: str  # "moderator" または "debater"
    config: ParticipantConfig
    provider: Provider
    model: str

    def public(self) -> dict:
        return {
            "id": self.id,
            "role": self.role,
            "name": self.config.name,
            "stance": self.config.stance,
            "provider": self.provider.id,
            "provider_label": self.provider.label,
            "model": self.model,
        }


@dataclass
class _TurnResult:
    text: str = ""
    error: str = ""


class _Abort(Exception):
    pass


def parse_judgment(text: str) -> str:
    """司会の中間整理から判定を読み取る。見つからなければ継続とみなす。"""
    matches = _JUDGMENT_RE.findall(text)
    return "stop" if matches and matches[-1] == "終了" else "continue"


def parse_vote(text: str) -> str:
    match = _VOTE_RE.search(text)
    return match.group(1) if match else "不明"


class DebateRunner:
    def __init__(self, cfg: DebateConfig, moderator: Seat, debaters: list[Seat]) -> None:
        self.cfg = cfg
        self.moderator = moderator
        self.debaters = debaters
        self.transcript: list[Entry] = []
        self._turns = 0

    async def run(self) -> AsyncIterator[dict]:
        yield {
            "type": "debate_start",
            "topic": self.cfg.topic,
            "rounds": self.cfg.rounds,
            "participants": [s.public() for s in [self.moderator, *self.debaters]],
        }
        status, message = "completed", ""
        try:
            async for event in self._proceed():
                yield event
        except _Abort as e:
            status, message = "error", str(e)
        yield {"type": "debate_end", "status": status, "message": message}

    async def _proceed(self) -> AsyncIterator[dict]:
        cfg = self.cfg

        # 1. 開会
        yield _phase("opening", "開会と論点提示")
        async for event in self._moderator_turn(TurnKind.OPENING, "開会"):
            yield event

        # 2. ラウンド
        for rnd in range(1, cfg.rounds + 1):
            yield _phase("round", f"第{rnd}ラウンド", rnd)
            spoke = 0
            for seat in self.debaters:
                result = _TurnResult()
                async for event in self._turn(seat, TurnKind.ARGUMENT, f"第{rnd}ラウンド", rnd, result):
                    yield event
                spoke += 0 if result.error else 1
            if spoke == 0:
                raise _Abort("このラウンドでは誰も発言できなかったため、討論を中止しました。")
            if rnd == cfg.rounds:
                break

            yield _phase("interim", f"第{rnd}ラウンドの整理", rnd)
            result = _TurnResult()
            async for event in self._moderator_turn(TurnKind.INTERIM, f"第{rnd}ラウンドの整理", rnd, result):
                yield event
            decision = parse_judgment(result.text)
            stop = decision == "stop" and cfg.early_stop
            yield {"type": "judgment", "round": rnd, "decision": decision, "early_stop": stop}
            if stop:
                break

        # 3. 結論案
        yield _phase("proposal", "結論案")
        async for event in self._moderator_turn(TurnKind.PROPOSAL, "結論案"):
            yield event

        # 4. 最終意見
        yield _phase("final", "最終意見")
        counts = {label: 0 for label in VOTE_LABELS}
        for seat in self.debaters:
            result = _TurnResult()
            async for event in self._turn(seat, TurnKind.FINAL, "最終意見", 0, result):
                yield event
            if result.error:
                continue
            vote = parse_vote(result.text)
            counts[vote] = counts.get(vote, 0) + 1
            yield {"type": "vote", "speaker_id": seat.id, "vote": vote}
        yield {"type": "tally", "counts": counts}

        # 5. 最終結論
        yield _phase("conclusion", "最終結論")
        async for event in self._moderator_turn(TurnKind.CONCLUSION, "最終結論"):
            yield event

    async def _moderator_turn(
        self, kind: TurnKind, label: str, round_no: int = 0, result: _TurnResult | None = None
    ) -> AsyncIterator[dict]:
        """司会の発言。司会が失敗すると進行できないので、討論を中止する。"""
        result = result or _TurnResult()
        async for event in self._turn(self.moderator, kind, label, round_no, result):
            yield event
        if result.error:
            raise _Abort(f"司会の発言に失敗したため、討論を中止しました。（{result.error}）")

    async def _turn(
        self, seat: Seat, kind: TurnKind, label: str, round_no: int, result: _TurnResult
    ) -> AsyncIterator[dict]:
        self._turns += 1
        turn_id = f"t{self._turns}"
        yield {
            "type": "turn_start",
            "turn_id": turn_id,
            "speaker_id": seat.id,
            "kind": kind.value,
            "label": label,
            "round": round_no,
        }

        req = self._build_request(seat, kind, round_no)
        chunks: list[str] = []
        try:
            async for text in seat.provider.stream(req):
                chunks.append(text)
                yield {"type": "delta", "turn_id": turn_id, "text": text}
        except ProviderError as e:
            result.error = str(e)
        except Exception as e:  # noqa: BLE001 - 想定外の失敗でも討論全体は止めない
            logger.exception("発言の生成中に予期しないエラー")
            result.error = f"予期しないエラーが発生しました（{type(e).__name__}）。"

        text = "".join(chunks).strip()
        if not result.error and not text:
            result.error = "応答が空でした。"
        if result.error:
            yield {"type": "turn_end", "turn_id": turn_id, "ok": False, "error": result.error}
            return

        result.text = text
        self.transcript.append(Entry(seat.config.name, label, text))
        yield {"type": "turn_end", "turn_id": turn_id, "ok": True, "text": text}

    def _build_request(self, seat: Seat, kind: TurnKind, round_no: int) -> TurnRequest:
        cfg = self.cfg
        if seat.role == "moderator":
            system = moderator_system(cfg)
            others = [d.config.name for d in self.debaters]
        else:
            system = debater_system(cfg, seat.config)
            others = [d.config.name for d in self.debaters if d.id != seat.id]
        prompt = render_context(cfg, self.transcript) + "\n\n# あなたへの指示\n" + instruction(kind, cfg, round_no)
        return TurnRequest(
            system=system,
            prompt=prompt,
            model=seat.model,
            max_tokens=MAX_TOKENS,
            kind=kind,
            speaker_name=seat.config.name,
            stance=seat.config.stance,
            topic=cfg.topic,
            round=round_no,
            total_rounds=cfg.rounds,
            others=others,
        )


def _phase(phase: str, title: str, round_no: int = 0) -> dict:
    return {"type": "phase", "phase": phase, "title": title, "round": round_no}
