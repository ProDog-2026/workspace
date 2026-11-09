"use strict";

const $ = id => document.getElementById(id);
const view = window.DashboardPresentation;
const config = () => ({provider: "ollama", base_url: $("base-url").value.trim(), model: $("model").value.trim()});
const runOptions = () => ({temperature: Number($("temperature").value), prompt_id: $("prompt-id").value});
const limits = () => ({max_calls: Number($("max-calls").value), max_tokens: Number($("max-tokens").value), total_seconds: Number($("total-seconds").value)});

let current = null;
let cursor = 0;
let polling = null;
let selection = 0;
let events = [];
let eventNodes = new Map();

async function api(path, options = {}) {
  const response = await fetch(path, {...options, headers: {"Content-Type": "application/json", ...options.headers}});
  let data;
  try { data = await response.json(); }
  catch (_) { throw new Error("서버 응답을 읽을 수 없습니다."); }
  if (!response.ok) throw new Error(data.error ? view.errorLabel(data.error) : "요청 실패");
  return data;
}

function error(value) { $("error").textContent = String(value.message || value); }
function stopPolling() { clearTimeout(polling); polling = null; }
function record(value) { return value && typeof value === "object" && !Array.isArray(value) ? value : {}; }

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = String(text);
  return node;
}

function empty(text) { return element("p", "empty-state", text); }

function rawDetails(title, value) {
  const details = element("details", "raw-details");
  details.appendChild(element("summary", "", title));
  const pre = element("pre", "", view.formatValue(value));
  details.appendChild(pre);
  return details;
}

function appendFields(parent, fields) {
  if (!fields.length) return;
  const list = element("dl", "field-list");
  for (const field of fields) {
    list.appendChild(element("dt", "", field.label));
    list.appendChild(element("dd", "", field.value));
  }
  parent.appendChild(list);
}

function renderEvent(event) {
  const data = view.describeEvent(event);
  const card = element("article", "event-card");
  card.dataset.source = data.source;
  card.tabIndex = -1;
  const top = element("div", "event-top");
  const labels = element("div", "");
  labels.appendChild(element("span", "source-label", data.sourceLabel));
  if (data.state) labels.appendChild(element("span", "state-label", data.state));
  top.appendChild(labels);
  top.appendChild(element("time", "", data.time));
  card.appendChild(top);
  card.appendChild(element("h3", "", data.title));
  appendFields(card, data.fields);
  if (data.eventId) card.appendChild(element("p", "result-item-meta", "이벤트 ID: " + data.eventId));
  card.appendChild(rawDetails("원본 이벤트 보기", event));
  if (data.eventId) eventNodes.set(data.eventId, card);
  return card;
}

function matchesFilter(event) {
  const selected = $("event-filter").value;
  return selected === "all" || record(event).source === selected;
}

function updateEventCount() {
  if (!events.length) {
    $("event-count").textContent = "기록된 이벤트가 없습니다.";
    return;
  }
  const visible = events.filter(matchesFilter).length;
  $("event-count").textContent = "총 " + events.length + "건 중 " + visible + "건 표시";
}

function renderEvents() {
  const target = $("events");
  target.replaceChildren();
  eventNodes = new Map();
  const visible = events.filter(matchesFilter);
  if (!visible.length) target.appendChild(empty(events.length ? "선택한 출처의 이벤트가 없습니다." : "실행을 선택하면 이벤트가 표시됩니다."));
  else for (const event of visible) target.appendChild(renderEvent(event));
  updateEventCount();
}

function appendEvents(incoming) {
  if (!Array.isArray(incoming) || !incoming.length) return;
  const hadVisible = events.some(matchesFilter);
  events.push(...incoming);
  const additions = incoming.filter(matchesFilter);
  if (additions.length && !hadVisible) $("events").replaceChildren();
  for (const event of additions) $("events").appendChild(renderEvent(event));
  updateEventCount();
}

function showEvidence(eventId) {
  const found = events.find(item => String(record(item).event_id || "") === eventId);
  if (!found) {
    $("error").textContent = "연결된 근거 이벤트를 찾을 수 없습니다.";
    return;
  }
  if (!matchesFilter(found)) {
    $("event-filter").value = "all";
    renderEvents();
  }
  const card = eventNodes.get(eventId);
  if (card) {
    card.scrollIntoView({behavior: "smooth", block: "center"});
    card.focus({preventScroll: true});
    card.classList.add("evidence-highlight");
    setTimeout(() => card.classList.remove("evidence-highlight"), 2500);
  }
}

