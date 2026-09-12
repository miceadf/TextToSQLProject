# 다음 개발 프롬프트: Phase 1 실제 식별값 가명화

다음 단계인 기존 학습 데이터셋의 실제 식별값 가명화 도구를 개발해줘.

작업 전 반드시 다음을 확인해.

- `docs/schema-adaptive-text-to-sql-plan.md` 전체
- `docs/prompts/phase1-01-jsonl-loader-quality-report.md`
- `docs/prompts/phase1-02-sql-normalization-ast-analysis.md`
- `docs/prompts/phase1-03-sql-to-query-ir-conversion.md`
- `backend/adaptive/dataset`의 loader, conversion contracts, source profile, converter, report, storage와 CLI
- `PrivateConvertedRecord`, `QueryIR`, `FilterIR`, `DuplicateProvenance`, `LimitProvenance` 계약
- course-domain ontology의 semantic field와 data type
- `.gitignore`의 `.schema-adaptive/` 정책
- 저장소의 Conventional Commit 기록
- 작업 시작 시 working tree 상태

## 목표

SQL→Query IR 변환 단계에서 생성한 private converted JSONL을 입력으로 받아, 실제 교수명·학과명·과목명·과목코드처럼 특정 학교를 식별하거나 원본 데이터와 연결할 수 있는 값을 안정적인 가명으로 교체한다.

질문의 표현과 Query IR filter value를 항상 함께 바꾸고, 중복·충돌 관계와 실행 의도는 보존한다. 자동으로 안전성을 확인할 수 없는 record는 부분적으로 가명화한 결과를 내보내지 않고 구조화된 수동 검토 목록으로 보낸다.

이번 결과물은 재식별 위험이 전혀 없는 익명 데이터가 아니라 연구·개발용 pseudonymized artifact다. 외부 공개 가능 여부는 별도의 라이선스·개인정보 검토 전까지 확정하지 않는다.

## 현재 기준선

직전 SQL→Query IR 변환 결과는 다음과 같다.

- 전체 dataset record: 5,385개
- `CONVERTED_EXACT`: 11개
- `CONVERTED_WITH_EXECUTION_POLICY`: 3,201개
- private converted record: 3,212개
- `REVIEW_REQUIRED`: 1,059개
- `UNSUPPORTED`: 1,114개
- 질문별 SQL conflict 영향 record: 6개이며 자동 변환 차단됨

이 수치는 입력 검증 기준이지 코드에 하드코딩할 값이 아니다. 이번 단계는 private converted record만 처리한다. 이전 단계에서 review 또는 unsupported로 분류되어 Query IR이 없는 record를 억지로 복원하거나 가명화하지 않는다.

## 용어와 보안 경계

- 문서와 코드에서는 가능한 한 `anonymization`보다 `pseudonymization`을 사용한다.
- 같은 secret key와 profile에서 같은 semantic field·원본 값은 항상 같은 가명을 가져야 한다.
- 다른 semantic field의 같은 문자열은 기본적으로 서로 다른 namespace에서 가명화한다.
- 원본 값의 일반 SHA-256처럼 사전 대입에 취약한 unkeyed hash를 가명으로 사용하지 않는다.
- 가명화 key와 원본→가명 대응표는 public report, 로그, 예외, Git history에 남기지 않는다.
- 가명화 결과만으로 법적·통계적 익명성을 보장한다고 주장하지 않는다.
- 원본 private converted JSONL과 key file은 계속 민감 자료로 취급한다.

## 이번 범위

