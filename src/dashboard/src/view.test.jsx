import assert from 'node:assert/strict'
import { test } from 'node:test'
import { renderToStaticMarkup } from 'react-dom/server'
import { MailTable, VerdictDetail } from './Verdict'
import * as Verdict from './Verdict'
import { scanRow } from './scans'

const row = (extra = {}) => scanRow({ id: 'synthetic', gmail_message_id: 'synthetic', user_id: 'synthetic-user', subject: '합성 검증 메일', sender: 'test@example.invalid', received_at: '2026-10-29T00:00:00Z', status: 'ok', verdict: 'safe', final_score: 13, ...extra })

test('automatic analysis shows flagged mail, model and observations without hiding static verdict', () => {
  const markup = renderToStaticMarkup(<Verdict.AutomaticAnalysis rows={[
    row({ subject: '<script>synthetic</script>', verdict: 'danger', analysis: { status: 'error', dynamic: {
      id: 'run-a', provider: 'ollama', model: 'synthetic-local', status: 'timeout',
      observations: { canary_leaked: null },
    } } }),
    row({ id: 'normal', subject: 'normal-mail-excluded', verdict: 'safe' }),
  ]} onSelect={() => {}} />)
  assert.match(markup, /수집 메일 자동 동적 분석/)
  assert.match(markup, /위험/)
  assert.match(markup, /synthetic-local/)
  assert.match(markup, /시간 초과/)
  assert.match(markup, /미확인/)
  assert.match(markup, /#\/experiment\?run=run-a/)
  assert.match(markup, /&lt;script&gt;synthetic&lt;\/script&gt;/)
  assert.doesNotMatch(markup, /normal-mail-excluded|<script>/)
})

test('automatic analysis distinguishes skipped, not yet registered and unavailable models', () => {
  const markup = renderToStaticMarkup(<Verdict.AutomaticAnalysis rows={[
    row({ verdict: 'suspicious', analysis: { status: 'skipped' } }),
    row({ id: 'old', verdict: 'danger', analysis: null }),
  ]} onSelect={() => {}} />)
  assert.match(markup, /동적 분석 생략/)
  assert.match(markup, /실행 기록 없음/)
  assert.match(markup, /미기록/)
})
test('stored pipeline detail uses the original score for its label and threshold meter', () => {
  const r = row({ verdict: 'danger', static_score: 94, final_score: 94, analysis: { static: {
    status: 'completed', verdict: 'danger', score: 0.943566, mode: 'ensemble',
    suspicious_threshold: 0.939322, danger_threshold: 0.942692,
  } } })
  const markup = renderToStaticMarkup(<VerdictDetail r={r} />)
  assert.match(markup, /94\.36/)
  assert.match(markup, new RegExp(`left:${r.final_score}%`))
  assert.ok(r.final_score > r.static_thresholds.danger)
})
test('danger verdict precedes incomplete warning in list and detail', () => {
  const r = row({ verdict: 'danger', status: 'incomplete' })
  for (const markup of [renderToStaticMarkup(<MailTable rows={[r]} onSelect={() => {}} />), renderToStaticMarkup(<VerdictDetail r={r} />)]) {
    assert.match(markup, /위험/)
    assert.match(markup, /수집 불완전/)
    assert.ok(markup.indexOf('위험') < markup.indexOf('수집 불완전'))
  }
})
test('dynamic step limit is explicit and never replaces the static danger verdict', () => {
  const markup = renderToStaticMarkup(<VerdictDetail r={row({ verdict: 'danger', analysis: {
    status: 'error', dynamic: { status: 'step_limit' },
  } })} />)
  assert.match(markup, /단계 제한 도달/)
  assert.match(markup, /band danger/)
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