function appendEvidence(parent, item) {
  const evidence = record(item);
  const source = view.sourceLabel(evidence.source);
  const id = evidence.event_id ? String(evidence.event_id) : "";
  parent.appendChild(element("p", "result-item-meta", "출처: " + source + (id ? " · 이벤트 ID: " + id : "")));
  if (id) {
    const link = element("button", "evidence-link", "근거 로그 보기");
    link.type = "button";
    link.addEventListener("click", () => showEvidence(id));
    parent.appendChild(link);
  }
}

function metric(title, value, note) {
  const card = element("div", "result-metric");
  card.appendChild(element("h3", "", title));
  card.appendChild(element("p", "", value));
  if (note) card.appendChild(element("p", "metric-note", note));
  return card;
}

function resultSection(title) {
  const section = element("div", "result-section");
  section.appendChild(element("h3", "", title));
  return section;
}

function renderResult(result) {
  const target = $("result");
  target.replaceChildren();
  if (!result || typeof result !== "object") {
    $("summary").textContent = "실행이 끝나면 요약이 표시됩니다.";
    target.appendChild(empty("표시할 분석 결과가 없습니다."));
    return;
  }
  const data = view.describeResult(result);
  $("summary").textContent = typeof result.summary === "string" && result.summary
    ? result.summary : "저장된 요약이 없거나 비어 있습니다.";
  const overview = element("div", "result-overview");
  overview.appendChild(metric("실행 상태", data.status,
    data.limitKind ? "한도: " + data.limitKind : data.incomplete ? "분석이 완료되지 않았습니다." : ""));
  overview.appendChild(metric("도구 요청 판정", data.deviationText,
    data.incomplete ? "미완료 실행의 기록을 표시합니다." : "수집된 도구 요청 기준"));
  overview.appendChild(metric("증거 검증", data.verification,
    result.runtime_mode === "host_process_no_docker" ? "호스트 프로세스 실행" : ""));
  target.appendChild(overview);

  const info = resultSection("실행 정보");
  const connection = record(result.connection);
  const runLimits = record(result.limits);
  const information = [];
  if (connection.model) information.push({label: "모델", value: view.formatValue(connection.model)});
  if (connection.provider) information.push({label: "연결 방식", value: view.formatValue(connection.provider)});
  if (result.created_at) information.push({label: "시작 시각", value: view.formatTime(result.created_at)});
  if (result.elapsed_seconds !== undefined && result.elapsed_seconds !== null) information.push({label: "소요 시간", value: view.formatValue(result.elapsed_seconds) + "초"});
  if (result.calls !== undefined && result.calls !== null) information.push({label: "LLM 요청", value: view.formatValue(result.calls) + "회"});
  if (runLimits.max_calls !== undefined) information.push({label: "LLM 호출 한도", value: view.formatValue(runLimits.max_calls) + "회"});
  if (runLimits.max_tokens !== undefined) information.push({label: "응답 토큰 한도", value: view.formatValue(runLimits.max_tokens)});
  if (result.temperature !== undefined) information.push({label: "temperature", value: view.formatValue(result.temperature)});
  if (result.prompt_version) information.push({label: "프롬프트 버전", value: view.formatValue(result.prompt_version)});
  if (information.length) appendFields(info, information);
  else info.appendChild(empty("기록된 실행 정보가 없습니다."));
  target.appendChild(info);

  if (data.verificationErrors.length || data.cleanupError) {
    const messages = data.verificationErrors.map(message => "검증 오류: " + message);
    if (data.cleanupError) messages.push("정리 오류: " + data.cleanupError);
    target.appendChild(element("div", "verification-notice", messages.join("\n")));
  }

  const deviations = resultSection("작업과 무관한 도구 요청");
  if (data.deviations === null) deviations.appendChild(empty("도구 요청 판정 기록이 없습니다."));
  else if (!data.deviations.length) deviations.appendChild(empty("기록된 이탈 요청이 없습니다."));
  else {
    const list = element("ul", "result-list");
    for (const raw of data.deviations) {
      const item = record(raw);
      const li = element("li", "result-item");
      li.appendChild(element("p", "result-item-title", view.toolLabel(item.tool)));
      const args = record(item.arguments);
      appendFields(li, Object.keys(args).map(key => ({label: "인자 · " + key, value: view.formatValue(args[key])})));
      if (item.arguments !== undefined && !Object.keys(args).length && (typeof item.arguments !== "object" || Array.isArray(item.arguments))) {
        appendFields(li, [{label: "인자", value: view.formatValue(item.arguments)}]);
      }
      appendEvidence(li, item);
      list.appendChild(li);
    }
    deviations.appendChild(list);
  }
  target.appendChild(deviations);

  const effects = resultSection("확인된 처리와 효과");
  const grid = element("div", "effect-grid");
  for (const effect of data.effects) {
    const card = element("div", "effect-card");
    card.appendChild(element("h4", "", effect.title));
    card.appendChild(element("p", "", effect.text));
    for (const raw of effect.entries) {
      const item = record(raw);
      if (item.path) card.appendChild(element("p", "result-item-meta", "파일: " + view.formatValue(item.path)));
      appendEvidence(card, item);
    }
    if (effect.key === "simulated_send_requests") {
      card.appendChild(element("p", "result-item-meta", "메일 전송 도구는 모의 실행으로 기록됩니다."));
    }
    grid.appendChild(card);
  }
  effects.appendChild(grid);
  effects.appendChild(element("p", "evidence-caption",
    data.systemMonitor + " · " + data.networkIsolation + ". 표시 내용은 수집된 기록을 기준으로 합니다."));
  target.appendChild(effects);
  target.appendChild(rawDetails("원본 결과 보기", result));
}

