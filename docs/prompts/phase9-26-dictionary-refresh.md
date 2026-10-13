# Phase 9-26 지침: 검토·승인·게시 뒤 값 사전·확장 항목 상자를 자동으로 새로 고침

## 증상 (Claude가 테스트 화면에서 재현, 2026-10-14)

- 새 PostgreSQL 등록 → 검토·게시 탭 → "항목 연결 검토 시작" → "AI 추천 모두 승인" → "검토한 항목 연결 게시"(v1)까지 했는데, 오른쪽 **값 사전** 상자는 처음 열렸을 때 그대로 "값 사전: 승인된 문자열 field가 없습니다(검토에서 먼저 승인)."를 보였다. 상자 안 [새로고침]을 눌러야 출처·항목 선택과 [가져오기]가 나타났다.
- 원인(추정, 확인할 것): `PostgresRegistrationPanel.tsx`가 `<ValueDictionaryPanel key={\`dictionary-${opened}\`} …>`로 한 번만 mount하고, 패널은 mount 때만 사전 정보를 읽는다. `MappingReviewPanel`의 승인·변경·없음·일괄 승인·게시로 검토 상태가 바뀌어도 알 길이 없다. `ExtensionFieldPanel`(연결된 항목·table 목록에 의존)도 같은 문제일 수 있다.

## 고칠 것 (화면만, API 변경 없음)

- `MappingReviewPanel`이 검토 상태가 바뀔 때마다 바뀌는 값(예: 검토 detail의 결정 상태 요약 + `mapping_version`/`active_mapping_version`을 묶은 문자열이나 증가하는 revision 번호)을 `reviewTools` 쪽으로 넘긴다(지금 `reviewTools(reviewer)` 형태를 확장).
- `ValueDictionaryPanel`과 `ExtensionFieldPanel`은 그 값이 바뀌면 자기 정보를 다시 읽는다. 단, **저장하지 않은 편집(dirty)이 있으면 덮어쓰지 않고** "검토 내용이 바뀌었습니다. [새로고침]" 안내만 보인다.
- 값 사전 쪽 "사전 적용해 게시"와 확장 항목 게시가 성공하면 `MappingReviewPanel`도 최신 상태(버전)를 다시 읽는다(이미 그렇다면 그대로).
- [새로고침] 버튼은 그대로 둔다.

## 테스트

- frontend: revision 값이 바뀌면 다시 읽기 호출이 한 번 일어나고, dirty면 다시 읽지 않고 안내가 나오는지(기존 테스트 방식으로 가능한 범위, 필요하면 판단 로직을 순수 함수로 빼서 테스트). 기존 관련 테스트 통과.
- `npx tsc -b`, 관련 파일 eslint, 전체 `node --test tests/frontend/*.test.ts`.

## 지킬 것

- 서버·사용자 데이터 안 건드림, live 모델 호출 없음, 커밋 없음. UTF-8로 쓴다.
- 문서: `docs/schema-adaptive-text-to-sql-plan.md` 변경 이력 2026-10-14 "9-26" 한 줄.
- 끝나면 바꾼 파일과 테스트 결과를 짧게 적는다.