- versioned pseudonymization profile과 Pydantic 계약
- semantic field별 `PSEUDONYMIZE`, `PRESERVE`, `REVIEW` 정책
- HMAC-SHA-256 기반 결정적 pseudonym token 생성
- secret key file과 명시적 key id 검증
- 입력 private JSONL streaming loader와 version 검증
- 전체 입력을 먼저 읽어 sensitive value lexicon 구성
- 질문·instruction과 Query IR filter value의 동기화된 치환
- Unicode NFC와 결정적 longest-match-first 치환
- ASCII code의 token boundary와 한글 문자열 치환 규칙
- 동일 surface의 semantic namespace 충돌과 겹치는 match 탐지
- record 단위 all-or-nothing 가명화
- 가명화 후 원본 sensitive value 잔존 검사
- Query IR 구조·field·operator·비민감 literal·LIMIT 보존 검증
- source line, SQL hash, duplicate/conflict group id를 공개 artifact에 직접 복사하지 않는 keyed lineage token
- duplicate와 conflict 관계의 동등성 보존 검증
- versioned pseudonymized JSONL 계약
- 개인정보를 복제하지 않는 summary report와 private review report
- 원자적 artifact bundle 저장과 manifest
- 기존 dataset CLI에 독립 `pseudonymize-ir` subcommand 추가
- 합성 fixture 기반 단위·적대적·CLI 테스트
- 실제 3,212개 private converted record의 read-only/in-memory 가명화 시도와 상태·사유 통계 기록
- 계획 문서의 Phase 1 체크박스, 현재 체크포인트, ADR와 변경 이력 갱신

## 초기 semantic field 정책

초기 profile은 최소한 다음 정책을 명시한다.

```text
Course.name               → PSEUDONYMIZE, namespace=course-name
Course.code               → PSEUDONYMIZE, namespace=course-code
Department.name           → PSEUDONYMIZE, namespace=department-name
Instructor.name           → PSEUDONYMIZE, namespace=instructor-name
Course.category           → PRESERVE
Course.credits            → PRESERVE
CourseOffering.year       → PRESERVE
CourseOffering.section    → PRESERVE
CourseOffering.capacity   → PRESERVE
CourseOffering.enrolled   → PRESERVE
```

- 정책에 없는 semantic field를 자동으로 `PRESERVE`로 간주하지 않는다.
- unknown field, 중복 field policy, 잘못된 namespace와 ontology version 불일치를 거부한다.
- `PSEUDONYMIZE`는 기본적으로 string field에만 허용한다.
- 숫자·boolean·date/time을 가명화하려면 별도 형식 보존 규칙과 명시적 profile 변경이 필요하다.
- `Course.category`는 초기에는 질의 의미를 보존하기 위해 유지하지만, 실제 값 분포와 학교 고유 용어 여부를 report에 집계하고 공개 전 재검토 대상으로 문서화한다.
- `CourseOffering.section`도 초기에는 유지하되 학교·학생을 식별하는 값이 포함되지 않는다는 전제와 재검토 필요성을 기록한다.

## secret key와 token 계약

- Python 표준 라이브러리의 HMAC-SHA-256을 사용한다.
- key는 CLI의 `--key-file`로만 읽고 command-line 문자열, 환경변수, source code에 직접 넣지 않는다.
- key file은 최소 32 byte의 무작위 binary 값이어야 한다.
- 빈 key, 짧은 key, 읽을 수 없는 key, 저장소에서 Git tracked인 key file을 거부한다.
- `--key-id`는 사람이 key rotation을 식별하는 비밀이 아닌 label이며 output metadata에 기록할 수 있다.
- HMAC 입력은 tool version, profile id/version, semantic namespace, Unicode NFC 원본 값으로 domain separation한다.
- token 형식은 versioned contract로 고정한다. 예: `<INSTRUCTOR_AB12CD34EF56>`.
- 최소 token 길이와 alphabet을 계약으로 명시하고 질문과 JSON에서 안전하게 표현한다.
- truncated token collision을 반드시 검사한다.
- collision이 있으면 입력 순서에 의존해 번호를 붙이지 말고, 충돌한 모든 token의 digest 길이를 결정적으로 늘려 유일하게 만든다.
- 같은 key·profile·input에서는 입력 record 순서와 관계없이 같은 token을 생성해야 한다.
- 다른 key 또는 namespace에서는 같은 원본 값도 다른 token을 생성해야 한다.
- key 원문, HMAC 중간값과 원본→token 사전은 report나 로그에 출력하지 않는다.

