"""画面から受け取る討論設定。"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator


class ParticipantConfig(BaseModel):
    name: str = Field(min_length=1, max_length=20)
    provider: str = Field(min_length=1, max_length=20)
    model: str = Field(default="", max_length=100)
    stance: str = Field(default="", max_length=300)

    @field_validator("name", "model", "stance")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()


class DebateConfig(BaseModel):
    topic: str = Field(min_length=1, max_length=500)
    rounds: int = Field(default=3, ge=1, le=5)
    early_stop: bool = True
    max_chars: int = Field(default=300, ge=100, le=1000)
    moderator: ParticipantConfig
    debaters: list[ParticipantConfig] = Field(min_length=2, max_length=5)

    @field_validator("topic")
    @classmethod
    def _strip_topic(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("議題を入力してください")
        return value

    @model_validator(mode="after")
    def _unique_names(self) -> DebateConfig:
        names = [self.moderator.name, *(d.name for d in self.debaters)]
        if any(not n for n in names):
            raise ValueError("参加者の名前を入力してください")
        if len(set(names)) != len(names):
            raise ValueError("参加者の名前が重複しています")
        return self
