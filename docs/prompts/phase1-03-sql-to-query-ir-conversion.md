# 다음 개발 프롬프트: Phase 1 SQL→Query IR 변환과 수동 검토 목록

다음 단계인 기존 학습 데이터셋의 SQL→Query IR 변환기와 결정적인 수동 검토 목록을 개발해줘.

작업 전 반드시 다음을 확인해.

- `docs/schema-adaptive-text-to-sql-plan.md` 전체
- `docs/prompts/phase1-01-jsonl-loader-quality-report.md`
- `docs/prompts/phase1-02-sql-normalization-ast-analysis.md`
- `backend/adaptive/dataset`의 loader, 품질 report, SQL parser, AST feature와 호환성 분류 계약
- 현재 `QueryIR`, `FilterIR`, ontology field·operator·data type 계약
- 기존 single-table 및 JOIN-capable compiler와 Query IR validator 동작
- 기존 데이터셋의 `courses` 물리 컬럼과 course-domain ontology의 대응 가능 범위
- 저장소의 Conventional Commit 기록
- 작업 시작 시 working tree 상태

## 목표

기존 `DatasetRecord.sql`을 PostgreSQL AST로 해석하고, 명시적으로 승인된 legacy dataset schema profile을 이용해 현재 `QueryIR`로 손실 없이 표현 가능한 부분만 결정적으로 변환한다.

자동 변환할 수 없는 레코드는 임의로 보정하거나 버리지 않고 구조화된 수동 검토 항목으로 남긴다. 이 단계의 목적은 Query IR 학습·평가 자료의 신뢰할 수 있는 원천을 만드는 것이며 Query IR 자체의 표현 범위를 넓히는 것이 아니다.

## 현재 기준선

직전 SQL AST 분석 결과는 다음과 같다.

- 전체 SQL: 5,385개
- parse 및 canonical round-trip issue: 0개
- 고유 canonical SQL: 5,085개
- literal-redacted shape: 1,667개
- `CURRENT_IR_CANDIDATE`: 3,395개
- `IR_EXTENSION_REQUIRED`: 1,115개
- `MANUAL_REVIEW`: 875개
- `UNSUPPORTED_OR_UNSAFE`: 0개

이 수치는 변환기의 검증 기준이지 코드에 하드코딩할 값이 아니다. `CURRENT_IR_CANDIDATE`도 자동 변환 성공을 보장하지 않으며 source mapping, literal type, 질문 충돌과 변환 후 검증에서 추가로 거부될 수 있다.

실제 데이터의 AST에는 정상 물리 컬럼 외에 PostgreSQL에서 identifier로 해석되는 double-quoted 값 후보가 존재한다. 예를 들어 문자열 의도로 작성된 `"컴퓨터공학과"`도 PostgreSQL AST에서는 column identifier가 된다. 이를 문자열 literal로 추측해 자동 변환하지 않는다.

## 이번 범위

- versioned legacy dataset schema profile 계약
- `courses` 물리 컬럼에서 ontology semantic field로 가는 명시적 mapping 정의
- source profile의 root entity, dialect, table allowlist, ontology version 검증
- versioned SQL→Query IR conversion result와 review item Pydantic 계약
- 이전 SQL analyzer의 canonical AST와 호환성 결과 재사용
- 단순 column projection의 semantic field 변환
- `AND` filter 순서와 비교 operator의 Query IR 변환
- 안전한 literal 추출과 ontology data type 검증
- 정확한 contains 형태의 `LIKE '%value%'` 변환
- 명시적 `LIMIT` 보존과 LIMIT이 없는 경우의 실행 안전 기본값 정책
- source intent signature와 변환된 IR intent signature 비교
- 기존 Query IR validator를 이용한 최종 계약 검증
- exact duplicate, 질문·SQL duplicate, 질문별 SQL conflict 정보 결합
- 자동 변환 성공, 실행 정책 적용 성공, 수동 검토, 미지원 상태 분류
- reason code와 source line 기반 결정적 수동 검토 목록
- 개인정보를 복제하지 않는 conversion summary JSON report
- 명시적으로 요청한 경우에만 쓰는 private converted JSONL artifact
- 기존 dataset CLI에 독립 subcommand 추가
- 합성 fixture 기반 converter·report·CLI 테스트
- 실제 5,385건 read-only 변환 시도와 상태·사유 통계 기록
- 계획 문서의 Phase 1 체크박스, 현재 체크포인트, ADR와 변경 이력 갱신

## Legacy dataset schema profile

물리 컬럼명을 ontology field로 변환할 때 이름 유사도를 다시 계산하거나 추측하지 않는다. versioned profile에 명시적으로 승인된 mapping만 사용한다.

