/* LangGraph 흐름 패널: /runs/{id}/flow 의 노드·엣지·단계별 State 를 SVG와 목록으로 그린다.
   비신뢰 값은 모두 textContent / createTextNode 로만 넣는다.
   상태(성공·주의·실행 중·실패·미도달)는 색뿐 아니라 아이콘 모양과 글자로도 구분한다. */
(function (root) {
  "use strict";

  const SVG = "http://www.w3.org/2000/svg";
  const COL = 234, ROW = 128, NW = 156, NH = 64, TW = 76, TH = 30, PAD = 30, PAD_BOTTOM = 58;
  const SEVERITY = {idle: -1, ok: 0, warn: 1, running: 2, fail: 3};
  const STATUS_TEXT = {ok: "성공", running: "실행 중", warn: "주의", fail: "실패", idle: "미도달"};
  const STATE_LABELS = {
    run_id: "실행 ID", waiting: "대기 중", request: "실행 요청", container_id: "작업 컨테이너",
    llm_calls: "LLM 호출", last_tool_calls: "마지막 응답의 도구 요청", tools: "도구 실행 결과",
    tool_blocked_limit: "도구 한도 차단", final: "에이전트 종료", evidence: "수집된 증거",
    evaluation: "판정", phase: "실행 단계", experiment: "실험 기록", anomalies: "불일치",
  };
  const LEGEND = [["ok", "성공"], ["running", "실행 중"], ["warn", "주의 · 이탈 요청이나 효과 감지"],
                  ["fail", "실패 · 한도·오류·불일치"], ["idle", "미도달"]];

  let flow = null;
  let selected = null;      // 사용자가 고른 단계 번호 (null 이면 최신 단계를 따라감)
  let renderedKey = "";
  let lastSummary = "";
  let pendingFocus = null;  // 방향키로 옮긴 단계로 포커스를 따라가게 한다

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

  // ── 상태 아이콘: 색이 달라도 모양으로 구분된다 ───────────────────────
  function glyph(status, size) {
    const g = svg("g", {class: "flow-glyph st-" + status});
    g.appendChild(svg("circle", {r: size / 2, class: "flow-glyph-bg"}));
    const k = size / 16;
    const mark = (tag, attrs) => g.appendChild(svg(tag, {...attrs, class: "flow-glyph-mark"}));
    if (status === "ok") mark("path", {d: `M${-3.6 * k},${0.4 * k} L${-1 * k},${3 * k} L${3.6 * k},${-3 * k}`});
    else if (status === "fail") mark("path", {d: `M${-3 * k},${-3 * k} L${3 * k},${3 * k} M${3 * k},${-3 * k} L${-3 * k},${3 * k}`});
    else if (status === "warn") {
      mark("path", {d: `M0,${-3.6 * k} L0,${0.9 * k}`});
      g.appendChild(svg("circle", {cy: 3.1 * k, r: 1 * k, class: "flow-glyph-dot"}));
    } else if (status === "running") {
      const spin = svg("g", {class: "flow-spin"});
      spin.appendChild(svg("circle", {r: 3.8 * k, class: "flow-glyph-mark flow-glyph-ring", "stroke-dasharray": `${11 * k} ${13 * k}`}));
      g.appendChild(spin);
    } else mark("path", {d: `M${-3 * k},0 L${3 * k},0`});
    return g;
  }

  function inlineGlyph(status, size) {
    const canvas = svg("svg", {viewBox: `${-size / 2} ${-size / 2} ${size} ${size}`, width: size, height: size,
      "aria-hidden": "true", focusable: "false", class: "flow-inline-glyph"});
    canvas.appendChild(glyph(status, size - 2));
    return canvas;
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

  function edgeGeometry(edge, pos, reversed, lastY) {
    const a = pos.get(edge.source), b = pos.get(edge.target);
    const mid = {x: (a.x + b.x) / 2, y: (a.y + b.y) / 2};
    const dx = b.x - a.x, dy = b.y - a.y, len = Math.hypot(dx, dy) || 1;
    let ctrl = mid;
    const sameRow = Math.abs(dy) < 1, span = Math.round(Math.abs(dx) / COL);
    if (reversed) ctrl = {x: mid.x - dy / len * 30, y: mid.y + dx / len * 30};
    else if (sameRow && span > 1) ctrl = {x: mid.x, y: mid.y + (a.y >= lastY ? 1 : -1) * 92};  // 사이 노드를 피해 바깥으로 휜다
    const start = clip(a, ctrl === mid ? b : ctrl, a), end = clip(b, ctrl === mid ? a : ctrl, b);
    const d = ctrl === mid ? `M${start.x},${start.y} L${end.x},${end.y}`
      : `M${start.x},${start.y} Q${ctrl.x},${ctrl.y} ${end.x},${end.y}`;
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
      layer.insertBefore(svg("rect", {x: r.x, y: r.y, width: r.w, height: r.h, rx: 5,
        class: "flow-label-bg " + label.tone}), text);
    }
  }

  // ── 선택한 시점 기준으로 그래프 상태를 계산한다 ──────────────────────────
  function currentIndex() {
    if (!flow || !flow.steps.length) return null;
    if (selected && flow.steps.some(s => s.index === selected)) return selected;
    return flow.steps[flow.steps.length - 1].index;
  }

  function stepOf(index) { return flow.steps.find(s => s.index === index); }

  function titleOf(nodeId) {
    const node = flow.nodes.find(n => n.id === nodeId);
    return node ? node.title : nodeId;
  }

  /* active 단계까지 지나간 것만 반영한다. 단계를 고르면 "그 시점"의 그래프가 보인다. */
  function snapshot(active) {
    const upto = flow.steps.filter(s => s.index <= active);
    const last = flow.steps.length ? flow.steps[flow.steps.length - 1].index : 0;
    const ended = active === last && !flow.waiting && flow.steps.length > 0;
    const visits = new Map();
    for (const s of upto) {
      if (!visits.has(s.node)) visits.set(s.node, []);
      visits.get(s.node).push(s);
    }
    const nodeState = new Map();
    for (const n of flow.nodes) {
      if (n.id === "__start__") { nodeState.set(n.id, {status: "ok", label: "시작", count: 1}); continue; }
      if (n.id === "__end__") {
        const failed = ended && flow.outcome && flow.outcome.status === "fail";
        nodeState.set(n.id, ended ? {status: failed ? "fail" : "ok", label: failed ? "실패로 종료" : "종료", count: 1}
          : {status: "idle", label: STATUS_TEXT.idle, count: 0});
        continue;
      }
      const list = visits.get(n.id) || [];
      if (!list.length) { nodeState.set(n.id, {status: "idle", label: STATUS_TEXT.idle, count: 0}); continue; }
      let worst = list[0];
      for (const s of list) if (SEVERITY[s.outcome.status] >= SEVERITY[worst.outcome.status]) worst = s;
      nodeState.set(n.id, {status: worst.outcome.status, label: worst.outcome.label, detail: worst.outcome.detail,
        count: list.length, steps: list});
    }
    const seq = ["__start__", ...upto.map(s => s.node)];
    if (ended) seq.push("__end__");
    const edgeCount = new Map();
    for (let i = 0; i + 1 < seq.length; i++) {
      const key = seq[i] + ">" + seq[i + 1];
      edgeCount.set(key, (edgeCount.get(key) || 0) + 1);
    }
    const activeStep = stepOf(active);
    const prev = upto.length > 1 ? upto[upto.length - 2].node : "__start__";
    return {nodeState, edgeCount, visits, ended, activeEdge: activeStep ? prev + ">" + activeStep.node : ""};
  }

  // ── 그래프 ──────────────────────────────────────────────
  function drawGraph() {
    const target = $("flow-graph");
    target.replaceChildren();
    const active = currentIndex();
    const snap = snapshot(active || 0);
    const pos = positions(flow.nodes);
    const lastY = Math.max(...[...pos.values()].map(p => p.y));
    const width = Math.max(...[...pos.values()].map(p => p.x + p.w / 2)) + PAD;
    const height = lastY + NH / 2 + PAD_BOTTOM;
    const canvas = svg("svg", {viewBox: `0 0 ${width} ${height}`, style: `max-width:${width}px`, role: "group",
      "aria-label": "LangGraph 데이터 흐름 그래프. 노드를 누르면 그 단계의 State를 볼 수 있습니다."});
    const defs = svg("defs");
    for (const [id, cls] of [["flow-arrow-on", "on"], ["flow-arrow-off", "off"], ["flow-arrow-bad", "bad"]]) {
      const marker = svg("marker", {id, viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: "auto-start-reverse"});
      marker.appendChild(svg("path", {d: "M0,0 L10,5 L0,10 z", class: "flow-arrow " + cls}));
      defs.appendChild(marker);
    }
    canvas.appendChild(defs);

    const groupLayer = svg("g"), edgeLayer = svg("g"), nodeLayer = svg("g"), labelLayer = svg("g");
    const labels = [];
    const obstacles = [...pos.values()].map(p => ({x: p.x - p.w / 2 - 5, y: p.y - p.h / 2 - 5, w: p.w + 10, h: p.h + 10}));

    // 반복 구간: 점선 상자로 묶고 지금까지 몇 번 돌았는지 적는다.
    for (const group of flow.groups || []) {
      const members = group.nodes.map(id => pos.get(id)).filter(Boolean);
      if (!members.length) continue;
      const x1 = Math.min(...members.map(p => p.x - p.w / 2)) - 16, x2 = Math.max(...members.map(p => p.x + p.w / 2)) + 16;
      const y1 = Math.min(...members.map(p => p.y - p.h / 2)) - 14, y2 = Math.max(...members.map(p => p.y + p.h / 2)) + 46;
      const running = group.nodes.some(id => (snap.nodeState.get(id) || {}).status === "running");
      const counts = group.nodes.map(id => (snap.nodeState.get(id) || {}).count || 0);
      const active_ = counts.some(Boolean);
      groupLayer.appendChild(svg("rect", {x: x1, y: y1, width: x2 - x1, height: y2 - y1, rx: 14,
        class: "flow-group" + (active_ ? " used" : "") + (running ? " running" : "")}));
      // 상자보다 긴 캡션이 잘리지 않도록 제목과 횟수를 두 줄로 나눈다.
      const counted = group.nodes.map((id, i) => titleOf(id) + " " + counts[i] + "회").join(" · ");
      groupLayer.appendChild(svg("text", {x: (x1 + x2) / 2, y: y2 - 27, class: "flow-group-label title" + (active_ ? " used" : "")}, group.title));
      groupLayer.appendChild(svg("text", {x: (x1 + x2) / 2, y: y2 - 11, class: "flow-group-label" + (active_ ? " used" : "")}, counted));
      obstacles.push({x: x1 + 4, y: y2 - 40, w: x2 - x1 - 8, h: 38});
    }

    const pairs = new Set(flow.edges.map(e => e.source + ">" + e.target));
    for (const edge of flow.edges) {
      if (!pos.has(edge.source) || !pos.has(edge.target)) continue;
      const key = edge.source + ">" + edge.target;
      const count = snap.edgeCount.get(key) || 0;
      const tone = count ? (edge.abnormal ? "bad" : "on") : "off";
      const geo = edgeGeometry(edge, pos, pairs.has(edge.target + ">" + edge.source), lastY);
      edgeLayer.appendChild(svg("path", {d: geo.d,
        class: "flow-edge " + tone + (key === snap.activeEdge ? " active" : "") + (edge.conditional ? " conditional" : ""),
        "marker-end": `url(#flow-arrow-${tone})`}));
      obstacles.push({x: geo.end.x - 8, y: geo.end.y - 8, w: 16, h: 16});   // 화살표 끝
      const text = [edge.label, count > 1 ? "×" + count : ""].filter(Boolean).join(" ");
      if (text) labels.push({...geo, text, tone, className: "flow-edge-label " + tone});
    }
    labels.sort((a, b) => b.text.length - a.text.length);   // 긴 라벨일수록 들어갈 자리가 적다

    const activeStep = stepOf(active);
    for (const node of flow.nodes) {
      const p = pos.get(node.id), st = snap.nodeState.get(node.id);
      const classes = ["flow-node", "st-" + st.status];
      if (node.terminal) classes.push("terminal");
      if (activeStep && activeStep.node === node.id) classes.push("selected");
      const g = svg("g", {class: classes.join(" "), transform: `translate(${p.x - p.w / 2},${p.y - p.h / 2})`});
      g.appendChild(svg("rect", {width: p.w, height: p.h, rx: node.terminal ? p.h / 2 : 10, class: "flow-box"}));
      if (node.terminal) {
        g.appendChild(svg("text", {x: p.w / 2, y: p.h / 2 + 4, class: "flow-node-title"}, node.id === "__start__" ? "START" : "END"));
      } else {
        const icon = glyph(st.status, 16);
        icon.setAttribute("transform", "translate(17,17)");
        g.appendChild(icon);
        g.appendChild(svg("text", {x: p.w / 2 + 8, y: 22, class: "flow-node-title"}, node.title));
        g.appendChild(svg("text", {x: p.w / 2, y: 40, class: "flow-node-id"}, node.id));
        g.appendChild(svg("text", {x: p.w / 2, y: 56, class: "flow-node-status"}, st.label));
        if (st.count > 1) {
          g.appendChild(svg("rect", {x: p.w - 28, y: -9, width: 34, height: 19, rx: 9.5, class: "flow-count"}));
          g.appendChild(svg("text", {x: p.w - 11, y: 4.5, class: "flow-count-text"}, "×" + st.count));
        }
        const visits = snap.visits.get(node.id);
        if (visits && visits.length) {
          const index = visits[visits.length - 1].index;
          g.setAttribute("tabindex", "0");
          g.setAttribute("role", "button");
          g.setAttribute("data-focus-key", "n:" + node.id);
          g.setAttribute("aria-label", node.title + " · " + STATUS_TEXT[st.status] + " · " + st.label + (st.count > 1 ? " · " + st.count + "회 방문" : "") + " · State 보기");
          if (activeStep && activeStep.node === node.id) g.setAttribute("aria-current", "step");
          const pick = () => { selected = index; render(); };
          g.addEventListener("click", pick);
          g.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } });
        }
      }
      g.appendChild(svg("title", {}, node.title + " · " + st.label + (st.detail ? "\n" + st.detail : "") + (node.where ? "\n" + node.where : "")));
      nodeLayer.appendChild(g);
    }
    canvas.append(groupLayer, edgeLayer, nodeLayer, labelLayer);
    target.appendChild(canvas);             // 글자 크기를 재려면 먼저 문서에 붙어 있어야 한다
    placeLabels(labelLayer, labels, obstacles, {w: width, h: height});
  }

  // ── 단계 목록 ───────────────────────────────────────────
  function moveSelection(delta, from) {
    const base = Number.isInteger(from) ? from : currentIndex();
    const next = flow.steps.find(s => s.index === base + delta);
    if (next) { selected = next.index; if (Number.isInteger(from)) pendingFocus = "s:" + next.index; render(); }
  }

  function drawSteps() {
    const list = $("flow-steps");
    const keepScroll = list.scrollTop;
    list.replaceChildren();
    const active = currentIndex();
    for (const step of flow.steps) {
      const li = el("li");
      const status = step.outcome.status;
      const button = el("button", "flow-step st-" + status + (step.index === active ? " active" : ""));
      button.type = "button";
      button.dataset.focusKey = "s:" + step.index;
      if (step.index === active) button.setAttribute("aria-current", "step");
      button.setAttribute("aria-label", step.index + "단계 " + titleOf(step.node) + " " + STATUS_TEXT[status] + " " + step.outcome.label);
      button.appendChild(inlineGlyph(status, 16));
      button.appendChild(el("span", "flow-step-no", step.index));
      button.appendChild(el("span", "flow-step-name", titleOf(step.node)));
      button.appendChild(el("span", "flow-step-label", step.outcome.label));
      button.addEventListener("click", () => { selected = step.index; render(); });
      li.appendChild(button);
      list.appendChild(li);
    }
    list.onkeydown = e => {      // 목록 안에서는 ↑/↓/Home/End 로 단계를 옮긴다 (포커스는 render 가 유지한다)
      const move = {ArrowDown: 1, ArrowUp: -1}[e.key];
      const focusedStep = Number(((e.target.dataset && e.target.dataset.focusKey) || "").replace(/^s:/, ""));
      if (move) { e.preventDefault(); moveSelection(move, Number.isInteger(focusedStep) && focusedStep > 0 ? focusedStep : undefined); }
      else if (e.key === "Home" || e.key === "End") {
        e.preventDefault();
        selected = flow.steps[e.key === "Home" ? 0 : flow.steps.length - 1].index;
        pendingFocus = "s:" + selected;
        render();
      }
    };
    // 최신 단계를 따라가는 중이면 맨 아래, 직접 고른 단계는 보이는 곳에 둔다.
    const activeButton = list.querySelector(".flow-step.active");
    if (selected === null) list.scrollTop = list.scrollHeight;
    else {
      list.scrollTop = keepScroll;
      if (activeButton) {
        const top = activeButton.offsetTop, bottom = top + activeButton.offsetHeight;
        if (top < list.scrollTop) list.scrollTop = top - 4;
        else if (bottom > list.scrollTop + list.clientHeight) list.scrollTop = bottom - list.clientHeight + 4;
      }
    }
  }

  // ── State 패널 ──────────────────────────────────────────
  function statusBadge(status, text) {
    const badge = el("span", "flow-status-badge st-" + status);
    badge.appendChild(inlineGlyph(status, 16));
    badge.appendChild(el("span", "", text));
    return badge;
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
    const active = currentIndex();
    const step = stepOf(active);
    if (!step) {
      target.appendChild(el("p", "empty-state", "아직 지나간 노드가 없습니다."));
      return;
    }
    const node = flow.nodes.find(n => n.id === step.node) || {};
    const out = step.outcome;
    const head = el("div", "flow-state-head");
    head.appendChild(el("h3", "flow-state-title", step.index + "단계 · " + (node.title || step.node)));
    head.appendChild(statusBadge(out.status, STATUS_TEXT[out.status] + (out.label && out.label !== STATUS_TEXT[out.status] ? " · " + out.label : "")));
    target.appendChild(head);
    target.appendChild(el("p", "result-item-meta", step.node + " — " + (node.where || "")));
    if (out.detail) target.appendChild(el("div", "flow-detail-note st-" + out.status, out.detail));

    const nav = el("div", "flow-nav");
    const prev = el("button", "flow-nav-btn", "‹ 이전 단계");
    prev.type = "button"; prev.disabled = active <= 1; prev.dataset.focusKey = "nav:prev";
    prev.addEventListener("click", () => moveSelection(-1));
    const next = el("button", "flow-nav-btn", "다음 단계 ›");
    next.type = "button"; next.disabled = active >= flow.steps.length; next.dataset.focusKey = "nav:next";
    next.addEventListener("click", () => moveSelection(1));
    nav.append(prev, next);
    if (selected !== null && !flow.done) {
      const follow = el("button", "flow-nav-btn follow", "최신 단계 따라가기");
      follow.type = "button"; follow.dataset.focusKey = "nav:follow";
      follow.addEventListener("click", () => { selected = null; render(); });
      nav.appendChild(follow);
    }
    target.appendChild(nav);

    const visits = flow.steps.filter(s => s.node === step.node);
    if (visits.length > 1) {
      const row = el("div", "flow-visits");
      row.appendChild(el("span", "flow-visits-label", "이 노드의 방문"));
      visits.forEach((v, i) => {
        const chip = el("button", "flow-visit st-" + v.outcome.status + (v.index === active ? " active" : ""), (i + 1) + "회");
        chip.type = "button";
        chip.dataset.focusKey = "v:" + v.index;
        chip.setAttribute("aria-label", (i + 1) + "번째 방문, " + v.index + "단계, " + STATUS_TEXT[v.outcome.status]);
        if (v.index === active) chip.setAttribute("aria-current", "true");
        chip.addEventListener("click", () => { selected = v.index; render(); });
        row.appendChild(chip);
      });
      target.appendChild(row);
    }

    const changed = step.changed.filter(k => k !== "waiting");
    target.appendChild(el("h4", "flow-sub", "이 단계에서 바뀐 State"));
    if (changed.length) fields(target, step.delta, changed);
    else target.appendChild(el("p", "empty-state", "바뀐 값이 없습니다."));
    const details = el("details", "raw-details");
    details.appendChild(el("summary", "", "이 시점의 전체 State 보기"));
    details.appendChild(el("pre", "", format(step.state)));
    target.appendChild(details);
  }

  // ── 요약 · 범례 ─────────────────────────────────────────
  function drawHeader() {
    const v = view();
    const out = flow.outcome || {status: "ok", label: "성공"};
    const status = v ? v.statusLabel(flow.status) + " / " + v.statusLabel(flow.phase) : flow.status + " / " + flow.phase;
    const summary = flow.current
      ? "진행 중 · 현재 노드 " + titleOf(flow.current) + " (" + flow.current + ") · " + status
      : (out.status === "fail" ? "실패 · " : out.status === "warn" ? "주의 · " : "완료 · ") + flow.steps.length + "단계 · " + status;
    if (summary !== lastSummary) { $("flow-summary").textContent = summary; lastSummary = summary; }

    const last = flow.steps.length ? flow.steps[flow.steps.length - 1].state : {};
    const chips = $("flow-chips");
    chips.replaceChildren();
    chips.appendChild(statusBadge(out.status, STATUS_TEXT[out.status] + (out.label && !["성공", "진행 중"].includes(out.label) ? " · " + out.label : "")));
    for (const text of [flow.steps.length + "단계", "LLM 호출 " + (last.llm_calls || []).length + "회", "도구 실행 " + (last.tools || []).length + "회"])
      chips.appendChild(el("span", "flow-chip", text));
    if (flow.current) chips.appendChild(el("span", "flow-chip live", "현재: " + titleOf(flow.current)));

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

  function lineSample(tone, text) {
    const item = el("span", "flow-legend-item");
    const sample = svg("svg", {viewBox: "0 0 34 10", width: 34, height: 10, "aria-hidden": "true", focusable: "false"});
    sample.appendChild(svg("path", {d: "M1,5 L33,5", class: "flow-edge " + tone}));
    item.append(sample, el("span", "", text));
    return item;
  }

  function drawLegend() {
    const legend = $("flow-legend");
    if (!legend || legend.dataset.ready) return;
    legend.dataset.ready = "1";
    for (const [status, text] of LEGEND) {
      const item = el("span", "flow-legend-item");
      item.append(inlineGlyph(status, 16), el("span", "", text));
      legend.appendChild(item);
    }
    legend.appendChild(lineSample("on", "지나간 경로"));
    legend.appendChild(lineSample("off conditional", "지나가지 않은 분기"));
    legend.appendChild(lineSample("bad", "비정상 분기"));
    const repeat = el("span", "flow-legend-item");
    repeat.append(el("span", "flow-count-sample", "×2"), el("span", "", "반복 횟수"));
    legend.appendChild(repeat);
  }

  function render() {
    if (!flow) return;
    // 폴링으로 다시 그려도 키보드 포커스와 목록 스크롤이 유지되게 한다.
    const focused = pendingFocus || (document.activeElement && document.activeElement.dataset ? document.activeElement.dataset.focusKey : null);
    pendingFocus = null;
    drawLegend();
    drawHeader();
    drawGraph();
    drawSteps();
    drawState();
    if (focused) {
      const again = document.querySelector('#flow-body [data-focus-key="' + focused.replace(/"/g, '\\"') + '"]');
      if (again) again.focus({preventScroll: true});
    }
  }

  root.DashboardFlow = {
    update(data, {reset = false} = {}) {
      if (reset) selected = null;
      const key = JSON.stringify(data);
      const unchanged = flow && key === renderedKey && !reset;
      flow = data;
      renderedKey = key;
      $("flow-empty").hidden = true;
      $("flow-body").hidden = false;
      if (!unchanged) render();     // 같은 내용이면 다시 그리지 않아 깜박임과 포커스 손실을 막는다
    },
    clear(message) {
      flow = null;
      selected = null;
      renderedKey = "";
      lastSummary = "";
      $("flow-body").hidden = true;
      $("flow-empty").hidden = false;
      $("flow-empty").textContent = message || "실행을 선택하면 LangGraph 흐름이 표시됩니다.";
      $("flow-summary").textContent = "";
      $("flow-chips").replaceChildren();
      $("flow-anomalies").hidden = true;
    },
  };
})(typeof globalThis !== "undefined" ? globalThis : this);
