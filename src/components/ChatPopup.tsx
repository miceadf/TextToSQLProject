import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import {
  chatErrorMessage,
  chatRowsToLegacyRows,
  structuredTimeText,
  TIME_TBA_TEXT,
  answerWarning,
  describeAnswer,
  describeChatModel,
  describeUnderstood,
  fetchChatModels,
  postChatQuery,
  QUERYIR_TIME_GUIDE,
} from '../adaptive/chatQuery.ts'
import type { ChatModelOption, ChatTarget } from '../adaptive/chatQuery.ts'
import { getAccessToken } from '../services/apiClient'
import type { Course, CourseDetail } from '../types/course'
import { cn } from '../utils/cn'
import {
  normalizeCourseDay,
  parseCourseClockHour,
  parseCourseSlotsFromText,
} from '../utils/courseSchedule'
import { hasUnmetPrerequisite } from '../utils/prerequisites'
import { getConflictingCourseIds } from '../utils/schedule'
import Timetable from './Timetable'

type ChatMessage = {
  id: string
  role: 'user' | 'system'
  text: string
}

type QueryApiResponse = {
  sql?: string
  data?: unknown[]
  warning?: string
  error?: string
}

type QueryRow = Record<string, unknown>
type QueryScheduleSlot = {
  day?: unknown
  start?: unknown
  end?: unknown
  room?: unknown
}

function departmentColor({
  selected,
  hasConflict,
  closed,
}: {
  selected: boolean
  hasConflict: boolean
  closed: boolean
}) {
  if (selected) return 'text-rose-700'
  if (hasConflict) return 'text-amber-700'
  if (closed) return 'text-slate-900'
  return 'text-blue-700'
}

function makeId(prefix: string) {
  return `${prefix}_${Math.random().toString(16).slice(2)}_${Date.now()}`
}

function LoadingSpinner({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        'inline-block h-4 w-4 animate-spin rounded-full border-2 border-current border-r-transparent',
        className,
      )}
      aria-hidden="true"
    />
  )
}

function isQueryRow(value: unknown): value is QueryRow {
  return !!value && typeof value === 'object' && !Array.isArray(value)
}

function readString(row: QueryRow, keys: string[]) {
  for (const key of keys) {
    const value = row[key]
    if (typeof value === 'string' && value.trim()) return value.trim()
    if (typeof value === 'number') return String(value)
  }
  return ''
}

function readNumber(row: QueryRow, keys: string[]) {
  for (const key of keys) {
    const value = row[key]
    if (typeof value === 'number' && Number.isFinite(value)) return value
    if (typeof value === 'string' && value.trim()) {
      const parsed = Number(value)
      if (Number.isFinite(parsed)) return parsed
      const match = value.match(/\d+/)
      if (match) return Number(match[0])
    }
  }
  return undefined
}

function readStringList(row: QueryRow, keys: string[]) {
  const text = readString(row, keys)
  const ignored = new Set(['', '없음', 'none', 'null', '-'])
  if (ignored.has(text.trim().toLowerCase())) return []
  return text
    .split(/[,;/\n]+/)
    .map((part) => part.trim())
    .filter((part) => part && !ignored.has(part.toLowerCase()))
}

function readDetails(row: QueryRow, extraLabels: Array<[string, string]> = []): CourseDetail[] {
  const labels: Array<[string, string]> = [
    ['course_year', '학년'],
    ['subject_code', '과목코드'],
    ['section', '분반'],
    ['subject_name', '과목명'],
    ['category', '이수구분'],
    ['credit_hours', '학점'],
    ['target_year', '대상학년'],
    ['target_grade', '대상학년'],
    ['professor', '교수'],
    ['capacity', '정원'],
    ['enrolled', '수강인원'],
    ['grading_method', '성적평가'],
    ['eval_type', '평가방식'],
    ['class_mode', '수업방식'],
    ['dept_name', '학과'],
    ['day_of_week', '요일'],
    ['start_time', '시작'],
    ['end_time', '종료'],
    ['table_schedule', '시간표'],
    ['classroom', '강의실'],
    ['prereq_subject_codes', '선수과목 코드'],
    ['prereq_subject_names', '선수과목명'],
    // Phase 8-19: this database's extension fields, with their labels.
    ...extraLabels,
  ]

  return labels.flatMap(([key, label]) => {
    const value = row[key]
    if (value === undefined || value === null || value === '') return []
    return [{ label, value: String(value) }]
  })
}

function detailTitle(course: Course) {
  const details = course.details ?? []
  if (details.length === 0) {
    return [
      course.name,
      `교수: ${course.professor}`,
      `시간: ${course.timeText}`,
      course.locationText ? `강의실: ${course.locationText}` : null,
    ]
      .filter(Boolean)
      .join('\n')
  }
  return details.map((detail) => `${detail.label}: ${detail.value}`).join('\n')
}

