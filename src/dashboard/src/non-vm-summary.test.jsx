import assert from 'node:assert/strict'
import { test } from 'node:test'
import { renderToStaticMarkup } from 'react-dom/server'
import { scanRow } from './scans'
import { VerdictDetail } from './Verdict'

test('mail detail distinguishes cumulative exposure from the last file snapshot', () => {
  const row = scanRow({ id: 'synthetic', status: 'ok', verdict: 'danger', analysis: {
    status: 'completed', static: { status: 'completed', verdict: 'danger', score: 0.9 },
    dynamic: { status: 'completed', observations: {
      complete: true, canary_leaked: true, canary_present_at_last_snapshot: false,
    } },
  } })
  const html = renderToStaticMarkup(<VerdictDetail r={row} />)
  assert.match(html, /누적 관측[^<]*관측됨/)
  assert.match(html, /마지막 파일 수집[^<]*없음/)
})
