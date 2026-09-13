# 다음 개발 프롬프트: Phase 2 공통 평가 질문과 기대 semantic 결과

다음 단계인 **공통 평가 질문 30~50개와 기대 semantic result 집합**을 개발해줘.

작업 전 반드시 다음을 확인해.

- `docs/schema-adaptive-text-to-sql-plan.md` 전체와 현재 Phase 2 체크포인트
- `backend/adaptive/fixtures`의 `FixtureSeed v1`, `LogicalOffering`, flat·normalized·legacy builder와 canonical logical row 계약
- `backend/adaptive/ontology/course-domain.yaml`의 field, data type, 허용 operator
- 현재 `QueryIR`·`FilterIR`, validator, JOIN planner, compiler, AST policy, read-only executor 계약
- 기존 cross-schema 실행 테스트와 Published Mapping 생성 방식
- `docs/prompts/phase1-04-identifier-pseudonymization.md` 및 CNU 원본·가명 데이터 사용 경계
- 저장소의 Conventional Commit 기록과 작업 시작 시 working tree 상태

## 목표

같은 한국어 질문을 세 가지 물리 스키마에 동일하게 제시할 수 있도록, synthetic `FixtureSeed v1`에만 근거한 versioned 평가 세트를 만든다. 각 사례에 사람이 검토할 수 있는 gold Query IR과 물리 DB 이름에 의존하지 않는 기대 semantic 결과를 붙여, 후속 evaluation runner와 모델 비교의 신뢰할 수 있는 기준 입력으로 사용한다.

이 단계의 성공은 모델 정확도 측정이 아니다. 질문·의도·정답 데이터와 그 검증 계약을 고정하는 것이다.

## 데이터 및 평가 경계

- 기본 대상은 현재 `DEFAULT_SEED`의 개설강좌 6건과 15개 논리 field다. 세 DB는 데이터가 같고 구조만 다르다.
- 평가 질문은 한국어로 새로 작성하며, 실제 CNU `dataset.csv`의 질문·SQL·literal이나 가명화 private artifact를 복사하지 않는다.
- seed에 없는 강좌·학과·교수·학기·시간을 사실처럼 답에 넣지 않는다. 존재하지 않는 값을 사용하는 **빈 결과** 사례는 명시적으로 표시한다.
- 원본 CNU 값에 대한 leak 검사는 이 synthetic 세트를 외부 공개할 수 있다는 보증이 아니다.
- 이 세 스키마는 개발 fixture다. 이를 독립 unseen school이나 실제 다른 대학 일반화 실험으로 부르지 않는다.
- Phase 1 가명화 review 684건을 승인된 평가 질문으로 자동 편입하지 않는다.
- 6건 seed로 만들기 어려운 연산·분포는 질문을 무의미하게 반복해 숫자를 채우지 말고 coverage gap으로 기록한다. 이번 단계에서 `FixtureSeed v1`이나 legacy 시스템을 조용히 바꾸지 않는다.

## 이번 범위

- versioned `EvaluationCase`·`ExpectedSemanticResult` Pydantic 계약
- 고정 ID를 가진 한국어 공통 평가 질문 30~50개
- 각 사례의 gold Query IR, 기대 semantic row multiset, 기대 개설강좌 key와 provenance
- fixture seed·ontology·Query IR version/fingerprint 연결
- 질문 유형·field·operator·JOIN 난도·결과 cardinality coverage metadata
- 중복 질문·중복 의도·동의어 paraphrase 식별 및 과다 반복 방지
- 독립적인 gold set 정적 validator와 결정적 JSON/YAML loader
- 세 스키마에서 **gold Query IR**의 실행 결과가 기대 semantic 결과와 일치하는지 검증하는 테스트
- 오류가 있는 gold 사례, stale seed, 미지원 연산을 명시적으로 거부하는 적대적 테스트
- 평가 세트의 machine-readable coverage report와 사람용 검토 표
- 계획 문서의 Phase 2 체크박스, 현재 체크포인트, 필요한 ADR와 날짜별 변경 이력 갱신

## 평가 사례 계약

각 사례는 최소한 다음 정보를 가진다.

- `schema_version`, 안정적인 `case_id` (`P2-001` 등), `question_ko`
- `seed_version` 또는 seed content fingerprint, ontology version
- `gold_query_ir`: 현재 Pydantic `QueryIR` 그대로 사용; 별도 유사 모델을 만들지 않음
- `expected_offering_keys`: 사람이 지정한 논리 개설강좌 key 목록
- `expected_semantic_rows`: `gold_query_ir.select`의 semantic field를 key로 한 typed row 목록
- `expected_cardinality`, answerability/zero-result 표시
- 검토 상태·검토 근거와 작성자/검토자 식별(실제 개인정보가 아닌 repository label)
- 난도·coverage tag: filter 수, operator, 참조 entity, 필요한 physical table 수/JOIN step 수, 결과 행 수, paraphrase family 등

