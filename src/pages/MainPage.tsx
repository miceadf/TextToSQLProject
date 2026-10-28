import { adminStyles, disabledTitle } from '../adaptive/adminStyles.ts';
import { useEffect, useMemo, useRef, useState } from 'react'
import ChatPopup from '../components/ChatPopup'
import CourseTable from '../components/CourseTable'
import ExpandedTimetableModal from '../components/ExpandedTimetableModal'
import Timetable from '../components/Timetable'
import {
  CatalogClientError,
  getCatalog,
  listDatabases,
} from '../adaptive/catalogClient.ts'
import type { CatalogFetchLike } from '../adaptive/catalogClient.ts'
import {
  CATALOG_GENERIC_MESSAGE,
  CATALOG_CONTRACT_MESSAGE,
  CATALOG_TRANSPORT_MESSAGE,
  CATALOG_UNAVAILABLE_MESSAGE,
  CATALOG_VIA_CHAT_MESSAGE,
  LEGACY_SELECTION_ID,
  mainScreenDatabases,
  STORAGE_KEY,
  catalogToCourses,
  databaseLabel,
  isDatabaseId,
  DATABASE_ID_INPUT_MESSAGE,
  registrationDatabaseIdMessage,
  readStoredDatabaseId,
  shouldResetCourseUi,
  writeStoredDatabaseId,
  type RegistryDatabase,
} from '../adaptive/databases.ts'
import {
  PROPOSER_LABELS,
  SQLITE_LABEL_INPUT_MESSAGE,
  UPLOAD_PENDING_NOTICE,
  canStartUpload,
  toUploadMessage,
  uploadSqliteDatabase,
  type SqliteUploadStatus,
  type SqliteUploadSuccess,
} from '../adaptive/sqliteUpload.ts'
import MappingReviewPanel from '../adaptive/MappingReviewPanel.tsx'
import type { RegisteredPostgres } from '../adaptive/postgresRegistration.ts'
import PostgresRegistrationPanel from '../adaptive/PostgresRegistrationPanel.tsx'
import CustomChatModelsPanel from '../adaptive/CustomChatModelsPanel.tsx'
import { chatTargetFor } from '../adaptive/chatQuery.ts'
import SchemaComparePanel from '../adaptive/SchemaComparePanel.tsx'
import { coursesApi } from '../services/api'
import type { Course } from '../types/course'
import { isStaleResponse, nextOperation } from '../adaptive/state.ts'
import { cn } from '../utils/cn'
import { courseColor } from '../utils/courseColors'
import { apiCourseToCourse } from '../utils/courseMapper'
import {
  extractCompletedCourseCodes,
  formatPrerequisites,
  getUnmetPrerequisiteCodes,
  getUnmetPrerequisiteNames,
} from '../utils/prerequisites'
import { getConflictingCourseIds } from '../utils/schedule'

// Phase 9-38: PostgreSQL-only scope per ADR-089.
const SHOW_SQLITE_UPLOAD = false

function ChatIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
    >
      <path
        d="M7.5 20.5c1.6.5 3.2.8 4.5.8 5.2 0 9-3.6 9-8.4S17.2 4.5 12 4.5 3 8.1 3 12.9c0 1.9.6 3.6 1.8 5.1l-.6 3.2 3.1-.9c.4.2.8.4 1.2.6Z"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinejoin="round"
      />
      <path
        d="M7.7 12.7h.8m3.1 0h.8m3.1 0h.8"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
      />
    </svg>
  )
}

type MainPageProps = {
  userStudentNo?: string
  userName?: string
  completedCourses?: unknown[]
  onLoginClick: () => void
  onLogout: () => void
}

function toCatalogMessage(error: unknown): string {
  if (error instanceof CatalogClientError) {
    if (error.kind === 'transport') return CATALOG_TRANSPORT_MESSAGE
    if (error.kind === 'contract') return CATALOG_CONTRACT_MESSAGE
    if (error.serverCode === 'SYNTHETIC_CATALOG_CONTEXT_UNAVAILABLE') return CATALOG_UNAVAILABLE_MESSAGE
    if (error.serverCode === 'SYNTHETIC_CATALOG_UNSUPPORTED') return CATALOG_VIA_CHAT_MESSAGE
    return CATALOG_GENERIC_MESSAGE
  }
  return CATALOG_GENERIC_MESSAGE
}

function buildCoursePrompt(
  course: Course,
  action: 'add' | 'replace',
  completedCourseCodes: Set<string> | null,
) {
  const closed =
    typeof course.capacity === 'number' &&
    typeof course.enrolled === 'number' &&
    course.capacity > 0 &&
    course.enrolled >= course.capacity
  const unmetPrerequisiteCodes = getUnmetPrerequisiteCodes(course, completedCourseCodes)

  if (!closed && unmetPrerequisiteCodes.length === 0) return null

  return {
    course,
    action,
    closed,
    unmetPrerequisiteCodes,
    unmetPrerequisiteNames: getUnmetPrerequisiteNames(course, unmetPrerequisiteCodes),
  }
}

