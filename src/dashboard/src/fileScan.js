const DOWN = '검사 서버에 연결하지 못했습니다. 서버가 실행 중인지 확인하세요.'

async function request(fetcher, path, options) {
  let response
  try { response = await fetcher(path, options) } catch { throw new Error(DOWN) }
  const body = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(body.error?.message ?? DOWN)
  return body
}

async function encodeFile(file) {
  const bytes = new Uint8Array(await file.arrayBuffer())
  let binary = ''
  for (let offset = 0; offset < bytes.length; offset += 32768) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 32768))
  }
  return { filename: file.name, data: btoa(binary) }
}

export async function scanFiles(eml, pdf, fetcher = fetch) {
  const session = await request(fetcher, '/api/session')
  if (!session.csrf_token) throw new Error('검사 세션을 확인할 수 없습니다. 페이지를 새로고침한 뒤 다시 시도해 주세요.')
  const payload = { eml: await encodeFile(eml) }
  if (pdf) payload.pdf = await encodeFile(pdf)
  return request(fetcher, '/api/scan/file', {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': session.csrf_token },
    body: JSON.stringify(payload),
  })
}
