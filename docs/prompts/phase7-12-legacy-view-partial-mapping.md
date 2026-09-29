# Phase 7-12 구현 지침: legacy view의 부분 mapping과 선언형 값 변환

## 배경

목표는 legacy 챗봇(AI Course Assistant)이 쓰는 legacy PostgreSQL 강좌 DB를 QueryIR 경로로도 질의해, 같은 질문의 결과가 legacy와 비슷하거나 더 나은지 확인하는 것이다(7-13 챗봇 endpoint, 7-14 화면 연결, 7-15 비교). 7-12는 그 전제인 **legacy DB 등록**이다.

- 대상은 legacy 챗봇이 SQL을 보내는 view `v_course_info`다. 두 경로가 같은 행 집합을 보므로, 결과 차이는 질문 이해 방식(QueryIR와 직접 SQL)에서만 생긴다(사용자 결정 A, 2026-09-29).
- 사용자 결정은 두 가지다. 부분 mapping을 허용하고, 추가 field 없이 15개 canonical field만으로 비교한다.
- 사용자 확인(2026-09-29): legacy 강좌 DB는 실제 충남대 테이블이 아니라 팀이 구성한 유사 스키마·데이터다. 이 DB의 mapping은 개발자가 작성한 fixture mapping과 같은 지위이므로, 실제 학교 DB의 L2 검토를 대신하지 않는다.

## 확인된 차이(구조·집계만 확인, 행 값 비노출)

| canonical field | view column | 처리 |
|---|---|---|
| Course.code | `subject_code` | 그대로 |
| Course.name | `subject_name` | 그대로 |
| Course.credits | `credit_hours` (varchar, "학점/이론/실습") | 변환 `SLASH_FIRST_NUMERIC` |
| Course.category | `category` | 그대로 |
| Department.code | 없음 | 미지원 |
| Department.name | `dept_name` | 그대로 |
| Instructor.name | `professor` | 그대로 |
| CourseOffering.year | 없음(`course_year`는 1~5 학년이다) | 미지원 |
| CourseOffering.semester | 없음 | 미지원 |
| CourseOffering.section | `section` | 그대로 |
| CourseOffering.capacity / enrolled | `capacity` / `enrolled` | 그대로 |
| Schedule.day | `day_of_week` (한 글자 한국어 요일) | 그대로 |
| Schedule.start_time / end_time | `start_time` / `end_time` (TIME) | 변환 `TIME_TO_HHMM` |

view는 시간표가 없는 강좌를 LEFT JOIN으로 포함한다(6,846행, 개설강좌 4,003개, 시간표 없는 행 632개). 따라서 view 한 개로 매핑하면 compiler의 INNER JOIN 한계가 드러나지 않는다.

## 범위

### 1. 선언형 변환 (`backend/adaptive/mapping/transforms.py`)

- 닫힌 목록 `FieldTransform`:
  - `SLASH_FIRST_NUMERIC`
    - PostgreSQL 식: `CAST(SPLIT_PART(col, '/', 1) AS NUMERIC)`
    - 허용 field 타입: ontology `number`
    - 허용 column 타입: 문자열
  - `TIME_TO_HHMM`
    - PostgreSQL 식: `TO_CHAR(col, 'HH24:MI')`
    - 허용 field 타입: ontology `time`
    - 허용 column 타입: `time`로 시작하는 선언 타입
- 변환된 field에는 비교 연산자(`equals`, 크기 비교)만 허용하고 `contains`는 compile 단계에서 거부한다.
- 결과값 정규화: `SLASH_FIRST_NUMERIC`의 `Decimal`은 정수면 `int`, 아니면 `float`로 바꾼다.
- AI나 사용자가 식을 쓰지 않는다. 식은 이 모듈의 고정 template에서만 나온다.

### 2. PostgreSQL inspector의 view 선택 포함

- `PostgresConnectionConfig(include_views=False)` 옵션을 추가한다. `True`이면 `relkind IN ('r','p','v')`로 조회한다. 기본값에서는 기존 query와 fingerprint가 그대로 유지된다.
- view에는 PK·FK가 없으므로 relation graph에는 독립 table로 들어간다.
- 한계: column 구성이 같은 채로 view 정의만 바뀌면 fingerprint가 달라지지 않는다. 이 한계는 문서에 기록한다.

### 3. runtime profile의 부분 mapping (`postgres_runtime.py`)

- `PostgresRuntimeProfile(field_transforms={}, unsupported_fields=())`
  - 기본값에서는 기존과 동일하게 canonical 15개 전부를 요구한다.
- 검증(`_validate_one`, fail closed):
  - mapped ∪ unsupported = canonical 15이고, 둘은 겹치지 않는다.
  - 변환 대상 field는 mapped에 속해야 한다.
  - 변환은 ontology 타입과 실제 column 선언 타입 모두와 호환되어야 한다.
