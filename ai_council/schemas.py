"""画面から受け取る合議の設定。"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator

MIN_PARTICIPANTS = 2
MAX_PARTICIPANTS = 6


class ParticipantConfig(BaseModel):
    name: str = Field(min_length=1, max_length=20)
    provider: str = Field(min_length=1, max_length=20)
    model: str = Field(default="", max_length=100)
    stance: str = Field(default="", max_length=300)

    @field_validator("name", "model", "stance")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()


class CouncilConfig(BaseModel):
    """合議の設定。参加者は全員が対等で、リーダーや編集長のような特別な役割はない。"""

    prompt: str = Field(min_length=1, max_length=4000)
    rounds: int = Field(default=2, ge=1, le=5)  # 修正ラウンドの最大数
    max_chars: int = Field(default=300, ge=100, le=1000)  # 議論での1回の発言の目安
    participants: list[ParticipantConfig] = Field(
        min_length=MIN_PARTICIPANTS, max_length=MAX_PARTICIPANTS
    )

    @field_validator("prompt")
    @classmethod
    def _strip_prompt(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("プロンプトを入力してください")
        return value

    @model_validator(mode="after")
    def _unique_names(self) -> CouncilConfig:
        names = [p.name for p in self.participants]
        if any(not n for n in names):
            raise ValueError("参加者の名前を入力してください")
        if len(set(names)) != len(names):
            raise ValueError("参加者の名前が重複しています")
        return self
