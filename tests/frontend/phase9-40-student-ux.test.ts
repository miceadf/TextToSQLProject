import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { describe, it } from 'node:test'
import { courseTableHeader } from '../../src/components/courseTableHeader.ts'

describe('phase9-40 student screen UX', () => {
  it('explains when the basic catalog is a partial result', () => {
    assert.deepEqual(courseTableHeader(200, 4003), {
      count: '200개 강좌 · 전체 4,003개 중',
      hint: '검색하면 나머지 강좌도 찾을 수 있습니다.',
    })
  })

  it('explains the adaptive catalog cap at 200 courses', () => {
    assert.deepEqual(courseTableHeader(200), {
      count: '200개 강좌 · 최대 200개까지 표시',
      hint: '검색하면 나머지 강좌도 찾을 수 있습니다.',
    })
    assert.equal(courseTableHeader(12).count, '12개 강좌')
  })

  it('does not show the adaptive cap when the basic catalog total is exactly 200', () => {
    assert.deepEqual(courseTableHeader(200, 200), { count: '200개 강좌', hint: '' })
  })

  it('registers the modal Escape handler in capture phase', () => {
    const source = readFileSync(new URL('../../src/components/ExpandedTimetableModal.tsx', import.meta.url), 'utf8')
    assert.match(source, /addEventListener\('keydown', onKeyDown, true\)/)
    assert.match(source, /stopImmediatePropagation\(\)/)
  })
})
