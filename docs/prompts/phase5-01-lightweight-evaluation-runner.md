# 다음 개발 프롬프트: Phase 5 경량 evaluation runner

다음 단계인 **FakeModel·gold Query IR·세 SQLite fixture 기반 경량 evaluation runner**를 개발해줘.

작업 전 반드시 다음을 확인해.

- `docs/schema-adaptive-text-to-sql-plan.md` 전체, Phase 5 체크박스와 현재 체크포인트
- `docs/prompts/phase2-01-common-evaluation-questions.md`와 `backend/adaptive/evaluation`의 40개 gold 사례, loader, 정적 validator, typed multiset comparator, coverage report
- `backend/adaptive/harness`의 `QueryModel`, `FakeModel`, `ExecutionHarness`, `HarnessResult`·오류·trace 계약
- `SchemaAdaptiveSQLiteCompiler`, `SchemaAdaptiveAstPolicy`, `SQLiteReadOnlyExecutor`, `to_harness_mapping`, fixture별 Published Mapping과 Catalog FK graph
- 기존 gold cross-schema 실행 테스트와 저장소 Conventional Commit 기록
- 작업 시작 시 working tree 상태

## 목표

하나의 질문 세트를 flat·normalized·legacy 스키마에 동일하게 적용하고, **모델 출력 Query IR → 검증 → JOIN 계획·SQL 컴파일 → AST policy → read-only 실행 → gold semantic 결과 비교**를 사례별로 재현 가능하게 기록하는 runner를 만든다.

이번 단계는 runner와 지표의 동작 검증이다. `FakeModel`이 gold IR을 그대로 되돌리는 oracle smoke 결과는 모델 정확도나 학교 간 일반화 성능이 아니다. 모든 gold 사례가 `NEEDS_HUMAN_REVIEW`이므로 사람 승인 전 수치를 논문의 확정 성능으로 표기하지 않는다.

## 이번 범위

- versioned `EvaluationRunConfig`, `CaseAttempt`, `EvaluationRunReport` Pydantic 계약
- gold set·seed·ontology·Query IR·mapping/catalog fingerprint 및 variant 검증
- `QueryModel` 주입을 받는 결정적 runner core; 질문을 모델에 전달하고 임의의 gold IR을 내부에서 정답 예측으로 대체하지 않음
- 세 fixture 각각의 Published Mapping·FK graph와 현재 `ExecutionHarness`의 schema-adaptive compiler·AST policy·read-only executor 결합
- 각 `(case_id, variant)`의 구조적 IR 비교, 실행 상태, semantic row multiset 비교와 failure stage/code 기록
- 세 스키마 간 모델 출력 의도 일관성 및 결과 일관성의 명시적 판정
- 전체·variant별·coverage tag별 분모와 성공·실패·비교 불가 수 집계
- 결정적 JSON report core와 사람이 읽는 요약 출력
- gold 응답 `FakeModel`의 oracle smoke와 의도적으로 틀린 응답·실패 응답을 쓰는 합성 테스트
- 기존 평가 CLI에 분명한 별도 subcommand를 추가하거나 독립 CLI 진입점 제공
- 계획 문서의 Phase 5 evaluation runner 상태, 다음 체크포인트, 필요 시 ADR, 날짜별 변경 이력 갱신

## 실행·비교 계약

- 평가 단위는 `(benchmark version, case_id, fixture variant, model run id)`다. 사례는 고정 ID 순서, variant는 명시한 순서로 실행한다.
- `HarnessRequest.request_id`의 UUID 기본값은 사용하지 않고 사례·variant에서 결정적인 ID를 명시한다.
- 모델에는 사례의 **질문**과 기존 harness가 제공하는 ontology·Published Mapping runtime view만 전달한다. gold IR·기대 row·offering key를 모델 입력이나 prompt context에 넣지 않는다.
- gold IR을 반환하는 `FakeModel`은 `oracle-smoke`로 별도 표시한다. 그 결과는 runner 정합성 확인이며 benchmark score 집계나 논문 성능 표에 넣지 않는다.
- IR 비교는 원시 JSON 문자열이 아니라 현재 `QueryIR`의 typed 구조를 기준으로 한다. projection 순서, filter field/operator/typed value, 명시·기본 LIMIT 정책을 보존하고 비교 규칙을 테스트로 고정한다. `AND` filter 순서를 의미상 동일하게 볼지 여부는 별도 structural-exact 지표와 혼동하지 않게 명시한다.
- semantic 비교는 기존 `semantic_row_multiset()`을 재사용한다. 행 순서는 무시하되 중복 행 수, ontology type, 문자열 정확성을 보존한다.
- 모델 출력 projection field 집합이 gold와 다르면 semantic 결과를 같은 답으로 억지 비교하지 않는다. `NOT_COMPARABLE_PROJECTION`과 구조적 IR 불일치를 별도로 기록한다.
- 정상 실행했으나 값 또는 행 수가 다르면 `SEMANTIC_MISMATCH`다. 0건 gold도 명시적으로 비교한다.
- 모델 출력의 구조 오류, ontology·mapping 검증 실패, JOIN ambiguity, AST 차단, timeout과 SQL 실행 실패는 각각 기존 harness stage/error code로 집계한다. 오류를 0건 정답으로 계산하지 않는다.
- 예상한 `(case, variant)` 모두 하나의 결과를 가져야 한다. 일부 실패를 누락하거나 성공한 사례만 분모로 쓰지 않는다.
- 같은 모델 응답이 세 variant에서 서로 다른 Query IR 또는 semantic 결과를 만들면 `CROSS_SCHEMA_INCONSISTENCY`를 별도로 드러낸다. 단순 SQL 문자열 차이는 불일치가 아니다.

