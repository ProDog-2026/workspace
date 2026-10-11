"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { test } = require("node:test");
const { JSDOM } = require("jsdom");

const staticDirectory = path.join(__dirname, "..", "src", "prodog_gmail", "static");
const html = fs.readFileSync(path.join(staticDirectory, "index.html"), "utf8");
const script = fs.readFileSync(path.join(staticDirectory, "app.js"), "utf8");
const mail = {
  id: "mail-one", thread_id: "thread-one", subject: "검토할 메일", sender: "sender@example.invalid",
  received_at: "2026-09-27T00:00:00Z", labels: ["INBOX"], body_hash: "a".repeat(64),
  text_plain: "민감한 예시 메일 본문", text_html: "<p>원문</p>", analysis_text: "UNTRUSTED\n민감한 예시 메일 본문",
  trust: "UNTRUSTED", warnings: [], truncated: false, attachments: [],
};

function session(authenticated = false, options = {}) {
  return { authenticated, email: authenticated ? "demo@prodog.invalid" : null,
    csrf_token: "csrf-original", mode: "demo", configured: true, needs_reconnect: false, ...options };
}

function response(data, status = 200) { return { data, status }; }
function failure(code, status = 401) { return response({ error: { code, message: `Server error: ${code}` } }, status); }
function inbox(messages = [mail], next = null) { return response({ messages, next_page_token: next, result_size_estimate: messages.length }); }
const immediate = () => new Promise((resolve) => setImmediate(resolve));

async function settle() { for (let step = 0; step < 8; step += 1) await immediate(); }

async function mount(t, handler, options = {}) {
  // No resources option: JSDOM never fetches external scripts/styles/images. Only
  // the already-read local app.js executes, and fetch is fully mocked below.
  const dom = new JSDOM(html, { url: options.url || "http://127.0.0.1:8765/", runScripts: "outside-only" });
  const { window } = dom;
  const calls = [];
  const errors = [];
  window.addEventListener("error", (event) => errors.push(event.error));
  window.matchMedia = () => ({ matches: Boolean(options.mobile) });
  window.HTMLElement.prototype.scrollIntoView = function scrollIntoView() {};
  window.confirm = options.confirm || (() => true);
  window.fetch = async (url, request) => {
    calls.push({ url, request });
    const value = await handler(url, request);
    assert.ok(value, `No mocked response for ${url}`);
    return { ok: value.status >= 200 && value.status < 300, status: value.status, json: async () => value.data };
  };
  // A regression to innerHTML would execute no script in outside-only mode, so
  // reject that sink explicitly in addition to checking the resulting DOM.
  const originalInnerHTML = Object.getOwnPropertyDescriptor(window.Element.prototype, "innerHTML");
  Object.defineProperty(window.Element.prototype, "innerHTML", { configurable: true, set() { throw new Error("Mail content must use textContent"); } });
  t.after(() => {
    Object.defineProperty(window.Element.prototype, "innerHTML", originalInnerHTML);
    window.close();
    assert.deepEqual(errors, []);
  });
  window.eval(script);
  await settle();
  const byId = (id) => window.document.getElementById(id);
  return { window, document: window.document, calls, byId,
    async click(id) { byId(id).click(); await settle(); },
    async select(index = 0) { window.document.querySelectorAll(".mail-item")[index].click(); await settle(); },
  };
}

