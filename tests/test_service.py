"""Endpoint tests for the central feedback service (service/app.py).

routing.process_feedback is monkeypatched throughout — these tests cover the
HTTP layer: validation, honeypot, rate limiting, uploads, CORS. Routing
behavior itself is covered in test_routing.py.
"""

import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "service"))

import app as app_module  # noqa: E402
import routing  # noqa: E402

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8\xff\xe0"
WEBP_MAGIC = b"RIFF\x00\x00\x00\x00WEBP"

APPS = {
    "test-app": {
        "display_name": "Test App",
        "app_url": "https://test.example.org",
        "origins": ["https://test.example.org"],
        "github_repo": "richardahasting/test-app",
        "email_to": "richard@example.org",
        "email_from": "test@example.org",
    },
    "other-app": {
        "display_name": "Other App",
        "app_url": "https://other.example.org",
        "origins": ["https://other.example.org"],
        "github_repo": "richardahasting/other-app",
        "email_to": "richard@example.org",
        "email_from": "other@example.org",
    },
}


@pytest.fixture
def routed(monkeypatch):
    """Capture process_feedback calls; default to a successful email route."""
    calls = []

    def fake(app_cfg, name, email, text, page, screenshot_url=None):
        calls.append(dict(app_cfg=app_cfg, name=name, email=email,
                          text=text, page=page, screenshot_url=screenshot_url))
        return True, "email", None

    monkeypatch.setattr(routing, "process_feedback", fake)
    return calls


@pytest.fixture
def client(tmp_path, routed):
    app = app_module.create_app({
        "TESTING": True,
        "APPS": APPS,
        "UPLOAD_DIR": str(tmp_path / "uploads"),
        "UPLOAD_URL_BASE": "https://feedback.example.org/uploads",
        "RATE_LIMIT_MAX": 100,
        "RATE_LIMIT_WINDOW": 3600,
    })
    return app.test_client()


def post(client, **overrides):
    data = {
        "app": "test-app",
        "feedback_text": "The chart on the history page is empty today.",
        "name": "",
        "email": "",
        "website": "",
        "page": "https://test.example.org/chart",
    }
    data.update(overrides)
    return client.post("/api/feedback", data=data,
                       content_type="multipart/form-data")


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------

def test_happy_path(client, routed):
    resp = post(client)
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True, "action": "email", "github_url": None}
    assert len(routed) == 1
    assert routed[0]["text"].startswith("The chart")


def test_unknown_app_rejected(client, routed):
    resp = post(client, app="nope")
    assert resp.status_code == 400
    assert not resp.get_json()["ok"]
    assert routed == []


def test_missing_app_rejected(client, routed):
    resp = post(client, app="")
    assert resp.status_code == 400
    assert routed == []


def test_short_text_rejected(client, routed):
    resp = post(client, feedback_text="too short")
    assert resp.status_code == 400
    assert "at least 10" in resp.get_json()["error"]
    assert routed == []


def test_overlong_text_rejected(client, routed):
    resp = post(client, feedback_text="x" * 4001)
    assert resp.status_code == 400
    assert routed == []


def test_text_at_boundaries_accepted(client, routed):
    assert post(client, feedback_text="x" * 10).status_code == 200
    assert post(client, feedback_text="x" * 4000).status_code == 200
    assert len(routed) == 2


def test_whitespace_only_text_rejected(client, routed):
    resp = post(client, feedback_text="              ")
    assert resp.status_code == 400
    assert routed == []


def test_name_email_page_truncated(client, routed):
    post(client, name="n" * 500, email="e" * 500, page="p" * 1000)
    assert len(routed[0]["name"]) == 100
    assert len(routed[0]["email"]) == 200
    assert len(routed[0]["page"]) == 500


# ---------------------------------------------------------------------------
# honeypot
# ---------------------------------------------------------------------------

