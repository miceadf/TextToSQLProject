# 다음 개발 프롬프트: Phase 1 JSONL loader와 데이터 품질 report

다음 단계인 기존 학습 데이터셋 JSONL loader와 결정적 데이터 품질 report를 개발해줘.

작업 전 반드시 다음을 확인해.

- `docs/schema-adaptive-text-to-sql-plan.md` 전체
- 기존 `dataset.csv` 분석 결과와 데이터 활용 방침
- 현재 `QueryIR` 계약과 테스트 구조
- 저장소의 Conventional Commit 기록
- 작업 시작 시 working tree 상태

## 목표

확장자는 `.csv`지만 실제로는 한 줄에 JSON 객체 하나가 저장된 기존 학습 데이터셋을 안전하게 읽고, 원본을 변경하지 않으면서 중복·충돌·파싱 오류를 재현 가능한 JSON report로 생성한다.

이 단계는 데이터셋을 바로 학습하거나 Query IR로 변환하는 단계가 아니다. 이후 SQL AST 분석과 변환 작업이 신뢰할 수 있는 입력 계층을 만드는 것이 목적이다.

## 입력 데이터 원칙

- 사용자가 제공한 실제 데이터셋 경로는 실행 시 인자로만 받는다.
- 실제 데이터셋이나 실제 질문·SQL 원문을 저장소에 복사하거나 커밋하지 않는다.
- 구현 전에 실제 레코드의 key와 값 타입을 read-only로 확인한다.
- 문서에 기록된 통계는 검증 기준이지 코드에 하드코딩할 값이 아니다.
- 실제 파일을 사용할 수 없는 테스트 환경에서는 작은 합성 JSONL fixture를 사용한다.
- UTF-8과 UTF-8 BOM을 명시적으로 처리하고, 지원하지 않는 인코딩이나 잘못된 JSON은 줄 번호가 포함된 issue로 보고한다.

현재 문서에 기록된 참고 통계:

- 전체 레코드 5,385개
- 고유 질문 5,354개
- 고유 SQL 5,124개
- 중복 질문 그룹 19개
- 같은 질문에 서로 다른 SQL이 연결된 충돌 그룹 3개

## 이번 범위

- versioned dataset record Pydantic 계약 정의
- source line number와 원본 field를 보존하는 streaming JSONL loader
- 빈 줄, malformed JSON, 객체가 아닌 JSON, 필수 field 누락, 잘못된 field type에 대한 구조화된 issue
- 전체·유효·무효 레코드 수 집계
- 고유 instruction·질문·SQL 수 집계
- 정확히 동일한 record 중복 탐지
- 동일 질문에 같은 SQL이 반복되는 duplicate group 탐지
- 동일 질문에 서로 다른 SQL이 연결된 conflict group 탐지
- 결과 순서가 항상 같은 deterministic JSON report
- report schema version, 입력 파일 fingerprint, 생성 도구 version 기록
- 사람이 검토할 수 있는 CLI 진입점
- 합성 fixture와 단위·CLI 테스트
- 계획 문서의 Phase 1 체크박스, 현재 체크포인트, 변경 이력 갱신

## 정규화 경계

- 원본 문자열은 변경하지 않고 보존한다.
- 중복 식별용 question key는 Unicode NFC, 줄바꿈 통일, 앞뒤 공백 제거까지만 허용한다.
- SQL은 이번 단계에서 의미 정규화하거나 AST로 재작성하지 않는다.
- SQL duplicate/conflict 판정은 원본 SQL에 줄바꿈 통일과 앞뒤 공백 제거만 적용한 key를 사용한다.
- 대소문자, 내부 공백, quote, literal 값이 다른 SQL을 임의로 같은 SQL로 취급하지 않는다.
- normalization 전후 값이 달라졌는지는 report에서 추적 가능해야 한다.

## 개인정보와 report 정책

- 기본 JSON report에는 전체 실제 질문·SQL 원문을 무제한으로 복제하지 않는다.
- group 식별에는 안정적인 hash와 source line 목록을 사용한다.
- 사람이 검토할 preview가 필요하면 명시적인 옵션과 제한된 group·variant 개수·문자 길이를 적용한다.
- 입력 파일 절대 경로는 report에 저장하지 않고 파일명 또는 사용자가 지정한 dataset id만 기록한다.
- fingerprint는 파일 내용 기반으로 계산해 같은 입력을 재현 가능하게 식별한다.

## 오류 정책

- 한 줄의 오류 때문에 기본 분석 전체를 중단하지 않는다.
- 각 issue는 최소한 code, line number, field, message를 가진다.
- 파일을 열 수 없음, 지원하지 않는 report version, 출력 실패는 명시적 fatal error로 처리한다.
- issue와 group 정렬 기준을 계약으로 고정해 같은 입력이 같은 report를 생성하게 한다.
- timestamp처럼 실행마다 달라지는 값은 결정성 비교 대상 report core와 분리하거나 주입 가능하게 한다.