test("demo connect rotates CSRF, loads inbox, renders inert content, and disconnects", async (t) => {
  let connected = false;
  let csrf = "csrf-original";
  const hostile = { ...mail, subject: '<img src="https://tracker.invalid/subject">',
    sender: '<script>window.compromised = true</script>',
    text_plain: '<img src="https://tracker.invalid/plain" onerror="window.compromised=true">',
    text_html: '<img src="https://tracker.invalid/html"><script>window.compromised=true</script>',
    analysis_text: '<iframe src="https://tracker.invalid/frame"></iframe>UNTRUSTED',
  };
  const app = await mount(t, (url, request) => {
    if (url === "/api/session") return response(session(connected, { csrf_token: csrf }));
    if (url === "/api/demo/connect") {
      assert.equal(request.method, "POST");
      assert.equal(request.headers["X-CSRF-Token"], "csrf-original");
      connected = true; csrf = "csrf-rotated";
      return response(session(true, { csrf_token: csrf }));
    }
    if (url.startsWith("/api/mails?")) return inbox([hostile]);
    if (url === "/api/mails/mail-one") return response(hostile);
    if (url === "/api/disconnect") {
      assert.equal(request.headers["X-CSRF-Token"], "csrf-rotated");
      connected = false; csrf = "csrf-after-disconnect";
      return response({ disconnected: true });
    }
    throw new Error(`Unexpected request ${url}`);
  });
  assert.equal(app.byId("mode-badge").textContent, "데모 모드");
  assert.equal(app.byId("connect-button").hidden, false);
  assert.equal(app.byId("refresh-button").disabled, true);
  await app.click("connect-button");
  assert.equal(app.byId("connection-badge").textContent, "연결됨");
  assert.equal(app.byId("account-label").textContent, "demo@prodog.invalid");
  assert.equal(app.byId("connect-button").hidden, true);
  assert.equal(app.document.querySelectorAll(".mail-item").length, 1);
  assert.match(app.byId("notice").textContent, /실제 Google 계정이나 Gmail 데이터에 접근하지 않습니다/);
  await app.select();
  for (const field of ["subject", "sender", "text_plain", "text_html", "analysis_text"]) assert.ok(app.byId("mail-detail").textContent.includes(hostile[field]));
  assert.equal(app.document.querySelectorAll("#mail-detail img, #mail-detail script, #mail-detail iframe, #mail-detail a").length, 0);
  assert.equal(app.document.querySelectorAll("#mail-list img, #mail-list script").length, 0);
  assert.equal(app.window.compromised, undefined);
  await app.click("disconnect-button");
  assert.equal(app.byId("connect-button").hidden, false);
  assert.equal(app.byId("disconnect-button").hidden, true);
  assert.ok(!app.byId("mail-detail").textContent.includes(hostile.text_plain));
  for (const call of app.calls) assert.equal(call.request.credentials, "same-origin");
});

test("pagination uses server tokens and returns to the previous token", async (t) => {
  const listCalls = [];
  const app = await mount(t, (url) => {
    if (url === "/api/session") return response(session(true));
    if (url.startsWith("/api/mails?")) {
      listCalls.push(url);
      return inbox([{ ...mail, subject: url.includes("page_token") ? "두 번째 페이지" : "첫 페이지" }], url.includes("page_token") ? null : "next+/=token");
    }
    throw new Error(`Unexpected request ${url}`);
  });
  assert.equal(app.byId("previous-button").disabled, true);
  await app.click("next-button");
  assert.equal(new URL(listCalls.at(-1), "http://local").searchParams.get("page_token"), "next+/=token");
  assert.equal(app.byId("page-label").textContent, "2 페이지");
  assert.equal(app.byId("next-button").disabled, true);
  await app.click("previous-button");
  assert.equal(new URL(listCalls.at(-1), "http://local").searchParams.get("page_token"), null);
  assert.equal(app.byId("page-label").textContent, "1 페이지");
});

test("401 exposes reconnect even while session metadata remains authenticated", async (t) => {
  const app = await mount(t, (url) => url === "/api/session" ? response(session(true, { mode: "google" })) : failure("reconnect_required"));
  assert.equal(app.byId("connection-badge").textContent, "재연결 필요");
  assert.equal(app.byId("connect-button").hidden, false);
  assert.equal(app.byId("disconnect-button").hidden, false);
  assert.equal(app.byId("refresh-button").disabled, true);
  assert.equal(app.byId("error-notice").hidden, false);
  assert.equal(app.document.querySelectorAll(".mail-item").length, 0);
});

test("initial needs_reconnect shows reconnect without fetching stale account mail", async (t) => {
  const app = await mount(t, (url) => {
    assert.equal(url, "/api/session");
    return response(session(true, { mode: "google", needs_reconnect: true }));
  });
  assert.equal(app.byId("connection-badge").textContent, "재연결 필요");
  assert.equal(app.byId("connect-button").hidden, false);
  assert.equal(app.byId("connect-button").disabled, false);
  assert.equal(app.byId("disconnect-button").hidden, false);
  assert.equal(app.byId("refresh-button").disabled, true);
  assert.equal(app.calls.length, 1);
});

test("logout clears previous account data when session refresh fails", async (t) => {
  let sessionReads = 0;
  const app = await mount(t, (url) => {
    if (url === "/api/session") {
      if (++sessionReads > 1) throw new Error("offline after logout");
      return response(session(true));
    }
    if (url.startsWith("/api/mails?")) return inbox();
    if (url === "/api/mails/mail-one") return response(mail);
    if (url === "/api/logout") return response({ logged_out: true });
    throw new Error(`Unexpected request ${url}`);
  });
  await app.select();
  assert.ok(app.byId("mail-detail").textContent.includes(mail.text_plain));
  await app.click("logout-button");
  assert.ok(!app.byId("mail-detail").textContent.includes(mail.text_plain));
  assert.equal(app.document.querySelectorAll(".mail-item").length, 0);
  assert.equal(app.byId("logout-button").hidden, true);
  assert.equal(app.byId("connect-button").hidden, false);
  assert.equal(app.byId("error-notice").hidden, false);
});

