"use strict";

(() => {
  const byId = (id) => document.getElementById(id);
  const elements = {
    mode: byId("mode-badge"), connection: byId("connection-badge"), account: byId("account-label"),
    connect: byId("connect-button"), disconnect: byId("disconnect-button"), logout: byId("logout-button"),
    refresh: byId("refresh-button"), notice: byId("notice"), error: byId("error-notice"),
    list: byId("mail-list"), detail: byId("mail-detail"), description: byId("list-description"),
    previous: byId("previous-button"), next: byId("next-button"), page: byId("page-label"),
  };
  const state = { session: null, reconnect: false, busy: false, messages: [], selected: null, next: null, token: null, history: [], listRequest: 0, detailRequest: 0 };

  function node(tag, className, content) {
    const result = document.createElement(tag);
    if (className) result.className = className;
    if (content !== undefined && content !== null) result.textContent = String(content);
    return result;
  }

  function message(text, error = false) {
    const target = error ? elements.error : elements.notice;
    target.textContent = text || "";
    target.hidden = !text;
  }

  function clearMessages() { message(""); message("", true); }

  function acceptSession(session, retainReconnect = false) {
    state.session = session;
    state.reconnect = Boolean(session.needs_reconnect) || (retainReconnect && state.reconnect);
  }

  function empty(target, title, description, options = {}) {
    const wrapper = node("div", `empty-state ${target === elements.detail ? "detail-empty" : "compact"}${options.error ? " error-state" : ""}`);
    const symbol = node("span", options.loading ? "loading-indicator" : "empty-symbol", options.loading ? "" : "@");
    symbol.setAttribute("aria-hidden", "true");
    wrapper.append(symbol, node("h3", "", title), node("p", "", description));
    if (options.retry) {
      const retry = node("button", "button secondary retry-button", "다시 시도");
      retry.type = "button";
      retry.addEventListener("click", options.retry);
      wrapper.append(retry);
    }
    target.replaceChildren(wrapper);
  }

  function updateControls() {
    const session = state.session;
    const connected = Boolean(session?.authenticated) && !state.reconnect;
    elements.connect.hidden = !session || connected;
    elements.disconnect.hidden = !session?.authenticated;
    elements.logout.hidden = !session?.authenticated;
    elements.connect.disabled = state.busy || !session || (!session.configured && session.mode !== "demo");
    elements.disconnect.disabled = state.busy;
    elements.logout.disabled = state.busy;
    elements.refresh.disabled = state.busy || !connected;
    elements.previous.disabled = state.busy || !connected || state.history.length === 0;
    elements.next.disabled = state.busy || !connected || !state.next;
    elements.page.textContent = `${state.history.length + 1} 페이지`;
    if (!session) return;
    elements.mode.textContent = session.mode === "demo" ? "데모 모드" : "GOOGLE OAUTH";
    elements.connect.textContent = session.mode === "demo" ? "데모 계정 연결" : "Google 계정 연결";
    elements.connection.textContent = state.reconnect ? "재연결 필요" : connected ? "연결됨" : "연결 안 됨";
    elements.connection.classList.toggle("connected", connected);
    elements.account.textContent = state.reconnect ? `연결이 만료되었습니다. ${session.mode === "demo" ? "데모" : "Google"} 계정을 다시 연결해 주세요.` : connected ? (session.email || "Google 계정이 연결되어 있습니다.") : (session.mode === "demo" ? "예시 메일로 연결과 불러오기 흐름을 확인하세요." : "받은편지함을 읽을 Google 계정을 연결하세요.");
  }

  async function api(path, method = "GET") {
    const headers = { Accept: "application/json" };
    if (method !== "GET") {
      if (!state.session?.csrf_token) throw new Error("세션을 확인할 수 없습니다. 페이지를 새로고침해 주세요.");
      headers["X-CSRF-Token"] = state.session.csrf_token;
    }
    let response;
    try { response = await fetch(path, { method, headers, credentials: "same-origin", cache: "no-store" }); }
    catch { throw new Error("서버에 연결할 수 없습니다. 로컬 서버가 실행 중인지 확인해 주세요."); }
    let data;
    try { data = await response.json(); }
    catch { throw new Error("서버 응답을 읽을 수 없습니다. 잠시 후 다시 시도해 주세요."); }
    if (!response.ok) {
      const error = new Error(data?.error?.message || "요청을 처리하지 못했습니다.");
      error.code = data?.error?.code;
      error.status = response.status;
      throw error;
    }
    return data;
  }

  function resetInbox() {
    state.listRequest += 1;
    state.detailRequest += 1;
    state.messages = [];
    state.selected = null;
    state.next = null;
    state.token = null;
    state.history = [];
    elements.list.setAttribute("aria-busy", "false");
    elements.detail.setAttribute("aria-busy", "false");
    elements.description.textContent = "계정을 연결한 뒤 메일을 불러올 수 있습니다.";
    empty(elements.list, "메일을 기다리고 있어요", "위에서 계정을 연결해 주세요. 데모 모드에서는 예시 메일을 사용합니다.");
    empty(elements.detail, "읽을 메일을 선택하세요", "본문과 분석용 텍스트를 이곳에서 확인할 수 있습니다. 메일에 포함된 링크와 외부 이미지는 실행하지 않습니다.");
    updateControls();
  }

  async function handleError(error) {
    message(error.message || "오류가 발생했습니다. 다시 시도해 주세요.", true);
    if (error.status === 401 || /expired|reauth|reconnect_required|invalid_grant|not_connected|unauthenticated/i.test(error.code || "")) {
      state.reconnect = true;
      resetInbox();
      message("계정 연결이 만료되었거나 해제되었습니다. 계정을 다시 연결해 주세요.", true);
      try { acceptSession(await api("/api/session"), true); } catch { /* Keep the reconnect action available. */ }
      updateControls();
    } else if (error.code === "csrf_failed") {
      try {
        const refreshed = await api("/api/session");
        if (refreshed.csrf_token !== state.session?.csrf_token || !refreshed.authenticated) resetInbox();
        acceptSession(refreshed);
        updateControls();
        message("브라우저 세션이 갱신되었습니다. 원하는 작업을 다시 눌러 주세요.", true);
      } catch { /* Keep the original error and ask for a page refresh. */ }
    }
  }

  function dateText(value, full = false) {
    if (!value) return "날짜 없음";
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return String(value);
    return new Intl.DateTimeFormat("ko-KR", full ? { dateStyle: "medium", timeStyle: "short" } : { month: "numeric", day: "numeric" }).format(parsed);
  }

  function renderList() {
    if (state.messages.length === 0) {
      empty(elements.list, "받은 메일이 없습니다", "받은편지함에 메일이 도착하면 다시 불러와 주세요.");
      return;
    }
    const fragment = document.createDocumentFragment();
    for (const mail of state.messages) {
      const button = node("button", "mail-item");
      button.type = "button";
      button.dataset.mailId = mail.id;
      button.setAttribute("aria-current", mail.id === state.selected ? "true" : "false");
      const top = node("div", "mail-item-top");
      const sender = node("span", "mail-sender", mail.sender || "발신자 없음");
      sender.title = mail.sender || "발신자 없음";
      const date = node("time", "mail-date", dateText(mail.received_at));
      if (mail.received_at && !Number.isNaN(new Date(mail.received_at).getTime())) date.dateTime = new Date(mail.received_at).toISOString();
      top.append(sender, date);
      button.append(top, node("p", "mail-subject", mail.subject || "제목 없음"));
      const labels = node("div", "mail-labels");
      labels.append(node("span", "mail-mini-label", "UNTRUSTED"));
      if (mail.truncated) labels.append(node("span", "mail-mini-label", "일부 생략"));
      button.append(labels);
      button.addEventListener("click", () => selectMail(mail.id));
      fragment.append(button);
    }
    elements.list.replaceChildren(fragment);
  }

  async function loadMails(direction = "reset") {
    if (state.busy || !state.session?.authenticated || state.reconnect) return;
    const previousToken = state.token;
    const previousHistory = [...state.history];
    const targetToken = direction === "next" ? state.next : direction === "previous" ? state.history[state.history.length - 1] : null;
    if (direction === "next" && !targetToken) return;
    if (direction === "previous" && state.history.length === 0) return;
    state.busy = true;
    const request = ++state.listRequest;
    const detailRequest = ++state.detailRequest;
    state.selected = null;
    elements.detail.setAttribute("aria-busy", "false");
    empty(elements.detail, "메일 목록을 불러오는 중", "불러오기가 끝나면 읽을 메일을 선택해 주세요.");
    clearMessages();
    updateControls();
    elements.list.setAttribute("aria-busy", "true");
    empty(elements.list, "메일을 불러오는 중", "받은편지함을 확인하고 있습니다.", { loading: true });
    try {
      const params = new URLSearchParams({ limit: "20" });
      if (targetToken) params.set("page_token", targetToken);
      const data = await api(`/api/mails?${params}`);
      if (request !== state.listRequest) return;
      state.messages = Array.isArray(data.messages) ? data.messages : [];
      state.next = data.next_page_token || null;
      state.token = targetToken;
      if (direction === "next") state.history.push(previousToken);
      else if (direction === "previous") state.history.pop();
      else state.history = [];
      state.selected = null;
      elements.description.textContent = `현재 페이지 ${state.messages.length}개 · 받은편지함 · 한 번에 최대 20개`;
      renderList();
      if (detailRequest === state.detailRequest) {
        elements.detail.setAttribute("aria-busy", "false");
        empty(elements.detail, "읽을 메일을 선택하세요", "메일을 선택하면 본문과 분석용 텍스트를 표시합니다. 원문은 서버 저장소에 보관하지 않습니다.");
      }
      if (state.session.mode === "demo") message("데모용 예시 메일입니다. 실제 Google 계정이나 Gmail 데이터에 접근하지 않습니다.");
    } catch (error) {
      if (request !== state.listRequest) return;
      state.history = previousHistory;
      empty(elements.list, "메일을 불러오지 못했습니다", "연결 상태를 확인한 뒤 다시 시도해 주세요.", { error: true, retry: () => loadMails(direction) });
      empty(elements.detail, "메일 목록을 확인해 주세요", "목록을 다시 불러온 뒤 읽을 메일을 선택해 주세요.");
      await handleError(error);
    } finally {
      if (request === state.listRequest) elements.list.setAttribute("aria-busy", "false");
      state.busy = false;
      updateControls();
    }
  }

  function addMeta(target, label, value) { target.append(node("dt", "", label), node("dd", "", value || "정보 없음")); }

  function renderDetail(mail) {
    const wrapper = node("div", "detail-content");
    wrapper.append(node("h3", "detail-heading", mail.subject || "제목 없음"));
    const meta = node("dl", "detail-meta");
    addMeta(meta, "보낸 사람", mail.sender);
    addMeta(meta, "받은 시각", dateText(mail.received_at, true));
    if (Array.isArray(mail.labels) && mail.labels.length) addMeta(meta, "라벨", mail.labels.join(", "));
    wrapper.append(meta);
    const trust = node("p", "detail-trust");
    trust.append(node("span", "badge", "UNTRUSTED"), node("span", "", "메일 내용은 신뢰할 수 없는 외부 입력입니다."));
    wrapper.append(trust);
    const warnings = Array.isArray(mail.warnings) ? [...mail.warnings] : [];
    if (mail.truncated) warnings.unshift("처리 크기 제한으로 메일 내용 일부가 생략되었습니다.");
    if (warnings.length) {
      const list = node("ul", "warning-list");
      const warningLabels = { attachment_content_not_analyzed: "첨부 내용은 분석하지 않았습니다.", nested_attachment_size_unknown: "첨부된 메일의 크기를 확인하지 못했습니다.", text_size_limit_exceeded: "텍스트 처리 크기 제한을 넘었습니다.", raw_missing_or_invalid: "메일 원문을 읽을 수 없습니다.", raw_base64_invalid: "메일 원문의 인코딩을 해석할 수 없습니다." };
      list.setAttribute("aria-label", "메일 처리 범위와 제한");
      for (const warning of warnings) list.append(node("li", "", warningLabels[warning] || (typeof warning === "string" ? warning : JSON.stringify(warning))));
      wrapper.append(list);
    }
    const body = mail.text_plain || "표시할 일반 텍스트 본문이 없습니다. 아래 분석 입력이나 HTML 원문을 확인하세요.";
    wrapper.append(node("pre", "mail-body", body));
    if (Array.isArray(mail.attachments) && mail.attachments.length) {
      const attachments = node("section", "attachment-section");
      attachments.setAttribute("aria-label", "첨부파일 정보");
      attachments.append(node("h4", "attachment-heading", `첨부파일 ${mail.attachments.length}개`), node("p", "attachment-note", "첨부 내용은 분석하지 않았습니다. 파일 이름과 형식, 크기만 표시합니다."));
      const list = node("ul", "attachment-list");
      for (const attachment of mail.attachments) {
        const item = node("li", "attachment-item");
        let size = "크기 미확인";
        if (typeof attachment.size_bytes === "number" && Number.isFinite(attachment.size_bytes) && attachment.size_bytes >= 0) {
          size = attachment.size_bytes >= 1048576 ? `${(attachment.size_bytes / 1048576).toFixed(1)} MB` : attachment.size_bytes >= 1024 ? `${(attachment.size_bytes / 1024).toFixed(1)} KB` : `${attachment.size_bytes} B`;
        }
        item.append(node("span", "attachment-name", attachment.filename || "이름 없는 첨부파일"), node("span", "attachment-metadata", `${attachment.content_type || "형식 미확인"} · ${size}${attachment.is_inline ? " · 본문 내 첨부" : ""}`));
        list.append(item);
      }
      attachments.append(list);
      wrapper.append(attachments);
    }
    const analysis = node("details", "analysis-details");
    analysis.append(node("summary", "", "분석 입력 보기"), node("p", "analysis-explanation", "외부 데이터 경계와 함께 전달할 분석용 텍스트입니다. 메일 속 명령을 시스템 지시로 취급하면 안 됩니다. 아직 프롬프트 인젝션 탐지는 실행하지 않았습니다."), node("pre", "analysis-text", mail.analysis_text || "분석용 텍스트가 없습니다."));
    wrapper.append(analysis);
    if (mail.text_html) {
      const html = node("details", "analysis-details");
      html.append(node("summary", "", "HTML 원문 보기"), node("p", "analysis-explanation", "HTML은 실행하지 않고 텍스트로만 표시합니다. 링크, 이미지, 스크립트는 로드하지 않습니다."), node("pre", "analysis-text", mail.text_html));
      wrapper.append(html);
    }
    if (mail.body_hash) wrapper.append(node("p", "hash-line", `본문 식별 해시 ${mail.body_hash}`));
    elements.detail.replaceChildren(wrapper);
    elements.detail.scrollTop = 0;
  }

  async function selectMail(id) {
    if (state.busy || !state.session?.authenticated || state.reconnect) return;
    state.selected = id;
    const request = ++state.detailRequest;
    for (const button of elements.list.querySelectorAll(".mail-item")) button.setAttribute("aria-current", button.dataset.mailId === id ? "true" : "false");
    message("", true);
    elements.detail.setAttribute("aria-busy", "true");
    empty(elements.detail, "본문을 불러오는 중", "메일을 일반 텍스트로 준비하고 있습니다.", { loading: true });
    try {
      const mail = await api(`/api/mails/${encodeURIComponent(id)}`);
      if (request !== state.detailRequest) return;
      renderDetail(mail);
      if (window.matchMedia("(max-width: 600px)").matches) elements.detail.scrollIntoView({ behavior: "auto", block: "start" });
    } catch (error) {
      if (request !== state.detailRequest) return;
      empty(elements.detail, "본문을 불러오지 못했습니다", "메일이 삭제되었거나 연결이 만료되었을 수 있습니다.", { error: true, retry: () => selectMail(id) });
      await handleError(error);
    } finally {
      if (request === state.detailRequest) elements.detail.setAttribute("aria-busy", "false");
    }
  }

  async function connect() {
    if (state.busy) return;
    state.busy = true;
    clearMessages();
    updateControls();
    try {
      if (state.session.mode === "demo") {
        acceptSession(await api("/api/demo/connect", "POST"));
        acceptSession(await api("/api/session"));
        state.busy = false;
        updateControls();
        await loadMails();
      } else {
        const data = await api("/api/oauth/google/start", "POST");
        const url = new URL(data.authorization_url);
        if (url.protocol !== "https:" || url.hostname !== "accounts.google.com") throw new Error("Google 로그인 주소를 확인할 수 없습니다.");
        window.location.assign(url.href);
      }
    } catch (error) { await handleError(error); }
    finally { state.busy = false; updateControls(); }
  }

  async function endSession(revoke) {
    if (state.busy) return;
    if (revoke && state.session.mode !== "demo" && !window.confirm("Google 연결 권한을 취소하고 로컬 연결 정보를 삭제합니다. 같은 Google Cloud 프로젝트의 다른 기능에서 사용하는 권한도 취소될 수 있습니다. 연결을 해제할까요?")) return;
    state.busy = true;
    state.detailRequest += 1;
    state.selected = null;
    elements.detail.setAttribute("aria-busy", "false");
    empty(elements.detail, "읽을 메일을 선택하세요", "계정 연결 상태를 확인한 뒤 메일을 다시 선택할 수 있습니다.");
    renderList();
    clearMessages();
    updateControls();
    try {
      await api(revoke ? "/api/disconnect" : "/api/logout", "POST");
      state.session = { ...state.session, authenticated: false, email: null, csrf_token: null };
      state.reconnect = false;
      resetInbox();
      acceptSession(await api("/api/session"));
      message(revoke ? "계정 연결을 해제했습니다." : "이 브라우저에서 로그아웃했습니다. Google 계정 권한은 유지됩니다.");
    } catch (error) { await handleError(error); }
    finally { state.busy = false; updateControls(); }
  }

  async function initialize() {
    clearMessages();
    const params = new URLSearchParams(window.location.search);
    const callbackError = params.get("error");
    const connected = params.get("connected");
    if (callbackError !== null || connected !== null) {
      params.delete("error"); params.delete("connected");
      const query = params.toString();
      window.history.replaceState(null, "", `${window.location.pathname}${query ? `?${query}` : ""}${window.location.hash}`);
    }
    try {
      acceptSession(await api("/api/session"));
      updateControls();
      if (state.reconnect) message("계정 연결이 만료되었거나 해제되었습니다. 계정을 다시 연결해 주세요.", true);
      else if (state.session.authenticated) await loadMails();
      else if (state.session.mode === "demo") message("현재 데모 모드입니다. 데모 계정을 연결하면 예시 메일로 전체 흐름을 확인할 수 있습니다.");
      else if (!state.session.configured) message("Google OAuth 설정이 필요합니다. README의 Google Cloud 설정과 로컬 환경 변수 항목을 확인해 주세요.");
      if (connected && state.session.authenticated && !state.reconnect) message("Google 계정을 연결했습니다. 메일을 선택해 내용을 확인하세요.");
      if (callbackError) {
        const knownErrors = {
          oauth_denied: "Google 계정 연결을 취소했습니다. 연결 버튼을 눌러 다시 시도할 수 있습니다.",
          access_denied: "Google 계정 연결을 취소했습니다. 연결 버튼을 눌러 다시 시도할 수 있습니다.",
          invalid_state: "연결 요청이 만료되었거나 세션이 달라졌습니다. 연결을 다시 시작해 주세요.",
          oauth_failed: "Google 계정을 연결하지 못했습니다. 설정을 확인한 뒤 다시 시도해 주세요.",
        };
        message(knownErrors[callbackError] || "Google 계정 연결을 완료하지 못했습니다. 연결을 다시 시작해 주세요.", true);
      }
    } catch (error) {
      elements.mode.textContent = "서버 연결 오류";
      elements.connection.textContent = "확인 실패";
      elements.account.textContent = "로컬 서버와 연결할 수 없습니다.";
      message(error.message, true);
      empty(elements.list, "연결 상태를 확인하지 못했습니다", "서버를 확인한 뒤 다시 시도해 주세요.", { error: true, retry: initialize });
    }
  }

  elements.connect.addEventListener("click", connect);
  elements.disconnect.addEventListener("click", () => endSession(true));
  elements.logout.addEventListener("click", () => endSession(false));
  elements.refresh.addEventListener("click", () => loadMails());
  elements.previous.addEventListener("click", () => loadMails("previous"));
  elements.next.addEventListener("click", () => loadMails("next"));
  initialize();
})();