def test_honeypot_pretends_success_and_routes_nothing(client, routed):
    resp = post(client, website="https://spam.example")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["action"] == "ignored"
    assert routed == []


# ---------------------------------------------------------------------------
# routing failure surfaces as 502
# ---------------------------------------------------------------------------

def test_routing_failure_returns_502(client, routed, monkeypatch):
    monkeypatch.setattr(routing, "process_feedback",
                        lambda *a, **kw: (False, "fallback_email", None))
    resp = post(client)
    assert resp.status_code == 502
    assert not resp.get_json()["ok"]


def test_github_url_passed_through(client, monkeypatch):
    monkeypatch.setattr(
        routing, "process_feedback",
        lambda *a, **kw: (True, "github_issue",
                          "https://github.com/richardahasting/test-app/issues/7"))
    resp = post(client)
    body = resp.get_json()
    assert body["action"] == "github_issue"
    assert body["github_url"].endswith("/issues/7")


# ---------------------------------------------------------------------------
# screenshot upload
# ---------------------------------------------------------------------------

def upload(client, content, filename="shot.png", content_type="image/png"):
    return client.post(
        "/api/feedback",
        data={
            "app": "test-app",
            "feedback_text": "See the attached screenshot of the bug.",
            "website": "",
            "screenshot": (io.BytesIO(content), filename, content_type),
        },
        content_type="multipart/form-data",
    )


def test_png_upload_saved_and_url_routed(client, routed, tmp_path):
    resp = upload(client, PNG_MAGIC + b"fakepixels")
    assert resp.status_code == 200
    url = routed[0]["screenshot_url"]
    assert url.startswith("https://feedback.example.org/uploads/")
    assert url.endswith(".png")
    saved = list((tmp_path / "uploads").iterdir())
    assert len(saved) == 1
    assert saved[0].read_bytes() == PNG_MAGIC + b"fakepixels"


def test_jpeg_and_webp_accepted(client, routed):
    assert upload(client, JPEG_MAGIC + b"x", "s.jpg", "image/jpeg").status_code == 200
    assert upload(client, WEBP_MAGIC + b"x", "s.webp", "image/webp").status_code == 200
    assert routed[0]["screenshot_url"].endswith(".jpg")
    assert routed[1]["screenshot_url"].endswith(".webp")


def test_magic_bytes_beat_filename_and_content_type(client, routed):
    """An .exe renamed to .png with a spoofed Content-Type is rejected."""
    resp = upload(client, b"MZ\x90\x00 not an image", "innocent.png", "image/png")
    assert resp.status_code == 400
    assert "PNG, JPEG, GIF, or WebP" in resp.get_json()["error"]
    assert routed == []


def test_svg_rejected(client, routed):
    """SVG can carry scripts; it is deliberately not on the allow-list."""
    resp = upload(client, b"<svg xmlns='http://www.w3.org/2000/svg'/>",
                  "img.svg", "image/svg+xml")
    assert resp.status_code == 400
    assert routed == []


def test_extension_comes_from_bytes_not_filename(client, routed, tmp_path):
    """A real PNG uploaded as 'shot.jpg' is stored as .png."""
    resp = upload(client, PNG_MAGIC + b"pixels", "shot.jpg", "image/jpeg")
    assert resp.status_code == 200
    assert routed[0]["screenshot_url"].endswith(".png")


def test_client_filename_never_used_on_disk(client, tmp_path):
    upload(client, PNG_MAGIC + b"pixels", "../../../etc/evil.png")
    saved = list((tmp_path / "uploads").iterdir())
    assert len(saved) == 1
    assert "evil" not in saved[0].name
    assert ".." not in saved[0].name


def test_empty_file_rejected(client, routed):
    resp = upload(client, b"")
    assert resp.status_code == 400
    assert routed == []


def test_oversize_request_gets_json_413(client):
    big = PNG_MAGIC + b"\x00" * (6 * 1024 * 1024)
    resp = upload(client, big)
    assert resp.status_code == 413
    assert resp.get_json()["ok"] is False


