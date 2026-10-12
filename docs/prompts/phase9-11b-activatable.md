# Phase 9-11 보완: 활성 버전의 `activatable` 처리 방향 바꾸기

9-11 첫 고침(미커밋)에 Claude 검토 지적 1건.

## 문제 (확인됨)

- 9-11은 PostgreSQL review 상세의 활성 버전을 `activatable: True`로 보냈다.
- 화면 `MappingReviewPanel.tsx`(약 215~224행)는 `activatable`이면 "회귀 확인" 버튼을 보인다. 이 버튼이 부르는 `/api/adaptive/v1/registrations/regression`(`api/admin_test_queries.py` `run_regression`)은 SQLite review 저장소(`review_storage.load_published_version`)만 읽어 PostgreSQL 등록에서는 오류가 난다. 새로 누를 수 있는 버튼이 오류를 내게 된다.

## 고칠 것

- 서버는 원래대로 PostgreSQL 활성 버전에 `activatable: False`를 보낸다(재활성화·회귀 확인을 지원하지 않는다는 뜻 그대로). `published_at` 초 단위 고침은 유지한다.
- 화면 계약을 넓힌다: `parseVersionHistory`의 `if (parsed.active && !parsed.activatable) return null;`을 없앤다. 활성 버전이 `activatable: false`인 것을 허용한다. 다른 검사는 그대로 둔다.
- 화면 표시: `!activatable`일 때 붙는 " · 현재 스키마에서 실행 불가"는 활성이 아닌 버전에만 붙인다. 활성 버전이면 "실행 중"만 보이고 "회귀 확인"·"이 버전으로 되돌리기" 버튼은 보이지 않는다(기존 조건 그대로).
- SQLite 경로 응답과 동작은 바꾸지 않는다.

## 테스트

- 9-11 테스트를 위 방향에 맞게 고친다: 서버 응답은 활성 버전 `activatable: False`·초 단위 `published_at`; frontend fixture가 `parseReviewDetail`을 통과; 활성·`activatable: false` 항목에 버튼과 "실행 불가" 문구가 없음(기존 frontend 테스트 방식으로 가능한 범위).
- SQLite 쪽 기존 이력 테스트가 그대로 통과하는지.
- 관련 테스트 파일만 실행한다.

## 정리

- 저장소 루트의 `.tmp-phase9-11-tests`(첫 실행이 남긴 테스트 임시 폴더)는 Claude가 지운다. 이번 실행은 임시 경로를 저장소 밖이나 기존 git-ignored 위치에 둔다.
- 변경 이력 9-11 줄을 고친 방향으로 바꾼다.
- 사용자 서버·데이터 안 건드림, live 모델 호출 없음, 커밋 없음. 끝나면 바꾼 파일과 테스트 결과를 짧게 적는다.