## 입력 계약

- 입력은 이전 단계의 `PrivateConvertedRecord` JSONL만 허용한다.
- `schema_version`, dataset fingerprint, profile id/version, ontology version과 record별 conversion status를 검증한다.
- `CONVERTED_EXACT`와 `CONVERTED_WITH_EXECUTION_POLICY`만 처리한다.
- record의 Query IR은 현재 Pydantic 계약과 ontology validator를 다시 통과해야 한다.
- 같은 artifact 내부의 dataset/profile/ontology metadata 불일치를 fatal error로 처리한다.
- malformed JSON, object가 아닌 행, version 불일치와 잘못된 field type은 줄 번호가 포함된 issue로 남긴다.
- 기본 정책은 record-level 오류 이후 계속 읽되, key·profile·artifact metadata처럼 전체 결과의 일관성을 깨는 오류는 fatal로 처리한다.
- 원본 input file을 수정하지 않는다.

## sensitive value lexicon

- 각 record의 Query IR filter 중 profile이 `PSEUDONYMIZE`로 지정한 field의 string value만 원본 sensitive value 후보로 수집한다.
- 원본 값과 NFC 정규화 값을 모두 추적하되 public report에는 저장하지 않는다.
- empty 또는 whitespace-only value는 자동 처리하지 않는다.
- NFC 이후 서로 다른 원본 값이 같은 값으로 합쳐지는 normalization collision을 명시적으로 탐지한다.
- lexicon key는 semantic namespace와 normalized value다.
- 같은 normalized surface가 여러 namespace에서 서로 다른 token을 갖는 경우 질문 text만으로 namespace를 결정할 수 없으므로 `AMBIGUOUS_SURFACE_NAMESPACE`로 처리한다.
- 원본 값 길이, 문자 종류와 field별 고유 값 개수는 구간화한 통계만 public report에 기록하고 실제 값은 기록하지 않는다.
- 질문에만 있고 Query IR에는 없는 새 고유명사를 NER나 추측으로 lexicon에 추가하지 않는다.
- 이번 단계에서 탐지할 수 없는 잔여 고유명사가 있을 수 있음을 report와 문서에 명시한다.

## 질문·instruction 치환 규칙

- output text는 Unicode NFC와 LF 줄바꿈으로 통일하며 이 변화를 provenance로 기록한다.
- lexicon의 normalized surface를 길이 내림차순, namespace, digest 순으로 정렬해 longest-match-first로 처리한다.
- 같은 위치에서 여러 후보가 일치하거나 match 범위가 겹쳐 결과가 달라질 수 있으면 임의 선택하지 않는다.
- ASCII letter·digit·underscore로 구성된 과목코드는 더 긴 identifier 내부를 잘못 치환하지 않도록 양쪽 token boundary를 검사한다.
- 한글 교수명·학과명·과목명은 NFC exact substring만 자동 치환한다.
- 띄어쓰기 제거, 조사 제거, 오타 교정, fuzzy matching, 형태소 분석을 조용히 적용하지 않는다.
- record의 sensitive Query IR filter value가 question과 instruction 어디에도 나타나지 않으면 의미 대응을 확인할 수 없으므로 `SENSITIVE_VALUE_NOT_FOUND_IN_TEXT`로 검토한다.
- question에 같은 sensitive value가 여러 번 등장하면 모든 occurrence를 같은 token으로 교체한다.
- instruction에도 lexicon의 sensitive value가 나타나면 같은 규칙으로 교체한다.
- 치환 후 question·instruction·Query IR 어디에도 해당 record 및 전역 lexicon의 원본 sensitive value가 남지 않았는지 검사한다.
- 단순 substring 검사는 짧은 값에서 false positive를 만들 수 있으므로 lexicon과 같은 boundary 규칙을 재사용한다.
- 자동 검증에 실패한 record는 일부 field만 바뀐 output으로 내보내지 않는다.

## Query IR 의미 보존 규칙