function normalizeDay(value: string): Course['slots'][number]['day'] | null {
  return normalizeCourseDay(value)
}

function parseClockHour(value: string) {
  return parseCourseClockHour(value)
}

function parseSlots(timeText: string): Course['slots'] {
  return parseCourseSlotsFromText(timeText)
}

function readScheduleSlots(row: QueryRow): Course['slots'] {
  const rawSchedule = row.schedule
  if (!Array.isArray(rawSchedule)) return []

  return rawSchedule
    .map((slot): Course['slots'][number] | null => {
      if (!slot || typeof slot !== 'object' || Array.isArray(slot)) return null
      const scheduleSlot = slot as QueryScheduleSlot
      const dayText = readString(scheduleSlot as QueryRow, ['day'])
      const startText = readString(scheduleSlot as QueryRow, ['start'])
      const endText = readString(scheduleSlot as QueryRow, ['end'])
      const day = normalizeDay(dayText)
      const startHour = parseClockHour(startText)
      const endHour = parseClockHour(endText)
      if (!day || startHour === null || endHour === null || endHour <= startHour) {
        return null
      }
      return { day, startHour, endHour }
    })
    .filter((slot): slot is Course['slots'][number] => slot !== null)
}

function readFieldSlot(row: QueryRow): Course['slots'] {
  const dayText = readString(row, ['day_of_week', 'day'])
  const startText = readString(row, ['start_time', 'start'])
  const endText = readString(row, ['end_time', 'end'])
  const day = normalizeDay(dayText)
  const startHour = parseClockHour(startText)
  const endHour = parseClockHour(endText)
  if (!day || startHour === null || endHour === null || endHour <= startHour) {
    return []
  }
  return [{ day, startHour, endHour }]
}

function rowToCourse(row: QueryRow, index: number, extraLabels: Array<[string, string]> = []): Course {
  const subjectCode = readString(row, [
    'subject_code',
    'course_code',
    'course_id',
    'id',
    'code',
  ])
  const section = readString(row, ['section', 'class_no', 'division'])
  const id = subjectCode
    ? section
      ? `${subjectCode}-${section}`
      : subjectCode
    : `DB-${index + 1}`
  const name =
    readString(row, ['subject_name', 'course_name', 'name', 'title']) ||
    `강좌 ${index + 1}`
  const professor =
    readString(row, ['professor', 'instructor', 'teacher', '교수']) || '-'
  const credits = readNumber(row, ['credit_hours', 'credits', 'credit', '학점']) ?? 0
  const timeText =
    readString(row, ['lecture_time', 'table_schedule', 'time_text', 'time', '시간']) ||
    structuredTimeText(readString(row, ['day_of_week', 'day']), readString(row, ['start_time', 'start']), readString(row, ['end_time', 'end']))
  const slots = [
    ...readScheduleSlots(row),
    ...readFieldSlot(row),
    ...parseSlots(timeText),
  ]
  const locationText = readString(row, ['classroom', 'room', 'locationText', 'location'])
  const capacity = readNumber(row, ['capacity', '정원'])
  const enrolled = readNumber(row, ['enrolled', 'registered', '수강인원'])
  const status =
    capacity !== undefined && enrolled !== undefined && capacity > 0 && enrolled >= capacity
      ? 'Closed'
      : 'Open'

  return {
    id,
    name,
    departmentName: readString(row, ['dept_name', 'departmentName', 'department_name']),
    professor,
    credits,
    status,
    capacity,
    enrolled,
    timeText,
    locationText,
    slots,
    prerequisiteCourseCodes: readStringList(row, ['prereq_subject_codes']),
    prerequisiteCourseNames: readStringList(row, ['prereq_subject_names']),
    details: readDetails(row, extraLabels),
  }
}

function slotKey(slot: Course['slots'][number]) {
  return `${slot.day}-${slot.startHour}-${slot.endHour}`
}

const weekdayOrder: Record<Course['slots'][number]['day'], number> = {
  Mon: 0,
  Tue: 1,
  Wed: 2,
  Thu: 3,
  Fri: 4,
  Sat: 5,
}
const timePartDayPattern =
  /(monday|mon|tuesday|tues|tue|wednesday|wed|thursday|thurs|thur|thu|friday|fri|saturday|sat|월요일|화요일|수요일|목요일|금요일|토요일|월|화|수|목|금|토)/i

function sortSlots(slots: Course['slots']) {
  return [...slots].sort(
    (a, b) =>
      weekdayOrder[a.day] - weekdayOrder[b.day] ||
      a.startHour - b.startHour ||
      a.endHour - b.endHour,
  )
}

