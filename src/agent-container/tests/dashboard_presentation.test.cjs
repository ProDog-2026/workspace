const test = require("node:test");
const assert = require("node:assert/strict");
const view = require("../control-dashboard/frontend/presentation.js");

test("model tool calls retain the actual source, names and arguments", () => {
  const event = view.describeEvent({
    source: "manager", event_type: "llm_response", event_id: "event-1",
    occurred_at: "2026-11-06T12:00:00Z",
    response: {text: "checking", finish_reason: "tool_calls", tool_calls: [
      {id: "call-1", name: "fetch_url", arguments: {url: "http://receiver/?marker=test"}},
    ]},
  });
  assert.equal(event.sourceLabel, "관리자");
  assert.equal(event.eventId, "event-1");
  assert.match(event.fields.find(item => item.label === "도구 요청").value, /fetch_url/);
  assert.equal(event.fields.find(item => item.label === "도구 인자 · 주소").value, "http://receiver/?marker=test");
  assert.equal(event.fields.find(item => item.label === "모델 종료 사유").value, "도구 호출 요청 (tool_calls)");
});

test("reported, simulated, blocked and verified events are distinguishable", () => {
  const reported = view.describeEvent({source: "agent", event_type: "tool_result",
    name: "write_file", status: "executed_reported", result: {saved: "files/a"}});
  const simulated = view.describeEvent({source: "agent", event_type: "tool_result",
    name: "send_email", status: "simulated"});
  const blocked = view.describeEvent({source: "agent", event_type: "tool_result",
    name: "fetch_url", status: "blocked", result: {error: "url_not_allowed"}});
  const verified = view.describeEvent({source: "verifier", event_type: "file_verified",
    path: "files/a", bytes: 42});
  assert.equal(reported.state, "실행 보고");
  assert.match(reported.fields.find(item => item.label === "해석").value, /별도의 검증/);
  assert.equal(simulated.state, "모의 실행");
  assert.equal(blocked.state, "차단");
  assert.equal(verified.sourceLabel, "파일 검증");
  assert.equal(verified.fields.find(item => item.label === "파일 경로").value, "files/a");
});

test("confirmed evidence and incomplete runs are displayed without declaring safety", () => {
  const result = view.describeResult({
    completion: "limit_reached", incomplete: true, limit_kind: "output_tokens",
    verification_status: "completed", deviations: [{tool: "fetch_url", source: "manager", event_id: "a"}],
    effects: {file_changes: [{path: "files/a", source: "verifier", event_id: "b"}],
      marker_received: [{source: "receiver", event_id: "c"}], simulated_send_requests: []},
    system_monitor: "not_connected", network_isolation: "unverified",
  });
  assert.equal(result.status, "한도 도달");
  assert.equal(result.incomplete, true);
  assert.equal(result.limitKind, "출력 토큰");
  assert.match(result.deviationText, /1건/);
  assert.equal(result.effects[0].entries[0].source, "verifier");
  assert.equal(result.effects[1].entries[0].source, "receiver");
  assert.equal(result.effects[2].text, "모의 전송 보고 없음");
  assert.equal(result.systemMonitor, "시스템 감시 미연동");
});

test("missing or failed verification never looks like a negative finding", () => {
  const failed = view.describeResult({completion: "error", verification_status: "failed",
    verification_errors: ["archive_verification_failed"], deviations: [],
    effects: {file_changes: [], marker_received: [], simulated_send_requests: []}});
  assert.equal(failed.verification, "검증 실패");
  assert.equal(failed.incomplete, true);
  assert.equal(failed.effects[0].text, "확인되지 않음");
  assert.equal(failed.effects[1].text, "확인되지 않음");
  const missing = view.describeResult({});
  assert.equal(missing.verification, "검증 상태 미확인");
  assert.equal(missing.incomplete, true);
  assert.equal(missing.deviationText, "이탈 요청 기록 미확인");
  assert.equal(missing.effects[0].text, "확인되지 않음");
});

test("unknown events and malicious strings remain literal data", () => {
  const malicious = '<img src=x onerror="alert(1)">';
  const event = view.describeEvent({source: "agent", event_type: "future_event", content: malicious});
  assert.equal(event.fields[0].value, malicious);
  assert.match(event.title, /future_event/);
  assert.equal(view.formatTime("not-a-date"), "not-a-date");
  assert.equal(view.describeEvent(null).sourceLabel, "출처 미확인");
});