- `entity`, `select` 순서, filter 순서, filter field, operator와 LIMIT를 변경하지 않는다.
- `PSEUDONYMIZE` field의 string filter value만 대응 token으로 교체한다.
- `PRESERVE` field의 value는 타입과 값을 그대로 유지한다.
- `REVIEW` field가 record에 나타나면 자동 output을 만들지 않는다.
- sensitive field의 non-string value는 추측하거나 문자열로 coercion하지 않는다.
- 가명화 전후 Query IR에서 값 이외의 구조가 동일한지 structural signature로 검증한다.
- 가명화된 question과 Query IR에는 동일한 token 문자열을 사용한다.
- 기존 conversion status와 LIMIT provenance를 보존한다.
- 가명 token을 원본 SQL로 다시 compile하거나 실행하지 않는다.

## lineage와 중복·충돌 정책

- 공개 가능한 pseudonymized record에는 원본 `source_line`, canonical SQL hash, shape hash와 기존 group id를 그대로 넣지 않는다.
- 각 값은 별도 HMAC domain을 사용한 stable lineage token으로 변환한다.
- record id는 dataset id/fingerprint와 source line을 포함한 keyed 입력에서 생성한다.
- canonical/shape lineage가 필요하면 원래 hash 자체 대신 keyed token만 저장한다.
- exact duplicate와 question-SQL duplicate group 관계는 group id를 keyed token으로 바꿔 보존한다.
- conflict record는 이전 단계에서 private converted artifact에 없어야 한다. 발견하면 자동 처리하지 않고 입력 계약 오류로 보고한다.
- 같은 원본 group의 record가 같은 transformed group id를 갖고 다른 group과 섞이지 않는지 검증한다.
- output record 순서는 source line 대신 생성 전 입력 순서와 무관한 stable record id 순으로 결정한다.
- 원본 source line과 변환된 record id의 대응은 local private review artifact에만 둘 수 있으며 public summary나 pseudonymized JSONL에는 넣지 않는다.

## 출력 artifact

최소한 다음 세 산출물을 구분한다.

1. `pseudonymized-query-ir.jsonl`
   - 가명화에 성공한 record만 포함
   - versioned schema, dataset id, pseudonymization profile, key id, stable record id 포함
   - 원본 source line·SQL hash·원본 group id·원본 sensitive value 제외
2. `pseudonymization-summary.json`
   - 상태·reason·field별 집계와 입력/output fingerprint
   - 질문, instruction, Query IR value, token과 원본 식별값 제외
3. `pseudonymization-review.json`
   - local private review용
   - source line, reason code와 원문 비노출 구조 정보만 포함
   - `.schema-adaptive/` 아래에만 저장하고 commit 금지

원본→가명 mapping table은 기본 산출물로 만들지 않는다. 같은 key와 profile로 다시 계산할 수 있게 한다. 별도 mapping export가 정말 필요하다면 이번 범위에 임의로 추가하지 말고 보안 설계를 먼저 문서화한다.

## 상태와 reason code

각 입력 record는 다음 중 정확히 하나의 상태를 가진다.

- `PSEUDONYMIZED_EXACT`
- `REVIEW_REQUIRED`
- `UNSUPPORTED`

최소한 다음 reason code를 enum으로 정의하고 순서를 고정한다.

- `INPUT_SCHEMA_VERSION_MISMATCH`
- `INPUT_METADATA_MISMATCH`
- `NON_CONVERTED_INPUT`
- `QUESTION_SQL_CONFLICT_PRESENT`
- `PROFILE_VERSION_MISMATCH`
- `ONTOLOGY_VERSION_MISMATCH`
- `UNKNOWN_SEMANTIC_FIELD`
- `FIELD_POLICY_MISSING`
- `FIELD_REQUIRES_REVIEW`
- `NON_STRING_SENSITIVE_VALUE`
- `EMPTY_SENSITIVE_VALUE`
- `NORMALIZATION_COLLISION`
- `AMBIGUOUS_SURFACE_NAMESPACE`
- `OVERLAPPING_TEXT_MATCH`
- `SENSITIVE_VALUE_NOT_FOUND_IN_TEXT`
- `ORIGINAL_VALUE_REMAINS`
- `PSEUDONYM_COLLISION_RESOLVED`
- `QUERY_IR_STRUCTURE_CHANGED`
- `QUESTION_IR_TOKEN_MISMATCH`
- `OUTPUT_VALIDATION_FAILED`
- `PSEUDONYMIZATION_ERROR`

