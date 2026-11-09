import { useEffect, useRef, useState } from 'react'
import { dynamic, gmail } from './api'
import { isActive } from './dynamicState'
import { createInboxDispatcher } from './inboxDispatcher'

export default function useDynamicInbox(rows, setRows) {
  const [settings, setSettings] = useState(null)
  const [notice, setNotice] = useState('')
  const dispatcher = useRef(null)
  useEffect(() => {
    let active = true
    dispatcher.current = createInboxDispatcher(
      (row) => dynamic(`/mail/${encodeURIComponent(row.id)}`, { method: 'POST' }),
      (row, data) => { if (data.job) setRows((prev) => prev?.map((r) => r.id === row.id ? { ...r, dynamic: data.job } : r)) },
      (error) => setNotice(error.message),
    )
    gmail('/api/session').then(() => dynamic('/settings')).then((value) => {
      if (active) setSettings(value)
    }).catch((error) => { if (active) setNotice(error.message) })
    return () => { active = false; dispatcher.current?.close() }
  }, [setRows])

  useEffect(() => {
    if (!rows || !settings?.settings.auto_submit) return
    const flagged = rows.filter((r) => !r.pending && ['suspicious', 'danger'].includes(r.verdict))
    if (flagged.length && !settings.configured) {
      setNotice('의심·위험 메일의 동적 검사를 시작하려면 모델 설정을 저장하세요.')
      return
    }
    dispatcher.current?.add(flagged)
  }, [rows, settings, setRows])

  const activeIds = (rows ?? []).filter((r) => isActive(r.dynamic)).map((r) => r.dynamic.id).join(',')
  useEffect(() => {
    if (!activeIds) return
    let stopped = false, timer
    const refresh = async () => {
      try {
        const { jobs } = await dynamic('/jobs')
        if (stopped) return
        setRows((prev) => prev?.map((row) => {
          const job = jobs.find((j) => j.mail_id === row.id && j.body_hash === row.body_hash)
          return job ? { ...row, dynamic: job } : row
        }))
      } catch (error) { if (!stopped) setNotice(error.message) }
      if (!stopped) timer = setTimeout(refresh, 1500)
    }
    timer = setTimeout(refresh, 1000)
    return () => { stopped = true; clearTimeout(timer) }
  }, [activeIds, setRows])
  return notice
}