## 지표와 report 정책

최소한 다음을 **서로 다른 지표**로 정의한다.

- `structural_ir_exact`: gold Query IR과 동일한 typed 구조
- `semantic_correct`: 정상 실행 결과의 typed row multiset이 gold와 동일
- `cross_schema_consistent`: 동일 질문의 세 variant 실행 결과가 비교 가능하고 서로 동일
- `execution_success`: AST 정책과 read-only 실행을 포함한 harness 성공
- `not_comparable`: projection 불일치 등 정답 비교 조건 미충족
- `failure_by_stage_and_code`: 모델 출력부터 결과 검증까지의 실패 분포

각 지표의 분모는 전체 예정 attempt와 비교 가능 attempt를 구분해 기록하고, 실패·비교 불가를 조용히 제외한 높은 정확도처럼 표시하지 않는다. `semantic_correct`는 실행 성공률과 분리해 보고한다. case-level 세 스키마 일관성은 3개 attempt가 모두 존재할 때만 판정한다.

report에는 benchmark·seed·ontology·Query IR fingerprint, fixture catalog·Published Mapping fingerprint/version, runner/report schema version, model label과 `oracle_smoke` 여부, 예정·완료 attempt 수, 각 지표의 count·denominator, case별 상태와 reason을 넣는다. 실행 시각·임시 경로·UUID·측정 시간은 결정적 report core에서 제외한다. SQL·전체 row·모델 응답 원문은 기본 report에 저장하지 않는다. 합성 fixture용 제한된 debug artifact가 필요하면 명시적 opt-in과 길이 상한을 둔다.

현재 harness trace의 `elapsed_ms`는 wall-clock 값이라 byte-identical core에 넣지 않는다. 실제 지연·GPU·메모리 benchmark는 이번 단계에서 측정하거나 주장하지 않는다.

## 오류·안전 경계

- 모델 예외 또는 비정상 출력 한 건 때문에 전체 평가를 중단하지 않는다. runner 경계에서 구조화된 안전한 오류로 바꾸되 원문 응답·예외의 민감 문자열을 기본 report에 복제하지 않는다.
- gold set의 seed/ontology/Query IR drift나 fixture mapping/catalog drift는 **run 시작 전 fatal error**로 처리한다. 잘못된 기준으로 점수를 만들지 않는다.
- Mapping Proposal을 실행에 사용하지 않는다. synthetic fixture의 명시적 Published Mapping만 사용한다.
- `ExecutionHarness`·compiler·policy·executor의 안전 계층을 우회하거나 모델 생성 raw SQL을 직접 실행하지 않는다.
- 입력으로 실제 CNU dataset·private 가명 artifact를 요구하지 않는다. 그런 입력을 report에 혼합하지 않는다.
- 실패한 attempt의 SQL이나 row를 성공 결과처럼 기록하지 않고, 결과가 부분적으로 만들어졌더라도 집계 규칙을 명확히 한다.

## CLI 예시

구체적 module 경로와 옵션은 기존 구조를 조사해 정하되 사용 방식은 다음 수준으로 유지한다.

```powershell
python -m backend.adaptive.evaluation run-smoke `
  --gold backend/adaptive/evaluation/data/fixture_v1_cases.yaml `
  --variants flat,normalized,legacy `
  --output .schema-adaptive/reports/fixture-runner-smoke.json
