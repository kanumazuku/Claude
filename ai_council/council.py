"""合議エンジン。ユーザーのプロンプトへの回答を、参加者全員が議論しながら組み立て、修正していく。

流れ：
1. 方針の議論：全員が順に、プロンプトの解釈、含めるべき内容、構成案を出し合う。
2. 初稿：くじ引きで選ばれた参加者が、議論を踏まえて初稿（第1版）を書く。
3. 修正ラウンド：全員が順に原稿を読み、意見を述べたうえで「承認」するか「修正版」を出す。
   修正版が出ると新しい版になり、それまでの承認はその版には引き継がれない。
   今の版をすでに承認している参加者（その版を作った本人を含む）の番は省略する。
4. 完成：全員が同じ版を承認したら完成。最大ラウンドまでに全員の承認がそろわなければ、
   最も多くの承認を得た版（同数なら新しい版）を採用する。

対等にするためのしくみ：
- リーダーや編集長はいない。全員が同じシステムプロンプト（名前と観点だけが違う）で参加する。
- 初稿の担当はくじ引きで決める。
- 修正ラウンドの発言順はラウンドごとに1人ずつずらす。
- 完成には全員の承認が必要。最後に修正した人の版が、誰の確認も受けずに採用されることはない。
"""

from __future__ import annotations

import difflib
import logging
import random
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from .prompts import (
    DECISION_MARK,
    OPINION_MARK,
    REVISED_MARK,
    Entry,
    draft_instruction,
    participant_system,
    planning_instruction,
    render_context,
    revision_instruction,
)
from .providers import Provider, ProviderError, TurnKind, TurnRequest
from .schemas import CouncilConfig, ParticipantConfig

logger = logging.getLogger(__name__)

MAX_TOKENS = 16000
_DECISION_RE = re.compile(re.escape(DECISION_MARK) + r"[\s*#:：_「『]*(承認|修正)")
_DECORATION_ONLY = re.compile(r"[\s*#:：_\-]*")


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
class Version:
    """原稿の1つの版。approvalsはこの版を承認した参加者のid。"""

    no: int
    author_id: str
    text: str
    approvals: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class Review:
    """修正ラウンドでの1回の発言を解析した結果。"""

    opinion: str
    decision: str  # "approve"（承認）、"revise"（修正）、"unknown"（判断が読み取れない）
    revised: str


@dataclass
class _TurnResult:
    text: str = ""
    error: str = ""


class _Abort(Exception):
    pass


def rotate(items: list, round_no: int) -> list:
    """第1ラウンドはそのまま、以降は1人ずつ先頭をずらした順番にする。"""
    k = (round_no - 1) % len(items)
    return items[k:] + items[:k]


def _after_mark(text: str, mark: str) -> str:
    """見出しの後ろの本文を取り出す。見出しと同じ行に飾りの記号しかなければ、その行は捨てる。"""
    rest = text.split(mark, 1)[1]
    first, newline, remainder = rest.partition("\n")
    if newline and _DECORATION_ONLY.fullmatch(first):
        rest = remainder
    return rest.strip()


def _before_mark(text: str, mark: str) -> str:
    body = text.split(mark, 1)[0].rstrip()
    return re.sub(r"[\s*#_]+$", "", body)  # 見出しの手前に付いた「**」や「##」を落とす


def parse_review(text: str) -> Review:
    """修正ラウンドの発言を【意見】【判断】【修正版】に分ける。"""
    revised = _after_mark(text, REVISED_MARK) if REVISED_MARK in text else ""
    head = _before_mark(text, REVISED_MARK) if REVISED_MARK in text else text

    match = _DECISION_RE.search(head)
    if match:
        decision = "approve" if match.group(1) == "承認" else "revise"
        opinion_part = _before_mark(head, DECISION_MARK)
    else:
        decision = "revise" if revised else "unknown"
        opinion_part = head
    opinion = _after_mark(opinion_part, OPINION_MARK) if OPINION_MARK in opinion_part else opinion_part.strip()
    return Review(opinion=opinion, decision=decision, revised=revised)


