# 다음 개발 프롬프트: Phase 1 SQL 정규화와 AST 분석

다음 단계인 기존 학습 데이터셋의 SQL 정규화 규칙과 sqlglot 기반 AST 분석기를 개발해줘.

작업 전 반드시 다음을 확인해.

- `docs/schema-adaptive-text-to-sql-plan.md` 전체
- `backend/adaptive/dataset`의 record, loader, quality report, CLI와 저장 안전장치
- 현재 `QueryIR`, ontology operator, compiler 계약
- `backend/requirements.txt`의 sqlglot 고정 버전과 기존 AST policy
- 기존 `dataset.csv`의 SQL 통계와 데이터 활용 방침
- 저장소의 Conventional Commit 기록
- 작업 시작 시 working tree 상태

## 목표

기존 `DatasetRecord.sql` 원문을 변경하지 않고 PostgreSQL dialect로 AST 분석해, 보수적인 canonical fingerprint와 SQL feature 분포, 현재 Query IR로의 변환 가능성 및 수동 검토 사유를 결정적이고 재현 가능한 JSON report로 만든다.

이번 단계는 SQL을 실행하거나 Query IR로 실제 변환하는 단계가 아니다. 다음 변환기의 입력 계약과 지원 범위를 실측으로 결정하는 것이 목적이다.

## 입력과 dialect 원칙

- 입력은 구현된 `load_jsonl_dataset()`의 유효 `DatasetRecord`를 사용한다.
- 실제 데이터셋 경로는 CLI 인자로만 받고 저장소에 복사하지 않는다.
- SQL 원문과 source line을 보존하되 기본 report에는 SQL·질문·literal 원문을 넣지 않는다.
- source dialect는 명시적인 설정으로 받고 기본값은 기존 시스템 DB에 맞춘 `postgres`로 한다.
- parser와 serializer 모두 같은 명시적 dialect를 사용한다.
- sqlglot 버전은 현재 고정된 버전을 사용하고 report metadata에 기록한다.
- parse error나 지원하지 않는 SQL 한 건 때문에 전체 분석을 중단하지 않는다.

현재 문서의 검증 대상 통계:

- 유효 SQL 5,385개
- 고유 SQL 5,124개
- 참조 table은 모두 `courses`
- JOIN 0개
- subquery 0개
- aggregate function 675개
- `GROUP BY` 4개
- `ORDER BY` 122개
- `LIMIT` 130개
- `capacity - enrolled` 형태 산술식 256개

이 값은 검증 기준이지 코드에 하드코딩할 값이 아니다. 새 분석기의 AST 정의와 기존 수치가 다르면 어느 정의가 다른지 설명하고 문서에 기록한다.

## 이번 범위

- versioned SQL analysis Pydantic 계약
- sqlglot PostgreSQL dialect parse
- 빈 SQL, parse error, 다중 statement, SELECT가 아닌 statement의 구조화된 issue
- 원문을 변경하지 않는 보수적 canonical SQL 생성 규칙
- canonical SQL 재parse와 AST round-trip 검증
- canonical SQL hash와 literal-redacted shape hash 생성
- statement·table·column·projection·predicate·function feature 추출
- aggregate, DISTINCT, GROUP BY, HAVING, ORDER BY, LIMIT/OFFSET 집계
- JOIN, subquery, CTE, set operation, window function 집계
- arithmetic, comparison, boolean operator와 wildcard 집계
- 현재 Query IR 후보 가능성 분류와 reason code 생성
- feature와 분류 결과의 전체·그룹별 결정적 통계
- 개인정보를 복제하지 않는 versioned JSON report
- 기존 dataset CLI에 독립 subcommand 또는 명확한 새 CLI 진입점 추가
- 합성 fixture 기반 parser·normalizer·classifier·CLI 테스트
- 실제 `dataset.csv` read-only 분석과 기존 통계 비교
- 계획 문서의 Phase 1 체크박스, 현재 체크포인트, ADR와 변경 이력 갱신

## SQL 정규화 계약

정규화 결과는 분석과 fingerprint 용도이며 실행 SQL이나 새로운 정답 SQL로 사용하지 않는다.

