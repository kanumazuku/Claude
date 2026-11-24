"""討論エンジン。参加者全員を対等に扱いながら、発言順と合議の手続きを管理する。

対等にするためのしくみ：
- 司会や議長はいない。全員が同じシステムプロンプト（名前と立場だけが違う）で発言する。
- ラウンドごとに発言順を1人ずつずらし、最初と最後に話す人が偏らないようにする。
- 議論を続けるかは、全員が発言の最後に書く【継続】【終了】の多数決で決める。
- 結論案は全員が同時に、互いの案を見ずに作る。
- 結論案は作成者を伏せて番号だけで示し、各自が自分以外の案を評価する。
- 最も支持を集めた案を結論として採択する。同点のときはくじ引きで決める。
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from fractions import Fraction

from .prompts import (
    RATING_LABELS,
    Entry,
    argument_instruction,
    evaluation_instruction,
    participant_system,
    proposal_instruction,
    render_context,
    render_proposals,
)
from .providers import Provider, ProviderError, TurnKind, TurnRequest
from .schemas import DebateConfig, ParticipantConfig

logger = logging.getLogger(__name__)

MAX_TOKENS = 16000
RATING_POINTS = {"賛成": 2, "条件付き賛成": 1, "反対": 0, "不明": 0}

_CONTINUE_RE = re.compile(r"【(継続|終了)】")
_RATING_RE = re.compile(
    r"案\s*(\d+)[^\n【]*?【(" + "|".join(sorted(RATING_LABELS, key=len, reverse=True)) + r")】"
)


@dataclass
class Seat:
    """参加者の席。設定と、使うAIをまとめたもの。"""

    id: str
    config: ParticipantConfig
    provider: Provider
    model: str

    def public(self) -> dict:
        return {
            "id": self.id,
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


@dataclass
class ProposalScore:
    no: int
    author_id: str
    counts: dict[str, int] = field(default_factory=lambda: {k: 0 for k in (*RATING_LABELS, "不明")})
    score: int = 0
    max_score: int = 0

    @property
    def support(self) -> Fraction:
        return Fraction(self.score, self.max_score) if self.max_score else Fraction(0)

    def public(self) -> dict:
        return {
            "no": self.no,
            "author_id": self.author_id,
            "counts": self.counts,
            "score": self.score,
            "max_score": self.max_score,
            "support": round(float(self.support) * 100),
        }


class _Abort(Exception):
    pass


def rotate(items: list, round_no: int) -> list:
    """第1ラウンドはそのまま、以降は1人ずつ先頭をずらした順番にする。"""
    k = (round_no - 1) % len(items)
    return items[k:] + items[:k]


def parse_continue_vote(text: str) -> str:
    """発言から継続の判断を読み取る。見つからなければ継続とみなす。"""
    matches = _CONTINUE_RE.findall(text)
    return "stop" if matches and matches[-1] == "終了" else "continue"


def parse_ratings(text: str, numbers: list[int]) -> dict[int, str]:
    """評価文から「案番号→評価」を読み取る。対象外の番号は無視し、書かれていない案は「不明」とする。"""
    found: dict[int, str] = {}
    for match in _RATING_RE.finditer(text):
        no = int(match.group(1))
        if no in numbers and no not in found:
            found[no] = match.group(2)
    return {no: found.get(no, "不明") for no in numbers}


def tally(
    authors: dict[int, str], ratings: dict[str, dict[int, str]], rng: random.Random
) -> tuple[list[ProposalScore], int | None, bool]:
    """評価を集計して採択する案を決める。

    authors: 案番号→作成者のid、ratings: 評価者のid→（案番号→評価）
    戻り値：支持率の高い順の集計、採択した案番号（なければNone）、くじ引きをしたか
    """
    scores = {no: ProposalScore(no, author) for no, author in authors.items()}
    for evaluator, rated in ratings.items():
        for no, label in rated.items():
            s = scores.get(no)
            if s is None or s.author_id == evaluator:
                continue  # 自分の案は評価できない
            s.counts[label] += 1
            s.score += RATING_POINTS[label]
            s.max_score += RATING_POINTS["賛成"]

    ranked = sorted(scores.values(), key=lambda s: (-s.support, s.no))
    if not ranked or ranked[0].score == 0:
        return ranked, None, False
    top = [s for s in ranked if s.support == ranked[0].support]
    winner = rng.choice(top) if len(top) > 1 else top[0]
    ranked.sort(key=lambda s: (-s.support, s.no != winner.no, s.no))  # 同点なら採択した案を先頭に
    return ranked, winner.no, len(top) > 1


class DebateRunner:
    def __init__(self, cfg: DebateConfig, seats: list[Seat], rng: random.Random | None = None) -> None:
        self.cfg = cfg
        self.seats = seats
        self.rng = rng or random.Random()
        self.transcript: list[Entry] = []
        self._turns = 0

    async def run(self) -> AsyncIterator[dict]:
        yield {
            "type": "debate_start",
            "topic": self.cfg.topic,
            "rounds": self.cfg.rounds,
            "participants": [s.public() for s in self.seats],
        }
        status, message = "completed", ""
        try:
            async for event in self._proceed():
                yield event
        except _Abort as e:
            status, message = "error", str(e)
        yield {"type": "debate_end", "status": status, "message": message}

    async def _proceed(self) -> AsyncIterator[dict]:
        async for event in self._rounds():
            yield event
        proposals: dict[int, tuple[Seat, str]] = {}
        async for event in self._proposals(proposals):
            yield event
        async for event in self._evaluations(proposals):
            yield event

    # ---- 1. ラウンド ------------------------------------------------------

    async def _rounds(self) -> AsyncIterator[dict]:
        cfg = self.cfg
        for rnd in range(1, cfg.rounds + 1):
            order = rotate(self.seats, rnd)
            asks_vote = rnd < cfg.rounds
            yield {
                "type": "phase",
                "phase": "round",
                "title": f"第{rnd}ラウンド",
                "round": rnd,
                "order": [s.id for s in order],
            }
            votes: dict[str, str] = {}
            for seat in order:
                label = f"第{rnd}ラウンド"
                prompt = self._prompt(argument_instruction(cfg, rnd, [s.config.name for s in order], asks_vote))
                req = self._request(seat, TurnKind.ARGUMENT, prompt, round_no=rnd, asks_continue_vote=asks_vote)
                result = _TurnResult()
                async for event in self._turn(seat, req, label, result, round=rnd):
                    yield event
                if result.error:
                    continue
                self.transcript.append(Entry(seat.config.name, label, result.text))
                if asks_vote:
                    votes[seat.id] = parse_continue_vote(result.text)
                    yield {"type": "continue_vote", "speaker_id": seat.id, "decision": votes[seat.id]}

            if not any(e.label == f"第{rnd}ラウンド" for e in self.transcript):
                raise _Abort("このラウンドでは誰も発言できなかったため、討論を中止しました。")
            if not asks_vote:
                break

            stop = sum(1 for v in votes.values() if v == "stop")
            cont = len(votes) - stop
            majority_stop = stop * 2 > len(votes)  # 過半数。同数なら議論を続ける
            ends = majority_stop and cfg.early_stop
            yield {
                "type": "round_decision",
                "round": rnd,
                "continue": cont,
                "stop": stop,
                "decision": "stop" if majority_stop else "continue",
                "early_stop": ends,
            }
            if ends:
                break

    # ---- 2. 結論案（全員が同時に作る） ------------------------------------------

    async def _proposals(self, proposals: dict[int, tuple[Seat, str]]) -> AsyncIterator[dict]:
        # 番号から作成者を推測できないよう、案の番号はくじで割り振る
        numbers = list(range(1, len(self.seats) + 1))
        self.rng.shuffle(numbers)
        assigned = dict(zip((s.id for s in self.seats), numbers))
        yield {"type": "phase", "phase": "proposal", "title": "結論案（全員が同時に作成）", "round": 0}

        # 全員が同じ時点の議事録だけを見て書く（ほかの人の案は見えない）
        prompt = self._prompt(proposal_instruction(self.cfg))
        results = {s.id: _TurnResult() for s in self.seats}
        jobs = [
            self._turn(
                seat,
                self._request(seat, TurnKind.PROPOSAL, prompt),
                f"案{assigned[seat.id]}",
                results[seat.id],
                proposal_no=assigned[seat.id],
            )
            for seat in self.seats
        ]
        async for event in _merge(jobs):
            yield event

        for seat in self.seats:
            if not results[seat.id].error:
                proposals[assigned[seat.id]] = (seat, results[seat.id].text)
        if not proposals:
            raise _Abort("結論案を1つも作れなかったため、討論を中止しました。")

    # ---- 3. 匿名の相互評価と採択 ----------------------------------------------

    async def _evaluations(self, proposals: dict[int, tuple[Seat, str]]) -> AsyncIterator[dict]:
        yield {"type": "phase", "phase": "evaluation", "title": "相互評価（作成者を伏せて評価）", "round": 0}
        context = self._prompt("")
        results: dict[str, _TurnResult] = {}
        targets: dict[str, list[int]] = {}
        jobs = []
        for seat in self.seats:
            others = sorted(no for no, (author, _) in proposals.items() if author.id != seat.id)
            if not others:
                continue  # 評価できる他人の案がない
            targets[seat.id] = others
            results[seat.id] = _TurnResult()
            prompt = (
                context.rstrip()
                + "\n\n"
                + render_proposals([(no, proposals[no][1]) for no in others])
                + "\n\n# あなたへの指示\n"
                + evaluation_instruction(others)
            )
            req = self._request(seat, TurnKind.EVALUATION, prompt, proposal_numbers=others)
            jobs.append(self._turn(seat, req, "相互評価", results[seat.id]))
        async for event in _merge(jobs):
            yield event

        ratings: dict[str, dict[int, str]] = {}
        for seat in self.seats:
            result = results.get(seat.id)
            if result is None or result.error:
                continue
            ratings[seat.id] = parse_ratings(result.text, targets[seat.id])
            yield {
                "type": "evaluation",
                "speaker_id": seat.id,
                "ratings": [{"proposal_no": no, "rating": r} for no, r in ratings[seat.id].items()],
            }

        authors = {no: seat.id for no, (seat, _) in proposals.items()}
        ranked, adopted, tie_break = tally(authors, ratings, self.rng)
        yield {"type": "phase", "phase": "result", "title": "採択", "round": 0}
        yield {
            "type": "result",
            "proposals": [s.public() for s in ranked],
            "adopted": adopted,
            "tie_break": tie_break,
            "text": proposals[adopted][1] if adopted is not None else "",
        }

    # ---- 共通 ---------------------------------------------------------------

    def _prompt(self, instruction: str) -> str:
        context = render_context(self.cfg, self.transcript)
        return context + ("\n\n# あなたへの指示\n" + instruction if instruction else "")

    def _request(self, seat: Seat, kind: TurnKind, prompt: str, round_no: int = 0, **extra) -> TurnRequest:
        return TurnRequest(
            system=participant_system(self.cfg, seat.config),
            prompt=prompt,
            model=seat.model,
            max_tokens=MAX_TOKENS,
            kind=kind,
            speaker_name=seat.config.name,
            stance=seat.config.stance,
            topic=self.cfg.topic,
            round=round_no,
            total_rounds=self.cfg.rounds,
            others=[s.config.name for s in self.seats if s.id != seat.id],
            **extra,
        )

    async def _turn(
        self, seat: Seat, req: TurnRequest, label: str, result: _TurnResult, **meta
    ) -> AsyncIterator[dict]:
        self._turns += 1
        turn_id = f"t{self._turns}"
        yield {
            "type": "turn_start",
            "turn_id": turn_id,
            "speaker_id": seat.id,
            "kind": req.kind.value,
            "label": label,
            **meta,
        }
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
        yield {"type": "turn_end", "turn_id": turn_id, "ok": True, "text": text}


async def _merge(generators: list[AsyncIterator[dict]]) -> AsyncIterator[dict]:
    """複数の発言を同時に進め、届いた順にイベントを流す。"""
    queue: asyncio.Queue = asyncio.Queue()
    done = object()

    async def pump(gen: AsyncIterator[dict]) -> None:
        try:
            async for event in gen:
                await queue.put(event)
        except Exception as e:  # noqa: BLE001 - 呼び出し側で送出し直す
            await queue.put(e)
        finally:
            await queue.put(done)

    tasks = [asyncio.create_task(pump(g)) for g in generators]
    remaining = len(tasks)
    try:
        while remaining:
            item = await queue.get()
            if item is done:
                remaining -= 1
            elif isinstance(item, Exception):
                raise item
            else:
                yield item
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
