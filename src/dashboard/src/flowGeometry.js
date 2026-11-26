// Layout describes the execution boundary; graph topology and counts still come from the API.
export const containerBounds = { x: 710, y: 58, width: 430, height: 470, nodeWidth: 176, nodeHeight: 64 }
export const graphWidth = 1180

// The trace ends at the waiting node; completed traces have no active edge.
export function activeFlowEdge(flow) {
  if (!flow.current || flow.done || !flow.path?.length || flow.path.at(-1) !== flow.current) return null
  return { source: flow.path.at(-2) ?? '__start__', target: flow.current }
}
const positionsById = {
  __start__: [30, 110], submit_request: [230, 110], launch_run: [430, 110],
  spawn_container: [770, 110], llm_gateway: [430, 275], execute_tools: [770, 275],
  agent_final: [770, 425], collect_evidence: [430, 425], evaluate_rules: [230, 425],
  persist_result: [30, 425], cleanup_container: [30, 575], dashboard_view: [230, 575],
  record_experiment_row: [430, 575], __end__: [730, 575],
}

export function flowLayout(nodes) {
  const positions = new Map()
  let unknown = 0
  for (const node of nodes) {
    const known = positionsById[node.id]
    positions.set(node.id, known ?? [30 + (unknown % 5) * 220, 765 + Math.floor(unknown / 5) * 110])
    if (!known) unknown++
  }
  return { positions, height: unknown ? 765 + Math.ceil(unknown / 5) * 110 : 750 }
}

const routed = {
  'spawn_container:llm_gateway': 'M858 174 V210 H680 V250 H518 V275',
  'llm_gateway:execute_tools': 'M606 297 H770',
  'execute_tools:llm_gateway': 'M770 317 H606',
  'llm_gateway:agent_final': 'M518 339 V380 H680 V440 H770',
  'cleanup_container:record_experiment_row': 'M118 639 V675 H518 V639',
  'dashboard_view:__end__': 'M318 639 V710 H818 V639',
  'persist_result:__end__': 'M30 457 H14 V730 H818 V639',
}

export function flowEdgePath(edge, positions) {
  const a = positions.get(edge.source), b = positions.get(edge.target)
  if (!a || !b) return null
  const special = routed[`${edge.source}:${edge.target}`]
  if (special) return special
  const [ax, ay] = a, [bx, by] = b
  if (ay === by) {
    const forward = ax < bx
    return `M${ax + (forward ? 176 : 0)} ${ay + 32} H${bx + (forward ? 0 : 176)}`
  }
  if (ax === bx) {
    const down = ay < by
    return `M${ax + 88} ${ay + (down ? 64 : 0)} V${by + (down ? 0 : 64)}`
  }
  const down = ay < by
  return `M${ax + 88} ${ay + (down ? 64 : 0)} L${bx + 88} ${by + (down ? 0 : 64)}`
}
