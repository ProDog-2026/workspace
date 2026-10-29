import assert from 'node:assert/strict'
import { test } from 'node:test'
import { renderToStaticMarkup } from 'react-dom/server'
import { MailTable, VerdictDetail } from './Verdict'
import { scanRow } from './scans'

const row = (extra = {}) => scanRow({ id: 'synthetic', gmail_message_id: 'synthetic', user_id: 'synthetic-user', subject: '합성 검증 메일', sender: 'test@example.invalid', received_at: '2026-10-29T00:00:00Z', status: 'ok', verdict: 'safe', final_score: 13, ...extra })
test('danger verdict precedes incomplete warning in list and detail', () => {
  const r = row({ verdict: 'danger', status: 'incomplete' })
  for (const markup of [renderToStaticMarkup(<MailTable rows={[r]} onSelect={() => {}} />), renderToStaticMarkup(<VerdictDetail r={r} />)]) {
    assert.match(markup, /위험/)
    assert.match(markup, /수집 불완전/)
    assert.ok(markup.indexOf('위험') < markup.indexOf('수집 불완전'))
  }
})
test('evidence, subject and model explanation render as escaped text', () => {
  const markup = renderToStaticMarkup(<VerdictDetail r={row({ subject: '<img src=x onerror=attack()>', llm_reason: '<script>attack()</script>', evidence: [{ rule: 'R01', type: 'html_comment', snippet: '<script>attack()</script>' }] })} />)
  assert.match(markup, /&lt;script&gt;attack\(\)&lt;\/script&gt;/)
  assert.match(markup, /&lt;img/)
  assert.doesNotMatch(markup, /<script>|<img/)
  assert.doesNotMatch(markup, /meter-pin/)
})
test('all verdict labels and unscanned state render without safe fallback', () => {
  const markup = renderToStaticMarkup(<MailTable rows={['danger', 'suspicious', 'safe', null].map((verdict, i) => ({ ...row({ verdict }), id: String(i) }))} onSelect={() => {}} />)
  for (const label of ['위험', '의심', '정상', '검사 전']) assert.match(markup, new RegExp(label))
  const partial = renderToStaticMarkup(<VerdictDetail r={row({ status: 'incomplete' })} />)
  assert.match(partial, /band incomplete/)
  assert.doesNotMatch(partial, /band safe/)
})
