/* hasting-web-kit — feedback.js
 *
 * <hasting-feedback app="canyon-lake"></hasting-feedback>
 *
 * Framework-agnostic feedback widget: floating button + modal dialog that
 * posts to the central feedback service, which AI-routes the submission to
 * a GitHub issue or email. Supports an optional screenshot (file picker,
 * paste, or drag-and-drop).
 *
 * Usage (any HTML page, any framework):
 *   <script type="module" src="https://feedback.hastingtx.org/kit/v1/feedback.js"></script>
 *   <hasting-feedback app="my-app"></hasting-feedback>
 *
 * Attributes:
 *   app       (required)  app id registered in the service's routing table
 *   endpoint  (optional)  override the service URL; default below
 *   label     (optional)  button text; default "💬 Feedback"
 *
 * Styling: uses the shared tokens (--primary, --radius, ...) when present,
 * with self-contained fallbacks so the widget works on un-tokened pages.
 *
 * The validation/formatting core is exported as pure functions so it can be
 * unit-tested in Node without a DOM.
 */

export const DEFAULT_ENDPOINT = "https://feedback.hastingtx.org/api/feedback";
export const MIN_TEXT_CHARS = 10;
export const MAX_TEXT_CHARS = 4000;
export const MAX_FILE_BYTES = 5 * 1024 * 1024; // 5 MB
export const ALLOWED_IMAGE_TYPES = [
  "image/png",
  "image/jpeg",
  "image/gif",
  "image/webp",
];

/* ------------------------------------------------------------------------
 * Pure core (unit-tested in Node)
 * --------------------------------------------------------------------- */

/** Validate the feedback text. Returns null if OK, else an error message. */
export function validateText(text) {
  const trimmed = (text || "").trim();
  if (trimmed.length < MIN_TEXT_CHARS) {
    return `Please describe your feedback (at least ${MIN_TEXT_CHARS} characters).`;
  }
  if (trimmed.length > MAX_TEXT_CHARS) {
    return `Feedback is limited to ${MAX_TEXT_CHARS} characters.`;
  }
  return null;
}

/**
 * Validate a screenshot file-like object ({ type, size, name }).
 * Returns null if OK (or no file), else an error message.
 */
export function validateFile(file) {
  if (!file) return null;
  if (!ALLOWED_IMAGE_TYPES.includes(file.type)) {
    return "Screenshots must be a PNG, JPEG, GIF, or WebP image.";
  }
  if (file.size > MAX_FILE_BYTES) {
    const mb = Math.round(MAX_FILE_BYTES / (1024 * 1024));
    return `Screenshots must be ${mb} MB or smaller.`;
  }
  if (file.size === 0) {
    return "That image file is empty.";
  }
  return null;
}

/**
 * Build the list of [key, value] form entries for a submission.
 * `fields`: { app, text, name, email, website, page }
 * The file is appended separately by the DOM layer (FormData is DOM-only).
 */
export function buildFormEntries(fields) {
  return [
    ["app", fields.app || ""],
    ["feedback_text", (fields.text || "").trim()],
    ["name", (fields.name || "").trim()],
    ["email", (fields.email || "").trim()],
    ["website", fields.website || ""], // honeypot — humans leave it empty
    ["page", fields.page || ""],
  ];
}

/**
 * True only for real GitHub issue URLs — the one shape of URL we will ever
 * render as a link. Anything else (javascript:, data:, other hosts) is
 * refused even though the service is ours: don't trust the wire.
 */
export function isSafeIssueUrl(url) {
  return typeof url === "string" &&
    /^https:\/\/github\.com\/[\w.-]+\/[\w.-]+\/issues\/\d+$/.test(url);
}

/**
 * Pick the success message (HTML string) from a service response
 * ({ ok, action, github_url }).
 */