function timePartSortKey(value: string) {
  const dayMatch = value.match(timePartDayPattern)
  const day = dayMatch ? normalizeCourseDay(dayMatch[0]) : normalizeCourseDay(value)
  const hour = parseCourseClockHour(value) ?? Number.POSITIVE_INFINITY
  return {
    dayOrder: day ? weekdayOrder[day] : Number.POSITIVE_INFINITY,
    hour,
    value,
  }
}

function sortTimeParts(parts: string[]) {
  return [...parts].sort((a, b) => {
    const left = timePartSortKey(a)
    const right = timePartSortKey(b)
    return (
      left.dayOrder - right.dayOrder ||
      left.hour - right.hour ||
      left.value.localeCompare(right.value)
    )
  })
}

function mergeCourses(existing: Course, next: Course): Course {
  const seenSlots = new Set(existing.slots.map(slotKey))
  const mergedSlots = [...existing.slots]
  for (const slot of next.slots) {
    const key = slotKey(slot)
    if (seenSlots.has(key)) continue
    seenSlots.add(key)
    mergedSlots.push(slot)
  }

  const timeParts = existing.timeText
    .split(/\s*,\s*/)
    .filter(Boolean)
  for (const part of next.timeText.split(/\s*,\s*/).filter(Boolean)) {
    if (part === TIME_TBA_TEXT || timeParts.includes(part)) continue
    timeParts.push(part)
  }
  const sortedTimeParts = sortTimeParts(timeParts)

  return {
    ...existing,
    timeText:
      sortedTimeParts.length > 0 ? sortedTimeParts.join(', ') : existing.timeText,
    locationText: mergeTextList(existing.locationText, next.locationText),
    slots: sortSlots(mergedSlots),
    prerequisiteCourseCodes: mergeStringArrays(
      existing.prerequisiteCourseCodes,
      next.prerequisiteCourseCodes,
    ),
    prerequisiteCourseNames: mergeStringArrays(
      existing.prerequisiteCourseNames,
      next.prerequisiteCourseNames,
    ),
    details: mergeDetails(existing.details, next.details),
  }
}

function mergeDetails(existing?: CourseDetail[], next?: CourseDetail[]) {
  const merged: CourseDetail[] = []
  const byLabel = new Map<string, CourseDetail>()

  for (const detail of [...(existing ?? []), ...(next ?? [])]) {
    if (!detail.label || detail.value === '') continue
    const current = byLabel.get(detail.label)
    if (current) {
      current.value = mergeTextList(current.value, detail.value) ?? current.value
    } else {
      const mergedDetail = { ...detail }
      merged.push(mergedDetail)
      byLabel.set(detail.label, mergedDetail)
    }
  }

  return merged
}

function mergeStringArrays(existing?: string[], next?: string[]) {
  const merged = [...(existing ?? [])]
  for (const item of next ?? []) {
    if (!merged.includes(item)) merged.push(item)
  }
  return merged
}

function mergeTextList(existing?: string, next?: string) {
  const parts = (existing ?? '').split(/\s*,\s*/).filter(Boolean)
  for (const part of (next ?? '').split(/\s*,\s*/).filter(Boolean)) {
    if (!parts.includes(part)) parts.push(part)
  }
  return parts.join(', ') || undefined
}

function rowsToCourses(rows: unknown[], extraLabels: Array<[string, string]> = []) {
  const courses = new Map<string, Course>()
  rows.filter(isQueryRow).forEach((row, index) => {
    const course = rowToCourse(row, index, extraLabels)
    const existing = courses.get(course.id)
    courses.set(course.id, existing ? mergeCourses(existing, course) : course)
  })
  return [...courses.values()]
}

const LEGACY_CHAT_TARGET: ChatTarget = { kind: 'legacy' }

function chatTargetKey(target: ChatTarget) {
  if (target.kind === 'queryir') return `queryir:${target.databaseId}`
  if (target.kind === 'unsupported') return `unsupported:${target.label}`
  return 'legacy'
}

function chatTargetCaption(target: ChatTarget) {
  // Phase 9-38: 학생 화면에는 구현 경로를 노출하지 않는다.
  if (target.kind === 'queryir') return `${target.label}`
  if (target.kind === 'unsupported') return `${target.label} · 챗봇 미지원`
  return '기본 DB · 기존 챗봇'
}

