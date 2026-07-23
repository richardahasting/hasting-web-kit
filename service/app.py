"""Central feedback service for hasting-web-kit.

One instance on linuxserver serves every Hasting web app. Apps embed the
<hasting-feedback> widget (kit/feedback.js), which POSTs multipart form data
here; the service validates, stores any screenshot, and AI-routes the
submission to the app's GitHub repo or Richard's inbox (see routing.py).

Endpoints:
    POST /api/feedback   multipart form: app, feedback_text, name, email,
                         website (honeypot), page, screenshot (optional file)
    GET  /healthz        liveness probe

Configuration (environment):
    APPS_CONFIG      path to apps.json (per-app routing table)  [required]
    UPLOAD_DIR       where screenshots are stored               [required]
    UPLOAD_URL_BASE  public URL prefix for stored screenshots   [required]
    GITHUB_TOKEN     one token for all issue creation (routing.py)
    RATE_LIMIT_MAX / RATE_LIMIT_WINDOW   per-IP limit (default 10 / 3600 s)

apps.json shape — see apps.json.example.
"""

import json
import logging
import os
import secrets
import time
from collections import defaultdict, deque

from flask import Flask, jsonify, request

import routing

log = logging.getLogger(__name__)

MIN_TEXT_CHARS = 10
MAX_TEXT_CHARS = 4000
MAX_FILE_BYTES = 5 * 1024 * 1024        # 5 MB screenshot cap
MAX_REQUEST_BYTES = 6 * 1024 * 1024     # multipart overhead headroom

# Extension -> the magic-byte prefixes that must match the file content.
# Content-Type headers and filenames are attacker-controlled; bytes are not.
_MAGIC = {
    ".png":  [b"\x89PNG\r\n\x1a\n"],
    ".jpg":  [b"\xff\xd8\xff"],
    ".jpeg": [b"\xff\xd8\xff"],
    ".gif":  [b"GIF87a", b"GIF89a"],
    ".webp": [b"RIFF"],  # plus 'WEBP' at offset 8, checked below
}


