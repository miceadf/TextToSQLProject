import { useState } from 'react'
import type { FormEvent } from 'react'

type AuthPageProps = {
  onLogin: (studentNo: string) => Promise<string | null>
  onBack: () => void
}

export default function AuthPage({ onLogin, onBack }: AuthPageProps) {
  const [studentNo, setStudentNo] = useState('')
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)

  function validateStudentNo(nextStudentNo: string) {
    return /^\d{8,12}$/.test(nextStudentNo)
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError('')

    const trimmedStudentNo = studentNo.trim()
    if (!trimmedStudentNo) {
      setError('학번을 입력해주세요.')
      return
    }

    if (!validateStudentNo(trimmedStudentNo)) {
      setError('학번은 숫자만 입력하며 8~12자리여야 합니다.')
      return
    }

    setSubmitting(true)

    try {
      const loginError = await onLogin(trimmedStudentNo)
      if (loginError) {
        setError(loginError)
      }
    } catch {
      setError('로그인 처리 중 오류가 발생했습니다. 다시 시도해주세요.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-50 px-4 py-8">
      <div className="w-full max-w-md rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
        <div className="mb-5 flex items-center justify-between gap-4">
          <div>
            <div className="text-lg font-bold text-slate-900">
              수강 신청
            </div>
            <div className="text-sm text-slate-500">학번으로 로그인</div>
          </div>
          <button
            type="button"
            onClick={onBack}
            className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-semibold text-slate-700 transition hover:bg-slate-50"
          >
            메인으로
          </button>
        </div>

        <form className="space-y-3" onSubmit={handleSubmit}>
          <div>
            <label className="mb-1 block text-xs font-semibold text-slate-700">
              학번
            </label>
            <input
              type="text"
              inputMode="numeric"
              value={studentNo}
              onChange={(event) => setStudentNo(event.target.value)}
              disabled={submitting}
              className="h-11 w-full rounded-xl border border-slate-200 px-3 text-sm text-slate-900 outline-none transition focus:border-blue-400 focus:ring-4 focus:ring-blue-100"
              placeholder="202012345"
              autoFocus
            />
          </div>

          {error && (
            <div className="rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={submitting}
            className="mt-2 h-11 w-full rounded-xl bg-blue-600 text-sm font-semibold text-white transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-blue-300"
          >
            {submitting ? '로그인 중...' : '로그인'}
          </button>
        </form>
      </div>
    </div>
  )
}
