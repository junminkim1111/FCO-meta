import os

import pytest

from fco_meta.chatbot.gemini import choose_backend
from fco_meta.config import load_env

KEYS = ("FCO_TEST_A", "FCO_TEST_B", "FCO_TEST_C", "FCO_TEST_D", "FCO_TEST_E", "GEMINI_API_KEY", "GOOGLE_API_KEY")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in KEYS:
        monkeypatch.delenv(k, raising=False)


def test_load_env_parses_file(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "# 주석\n"
        "FCO_TEST_A=plain\n"
        'FCO_TEST_B="quoted value"\n'
        "export FCO_TEST_C='single'\n"
        "FCO_TEST_D=value # 뒤 주석\n"
        "FCO_TEST_E=\n"  # 빈 값은 설정하지 않음
        "잘못된 줄\n",
        encoding="utf-8",
    )
    assert load_env(env) == [env.resolve()]
    assert os.environ["FCO_TEST_A"] == "plain"
    assert os.environ["FCO_TEST_B"] == "quoted value"
    assert os.environ["FCO_TEST_C"] == "single"
    assert os.environ["FCO_TEST_D"] == "value"
    assert "FCO_TEST_E" not in os.environ


def test_environment_wins_over_file(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("FCO_TEST_A=from_file\n", encoding="utf-8")
    monkeypatch.setenv("FCO_TEST_A", "from_shell")
    load_env(env)
    assert os.environ["FCO_TEST_A"] == "from_shell"


def test_missing_file_is_ignored(tmp_path):
    assert load_env(tmp_path / "nope.env") == []


def test_default_backend_falls_back_without_key():
    backend, notice = choose_backend("gemini")
    assert backend == "rules" and "GEMINI_API_KEY" in notice
    assert choose_backend("rules") == ("rules", None)


def test_gemini_backend_with_key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    assert choose_backend("gemini") == ("gemini", None)
