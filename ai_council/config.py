"""環境変数（.env）から設定を読み込む。"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _env(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return value.strip() if value and value.strip() else default


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    claude_model: str
    claude_effort: str
    openai_model: str
    gemini_model: str
    ollama_host: str
    ollama_model: str
    mock_delay: float
    host: str
    port: int


_EFFORTS = {"low", "medium", "high", "xhigh", "max"}


def load_settings() -> Settings:
    # APIキー以外のアプリ設定は、ほかのツールの環境変数と衝突しないよう COUNCIL_ を付ける。
    effort = _env("COUNCIL_CLAUDE_EFFORT", "medium")
    return Settings(
        claude_model=_env("COUNCIL_CLAUDE_MODEL", "claude-opus-5-5"),
        claude_effort=effort if effort in _EFFORTS else "medium",
        openai_model=_env("COUNCIL_OPENAI_MODEL", "gpt-5.5"),
        gemini_model=_env("COUNCIL_GEMINI_MODEL", "gemini-3.8-flash"),
        ollama_host=_env("OLLAMA_HOST", "http://localhost:11434"),
        ollama_model=_env("COUNCIL_OLLAMA_MODEL", "gemma3"),
        mock_delay=_env_float("COUNCIL_MOCK_DELAY", 0.015),
        host=_env("COUNCIL_HOST", "127.0.0.1"),
        port=int(_env_float("COUNCIL_PORT", 8000)),
    )


def has_env(*names: str) -> bool:
    """指定した環境変数のどれか1つに値が入っていればTrue。"""
    return any(_env(name) for name in names)
