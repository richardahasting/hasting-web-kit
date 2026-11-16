"""AI-routed feedback triage for the central hasting-web-kit feedback service.

Generalized from canyon_lake_monitor/feedback.py: the app-specific pieces
(site name, URL, GitHub repo, email addresses) come from the per-app entry
in apps.json instead of environment variables, so one service instance
routes for every Hasting app.

process_feedback(app_cfg, name, email, text, page, screenshot_url)
    -> (ok, action, github_issue_url)

Routing via the `claude` CLI (no ANTHROPIC_API_KEY needed):
- Bugs / feature requests / code suggestions -> GitHub issue in the app's repo
- Everything else                            -> email to the app's inbox
- claude CLI not found / any error           -> fallback plain email, no crash
"""

import json
import logging
import os
import shutil
import smtplib
import subprocess
import urllib.error
import urllib.request
from email.mime.text import MIMEText

log = logging.getLogger(__name__)

_PROMPT_TEMPLATE = """\
You are the feedback assistant for {display_name} ({app_url}), one of
Richard Hasting's public web applications. An anonymous visitor has submitted
feedback through the site. Triage it and decide where to route it.

Rules:
- Route to github_issue for: bugs, broken pages, wrong or stale data, UI
  glitches, performance problems, and feature requests or suggestions to
  improve or change the site or its code.
- Route to email for: general comments, questions, praise, thanks, or anything
  that is not a software bug or change request.
- Be concise but include all relevant context in the title/body.
- If the visitor gave a name or email, attribute the feedback to them in the body.
- Note which page of the site the feedback was submitted from.
- If a screenshot URL is provided, include it in the body as a markdown image.

Output ONLY a single JSON object — no other text, no markdown fences.

For bugs / feature requests / code suggestions:
{{"action":"github_issue","title":"<short title>","body":"<full body>","labels":["bug"|"enhancement"]}}

For everything else:
{{"action":"email","subject":"<subject>","body":"<full body>"}}
"""


def process_feedback(app_cfg, name, email, text, page, screenshot_url=None):
    """Triage and route visitor feedback for one app.

    app_cfg keys: display_name, app_url, github_repo, email_to, email_from.
    Returns (success, action, github_issue_url).
    """
    try:
        result = _call_claude_cli(app_cfg, name, email, text, page, screenshot_url)
    except Exception as exc:
        log.error("claude CLI error in process_feedback: %s", exc)
        ok = _fallback_email(app_cfg, name, email, text, page, screenshot_url)
        return ok, "fallback_email", None

    action = result.get("action")

    if action == "github_issue":
        github_url, ok = _create_github_issue(
            app_cfg, result["title"], result["body"],
            result.get("labels", []), name, email)
        if ok:
            return True, "github_issue", github_url
        log.warning("GitHub issue creation failed; falling back to email for: %s",
                    result["title"])
        ok = _send_email(app_cfg, f"[Feature/Bug] {result['title']}", result["body"])
        return ok, "fallback_email", None

    if action == "email":
        ok = _send_email(app_cfg, result["subject"], result["body"])
        return ok, "email", None

    log.warning("Unexpected action from claude CLI: %s; falling back to email", action)
    ok = _fallback_email(app_cfg, name, email, text, page, screenshot_url)
    return ok, "fallback_email", None


# ---------------------------------------------------------------------------
# claude CLI call
# ---------------------------------------------------------------------------