export function successMessage(data) {
  if (data.action === "github_issue" && isSafeIssueUrl(data.github_url)) {
    const url = escapeHtml(data.github_url);
    return (
      "Thank you! Your suggestion is now on our to-do list &mdash; " +
      `<a href="${url}" target="_blank" rel="noopener noreferrer">track it here</a>.`
    );
  }
  if (data.action === "github_issue") {
    return "Thank you! Your suggestion is now on our to-do list.";
  }
  return "Thank you! Your feedback is on its way.";
}

/** Minimal HTML escaping for attribute/text interpolation. */
export function escapeHtml(s) {
  return String(s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

/* ------------------------------------------------------------------------
 * Web component (browser only)
 * --------------------------------------------------------------------- */

const TEMPLATE = `
<style>
  :host {
    /* token consumption with self-contained fallbacks */
    --_primary: var(--primary, #2f6f8f);
    --_primary-fg: var(--primary-foreground, #ffffff);
    --_bg: var(--card, var(--background, #ffffff));
    --_fg: var(--card-foreground, var(--foreground, #222222));
    --_muted-fg: var(--muted-foreground, #6b6b6b);
    --_border: var(--border, #dddddd);
    --_input: var(--input, #cccccc);
    --_destructive: var(--destructive, #b3261e);
    --_success: var(--success, #2e7d32);
    --_overlay: var(--overlay, rgba(20, 20, 20, 0.45));
    --_radius: var(--radius, 10px);
    --_shadow: var(--shadow-lg, 0 12px 32px rgba(0, 0, 0, 0.18));
    --_font: var(--font-sans, system-ui, sans-serif);
    font-family: var(--_font);
  }
  [hidden] { display: none !important; }

  .fab {
    position: fixed;
    right: 1.25rem;
    bottom: 1.25rem;
    z-index: var(--z-fab, 1000);
    background: var(--_primary);
    color: var(--_primary-fg);
    border: none;
    border-radius: 999px;
    padding: 0.65em 1.2em;
    font: 600 0.9rem var(--_font);
    cursor: pointer;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.2);
    transition: transform 0.1s ease;
  }
  .fab:hover { transform: translateY(-2px); }
  .fab:focus-visible { outline: 2px solid var(--ring, #888); outline-offset: 2px; }

  .overlay {
    position: fixed;
    inset: 0;
    z-index: var(--z-modal, 1100);
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 1rem;
    background: var(--_overlay);
  }

  .modal {
    width: 100%;
    max-width: 30rem;
    max-height: 90vh;
    overflow-y: auto;
    background: var(--_bg);
    color: var(--_fg);
    border-radius: var(--_radius);
    box-shadow: var(--_shadow);
    display: flex;
    flex-direction: column;
  }
  .modal.dragover { outline: 3px dashed var(--_primary); outline-offset: -6px; }

  .header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 1rem 1.25rem 0.5rem;
  }
  .header h2 { margin: 0; font-size: 1.2rem; }
  .close {
    background: none;
    border: none;
    font-size: 1.5rem;
    line-height: 1;
    cursor: pointer;
    color: var(--_muted-fg);
    padding: 0.25rem;
  }

  .body { padding: 0 1.25rem; }
  .hint { font-size: 0.85rem; color: var(--_muted-fg); margin: 0.25rem 0 0.75rem; }

  .field { display: flex; flex-direction: column; gap: 0.25rem; margin-bottom: 0.75rem; }
  .row { display: flex; gap: 0.75rem; }
  .row > span { flex: 1; display: flex; flex-direction: column; gap: 0.25rem; }
  label { font-size: 0.85rem; font-weight: 600; }
  label small { font-weight: 400; color: var(--_muted-fg); }
  input[type="text"], input[type="email"], textarea {
    font: 1rem var(--_font);
    color: var(--_fg);
    background: var(--_bg);
    border: 1px solid var(--_input);
    border-radius: calc(var(--_radius) - 4px);
    padding: 0.45em 0.6em;
  }
  input:focus, textarea:focus { outline: 2px solid var(--_primary); outline-offset: 1px; }
  textarea { resize: vertical; min-height: 6em; }
  .count { font-size: 0.75rem; color: var(--_muted-fg); text-align: right; }

  /* honeypot: off-screen, not display:none — some bots skip hidden fields */
  .hp {
    position: absolute;
    left: -9999px;
    top: auto;
    width: 1px;
    height: 1px;
    overflow: hidden;
  }

  .shot-zone {
    border: 1.5px dashed var(--_input);
    border-radius: calc(var(--_radius) - 4px);
    padding: 0.6rem;
    text-align: center;
    font-size: 0.85rem;
    color: var(--_muted-fg);
    cursor: pointer;
  }
  .shot-zone:hover { border-color: var(--_primary); }
  .shot-preview {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    border: 1px solid var(--_border);
    border-radius: calc(var(--_radius) - 4px);
    padding: 0.4rem 0.6rem;
  }
  .shot-preview img {
    max-height: 3rem;
    max-width: 5rem;
    border-radius: 4px;
    display: block;
  }
  .shot-preview .meta { flex: 1; font-size: 0.8rem; color: var(--_muted-fg); overflow-wrap: anywhere; }
  .shot-remove {
    background: none;
    border: none;
    color: var(--_destructive);
    cursor: pointer;
    font-size: 0.85rem;
    font-weight: 600;
  }

  .error {
    background: color-mix(in srgb, var(--_destructive) 10%, transparent);
    color: var(--_destructive);
    border-radius: calc(var(--_radius) - 4px);
    padding: 0.5rem 0.75rem;
    font-size: 0.85rem;
    margin-bottom: 0.75rem;
  }

  .success { text-align: center; padding: 1.5rem 1.25rem; }
  .success .icon {
    width: 3rem; height: 3rem;
    margin: 0 auto 0.75rem;
    border-radius: 50%;
    background: var(--_success);
    color: #fff;
    display: flex; align-items: center; justify-content: center;
    font-size: 1.5rem;
  }
  .success a { color: var(--_primary); font-weight: 600; }

  .footer {
    display: flex;
    justify-content: flex-end;
    gap: 0.6rem;
    padding: 0.75rem 1.25rem 1.25rem;
  }
  .btn {
    font: 600 0.9rem var(--_font);
    border-radius: calc(var(--_radius) - 2px);
    padding: 0.5em 1.1em;
    cursor: pointer;
    border: 1px solid transparent;
  }
  .btn:disabled { opacity: 0.55; cursor: not-allowed; }
  .btn.primary { background: var(--_primary); color: var(--_primary-fg); }
  .btn.secondary {
    background: transparent;
    color: var(--_fg);
    border-color: var(--_border);
  }
</style>

<button type="button" class="fab" part="button" aria-haspopup="dialog"></button>

<div class="overlay" role="dialog" aria-modal="true" aria-labelledby="title" hidden>
  <div class="modal">
    <div class="header">
      <h2 id="title">Share Feedback</h2>
      <button type="button" class="close" aria-label="Close">&times;</button>
    </div>

    <div class="body" data-part="form">
      <p class="hint">
        Found a bug or have an idea? It goes straight onto our to-do list.
        Everything else lands in Richard's inbox.
      </p>
      <div class="row field">
        <span>
          <label for="name">Name <small>(optional)</small></label>
          <input type="text" id="name" maxlength="100" autocomplete="name">
        </span>
        <span>
          <label for="email">Email <small>(optional)</small></label>
          <input type="email" id="email" maxlength="200" autocomplete="email">
        </span>
      </div>
      <div class="hp" aria-hidden="true">
        <label for="website">Website</label>
        <input type="text" id="website" tabindex="-1" autocomplete="off">
      </div>
      <div class="field">
        <label for="text">Your feedback</label>
        <textarea id="text" rows="5" required
          placeholder="Describe a bug, suggest an improvement, or just say howdy&hellip;"></textarea>
        <div class="count"><span data-part="count">0</span> / 4000</div>
      </div>
      <div class="field">
        <label>Screenshot <small>(optional — attach, paste, or drop an image)</small></label>
        <div class="shot-zone" data-part="zone" tabindex="0" role="button"
             aria-label="Attach a screenshot">
          Click to attach an image, paste from clipboard, or drop it here
        </div>
        <div class="shot-preview" data-part="preview" hidden>
          <img alt="Screenshot preview">
          <span class="meta"></span>
          <button type="button" class="shot-remove">Remove</button>
        </div>
        <input type="file" data-part="file" accept="image/png,image/jpeg,image/gif,image/webp" hidden>
      </div>
      <div class="error" data-part="error" hidden></div>
    </div>

    <div class="success" data-part="success" hidden>
      <div class="icon">&#10003;</div>
      <p data-part="success-msg"></p>
    </div>

    <div class="footer" data-part="footer">
      <button type="button" class="btn secondary" data-part="cancel">Cancel</button>
      <button type="button" class="btn primary" data-part="send">
        <span data-part="send-label">Send</span>
        <span data-part="send-spinner" hidden>Sending&hellip;</span>
      </button>
    </div>
  </div>
</div>
`;

export class HastingFeedback extends (typeof HTMLElement !== "undefined"
  ? HTMLElement
  : class {}) {
  constructor() {
    super();
    this._file = null;
    this._previewUrl = null;
  }

  connectedCallback() {
    if (this.shadowRoot) return; // already initialized
    const root = this.attachShadow({ mode: "open" });
    root.innerHTML = TEMPLATE;

    this._els = {
      fab: root.querySelector(".fab"),
      overlay: root.querySelector(".overlay"),
      modal: root.querySelector(".modal"),
      close: root.querySelector(".close"),
      form: root.querySelector('[data-part="form"]'),
      name: root.getElementById("name"),
      email: root.getElementById("email"),
      website: root.getElementById("website"),
      text: root.getElementById("text"),
      count: root.querySelector('[data-part="count"]'),
      zone: root.querySelector('[data-part="zone"]'),
      preview: root.querySelector('[data-part="preview"]'),
      previewImg: root.querySelector('[data-part="preview"] img'),
      previewMeta: root.querySelector('[data-part="preview"] .meta'),
      previewRemove: root.querySelector(".shot-remove"),
      fileInput: root.querySelector('[data-part="file"]'),
      error: root.querySelector('[data-part="error"]'),
      success: root.querySelector('[data-part="success"]'),
      successMsg: root.querySelector('[data-part="success-msg"]'),
      footer: root.querySelector('[data-part="footer"]'),
      cancel: root.querySelector('[data-part="cancel"]'),
      send: root.querySelector('[data-part="send"]'),
      sendLabel: root.querySelector('[data-part="send-label"]'),
      sendSpinner: root.querySelector('[data-part="send-spinner"]'),
    };
    const e = this._els;

    e.fab.textContent = this.getAttribute("label") || "\u{1F4AC} Feedback";

    e.fab.addEventListener("click", () => this.open());
    e.close.addEventListener("click", () => this.close());
    e.cancel.addEventListener("click", () => this.close());
    e.overlay.addEventListener("click", (ev) => {
      if (ev.target === e.overlay) this.close();
    });
    this._onKeydown = (ev) => {
      if (ev.key === "Escape" && !e.overlay.hidden) this.close();
    };
    document.addEventListener("keydown", this._onKeydown);

    e.text.addEventListener("input", () => {
      e.count.textContent = String(e.text.value.length);
    });

    // screenshot: picker, paste, drag-drop
    e.zone.addEventListener("click", () => e.fileInput.click());
    e.zone.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" || ev.key === " ") {
        ev.preventDefault();
        e.fileInput.click();
      }
    });
    e.fileInput.addEventListener("change", () => {
      if (e.fileInput.files.length) this._setFile(e.fileInput.files[0]);
      e.fileInput.value = "";
    });
    this._onPaste = (ev) => {
      if (e.overlay.hidden) return;
      const item = Array.from(ev.clipboardData?.items || []).find((i) =>
        i.type.startsWith("image/"),
      );
      if (item) {
        ev.preventDefault();
        this._setFile(item.getAsFile());
      }
    };
    document.addEventListener("paste", this._onPaste);
    for (const evName of ["dragover", "dragenter"]) {
      e.modal.addEventListener(evName, (ev) => {
        ev.preventDefault();
        e.modal.classList.add("dragover");
      });
    }
    for (const evName of ["dragleave", "drop"]) {
      e.modal.addEventListener(evName, (ev) => {
        ev.preventDefault();
        e.modal.classList.remove("dragover");
        if (ev.type === "drop" && ev.dataTransfer?.files?.length) {
          this._setFile(ev.dataTransfer.files[0]);
        }
      });
    }
    e.previewRemove.addEventListener("click", () => this._clearFile());

    e.send.addEventListener("click", () => this._submit());
  }

  disconnectedCallback() {
    document.removeEventListener("keydown", this._onKeydown);
    document.removeEventListener("paste", this._onPaste);
    this._clearFile();
  }

  open() {
    this._els.overlay.hidden = false;
    document.body.style.overflow = "hidden";
    this._els.text.focus();
  }

  close() {
    this._els.overlay.hidden = true;
    document.body.style.overflow = "";
  }

  _setFile(file) {
    const err = validateFile(file);
    if (err) {
      this._showError(err);
      return;
    }
    this._clearFile();
    this._file = file;
    this._previewUrl = URL.createObjectURL(file);
    this._els.previewImg.src = this._previewUrl;
    this._els.previewMeta.textContent =
      `${file.name || "pasted image"} — ${(file.size / 1024).toFixed(0)} KB`;
    this._els.preview.hidden = false;
    this._els.zone.hidden = true;
    this._els.error.hidden = true;
  }

  _clearFile() {
    if (this._previewUrl) URL.revokeObjectURL(this._previewUrl);
    this._previewUrl = null;
    this._file = null;
    if (this._els) {
      this._els.preview.hidden = true;
      this._els.zone.hidden = false;
    }
  }

  _showError(msg) {
    this._els.error.textContent = msg;
    this._els.error.hidden = false;
  }

  async _submit() {
    const e = this._els;
    e.error.hidden = true;

    const app = this.getAttribute("app");
    if (!app) {
      this._showError("This widget is missing its app attribute.");
      return;
    }
    const textErr = validateText(e.text.value);
    if (textErr) {
      this._showError(textErr);
      return;
    }
    const fileErr = validateFile(this._file);
    if (fileErr) {
      this._showError(fileErr);
      return;
    }

    e.send.disabled = true;
    e.sendLabel.hidden = true;
    e.sendSpinner.hidden = false;

    const form = new FormData();
    for (const [k, v] of buildFormEntries({
      app,
      text: e.text.value,
      name: e.name.value,
      email: e.email.value,
      website: e.website.value,
      page: window.location.href,
    })) {
      form.append(k, v);
    }
    if (this._file) {
      form.append("screenshot", this._file, this._file.name || "screenshot.png");
    }

    const endpoint = this.getAttribute("endpoint") || DEFAULT_ENDPOINT;
    let data;
    try {
      const resp = await fetch(endpoint, { method: "POST", body: form });
      data = await resp.json();
    } catch {
      data = { ok: false, error: "Could not reach the server. Please try again." };
    }

    e.send.disabled = false;
    e.sendLabel.hidden = false;
    e.sendSpinner.hidden = true;

    if (!data.ok) {
      this._showError(data.error || "Something went wrong. Please try again.");
      return;
    }

    e.successMsg.innerHTML = successMessage(data);
    e.form.hidden = true;
    e.footer.hidden = true;
    e.success.hidden = false;

    setTimeout(() => {
      this.close();
      e.form.hidden = false;
      e.footer.hidden = false;
      e.success.hidden = true;
      e.text.value = "";
      e.count.textContent = "0";
      this._clearFile();
    }, 6000);
  }
}

if (typeof customElements !== "undefined" &&
    !customElements.get("hasting-feedback")) {
  customElements.define("hasting-feedback", HastingFeedback);
}
