import { useCallback, useEffect, useRef, useState } from 'react'
import { dynamic, gmail } from './api'
import { canStartDynamic, isActive, isLinkedDynamicJob, matchesDynamicJob, mergeDynamicJob } from './dynamicState'
import { createInboxDispatcher } from './inboxDispatcher'

const SUBMISSION_ERRORS = {
  static_not_ready: '정적 검사 결과를 확인하지 못했어요.',
  not_flagged: '동적 검사는 정적 검사에서 의심·위험으로 분류된 메일에 실행해요.',
}

export default function useDynamicInbox(rows, setRows) {
  const [settings, setSettings] = useState(null)
  const [notice, setNotice] = useState('')
  const dispatcher = useRef(null)
  const submissions = useRef(new Set())
  const updateSubmission = useCallback((row, submitting, error, job) => {
    setRows((prev) => prev?.map((current) => current.id === row.id && current.body_hash === row.body_hash
      ? { ...mergeDynamicJob(current, job), dynamicSubmitting: submitting, dynamicSubmitError: error }
      : current) ?? prev)
  }, [setRows])

  const request = useCallback(async (row, start = false) => {
    const key = `${row.id}:${row.body_hash}`
    if (submissions.current.has(key)) return
    submissions.current.add(key)
    updateSubmission(row, true, '')
    try {
      return await dynamic(`/mail/${encodeURIComponent(row.id)}${start ? '?start=true' : ''}`, { method: 'POST' })
    } finally {
      submissions.current.delete(key)
    }
  }, [updateSubmission])

  const submitted = useCallback((row, data) => {
    if (!data) return
    const error = !data.job && data.reason !== 'automatic_disabled'
      ? SUBMISSION_ERRORS[data.reason] ?? '동적 검사를 시작하지 못했어요.' : ''
    updateSubmission(row, false, error, data.job)
  }, [updateSubmission])

  const failed = useCallback((error, row) => {
    updateSubmission(row, false, error.message)
  }, [updateSubmission])

  const startDynamic = async (row) => {
    try { submitted(row, await request(row, true)) }
    catch (error) { failed(error, row) }
  }

  useEffect(() => {
    let active = true
    dispatcher.current = createInboxDispatcher(request, submitted, failed)
    gmail('/api/session').then(() => dynamic('/settings')).then((value) => {
      if (active) setSettings(value)
    }).catch((error) => { if (active) setNotice(error.message) })
    return () => { active = false; dispatcher.current?.close() }
  }, [request, submitted, failed])

  useEffect(() => {
    if (!rows || !settings?.settings.auto_submit) return
    const flagged = rows.filter(canStartDynamic)
    if (flagged.length && !settings.configured) {
      setNotice('의심·위험 메일의 동적 검사를 시작하려면 모델 설정을 저장하세요.')
      return
    }
    dispatcher.current?.add(flagged)
  }, [rows, settings])

  const activeIds = (rows ?? []).filter((r) => isActive(r.dynamic)).map((r) => r.dynamic.id).join(',')
  useEffect(() => {
    if (!activeIds) return
    let stopped = false, timer
    const refresh = async () => {
      try {
        const { jobs } = await dynamic('/jobs')
        if (stopped) return
        setRows((prev) => prev?.map((row) => {
          const matching = jobs.filter((job) => matchesDynamicJob(row, job))
          const job = matching.find(isLinkedDynamicJob) ?? matching[0]
          return mergeDynamicJob(row, job)
        }) ?? prev)
      } catch (error) { if (!stopped) setNotice(error.message) }
      if (!stopped) timer = setTimeout(refresh, 1500)
    }
    timer = setTimeout(refresh, 1000)
    return () => { stopped = true; clearTimeout(timer) }
  }, [activeIds, setRows])
  return { notice, startDynamic }
}