- raw SQL은 `DatasetRecord`에서 그대로 보존한다.
- canonicalization은 sqlglot이 parse한 AST를 동일 dialect로 결정적으로 직렬화한다.
- identifier 이름, identifier qualification, alias, literal 값과 literal type을 보존한다.
- predicate, projection, JOIN, GROUP BY, ORDER BY 순서를 임의로 바꾸지 않는다.
- commutative expression이나 boolean 조건을 재정렬하지 않는다.
- quote 제거·추가로 identifier 의미가 달라지지 않도록 한다.
- comments와 formatting 차이는 canonical fingerprint에서 제외할 수 있지만 적용 규칙을 계약과 테스트로 고정한다.
- canonical SQL을 다시 parse한 AST가 원래 분석 AST와 동등한지 확인한다.
- round-trip이 불일치하면 성공으로 숨기지 말고 구조화된 issue로 분류한다.
- canonical hash는 identifier와 literal을 포함한다.
- shape hash는 literal 값만 type-aware placeholder로 치환한 복사 AST에서 계산한다.
- shape hash를 duplicate 정답 판정이나 실행 동등성의 근거로 사용하지 않는다.
- 원본 AST를 shape 생성 과정에서 in-place로 변경하지 않는다.

## AST feature 계약

문자열 정규식이 아니라 AST node와 clause를 근거로 최소한 다음을 기록한다.

- statement kind와 root node
- table과 column 참조 수
- projection 수, wildcard, alias, expression projection
- WHERE 존재 여부와 predicate 수
- `AND`, `OR`, `NOT`
- `=`, `!=`, `<`, `<=`, `>`, `>=`, `LIKE`, `IN`, `BETWEEN`, `IS NULL`
- aggregate function 종류와 개수
- 일반 function 종류와 개수
- arithmetic operator 종류와 개수
- DISTINCT, GROUP BY, HAVING, ORDER BY, LIMIT, OFFSET
- JOIN 종류와 개수
- subquery, CTE, UNION/INTERSECT/EXCEPT, window function
- placeholder와 literal type별 개수

동일 node를 부모와 자식 순회에서 중복 집계하지 않도록 feature 정의와 count 단위를 문서화한다. table·column·function 이름을 report에 포함한다면 정렬하고 literal이나 질문 원문과 섞지 않는다.

## Query IR 가능성 분류

각 SQL을 다음 상태 중 하나로 결정적으로 분류한다.

- `CURRENT_IR_CANDIDATE`: 현재 Query IR의 select, AND filter, 지원 operator, limit만으로 표현 가능해 보이는 단일 SELECT
- `IR_EXTENSION_REQUIRED`: 안전한 SELECT지만 aggregate, DISTINCT, GROUP BY, ORDER BY, 산술식, 추가 operator 등 현재 IR에 없는 표현이 필요함
- `MANUAL_REVIEW`: AST만으로 의도 보존 여부를 확정하기 어렵거나 모호한 alias·LIKE·projection·literal 처리가 있음
- `UNSUPPORTED_OR_UNSAFE`: parse 실패, 다중 statement, SELECT 외 statement 또는 현재 연구 범위 밖의 구조

분류는 최소한 다음 조건을 지킨다.

- 단일 table이라는 이유만으로 자동 변환 가능하다고 판단하지 않는다.
- 단순 column projection과 지원되는 literal filter만 `CURRENT_IR_CANDIDATE` 후보가 된다.
- 현재 Query IR의 지원 operator는 ontology와 실제 계약에서 읽고 별도로 중복 하드코딩하지 않는다.
- `LIKE '%값%'`과 prefix/suffix/exact LIKE를 구분한다.
- OR, NOT, IN, BETWEEN, IS NULL은 현재 계약으로 표현할 수 있는지 명시적으로 판정한다.
- aggregate, GROUP BY, HAVING, DISTINCT, ORDER BY와 산술 projection은 조용히 손실하지 않는다.
- `SELECT *`, function projection, alias 의존 결과명은 명시적인 reason code를 가진다.
- LIMIT 값이 현재 Query IR 범위를 벗어나면 후보로 승인하지 않는다.
- 한 SQL에 여러 사유가 있으면 모든 reason code를 결정적 순서로 보존한다.
- 이 분류는 자동 변환 성공 판정이 아니라 다음 변환 작업의 후보 선별 결과임을 report에 명시한다.