- compiler와 policy에 같은 변환을 넘긴다.
- `ValidatedPostgresContext.unsupported_fields()`를 둔다. 7-13에서 "지원하지 않는 항목" 안내에 쓴다.
- `DatabaseRoutingExecutor`는 변환된 field의 결과값만 정규화한다.
- 부분 profile의 catalog(`/courses`)는 지금처럼 15개 전체를 요구하므로 `CONTEXT_UNAVAILABLE`로 닫힌다. 화면 연결은 7-14에서 다룬다.

### 4. compiler·AST policy

- `JoinCompilationRequest.field_transforms`: 변환 field의 projection과 WHERE 좌변을 변환식으로 렌더링한다. placeholder·LIMIT 계약은 그대로다.
- `AstPolicyContext.field_transforms`: 변환 field의 projection과 predicate 좌변은 **mapping column에 선언 변환을 적용해 파싱한 AST와 정확히 같아야** 한다.
  - 검증된 변환 subtree 안의 노드(Cast, 함수, literal)만 허용 노드 검사에서 제외한다.
  - 그 밖의 위치에 나오는 함수·Cast·literal은 기존처럼 거부한다.
  - 변환이 없는 context는 기존 검사와 byte 단위로 같은 경로를 탄다.

### 5. private startup profile v2 (`postgres_startup_profile.py`)

- `postgres-runtime-profile-v2`: v1 key에 `include_views`(bool), `field_transforms`(object), `unsupported_fields`(정렬된 고유 문자열 배열)를 더한다.
- v1은 변경하지 않는다.
- 알 수 없는 변환·key·타입은 고정 오류로 거부한다.

### 6. legacy view 등록 CLI (`backend/adaptive/api/legacy_view_profile.py`)

- 서버 코드 상수:
  - database ID `legacy-view`
  - label `Legacy DB (QueryIR)`
  - DSN 환경변수 `SCHEMA_ADAPTIVE_POSTGRES_DSN_LEGACY_VIEW`
  - 위 표의 mapping과 변환·미지원 목록
- `--check`: 연결 없이 profile 파일의 존재와 strict load 가능 여부만 확인한다.
- `--write --live`
  - view 포함 inspection으로 fingerprint를 얻는다.
  - view column의 존재와 타입을 확인한다.
  - `.schema-adaptive/postgres-profiles/legacy-view/{profile.json,mapping.json}`을 원자적으로 쓴다.
  - 기존 파일이 다르면 `--replace` 없이 거부한다.
- `--smoke --live`
  - 고정 QueryIR를 기존 검증 → compiler → AST policy → read-only executor 경로로 실행한다.
  - 행 값은 출력하지 않고, query별 상태·행 수·형식 검사 결과만 출력한다.
- 출력은 고정 code와 수치뿐이다. DSN, SQL, 물리 이름, 행 값은 출력하지 않는다.

## 제외

- 챗봇 endpoint(7-13), 화면 연결(7-14), legacy와의 비교(7-15).
- LEFT JOIN, 기본 table mapping(B), 코드값 사전(8-06), AI mapping 제안.
- legacy backend·`v_course_info`·legacy DB의 변경. legacy DB에는 읽기 전용 연결만 사용한다.

## 보안 계약(유지)

- server-owned DSN 환경변수를 쓰고, read-only transaction·timeout·행 제한을 유지한다.
- 모델 호출은 1회이며 raw SQL fallback은 없다. QueryIR 검증·compiler·policy·executor를 우회하지 않는다.
- 변환은 닫힌 목록과 고정 template만 쓴다.
- 부분 mapping은 명시적 미지원 목록으로만 허용한다(누락은 여전히 오류다).
- SQLite 등록·upload·review 경로와 v1 profile은 변경하지 않는다.

## 테스트

- 변환
  - compiler 출력이 변환식을 포함하고, AST policy가 이를 통과한다.
  - 변환 subtree 밖의 literal·함수와 다른 변환식·다른 column·`contains`는 거부한다.
  - 변환이 없는 기존 query의 SQL은 이전과 같다.
- runtime 검증
  - 부분 profile이 통과하려면 합집합이 15개여야 한다.
  - 누락, 중복, 미지원 field의 mapping, 알 수 없는 field는 거부한다.
  - 변환 타입이 ontology·column과 맞지 않으면 거부한다.
- inspector: 기본값에서 view가 제외되고 query가 동일하다. `include_views`일 때 view를 포함한다.
- startup profile: v2 strict load와 거부 사례를 확인하고, v1 회귀가 없음을 확인한다.
- 실행 결과: 변환 결과값 정규화(`Decimal` → 수)를 확인한다.
- CLI: 기본 실행은 연결 0회이고 출력에 민감값이 없다.
- live(수동): legacy DB에 `--write --live` 후 `--smoke --live`를 실행한다. 학점 필터 결과가 view의 동일 조건 직접 계산과 같은지 scratch에서 대조하고, 행 값은 기록하지 않는다.