def sniff_image_ext(head):
    """Return the canonical extension for the magic bytes in `head`,
    or None if it is not one of the allowed image formats."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if head.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return ".webp"
    return None


def create_app(config=None):
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_REQUEST_BYTES
    app.config["RATE_LIMIT_MAX"] = int(os.environ.get("RATE_LIMIT_MAX", "10"))
    app.config["RATE_LIMIT_WINDOW"] = int(os.environ.get("RATE_LIMIT_WINDOW", "3600"))
    app.config["UPLOAD_DIR"] = os.environ.get("UPLOAD_DIR", "")
    app.config["UPLOAD_URL_BASE"] = os.environ.get("UPLOAD_URL_BASE", "")
    if config:
        app.config.update(config)

    # ---- per-app routing table -------------------------------------------
    if "APPS" in app.config:
        apps_table = app.config["APPS"]
    else:
        with open(os.environ["APPS_CONFIG"]) as fh:
            apps_table = json.load(fh)
    allowed_origins = {
        origin
        for cfg in apps_table.values()
        for origin in cfg.get("origins", [])
    }

    # ---- naive per-IP rate limit (single-process; fronted by nginx) ------
    hits = defaultdict(deque)

    def rate_limited(ip):
        window = app.config["RATE_LIMIT_WINDOW"]
        now = time.time()
        q = hits[ip]
        while q and q[0] <= now - window:
            q.popleft()
        if len(q) >= app.config["RATE_LIMIT_MAX"]:
            return True
        q.append(now)
        return False

    # ---- CORS ------------------------------------------------------------
    @app.after_request
    def add_cors(resp):
        origin = request.headers.get("Origin")
        if origin in allowed_origins:
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers["Vary"] = "Origin"
        return resp

    @app.route("/api/feedback", methods=["OPTIONS"])
    def feedback_preflight():
        resp = app.make_response(("", 204))
        origin = request.headers.get("Origin")
        if origin in allowed_origins:
            resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
            resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
            resp.headers["Access-Control-Max-Age"] = "86400"
        return resp

    # ---- errors as JSON ---------------------------------------------------
    @app.errorhandler(413)
    def too_large(_exc):
        return jsonify(ok=False, error="Upload too large (5 MB max)."), 413

    # ---- endpoints --------------------------------------------------------
    @app.get("/healthz")
    def healthz():
        return jsonify(ok=True, apps=sorted(apps_table))

    @app.post("/api/feedback")
    def feedback():
        app_id = request.form.get("app", "").strip()
        app_cfg = apps_table.get(app_id)
        if not app_cfg:
            return jsonify(ok=False, error="Unknown application."), 400

        # Honeypot: report success so bots move on, route nothing.
        if request.form.get("website", "").strip():
            log.info("honeypot tripped for app=%s ip=%s", app_id, _client_ip())
            return jsonify(ok=True, action="ignored")

        text = request.form.get("feedback_text", "").strip()
        if len(text) < MIN_TEXT_CHARS:
            return jsonify(
                ok=False,
                error=f"Please describe your feedback (at least {MIN_TEXT_CHARS} characters).",
            ), 400
        if len(text) > MAX_TEXT_CHARS:
            return jsonify(
                ok=False,
                error=f"Feedback is limited to {MAX_TEXT_CHARS} characters.",
            ), 400

        ip = _client_ip()
        if rate_limited(ip):
            return jsonify(
                ok=False,
                error="Too many submissions from your network. Please try again later.",
            ), 429

        name = request.form.get("name", "").strip()[:100]
        email = request.form.get("email", "").strip()[:200]
        page = request.form.get("page", "").strip()[:500]

        screenshot_url = None
        file = request.files.get("screenshot")
        if file and file.filename != "":
            saved, err = _save_screenshot(file)
            if err:
                return jsonify(ok=False, error=err), 400
            screenshot_url = saved

        ok, action, github_url = routing.process_feedback(
            app_cfg, name, email, text, page, screenshot_url)

        if not ok:
            return jsonify(
                ok=False,
                error="We couldn't record your feedback just now. Please try again later.",
            ), 502
        # Private repos: the issue exists but public visitors can't see it —
        # suppress the link so the widget shows the plain thank-you instead.
        if app_cfg.get("private_repo"):
            github_url = None
        return jsonify(ok=True, action=action, github_url=github_url)

    # ---- helpers ----------------------------------------------------------
    def _client_ip():
        # Trust the last X-Forwarded-For hop only when nginx sets it.
        fwd = request.headers.get("X-Forwarded-For", "")
        if fwd:
            return fwd.split(",")[-1].strip()
        return request.remote_addr or "unknown"

    def _save_screenshot(file):
        """Validate and store an uploaded image.

        Returns (public_url, None) on success or (None, error_message).
        """
        head = file.stream.read(16)
        file.stream.seek(0)
        ext = sniff_image_ext(head)
        if ext is None:
            return None, "Screenshots must be a PNG, JPEG, GIF, or WebP image."

        data = file.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            return None, "Screenshots must be 5 MB or smaller."
        if not data:
            return None, "That image file is empty."

        upload_dir = app.config["UPLOAD_DIR"]
        url_base = app.config["UPLOAD_URL_BASE"]
        if not upload_dir or not url_base:
            log.error("UPLOAD_DIR/UPLOAD_URL_BASE not configured; dropping screenshot")
            return None, "Screenshot uploads are not available right now."

        # Server-chosen random name: client filename never touches the disk.
        fname = f"{int(time.time())}-{secrets.token_hex(8)}{ext}"
        os.makedirs(upload_dir, exist_ok=True)
        with open(os.path.join(upload_dir, fname), "wb") as fh:
            fh.write(data)
        return f"{url_base.rstrip('/')}/{fname}", None

    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    create_app().run(host="127.0.0.1", port=8090)
