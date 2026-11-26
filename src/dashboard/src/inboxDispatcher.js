// A render/update never cancels the rest of the batch. Only unmount closes it.
export function createInboxDispatcher(submit, onResult, onError) {
  const seen = new Set(), waiting = []
  let closed = false, running = null
  const drain = async () => {
    while (!closed && waiting.length) {
      const row = waiting.shift()
      try { const result = await submit(row); if (!closed) onResult(row, result) }
      catch (error) { if (!closed) onError(error, row) }
    }
  }
  return {
    add(rows) {
      for (const row of rows) {
        const key = row.id + ':' + row.body_hash
        if (!seen.has(key)) { seen.add(key); waiting.push(row) }
      }
      if (!running && !closed) running = drain().finally(() => { running = null })
    },
    close() { closed = true; waiting.length = 0 },
    finished() { return running ?? Promise.resolve() },
  }
}
