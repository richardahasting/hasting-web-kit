/* Unit tests for the pure core of kit/feedback.js.
 * Run with: node --test tests/component/*.test.mjs
 * No DOM required — the DOM layer is exercised by the browser harness
 * (tests/component/test-page.html) and the deploy smoke test.
 */

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  MIN_TEXT_CHARS,
  MAX_TEXT_CHARS,
  MAX_FILE_BYTES,
  validateText,
  validateFile,
  buildFormEntries,
  successMessage,
  isSafeIssueUrl,
  escapeHtml,
} from "../../kit/feedback.js";

/* ---- validateText ---------------------------------------------------- */

test("text below minimum is rejected", () => {
  assert.match(validateText("too short"), /at least 10/);
});

test("text of exactly the minimum passes", () => {
  assert.equal(validateText("x".repeat(MIN_TEXT_CHARS)), null);
});

test("whitespace padding does not count toward the minimum", () => {
  assert.match(validateText("   hi   \n\n\t   "), /at least 10/);
});

test("text at the maximum passes; one over fails", () => {
  assert.equal(validateText("x".repeat(MAX_TEXT_CHARS)), null);
  assert.match(validateText("x".repeat(MAX_TEXT_CHARS + 1)), /4000/);
});

test("null and undefined text are rejected, not crashed on", () => {
  assert.match(validateText(null), /at least 10/);
  assert.match(validateText(undefined), /at least 10/);
});

/* ---- validateFile ---------------------------------------------------- */

test("no file is fine (screenshot is optional)", () => {
  assert.equal(validateFile(null), null);
  assert.equal(validateFile(undefined), null);
});

test("allowed image types pass", () => {
  for (const type of ["image/png", "image/jpeg", "image/gif", "image/webp"]) {
    assert.equal(validateFile({ type, size: 1024, name: "s" }), null);
  }
});

test("disallowed types are rejected — including svg and pdf", () => {
  for (const type of ["image/svg+xml", "application/pdf", "text/html", ""]) {
    assert.match(validateFile({ type, size: 10, name: "f" }), /PNG, JPEG/);
  }
});

test("file at the size cap passes; one byte over fails", () => {
  assert.equal(validateFile({ type: "image/png", size: MAX_FILE_BYTES }), null);
  assert.match(validateFile({ type: "image/png", size: MAX_FILE_BYTES + 1 }), /5 MB/);
});

test("empty file is rejected", () => {
  assert.match(validateFile({ type: "image/png", size: 0 }), /empty/);
});

/* ---- buildFormEntries ------------------------------------------------- */

test("form entries carry all fields in service order", () => {
  const entries = buildFormEntries({
    app: "canyon-lake",
    text: "  the lake chart is broken  ",
    name: " Jane ",
    email: " jane@x.org ",
    website: "",
    page: "https://x/chart",
  });
  assert.deepEqual(entries, [
    ["app", "canyon-lake"],
    ["feedback_text", "the lake chart is broken"],
    ["name", "Jane"],
    ["email", "jane@x.org"],
    ["website", ""],
    ["page", "https://x/chart"],
  ]);
});

test("honeypot value is passed through untouched, never trimmed away", () => {
  const entries = buildFormEntries({ app: "a", text: "0123456789", website: "bot!" });
  assert.deepEqual(entries.find(([k]) => k === "website"), ["website", "bot!"]);
});

test("missing optional fields become empty strings", () => {
  const entries = buildFormEntries({ app: "a", text: "0123456789" });
  for (const [, v] of entries) assert.equal(typeof v, "string");
});

/* ---- isSafeIssueUrl / successMessage ---------------------------------- */

test("real GitHub issue URLs are safe", () => {
  assert.ok(isSafeIssueUrl("https://github.com/richardahasting/toodoo/issues/360"));
});

test("non-issue and non-GitHub URLs are refused", () => {
  for (const url of [
    "http://github.com/o/r/issues/1",           // not https
    "https://github.com.evil.com/o/r/issues/1", // lookalike host
    "https://evil.com/issues/1",
    "javascript:alert(1)",
    "data:text/html,hi",
    "https://github.com/o/r/pull/1",            // not an issue
    "",
    null,
    42,
  ]) {
    assert.equal(isSafeIssueUrl(url), false, `should refuse: ${url}`);
  }
});

test("success message links only safe issue URLs", () => {
  const msg = successMessage({
    ok: true,
    action: "github_issue",
    github_url: "https://github.com/richardahasting/toodoo/issues/9",
  });
  assert.match(msg, /href="https:\/\/github\.com\/richardahasting\/toodoo\/issues\/9"/);
  assert.match(msg, /rel="noopener noreferrer"/);
});

test("unsafe URL degrades to linkless thank-you, not an injected link", () => {
  const msg = successMessage({
    ok: true,
    action: "github_issue",
    github_url: "javascript:alert(1)",
  });
  assert.equal(msg.includes("<a"), false);
  assert.match(msg, /to-do list/);
});

test("email route gets the plain thank-you", () => {
  assert.match(successMessage({ ok: true, action: "email" }), /on its way/);
});

/* ---- escapeHtml -------------------------------------------------------- */

test("escapeHtml neutralizes markup", () => {
  assert.equal(
    escapeHtml(`<img src=x onerror="alert('1')">&`),
    "&lt;img src=x onerror=&quot;alert(&#39;1&#39;)&quot;&gt;&amp;",
  );
});