def test_submission_without_screenshot_has_no_url(client, routed):
    post(client)
    assert routed[0]["screenshot_url"] is None


# ---------------------------------------------------------------------------
# rate limiting
# ---------------------------------------------------------------------------

def test_rate_limit_blocks_after_max(tmp_path, routed):
    app = app_module.create_app({
        "TESTING": True,
        "APPS": APPS,
        "UPLOAD_DIR": str(tmp_path),
        "UPLOAD_URL_BASE": "https://x/u",
        "RATE_LIMIT_MAX": 2,
        "RATE_LIMIT_WINDOW": 3600,
    })
    c = app.test_client()
    assert post(c).status_code == 200
    assert post(c).status_code == 200
    resp = post(c)
    assert resp.status_code == 429
    assert "Too many" in resp.get_json()["error"]
    assert len(routed) == 2


def test_rate_limit_not_consumed_by_invalid_requests(tmp_path, routed):
    """Validation rejects must not eat the rate budget."""
    app = app_module.create_app({
        "TESTING": True,
        "APPS": APPS,
        "UPLOAD_DIR": str(tmp_path),
        "UPLOAD_URL_BASE": "https://x/u",
        "RATE_LIMIT_MAX": 1,
        "RATE_LIMIT_WINDOW": 3600,
    })
    c = app.test_client()
    for _ in range(5):
        assert post(c, feedback_text="short").status_code == 400
    assert post(c).status_code == 200


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------

def test_cors_allowed_origin_echoed(client):
    resp = post(client)
    # no Origin header -> no CORS header
    assert "Access-Control-Allow-Origin" not in resp.headers

    resp = client.post("/api/feedback",
                       data={"app": "test-app",
                             "feedback_text": "Cross-origin feedback works.",
                             "website": ""},
                       content_type="multipart/form-data",
                       headers={"Origin": "https://test.example.org"})
    assert resp.headers["Access-Control-Allow-Origin"] == "https://test.example.org"
    assert resp.headers["Vary"] == "Origin"


def test_cors_unknown_origin_gets_no_header(client):
    resp = client.post("/api/feedback",
                       data={"app": "test-app",
                             "feedback_text": "Cross-origin feedback works.",
                             "website": ""},
                       content_type="multipart/form-data",
                       headers={"Origin": "https://evil.example.com"})
    assert "Access-Control-Allow-Origin" not in resp.headers


def test_cors_preflight(client):
    resp = client.options("/api/feedback",
                          headers={"Origin": "https://other.example.org"})
    assert resp.status_code == 204
    assert resp.headers["Access-Control-Allow-Origin"] == "https://other.example.org"
    assert "POST" in resp.headers["Access-Control-Allow-Methods"]


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------

def test_healthz_lists_apps(client):
    body = client.get("/healthz").get_json()
    assert body["ok"] is True
    assert body["apps"] == ["other-app", "test-app"]


# ---------------------------------------------------------------------------
# private_repo flag
# ---------------------------------------------------------------------------

def test_private_repo_suppresses_github_url(tmp_path, routed, monkeypatch):
    monkeypatch.setattr(
        routing, "process_feedback",
        lambda *a, **kw: (True, "github_issue",
                          "https://github.com/richardahasting/private-app/issues/9"))
    apps = dict(APPS)
    apps["private-app"] = dict(APPS["test-app"], private_repo=True)
    app = app_module.create_app({
        "TESTING": True,
        "APPS": apps,
        "UPLOAD_DIR": str(tmp_path),
        "UPLOAD_URL_BASE": "https://x/u",
    })
    c = app.test_client()
    body = post(c, app="private-app").get_json()
    assert body["action"] == "github_issue"
    assert body["github_url"] is None
    # non-private apps still get the link
    body2 = post(c, app="test-app").get_json()
    assert body2["github_url"].endswith("/issues/9")
