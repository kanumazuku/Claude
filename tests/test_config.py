from ai_council.config import load_settings


def test_defaults(monkeypatch):
    for name in ("COUNCIL_CLAUDE_MODEL", "COUNCIL_CLAUDE_EFFORT", "COUNCIL_PORT"):
        monkeypatch.delenv(name, raising=False)
    s = load_settings()
    assert s.claude_model == "claude-opus-5-5"
    assert s.claude_effort == "medium"
    assert s.port == 8000


def test_invalid_effort_falls_back_to_medium(monkeypatch):
    monkeypatch.setenv("COUNCIL_CLAUDE_EFFORT", "extreme")
    assert load_settings().claude_effort == "medium"


def test_unrelated_claude_effort_is_ignored(monkeypatch):
    # Claude Codeなどが設定するCLAUDE_EFFORTには影響されない
    monkeypatch.delenv("COUNCIL_CLAUDE_EFFORT", raising=False)
    monkeypatch.setenv("CLAUDE_EFFORT", "max")
    assert load_settings().claude_effort == "medium"
