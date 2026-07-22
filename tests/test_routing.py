"""Tests for the AI triage layer (service/routing.py).

The claude CLI, GitHub API, and SMTP are all monkeypatched — these tests
pin down the decision tree: which route wins, and that every failure mode
degrades to a plain email instead of crashing or silently dropping.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "service"))

import routing  # noqa: E402

APP_CFG = {
    "display_name": "Test App",
    "app_url": "https://test.example.org",
    "github_repo": "richardahasting/test-app",
    "email_to": "richard@example.org",
    "email_from": "test@example.org",
}


@pytest.fixture
def emails(monkeypatch):
    sent = []
    monkeypatch.setattr(routing, "_send_email",
                        lambda cfg, subject, body: sent.append((subject, body)) or True)
    return sent


@pytest.fixture
def github(monkeypatch):
    created = []

    def fake(app_cfg, title, body, labels, name, email):
        created.append(dict(repo=app_cfg["github_repo"], title=title,
                            body=body, labels=labels))
        return "https://github.com/richardahasting/test-app/issues/42", True

    monkeypatch.setattr(routing, "_create_github_issue", fake)
    return created


def triage_returns(monkeypatch, result):
    monkeypatch.setattr(routing, "_call_claude_cli", lambda *a, **kw: result)


# ---------------------------------------------------------------------------
# routing decisions
# ---------------------------------------------------------------------------

def test_bug_goes_to_github(monkeypatch, github, emails):
    triage_returns(monkeypatch, {"action": "github_issue", "title": "Broken chart",
                                 "body": "Chart is empty", "labels": ["bug"]})
    ok, action, url = routing.process_feedback(
        APP_CFG, "Jane", "jane@x.org", "chart broken", "/chart")
    assert (ok, action) == (True, "github_issue")
    assert url.endswith("/issues/42")
    assert github[0]["repo"] == "richardahasting/test-app"
    assert emails == []


def test_praise_goes_to_email(monkeypatch, github, emails):
    triage_returns(monkeypatch, {"action": "email", "subject": "Praise",
                                 "body": "Love the site"})
    ok, action, url = routing.process_feedback(
        APP_CFG, "", "", "love the site", "/")
    assert (ok, action, url) == (True, "email", None)
    assert emails == [("Praise", "Love the site")]
    assert github == []


def test_claude_failure_falls_back_to_email(monkeypatch, emails):
    def boom(*a, **kw):
        raise RuntimeError("claude not found")
    monkeypatch.setattr(routing, "_call_claude_cli", boom)
    ok, action, url = routing.process_feedback(
        APP_CFG, "Jane", "", "something happened here", "/page")
    assert (ok, action, url) == (True, "fallback_email", None)
    subject, body = emails[0]
    assert "Test App" in subject
    assert "something happened here" in body


def test_github_failure_falls_back_to_email(monkeypatch, emails):
    triage_returns(monkeypatch, {"action": "github_issue", "title": "T",
                                 "body": "B", "labels": []})
    monkeypatch.setattr(routing, "_create_github_issue",
                        lambda *a, **kw: (None, False))
    ok, action, url = routing.process_feedback(APP_CFG, "", "", "bug bug bug", "/")
    assert (ok, action, url) == (True, "fallback_email", None)
    assert emails[0][0] == "[Feature/Bug] T"


def test_unexpected_action_falls_back_to_email(monkeypatch, emails):
    triage_returns(monkeypatch, {"action": "carrier_pigeon"})
    ok, action, url = routing.process_feedback(APP_CFG, "", "", "hello there", "/")
    assert (ok, action, url) == (True, "fallback_email", None)
    assert len(emails) == 1


def test_total_failure_reports_not_ok(monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("down")
    monkeypatch.setattr(routing, "_call_claude_cli", boom)
    monkeypatch.setattr(routing, "_send_email", lambda *a, **kw: False)
    ok, action, url = routing.process_feedback(APP_CFG, "", "", "hello there", "/")
    assert ok is False
    assert action == "fallback_email"


def test_screenshot_url_reaches_fallback_email(monkeypatch, emails):
    def boom(*a, **kw):
        raise RuntimeError("down")
    monkeypatch.setattr(routing, "_call_claude_cli", boom)
    routing.process_feedback(APP_CFG, "", "", "look at this", "/",
                             screenshot_url="https://f.example/u/x.png")
    assert "https://f.example/u/x.png" in emails[0][1]


# ---------------------------------------------------------------------------
# claude CLI plumbing
# ---------------------------------------------------------------------------

class FakeCompleted:
    def __init__(self, stdout, returncode=0, stderr=""):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


def test_cli_prompt_carries_app_identity_and_screenshot(monkeypatch):
    captured = {}

    def fake_run(cmd, **kw):
        captured["prompt"] = cmd[2]
        captured["input"] = kw["input"]
        return FakeCompleted('{"action":"email","subject":"s","body":"b"}')

    monkeypatch.setattr(routing.subprocess, "run", fake_run)
    result = routing._call_claude_cli(
        APP_CFG, "Jane", "j@x.org", "feedback text", "/page",
        "https://f.example/u/shot.png")
    assert result["action"] == "email"
    assert "Test App" in captured["prompt"]
    assert "https://test.example.org" in captured["prompt"]
    assert "Jane (j@x.org)" in captured["input"]
    assert "https://f.example/u/shot.png" in captured["input"]


def test_cli_markdown_fences_stripped(monkeypatch):
    monkeypatch.setattr(
        routing.subprocess, "run",
        lambda *a, **kw: FakeCompleted(
            '```json\n{"action":"email","subject":"s","body":"b"}\n```'))
    result = routing._call_claude_cli(APP_CFG, "", "", "t", "/", None)
    assert result["subject"] == "s"


def test_cli_nonzero_exit_raises(monkeypatch):
    monkeypatch.setattr(
        routing.subprocess, "run",
        lambda *a, **kw: FakeCompleted("", returncode=1, stderr="bad"))
    with pytest.raises(RuntimeError):
        routing._call_claude_cli(APP_CFG, "", "", "t", "/", None)


def test_missing_github_token_fails_closed(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    url, ok = routing._create_github_issue(APP_CFG, "T", "B", [], "", "")
    assert (url, ok) == (None, False)


def test_missing_github_repo_fails_closed(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    cfg = dict(APP_CFG, github_repo=None)
    url, ok = routing._create_github_issue(cfg, "T", "B", [], "", "")
    assert (url, ok) == (None, False)