초기 profile은 최소한 다음 정보를 가진다.

- profile schema version과 profile id
- source dialect: `postgres`
- source table allowlist: `courses`
- root entity: `CourseOffering`
- ontology version
- 물리 column → semantic field mapping
- 각 mapping의 expected source type 또는 허용 literal type
- 검토 상태와 근거

초기 검토 대상 mapping:

```text
courses.course_year   → CourseOffering.year
courses.subject_code  → Course.code
courses.section       → CourseOffering.section
courses.subject_name  → Course.name
courses.category      → Course.category
courses.credit_hours  → Course.credits
courses.professor     → Instructor.name
courses.capacity      → CourseOffering.capacity
courses.enrolled      → CourseOffering.enrolled
courses.dept_name     → Department.name
```

`lecture_time`, `class_mode` 등 현재 ontology에서 하나의 semantic field로 손실 없이 대응하지 않는 컬럼은 억지로 매핑하지 않는다. `lecture_time`을 `Schedule.start_time` 또는 `Schedule.end_time`으로 임의 분해하지 않는다.

이 profile은 학습 데이터 변환용 source metadata다. 운영 실행에 사용하는 `PublishedMappingProfile`인 것처럼 가장하거나 runtime publish gate를 우회하지 않는다.

## 변환 상태 계약

각 유효 dataset record는 다음 중 정확히 하나의 상태를 가진다.

- `CONVERTED_EXACT`: 현재 Query IR로 source intent를 그대로 표현 가능
- `CONVERTED_WITH_EXECUTION_POLICY`: source SQL에는 없던 안전 LIMIT 기본값만 적용됨
- `REVIEW_REQUIRED`: 사람의 의미 판단이나 교정이 필요함
- `UNSUPPORTED`: 현재 연구 범위 또는 Query IR 계약 밖이라 이번 단계에서 변환하지 않음

상태 우선순위와 reason code 순서를 계약으로 고정한다. 한 레코드에 여러 문제가 있으면 첫 문제만 남기지 말고 모든 reason code를 enum 순서로 보존한다.

이전 analyzer 상태는 입력 근거지만 최종 변환 상태와 동일하지 않다.

- `CURRENT_IR_CANDIDATE`만 자동 변환 경로에 진입할 수 있다.
- `MANUAL_REVIEW`는 `REVIEW_REQUIRED`로 전달하되 더 구체적인 변환 사유를 추가할 수 있다.
- `IR_EXTENSION_REQUIRED`는 현재 Query IR을 변경하지 않고 `UNSUPPORTED` 또는 의미 판단이 필요한 경우 `REVIEW_REQUIRED`로 보낸다.
- `UNSUPPORTED_OR_UNSAFE`는 변환하지 않는다.

## projection 변환 규칙

- projection은 단순 physical column만 자동 변환한다.
- source projection 순서를 Query IR `select` 순서로 보존한다.
- profile에 없는 table·column은 `UNMAPPED_SOURCE_COLUMN`으로 검토 목록에 남긴다.
- wildcard, expression projection, aggregate, function, alias 의존 결과명은 자동 변환하지 않는다.
- 같은 semantic field가 중복 projection되면 Query IR의 unique select 계약을 위반하므로 조용히 제거하지 않는다.
- double-quoted identifier가 profile에 없는 경우 실제 문자열 값으로 추측하지 않는다.
- projection이 여러 ontology entity의 field를 포함해도 root entity는 projection 첫 항목으로 추측하지 않고 profile의 `CourseOffering`을 사용한다.

## filter와 literal 변환 규칙

- top-level `AND`만 flatten하며 원래 predicate 순서를 보존한다.
- 왼쪽이 승인된 physical column이고 오른쪽이 단일 literal인 비교만 자동 변환한다.
- `=`, `!=`, `<`, `<=`, `>`, `>=`는 현재 ontology `Operator`에서 읽어 변환한다.
- operator가 해당 ontology field의 `allowed_operators`에 없으면 변환하지 않는다.
- literal은 string, integer, decimal, boolean만 현재 `FilterIR.value` 범위에서 처리한다.
- `NULL`, column-to-column 비교, expression, placeholder, cast가 필요한 값은 자동 변환하지 않는다.
- ontology data type과 literal type이 정확히 맞지 않으면 문자열 숫자 변환 같은 coercion을 조용히 적용하지 않는다.
- 음수와 decimal의 부호·정밀도를 손실하지 않는다. 현재 `float` 변환이 정밀도를 보존하지 못하면 수동 검토로 보낸다.
- 비교식의 column과 literal 위치가 뒤집힌 형태는 operator 반전 의미를 명시적으로 검증하지 않는 한 자동 변환하지 않는다.