def line_diff(old: str, new: str) -> list[dict]:
    """行単位の差分。opは same（変更なし）、add（追加）、del（削除）。"""
    a, b = old.splitlines(), new.splitlines()
    ops: list[dict] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            ops += [{"op": "same", "text": line} for line in a[i1:i2]]
            continue
        ops += [{"op": "del", "text": line} for line in a[i1:i2]]
        ops += [{"op": "add", "text": line} for line in b[j1:j2]]
    return ops


def choose_final(versions: list[Version], everyone: set[str]) -> tuple[Version, bool]:
    """採用する版を決める。全員が承認した版があればそれを、なければ承認の最も多い版（同数なら新しい版）。"""
    current = versions[-1]
    if current.approvals >= everyone:
        return current, True
    best = max(versions, key=lambda v: (len(v.approvals), v.no))
    return best, False


class CouncilRunner:
    def __init__(self, cfg: CouncilConfig, seats: list[Seat], rng: random.Random | None = None) -> None:
        self.cfg = cfg
        self.seats = seats
        self.rng = rng or random.Random()
        self.transcript: list[Entry] = []
        self.versions: list[Version] = []
        self._turns = 0

    @property
    def everyone(self) -> set[str]:
        return {s.id for s in self.seats}

    @property
    def current(self) -> Version | None:
        return self.versions[-1] if self.versions else None

    async def run(self) -> AsyncIterator[dict]:
        yield {
            "type": "session_start",
            "prompt": self.cfg.prompt,
            "rounds": self.cfg.rounds,
            "participants": [s.public() for s in self.seats],
        }
        status, message = "completed", ""
        try:
            async for event in self._proceed():
                yield event
        except _Abort as e:
            status, message = "error", str(e)
        yield {"type": "session_end", "status": status, "message": message}

    async def _proceed(self) -> AsyncIterator[dict]:
        async for event in self._planning():
            yield event
        drafter = None
        async for event in self._draft():
            if event["type"] == "lottery":
                drafter = event["drafter_id"]
            yield event
        async for event in self._revisions(drafter):
            yield event

        final, unanimous = choose_final(self.versions, self.everyone)
        yield {"type": "phase", "phase": "final", "title": "完成", "round": 0}
        yield {
            "type": "final",
            "version": final.no,
            "text": final.text,
            "unanimous": unanimous,
            "approvals": sorted(final.approvals),
            "latest_version": self.current.no,
        }

    # ---- 1. 方針の議論 -----------------------------------------------------

    async def _planning(self) -> AsyncIterator[dict]:
        order = self.seats
        names = [s.config.name for s in order]
        yield {"type": "phase", "phase": "planning", "title": "方針の議論", "round": 0, "order": [s.id for s in order]}
        for seat in order:
            prompt = self._prompt(planning_instruction(self.cfg, names))
            result = _TurnResult()
            async for event in self._turn(seat, self._request(seat, TurnKind.PLANNING, prompt), "方針の議論", result):
                yield event
            if not result.error:
                self.transcript.append(Entry(seat.config.name, "方針の議論", result.text))
        if not self.transcript:
            raise _Abort("方針の議論で誰も発言できなかったため、中止しました。")

    # ---- 2. 初稿（担当はくじ引き） ---------------------------------------------

    async def _draft(self) -> AsyncIterator[dict]:
        candidates = list(self.seats)
        self.rng.shuffle(candidates)
        yield {"type": "phase", "phase": "draft", "title": "初稿", "round": 0}
        for attempt, seat in enumerate(candidates):
            yield {"type": "lottery", "drafter_id": seat.id, "retry": attempt > 0}
            result = _TurnResult()
            req = self._request(seat, TurnKind.DRAFT, self._prompt(draft_instruction()))
            async for event in self._turn(seat, req, "初稿", result):
                yield event
            if result.error:
                continue
            version = Version(1, seat.id, result.text, {seat.id})  # 書いた本人はその版を承認したとみなす
            self.versions.append(version)
            yield self._version_event(version, previous="")
            return
        raise _Abort("誰も初稿を書けなかったため、中止しました。")

    # ---- 3. 修正ラウンド ---------------------------------------------------

    async def _revisions(self, drafter_id: str) -> AsyncIterator[dict]:
        # 初稿の担当者は各ラウンドの最後に回し、ほかの参加者が先に原稿を見られるようにする
        start = next(i for i, s in enumerate(self.seats) if s.id == drafter_id) + 1
        base = self.seats[start:] + self.seats[:start]

        for rnd in range(1, self.cfg.rounds + 1):
            order = rotate(base, rnd)
            names = [s.config.name for s in order]
            yield {
                "type": "phase",
                "phase": "revision",
                "title": f"修正ラウンド{rnd}",
                "round": rnd,
                "order": [s.id for s in order],
            }
            for seat in order:
                if seat.id in self.current.approvals:
                    # 今の版をすでに承認している（または自分で作った）参加者は、同じ版を見直す必要がない
                    yield {"type": "skip", "speaker_id": seat.id, "version": self.current.no, "round": rnd}
                    continue
                async for event in self._review_turn(seat, rnd, names):
                    yield event
                if self.current.approvals >= self.everyone:
                    yield {"type": "consensus", "version": self.current.no}
                    return

    async def _review_turn(self, seat: Seat, rnd: int, names: list[str]) -> AsyncIterator[dict]:
        current = self.current
        prompt = self._prompt(revision_instruction(self.cfg, rnd, names, current.no))
        req = self._request(seat, TurnKind.REVISION, prompt, round_no=rnd)
        result = _TurnResult()
        async for event in self._turn(seat, req, f"修正ラウンド{rnd}", result, round=rnd, version=current.no):
            yield event
        if result.error:
            return

        review = parse_review(result.text)
        if review.decision == "approve":
            current.approvals.add(seat.id)
            outcome, label = "approve", "承認"
        elif review.revised and review.revised.strip() != current.text.strip():
            new = Version(current.no + 1, seat.id, review.revised, {seat.id})
            self.versions.append(new)
            yield self._version_event(new, previous=current.text)
            outcome, label = "revise", f"修正して第{new.no}版を作成"
        else:
            current.approvals.discard(seat.id)
            outcome, label = "object", "修正を求める"

        opinion = review.opinion or "（意見なし）"
        self.transcript.append(Entry(seat.config.name, f"修正ラウンド{rnd}・第{current.no}版に{label}", opinion))
        yield {
            "type": "review",
            "speaker_id": seat.id,
            "decision": outcome,
            "opinion": opinion,
            "reviewed_version": current.no,
            "current_version": self.current.no,
            "approvals": sorted(self.current.approvals),
        }

    # ---- 共通 ---------------------------------------------------------------

    def _version_event(self, version: Version, previous: str) -> dict:
        diff = line_diff(previous, version.text) if previous else []
        return {
            "type": "version",
            "version": version.no,
            "author_id": version.author_id,
            "text": version.text,
            "approvals": sorted(version.approvals),
            "diff": diff,
            "added": sum(1 for d in diff if d["op"] == "add"),
            "removed": sum(1 for d in diff if d["op"] == "del"),
        }

    def _prompt(self, instruction: str) -> str:
        current = self.current
        editor = ""
        if current:
            editor = next(s.config.name for s in self.seats if s.id == current.author_id)
        context = render_context(
            self.cfg,
            self.transcript,
            draft=current.text if current else "",
            version=current.no if current else 0,
            editor=editor,
        )
        return context + "\n\n# あなたへの指示\n" + instruction

    def _request(self, seat: Seat, kind: TurnKind, prompt: str, round_no: int = 0) -> TurnRequest:
        current = self.current
        return TurnRequest(
            system=participant_system(self.cfg, seat.config),
            prompt=prompt,
            model=seat.model,
            max_tokens=MAX_TOKENS,
            kind=kind,
            speaker_name=seat.config.name,
            stance=seat.config.stance,
            user_prompt=self.cfg.prompt,
            round=round_no,
            total_rounds=self.cfg.rounds,
            others=[s.config.name for s in self.seats if s.id != seat.id],
            current_draft=current.text if current else "",
            version=current.no if current else 0,
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
        except Exception as e:  # noqa: BLE001 - 想定外の失敗でも合議全体は止めない
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
