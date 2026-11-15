# hasting-web-kit

Shared look-and-feel and functional common areas for Richard Hasting's web
apps — the antidote to every app inventing its own buttons, colors, and
feedback form.

Three layers:

| Layer | What | Who consumes it |
|---|---|---|
| `kit/tokens.css` | Design tokens (colors, type, spacing) as CSS custom properties, names aligned with the claude-skills `design-system` skill | Every app — Jinja, PHP, static, React, Astro |
| `kit/base.css` + `kit/feedback.js` | Base furniture and framework-agnostic web components (`<hasting-feedback>`) | Non-React apps directly; React apps can use them too |
| `service/` | One central Flask service that AI-routes feedback from **all** apps to the right GitHub repo or inbox, with screenshot storage | Deployed once, on linuxserver |

## Using the feedback widget in an app

```html
<link rel="stylesheet" href="https://feedback.hastingtx.org/kit/v1/tokens.css">
<link rel="stylesheet" href="https://feedback.hastingtx.org/kit/v1/base.css">
<script type="module" src="https://feedback.hastingtx.org/kit/v1/feedback.js"></script>

<hasting-feedback app="my-app"></hasting-feedback>
```

1. Register the app in `service/apps.json` (id, allowed origins, GitHub repo,
   email) and restart `feedback-kit.service`.
2. Drop the three tags above into the app's base template.
3. Delete the app's home-grown feedback code, if any.

The widget gives visitors a floating button → modal with optional name/email,
the feedback text, and an optional **screenshot** (file picker, Ctrl+V paste,
or drag-and-drop). Submissions are honeypot-protected and rate-limited.

Version pinning: `/kit/v1/` is a frozen snapshot published by
`deploy/release-kit.sh` — a repo push never changes what live sites load.
Breaking changes go to `/kit/v2/`; apps upgrade by editing the pin. Apps that
want subresource-integrity can additionally pin exact hashes
(`integrity="sha384-..."` on the script tag) at the cost of not receiving
patch releases within a version line.

## Theming an app

Import `tokens.css` first, then override the accent properties in the app's
own stylesheet:

```css
:root {
  --primary: #2e6e8e;            /* this app's accent */
  --primary-foreground: #fff;
}
```

Never write raw hex in app components — use `var(--token)`. React apps using
the `design-system` skill already share these token names.

## The feedback service

`POST /api/feedback` (multipart): `app`, `feedback_text`, optional `name` /
`email` / `page` / `screenshot` (image), `website` honeypot. Responses:
`{ok, action: github_issue|email|fallback_email|ignored, github_url}`.

Triage runs through the `claude` CLI: bugs and feature requests become GitHub
issues in the app's repo; everything else is emailed. Any failure degrades to
a plain email — feedback is never dropped silently. Screenshots are validated
by magic bytes (not filename or Content-Type), stored under a server-chosen
random name, and linked from the issue/email.

## Development

```bash
python3 -m venv venv && venv/bin/pip install -r service/requirements.txt pytest

venv/bin/pytest tests/                     # service + routing tests
node --test tests/component/*.test.mjs     # widget core tests

# browser harness
python3 -m http.server 8099       # then open /tests/component/test-page.html
```

## Running your own copy

The examples above use Richard's hosted service. To use this yourself you need
your own instance, because the widget posts to whatever service it is pointed at.

1. Clone the repo, `python3 -m venv venv && venv/bin/pip install -r service/requirements.txt`.
2. `cp service/.env.example service/.env` and `cp service/apps.json.example service/apps.json`;
   fill in your GitHub token, repos, origins and email addresses. Both files are gitignored.
3. Routing uses the `claude` CLI (no Anthropic API key). Install and sign in to it for the
   service user, or set `CLAUDE_BIN`. If it is unavailable, feedback falls back to plain email.
4. Edit `deploy/feedback-kit.service` and `deploy/nginx-feedback.conf` (user, paths, domain,
   certs) and install them. Publish the kit with `deploy/release-kit.sh`.
5. In each site, load the kit from **your** domain and point the widget at your API:

```html
<script type="module" src="https://feedback.example.com/kit/v1/feedback.js"></script>
<hasting-feedback app="my-app" endpoint="https://feedback.example.com/api/feedback"></hasting-feedback>
```

The `endpoint` attribute is required for your own copy: the built-in default
(`DEFAULT_ENDPOINT` in `kit/feedback.js`) targets the original author's service.
Email needs `email_to`/`email_from` per app (or `FEEDBACK_EMAIL`/`EMAIL_FROM` in `.env`);
there are no built-in addresses.

## Deploy (linuxserver)

See `deploy/`: `feedback-kit.service` (gunicorn on 127.0.0.1:8090),
`nginx-feedback.conf` (feedback.hastingtx.org: /api → service, /kit/v1 →
assets, /uploads → screenshots), `release-kit.sh` (publish a kit version).
Secrets live in `service/.env` (never committed); the per-app routing table
is `service/apps.json`.