한 record에 여러 사유가 있으면 첫 사유만 남기지 않고 enum 순서로 모두 보존한다. `PSEUDONYM_COLLISION_RESOLVED`처럼 안전하게 해결된 provenance와 실패 reason을 상태 계약에서 구분한다.

## report와 개인정보 정책

- public summary에는 질문, instruction, 원본 Query IR value, 가명 token, key 원문과 source line을 저장하지 않는다.
- error message는 입력 문자열이나 key material을 포함하지 않는 고정된 문구를 사용한다.
- field별 고유 sensitive value 수, 성공·review·unsupported 수와 reason 분포만 결정적으로 기록한다.
- sample이나 preview는 이번 단계의 public report에서 제공하지 않는다.
- local review report에도 원본 sensitive value를 복제하지 않고 source line과 reason만 둔다.
- input 절대 경로와 실행 시각은 deterministic report core에 포함하지 않는다.
- dataset fingerprint, input artifact fingerprint, profile id/version, ontology version, algorithm version과 key id를 기록한다.
- pseudonymized artifact도 별도 공개 검토 전까지 Git에 commit하지 않는다.

## 저장과 실패 원자성

- input, key, pseudonymized output, summary, review와 manifest 사이의 동일 경로·symlink·hardlink를 모두 차단한다.
- key file을 어떤 출력으로도 덮어쓰지 않는다.
- 실제 artifact는 `.schema-adaptive/` 아래처럼 gitignore된 경로에만 쓴다.
- Git tracked target을 명시적으로 거부한다.
- 기본은 non-overwrite이며 `--force`에서만 교체한다.
- 모든 파일을 temporary generation directory에 완전히 쓰고 fsync·검증한 뒤 manifest를 마지막에 원자 교체한다.
- 일부 output만 새 generation이고 나머지는 이전 generation인 상태를 성공으로 노출하지 않는다.
- 실패 시 이전 manifest와 이전 generation은 그대로 사용할 수 있어야 한다.
- key나 원본 문자열이 temporary filename, 예외 또는 stdout/stderr에 들어가지 않게 한다.

## CLI 예시

현재 dataset CLI에 독립 subcommand를 추가하되 기존 command가 항상 원본 JSONL loader를 먼저 실행하는 구조는 command별로 안전하게 분리한다.

```powershell
python -m backend.adaptive.dataset pseudonymize-ir `
  --input ".schema-adaptive\private\cnu-query-ir-v1.jsonl" `
  --dataset-id cnu-anonymized-v1 `
  --profile backend/adaptive/dataset/profiles/cnu-pseudonymization-v1.yaml `
  --ontology backend/adaptive/ontology/course-domain.yaml `
  --key-file ".schema-adaptive\secrets\pseudonymization-v1.key" `
  --key-id local-key-v1 `
  --output-dir ".schema-adaptive\pseudonymized\cnu-v1"
