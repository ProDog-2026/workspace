(function (root, factory) {
  const presentation = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = presentation;
  if (root) root.DashboardPresentation = presentation;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const STATUS = {
    running: "실행 중", executing: "실행 중", verifying: "결과 검증 중",
    finished: "결과 보관 완료", completed: "완료", stopped: "중지됨",
    limit_reached: "한도 도달", timeout: "시간 초과", error: "오류",
    storage_error: "결과 저장 실패", failed: "실패", blocked: "차단",
    simulated: "모의 실행", executed_reported: "실행 보고",
    ok: "정상 응답", manual: "대기",
  };
  const SOURCES = {
    manager: "관리자", agent: "에이전트 보고",
    receiver: "수신 서버", verifier: "파일 검증", sensor: "시스템 감시",
  };
  const TOOLS = {
    read_email: "메일 읽기", save_summary: "요약 저장", send_email: "메일 전송 모의",
    write_file: "테스트 파일 쓰기", fetch_url: "URL 요청",
  };
  const LIMITS = {
    total_time: "전체 실행 시간", llm_calls: "LLM 호출 횟수",
    output_tokens: "출력 토큰", tool_calls: "도구 호출 횟수",
  };
  const FINISH_REASONS = {stop: "정상 종료", tool_calls: "도구 호출 요청", length: "출력 길이 한도"};
  const ERRORS = {
    invalid_prompt: "프롬프트 내용은 비어 있지 않고 4000자 이하여야 합니다",
    invalid_prompt_id: "프롬프트 ID는 영문·숫자·_ . - 만 쓸 수 있고 40자 이하여야 합니다",
    prompt_exists: "같은 ID의 프롬프트가 이미 있습니다",
    builtin_prompt_read_only: "기본 프롬프트는 수정하거나 삭제할 수 없습니다. 새 프롬프트로 저장하세요",
    prompt_not_found: "선택한 프롬프트를 찾지 못했습니다",
    invalid_base_url: "Ollama 주소 형식이 올바르지 않습니다",
    connection_failed: "Ollama 서버에 연결하지 못했습니다",
    authentication_failed: "인증에 실패했습니다",
    model_or_endpoint_not_found: "모델 또는 요청 주소를 찾지 못했습니다",
    provider_timeout: "모델 서버 응답 시간이 초과됐습니다",
    context_limit: "메일과 대화가 로컬 입력 한도 16,384토큰을 초과했습니다. 메모리 과부하를 막기 위해 원문을 자르지 않고 검사를 중단했습니다",
    long_input_timeout: "긴 입력 처리 중 모델 응답 시간이 초과됐습니다. 호스트 메모리·모델 속도를 확인하세요. 검사는 미완료입니다",
    docker_response_timeout: "Docker 응답 지연으로 검사를 중단했습니다. 남은 컨테이너의 증거 복구가 필요합니다",
    runtime_interrupted: "컨테이너 실행 중단으로 모델 요청을 취소했습니다",
    limit_reached: "설정된 실행 한도에 도달했습니다",
    docker_execution_or_verification_failed: "컨테이너 실행 또는 증거 검증에 실패했습니다",
    archive_verification_failed: "작업 파일을 확인하지 못했습니다",
    container_cleanup_failed: "작업 컨테이너를 정리하지 못했습니다",
    environment_not_ready: "실행 환경 점검을 통과하지 못해 검사를 보류했습니다",
    controller_interrupted: "제어 서버 중단으로 남은 컨테이너에서 증거를 복구했습니다. 실행 결과는 미확정입니다",
    execution_outcome_unknown: "접수 후 중단돼 실행 여부를 확인하지 못했습니다. 자동 재실행하지 않습니다",
    unsupported_file_type_or_size: "지원하지 않는 파일 종류이거나 크기 한도를 초과했습니다",
    unsafe_path: "안전하지 않은 파일 경로가 발견됐습니다",
    tool_io_failed: "도구 처리 중 파일 또는 통신 오류가 발생했습니다",
    path_or_url_not_allowed: "허용되지 않은 파일 경로 또는 URL입니다",
    unknown_tool_or_arguments: "알 수 없는 도구 또는 도구 인자입니다",
    tool_limit: "도구 호출 한도에 도달했습니다",
  };
  const FIELD_LABELS = {
    subject: "제목", body: "본문", content: "내용", summary: "요약", recipient: "받는 사람",
    path: "경로", url: "주소", saved: "저장 경로", error: "오류", http_status: "HTTP 상태",
    simulated: "모의 실행", delivered: "실제 전달", bytes: "크기(바이트)", sha256: "SHA-256",
    query: "쿼리", received_at: "수신 시각", name: "이름", arguments: "인자",
    prompt_tokens: "입력 토큰", completion_tokens: "출력 토큰", total_tokens: "전체 토큰",
    context_tokens: "확장된 입력창(토큰)",
  };
  const has = (object, key) => Object.prototype.hasOwnProperty.call(object, key);
  const obj = value => value && typeof value === "object" && !Array.isArray(value) ? value : {};
  const array = value => Array.isArray(value) ? value : [];
  const present = value => value !== undefined && value !== null;
  const label = (map, key, fallback) =>
    typeof key === "string" && has(map, key) ? map[key] : (present(key) && key !== "" ? String(key) : fallback);

  function formatValue(value) {
    if (!present(value)) return "정보 없음";
    if (typeof value === "string") return value || "(빈 값)";
    if (typeof value === "boolean") return value ? "예" : "아니요";
    if (typeof value === "number") return String(value);
    try { return JSON.stringify(value, null, 2) || "정보 없음"; }
    catch (_) { return "표시할 수 없는 값"; }
  }

  function formatTime(value) {
    if (!present(value) || value === "") return "시각 정보 없음";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return new Intl.DateTimeFormat("ko-KR", {
      year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit",
      hour12: false,
    }).format(date);
  }

  function statusLabel(value) { return label(STATUS, value, "정보 없음"); }
  function sourceLabel(value) { return label(SOURCES, value, "출처 미확인"); }
  function errorLabel(value) {
    return typeof value === "string" && has(ERRORS, value) ? ERRORS[value] + " (" + value + ")" : formatValue(value);
  }
  function toolLabel(value) {
    const name = present(value) && value !== "" ? String(value) : "이름 없음";
    return has(TOOLS, name) ? TOOLS[name] + " (" + name + ")" : name;
  }
  function registerToolLabels(catalog) {
    for (const item of catalog) TOOLS[item.name] = item.label;
  }
  function limitLabel(value) { return label(LIMITS, value, "정보 없음"); }

  function fieldsFor(value, prefix) {
    const data = obj(value);
    const keys = Object.keys(data);
    if (!keys.length) return present(value) && (typeof value !== "object" || Array.isArray(value))
      ? [{label: prefix, value: formatValue(value)}] : [];
    return keys.map(key => ({
      label: (prefix ? prefix + " · " : "") + label(FIELD_LABELS, key, key),
      value: key === "error" ? errorLabel(data[key]) : formatValue(data[key]),
    }));
  }

  function describeEvent(input) {
    const event = obj(input);
    const response = obj(event.response);
    const fields = [];
    let title;
    let state = "";

    switch (event.event_type) {
      case "llm_request":
        title = "LLM 요청" + (present(event.call_number) ? " #" + event.call_number : "");
        break;
      case "llm_response": {
        title = event.source === "agent" ? "에이전트가 받은 모델 응답" : "모델 응답";
        state = response.status === "limit_reached" ? "한도 도달" : "";
        if (present(response.text) && response.text !== "") fields.push({label: "응답 내용", value: formatValue(response.text)});
        const calls = array(response.tool_calls);
        if (calls.length) {
          calls.forEach((call, index) => {
            const item = obj(call);
            fields.push({label: "도구 요청" + (calls.length > 1 ? " " + (index + 1) : ""), value: toolLabel(item.name)});
            fields.push(...fieldsFor(item.arguments, "도구 인자"));
            if (present(item.id)) fields.push({label: "도구 호출 ID", value: formatValue(item.id)});
          });
        } else {
          fields.push({label: "도구 요청", value: "이 응답에 기록된 도구 요청 없음"});
        }
        if (present(response.finish_reason)) {
          const reason = String(response.finish_reason);
          fields.push({label: "모델 종료 사유", value: has(FINISH_REASONS, reason)
            ? FINISH_REASONS[reason] + " (" + reason + ")" : reason});
        }
        fields.push(...fieldsFor(response.usage, "사용량"));
        if (present(response.error)) fields.push({label: "오류", value: errorLabel(response.error)});
        break;
      }
      case "tool_requested":
        title = "도구 호출 요청 · " + toolLabel(event.name);
        state = "요청";
        fields.push(...fieldsFor(event.arguments, "인자"));
        if (present(event.tool_call_id)) fields.push({label: "도구 호출 ID", value: formatValue(event.tool_call_id)});
        break;
      case "tool_result":
        title = "도구 처리 결과 · " + toolLabel(event.name);
        state = statusLabel(event.status);
        fields.push({label: "처리 상태", value: state});
        if (event.status === "executed_reported") fields.push({label: "해석", value: "에이전트가 실행을 보고했습니다. 실제 효과는 별도의 검증 기록을 확인하세요."});
        if (event.status === "simulated") fields.push({label: "해석", value: "모의 실행 기록입니다."});
        fields.push(...fieldsFor(event.result, "결과"));
        if (present(event.tool_call_id)) fields.push({label: "도구 호출 ID", value: formatValue(event.tool_call_id)});
        break;
      case "final":
        title = "에이전트 작업 종료";
        state = statusLabel(event.completion);
        fields.push({label: "종료 상태", value: state});
        if (present(event.limit_kind)) fields.push({label: "한도 항목", value: limitLabel(event.limit_kind)});
        if (present(event.error)) fields.push({label: "오류", value: errorLabel(event.error)});
        break;
      case "file_verified":
        title = "테스트 파일 확인";
        if (present(event.path)) fields.push({label: "파일 경로", value: formatValue(event.path)});
        if (present(event.bytes)) fields.push({label: "크기(바이트)", value: formatValue(event.bytes)});
        if (present(event.sha256)) fields.push({label: "SHA-256", value: formatValue(event.sha256)});
        break;
      case "request_received":
        title = "테스트 수신 서버 요청 확인";
        ["path", "query", "body"].forEach(key => {
          if (present(event[key]) && event[key] !== "") fields.push({label: label(FIELD_LABELS, key, key), value: formatValue(event[key])});
        });
        break;
      case "runtime_error":
        title = "실행 또는 검증 오류";
        state = "오류";
        if (present(event.error)) fields.push({label: "오류", value: errorLabel(event.error)});
        break;
      case "cleanup_failed":
        title = "컨테이너 정리 실패";
        state = "오류";
        if (present(event.error)) fields.push({label: "오류", value: errorLabel(event.error)});
        break;
      default: {
        title = "기타 이벤트" + (event.event_type ? " · " + String(event.event_type) : "");
        const extra = {};
        Object.keys(event).forEach(key => {
          if (!["event_id", "run_id", "source", "event_type", "occurred_at"].includes(key)) extra[key] = event[key];
        });
        fields.push(...fieldsFor(extra, ""));
      }
    }
    return {
      title, state, source: present(event.source) ? String(event.source) : "",
      sourceLabel: sourceLabel(event.source), time: formatTime(event.source === "receiver" && event.received_at ? event.received_at : event.occurred_at || event.received_at),
      eventId: present(event.event_id) ? String(event.event_id) : "", fields,
    };
  }

  function describeResult(input) {
    const result = obj(input);
    const effects = obj(result.effects);
    const errors = array(result.verification_errors);
    const verificationFailed = result.verification_status === "failed" || errors.length > 0;
    const verificationComplete = result.verification_status === "completed" && !verificationFailed;
    const status = result.completion || result.status;
    const incomplete = result.incomplete === true || status !== "completed";
    const deviations = Array.isArray(result.deviations) ? result.deviations : null;
    const effectSpecs = [
      ["file_changes", "테스트 파일", "검증된 테스트 파일", "검증된 테스트 파일 없음"],
      ["marker_received", "수신 표식", "수신된 테스트 표식", "기록된 표식 수신 없음"],
      ["simulated_send_requests", "모의 메일 전송", "모의 실행 보고", "모의 전송 보고 없음"],
    ];
    const effectsView = effectSpecs.map(([key, title, found, empty]) => {
      const list = Array.isArray(effects[key]) ? effects[key] : null;
      let text;
      if (list && list.length) text = found + " " + list.length + "건";
      else if (!list || (key !== "simulated_send_requests" && !verificationComplete)) text = "확인되지 않음";
      else text = empty;
      return {key, title, text, entries: list || []};
    });
    return {
      status: statusLabel(status),
      incomplete,
      limitKind: present(result.limit_kind) ? limitLabel(result.limit_kind) : "",
      verification: verificationFailed ? "검증 실패" : verificationComplete ? "검증 완료" : "검증 상태 미확인",
      verificationErrors: errors.map(errorLabel),
      cleanupError: present(result.cleanup_error) ? errorLabel(result.cleanup_error) : "",
      deviations,
      deviationText: deviations === null ? "이탈 요청 기록 미확인"
        : deviations.length ? "작업과 무관한 도구 요청 " + deviations.length + "건"
        : "기록된 이탈 요청 없음",
      effects: effectsView,
      systemMonitor: result.system_monitor === "not_connected" ? "시스템 감시 미연동"
        : present(result.system_monitor) ? String(result.system_monitor) : "시스템 감시 상태 미확인",
      networkIsolation: result.network_isolation === "unverified" ? "네트워크 격리 미확인"
        : present(result.network_isolation) ? String(result.network_isolation) : "네트워크 격리 상태 미확인",
    };
  }

  return {formatValue, formatTime, statusLabel, sourceLabel, errorLabel, toolLabel, registerToolLabels, limitLabel, describeEvent, describeResult};
});

