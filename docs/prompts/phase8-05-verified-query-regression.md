# Phase 8-05 구현 지침: verified query 기준선과 버전 회귀 비교

## 배경

8-04는 활성 mapping에서 고정 test query를 실행해 결과 일부와 순서 무관 결과 hash를 보여준다. 8-05는 관리자가 **확인한 결과를 기준선(verified)으로 기록**하고, publish·재활성화 전후에 같은 query를 다시 실행해 **결과가 바뀐 query를 표시**하는 회귀 비교를 추가한다. 회귀 결과는 경고 정보이며 publish·재활성화를 자동으로 막거나 실행하지 않는다.

## 범위

### 1. 게시 버전 로드 (`backend/adaptive/registration/reviews.py`)

- `load_published_version(repository_root, database_id, mapping_version) -> LoadedActivation`형 결과: `current.json`의 게시 digest 기록에 있는 버전이고 파일 bytes가 digest와 같으며, 재활성화와 같은 검사(`_check_activatable`: 등록 fingerprint, 현재 ontology, canonical coverage, 실행 가능성)를 통과할 때만 반환한다. 파일을 쓰지 않고 activation을 바꾸지 않는다.

### 2. verified 기준선 (`verified-queries.json`, `verified-queries-v1`)

- review 디렉터리의 관리자 metadata(실행 권한과 무관). 항목: `query_id`, `mapping_version`, `result_sha256`, `row_count`, `verified_by`, `verified_at`. query ID당 최신 1개. 행 값은 저장하지 않는다.
- `verify_test_query(repository_root, database_id, query_id, verified_by)`: **서버가 활성 버전에서 query를 실행한 결과 hash**를 기록한다(클라이언트가 hash를 보내지 않는다). per-DB lock, 원자적 교체.
- 파일 허용 목록에 `verified-queries.json`을 추가한다. 손상된 기준선은 없는 것으로 보고 회귀를 `UNVERIFIED`로 표시한다(실행에는 영향 없음).

### 3. 회귀 비교 (`backend/adaptive/api/admin_test_queries.py`)

- `run_regression(repository_root, database_id, mapping_version)`: 모든 고정 query에 대해 상태를 계산한다.
  - `MATCH`: 기준선 hash와 같음, `CHANGED`: 다름(행 수 함께), `FAILED`: 실행 경로 실패(harness 고정 code), `UNVERIFIED`: 기준선 없음(실행 결과 hash는 표시).
  - 대상 버전은 활성 버전 또는 게시 digest 기록이 있는 비활성 버전(재활성화 전 확인용). 고정 query만, 같은 실행 경로로 실행한다.
- HTTP:
  - `POST /api/adaptive/v1/registrations/verified-queries` `{"action":"VERIFY","database_id","query_id","verified_by"}` → 갱신된 기준선 목록.
  - `GET /api/adaptive/v1/registrations/regression?database_id=..&mapping_version=..` → `verified-query-regression-v1`: 대상 버전, 활성 버전, query별 상태·기준 버전·hash·행 수, 요약(match/changed/failed/unverified 수).
  - 고정 오류: 형식 400, 활성·게시 버전 아님 404, 실행 전 오류 500, lock 충돌 409.

### 4. frontend

- `testQueries.ts`: 기준선·회귀 parser와 client.
- `TestQueryPanel.tsx`: 결과 옆 "이 결과를 verified로 기록"(검토자 필수).
- `MappingReviewPanel.tsx`: 버전 이력의 비활성 버전에 "회귀 확인"(재활성화 전 비교), publish·재활성화 직후 새 활성 버전 회귀 자동 실행과 `CHANGED`·`FAILED` 경고 표시.

## 제외

- 회귀 결과로 publish·재활성화를 자동 차단하거나 자동 되돌리기.
- 임의 query의 verified 등록, 등록 간(승계 기준 DB ↔ 새 DB) 비교.
- 미게시 draft에서의 실행.

## 보안 계약(유지)

미게시 draft 실행 금지, 고정 QueryIR만 기존 검증·compiler·AST policy·read-only executor로 실행, 비활성 게시 버전 실행은 관리자 회귀 비교의 고정 query에 한하고 사용자 질의 실행 권한(activation)은 바꾸지 않음, 행 값 저장 없음, SQL·물리 이름·경로 비노출, 고정 오류 code.

## 테스트

- verify 뒤 같은 버전 회귀는 전부 `MATCH`; 과목명 column을 바꾼 v2는 해당 query만 `CHANGED`, 나머지 `MATCH`; 재활성화 전 비활성 v1 회귀 가능.
- 기준선 없는 query `UNVERIFIED`, 손상된 기준선은 `UNVERIFIED`로 처리하고 activation·registry 영향 없음.
- digest 기록에 없는 orphan·교체된 버전, 미게시·검토 대기 DB는 404. 클라이언트가 hash를 보낼 수 없음(추가 key 400).
- 파일 허용 목록 회귀: 기준선이 있어도 `load_activation`·INIT·publish 정상.
- frontend parser·client 거부 사례.
