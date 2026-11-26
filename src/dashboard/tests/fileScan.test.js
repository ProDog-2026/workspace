import test from 'node:test'
import assert from 'node:assert/strict'
import { scanFiles } from '../src/fileScan.js'

test('file scan submits base64 files with session CSRF token and returns result', async () => {
  const calls = []
  const transport = async (url, options) => {
    calls.push([url, options])
    return url === '/api/session'
      ? { ok: true, json: async () => ({ csrf_token: 'token' }) }
      : { ok: true, json: async () => ({ mail: { detection: { verdict: 'danger' } } }) }
  }
  const result = await scanFiles({ name: 'a.eml', arrayBuffer: async () => new Uint8Array([65, 66]).buffer },
    { name: 'a.pdf', arrayBuffer: async () => new Uint8Array([67]).buffer }, transport)
  assert.equal(result.mail.detection.verdict, 'danger')
  assert.equal(calls[1][0], '/api/scan/file')
  assert.equal(calls[1][1].headers['X-CSRF-Token'], 'token')
  assert.deepEqual(JSON.parse(calls[1][1].body), {
    eml: { filename: 'a.eml', data: 'QUI=' }, pdf: { filename: 'a.pdf', data: 'Qw==' },
  })
})

test('file scan surfaces server error messages', async () => {
  const transport = async (url) => url === '/api/session'
    ? { ok: true, json: async () => ({ csrf_token: 'token' }) }
    : { ok: false, json: async () => ({ error: { message: 'PDF 파일을 읽을 수 없습니다.' } }) }
  await assert.rejects(scanFiles({ name: 'a.eml', arrayBuffer: async () => new Uint8Array([65]).buffer }, null, transport), /PDF 파일을 읽을 수 없습니다/)
})