질문은 세 물리 스키마에 공통으로 사용한다. 질문 문자열이나 gold Query IR 안에 `course_offerings`, `sbjt_nm`, `prof_nm` 같은 물리 table·column 이름을 넣지 않는다. `gold_query_ir.entity`는 질문의 논리 root entity를 명시하고 첫 projection field에서 추측하지 않는다.

평가 사례의 정답은 생성된 SQL 문자열이 아니라 **semantic 결과**다. `expected_offering_keys`는 검토용 행 provenance이고, 실제 결과 비교는 선택한 semantic field의 typed row multiset으로 한다. 동일 projection 값이 두 개설강좌에서 반복되면 중복 행 수를 보존한다. `ORDER BY`가 없는 현 Query IR에 순서를 요구하지 않는다. 문자열은 정확히 비교하고, 숫자는 ontology data type에 따른 명시적 비교 규칙을 사용하며 `bool`을 숫자로 혼동하지 않는다. 기대 결과를 테스트 대상 compiler나 SQL 실행 결과에서 자동 복사해 gold로 삼지 않는다.

## 질문 구성 원칙

30~50개 모두 현재 Query IR과 ontology가 표현할 수 있고 현재 seed에서 답을 판정할 수 있어야 한다. 단순 어미 변경만으로 사례 수를 채우지 않는다.

최소한 다음 축을 균형 있게 포함한다.

- 단일 field·다중 field projection, 여러 entity field를 함께 선택하는 사례
- filter 없음, 단일 filter, `AND` 다중 filter
- string `equals`·`contains`, 지원되는 정수/수치 비교 operator의 유효 사례
- 과목명·과목코드·학과명·교수명·연도·학기·분반·정원·수강인원·요일·시간 등 현재 fixture에 존재하는 field의 실제 coverage
- 정규화 스키마에서 직접 JOIN과 2단계 이상 JOIN이 필요한 field 조합
- 0건·1건·여러 건의 결과, 같은 과목의 서로 다른 분반, 같은 projection 값의 중복 가능성
- 값의 경계: 정원과 수강인원 일치, 숫자 threshold의 경계, `contains`의 부분 문자열
- 같은 의미의 한국어 paraphrase는 일부 포함하되 family ID로 묶어 독립 난도로 과장하지 않음

목표 개수보다 **서로 다른 논리 의도와 검증 가능한 정답**을 우선한다. field 또는 operator가 seed 분포상 구별력을 갖지 못한다면 coverage report에 그 한계를 적고, 허위 다양성을 만들지 않는다. 30개를 의미 있게 만들 수 없다면 부족한 축과 필요한 seed 보강안을 제시하고 완료로 표시하지 않는다. 현재 IR에 없는 잔여석 산술, OR, 집계, GROUP BY, ORDER BY, DISTINCT, 시간표 충돌 계산은 실행 가능한 gold 세트에 섞지 않는다. 필요하면 별도 `out_of_scope_examples` 문서에만 기록하고 30~50개 완료 수에 포함하지 않는다.

## gold 작성·검토 절차

1. `DEFAULT_SEED.canonical_rows()`와 ontology에서 사용 가능한 값·field·operator를 추출해 사람이 읽는 작업용 표를 만든다.
2. 사례별 한국어 질문과 gold Query IR을 작성한다. 질문이 여러 해석을 허용하면 보수적으로 수정하거나 제외한다.
3. gold Query IR의 Pydantic 및 ontology validator를 통과시킨다. 1~200 `LIMIT`을 명시하고 기대 행이 잘리는 사례는 현재 세트에서 피한다.
4. 사람이 seed를 보고 `expected_offering_keys`와 `expected_semantic_rows`를 기록한다. 예상 SQL이나 실행 결과에서 정답을 역생성하지 않는다.
5. 별도의 consistency 검사로 key 존재·중복, row shape/type, cardinality, seed 값 일치를 확인한다.
6. 세 스키마의 Published Mapping·Catalog FK·JoinPlan을 이용해 같은 gold Query IR을 compile·AST validate·read-only execute하고 각 결과 multiset을 gold와 비교한다.
7. 작성자와 다른 검토자가 질문 의도와 정답을 확인한다. 별도 사람이 없는 개발 환경에서는 `review_status=NEEDS_HUMAN_REVIEW`를 유지하고 자동 테스트 통과를 사람 승인으로 표기하지 않는다.

## 결정성·버전·저장 정책

