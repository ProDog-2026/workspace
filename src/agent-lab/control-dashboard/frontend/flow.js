/* LangGraph 흐름 패널: /runs/{id}/flow 의 노드·엣지·단계별 State 를 SVG와 목록으로 그린다.
   비신뢰 값은 모두 textContent / createTextNode 로만 넣는다. */
(function (root) {
  "use strict";

  const SVG = "http://www.w3.org/2000/svg";
  const COL = 234, ROW = 126, NW = 150, NH = 56, TW = 76, TH = 30, PAD = 28;
  const STATE_LABELS = {
    run_id: "실행 ID", waiting: "대기 중", request: "실행 요청", container_id: "작업 컨테이너",
    llm_calls: "LLM 호출", last_tool_calls: "마지막 응답의 도구 요청", tools: "도구 실행 결과",
    tool_blocked_limit: "도구 한도 차단", final: "에이전트 종료", evidence: "수집된 증거",
    evaluation: "판정", phase: "실행 단계", experiment: "실험 기록", anomalies: "불일치",
  };

  let flow = null;
  let selected = null;      // 사용자가 고른 단계 번호 (null 이면 최신 단계를 따라감)

  const $ = id => document.getElementById(id);
  const view = () => root.DashboardPresentation;

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = String(text);
    return node;
  }

  function svg(tag, attrs, text) {
    const node = document.createElementNS(SVG, tag);
    for (const [k, v] of Object.entries(attrs || {})) node.setAttribute(k, String(v));
    if (text !== undefined) node.appendChild(document.createTextNode(String(text)));
    return node;
  }

  function format(value) {
    const v = view();
    return v ? v.formatValue(value) : JSON.stringify(value, null, 2);
  }

  // ── 배치 ────────────────────────────────────────────────
  function positions(nodes) {
    const maxRow = Math.max(0, ...nodes.filter(n => Number.isInteger(n.row)).map(n => n.row));
    let spare = 0;
    const pos = new Map();
    for (const n of nodes) {
      const col = Number.isInteger(n.col) ? n.col : spare++;
      const row = Number.isInteger(n.row) ? n.row : maxRow + 1;
      pos.set(n.id, {x: PAD + col * COL + NW / 2, y: PAD + row * ROW + NH / 2,
                     w: n.terminal ? TW : NW, h: n.terminal ? TH : NH});
    }
    return pos;
  }

  function clip(from, to, box) {
    const dx = to.x - from.x, dy = to.y - from.y;
    if (!dx && !dy) return {x: from.x, y: from.y};
    const t = Math.min(dx ? (box.w / 2) / Math.abs(dx) : Infinity, dy ? (box.h / 2) / Math.abs(dy) : Infinity);
    return {x: from.x + dx * t, y: from.y + dy * t};
  }

  function edgeGeometry(edge, pos, reversed) {
    const a = pos.get(edge.source), b = pos.get(edge.target);
    const mid = {x: (a.x + b.x) / 2, y: (a.y + b.y) / 2};
    const dx = b.x - a.x, dy = b.y - a.y, len = Math.hypot(dx, dy) || 1;
    let ctrl = mid;
    const sameRow = Math.abs(dy) < 1, span = Math.round(Math.abs(dx) / COL);
    if (reversed) ctrl = {x: mid.x - dy / len * 30, y: mid.y + dx / len * 30};
    else if (sameRow && span > 1) ctrl = {x: mid.x, y: mid.y + (dx < 0 ? -1 : 1) * 92};
    const start = clip(a, ctrl === mid ? b : ctrl, a), end = clip(b, ctrl === mid ? a : ctrl, b);
    const d = ctrl === mid ? `M${start.x},${start.y} L${end.x},${end.y}`
      : `M${start.x},${start.y} Q${ctrl.x},${ctrl.y} ${end.x},${end.y}`;
    // 라벨 기준점: 선(곡선)의 중간. 실제 위치는 placeLabels 가 빈자리를 찾아 정한다.
    const at = ctrl === mid ? {x: (start.x + end.x) / 2, y: (start.y + end.y) / 2}
      : {x: 0.25 * start.x + 0.5 * ctrl.x + 0.25 * end.x, y: 0.25 * start.y + 0.5 * ctrl.y + 0.25 * end.y};
    return {d, at, end, tx: dx / len, ty: dy / len, nx: -dy / len, ny: dx / len};
  }

  const overlaps = (a, b) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;

  /* 라벨을 하나씩 실제 크기로 재고, 노드·화살표 끝·먼저 놓인 라벨과 겹치지 않는 가장 가까운 자리에 둔다.
     자리마다 배경 상자를 깔아 지나가는 선 위에서도 읽히게 한다. */
  function placeLabels(layer, labels, obstacles, bounds) {
    const offsets = [];
    for (const d of [0, 12, -12, 24, -24, 36, -36, 50, -50, 64, -64])
      for (const s of [0, 18, -18, 36, -36]) offsets.push([d, s]);
    offsets.sort((p, q) => Math.hypot(...p) - Math.hypot(...q));
    for (const label of labels) {
      const text = svg("text", {x: label.at.x, y: label.at.y, "text-anchor": "middle", class: label.className}, label.text);
      layer.appendChild(text);
      let box;
      try { box = text.getBBox(); } catch (_) { box = null; }
      const w = (box && box.width) || label.text.length * 7, h = (box && box.height) || 14;
      const rise = box && box.height ? label.at.y - box.y : 11;     // 기준선에서 글자 위쪽까지
      const rectAt = p => ({x: p.x - w / 2 - 4, y: p.y - rise - 2, w: w + 8, h: h + 4});
      let spot = label.at;
      for (const [d, s] of offsets) {
        const p = {x: label.at.x + label.nx * d + label.tx * s, y: label.at.y + label.ny * d + label.ty * s};
        const r = rectAt(p);
        if (r.x < 0 || r.y < 0 || r.x + r.w > bounds.w || r.y + r.h > bounds.h) continue;
        if (!obstacles.some(o => overlaps(r, o))) { spot = p; break; }
      }
      text.setAttribute("x", spot.x);
      text.setAttribute("y", spot.y);
      const r = rectAt(spot);
      obstacles.push(r);
      layer.insertBefore(svg("rect", {x: r.x, y: r.y, width: r.w, height: r.h, rx: 5, class: "flow-label-bg"}), text);
    }
  }

  // ── 그래프 ──────────────────────────────────────────────
  function lastStepOf(nodeId) {
    const steps = flow.steps.filter(s => s.node === nodeId);
    return steps.length ? steps[steps.length - 1].index : null;
  }

  function drawGraph() {
    const target = $("flow-graph");
    target.replaceChildren();
    const pos = positions(flow.nodes);
    const width = Math.max(...[...pos.values()].map(p => p.x + p.w / 2)) + PAD;
    const height = Math.max(...[...pos.values()].map(p => p.y + p.h / 2)) + PAD;
    const canvas = svg("svg", {viewBox: `0 0 ${width} ${height}`, style: `max-width:${width}px`, role: "img",
      "aria-label": "LangGraph 데이터 흐름 그래프"});
    const defs = svg("defs");
    for (const [id, color] of [["flow-arrow-on", "#6cc7b9"], ["flow-arrow-off", "#3d4d63"]]) {
      const marker = svg("marker", {id, viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: "auto-start-reverse"});
      marker.appendChild(svg("path", {d: "M0,0 L10,5 L0,10 z", fill: color}));
      defs.appendChild(marker);
    }
    canvas.appendChild(defs);

    const pairs = new Set(flow.edges.map(e => e.source + ">" + e.target));
    const step = flow.steps.find(s => s.index === currentIndex());
    const edgeLayer = svg("g"), labelLayer = svg("g"), nodeLayer = svg("g");
    const labels = [];
    const obstacles = [...pos.values()].map(p => ({x: p.x - p.w / 2 - 5, y: p.y - p.h / 2 - 5, w: p.w + 10, h: p.h + 10}));
    for (const edge of flow.edges) {
      if (!pos.has(edge.source) || !pos.has(edge.target)) continue;
      const geo = edgeGeometry(edge, pos, pairs.has(edge.target + ">" + edge.source));
      const on = edge.count > 0;
      edgeLayer.appendChild(svg("path", {d: geo.d, class: "flow-edge" + (on ? " on" : "") + (edge.conditional ? " conditional" : ""),
        "marker-end": `url(#${on ? "flow-arrow-on" : "flow-arrow-off"})`}));
      obstacles.push({x: geo.end.x - 8, y: geo.end.y - 8, w: 16, h: 16});   // 화살표 끝
      const text = [edge.label, edge.count > 1 ? "×" + edge.count : ""].filter(Boolean).join(" ");
      if (text) labels.push({...geo, text, className: "flow-edge-label" + (on ? " on" : "")});
    }
    // 긴 라벨일수록 들어갈 자리가 적으므로 먼저 자리를 잡는다.
    labels.sort((a, b) => b.text.length - a.text.length);
    for (const node of flow.nodes) {
      const p = pos.get(node.id);
      const classes = ["flow-node"];
      if (node.terminal) classes.push("terminal");
      if (node.visits > 0) classes.push("visited");
      if (flow.current === node.id) classes.push("current");
      if (step && step.node === node.id) classes.push("selected");
      const g = svg("g", {class: classes.join(" "), transform: `translate(${p.x - p.w / 2},${p.y - p.h / 2})`});
      g.appendChild(svg("rect", {width: p.w, height: p.h, rx: node.terminal ? p.h / 2 : 9}));
      if (node.terminal) {
        g.appendChild(svg("text", {x: p.w / 2, y: p.h / 2 + 4, class: "flow-node-title"}, node.id === "__start__" ? "START" : "END"));
      } else {
        g.appendChild(svg("text", {x: p.w / 2, y: 22, class: "flow-node-title"}, node.title));
        g.appendChild(svg("text", {x: p.w / 2, y: 40, class: "flow-node-id"}, node.id));
        if (node.visits > 1) {
          g.appendChild(svg("rect", {x: p.w - 26, y: -8, width: 30, height: 18, rx: 9, class: "flow-badge"}));
          g.appendChild(svg("text", {x: p.w - 11, y: 5, class: "flow-badge-text"}, "×" + node.visits));
        }
        const index = lastStepOf(node.id);
        if (index) {
          g.setAttribute("tabindex", "0");
          g.setAttribute("role", "button");
          g.setAttribute("aria-label", node.title + " 단계의 State 보기");
          const pick = () => { selected = index; render(); };
          g.addEventListener("click", pick);
          g.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } });
        }
      }
      g.appendChild(svg("title", {}, node.where || node.id));
      nodeLayer.appendChild(g);
    }
    canvas.append(edgeLayer, nodeLayer, labelLayer);
    target.appendChild(canvas);             // 글자 크기를 재려면 먼저 문서에 붙어 있어야 한다
    placeLabels(labelLayer, labels, obstacles, {w: width, h: height});
  }

  // ── 단계와 State ────────────────────────────────────────
  function currentIndex() {
    if (!flow || !flow.steps.length) return null;
    if (selected && flow.steps.some(s => s.index === selected)) return selected;
    return flow.steps[flow.steps.length - 1].index;
  }

  function titleOf(nodeId) {
    const node = flow.nodes.find(n => n.id === nodeId);
    return node ? node.title : nodeId;
  }

  function drawSteps() {
    const list = $("flow-steps");
    list.replaceChildren();
    const active = currentIndex();
    for (const step of flow.steps) {
      const li = el("li");
      const button = el("button", "flow-step" + (step.index === active ? " active" : "") + (step.waiting ? " waiting" : ""));
      button.type = "button";
      button.appendChild(el("span", "flow-step-no", step.index));
      button.appendChild(el("span", "", titleOf(step.node)));
      if (step.waiting) button.appendChild(el("span", "flow-step-wait", "대기"));
      button.addEventListener("click", () => { selected = step.index; render(); });
      li.appendChild(button);
      list.appendChild(li);
    }
  }

  function fields(parent, data, keys) {
    const dl = el("dl", "field-list");
    for (const key of keys) {
      dl.appendChild(el("dt", "", (STATE_LABELS[key] || key) + " · " + key));
      dl.appendChild(el("dd", "", format(data[key])));
    }
    parent.appendChild(dl);
  }

  function drawState() {
    const target = $("flow-state");
    target.replaceChildren();
    const step = flow.steps.find(s => s.index === currentIndex());
    if (!step) {
      target.appendChild(el("p", "empty-state", "아직 지나간 노드가 없습니다."));
      return;
    }
    const node = flow.nodes.find(n => n.id === step.node) || {};
    target.appendChild(el("h3", "flow-state-title", step.index + "단계 · " + (node.title || step.node)));
    target.appendChild(el("p", "result-item-meta", step.node + " — " + (node.where || "")));
    if (step.waiting) target.appendChild(el("div", "flow-waiting", "현재 노드: " + step.waiting));
    const changed = step.changed.filter(k => k !== "waiting");
    target.appendChild(el("h4", "flow-sub", "이 단계에서 바뀐 State"));
    if (changed.length) fields(target, step.delta, changed);
    else target.appendChild(el("p", "empty-state", "바뀐 값이 없습니다."));
    const details = el("details", "raw-details");
    details.appendChild(el("summary", "", "이 시점의 전체 State 보기"));
    details.appendChild(el("pre", "", format(step.state)));
    target.appendChild(details);
  }

  function drawHeader() {
    const v = view();
    const status = v ? v.statusLabel(flow.status) + " / " + v.statusLabel(flow.phase) : flow.status + " / " + flow.phase;
    $("flow-summary").textContent = flow.current
      ? "진행 중 · 현재 노드 " + titleOf(flow.current) + " (" + flow.current + ") · " + status
      : "완료 · " + flow.steps.length + "단계 · " + status;
    const notice = $("flow-anomalies");
    notice.replaceChildren();
    notice.hidden = !flow.anomalies.length;
    if (flow.anomalies.length) {
      notice.appendChild(el("strong", "", "실제 이벤트와 맞지 않는 부분"));
      const ul = el("ul");
      for (const a of flow.anomalies) ul.appendChild(el("li", "", a));
      notice.appendChild(ul);
    }
  }

  function render() {
    if (!flow) return;
    drawHeader();
    drawGraph();
    drawSteps();
    drawState();
  }

  root.DashboardFlow = {
    update(data, {reset = false} = {}) {
      if (reset) selected = null;
      flow = data;
      $("flow-empty").hidden = true;
      $("flow-body").hidden = false;
      render();
    },
    clear(message) {
      flow = null;
      selected = null;
      $("flow-body").hidden = true;
      $("flow-empty").hidden = false;
      $("flow-empty").textContent = message || "실행을 선택하면 LangGraph 흐름이 표시됩니다.";
      $("flow-summary").textContent = "";
      $("flow-anomalies").hidden = true;
    },
  };
})(typeof globalThis !== "undefined" ? globalThis : this);