`LIKE`는 다음 조건을 모두 만족할 때만 `Operator.CONTAINS`로 변환한다.

- 왼쪽이 string ontology field로 매핑된 column
- 오른쪽이 string literal
- pattern이 정확히 `%value%`
- 내부 `%`, `_`, escape 문자가 없음
- value가 빈 문자열이 아님

prefix, suffix, exact LIKE, `ILIKE`, complex wildcard와 double-quoted identifier RHS는 수동 검토 대상으로 남긴다.

## LIMIT 정책

- source SQL에 1~200 범위 integer literal LIMIT이 있으면 그대로 보존한다.
- 범위 밖, expression, placeholder, OFFSET 결합 LIMIT은 자동 변환하지 않는다.
- source SQL에 LIMIT이 없으면 현재 Query IR 계약상 configurable safe default를 적용한다. 초기값은 기존 계약과 같은 50이다.
- 기본 LIMIT 적용은 원본 SQL과 완전히 같은 의미라고 기록하지 않고 `CONVERTED_WITH_EXECUTION_POLICY`와 `DEFAULT_LIMIT_APPLIED` provenance를 남긴다.
- 안전 LIMIT과 사용자 의도 LIMIT을 report에서 구분한다.

## 의미 보존 검증

SQL 문자열을 다시 생성해 text equality만 비교하지 않는다. 데이터베이스에서 SQL을 실행하지도 않는다.

지원 subset에 대해 source AST에서 다음 intent signature를 만든다.

- root entity
- semantic projection 순서
- semantic filter field·operator·typed literal 순서
- source LIMIT 또는 default-limit provenance

생성한 `QueryIR`에서 같은 signature를 다시 만들고 정확히 비교한다. 불일치하면 변환 성공으로 숨기지 않고 `INTENT_SIGNATURE_MISMATCH`로 실패시킨다.

최종 Query IR은 기존 Pydantic model과 ontology/mapping validator를 통과해야 한다. validator 통과를 위해 source profile에서 runtime 검증용 mapping view를 만들 수 있지만, 이를 운영용 Published Mapping으로 저장하거나 publish하지 않는다.

## 중복·충돌 정책

- 원본 record와 source line을 임의로 제거하거나 재정렬하지 않는다.
- exact duplicate와 동일 질문·동일 SQL duplicate는 변환 통계에서 별도로 표시한다.
- 질문별 SQL conflict group의 레코드는 자동 학습 artifact 승인 대상으로 취급하지 않고 `QUESTION_SQL_CONFLICT`로 검토 목록에 보낸다.
- conflict를 다수결, 첫 레코드 또는 SQL hash 순으로 자동 해결하지 않는다.
- 중복 제거된 학습셋 생성은 이번 단계 범위가 아니다.

## 개인정보와 artifact 정책

- 기본 conversion report와 review list에는 질문, SQL, literal, 완전한 Query IR value를 저장하지 않는다.
- record 식별은 source line, canonical hash, shape hash와 안정적인 group hash를 사용한다.
- review item은 reason code와 redacted 구조 정보만 기본 저장한다.
- parse·validation error message에 원문이나 literal이 포함되지 않도록 정제한다.
- literal-redacted preview는 명시적 옵션과 record 수·문자 길이 상한에서만 허용한다.
- 질문과 literal을 포함한 converted JSONL은 별도의 명시적 `--private-output` 옵션에서만 생성한다.
- private artifact는 `.schema-adaptive/`처럼 gitignore된 위치를 기본 권장하고 저장소 tracked 경로에는 쓰지 못하게 보호한다.
- private artifact에는 schema version, dataset fingerprint, profile id/version, ontology version과 source line을 포함한다.
- 입력 절대 경로와 실행 시각은 deterministic report core에 넣지 않는다.
- 실제 dataset, 실제 converted JSONL, 실제 review report를 커밋하지 않는다.

## 오류와 안전 정책

- SQL을 어떤 데이터베이스에서도 실행하지 않는다.
- raw SQL을 rewrite해 정답 SQL로 저장하지 않는다.
- unknown table, unknown column, ambiguous quoted identifier와 type mismatch를 추측으로 해결하지 않는다.
- 한 record 변환 실패가 전체 분석을 중단하지 않는다.
- conversion 예외는 원문 비노출 record-level issue로 변환한다.
- input, public report, private output 사이의 동일 경로·symlink·hardlink를 모두 차단한다.
- 출력은 기본 non-overwrite이며 `--force`에서만 원자 교체한다.
- public report 저장 성공 후 private output 저장 실패처럼 artifact가 부분적으로 섞이지 않도록 commit 순서 또는 bundle manifest 정책을 명시한다.
- 같은 입력, profile, ontology와 설정은 동일한 report core·review 순서·private JSONL 순서를 생성해야 한다.