- 파일 내 사례 정렬은 `case_id` 기준, row와 key 정렬 규칙은 계약으로 고정한다.
- 같은 seed·ontology·평가 세트 내용은 동일 fingerprint와 coverage report core를 만든다. 생성 시각·절대 경로는 core에 넣지 않는다.
- 질문 수정, gold IR 수정, 기대 결과 수정은 case ID 재사용 정책과 세트 version 변경 기준을 문서화한다. 이미 사용한 benchmark의 의미를 조용히 바꾸지 않는다.
- 합성 질문과 gold는 저장소에 커밋할 수 있다. 실제 dataset, private converted/pseudonymized artifact, key, 실제 질문·SQL·값은 포함하지 않는다.
- 사람용 검토 표에 개인정보나 실제 CNU 원문이 섞이지 않았는지 확인한다.

## 필수 테스트

- 30~50개 고유 case ID와 안정적 정렬
- 한국어 질문, 중복/공백/빈 질문, 동일 의도 paraphrase family 검증
- 모든 gold Query IR의 Pydantic·ontology·Published Mapping 유효성
- projection 순서, filter operator·typed literal, LIMIT 계약
- gold expected row의 key/field/type/cardinality·중복 행 multiset 정합성
- empty·single·multi-result와 분반 중복 처리
- flat·normalized·legacy 각각에서 gold Query IR의 기대 semantic 결과 일치
- 단일 테이블에서는 JOIN 없음, normalized에서는 필요한 FK 경로만 사용
- 예상 결과를 SQL 문자열 비교나 단순 행 정렬로 잘못 판정하지 않음
- fixture seed version/fingerprint 또는 ontology version drift 명시적 실패
- 없는 offering key, 없는 semantic field, 허용되지 않은 operator, 틀린 예상 행을 테스트에서 탐지
- 같은 입력의 byte-identical 평가 세트·coverage report core
- synthetic 자료만 저장소에 포함되고 실제 CNU/private 값이 새 파일에 들어가지 않음
- 기존 전체 테스트 회귀 없음, `compileall`, `git diff --check` 통과

## 제외 범위

- 모델 응답을 수집하거나 점수화하는 Phase 5 evaluation runner
- `RecordedModel`, Ollama, PostgreSQL, 기존 fine-tuned 모델 실행
- 모델별 정확도·응답시간·메모리 수치 측정
- 독립 unseen school 확보 또는 일반화 성능 주장
- 학습/검증/test split과 기존 데이터셋으로부터의 질문 자동 생성
- Query IR 모델·ontology operator·SQL compiler 확장
- 집계·정렬·산술·OR 등을 현재 IR에 억지로 삽입
- 관리자 UI, repair loop, schema evolution 실험

## 서브 에이전트

- 메인 에이전트가 먼저 평가 사례 계약, seed 고정 정책, 결과 multiset 비교 규칙과 review status를 정의한다.
- 독립 작업을 분배한다면 사례 작성·gold 정답 검토·검증기/테스트를 수정 파일이 겹치지 않게 나눈다.
- 메인 에이전트가 세 스키마 통합과 전체 테스트, coverage gap 및 문서 갱신을 담당한다.
- 구현 후 별도 리뷰 에이전트가 질문-IR 의미 일치, 정답의 독립성, 누락된 JOIN/중복 행, seed 과적합과 개인정보 경계를 검사한다.
- 리뷰 지적 수정 후 전체 테스트를 다시 실행한다.

## 완료 기준

- 30~50개의 사람이 읽고 의미를 확인할 수 있는 synthetic 한국어 평가 사례가 versioned 파일에 존재
- 각 사례에 gold Query IR과 기대 semantic 결과·logical offering provenance가 있음
- 모든 사례가 현재 Query IR 지원 범위에 있고 결과가 세 fixture에서 동일함
- coverage report가 field/operator/JOIN/cardinality와 부족한 축을 정직하게 보여줌
- 자동 정합성 검사가 사람의 검토 상태를 대신하지 않음
- CNU 원본 또는 private 가명 artifact가 benchmark에 섞이지 않음
- 기존 테스트 회귀 없음, `compileall`·`git diff --check` 통과, working tree clean

## 문서와 커밋

- Phase 2의 `공통 평가 질문 30~50개 선정`과 `기대 결과 집합 작성`은 실제 완료 후에만 체크한다.
- seed 규모, coverage, 사람 검토 상태, 결과 비교 규칙과 한계를 계획 문서에 기록한다.
- gold의 독립성·version 정책이 중요한 결정이면 ADR을 추가하고 날짜별 변경 이력을 갱신한다.
- 다음 체크포인트는 Phase 5 `RecordedModel` 또는 evaluation runner 중 이 평가 세트에 직접 의존하는 작은 단위로 정한다. 실제 모델 baseline 측정은 데스크톱 작업으로 남긴다.
- 과거 Conventional Commit 형식에 맞춰 계약/loader, 평가 사례, 검증·테스트, 문서를 분리 커밋한다. 각 커밋 전 staged diff를 확인하고 원격 push는 하지 않는다.

이번 단계까지만 개발하고 모델 실행·평가 runner·Query IR 확장·관리자 UI는 다음 작업으로 남겨줘.
