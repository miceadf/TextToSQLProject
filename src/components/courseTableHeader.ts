import { CATALOG_OFFERING_LIMIT } from '../adaptive/databases.ts'

export function courseTableHeader(count: number, totalCount?: number): { count: string; hint: string } {
  if (totalCount !== undefined && totalCount > count) {
    return {
      count: `${count}개 강좌 · 전체 ${totalCount.toLocaleString('ko-KR')}개 중`,
      hint: '검색하면 나머지 강좌도 찾을 수 있습니다.',
    }
  }
  // 기본 DB는 실제 전체 개수를 알므로 등록 DB의 200개 상한 안내를 쓰지 않는다.
  if (totalCount === undefined && count >= CATALOG_OFFERING_LIMIT) {
    return {
      count: `${count}개 강좌 · 최대 ${CATALOG_OFFERING_LIMIT}개까지 표시`,
      hint: '검색하면 나머지 강좌도 찾을 수 있습니다.',
    }
  }
  return { count: `${count}개 강좌`, hint: '' }
}
