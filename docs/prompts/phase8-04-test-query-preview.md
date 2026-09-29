# Phase 8-04 구현 지침: 게시·활성 mapping의 고정 test query 결과 미리보기

## 배경

8-01~8-03으로 관리자는 mapping을 게시·재활성화·승계할 수 있다. 게시 뒤 "이 mapping이 실제로 그럴듯한 결과를 내는가"를 확인할 방법이 없다. 8-04는 **서버가 소유한 고정 합성 test query**를 **게시·활성화된 private 등록**에서만 기존 실행 경로로 돌려 결과 일부를 보여준다. 결과 hash는 8-05(verified query 회귀)에서 버전 간 비교에 쓴다.

## 범위

### 1. 고정 test query 집합 (`admin-test-queries-v1`)

서버 코드에 고정된 QueryIR 5개(filter 없음, limit 200, canonical field만):

| ID | entity | select |
| --- | --- | --- |
| `offerings` | CourseOffering | Course.code, Course.name, CourseOffering.section, Instructor.name |
| `courses` | CourseOffering | Course.code, CourseOffering.section, Course.name, Course.credits, Course.category |
| `departments` | CourseOffering | Course.code, CourseOffering.section, Department.code, Department.name |
| `schedules` | CourseOffering | Course.code, CourseOffering.section, Schedule.day, Schedule.start_time, Schedule.end_time |
| `seats` | CourseOffering | Course.code, CourseOffering.section, CourseOffering.capacity, CourseOffering.enrolled |

compiler는 entity가 아니라 select field의 JOIN tree로 행 단위를 정하므로(합성 확인: 과목 field만 select하면 flat 6행, normalized·coded 5행; 학과만이면 3행), 모든 query가 개설강좌 단위 field(`CourseOffering.section`)를 포함해 행 단위를 개설강좌로 고정한다. 그래야 같은 seed의 schema 간·버전 간 결과를 비교할 수 있다.

브라우저는 query ID만 고른다. SQL·QueryIR·filter 값을 보낼 수 없다.

### 2. 실행 (`backend/adaptive/api/admin_test_queries.py`, 신규)

- 대상은 strict `load_activation`이 성공하는 게시·활성 private 등록뿐이다(검토 대기·미게시·fixture·PostgreSQL 제외).
- 기존 `ExecutionHarness`(고정 IR을 돌려주는 `FakeModel`, `SchemaAdaptiveSQLiteCompiler`, `SchemaAdaptiveAstPolicy`, 활성 DB 경로만 가진 `SQLiteReadOnlyExecutor`)로 QueryIR 검증→compile→AST policy→read-only 실행을 모두 거친다.
- 결과 행은 JSON scalar(문자열·정수·유한 실수·bool·null)만 허용하고 BLOB 등은 실패로 처리한다. 행을 값 기준으로 결정적 정렬한 뒤 최대 5행만 반환하고, 전체 행 수(`row_count`, 200에서 잘리면 `row_count_capped`)와 정렬된 전체 결과의 SHA-256(`result_sha256`, NULL과 문자열 구분)을 함께 준다.
- column은 semantic field 이름으로만 반환한다. SQL·물리 table/column 이름·경로는 반환하지 않는다.

### 3. HTTP

- `GET /api/adaptive/v1/registrations/test-queries`: 고정 목록 `{version, queries: [{query_id, title, entity, select}]}`.
- `GET /api/adaptive/v1/registrations/test-query?database_id=..&query_id=..`: 두 key만 허용. 응답 `admin-test-query-result-v1`: `database_id, mapping_version, query_id, columns, rows, row_count, row_count_capped, result_sha256`.
- 고정 오류: `ADMIN_TEST_QUERY_INVALID_REQUEST`(400, 형식·알 수 없는 query), `ADMIN_TEST_QUERY_NOT_FOUND`(404, 활성 게시 등록 아님), `ADMIN_TEST_QUERY_FAILED`(422, 실행 경로 실패; harness 고정 오류 code만 `failure_code`로 동봉), `ADMIN_TEST_QUERY_INTERNAL_ERROR`(500).

### 4. frontend

- `src/adaptive-prototype/testQueries.ts`: strict parser·client.
- `TestQueryPanel.tsx`: `MappingReviewPanel`의 게시 상태이고 활성 버전이 있을 때 표시. query 선택 → 실행 → 결과 표(최대 5행)·전체 행 수·실행 mapping 버전. 재활성화·publish 뒤에는 결과를 비운다.

## 제외

- 임의 QueryIR·filter 입력, 비활성 버전 미리보기, 자연어 질문 실행.
- verified query 저장·회귀 비교(8-05).
- 합성 fixture·PostgreSQL 대상.

## 보안 계약(유지)

미게시 mapping 실행 금지, 활성 mapping만 실행, 기존 QueryIR 검증·compiler·AST policy·read-only executor 우회 없음, raw SQL 입력 없음, SQL·물리 이름·경로·traceback 비노출, 행 수 제한. 결과 값은 기존 catalog 화면과 같은 수준으로 관리자에게 보이므로 실제 학교 DB에서는 L2(연결 전)·L5(실데이터 시연 전) 관문을 그대로 적용한다.

## 테스트

- flat·coded·hybrid 합성 등록을 게시한 뒤 5개 query 모두 seed 기대 행 수·결과와 일치, 버전 간 hash 비교(같은 mapping → 같은 hash, 다른 column mapping → 다른 hash).
- 결정적 정렬과 5행 제한, NULL과 'NULL' 문자열 hash 구분.
- 미게시·검토 대기·없는 DB 404, 알 수 없는 query·추가 key 400, 재활성화 뒤 새 활성 버전 사용.
- 응답에 SQL·물리 이름·경로 없음.
- frontend parser 거부 사례와 client URL.