```

- 기존 `inspect`, `analyze-sql`, `convert-ir` subcommand와 JSON 계약을 깨지 않는다.
- CLI stdout에는 성공 여부, dataset id, generation id, 비민감 summary만 출력한다.
- key path, key fingerprint, 원본 값과 가명 token을 stdout/stderr에 출력하지 않는다.

## 필수 테스트

- versioned pseudonymization profile loading
- unknown·duplicate field policy, unknown namespace와 ontology version 불일치 거부
- 초기 10개 semantic field가 빠짐없이 명시됨
- key file 최소 길이, empty/short key와 tracked key 거부
- 같은 key·profile·field·원본 값의 같은 token
- 다른 key, namespace 또는 원본 값의 다른 token
- token alphabet·길이·prefix 계약
- truncated HMAC collision의 결정적 길이 확장
- 입력 private JSONL 정상 loading과 metadata 일관성
- malformed JSON, non-object, 잘못된 schema version과 field type
- non-converted status와 conflict provenance 거부
- sensitive filter value와 question의 동시 치환
- instruction에 포함된 sensitive value 치환
- 같은 값의 복수 occurrence 전체 치환
- 여러 sensitive value의 longest-match-first 처리
- ASCII 과목코드 boundary 처리
- 한글 NFC exact match
- 띄어쓰기·오타·형태 변형을 임의로 fuzzy match하지 않음
- semantic namespace가 모호한 같은 surface 차단
- 겹치는 text match의 결정적 처리 또는 review 전환
- question에서 찾지 못한 sensitive IR value 검토 전환
- empty·non-string sensitive value 차단
- `PRESERVE` field의 type과 값 유지
- `REVIEW` field 포함 record 차단
- entity·select·filter order·field·operator·LIMIT와 provenance 불변
- question과 Query IR token 일치
- 원본 sensitive value 잔존 검사
- 한 record 실패 시 부분 가명화 output 없음
- 같은 입력·key·profile의 byte-identical JSONL과 report core
- 입력 순서를 바꿔도 token과 stable record id가 같음
- source line, 원본 SQL hash와 원본 group id가 output에 직접 남지 않음
- duplicate group 관계가 transformed lineage에서도 유지됨
- public report와 error에 원본 값·token·key 비노출
- output bundle manifest의 generation 일관성
- non-overwrite, `--force`, 중간 실패 rollback
- input/key/output의 동일 경로·symlink·hardlink 차단
- 기존 `inspect`, `analyze-sql`, `convert-ir` CLI 회귀 없음
- 실제 private converted record 3,212개 전체 처리 시도와 상태 합계 일치
- 실제 output에서 lexicon 기반 원본 sensitive value 잔존 0건
- 기존 전체 테스트 회귀 없음

## 실제 데이터 검증 정책

- 실제 dataset과 private converted artifact는 read-only로 다룬다.
- 실제 key는 저장소에 만들거나 commit하지 않는다.
- 개발 검증에는 temporary directory의 ephemeral 32-byte key를 사용하고 종료 후 폐기한다.
- 실제 3,212개 변환 record를 처리하되 output artifact와 review report는 commit하지 않는다.
- 성공률만 기록하지 말고 field별 고유 값 수, review reason과 text-match 실패율을 기록한다.
- lexicon 기반 leak scan 0건은 “알려진 원본 값이 남지 않았다”는 뜻일 뿐, 알려지지 않은 고유명사까지 완전 제거했다는 뜻이 아님을 문서에 명시한다.
- `Course.category`, `CourseOffering.section`, instruction의 학교 고유 정보 잔존 가능성을 별도 검토 항목으로 기록한다.

## 제외 범위

- review/unsupported SQL을 Query IR로 복구
- legacy double-quoted identifier 자동 수정
- SQL 원문 재작성 또는 SQL 실행
- NER, LLM, 외부 API를 이용한 고유명사 추측
- fuzzy matching, 형태소 기반 자동 보정
- 법적·통계적 익명성 보장
- 원본→가명 mapping table의 평문 export
- 관리자 review UI
- Query IR 모델 확장
- aggregate, DISTINCT, GROUP BY, ORDER BY, OR, 산술식 지원
- 중복 제거와 train/validation/test split
- 공통 평가 질문·기대 결과 작성
- evaluation runner
- Ollama/PostgreSQL 연결
- repair loop

## 설계 원칙

- profile/contracts, key provider, token generator, private loader, lexicon builder, text rewriter, IR transformer, leak auditor, report, storage와 CLI 책임을 분리한다.
- 상태, reason, lineage와 artifact를 Pydantic 및 enum으로 명시한다.
- 원본 식별값을 로그·예외·public artifact에 넣지 않는다.
- HMAC domain separation과 token format을 unit test로 고정한다.
- field policy가 없는 값을 추측으로 보존하거나 가명화하지 않는다.
- 질문과 Query IR 중 하나만 바뀐 record를 성공으로 내보내지 않는다.
- 실패한 record는 부분 결과 없이 review로 보낸다.
- 기존 conversion artifact와 Query IR 계약을 변경하지 않는다.
- 기존 legacy 시스템과 schema-adaptive 실행 경로를 변경하지 않는다.
- 가명화율뿐 아니라 review 비율과 알려진 값 잔존 검사 결과를 연구 지표로 남긴다.

## 서브 에이전트

- 메인 에이전트가 먼저 field policy, HMAC domain/token format, text match 경계, lineage 변환과 all-or-nothing 상태 계약을 정의한다.
- 한 에이전트는 profile·contracts·key provider·token generator를 담당한다.
- 다른 에이전트는 private loader·lexicon·text/IR transformer·leak auditor를 담당한다.
- 별도 에이전트는 artifact bundle·CLI 안전성과 합성·적대적 테스트를 담당한다.
- 에이전트별 수정 파일이 겹치지 않게 한다.
- 메인 에이전트가 통합, 실제 3,212건 검증, 계획 문서 갱신을 담당한다.
- 구현 후 별도 리뷰 에이전트가 key 노출, unkeyed hash, token collision, namespace 혼동, 부분 치환, question-IR 불일치, 원본 값 잔존, lineage linkability, 결정성과 저장 실패 원자성을 검사한다.
- 리뷰 지적 수정 후 실제 데이터 검증과 전체 테스트를 다시 실행한다.

## 완료 기준

- private converted JSONL의 모든 행이 성공·review·unsupported 중 하나로 결정적으로 분류됨
- 초기 sensitive field 네 종류가 HMAC 기반 stable token으로 교체됨
- 같은 semantic value의 question·instruction·Query IR 표현이 일치함
- 비민감 Query IR 구조와 typed value가 보존됨
- 자동 처리 실패 record가 부분 가명화 output에 포함되지 않음
- 공개 artifact에 source line, 원본 SQL hash, 원본 group id와 sensitive value가 직접 남지 않음
- duplicate 관계가 keyed lineage token으로 보존됨
- 같은 입력·key·profile은 byte-identical artifact와 report core 생성
- 실제 private converted record 전체 상태 합계가 3,212개와 일치함
- 알려진 sensitive lexicon 기준 원본 값 잔존 0건
- key, 실제 dataset, private input, 실제 pseudonymized output과 review report가 저장소에 추가되지 않음
- 기존 dataset CLI 회귀 없음
- 기존 전체 테스트 회귀 없음
- `compileall` 통과
- `git diff --check` 통과
- working tree clean

## 문서

- Phase 1의 실제 식별값 가명화 도구 체크박스 갱신
- 실제 처리 상태, field별 고유 값 수, review reason과 leak audit 결과 기록
- pseudonymization 용어, HMAC key 관리, field policy, lineage token과 공개 한계에 대한 ADR 추가
- 다음 체크포인트를 Phase 2의 공통 평가 질문 30~50개와 기대 semantic result 작성으로 지정
- 날짜가 포함된 변경 이력 추가

## 커밋

- 과거 Conventional Commit 형식을 따른다.
- profile/contracts/key, transformer/auditor, report/storage/CLI, 테스트, 문서를 기능별로 분리 커밋한다.
- 각 커밋 전 staged diff를 확인한다.
- key, 실제 dataset, private converted JSONL, 실제 pseudonymized JSONL, 실제 review report를 commit하지 않는다.
- 원격 push는 하지 않는다.

이번 단계까지만 개발하고 수동 검토 UI, Query IR 확장, 데이터 split, 평가 질문·기대 결과와 evaluation runner는 다음 작업으로 남겨줘.