async function history() {
  const runs = await api("/runs");
  const target = $("history");
  target.replaceChildren();
  if (!Array.isArray(runs) || !runs.length) {
    target.appendChild(empty("이전 실행이 없습니다."));
    return;
  }
  for (const run of runs) {
    const item = record(run);
    const button = element("button", "", (item.subject || "제목 없음") + " · " +
      view.statusLabel(item.status) + " · " + view.formatTime(item.created_at));
    button.type = "button";
    button.addEventListener("click", () => select(item.run_id).catch(error));
    target.appendChild(button);
  }
}

async function select(id) {
  stopPolling();
  current = id;
  cursor = 0;
  selection += 1;
  events = [];
  $("event-filter").value = "all";
  $("error").textContent = "";
  renderEvents();
  renderResult(null);
  if (window.DashboardFlow) window.DashboardFlow.clear("흐름을 불러오는 중…");
  flowFresh = true;
  await poll(id, selection);
}

let flowFresh = false;
async function refreshFlow(id, selected) {
  if (!window.DashboardFlow) return;
  try {
    const data = await api("/runs/" + encodeURIComponent(id) + "/flow");
    if (id !== current || selected !== selection) return;
    window.DashboardFlow.update(data, {reset: flowFresh});
    flowFresh = false;
  } catch (value) {
    if (id !== current || selected !== selection) return;
    window.DashboardFlow.clear("흐름을 계산하지 못했습니다: " + String(value.message || value));
  }
}

async function poll(id, selected) {
  if (id !== current || selected !== selection) return;
  try {
    const [run, log] = await Promise.all([
      api("/runs/" + encodeURIComponent(id)),
      api("/runs/" + encodeURIComponent(id) + "/events?after=" + cursor),
    ]);
    if (id !== current || selected !== selection) return;
    const elapsed = Math.max(0, Math.floor(Number(run.elapsed_seconds) || 0));
    $("status").textContent = id + " · " + view.statusLabel(run.status) + " / " +
      view.statusLabel(run.phase) + " · " + elapsed + "초 · LLM " + (run.calls ?? 0) + "회";
    appendEvents(log.events);
    if (Number.isInteger(log.next) && log.next >= 0) cursor = log.next;
    const done = ["finished", "storage_error"].includes(run.phase);
    await refreshFlow(id, selected);
    $("stop").disabled = done || run.status !== "running";
    $("start").disabled = !done;
    if (done) {
      if (run.phase === "finished") {
        const result = await api("/runs/" + encodeURIComponent(id) + "/result");
        if (id !== current || selected !== selection) return;
        renderResult(result);
      } else {
        $("error").textContent = "증거 저장에 실패했습니다. 컨테이너를 보존했습니다.";
      }
      await history();
    } else {
      polling = setTimeout(() => poll(id, selected), 1500);
    }
  } catch (value) {
    if (id !== current || selected !== selection) return;
    error(value);
    $("start").disabled = false;
    stopPolling();
  }
}