## 개인정보와 report 정책

- 기본 report에는 실제 SQL, 질문, string/numeric literal 원문을 저장하지 않는다.
- record 식별은 source line, canonical hash, shape hash를 사용한다.
- parse error 메시지에 SQL 원문이나 literal이 포함되지 않도록 정제한다.
- feature 예시가 필요하면 명시적 preview 옵션과 group·record·문자 길이 상한을 적용한다.
- preview에서도 literal redaction을 기본으로 하고 원문 preview는 별도 명시 없이는 허용하지 않는다.
- 입력 절대 경로와 실행 시각은 deterministic report core에 포함하지 않는다.
- dataset content fingerprint, report schema version, analyzer version, sqlglot version, dialect는 기록한다.
- 실제 분석 report와 실제 데이터 원문은 저장소에 커밋하지 않는다.

## 오류와 안전 정책

- SQL을 어떤 데이터베이스에서도 실행하지 않는다.
- sqlglot parse·transform·serialize 예외를 record-level issue로 변환한다.
- 한 record의 실패 이후 다음 record 분석을 계속한다.
- 빈 SQL과 whitespace-only SQL을 구분 가능한 issue로 남긴다.
- 한 record에 다중 statement가 있으면 첫 statement만 선택하지 않는다.
- DML, DDL, transaction, PRAGMA, ATTACH와 database control statement를 분석 성공으로 취급하지 않는다.
- input/output 동일 경로·symlink·hardlink 보호와 atomic non-overwrite 정책은 기존 dataset CLI 안전장치를 재사용한다.
- 같은 설정과 입력은 동일한 report core 및 정렬 순서를 생성해야 한다.

## CLI 예시

현재 dataset CLI 구조를 우선 재사용한다.

```powershell
python -m backend.adaptive.dataset analyze-sql `
  --input "C:\path\to\dataset.csv" `
  --dataset-id cnu-original-v1 `
  --dialect postgres `
  --output ".schema-adaptive\reports\sql-analysis.json"
```

기존 `inspect` subcommand 동작과 JSON 계약을 깨지 않는다. 출력 파일은 기본적으로 덮어쓰지 않고 `--force`에서만 원자 교체한다.

## 필수 테스트

- 단순 단일 table SELECT parse와 canonical round-trip
- whitespace·keyword formatting만 다른 SQL의 같은 canonical hash
- identifier, alias, quote, literal이 다른 SQL을 과도하게 병합하지 않음
- literal 값만 다른 SQL의 다른 canonical hash와 같은 shape hash
- string, integer, decimal, boolean, NULL literal type 구분
- 원본 AST와 raw SQL 불변
- predicate·projection 순서 미변경
- comment 처리 규칙
- 빈 SQL과 malformed SQL
- 다중 statement
- INSERT/UPDATE/DELETE와 DDL/database control statement
- aggregate와 일반 function 구분
- DISTINCT, GROUP BY, HAVING, ORDER BY, LIMIT/OFFSET
- JOIN 종류, subquery, CTE, set operation, window function
- arithmetic과 boolean/comparison operator 집계
- wildcard와 expression projection
- 현재 IR 후보, IR 확장 필요, 수동 검토, 미지원 분류
- LIKE contains와 prefix/suffix pattern 구분
- 복수 reason code의 결정적 순서
- parse error message의 원문·literal 비노출
- report의 deterministic order와 metadata
- preview 기본 비활성화와 redaction·개수·길이 제한
- CLI output 안전성과 input 불변
- 동일 경로·symlink·hardlink 출력 차단
- 실제 데이터 5,385건 전체 분석
- 기존 문서의 JOIN/subquery/aggregate/GROUP BY/ORDER BY/LIMIT/산술 통계 비교
- 기존 전체 테스트 회귀 없음

## 제외 범위

