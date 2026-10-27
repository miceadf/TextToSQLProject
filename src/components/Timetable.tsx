import type { ElementType } from 'react'
import type { Course, Weekday } from '../types/course'
import { cn } from '../utils/cn'
import { courseColor } from '../utils/courseColors'

const days: Weekday[] = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
// Phase 9-39: show Korean weekday labels while retaining the course model's internal keys.
const dayLabels: Record<Weekday, string> = { Mon: '월', Tue: '화', Wed: '수', Thu: '목', Fri: '금', Sat: '토' }

function formatHour(value: number) {
  const hour = Math.floor(value)
  const minute = Math.round((value - hour) * 60)
  return `${hour}:${String(minute).padStart(2, '0')}`
}

export default function Timetable({
  courses,
  variant,
  onClick,
  overlayCourse,
  conflictIds,
}: {
  courses: Course[]
  variant: 'mini' | 'full'
  onClick?: () => void
  overlayCourse?: Course | null
  conflictIds?: Set<string>
}) {
  const startHour = 9
  const endHour = 22
  const hours = Array.from({ length: endHour - startHour + 1 }, (_, i) => startHour + i)

  const hourHeight = variant === 'mini' ? 16 : 44
  const totalHeight = (endHour - startHour) * hourHeight
  const overlayHasConflict = (conflictIds?.size ?? 0) > 0

  const Wrapper: ElementType = onClick ? 'button' : 'div'
  const wrapperProps = onClick
    ? {
        type: 'button' as const,
        onClick,
        role: 'button' as const,
        tabIndex: 0,
        'aria-label':
          variant === 'mini' ? '시간표 크게 보기' : '시간표',
      }
    : {
        role: 'region' as const,
        'aria-label': '시간표',
      }

  return (
    <Wrapper
      {...wrapperProps}
      className={cn(
        'w-full text-left',
        onClick && 'cursor-pointer',
        !onClick && 'cursor-default',
      )}
    >
      <div
        className={cn(
          'rounded-xl border border-slate-200 bg-white shadow-sm',
          variant === 'mini' ? 'p-3' : 'p-4',
          onClick && 'transition hover:shadow-md',
        )}
      >
        <div className="flex items-center justify-between">
          <div className={cn('font-semibold text-slate-900', variant === 'mini' ? 'text-sm' : 'text-base')}>
            시간표
          </div>
          {variant === 'mini' ? (
            <div className="text-xs text-slate-500">눌러서 크게 보기</div>
          ) : (
            <div className="text-sm text-slate-500">{courses.length}개 담음</div>
          )}
        </div>

        <div className={cn('mt-3', variant === 'mini' ? 'text-[10px]' : 'text-xs')}>
          <div className="grid grid-cols-[42px_1fr] gap-2">
            <div className="pt-7 text-slate-400">
              {hours.slice(0, -1).map((h) => (
                <div
                  key={h}
                  className="flex items-start justify-end pr-1"
                  style={{ height: hourHeight }}
                >
                  {h}
                </div>
              ))}
            </div>

            <div className="grid grid-cols-6 gap-2">
              {days.map((d) => (
                <div key={d} className="min-w-0">
                  <div className="mb-1.5 text-center font-semibold text-slate-600">
                    {dayLabels[d]}
                  </div>
                  <div
                    className="relative overflow-hidden rounded-lg border border-slate-200 bg-slate-50"
                    style={{ height: totalHeight }}
                  >
                    {hours.slice(0, -1).map((h) => (
                      <div
                        key={`${d}-${h}`}
                        className="border-b border-slate-200/70"
                        style={{ height: hourHeight }}
                      />
                    ))}

                    {courses.flatMap((c, courseIndex) =>
                      c.slots
                        .filter((s) => s.day === d)
                        .map((s) => {
                          const top = (s.startHour - startHour) * hourHeight
                          const height = Math.max(1, (s.endHour - s.startHour) * hourHeight)
                          const conflicting = conflictIds?.has(c.id) ?? false
                          return (
                            <div
                              key={`${c.id}-${d}-${s.startHour}-${s.endHour}`}
                              className={cn(
                                'absolute left-1 right-1 rounded-md px-2 py-1 text-white shadow-sm ring-1',
                                conflicting
                                  ? 'bg-amber-400/90 text-slate-900 ring-amber-200'
                                  : courseColor(courseIndex),
                              )}
                              style={{ top, height }}
                              title={`${c.name} (${formatHour(s.startHour)}-${formatHour(s.endHour)})`}
                            >
                              {variant === 'mini' ? (
                                <div className="flex h-full items-center justify-center text-xs font-black">
                                  {courseIndex + 1}
                                </div>
                              ) : (
                                <div className="truncate text-xs font-semibold">
                                  {courseIndex + 1}. {c.name}
                                </div>
                              )}
                              {variant === 'full' ? (
                                <div className="mt-0.5 truncate text-[11px] opacity-90">
                                  {formatHour(s.startHour)}-{formatHour(s.endHour)}
                                </div>
                              ) : null}
                            </div>
                          )
                        }),
                    )}

                    {overlayCourse?.slots
                      .filter((s) => s.day === d)
                      .map((s) => {
                        const top = (s.startHour - startHour) * hourHeight
                        const height = Math.max(1, (s.endHour - s.startHour) * hourHeight)
                        return (
                          <div
                            key={`overlay-${overlayCourse.id}-${d}-${s.startHour}-${s.endHour}`}
                            className={cn(
                              'pointer-events-none absolute rounded-md border border-slate-500/50 bg-slate-500/65 px-2 py-1 text-white shadow-sm ring-1 ring-slate-300',
                              overlayHasConflict ? 'left-3 right-1' : 'left-1 right-1',
                            )}
                            style={{ top, height }}
                            title={`${overlayCourse.name} 미리보기 (${formatHour(s.startHour)}-${formatHour(s.endHour)})`}
                          >
                            <div className={cn('truncate font-semibold', variant === 'mini' ? 'text-[10px]' : 'text-xs')}>
                              {overlayCourse.name}
                            </div>
                            {variant === 'full' ? (
                              <div className="mt-0.5 truncate text-[11px] opacity-90">
                                {formatHour(s.startHour)}-{formatHour(s.endHour)}
                              </div>
                            ) : null}
                          </div>
                        )
                      })}
                  </div>
                </div>
              ))}
            </div>
          </div>

          {courses.length === 0 ? (
            <div className="mt-3 rounded-lg border border-dashed border-slate-200 bg-white px-3 py-2 text-slate-500">
              강좌를 담으면 시간표에 표시됩니다.
            </div>
          ) : null}
        </div>
      </div>
    </Wrapper>
  )
}