$("event-filter").addEventListener("change", renderEvents);
$("check").addEventListener("click", async () => {
  $("check").disabled = true;
  $("connection").textContent = " 연결 확인 중…";
  $("error").textContent = "";
  try {
    const result = await api("/connections/check", {method: "POST",
      body: JSON.stringify({config: config(), max_tokens: limits().max_tokens})});
    const connectionStatuses = {
      ready: "연결 및 도구 호출 확인 완료", limit_reached: "출력 한도에 도달했습니다.",
      tool_call_not_verified: "도구 호출을 확인하지 못했습니다.",
      tool_roundtrip_not_verified: "도구 결과 전달 후 최종 응답을 확인하지 못했습니다.",
    };
    $("connection").textContent = " " + (connectionStatuses[result.status] || String(result.status || "확인 결과 없음"));
  } catch (value) { error(value); }
  finally { $("check").disabled = false; }
});

$("start").addEventListener("click", async () => {
  $("error").textContent = "";
  $("start").disabled = true;
  try {
    const run = await api("/runs", {method: "POST", body: JSON.stringify({
      config: config(), limits: limits(), ...runOptions(), email: {subject: $("subject").value, body: $("body").value},
    })});
    await select(run.run_id);
  } catch (value) { error(value); $("start").disabled = false; }
});

$("stop").addEventListener("click", async () => {
  if (!current) return;
  try {
    await api("/runs/" + encodeURIComponent(current) + "/stop", {method: "POST"});
    $("stop").disabled = true;
  } catch (value) { error(value); }
});

let promptList = [];
async function loadPrompts(keep) {
  const data = await api("/prompts");
  promptList = data.prompts;
  const select = $("prompt-id");
  select.replaceChildren();
  for (const item of promptList) {
    const option = element("option", "", item.id + " — " + item.description);
    option.value = item.id;
    select.appendChild(option);
  }
  select.value = promptList.some(item => item.id === keep) ? keep : data.default;
  showPromptDescription();
}
function selectedPrompt() { return promptList.find(item => item.id === $("prompt-id").value); }
function showPromptDescription() {
  const found = selectedPrompt();
  $("prompt-desc").textContent = found ? found.description : "";
  if (!found) return;
  $("prompt-name").value = found.id;
  $("prompt-description").value = found.description;
  $("prompt-text").value = found.text;
  $("prompt-save").disabled = $("prompt-delete").disabled = found.builtin;
  $("prompt-note").textContent = found.builtin
    ? "기본 프롬프트는 수정할 수 없습니다. 내용을 고친 뒤 새 ID로 '새 프롬프트로 저장'하세요."
    : "직접 만든 프롬프트입니다. 수정하면 이후 실행부터 반영됩니다(이전 결과에는 당시 내용이 남습니다).";
}
async function savePrompt(method, id) {
  $("error").textContent = "";
  const body = {description: $("prompt-description").value.trim(), text: $("prompt-text").value};
  if (method === "POST") body.id = $("prompt-name").value.trim();
  try {
    const saved = await api("/prompts" + (method === "POST" ? "" : "/" + encodeURIComponent(id)),
      {method, body: JSON.stringify(body)});
    await loadPrompts(saved.id);
  } catch (value) { error(value); }
}
async function loadModels() {
  const select = $("model");
  const previous = select.value;
  try {
    const data = await api("/ollama/models?base_url=" + encodeURIComponent($("base-url").value.trim()));
    select.replaceChildren();
    for (const name of data.models) {
      const option = element("option", "", name);
      option.value = name;
      select.appendChild(option);
    }
    if (!data.models.length) select.appendChild(element("option", "", "설치된 모델이 없습니다"));
    else if (data.models.includes(previous)) select.value = previous;
    if (!data.models.length) $("start").disabled = true;
  } catch (value) {
    select.replaceChildren(element("option", "", "모델 목록을 불러오지 못했습니다"));
    $("start").disabled = true;
    error(value);
  }
}
$("prompt-id").addEventListener("change", showPromptDescription);
$("prompt-new").addEventListener("click", () => savePrompt("POST"));
$("prompt-save").addEventListener("click", () => {
  const found = selectedPrompt();
  if (found) savePrompt("PUT", found.id);
});
$("prompt-delete").addEventListener("click", async () => {
  const found = selectedPrompt();
  if (!found || !window.confirm(found.id + " 프롬프트를 삭제할까요?")) return;
  try { await api("/prompts/" + encodeURIComponent(found.id), {method: "DELETE"}); await loadPrompts(); }
  catch (value) { error(value); }
});
$("reload-models").addEventListener("click", () => { $("error").textContent = ""; loadModels(); });
$("base-url").addEventListener("change", loadModels);
loadPrompts().catch(error);
loadModels();

$("refresh").addEventListener("click", () => history().catch(error));
history().catch(error);
