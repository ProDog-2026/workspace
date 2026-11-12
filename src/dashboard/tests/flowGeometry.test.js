import test from 'node:test'
import assert from 'node:assert/strict'
import { containerBounds, flowLayout, flowEdgePath } from '../src/flowGeometry.js'

test('worker steps are inside the container boundary and the LLM gateway is outside', () => {
  const nodes = ['spawn_container', 'execute_tools', 'agent_final', 'llm_gateway', 'collect_evidence'].map((id) => ({ id }))
  const { positions } = flowLayout(nodes)
  const inside = (id) => {
    const [x, y] = positions.get(id)
    return x >= containerBounds.x && x + containerBounds.nodeWidth <= containerBounds.x + containerBounds.width &&
      y >= containerBounds.y && y + containerBounds.nodeHeight <= containerBounds.y + containerBounds.height
  }
  for (const id of ['spawn_container', 'execute_tools', 'agent_final']) assert.ok(inside(id), id)
  assert.equal(inside('llm_gateway'), false)
  assert.equal(inside('collect_evidence'), false)
})

test('request and response have separate paths, and unknown steps remain visible', () => {
  const { positions, height } = flowLayout([{ id: 'llm_gateway' }, { id: 'execute_tools' }, { id: 'new_step' }])
  const request = flowEdgePath({ source: 'llm_gateway', target: 'execute_tools' }, positions)
  const response = flowEdgePath({ source: 'execute_tools', target: 'llm_gateway' }, positions)
  assert.notEqual(request, response)
  assert.ok(height > positions.get('new_step')[1])
  assert.equal(flowEdgePath({ source: 'missing', target: 'new_step' }, positions), null)
})