test("late detail response cannot restore mail after logout", async (t) => {
  let authenticated = true;
  let finishDetail;
  const app = await mount(t, (url) => {
    if (url === "/api/session") return response(session(authenticated));
    if (url.startsWith("/api/mails?")) return inbox();
    if (url === "/api/mails/mail-one") return new Promise((resolve) => { finishDetail = () => resolve(response(mail)); });
    if (url === "/api/logout") { authenticated = false; return response({ logged_out: true }); }
    throw new Error(`Unexpected request ${url}`);
  });
  await app.select();
  assert.equal(app.byId("mail-detail").getAttribute("aria-busy"), "true");
  await app.click("logout-button");
  finishDetail();
  await settle();
  assert.ok(!app.byId("mail-detail").textContent.includes(mail.text_plain));
  assert.equal(app.byId("mail-detail").getAttribute("aria-busy"), "false");
});

test("attachment metadata stays inert and explicitly reports unexamined content", async (t) => {
  const attachedMail = { ...mail, warnings: ["attachment_content_not_analyzed"], attachments: [
    { filename: '<img src="https://tracker.invalid/attachment">.eml', content_type: "message/rfc822", size_bytes: null, is_inline: false },
    { filename: "logo.png", content_type: "image/png", size_bytes: 2048, is_inline: true },
  ] };
  const app = await mount(t, (url) => {
    if (url === "/api/session") return response(session(true));
    if (url.startsWith("/api/mails?")) return inbox([attachedMail]);
    return response(attachedMail);
  });
  await app.select();
  assert.equal(app.document.querySelectorAll(".attachment-item").length, 2);
  assert.ok(app.byId("mail-detail").textContent.includes(attachedMail.attachments[0].filename));
  assert.match(app.document.querySelector(".attachment-section").textContent, /첨부 내용은 분석하지 않았습니다/);
  assert.match(app.document.querySelector(".attachment-section").textContent, /크기 미확인/);
  assert.match(app.document.querySelector(".attachment-section").textContent, /2.0 KB/);
  assert.equal(app.document.querySelectorAll(".attachment-section img, .attachment-section a").length, 0);
  assert.match(app.document.querySelector(".warning-list").textContent, /첨부 내용은 분석하지 않았습니다/);
});

test("CSRF refresh honors needs_reconnect and does not replay destructive POST", async (t) => {
  let sessionReads = 0;
  let logoutCalls = 0;
  const app = await mount(t, (url) => {
    if (url === "/api/session") return response(session(true, { mode: "google", csrf_token: ++sessionReads === 1 ? "old" : "new", needs_reconnect: sessionReads > 1 }));
    if (url.startsWith("/api/mails?")) return inbox();
    if (url === "/api/mails/mail-one") return response(mail);
    if (url === "/api/logout") { logoutCalls += 1; return failure("csrf_failed", 403); }
    throw new Error(`Unexpected request ${url}`);
  });
  await app.select();
  await app.click("logout-button");
  assert.equal(logoutCalls, 1);
  assert.equal(app.byId("connection-badge").textContent, "재연결 필요");
  assert.ok(!app.byId("mail-detail").textContent.includes(mail.text_plain));
  assert.equal(app.byId("connect-button").hidden, false);
});

test("canceling real Google disconnect makes no revocation request", async (t) => {
  let confirms = 0;
  const app = await mount(t, (url) => {
    if (url === "/api/session") return response(session(true, { mode: "google" }));
    if (url.startsWith("/api/mails?")) return inbox();
    throw new Error(`Unexpected request ${url}`);
  }, { confirm: (text) => { confirms += 1; assert.match(text, /같은 Google Cloud 프로젝트/); return false; } });
  await app.click("disconnect-button");
  assert.equal(confirms, 1);
  assert.equal(app.calls.some((call) => call.url === "/api/disconnect"), false);
  assert.equal(app.byId("connection-badge").textContent, "연결됨");
});

test("OAuth callback messages remove callback parameters and preserve unrelated query", async (t) => {
  const app = await mount(t, () => response(session(false, { mode: "google" })), { url: "http://127.0.0.1:8765/?error=oauth_denied&keep=one#detail" });
  assert.equal(app.window.location.search, "?keep=one");
  assert.equal(app.window.location.hash, "#detail");
  assert.match(app.byId("error-notice").textContent, /연결을 취소했습니다/);
});