export default function ChatPopup({
  open,
  onClose,
  chatTarget = LEGACY_CHAT_TARGET,
  userStudentNo,
  selectedCourses,
  selectedIds,
  completedCourseCodes,
  onAddCourse,
  onRemoveCourse,
  onReplaceCourse,
  onOpenExpandedTimetable,
  modelRevision = 0,
}: {
  open: boolean
  onClose: () => void
  chatTarget?: ChatTarget
  userStudentNo?: string
  selectedCourses: Course[]
  selectedIds: Set<string>
  completedCourseCodes: Set<string> | null
  onAddCourse: (course: Course) => void
  onRemoveCourse: (courseId: string) => void
  onReplaceCourse: (course: Course) => void
  onOpenExpandedTimetable: () => void
  modelRevision?: number
}) {
  const [mounted, setMounted] = useState(false)
  const [visible, setVisible] = useState(false)
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      id: makeId('sys'),
      role: 'system',
      text: '안녕하세요! 원하는 과목 키워드를 입력하면 검색 결과와 시간표를 보여드릴게요.',
    },
  ])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [lastSql, setLastSql] = useState('')
  const [lastWarning, setLastWarning] = useState('')
  const [lastError, setLastError] = useState('')
  const [dbCourses, setDbCourses] = useState<Course[] | null>(null)
  const [hoveredCourse, setHoveredCourse] = useState<Course | null>(null)
  const [excludeCompletedCourses, setExcludeCompletedCourses] = useState(false)
  const [guidelineOpen, setGuidelineOpen] = useState(true)
  const [sqlOpen, setSqlOpen] = useState(true)
  const [lastUnderstood, setLastUnderstood] = useState('')
  // Phase 9-08: the QueryIR chat's model choices (null until loaded) and the pick.
  const [chatModels, setChatModels] = useState<ChatModelOption[] | null>(null)
  const [chatModel, setChatModel] = useState<string | null>(null)
  const isQueryIr = chatTarget.kind === 'queryir'
  const targetKey = chatTargetKey(chatTarget)
  const targetKeyRef = useRef(targetKey)

  const scrollRef = useRef<HTMLDivElement | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  useEffect(() => {
    if (!open) {
      setVisible(false)
      const t = window.setTimeout(() => setMounted(false), 180)
      return () => window.clearTimeout(t)
    }
    setMounted(true)
    const t = window.setTimeout(() => setVisible(true), 10)
    return () => window.clearTimeout(t)
  }, [open])

  useEffect(() => {
    if (!mounted) return
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [mounted, onClose])

  // A different database answers differently: drop the previous answer and say so.
  // Layout effect: the ref flips during commit, before any pending answer resolves.
  useLayoutEffect(() => {
    if (targetKeyRef.current === targetKey) return
    targetKeyRef.current = targetKey
    abortRef.current?.abort()
    abortRef.current = null
    setLoading(false)
    setDbCourses(null)
    setLastSql('')
    setLastWarning('')
    setLastError('')
    setLastUnderstood('')
    // Restoring the stored selection on page load is not a change the user made.
    setMessages((prev) =>
      prev.some((message) => message.role === 'user')
        ? [
            ...prev,
            {
              id: makeId('sys'),
              role: 'system',
              text: `질의 대상이 바뀌었습니다: ${chatTargetCaption(chatTarget)}`,
            },
          ]
        : prev,
    )
  }, [targetKey, chatTarget])

  useEffect(() => {
    if (!mounted || !isQueryIr || chatModels !== null) return
    const controller = new AbortController()
    fetchChatModels((url, init) => fetch(url, init), { signal: controller.signal })
      .then((listing) => {
        setChatModels(listing.models)
        setChatModel((current) =>
          current !== null && listing.models.some((option) => option.id === current) ? current : listing.defaultId,
        )
      })
      .catch(() => {
        // Without a listing the server's default model answers.
        if (!controller.signal.aborted) setChatModels([])
      })
    return () => controller.abort()
  }, [mounted, isQueryIr, chatModels])

  useEffect(() => {
    if (!mounted || !isQueryIr || modelRevision === 0) return
    const controller = new AbortController()
    fetchChatModels((url, init) => fetch(url, init), { signal: controller.signal })
      .then((listing) => {
        setChatModels(listing.models)
        setChatModel((current) => current !== null && listing.models.some((option) => option.id === current) ? current : listing.defaultId)
      })
      .catch(() => {
        if (controller.signal.aborted) return
        // Luna 9-31: a removed model must not stay selected; the server's default answers.
        setChatModels([])
        setChatModel(null)
        setLastError('챗봇 모델 목록을 새로 고치지 못했습니다. 기본 모델로 답합니다.')
      })
    return () => controller.abort()
  }, [mounted, isQueryIr, modelRevision])

  function changeChatModel(id: string) {
    const option = chatModels?.find((item) => item.id === id)
    if (option === undefined || id === chatModel) return
    setChatModel(id)
    setMessages((prev) => [
      ...prev,
      { id: makeId('sys'), role: 'system', text: `다음 질문부터 이 모델이 답합니다: ${describeChatModel(option)}` },
    ])
  }

  useEffect(() => {
    if (!mounted) return
    const el = scrollRef.current
    if (!el) return
    el.scrollTop = el.scrollHeight
  }, [mounted, messages.length])

  const results = useMemo(() => {
    if (dbCourses === null) return []
    return dbCourses
  }, [dbCourses])
  const hoveredConflictIds = useMemo(
    () => getConflictingCourseIds(hoveredCourse, selectedCourses),
    [hoveredCourse, selectedCourses],
  )

  function cancelQuery() {
    abortRef.current?.abort()
    abortRef.current = null
    setLoading(false)
    setLastError('')
    setLastWarning('')
    setDbCourses(null)
    setMessages((prev) => [
      ...prev,
      {
        id: makeId('sys'),
        role: 'system',
        text: '질의를 취소하셨습니다. 다시 질문해 주세요!',
      },
    ])
  }

  async function send() {
    if (loading) {
      cancelQuery()
      return
    }

    const text = input.trim()
    if (!text) return
    setInput('')
    setLastError('')
    setLastWarning('')
    setLastUnderstood('')
    setDbCourses(null)

    if (chatTarget.kind === 'unsupported') {
      setLastError(
        `${chatTarget.label}은(는) AI 수강 도우미를 지원하지 않습니다. DB 변경에서 다른 DB를 골라 주세요.`,
      )
      return
    }

    if (chatTarget.kind === 'queryir') {
      await sendQueryIr(text, chatTarget.databaseId)
      return
    }

    if (excludeCompletedCourses && !userStudentNo) {
      setLastError('수강 이력 과목 제외는 로그인 후 사용할 수 있습니다.')
      return
    }

    setMessages((prev) => [
      ...prev,
      { id: makeId('usr'), role: 'user', text },
    ])

    setLoading(true)
    const controller = new AbortController()
    abortRef.current = controller
    const requestKey = targetKeyRef.current

    try {
      const token = getAccessToken()
      const res = await fetch('/api/v1/chat/query', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({
          query: text,
          excludeCompletedCourses,
        }),
        signal: controller.signal,
      })
      const payload = (await res.json()) as QueryApiResponse
      if (controller.signal.aborted || targetKeyRef.current !== requestKey) return

      if (!res.ok || payload.error) {
        const errorText =
          payload.error ??
          `요청 처리에 실패했습니다. (${res.status})`
        setLastError(errorText)
        setDbCourses(null)
        setMessages((prev) => [
          ...prev,
          {
            id: makeId('sys'),
            role: 'system',
            text: `요청 실패: ${errorText}`,
          },
        ])
        return
      }

      setLastSql(payload.sql ?? '')
      setLastWarning(payload.warning ?? '')

      const nextDbCourses =
        !payload.warning && Array.isArray(payload.data)
          ? rowsToCourses(payload.data)
          : null
      setDbCourses(nextDbCourses)
      const summary = payload.warning
        ? 'SQL 생성 완료 (DB 미연결로 결과 조회는 생략되었습니다).'
        : `조회 완료: 강좌 ${nextDbCourses?.length ?? 0}개를 찾았습니다.`

      setMessages((prev) => [
        ...prev,
        {
          id: makeId('sys'),
          role: 'system',
          text: summary,
        },
      ])
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        return
      }
      if (controller.signal.aborted || targetKeyRef.current !== requestKey) return
      const errorText = '백엔드 연결에 실패했습니다. 서버 실행 상태를 확인해주세요.'
      setLastError(errorText)
      setDbCourses(null)
      setMessages((prev) => [
        ...prev,
        {
          id: makeId('sys'),
          role: 'system',
          text: `요청 실패: ${errorText}`,
        },
      ])
    } finally {
      if (abortRef.current === controller) {
        abortRef.current = null
        setLoading(false)
      }
    }
  }

  async function sendQueryIr(text: string, databaseId: string) {
    setMessages((prev) => [...prev, { id: makeId('usr'), role: 'user', text }])
    setLastSql('')
    setLoading(true)
    const controller = new AbortController()
    abortRef.current = controller
    const requestKey = targetKeyRef.current
    try {
      const answer = await postChatQuery(
        (url, init) => fetch(url, init),
        databaseId,
        text,
        { signal: controller.signal, ...(chatModel === null ? {} : { model: chatModel }) },
      )
      // The user may have switched databases while the model was answering.
      if (controller.signal.aborted || targetKeyRef.current !== requestKey) return
      const nextDbCourses = answer.status === 'SUCCEEDED'
        ? rowsToCourses(
            chatRowsToLegacyRows(answer.rows),
            (answer.extensions ?? []).map((item): [string, string] => [item.key, item.label]),
          )
        : null
      setDbCourses(nextDbCourses)
      setLastUnderstood(answer.status === 'SUCCEEDED' ? describeUnderstood(answer.filters) : '')
      setLastSql(answer.status === 'SUCCEEDED' ? (answer.sql ?? '') : '')
      if (answer.status !== 'SUCCEEDED') setLastWarning(describeAnswer(answer))
      else setLastWarning(answerWarning(answer))
      setMessages((prev) => [...prev, { id: makeId('sys'), role: 'system', text: describeAnswer(answer, { courseCount: nextDbCourses?.length ?? 0 }) }])
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') return
      if (controller.signal.aborted || targetKeyRef.current !== requestKey) return
      const errorText = chatErrorMessage(error)
      setLastError(errorText)
      setDbCourses(null)
      setMessages((prev) => [...prev, { id: makeId('sys'), role: 'system', text: `요청 실패: ${errorText}` }])
    } finally {
      if (abortRef.current === controller) {
        abortRef.current = null
        setLoading(false)
      }
    }
  }

  if (!mounted) return null

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-end p-4 sm:p-6">
      <button
        type="button"
        aria-label="도우미 닫기"
        onClick={onClose}
        className={cn(
          'absolute inset-0 bg-slate-900/30 backdrop-blur-[1px] transition-opacity duration-200',
          visible ? 'opacity-100' : 'opacity-0',
        )}
      />

      <div
        className={cn(
          'relative flex max-h-[calc(100vh-2rem)] w-full max-w-md flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xl transition duration-200',
          visible ? 'opacity-100 scale-100' : 'opacity-0 scale-95',
        )}
        role="dialog"
        aria-modal="true"
        aria-label="AI 수강 도우미"
      >
        <div className="shrink-0 flex items-center justify-between border-b border-slate-200 bg-white px-4 py-3">
          <div>
            <div className="text-sm font-semibold text-slate-900">
              AI 수강 도우미
            </div>
            <div className="text-xs text-slate-500">
              강좌 검색 · 시간표 미리보기
            </div>
            <div
              className={cn(
                'mt-0.5 text-[11px] font-semibold',
                chatTarget.kind === 'queryir'
                  ? 'text-violet-700'
                  : chatTarget.kind === 'unsupported'
                    ? 'text-amber-700'
                    : 'text-slate-500',
              )}
              data-testid="chat-target"
            >
              {chatTargetCaption(chatTarget)}
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="inline-flex h-9 w-9 items-center justify-center rounded-lg text-slate-500 transition hover:bg-slate-100 hover:text-slate-700"
            aria-label="Close"
          >
            <span className="text-lg leading-none">×</span>
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="px-4 pt-4">
            <div
              ref={scrollRef}
              className="h-52 overflow-y-auto rounded-xl border border-slate-200 bg-slate-50 p-3"
            >
              <div className="space-y-2">
                {messages.map((m) => (
                  <div
                    key={m.id}
                    className={cn(
                      'flex',
                      m.role === 'user' ? 'justify-end' : 'justify-start',
                    )}
                  >
                    <div
                      className={cn(
                        'max-w-[85%] rounded-2xl px-3 py-2 text-sm shadow-sm',
                        m.role === 'user'
                          ? 'bg-blue-600 text-white'
                          : 'bg-white text-slate-800 ring-1 ring-slate-200',
                      )}
                    >
                      {m.text}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>

          <div className="px-4 py-3">
            <label className="mb-2 flex items-center gap-2 text-xs font-semibold text-slate-700">
              <input
                type="checkbox"
                checked={chatTarget.kind === 'legacy' && excludeCompletedCourses}
                onChange={(event) => setExcludeCompletedCourses(event.target.checked)}
                disabled={loading || chatTarget.kind !== 'legacy'}
                className="h-4 w-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500"
              />
              수강 이력 존재 과목 제외
              {chatTarget.kind === 'queryir' ? (
                <span className="font-normal text-slate-400">(이 DB에서는 미지원)</span>
              ) : null}
            </label>
            {isQueryIr && chatModels !== null && chatModels.length > 1 ? (
              <label className="mb-2 flex items-center gap-2 text-xs font-semibold text-slate-700">
                <span className="shrink-0">모델</span>
                <select
                  value={chatModel ?? ''}
                  onChange={(event) => changeChatModel(event.target.value)}
                  disabled={loading}
                  data-testid="chat-model"
                  className="h-8 min-w-0 flex-1 rounded-lg border border-slate-200 bg-white px-2 text-xs font-normal text-slate-900 shadow-sm outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100 disabled:opacity-60"
                >
                  {chatModels.map((option) => (
                    <option key={option.id} value={option.id}>
                      {describeChatModel(option)}
                    </option>
                  ))}
                </select>
              </label>
            ) : null}
            {loading ? (
              <div
                className="mb-2 flex items-center gap-2 rounded-lg border border-blue-100 bg-blue-50 px-3 py-2 text-xs font-medium text-blue-700"
                role="status"
                aria-live="polite"
              >
                <LoadingSpinner className="h-3.5 w-3.5" />
                답변을 생성하고 있습니다...
              </div>
            ) : null}
            <div className="flex items-center gap-2">
              <input
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') send()
                }}
                placeholder="예: 컴퓨터융합학부 전공 과목 보여줘"
                disabled={loading}
                className="h-10 w-full rounded-xl border border-slate-200 bg-white px-3 text-sm text-slate-900 shadow-sm outline-none transition focus:border-blue-400 focus:ring-4 focus:ring-blue-100"
              />
              <button
                type="button"
                onClick={send}
                className={cn(
                  'h-10 shrink-0 rounded-xl px-4 text-sm font-semibold text-white shadow-sm transition',
                  loading
                    ? 'bg-rose-600 hover:bg-rose-700 active:bg-rose-700'
                      : 'bg-blue-600 hover:bg-blue-700 active:bg-blue-700',
                )}
              >
                <span className="inline-flex items-center gap-2">
                  {loading ? <LoadingSpinner /> : null}
                  {loading ? '취소' : '보내기'}
                </span>
              </button>
            </div>
            <div className="mt-2 rounded-lg border border-slate-200 bg-white">
              <button
                type="button"
                onClick={() => setGuidelineOpen((open) => !open)}
                className="flex w-full items-center justify-between gap-3 px-3 py-2 text-left text-xs font-semibold text-slate-700 transition hover:bg-slate-50"
                aria-expanded={guidelineOpen}
              >
                <span>질문 작성 가이드</span>
                <span className="text-slate-400">
                  {guidelineOpen ? '접기' : '펼치기'}
                </span>
              </button>
              {guidelineOpen ? (
                <div className="border-t border-slate-100 px-3 py-2 text-xs leading-5 text-slate-600">
                  <ul className="list-disc space-y-1 pl-4">
                    <li>과목명은 가능한 한 정확히 입력해 주세요. 줄임말이나 오타가 있으면 결과가 잘 나오지 않을 수 있습니다.</li>
                    <li>과목명과 학과명은 공식 명칭과 띄어쓰기에 가깝게 입력할수록 정확합니다. 띄어쓰기가 많이 다르면 결과가 누락될 수 있습니다.</li>
                    <li>학과, 학년, 이수구분, 요일처럼 원하는 조건을 함께 적으면 더 정확합니다.</li>
                    <li>
                      {chatTarget.kind === 'queryir'
                        ? QUERYIR_TIME_GUIDE
                        : '시간은 24시간 형식으로 적어 주세요. 예: 오전 2시는 2시, 오후 2시는 14시.'}
                    </li>
                    <li>현재는 SQL 질의문으로 조회 가능한 조건만 처리합니다.</li>
                    <li>현재 시간표의 빈 시간에 맞는 강의 추천, 남은 학점에 따른 자동 추천처럼 개인 상황을 해석하는 요청은 아직 지원하지 않습니다.</li>
                  </ul>
                  <div className="mt-2 rounded-md border border-blue-100 bg-blue-50 px-3 py-2 text-blue-800">
                    <div className="mb-0.5 text-[11px] font-semibold text-blue-700">
                      예시
                    </div>
                    <div className="font-medium">
                      컴퓨터융합학부 3학년 전공핵심 중 목요일 14시 강의 보여줘.
                    </div>
                  </div>
                </div>
              ) : null}
            </div>
            {lastWarning && (
              <div className="mt-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                {lastWarning}
              </div>
            )}
            {lastError && (
              <div className="mt-2 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700">
                {lastError}
              </div>
            )}
            {lastUnderstood && (
              <div
                className="mt-2 rounded-lg border border-violet-200 bg-violet-50 px-3 py-2 text-xs text-violet-800"
                data-testid="chat-understood"
              >
                <span className="font-semibold">이해한 조건</span>: {lastUnderstood}
              </div>
            )}
            {lastSql && (
              <div className="mt-2 rounded-lg border border-slate-200 bg-slate-50">
                <button
                  type="button"
                  onClick={() => setSqlOpen((open) => !open)}
                  className="flex w-full items-center justify-between gap-3 px-3 py-2 text-left text-[11px] font-semibold text-slate-600 transition hover:bg-slate-100"
                  aria-expanded={sqlOpen}
                >
                  <span>
                    {chatTarget.kind === 'queryir'
                      ? 'Executed SQL (QueryIR · 값은 보기용으로 채워 넣음)'
                      : 'Generated SQL'}
                  </span>
                  <span className="text-slate-400">
                    {sqlOpen ? '접기' : '펼치기'}
                  </span>
                </button>
                {sqlOpen ? (
                  <pre className="border-t border-slate-200 px-3 py-2 overflow-x-auto whitespace-pre-wrap break-all text-[11px] text-slate-700">
                    {lastSql}
                  </pre>
                ) : null}
              </div>
            )}
          </div>

          {dbCourses !== null && (
            <div className="border-t border-slate-200 px-4 py-3">
              <div className="flex items-center justify-between">
                <div className="text-xs font-semibold text-slate-700">
                  검색 결과
                </div>
                <div className="text-xs text-slate-500">
                  {results.length}개
                </div>
              </div>
              <div className="mt-2 max-h-72 min-h-36 space-y-2 overflow-y-auto pr-1">
                {results.map((c) => {
                  const already = selectedIds.has(c.id)
                  const hasConflict = getConflictingCourseIds(c, selectedCourses).size > 0
                  const closed = c.status === 'Closed'
                  const unmetPrerequisite = hasUnmetPrerequisite(c, completedCourseCodes)
                  return (
                    <div
                      key={c.id}
                      onMouseEnter={() => setHoveredCourse(c)}
                      onMouseLeave={() => setHoveredCourse(null)}
                      className="flex items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white px-3 py-2"
                      title={detailTitle(c)}
                    >
                      <div className="min-w-0">
                        {c.departmentName ? (
                          <div
                            className={cn(
                              'mb-1 truncate text-[11px] font-semibold',
                              departmentColor({
                                selected: already,
                                hasConflict,
                                closed,
                              }),
                              unmetPrerequisite && !already && 'text-violet-700',
                            )}
                          >
                            {c.departmentName}
                          </div>
                        ) : null}
                        <div className="truncate text-sm font-semibold text-slate-900">
                          {c.name} - {c.id}
                        </div>
                        <div className="truncate text-xs text-slate-500">
                          {c.professor} · {c.timeText}
                          {c.locationText ? ` · 강의실: ${c.locationText}` : ''}
                        </div>
                        <div className="mt-1 flex flex-wrap gap-1">
                          {closed ? (
                            <span className="inline-flex rounded-full bg-slate-100 px-2 py-0.5 text-[11px] font-semibold text-slate-700 ring-1 ring-slate-200">
                              정원 주의
                            </span>
                          ) : null}
                          {unmetPrerequisite ? (
                            <span className="inline-flex rounded-full bg-violet-50 px-2 py-0.5 text-[11px] font-semibold text-violet-700 ring-1 ring-violet-200">
                              선수과목 주의
                            </span>
                          ) : null}
                        </div>
                      </div>
                      <button
                        type="button"
                        onMouseEnter={() => setHoveredCourse(c)}
                        onFocus={() => setHoveredCourse(c)}
                        onBlur={() => setHoveredCourse(null)}
                        onClick={() => {
                          if (already) {
                            onRemoveCourse(c.id)
                          } else if (hasConflict) {
                            onReplaceCourse(c)
                          } else {
                            onAddCourse(c)
                          }
                        }}
                        className={cn(
                          'h-9 shrink-0 rounded-lg px-3 text-xs font-semibold transition',
                          already
                            ? 'bg-rose-50 text-rose-700 hover:bg-rose-100 hover:text-rose-800'
                            : closed
                              ? 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                            : unmetPrerequisite
                              ? 'bg-violet-50 text-violet-700 hover:bg-violet-100'
                            : hasConflict
                              ? 'bg-amber-100 text-amber-800 hover:bg-amber-200'
                              : 'bg-blue-50 text-blue-700 hover:bg-blue-100',
                        )}
                      >
                        {already ? '빼기' : hasConflict ? '바꾸기' : '담기'}
                      </button>
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          <div className="border-t border-slate-200 bg-white px-4 py-4">
            <div className="mb-2 flex items-center justify-between">
              <div className="text-xs font-semibold text-slate-700">
                미리 보는 시간표
              </div>
              <div className="text-xs text-slate-500">
                {selectedCourses.length}개 담음
              </div>
            </div>
            <Timetable
              courses={selectedCourses}
              variant="mini"
              overlayCourse={hoveredCourse}
              conflictIds={hoveredConflictIds}
              onClick={onOpenExpandedTimetable}
            />
          </div>
        </div>
      </div>
    </div>
  )
}