export default function MainPage({
  userStudentNo,
  userName,
  completedCourses,
  onLoginClick,
  onLogout,
}: MainPageProps) {
  const [allCourses, setAllCourses] = useState<Course[]>([])
  const [courseTotalCount, setCourseTotalCount] = useState<number | undefined>(undefined)
  const [popupOpen, setPopupOpen] = useState(false)
  const [expandedOpen, setExpandedOpen] = useState(false)
  const [selectedCourses, setSelectedCourses] = useState<Course[]>([])
  const [hoveredCourse, setHoveredCourse] = useState<Course | null>(null)
  const [courseSearch, setCourseSearch] = useState('')
  const [coursePrompt, setCoursePrompt] = useState<{
    course: Course
    action: 'add' | 'replace'
    closed: boolean
    unmetPrerequisiteCodes: string[]
    unmetPrerequisiteNames: Array<string | undefined>
  } | null>(null)
  const [activeDbId, setActiveDbId] = useState<string>(LEGACY_SELECTION_ID)
  const [registry, setRegistry] = useState<RegistryDatabase[]>([])
  const [registryLoading, setRegistryLoading] = useState(true)
  const [registryError, setRegistryError] = useState<string | null>(null)
  const [registryWarning, setRegistryWarning] = useState<string | null>(null)
  const [dbAdminTab, setDbAdminTab] = useState<'use' | 'register' | 'review' | 'chat-models'>('use')
  const [chatModelRevision, setChatModelRevision] = useState(0)
  const [pgRegistrations, setPgRegistrations] = useState<RegisteredPostgres[]>([])
  const [dbModalOpen, setDbModalOpen] = useState(false)
  const [pendingDbId, setPendingDbId] = useState<string>(LEGACY_SELECTION_ID)
  const [catalogLoading, setCatalogLoading] = useState(true)
  const [catalogError, setCatalogError] = useState<string | null>(null)
  const [catalogRetry, setCatalogRetry] = useState(0)
  const [registryRetry, setRegistryRetry] = useState(0)
  const [uploadDbId, setUploadDbId] = useState('')
  const [uploadLabel, setUploadLabel] = useState('')
  const [uploadFile, setUploadFile] = useState<File | null>(null)
  const [uploadStatus, setUploadStatus] = useState<SqliteUploadStatus>('idle')
  const [uploadResult, setUploadResult] = useState<SqliteUploadSuccess | null>(null)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const uploadDbIdError = registrationDatabaseIdMessage(uploadDbId.trim())

  const operationRef = useRef(0)
  const abortRef = useRef<AbortController | null>(null)
  const uploadOperationRef = useRef(0)
  const uploadAbortRef = useRef<AbortController | null>(null)
  const mountedRef = useRef(true)
  const activeDbIdRef = useRef(activeDbId)

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      abortRef.current?.abort()
      uploadAbortRef.current?.abort()
    }
  }, [])

  function closeDbModal() {
    uploadAbortRef.current?.abort()
    uploadOperationRef.current = nextOperation(uploadOperationRef.current)
    setUploadStatus('idle')
    setUploadResult(null)
    setUploadError(null)
    setDbModalOpen(false)
  }

  async function submitSqliteUpload() {
    if (!canStartUpload(uploadStatus)) return
    const file = uploadFile
    const databaseId = uploadDbId.trim()
    const label = uploadLabel.trim()
    if (file === null) {
      setUploadResult(null)
      setUploadError('SQLite 파일(.sqlite/.sqlite3)을 선택해 주세요.')
      setUploadStatus('error')
      return
    }
    const operation = nextOperation(uploadOperationRef.current)
    uploadOperationRef.current = operation
    uploadAbortRef.current?.abort()
    const controller = new AbortController()
    uploadAbortRef.current = controller
    setUploadStatus('uploading')
    setUploadResult(null)
    setUploadError(null)
    try {
      const result = await uploadSqliteDatabase(
        (url, init) =>
          fetch(url, {
            method: init.method,
            headers: init.headers,
            body: init.body,
            signal: init.signal ?? controller.signal,
          }),
        databaseId,
        label,
        file,
        { signal: controller.signal },
      )
      if (!mountedRef.current || isStaleResponse(uploadOperationRef.current, operation)) return
      if (controller.signal.aborted) return
      setUploadResult(result)
      setUploadError(null)
      setUploadStatus('success')
    } catch (error) {
      if (!mountedRef.current || isStaleResponse(uploadOperationRef.current, operation)) return
      if (controller.signal.aborted) return
      setUploadResult(null)
      setUploadError(toUploadMessage(error))
      setUploadStatus('error')
    }
  }

  useEffect(() => {
    let disposed = false
    const controller = new AbortController()
    const adaptiveFetch: CatalogFetchLike = (url, init) =>
      fetch(url, { method: init.method, headers: init.headers, signal: init.signal ?? controller.signal })

    async function bootstrap() {
      setRegistryLoading(true)
      try {
        let warning: string | null = null
        const databases = mainScreenDatabases(await listDatabases(adaptiveFetch, { onWarning: (message) => { warning = message } }))
        if (disposed || !mountedRef.current) return
        const known = databases.map((entry) => entry.database_id)
        let resolved = LEGACY_SELECTION_ID
        try {
          resolved = readStoredDatabaseId(window.sessionStorage, known)
        } catch {
          resolved = LEGACY_SELECTION_ID
        }
        if (activeDbIdRef.current !== resolved) {
          operationRef.current = nextOperation(operationRef.current)
          abortRef.current?.abort()
          setSelectedCourses([])
          setCoursePrompt(null)
          setHoveredCourse(null)
          setExpandedOpen(false)
          setPopupOpen(false)
          setAllCourses([])
          setCourseTotalCount(undefined)
          setCatalogLoading(true)
          setCatalogError(null)
        }
        activeDbIdRef.current = resolved
        setRegistry(databases)
        setRegistryError(null)
        setRegistryWarning(warning)
        setActiveDbId(resolved)
        setPendingDbId(resolved)
        try {
          window.sessionStorage.setItem(STORAGE_KEY, resolved)
        } catch {
          // fail closed without surfacing storage errors
        }
      } catch {
        if (disposed || !mountedRef.current) return
        setRegistry([])
        setRegistryWarning(null)
        setRegistryError('DB 목록을 불러오지 못했습니다. 기본 DB로 동작합니다.')
        if (activeDbIdRef.current !== LEGACY_SELECTION_ID) {
          operationRef.current = nextOperation(operationRef.current)
          abortRef.current?.abort()
          setSelectedCourses([])
          setCoursePrompt(null)
          setHoveredCourse(null)
          setExpandedOpen(false)
          setPopupOpen(false)
          setAllCourses([])
          setCourseTotalCount(undefined)
          setCatalogLoading(true)
          setCatalogError(null)
        }
        activeDbIdRef.current = LEGACY_SELECTION_ID
        setActiveDbId(LEGACY_SELECTION_ID)
        setPendingDbId(LEGACY_SELECTION_ID)
        try {
          writeStoredDatabaseId(window.sessionStorage, LEGACY_SELECTION_ID, [])
        } catch {
          // fail closed without surfacing storage errors
        }
      } finally {
        if (!disposed && mountedRef.current) setRegistryLoading(false)
      }
    }

    void bootstrap()
    return () => {
      disposed = true
      controller.abort()
    }
  }, [registryRetry])

  function applyDatabase(nextId: string) {
    uploadAbortRef.current?.abort()
    uploadOperationRef.current = nextOperation(uploadOperationRef.current)
    setUploadStatus('idle')
    setUploadResult(null)
    setUploadError(null)
    const known = registry.map((entry) => entry.database_id)
    const resolved =
      nextId === LEGACY_SELECTION_ID || (isDatabaseId(nextId) && known.includes(nextId))
        ? nextId
        : LEGACY_SELECTION_ID
    if (shouldResetCourseUi(activeDbIdRef.current, resolved)) {
      operationRef.current = nextOperation(operationRef.current)
      abortRef.current?.abort()
      setSelectedCourses([])
      setCoursePrompt(null)
      setHoveredCourse(null)
      setExpandedOpen(false)
      setPopupOpen(false)
      setAllCourses([])
      setCourseTotalCount(undefined)
      setCatalogLoading(true)
    }
    activeDbIdRef.current = resolved
    setActiveDbId(resolved)
    setPendingDbId(resolved)
    setCatalogError(null)
    try {
      writeStoredDatabaseId(window.sessionStorage, resolved, known)
    } catch {
      // fail closed without surfacing storage errors
    }
    setDbModalOpen(false)
  }

  function updateCourseSearch(value: string) {
    operationRef.current = nextOperation(operationRef.current)
    abortRef.current?.abort()
    setCourseSearch(value)
  }

  useEffect(() => {
    let disposed = false
    const operation = nextOperation(operationRef.current)
    operationRef.current = operation
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller
    const timeoutId = window.setTimeout(() => {
      void loadCourses()
    }, 250)

    async function loadCourses() {
      const keyword = courseSearch.trim()
      setCatalogLoading(true)
      setCatalogError(null)
      try {
        let courses: Course[]
        let totalCount: number | undefined
        if (activeDbId === LEGACY_SELECTION_ID) {
          const result = await coursesApi.getCourses({
            year: new Date().getFullYear(),
            semester: '1',
            keyword: keyword || undefined,
            page: 1,
            pageSize: 200,
          })
          courses = result.items.map(apiCourseToCourse)
          totalCount = result.total
        } else {
          const adaptiveFetch: CatalogFetchLike = (url, init) =>
            fetch(url, { method: init.method, headers: init.headers, signal: init.signal ?? controller.signal })
          const catalog = await getCatalog(adaptiveFetch, activeDbId, {
            keyword: keyword || undefined,
            signal: controller.signal,
          })
          courses = catalogToCourses(catalog.items)
        }
        if (disposed || !mountedRef.current || isStaleResponse(operationRef.current, operation)) return
        setAllCourses(courses)
        setCourseTotalCount(totalCount)
        setCatalogError(null)
      } catch (error) {
        if (disposed || !mountedRef.current || isStaleResponse(operationRef.current, operation)) return
        if (controller.signal.aborted) return
        setAllCourses([])
        setCourseTotalCount(undefined)
        setCatalogError(activeDbId === LEGACY_SELECTION_ID ? CATALOG_GENERIC_MESSAGE : toCatalogMessage(error))
      } finally {
        if (!disposed && mountedRef.current && !isStaleResponse(operationRef.current, operation)) {
          setCatalogLoading(false)
        }
      }
    }

    return () => {
      disposed = true
      window.clearTimeout(timeoutId)
      controller.abort()
      if (!isStaleResponse(operationRef.current, operation)) {
        operationRef.current = nextOperation(operationRef.current)
      }
    }
  }, [courseSearch, activeDbId, catalogRetry])

  const selectedIds = useMemo(
    () => new Set(selectedCourses.map((c) => c.id)),
    [selectedCourses],
  )
  const hoveredConflictIds = useMemo(
    () => getConflictingCourseIds(hoveredCourse, selectedCourses),
    [hoveredCourse, selectedCourses],
  )
  const chatTarget = useMemo(() => chatTargetFor(activeDbId, registry), [activeDbId, registry])
  // A partially mapped PostgreSQL DB (Phase 7-12) has no full course catalog;
  // it is queried through the AI Course Assistant instead. Other errors keep retry.
  const catalogOnlyViaChat = catalogError === CATALOG_VIA_CHAT_MESSAGE
  const completedCourseCodes = useMemo(
    () => extractCompletedCourseCodes(completedCourses),
    [completedCourses],
  )

  function addCourseDirect(course: Course) {
    setSelectedCourses((prev) => {
      if (prev.some((c) => c.id === course.id)) return prev
      return [...prev, course]
    })
  }

  function replaceConflictingCoursesDirect(course: Course) {
    setSelectedCourses((prev) => {
      const conflicts = getConflictingCourseIds(course, prev)
      const next = prev.filter((c) => c.id !== course.id && !conflicts.has(c.id))
      return [...next, course]
    })
  }

  function addCourse(course: Course) {
    const warning = buildCoursePrompt(course, 'add', completedCourseCodes)
    if (warning) {
      setCoursePrompt(warning)
      return
    }
    addCourseDirect(course)
  }

  function removeCourse(courseId: string) {
    setSelectedCourses((prev) => prev.filter((c) => c.id !== courseId))
  }

  function replaceConflictingCourses(course: Course) {
    const warning = buildCoursePrompt(course, 'replace', completedCourseCodes)
    if (warning) {
      setCoursePrompt(warning)
      return
    }
    replaceConflictingCoursesDirect(course)
  }

  function confirmCoursePrompt() {
    if (!coursePrompt) return
    if (coursePrompt.action === 'replace') {
      replaceConflictingCoursesDirect(coursePrompt.course)
    } else {
      addCourseDirect(coursePrompt.course)
    }
    setCoursePrompt(null)
  }

  return (
    <div className="min-h-screen bg-slate-50">
      {coursePrompt ? (
        <div className="fixed inset-0 z-[60] flex items-center justify-center bg-slate-900/35 px-4">
          <div
            className="w-full max-w-sm rounded-xl border border-slate-200 bg-white p-5 shadow-2xl"
            role="dialog"
            aria-modal="true"
            aria-labelledby="course-warning-title"
          >
            <div id="course-warning-title" className="text-sm font-semibold text-slate-900">
              확인이 필요한 강의입니다
            </div>
            <div className="mt-2 text-sm text-slate-600">
              {coursePrompt.course.name}을(를) 시간표에 담기 전에 확인해 주세요.
            </div>
            <div className="mt-3 space-y-2">
              {coursePrompt.closed ? (
                <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs font-medium text-amber-900">
                  정원이 마감되었거나 초과된 강의입니다.
                </div>
              ) : null}
              {coursePrompt.unmetPrerequisiteCodes.length > 0 ? (
                <div className="rounded-lg border border-violet-200 bg-violet-50 px-3 py-2 text-xs font-medium text-violet-900">
                  선수과목을 아직 이수하지 않았습니다:
                  <span className="ml-1">
                    {formatPrerequisites(
                      coursePrompt.unmetPrerequisiteCodes,
                      coursePrompt.unmetPrerequisiteNames,
                    )}
                  </span>
                </div>
              ) : null}
            </div>
            <div className="mt-4 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setCoursePrompt(null)}
                className="h-9 rounded-lg border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-700 transition hover:bg-slate-50"
              >
                취소
              </button>
              <button
                type="button"
                onClick={confirmCoursePrompt}
                className="h-9 rounded-lg bg-violet-700 px-3 text-sm font-semibold text-white transition hover:bg-violet-800"
              >
                그래도 담기
              </button>
            </div>
          </div>
        </div>
      ) : null}

      <header className="sticky top-0 z-10 border-b border-slate-200 bg-white/80 backdrop-blur">
        <div className="mx-auto flex max-w-7xl items-center gap-4 px-4 py-4 sm:px-6">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-blue-600 text-white shadow-sm">
              <span className="text-sm font-black">UNI</span>
            </div>
            <div className="hidden shrink-0 whitespace-nowrap sm:block">
              <div className="text-sm font-semibold text-slate-900">
                수강 신청
              </div>
              <div className="text-xs text-slate-500">
                강좌 둘러보기
              </div>
            </div>
          </div>

          <div className="ml-auto w-full max-w-xl">
            <div className="relative">
              <input
                value={courseSearch}
                onChange={(event) => updateCourseSearch(event.target.value)}
                placeholder="과목명으로 검색"
                className="h-11 w-full rounded-xl border border-slate-200 bg-white px-4 pr-12 text-sm text-slate-900 shadow-sm outline-none transition focus:border-blue-400 focus:ring-4 focus:ring-blue-100"
              />
              <div className="pointer-events-none absolute inset-y-0 right-3 flex items-center text-slate-400">
                <svg
                  width="18"
                  height="18"
                  viewBox="0 0 24 24"
                  fill="none"
                  xmlns="http://www.w3.org/2000/svg"
                  aria-hidden="true"
                >
                  <path
                    d="M10.5 18a7.5 7.5 0 1 1 0-15 7.5 7.5 0 0 1 0 15Z"
                    stroke="currentColor"
                    strokeWidth="1.8"
                  />
                  <path
                    d="M16.2 16.2 21 21"
                    stroke="currentColor"
                    strokeWidth="1.8"
                    strokeLinecap="round"
                  />
                </svg>
              </div>
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <div className="hidden rounded-lg bg-slate-100 px-3 py-2 text-xs font-medium text-slate-700 md:block">
              현재 DB: {registryLoading ? '확인 중' : databaseLabel(registry, activeDbId)}
            </div>
            <button
              type="button"
              onClick={() => {
                setPendingDbId(activeDbId)
                setDbAdminTab('use')
                setDbModalOpen(true)
              }}
              className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-semibold text-slate-700 transition hover:bg-slate-50"
            >
              DB 변경
            </button>
            {userName ? (
              <>
                <div className="hidden rounded-lg bg-slate-100 px-3 py-2 text-xs font-medium text-slate-700 sm:block">
                  {userName}
                </div>
                <button
                  type="button"
                  onClick={onLogout}
                  className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-semibold text-slate-700 transition hover:bg-slate-50"
                >
                  Logout
                </button>
              </>
            ) : (
              <button
                type="button"
                onClick={onLoginClick}
                className="rounded-lg bg-blue-600 px-3 py-2 text-xs font-semibold text-white transition hover:bg-blue-700"
              >
                로그인
              </button>
            )}
          </div>
        </div>
      </header>

      {dbModalOpen ? (
        <div className="fixed inset-0 z-[70] flex items-center justify-center bg-slate-900/35 px-4">
          <div
            className="flex h-[90vh] w-[95vw] max-w-6xl flex-col overflow-hidden rounded-xl border border-slate-200 bg-white p-5 shadow-2xl"
            role="dialog"
            aria-modal="true"
            aria-labelledby="database-select-title"
          >
            <div id="database-select-title" className={adminStyles.title}>
              DB 선택 및 관리
            </div>
            <div className="mt-3 flex shrink-0 flex-col gap-2 border-b border-slate-200 pb-3 md:flex-row" aria-label="DB 관리 메뉴">
              {([['use', '사용할 DB'], ['register', 'DB 등록'], ['review', '검토·게시'], ['chat-models', '챗봇 모델']] as const).map(([tab, title]) => (
                <button key={tab} type="button" aria-pressed={dbAdminTab === tab}
                  onClick={() => setDbAdminTab(tab)}
                  className={dbAdminTab === tab ? adminStyles.primary : adminStyles.secondary}>
                  {title}
                </button>
              ))}
              <button type="button" onClick={() => closeDbModal()} className={`${adminStyles.secondary} md:ml-auto`}>닫기</button>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto py-4">
              <section hidden={dbAdminTab !== 'use'} aria-label="사용할 DB" className={adminStyles.box}>
                <div className="mt-1 text-xs text-slate-500">
                  적용을 눌러야 변경됩니다. 변경 시 선택 강의·경고·미리보기가 초기화됩니다.
                </div>
                {registryError !== null || registryWarning !== null ? (
                  <div className={`${adminStyles.notice} mt-3`}>
                    {registryError ?? registryWarning}
                  </div>
                ) : null}
                <fieldset className={`${adminStyles.box} mt-3 space-y-2`}>
                  <legend className={adminStyles.title}>DB 목록</legend>
                  <label className="flex items-center gap-2 rounded-lg border border-slate-200 px-3 py-2 text-sm">
                    <input
                      type="radio"
                      name="active-database"
                      checked={pendingDbId === LEGACY_SELECTION_ID}
                      onChange={() => setPendingDbId(LEGACY_SELECTION_ID)}
                    />
                    기본 DB
                  </label>
                  {registry.map((entry) => (
                    <label
                      key={entry.database_id}
                      className="flex items-center gap-2 rounded-lg border border-slate-200 px-3 py-2 text-sm"
                    >
                      <input
                        type="radio"
                        name="active-database"
                        checked={pendingDbId === entry.database_id}
                        onChange={() => setPendingDbId(entry.database_id)}
                      />
                      <span className="min-w-0 break-all">{databaseLabel(registry, entry.database_id)} ({entry.schema})</span>
                      <span className={`${adminStyles.published} ml-auto shrink-0`}>
                        게시됨{pgRegistrations.find((item) => item.database_id === entry.database_id)?.mapping_version != null
                          ? ` v${pgRegistrations.find((item) => item.database_id === entry.database_id)?.mapping_version}` : ''}
                      </span>
                    </label>
                  ))}
                </fieldset>
                <div className="mt-4 flex justify-end gap-2">
                  <button
                    type="button"
                    onClick={() => closeDbModal()}
                    className={adminStyles.secondary}
                  >
                    취소
                  </button>
                  <button
                    type="button"
                    onClick={() => applyDatabase(pendingDbId)}
                    className={adminStyles.primary}
                  >
                    적용
                  </button>
                </div>
              </section>
              <PostgresRegistrationPanel
                view={dbAdminTab === 'review' ? 'review' : 'register'}
                hidden={dbAdminTab === 'use' || dbAdminTab === 'chat-models'}
                onOpenReview={() => setDbAdminTab('review')}
                onRegistrations={setPgRegistrations}
                onPublished={() => setRegistryRetry((value) => value + 1)}
                onDeleted={(databaseId) => {
                  if (activeDbIdRef.current === databaseId) applyDatabase(LEGACY_SELECTION_ID)
                  else if (pendingDbId === databaseId) setPendingDbId(LEGACY_SELECTION_ID)
                  setRegistry((items) => items.filter((item) => item.database_id !== databaseId))
                  setRegistryRetry((value) => value + 1)
                }}
              />
              {dbAdminTab === 'chat-models' ? <CustomChatModelsPanel onChanged={() => setChatModelRevision((value) => value + 1)} /> : null}
              {SHOW_SQLITE_UPLOAD && <section hidden={dbAdminTab !== 'register'} className={adminStyles.box}>
                <details className={`${adminStyles.box} mt-6`}>
                  <summary className={`${adminStyles.title} cursor-pointer`}>기타: SQLite 파일 등록</summary>
                  <fieldset className={`${adminStyles.box} mt-4 space-y-2`}>
                    <legend className={adminStyles.title}>
                      SQLite 등록(검토 대기)
                    </legend>
                    <div className="text-xs text-slate-500">
                      standalone SQLite 파일(.sqlite/.sqlite3)을 등록합니다. 등록 결과는
                      검토 대기 상태로만 표시되며 조회·질의에 사용할 수 없습니다.
                    </div>
                    <label className="block text-xs font-medium text-slate-700">
                      Database ID
                      <input
                        value={uploadDbId}
                        onChange={(event) => setUploadDbId(event.target.value)}
                        placeholder="예: my-review-db"
                        aria-invalid={uploadDbIdError !== null}
                        aria-describedby="sqlite-id-guidance"
                        className={`${adminStyles.input} mt-1 w-full`}
                      />
                      <span id="sqlite-id-guidance" className={`mt-1 block text-xs ${uploadDbId !== '' && uploadDbIdError !== null ? 'text-rose-700' : 'text-slate-500'}`}>
                        {DATABASE_ID_INPUT_MESSAGE}
                        {uploadDbIdError !== null && uploadDbIdError !== DATABASE_ID_INPUT_MESSAGE ? <span className="block text-rose-700">{uploadDbIdError}</span> : null}
                      </span>
                    </label>
                    <label className="block text-xs font-medium text-slate-700">
                      Label
                      <input
                        value={uploadLabel}
                        onChange={(event) => setUploadLabel(event.target.value)}
                        placeholder="예: 검토용 강의 DB"
                        className={`${adminStyles.input} mt-1 w-full`}
                      />
                      <span className="mt-1 block text-xs text-slate-500">{SQLITE_LABEL_INPUT_MESSAGE}</span>
                    </label>
                    <label className="block text-xs font-medium text-slate-700">
                      SQLite 파일
                      <input
                        type="file"
                        accept=".sqlite,.sqlite3"
                        onChange={(event) => setUploadFile(event.target.files?.[0] ?? null)}
                        className={`${adminStyles.input} mt-1 w-full`}
                      />
                    </label>
                    <div className="flex items-center gap-2">
                      <button
                        type="button"
                        onClick={() => void submitSqliteUpload()}
                        disabled={uploadStatus === 'uploading' || uploadDbIdError !== null}
                        className={adminStyles.primary} title={disabledTitle([uploadStatus === 'uploading', "업로드 중입니다."], [uploadDbIdError !== null, "DB 식별자(ID)를 확인해 주세요."])}
                      >
                        {uploadStatus === 'uploading' ? '등록 중' : '등록'}
                      </button>
                      {uploadStatus === 'uploading' ? (
                        <span className="text-xs text-slate-500" role="status">
                          업로드 중입니다. 중복 등록은 차단됩니다.
                        </span>
                      ) : null}
                    </div>
                    {uploadStatus === 'error' && uploadError !== null ? (
                      <div
                        className={adminStyles.error}
                        role="alert"
                      >
                        {uploadError}
                      </div>
                    ) : null}
                    {uploadStatus === 'success' && uploadResult !== null ? (
                      <div
                        className={`${adminStyles.success} space-y-1`}
                        role="status"
                      >
                        <div className={adminStyles.title}>
                          {uploadResult.database_id} · {uploadResult.label} · PENDING_REVIEW
                        </div>
                        <div>{UPLOAD_PENDING_NOTICE}</div>
                        <div>
                          테이블 {uploadResult.schema.table_count}개 · 컬럼{' '}
                          {uploadResult.schema.column_count}개 · FK {uploadResult.schema.foreign_key_count}
                          개
                        </div>
                        {uploadResult.schema.tables.map((table) => (
                          <div key={table.name}>
                            {table.name} ({table.columns.map((column) => column.name).join(', ')})
                          </div>
                        ))}
                        <div>
                          매핑 {uploadResult.mapping.field_count}개 필드 · 미매핑{' '}
                          {uploadResult.mapping.unmapped_count}개 · 제안:{' '}
                          {PROPOSER_LABELS[uploadResult.mapping.proposer]}
                        </div>
                        {uploadResult.mapping.fields.map((field) => (
                          <div key={field.semantic_field}>
                            {field.semantic_field} · {field.review_status}
                            {field.top_candidate !== null
                              ? ` · ${field.top_candidate.table}.${field.top_candidate.column} (${field.top_candidate.confidence})`
                              : null}
                            {field.top_candidate?.reasons.some((reason) => reason.signal === 'AI_PROPOSAL')
                              ? ' · AI'
                              : null}
                          </div>
                        ))}
                        <MappingReviewPanel
                          key={uploadResult.database_id}
                          databaseId={uploadResult.database_id}
                          onPublished={() => setRegistryRetry((value) => value + 1)}
                          baseOptions={registry.filter((entry) => entry.schema === 'private')}
                        />
                        <SchemaComparePanel
                          key={`compare-${uploadResult.database_id}`}
                          candidateDatabaseId={uploadResult.database_id}
                          baseOptions={registry.filter((entry) => entry.schema === 'private')}
                        />
                      </div>
                    ) : null}
                  </fieldset>
                </details>
              </section>}
            </div>
          </div>
        </div>
      ) : null}

      <div className="mx-auto max-w-7xl px-4 pt-4 sm:px-6">
        {registryWarning !== null ? <div className="mt-2 text-xs text-amber-800" role="status">{registryWarning}</div> : null}
        {registryError !== null ? (
          <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-amber-800" role="alert">
            <span>{registryError}</span>
            <button
              type="button"
              onClick={() => setRegistryRetry((value) => value + 1)}
              className="rounded-lg border border-amber-300 bg-white px-2 py-1 font-semibold text-amber-900 transition hover:bg-amber-50"
            >
              DB 목록 다시 시도
            </button>
          </div>
        ) : null}
        {activeDbId !== LEGACY_SELECTION_ID ? (
          <div className="mt-2 text-xs text-slate-500" role="status">
            {catalogLoading ? '강좌 목록을 불러오는 중입니다.' : null}
            {catalogOnlyViaChat ? (
              <span className="font-semibold text-violet-700" data-testid="catalog-via-chat">
                {CATALOG_VIA_CHAT_MESSAGE}
              </span>
            ) : catalogError !== null ? (
              catalogError
            ) : null}
            {catalogError !== null && !catalogOnlyViaChat ? (
              <button
                type="button"
                onClick={() => setCatalogRetry((value) => value + 1)}
                className="ml-2 rounded-lg border border-slate-200 bg-white px-2 py-1 text-xs font-semibold text-slate-700 transition hover:bg-slate-50"
              >
                다시 시도
              </button>
            ) : null}
          </div>
        ) : null}
        {activeDbId === LEGACY_SELECTION_ID && (catalogLoading || catalogError !== null) ? (
          <div className="mt-2 text-xs text-slate-500" role="status">
            {catalogLoading ? '강좌 목록을 불러오는 중입니다.' : null}
            {catalogError !== null ? catalogError : null}
            {catalogError !== null ? (
              <button
                type="button"
                onClick={() => setCatalogRetry((value) => value + 1)}
                className="ml-2 rounded-lg border border-slate-200 bg-white px-2 py-1 text-xs font-semibold text-slate-700 transition hover:bg-slate-50"
              >
                다시 시도
              </button>
            ) : null}
          </div>
        ) : null}
      </div>

      <main className="mx-auto grid max-w-7xl grid-cols-1 gap-5 px-4 py-6 sm:px-6 lg:grid-cols-[1.45fr_0.95fr]">
        <section className="min-w-0">
          <CourseTable
            courses={allCourses}
            totalCount={activeDbId === LEGACY_SELECTION_ID ? courseTotalCount : undefined}
            selectedIds={selectedIds}
            selectedCourses={selectedCourses}
            completedCourseCodes={completedCourseCodes}
            onAddCourse={addCourse}
            onRemoveCourse={removeCourse}
            onHoverCourse={setHoveredCourse}
            onReplaceCourse={replaceConflictingCourses}
          />
        </section>

        <aside className="space-y-5">
          <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
            <div className="flex items-center justify-between">
              <div>
                <div className="text-sm font-semibold text-slate-900">
                  담은 강좌
                </div>
                <div className="text-xs text-slate-500">
                  도우미에서 담은 강좌
                </div>
              </div>
              <div className="rounded-full bg-blue-50 px-3 py-1 text-xs font-semibold text-blue-700">
                {selectedCourses.length}
              </div>
            </div>

            <div className="mt-3 max-h-64 space-y-2 overflow-y-auto pr-1">
              {selectedCourses.length === 0 ? (
                <div className="rounded-xl border border-dashed border-slate-200 bg-slate-50 px-3 py-3 text-sm text-slate-600">
                  도우미를 열어 강좌를 담으면 시간표가 만들어집니다.
                </div>
              ) : (
                selectedCourses.map((c, index) => (
                  <div
                    key={c.id}
                    className="flex items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white px-3 py-2"
                  >
                    <div className="flex min-w-0 items-start gap-3">
                      <div
                        className={cn(
                          'mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-lg text-xs font-black text-white shadow-sm ring-1',
                          courseColor(index),
                        )}
                      >
                        {index + 1}
                      </div>
                      <div className="min-w-0">
                        <div className="truncate text-sm font-semibold text-slate-900">
                          {c.name}
                        </div>
                        <div className="truncate text-xs text-slate-500">
                          {c.timeText}
                        </div>
                        {c.locationText ? (
                          <div className="truncate text-xs text-slate-500">
                            강의실: {c.locationText}
                          </div>
                        ) : null}
                      </div>
                    </div>
                    <div className="flex shrink-0 items-center gap-2">
                      <div className="text-xs font-semibold text-slate-600">
                        {c.credits > 0 ? `${c.credits}학점` : '-'}
                      </div>
                      <button
                        type="button"
                        onClick={() => removeCourse(c.id)}
                        className="inline-flex h-7 w-7 items-center justify-center rounded-lg text-slate-400 transition hover:bg-rose-50 hover:text-rose-700"
                        aria-label={`${c.name} 빼기`}
                      >
                        <span className="text-base leading-none">×</span>
                      </button>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>

          <Timetable
            courses={selectedCourses}
            variant="mini"
            overlayCourse={hoveredCourse}
            conflictIds={hoveredConflictIds}
            onClick={() => setExpandedOpen(true)}
          />
        </aside>
      </main>

      <button
        type="button"
        onClick={() => setPopupOpen(true)}
        className={cn(
          'fixed bottom-6 right-6 z-40 inline-flex h-14 w-14 items-center justify-center rounded-full bg-blue-600 text-white shadow-xl',
          'transition hover:bg-blue-700 active:scale-[0.98]',
        )}
        aria-label="AI 수강 도우미 열기"
      >
        <ChatIcon className="h-6 w-6" />
      </button>

      <ChatPopup
        open={popupOpen}
        onClose={() => setPopupOpen(false)}
        chatTarget={chatTarget}
        modelRevision={chatModelRevision}
        userStudentNo={userStudentNo}
        selectedCourses={selectedCourses}
        selectedIds={selectedIds}
        completedCourseCodes={completedCourseCodes}
        onAddCourse={addCourse}
        onRemoveCourse={removeCourse}
        onReplaceCourse={replaceConflictingCourses}
        onOpenExpandedTimetable={() => setExpandedOpen(true)}
      />

      <ExpandedTimetableModal
        open={expandedOpen}
        courses={selectedCourses}
        onClose={() => setExpandedOpen(false)}
      />
    </div>
  )
}