## CLI 예시

현재 dataset CLI 구조를 재사용한다.

```powershell
python -m backend.adaptive.dataset convert-ir `
  --input "C:\path\to\dataset.csv" `
  --dataset-id cnu-original-v1 `
  --profile backend/adaptive/dataset/profiles/cnu-courses-v1.yaml `
  --ontology backend/adaptive/ontology/course-domain.yaml `
  --output ".schema-adaptive\reports\ir-conversion.json"
```

민감한 변환 결과가 실제로 필요한 경우에만 다음 옵션을 추가한다.

```powershell
  --private-output ".schema-adaptive\private\cnu-query-ir-v1.jsonl"
```

기존 `inspect`와 `analyze-sql` subcommand 및 JSON 계약을 깨지 않는다.

## 필수 reason code

최소한 다음 사유를 enum으로 정의하고 결정적 순서를 고정한다.

- `ANALYZER_NOT_CURRENT_IR_CANDIDATE`
- `QUESTION_SQL_CONFLICT`
- `SOURCE_PROFILE_MISMATCH`
- `UNKNOWN_SOURCE_TABLE`
- `UNMAPPED_SOURCE_COLUMN`
- `AMBIGUOUS_QUOTED_IDENTIFIER`
- `UNSUPPORTED_PROJECTION`
- `DUPLICATE_SEMANTIC_PROJECTION`
- `UNSUPPORTED_BOOLEAN_STRUCTURE`
- `UNSUPPORTED_OPERATOR`
- `OPERATOR_NOT_ALLOWED_FOR_FIELD`
- `NON_LITERAL_FILTER_VALUE`
- `LITERAL_TYPE_MISMATCH`
- `UNSAFE_LITERAL_COERCION`
- `UNSUPPORTED_LIKE_PATTERN`
- `EMPTY_CONTAINS_VALUE`
- `INVALID_SOURCE_LIMIT`
- `DEFAULT_LIMIT_APPLIED`
- `QUERY_IR_VALIDATION_FAILED`
- `INTENT_SIGNATURE_MISMATCH`

이전 analyzer reason code를 문자열로 복사해 새 enum인 것처럼 만들지 않는다. 필요하면 원래 analyzer status와 reason code를 별도 provenance field로 보존한다.

## 필수 테스트

- 승인된 `courses` source profile loading과 version 검증
- profile의 unknown/duplicate physical column과 unknown semantic field 거부
- ontology version·dialect·table 불일치 거부
- 단순 projection의 semantic field 변환과 순서 보존
- 여러 ontology entity field를 포함해도 root entity가 결정적임
- `AND` filter flatten과 predicate 순서 보존
- 여섯 비교 operator의 변환
- ontology field별 operator allowlist 검증
- string, integer, decimal, boolean literal 처리
- NULL, placeholder, cast, expression과 column-to-column 비교 거부
- literal type mismatch와 unsafe coercion 차단
- 정확한 contains LIKE 변환
- prefix/suffix/exact/complex/empty LIKE 검토 전환
- double-quoted 값 후보를 string literal로 자동 변환하지 않음
- unmapped `lecture_time`, `class_mode` 등 명시적 검토
- 명시 LIMIT 보존
- LIMIT 없는 SQL의 default 50과 `CONVERTED_WITH_EXECUTION_POLICY`
- 범위 밖·비정수 LIMIT 및 OFFSET 거부
- wildcard, alias, function, aggregate, arithmetic projection 거부
- OR, NOT, IN, BETWEEN, IS NULL 거부 또는 이전 분석 상태 전달
- source와 Query IR intent signature 동등성
- 기존 Query IR validator 통과
- exact duplicate와 question/SQL duplicate provenance 보존
- question conflict 전체를 review 대상으로 분류
- 복수 reason code의 enum 순서 보존
- record와 review list의 source line 결정성
- 기본 report에 질문·SQL·literal·Query IR value 비노출
- preview redaction 및 개수·길이 제한
- private output opt-in과 tracked path 차단
- input/output/private-output 동일 경로·symlink·hardlink 차단
- atomic non-overwrite 및 `--force`
- malformed record 이후 계속 변환
- 실제 5,385건 전체 변환 시도 및 상태 합계 일치
- 기존 `inspect`, `analyze-sql`, 전체 테스트 회귀 없음

## 제외 범위

- Query IR Pydantic 모델 변경
- aggregate, DISTINCT, GROUP BY, ORDER BY, OR, 산술식을 위한 IR 확장
- legacy double-quoted identifier를 문자열 literal로 자동 수정
- 수동 검토 항목의 관리자 승인·교정 UI
- conflict 자동 해결
- 중복 제거 또는 train/validation/test split
- 실제 식별값 가명화
- 변환 결과를 학습 데이터로 최종 배포
- SQL 실행 또는 PostgreSQL 연결
- semantic execution equivalence 평가
- 공통 평가 질문·기대 결과 작성
- evaluation runner
- 모델 실행, Ollama, repair loop

## 설계 원칙

- source profile, AST subset extractor, converter, intent verifier, report serializer, private writer, CLI 책임을 분리한다.
- 모든 저장 계약과 오류·상태·reason code를 Pydantic과 enum으로 명시한다.
- analyzer의 후보 판정을 그대로 신뢰하지 않고 converter가 실제 AST와 profile을 다시 검증한다.
- physical→semantic mapping은 profile에서만 읽고 column 이름을 추측하지 않는다.
- 기존 Query IR과 ontology operator·data type을 중복 정의하지 않는다.
- source projection·filter 순서와 literal type을 보존한다.
- 자동 변환 범위를 넓히기 위해 의미가 불명확한 SQL을 임의 보정하지 않는다.
- 변환 성공률뿐 아니라 review 비율과 reason 분포도 연구 결과로 남긴다.
- 기존 legacy 시스템, compiler, AST execution policy를 변경하지 않는다.

## 서브 에이전트

- 메인 에이전트가 먼저 source profile 계약, root entity, LIMIT provenance, 변환 상태와 reason code를 정의한다.
- 한 에이전트는 source profile loader와 validation을 담당한다.
- 다른 에이전트는 AST subset→Query IR converter와 intent signature 검증을 담당한다.
- 별도 에이전트는 report·private artifact 안전성 및 합성·적대적 테스트를 담당한다.
- 에이전트별 수정 파일이 겹치지 않게 한다.
- 메인 에이전트가 CLI 통합, 실제 5,385건 분석과 문서 갱신을 담당한다.
- 구현 후 별도 리뷰 에이전트가 quoted identifier 오인, literal coercion, operator 방향, LIMIT 의미, conflict 누락, 개인정보 노출과 결정성을 검사한다.
- 리뷰 지적 수정 후 실제 데이터 변환과 전체 테스트를 다시 실행한다.

## 완료 기준

- 실제 5,385개 record가 모두 conversion result 또는 구조화된 review/unsupported 결과를 가짐
- 자동 변환은 승인된 source profile과 `CURRENT_IR_CANDIDATE`에만 제한됨
- 변환된 Query IR이 기존 Pydantic·ontology validator를 통과함
- projection·filter 순서와 typed literal 의미가 intent signature에서 보존됨
- LIMIT 기본값 적용과 exact 변환이 구분됨
- double-quoted 값, unmapped field와 type mismatch가 자동 보정되지 않음
- conflict group이 자동 학습 후보로 통과하지 않음
- 상태 합계가 5,385개와 일치하고 reason 통계가 결정적임
- 실제 데이터 기반 상태·사유 통계를 계획 문서에 기록함
- 기본 report에 실제 질문·SQL·literal·IR value가 노출되지 않음
- 실제 dataset과 생성 artifact가 저장소에 추가되지 않음
- 기존 dataset CLI 회귀 없음
- 기존 전체 테스트 회귀 없음
- `compileall` 통과
- `git diff --check` 통과
- working tree clean

## 문서

- Phase 1의 기존 SQL→Query IR 변환과 수동 검토 목록 체크박스 갱신
- 실제 변환 상태와 주요 review reason 통계 기록
- source profile, root entity, LIMIT provenance, conflict 차단 정책이 중요하면 ADR 추가
- 다음 체크포인트를 Phase 1의 실제 식별값 가명화 도구로 지정
- 날짜가 포함된 변경 이력 추가

## 커밋

- 과거 Conventional Commit 형식을 따른다.
- source profile/contracts, converter/verifier, report/CLI, 테스트, 문서를 기능별로 분리 커밋한다.
- 각 커밋 전 staged diff를 확인한다.
- 실제 dataset, 질문·SQL·literal·private Query IR artifact와 실제 review report를 커밋하지 않는다.
- 원격 push는 하지 않는다.

이번 단계까지만 개발하고 Query IR 확장, 수동 검토 UI, 가명화, 평가 질문 작성과 evaluation runner는 다음 작업으로 남겨줘.