```

CLI의 기본 provider는 명시적인 `oracle-smoke`여야 하며 report에 그 사실을 눈에 띄게 기록한다. gold 답을 사용한 smoke run에는 `model_accuracy`라는 이름을 붙이지 않는다. 기존 `--format json|review-table` inspection CLI 동작을 깨지 않는다. 출력은 기존 dataset CLI처럼 기본 덮어쓰기 금지, 명시적 `--force`에서만 원자 교체하고 입력 gold와 출력의 동일 경로를 차단한다.

## 필수 테스트

- 40개 사례 × 3 variant의 예정·완료 attempt 수와 안정적 순서
- oracle-smoke의 모든 정상 attempt 실행 및 gold semantic multiset 일치; 결과를 실제 모델 성능으로 표기하지 않음
- 같은 질문에서 variant별 물리 SQL은 달라도 semantic 결과가 같음
- 틀린 field·filter·typed value·LIMIT·projection 순서에 대한 structural IR 판정
- projection 불일치의 `NOT_COMPARABLE_PROJECTION`과 semantic mismatch의 분리
- gold 0건을 빈 정상 결과와 비교하고 실패를 빈 정답으로 오인하지 않음
- duplicate semantic row를 set으로 축약하지 않음
- malformed Query IR, 미매핑 field, JOIN 실패, AST 거부, 실행 오류, model 예외의 stage/code 및 다음 사례 계속 실행
- 한 variant만 다른 결과를 낼 때 case-level cross-schema inconsistency 검출
- 실패 포함 전체 분모와 variant·coverage별 count 합계 정합성
- seed·ontology·Query IR·mapping/catalog drift의 run 시작 전 차단
- SQL·row·모델 원문과 임시 경로가 기본 report에 없음
- 같은 입력·고정 응답의 byte-identical report core와 결정적 request ID
- CLI 기존 inspection 회귀, 출력 덮어쓰기·동일 경로 보호, 기존 전체 테스트 회귀 없음

## 제외 범위

- `RecordedModel`, Ollama, PostgreSQL, 기존 fine-tuned/base instruct 모델 실행
- 새 자연어 모델 prompt 설계나 few-shot context 실험
- 실제 모델 정확도·응답시간·메모리 benchmark와 논문 성능 주장
- gold의 사람 승인 자동 처리
- 새로운 평가 질문·seed·ontology·Query IR 또는 SQL compiler 확장
- 독립 unseen school 일반화 실험
- repair loop, 관리자 UI, schema evolution
- 실제 CNU 데이터셋·private 가명 artifact 사용

## 서브 에이전트

- 메인 에이전트가 먼저 지표의 분모, projection 비교 가능성, `oracle_smoke` 표기, 결정적 report core 계약을 정의한다.
- 독립 구현을 분배한다면 runner core, report/CLI, 적대적 테스트를 수정 파일이 겹치지 않게 나눈다.
- 메인 에이전트가 세 fixture 통합과 전체 테스트·문서 갱신을 담당한다.
- 구현 후 별도 리뷰 에이전트가 gold 누출, oracle 결과의 정확도 오인, 누락 attempt, failure denominator, 비교 불가 처리, AST·read-only 우회와 개인정보 노출을 점검한다.
- 리뷰 지적 수정 후 전체 테스트를 재실행한다.

## 완료 기준

- FakeModel 주입으로 40개 질문을 세 fixture에서 끝까지 반복 실행 가능
- 모델 출력과 gold를 혼동하지 않고 구조적 IR·semantic 정답·실행 성공·세 스키마 일관성을 분리 보고
- 실패와 비교 불가를 전체 분모에서 숨기지 않음
- oracle smoke와 `NEEDS_HUMAN_REVIEW` 경고가 report·CLI에 명시됨
- 결정적 report core, 안정적 failure taxonomy, 안전한 기본 출력 정책 검증
- 기존 inspection CLI와 legacy 시스템 보존
- 기존 전체 테스트 회귀 없음, `compileall`·`git diff --check` 통과, working tree clean

## 문서와 커밋

- Phase 5 `evaluation runner 구현`은 runner·테스트가 완료된 뒤에만 체크한다. `RecordedModel`과 실제 모델 평가 체크박스는 유지한다.
- 계획 문서에 runner 지표 분모, oracle smoke 경계, gold 사람 검토 상태, 실제 모델 평가를 위한 데스크톱 체크포인트를 기록한다.
- 지표·report 결정이 중요하면 ADR을 추가하고 날짜별 변경 이력을 갱신한다.
- 다음 시작점은 `RecordedModel`의 안전한 재생 계약 또는 사람 gold 검토 절차 중 의존성이 적은 단위로 지정한다.
- 과거 Conventional Commit 형식에 맞춰 runner 계약/core, report/CLI, 테스트, 문서를 분리 커밋한다. 각 커밋 전 staged diff를 확인하고 원격 push는 하지 않는다.

이번 단계까지만 개발하고 실제 모델 실행·성능 주장·repair loop·관리자 UI는 다음 작업으로 남겨줘.