def _call_claude_cli(app_cfg, name, email, text, page, screenshot_url):
    """Invoke the claude CLI and return the parsed JSON routing decision."""
    who = name or "Anonymous visitor"
    if email:
        who += f" ({email})"
    message = (
        f"Visitor: {who}\n"
        f"Page: {page or 'unknown'}\n"
    )
    if screenshot_url:
        message += f"Screenshot: {screenshot_url}\n"
    message += f"\nFeedback:\n{text}"

    prompt = _PROMPT_TEMPLATE.format(
        display_name=app_cfg.get("display_name", "a Hasting web app"),
        app_url=app_cfg.get("app_url", "unknown URL"),
    )

    claude_bin = os.environ.get("CLAUDE_BIN") or shutil.which("claude") or "claude"
    # Remove CLAUDECODE so nested-session detection doesn't block us
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    result = subprocess.run(
        [claude_bin, "-p", prompt],
        input=message,
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )

    if result.returncode != 0:
        raise RuntimeError(f"claude exited {result.returncode}: {result.stderr.strip()}")

    output = result.stdout.strip()
    # Strip any accidental markdown fences
    if output.startswith("```"):
        output = output.split("```")[1]
        if output.startswith("json"):
            output = output[4:]
    return json.loads(output)


# ---------------------------------------------------------------------------
# GitHub issue creation (stdlib urllib only)
# ---------------------------------------------------------------------------

def _create_github_issue(app_cfg, title, body, labels, name, email):
    """Returns (issue_url, success)."""
    token = os.environ.get("GITHUB_TOKEN")
    repo = app_cfg.get("github_repo")
    if not token:
        log.error("GITHUB_TOKEN not set; cannot create GitHub issue: %s", title)
        return None, False
    if not repo:
        log.error("app has no github_repo configured; cannot create issue: %s", title)
        return None, False

    full_body = body
    who = name or "Anonymous visitor"
    if email:
        who += f" <{email}>"
    full_body += f"\n\n---\n**Submitted by:** {who} (via site feedback form)"

    payload = json.dumps({"title": title, "body": full_body, "labels": labels}).encode()
    url = f"https://api.github.com/repos/{repo}/issues"
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization":        f"Bearer {token}",
            "Accept":               "application/vnd.github+json",
            "Content-Type":         "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status in (200, 201):
                data = json.loads(resp.read())
                return data.get("html_url"), True
            return None, False
    except urllib.error.HTTPError as exc:
        log.error("GitHub API HTTP %s: %s", exc.code, exc.read())
        return None, False
    except Exception as exc:
        log.error("GitHub API error: %s", exc)
        return None, False


# ---------------------------------------------------------------------------
# Email sending (SMTP_HOST/SMTP_PORT, default local Postfix, no auth)
# ---------------------------------------------------------------------------

def _send_email(app_cfg, subject, body):
    # No built-in defaults: set email_to/email_from per app in apps.json,
    # or FEEDBACK_EMAIL / EMAIL_FROM in .env as a service-wide fallback.
    to_addr = app_cfg.get("email_to") or os.environ.get("FEEDBACK_EMAIL")
    from_addr = app_cfg.get("email_from") or os.environ.get("EMAIL_FROM")
    if not to_addr or not from_addr:
        log.error("Email not sent (%s): no email_to/email_from configured", subject)
        return False
    display = app_cfg.get("display_name", "Hasting Feedback")
    smtp_host = os.environ.get("SMTP_HOST", "localhost")
    smtp_port = int(os.environ.get("SMTP_PORT", "25"))

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = f"{display} <{from_addr}>"
    msg["To"] = to_addr
    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as smtp:
            smtp.sendmail(from_addr, [to_addr], msg.as_string())
        return True
    except Exception as exc:
        log.error("Email send failed (%s): %s", subject, exc)
        return False


def _fallback_email(app_cfg, name, email, text, page, screenshot_url=None):
    """Send a plain email when the claude CLI is unavailable."""
    who = name or "Anonymous visitor"
    if email:
        who += f" ({email})"
    display = app_cfg.get("display_name", "a Hasting app")
    subject = f"[{display}] Site feedback from {who}"
    body = (
        f"Feedback submitted by {who}\n"
        f"App: {display}\n"
        f"Page: {page or 'unknown'}\n"
    )
    if screenshot_url:
        body += f"Screenshot: {screenshot_url}\n"
    body += f"\n{text}"
    return _send_email(app_cfg, subject, body)