## CLI 예시

구체적인 module 경로는 현재 패키지 구조를 조사해 정하되, 사용 방식은 다음 수준으로 단순하게 유지한다.

```powershell
python -m backend.adaptive.dataset inspect `
  --input "C:\path\to\dataset.csv" `
  --dataset-id cnu-original-v1 `
  --output ".schema-adaptive\reports\dataset-quality.json"
```

CLI는 원본 파일을 수정하지 않아야 하며, 기존 report를 덮어쓰는 정책은 명시적으로 정한다. 가능하면 임시 파일 작성 후 원자 교체를 사용한다.

## 필수 테스트

- 정상 JSONL 여러 행 loading
- `.csv` 확장자의 JSONL loading
- UTF-8 BOM 처리
- 빈 줄 처리
- malformed JSON과 정확한 line number
- JSON object가 아닌 행 거부
- 필수 field 누락과 잘못된 type
- 동일 record 중복
- 동일 질문·동일 SQL duplicate group
- 동일 질문·서로 다른 SQL conflict group
- NFC·줄바꿈·경계 공백 normalization
- SQL 내부 공백이나 대소문자를 과도하게 합치지 않음
- deterministic issue/group/report 순서
- 같은 입력의 동일 fingerprint와 report core
- preview 기본 비활성화 및 개수·길이 제한
- CLI JSON 출력과 입력 파일 불변
- input/output 동일 경로·symlink·hardlink에서 `--force`를 사용해도 원본 보존
- escaped lone surrogate를 행 issue로 처리하고 다음 행 분석 지속
- 기존 전체 테스트 회귀 없음

## 제외 범위

- SQL 의미 정규화
- sqlglot 기반 SQL AST 분석
- 기존 SQL을 Query IR로 변환
- 변환 실패 수동 검토 목록
- 실제 식별값 가명화
- train/validation/test split
- 공통 평가 질문 30~50개 작성
- 기대 semantic result 작성
- evaluation runner
- RecordedModel 또는 실제 모델 실행
- Ollama/PostgreSQL
- repair loop와 관리자 UI

## 설계 원칙

- 실제 레코드 key를 확인하기 전에 schema를 추측하지 않는다.
- loader, 분석기, report serializer, CLI 책임을 분리한다.
- Pydantic 계약으로 record, issue, duplicate/conflict group, summary, report를 명시한다.
- 통계 계산은 입력 순서와 무관하게 결정적이어야 한다.
- 메모리 사용을 불필요하게 키우지 않되 5천여 건 규모에서 과도한 구조를 만들지 않는다.
- 기존 legacy 시스템과 현재 schema-adaptive 실행 경로를 변경하지 않는다.

## 서브 에이전트

- 메인 에이전트가 먼저 실제 데이터 key, normalization 경계, 개인정보 report 정책을 정의한다.
- 독립 구현을 분배한다면 loader/contracts와 report/tests의 수정 파일을 겹치지 않게 한다.
- 메인 에이전트가 CLI 통합, 실제 통계 확인, 전체 테스트를 담당한다.
- 구현 후 별도 리뷰 에이전트가 malformed input, 결정성, 중복·충돌 정의, 개인정보 노출, 원본 불변성을 점검한다.
- 리뷰 지적 수정 후 전체 테스트를 다시 실행한다.

## 완료 기준

- 실제 `dataset.csv`를 read-only로 끝까지 분석 가능
- 문서의 기존 통계와 실제 report 차이를 확인하고 차이가 있으면 원인을 기록
- 오류 행이 구조화된 issue로 남고 나머지 유효 행 분석은 계속됨
- duplicate와 conflict가 서로 구분됨
- 같은 입력·설정은 같은 report core와 fingerprint 생성
- 실제 원문 데이터가 저장소에 추가되지 않음
- 기존 테스트 회귀 없음
- `compileall` 통과
- `git diff --check` 통과
- working tree clean

## 문서

- Phase 1의 JSONL loader와 데이터 품질 report 상태 갱신
- 실제 통계와 문서의 기존 통계 비교 결과 기록
- normalization·개인정보·report 결정이 중요하면 ADR 추가
- 다음 체크포인트를 Phase 1의 SQL 정규화 규칙과 sqlglot AST 분석으로 지정
- 날짜가 포함된 변경 이력 추가

## 커밋

- 과거 Conventional Commit 형식을 따른다.
- contracts/loader, report/CLI, 테스트, 문서를 기능별로 분리 커밋한다.
- 각 커밋 전 staged diff를 확인한다.
- 실제 데이터셋과 생성된 민감 report를 커밋하지 않는다.
- 원격 push는 하지 않는다.

이번 단계까지만 개발하고 SQL AST 분석, SQL→Query IR 변환, 가명화, 평가 질문 작성과 evaluation runner는 다음 작업으로 남겨줘.