- SQL 실행 또는 실행 결과 비교
- SQL을 실제 Query IR로 변환
- 자동 변환된 Query IR 저장
- 변환 실패 수동 검토 목록 확정
- Query IR 모델 변경
- ontology나 compiler 변경
- SQL 의미 동등성 증명
- aggregate·ORDER BY·OR 등을 지원하기 위한 IR 확장
- 질문 template 분류와 train/test split
- 실제 식별값 가명화
- 공통 평가 질문·기대 결과 작성
- evaluation runner
- 모델 실행, Ollama, PostgreSQL 연결
- repair loop와 관리자 UI

## 설계 원칙

- parser, canonicalizer, feature extractor, IR compatibility classifier, report serializer, CLI 책임을 분리한다.
- 모든 결과와 오류를 Pydantic 계약으로 명시한다.
- 분류 reason code는 문자열 설명만 두지 말고 enum으로 정의한다.
- sqlglot 내부 class 이름을 그대로 장기 저장 계약으로 노출하지 않는다.
- feature 집계 정의를 unit test로 고정한다.
- 원문 SQL의 의미를 바꿀 수 있는 rewrite를 하지 않는다.
- 현재 Query IR이 표현하지 못하는 기능을 자동으로 버리지 않는다.
- 기존 legacy 시스템, compiler, AST execution policy를 변경하지 않는다.

## 서브 에이전트

- 메인 에이전트가 먼저 canonicalization 계약, feature count 단위, IR 분류 상태와 reason code를 정의한다.
- 한 에이전트는 parser·canonicalizer core를 담당한다.
- 다른 에이전트는 feature extractor·classifier를 담당한다.
- 별도 에이전트는 합성·적대적 테스트를 담당한다.
- 수정 파일은 에이전트별로 겹치지 않게 한다.
- 메인 에이전트가 report·CLI 통합, 실제 5,385건 분석과 기존 통계 비교를 담당한다.
- 구현 후 별도 리뷰 에이전트가 canonical 의미 보존, literal redaction, node 중복 집계, 분류 누락, 결정성, 원본 불변성을 점검한다.
- 리뷰 지적 수정 후 실제 데이터 분석과 전체 테스트를 다시 실행한다.

## 완료 기준

- 실제 dataset 5,385개 SQL을 끝까지 분석하고 개별 오류가 전체 실행을 중단하지 않음
- source dialect와 sqlglot version이 report에 기록됨
- raw SQL과 원본 AST를 변경하지 않음
- canonical hash와 literal-redacted shape hash의 역할이 분리됨
- canonical round-trip 불일치가 명시적으로 보고됨
- SQL feature count 정의가 계약과 테스트로 고정됨
- 네 가지 Query IR 후보 상태와 모든 reason code가 결정적임
- 기존 통계와 새 AST 통계를 비교하고 차이를 설명함
- 기본 report와 parse error에 실제 SQL·질문·literal 원문이 노출되지 않음
- 실제 데이터와 실제 분석 report가 저장소에 추가되지 않음
- 기존 `inspect` CLI 회귀 없음
- 기존 전체 테스트 회귀 없음
- `compileall` 통과
- `git diff --check` 통과
- working tree clean

## 문서

- Phase 1의 SQL 정규화 규칙과 sqlglot AST 분석 체크박스 갱신
- 실제 AST feature 통계와 기존 문서 수치 비교 결과 기록
- canonical hash, shape hash, IR 분류 경계가 중요한 결정이면 ADR 추가
- 다음 체크포인트를 기존 SQL→Query IR 변환기와 수동 검토 목록으로 지정
- 날짜가 포함된 변경 이력 추가

## 커밋

- 과거 Conventional Commit 형식을 따른다.
- parser/canonicalizer, feature/classifier, report/CLI, 테스트, 문서를 기능별로 분리 커밋한다.
- 각 커밋 전 staged diff를 확인한다.
- 실제 dataset, SQL 원문, literal preview, 생성된 실제 report를 커밋하지 않는다.
- 원격 push는 하지 않는다.

이번 단계까지만 개발하고 SQL→Query IR 변환, Query IR 확장, 가명화, 평가 질문 작성과 evaluation runner는 다음 작업으로 남겨줘.
