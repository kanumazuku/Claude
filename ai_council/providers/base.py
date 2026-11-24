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
    """発言の種類。"""

    OPENING = "opening"  # 司会：開会と論点提示
    ARGUMENT = "argument"  # 討論者：主張・反論
    INTERIM = "interim"  # 司会：ラウンドごとの中間整理と継続判定
    PROPOSAL = "proposal"  # 司会：結論案
    FINAL = "final"  # 討論者：結論案への賛否
    CONCLUSION = "conclusion"  # 司会：最終結論


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
