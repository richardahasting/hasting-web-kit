/* Headless-browser verification of the <hasting-feedback> DOM layer.
 *
 * Run:  node tests/component/browser-check.mjs
 * (uses the puppeteer installed at ~/node_modules)
 *
 * Spawns a static server for the repo and a stub feedback endpoint (no real
 * routing — no issues or emails are created), then drives the real widget in
 * headless Chrome:
 *   1. modal is hidden on load (the bug that started all this)
 *   2. button opens it, Escape closes it
 *   3. short text -> inline error
 *   4. screenshot attach -> preview appears, Remove clears it
 *   5. valid submit -> multipart POST arrives, success panel links the issue
 */

import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { writeFileSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const require = createRequire("/home/richard/");
const puppeteer = require("puppeteer");

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const STATIC_PORT = 8099;
const STUB_PORT = 8090;

let failures = 0;
function check(name, cond, extra = "") {
  const mark = cond ? "PASS" : "FAIL";
  if (!cond) failures++;
  console.log(`${mark}  ${name}${cond ? "" : "  " + extra}`);
}

// ---- stub feedback endpoint ------------------------------------------------
const received = [];
const stub = createServer((req, res) => {
  let body = [];
  req.on("data", (c) => body.push(c));
  req.on("end", () => {
    received.push({ method: req.method, body: Buffer.concat(body).toString("latin1") });
    res.writeHead(200, {
      "Content-Type": "application/json",
      "Access-Control-Allow-Origin": "*",
    });
    res.end(JSON.stringify({
      ok: true,
      action: "github_issue",
      github_url: "https://github.com/richardahasting/test-app/issues/123",
    }));
  });
});
await new Promise((r) => stub.listen(STUB_PORT, r));

// ---- static server ---------------------------------------------------------
const statics = spawn("python3", ["-m", "http.server", String(STATIC_PORT)],
  { cwd: repoRoot, stdio: "ignore" });
await new Promise((r) => setTimeout(r, 1200));

// ---- tiny png fixture ------------------------------------------------------
const pngPath = join(mkdtempSync(join(tmpdir(), "hwk-")), "shot.png");
writeFileSync(pngPath, Buffer.concat([
  Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
  Buffer.from("fake image bytes for upload testing"),
]));

// ---- drive the widget ------------------------------------------------------
const browser = await puppeteer.launch({ headless: "new" });
try {
  const page = await browser.newPage();
  await page.goto(
    `http://localhost:${STATIC_PORT}/tests/component/test-page.html`,
    { waitUntil: "networkidle0" });

  const $ = (sel) => page.evaluate((s) => {
    const el = document.querySelector("hasting-feedback").shadowRoot.querySelector(s);
    return el ? { hidden: el.hidden, text: el.textContent.trim() } : null;
  }, sel);
  const click = (sel) => page.evaluate((s) => {
    document.querySelector("hasting-feedback").shadowRoot.querySelector(s).click();
  }, sel);

  // 1. hidden on load
  check("modal hidden on page load", (await $(".overlay")).hidden === true);
  check("feedback button visible", (await $(".fab")).text.includes("Feedback"));

  // 2. open / close
  await click(".fab");
  check("button click opens modal", (await $(".overlay")).hidden === false);
  await page.keyboard.press("Escape");
  check("Escape closes modal", (await $(".overlay")).hidden === true);
  await click(".fab");

  // 3. short-text validation
  await page.evaluate(() => {
    const r = document.querySelector("hasting-feedback").shadowRoot;
    r.getElementById("text").value = "short";
  });
  await click('[data-part="send"]');
  const err = await $('[data-part="error"]');
  check("short text shows inline error", !err.hidden && /at least 10/.test(err.text),
    JSON.stringify(err));

  // 4. screenshot attach + remove
  const fileInput = await page.evaluateHandle(() =>
    document.querySelector("hasting-feedback").shadowRoot
      .querySelector('[data-part="file"]'));
  await fileInput.uploadFile(pngPath);
  await new Promise((r) => setTimeout(r, 300));
  const preview = await $('[data-part="preview"]');
  check("screenshot preview appears", preview.hidden === false &&
    preview.text.includes("shot.png"), JSON.stringify(preview));
  await click(".shot-remove");
  check("Remove clears the preview", (await $('[data-part="preview"]')).hidden === true);
  await fileInput.uploadFile(pngPath); // re-attach for the real submit
  await new Promise((r) => setTimeout(r, 300));

  // 5. valid submit round trip
  await page.evaluate(() => {
    const r = document.querySelector("hasting-feedback").shadowRoot;
    r.getElementById("text").value = "The browser check says the widget works.";
    r.getElementById("name").value = "Headless Harness";
  });
  await click('[data-part="send"]');
  await page.waitForFunction(() =>
    !document.querySelector("hasting-feedback").shadowRoot
      .querySelector('[data-part="success"]').hidden, { timeout: 5000 });

  const success = await $('[data-part="success-msg"]');
  check("success panel shown after submit", /to-do list/.test(success.text));
  const link = await page.evaluate(() =>
    document.querySelector("hasting-feedback").shadowRoot
      .querySelector('[data-part="success-msg"] a')?.href || "");
  check("success links the created issue",
    link === "https://github.com/richardahasting/test-app/issues/123", link);

  const post = received.find((r) => r.method === "POST");
  check("service received multipart POST", Boolean(post));
  check("POST carries feedback text",
    post.body.includes("The browser check says the widget works."));
  check("POST carries app id", post.body.includes('name="app"') &&
    post.body.includes("test-app"));
  check("POST carries screenshot file",
    post.body.includes('name="screenshot"') && post.body.includes("PNG"));
  check("honeypot sent empty",
    /name="website"\r\n\r\n\r\n/.test(post.body));
} finally {
  await browser.close();
  statics.kill();
  stub.close();
}

console.log(failures === 0 ? "\nAll browser checks passed."
  : `\n${failures} browser check(s) FAILED.`);
process.exit(failures === 0 ? 0 : 1);
