"""AIプロバイダー共通のインターフェース。

各社のAIは、このProviderを継承したクラスとして実装する。
討論エンジンはProvider.stream()だけを呼ぶので、新しいAIを追加するときも
このファイルの形に合わせるだけでよい。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import Enum


class TurnKind(str, Enum):
    """発言の種類。どの種類の発言も、参加者全員が同じ条件で行う。"""

    ARGUMENT = "argument"  # ラウンドでの主張・応答
    PROPOSAL = "proposal"  # 結論案（全員が同時に、互いの案を見ずに作る）
    EVALUATION = "evaluation"  # 自分以外の結論案への匿名評価


@dataclass(frozen=True)
class TurnRequest:
    """1回の発言を生成するための入力。"""

    system: str
    prompt: str
    model: str
    max_tokens: int
    # 以下はデモAIが文面を組み立てるための補助情報。実際のAIは使わない。
    kind: TurnKind
    speaker_name: str
    stance: str
    topic: str
    round: int = 0
    total_rounds: int = 0
    others: list[str] = field(default_factory=list)
    asks_continue_vote: bool = False  # 発言の最後に【継続】【終了】を求めているか
    proposal_numbers: list[int] = field(default_factory=list)  # 評価する案の番号


@dataclass(frozen=True)
class ProviderStatus:
    available: bool
    reason: str = ""
    models: list[str] = field(default_factory=list)
    default_model: str = ""


class ProviderError(Exception):
    """AIの呼び出しに失敗したときの例外。メッセージは画面にそのまま表示する。"""


class Provider(ABC):
    id: str
    label: str

    @abstractmethod
    async def status(self) -> ProviderStatus:
        """APIキーの有無や接続可否を返す。"""

    @abstractmethod
    def stream(self, req: TurnRequest) -> AsyncIterator[str]:
        """発言の本文を少しずつ返す。失敗したらProviderErrorを送出する。"""
