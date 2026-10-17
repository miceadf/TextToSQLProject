# 데스크톱 테스트 인수인계: Schema-adaptive Text-to-SQL

> 기준일: 2026-09-19 · 구현 브랜치: `feat/schema-adaptive-agent` · 이 문서는 **실행 결과 보고서가 아니라 테스트 출발점**이다.
>
> 설계·진행 상태의 원본: [`schema-adaptive-text-to-sql-plan.md`](schema-adaptive-text-to-sql-plan.md) · 사람 승인 관문: [`human-validation-checkpoints.md`](human-validation-checkpoints.md)

## 1. 프로젝트가 무엇인가

기존 졸업·경연 프로젝트는 충남대학교 수강정보에 특화된 한국어 Text-to-SQL 서비스다. React/FastAPI 화면에서 질문을 받아 로컬 Ollama 모델이 PostgreSQL용 SQL을 만들고, 수강정보를 조회한다. 기존 모델과 서비스는 **비교 기준선(legacy baseline)** 으로 보존한다.

확장 프로젝트의 목표는 대학 수강정보 도메인 안에서 **학교마다 다른 DB 스키마에 적응하는 시스템**이다. 새 DB를 Catalog로 분석하고, ontology와 물리 컬럼의 Mapping Proposal을 생성한다. 관리자가 의미를 교정·승인한 `Published Mapping`만 질의에 사용한다. 모델은 물리 SQL이 아니라 공통 `QueryIR`을 생성하고, 결정적 compiler가 학교별 JOIN과 parameterized SQL을 만든다. AST 정책과 읽기 전용 실행이 이를 검증한다. 수강 추천은 핵심 연구 주제가 아니다.

```text
새 DB → Catalog → Schema Aligner → 관리자 교정·publish
질문 → 모델의 QueryIR → Published Mapping + FK JoinPlan → SQL compiler
     → AST read-only policy → 읽기 전용 실행 → semantic 결과
```

기존 모델은 SQL을 생성한다. 새 경로의 `QueryModel`은 **QueryIR을 생성해야 한다**. Ollama에 접속된다는 사실만으로 기존 fine-tuned 모델을 새 QueryIR 경로의 성공 모델이라고 간주하지 않는다.

## 2. 현재 구현과 검증 경계

| 영역 | 구현 상태 | 아직 증명하지 못한 것 |
| --- | --- | --- |
| 데이터·IR | JSONL 품질 report, PostgreSQL SQL AST 분석, 승인 source profile 기반 SQL→QueryIR 변환, HMAC 가명화 도구 | 변환·가명화 결과의 외부 공개 승인, 모든 legacy SQL의 자동 변환 |
| 스키마 적응 | 공통 ontology, SQLite Catalog·aligner, 관리자 교정 CLI와 publish gate | 실제 다른 학교 컬럼·코드 의미에 대한 mapping 정확도 |
| 질의 실행 | FK JoinPlan, JOIN 가능한 SQLite compiler, AST allowlist, parameter binding·읽기 전용 SQLite executor | PostgreSQL용 adapter/compiler/policy의 실행 성공 |
| 평가 입력 | 한 synthetic seed의 flat·normalized·legacy SQLite 3개, 한국어 gold 40건 | 독립 unseen school, 40건의 독립 사람 승인 |
| 모델 평가 기반 | FakeModel runner, RecordedModel replay/CLI, capture session, private bundle storage, provider-neutral orchestrator | 실제 Ollama adapter·수집 CLI, live 모델 정확도·지연·VRAM |
| 제품 통합 | 기존 FastAPI/React legacy 경로 보존 | 새 adaptive 경로의 기존 API/화면 통합, repair loop·관리자 UI·schema evolution |

마지막 확인된 노트북 테스트(2026-09-18): `python -m pytest tests -q`에서 **254 passed, 3 skipped, 322 subtests passed**. 프론트 lint/build와 Python `compileall`도 직전 점검에서 통과했다. 이는 데스크톱의 새 환경 통과를 보증하지 않는다. 세 skip은 Windows에서 symlink 생성 권한이 없었던 보안 테스트다. 2026-10-17 Phase 9-27에서 assertion 없는 NER 보정 예제를 `backend/scripts/demo_ner_correction.py`로 옮겨 pytest 수집 대상에서 제외했다. 예제 실행은 `python -m backend.scripts.demo_ner_correction`이며, 전체 pytest 최종 확인은 별도로 수행한다.

`run-smoke`는 gold IR을 그대로 돌려주는 **oracle smoke**다. 실제 모델 정확도가 아니다. `run-recorded`는 외부에서 이미 만든 recording을 재생할 뿐 live 모델을 호출하지 않으며, recording의 provenance도 독립 증명이 아니다.

## 3. 데스크톱으로 가져갈 것과 가져가지 말 것

- fork: `https://github.com/miceadf/TextToSQLProject`, 브랜치 `feat/schema-adaptive-agent`. 마지막 인수인계 커밋 `2d666d6`. 데스크톱에서 `git status -sb`와 `git rev-parse HEAD`로 실제 체크아웃 상태를 다시 확인한다. 이 문서가 커밋되면 HEAD는 달라지므로 위 해시는 **기준선 식별용**이다.
- 원본: `https://github.com/rumyakun/TextToSQLProject`. 2026-09-19 확인 당시 원본 `master`의 `3ac6d8f`가 위 작업 브랜치의 조상이었다. 새 테스트 시점의 원본 변경 여부는 다시 확인해야 한다.
- Git에는 소스, 합성 fixture와 평가 질문만 둔다. 실제 CNU `dataset.csv`, DB dump, GGUF/model weight, 실제 질문·응답 recording, 가명화 key, 접속 비밀값은 Git으로 옮기지 않는다.
- 현재 저장소에는 실제 fine-tuned Ollama 모델 artifact가 포함되지 않는다. 데스크톱의 기존 로컬 모델 tag/Modelfile/GGUF 위치와 사용 권한을 별도로 확인한다. `models/Modelfile`만으로 모델 파일이 있다는 뜻은 아니다.
- 기록 파일·민감 report가 필요하면 `.schema-adaptive/` 등 gitignore된 private 경로를 사용한다. 그 경로도 보안·접근권한 검토 없이 공유하지 않는다.

## 4. 첫 세션: 환경 재현과 기준선 확인

작업 디렉터리를 저장소 루트로 둔다. 아래 명령은 새 코드 작성이나 DB 변경을 요구하지 않는다.

```powershell
git status -sb
git rev-parse HEAD
python --version
node --version
npm --version
docker --version
ollama --version
psql --version
```

클린 clone/가상환경에서 의존성을 설치할 때는 `backend/requirements.txt`, `package-lock.json`, `Dockerfile.backend`의 Python/Node 환경을 확인한다. `requirements.txt`는 일부 의존성만 버전이 고정되어 있어 노트북·데스크톱 결과가 다를 수 있다. 설치 실패나 버전 차이를 기록하고, 임의로 전체 의존성을 업데이트하지 않는다.

```powershell
python -m pytest tests -q
python -m compileall -q backend tests
npm ci
npm run lint
npm run build
```

위 테스트의 실제 pass/skip/fail을 기록한다. `npm ci`는 의존성 설치가 필요할 때만 실행한다. 루트 전체 수집도 별도 확인하되 현재 알려진 legacy import 실패는 adaptive 테스트 실패와 구분한다.

```powershell
python -m pytest --collect-only -q
```

참고: 현재 `docker-compose.yml`에는 PostgreSQL 서비스가 없고 backend의 `OLLAMA_BASE_URL`, `DATABASE_URL`이 비어 있다. README의 오래된 Docker 설명을 그대로 믿지 않는다. Docker `up`만으로 PostgreSQL·모델·스키마가 준비됐다고 판정하지 않는다. 기존 API의 `/api/v1/health` 통과도 실제 모델·DB 질의 성공의 증거가 아니다.

## 5. 첫 데스크톱 smoke test: 범위를 작게 유지

### A. Ollama 자체 확인 — DB와 분리

1. `ollama list`로 실제 설치된 모델 tag를 확인한다. 기존 fine-tuned 모델과 base 모델을 혼동하지 않는다.
2. Ollama 서버의 로컬 `/api/tags` 접근과 `ollama show <확인한-tag>`를 확인한다. 모델 tag, 가능하면 모델 digest, Ollama 버전, Modelfile/설정 버전을 기록한다. 비밀 경로·환경변수·프롬프트 원문은 공유 문서에 복제하지 않는다.
3. **합성 수강 질문 3~5개**만 별도 수동 호출한다. 질문과 schema context를 고정하고 응답이 raw SQL인지, JSON/QueryIR인지, Markdown fence·설명문이 섞이는지 확인한다. JSON parse 가능 여부와 필드 구조를 관찰하되 의미를 자동 보정하지 않는다.
4. 첫 호출과 재호출의 대략적인 시간·메모리/VRAM, model-not-found·timeout·빈 응답의 실제 형태를 기록한다. 이 관찰은 adapter 설계 입력이며 benchmark 점수가 아니다.

현재 새 adaptive 경로의 Ollama adapter와 live capture CLI는 **없다**. `backend/llm.py`는 legacy SQL 생성용이며 오류 본문·프롬프트 취급도 새 private recording 계약과 다르다. 이를 `QueryModel` 구현처럼 재사용하거나 실제 응답을 `run-recorded`에 바로 넣지 않는다. 다음 작은 개발 단계는 계획 문서의 **Ollama adapter 최소 호출·오류 변환 계약**이다.

### B. PostgreSQL 연결·메타데이터 확인 — adaptive 실행과 분리

1. 개발용 DB 또는 승인된 읽기 전용 계정·replica에서 PostgreSQL 버전, DB/role, 접속 방식(TLS·host/port), `SELECT 1` 성공을 확인한다. 실제 계정 비밀번호나 DSN을 로그·채팅·커밋에 남기지 않는다.
2. 메타데이터만 조회해 예상 테이블/view, PK/FK, 타입, nullable, 권한을 확인한다. `information_schema`와 `pg_catalog` 조회 가능 여부도 확인한다. 이때 실제 행이나 학생 정보는 출력하지 않는다.
3. `v_course_info` 등 legacy가 기대하는 view가 실제 DB에 있는지 확인한다. 없는 view를 smoke test 중 즉흥적으로 생성하지 않는다. README의 view DDL은 과거 환경의 예시이며 현재 DB에 그대로 적용해도 된다는 승인 정보가 아니다.
4. 읽기 전용 계정의 권한과 `statement_timeout` 적용 가능성을 DB 담당자와 확인한다. 단순 `SELECT 1`은 권한·행 수준 보안·실제 질의 안전을 증명하지 않는다. 운영 DB에서 DDL/DML 차단 시험을 위해 실제 쓰기 명령을 실행하지 않는다.

예를 들어 `psql` 접속 설정을 안전하게 준비한 뒤 아래 읽기 전용 명령만 사용할 수 있다. 연결 문자열·비밀번호는 명령 인자나 결과 공유 문서에 적지 않는다.

```powershell
psql -X -c "SELECT current_database(), current_user, current_setting('server_version')"
psql -X -c "SELECT 1"
psql -X -c "SELECT table_schema, table_name, table_type FROM information_schema.tables WHERE table_schema NOT IN ('pg_catalog', 'information_schema') ORDER BY table_schema, table_name LIMIT 30"
```

현재 새 경로에는 `PostgresAdapter`와 PostgreSQL JOIN compiler/executor가 없다. DB 연결 성공을 schema-adaptive end-to-end 성공으로 보고하지 않는다. SQLite SQL을 PostgreSQL에 그대로 실행해 성능을 판정하지 않는다.

### C. 합성 평가 경로 재확인 — 모델 성능과 분리

`python -m backend.adaptive.evaluation --format review-table`로 40개 synthetic gold의 검토 표를 확인한다. `run-smoke`는 합성 fixture의 실행 파이프라인 점검에만 사용한다. 보고서를 저장할 때는 민감 자료가 아닌 별도 출력 경로를 지정하고 기존 파일 덮어쓰기를 피한다.

```powershell
python -m backend.adaptive.evaluation run-smoke --output .schema-adaptive/reports/desktop-oracle-smoke.json
```

동일 seed의 3개 SQLite 스키마는 **타 대학 3곳이 아니다**. 40개 질문은 모두 `NEEDS_HUMAN_REVIEW`다. `run-recorded`에는 기존 `recorded-model-v1` JSON만 입력할 수 있으며, 새 private bundle의 `manifest.json`을 직접 받는 CLI는 아직 없다.

## 6. 이번 smoke test의 성공·중단 기준

- 성공: 새 환경에서 핵심 테스트가 재현되고, Ollama 모델과 PostgreSQL에 각각 **독립적으로 연결**되며, 모델 출력 형식·DB 메타데이터·권한 범위·환경 차이가 기록된다.
- 중단: 모델 tag/weight가 없거나, DB 연결·권한이 불명확하거나, 실제 CNU 자료 사용 범위가 미승인인 경우. 실패를 임의 데이터·다른 모델·관리자 계정으로 대체하지 않는다.
- 성공으로 **표기하지 않을 것**: 실제 모델 QueryIR 정확도, PostgreSQL adaptive 실행 성공, 학교 간 일반화, 40문항 benchmark, 개인정보 공개 안전성.
- 결함 발견 시: 재현 조건, 구성 버전, 실패 단계·안전한 오류 코드, 관련 커밋과 다음 작은 수정 단위를 기록한다. 실제 질문·SQL·응답·DSN은 이슈 본문에 붙이지 않는다.

## 7. 사람 확인과 후속 개발

가능하면 지금 E1(ontology/행 단위), E2(gold 5~10건), E4(독립 평가 스키마 확보 가능성), E5(깨끗한 clone 재현)을 확인한다. 실제 CNU 자료 활용·반출 전 **E3**, 실제 응답 기록·저장·외부 시연 전 **L5**의 데이터·보안 범위를 확인한다. 실제 학교 DB mapping을 publish·질의하기 전 **L2**, 모델 성능 수치를 확정하기 전 **L1·L3**, 학교 간 일반화 주장 전 **L6**가 필요하다. 승인되지 않은 상태를 코드나 문서에서 자동으로 완료 처리하지 않는다.

환경 smoke test 후의 권장 순서:

1. Ollama adapter의 JSON 반환·timeout/empty/provider error 변환을 합성 transport로 테스트한다.
2. 데스크톱에서 소수 합성 질문으로 adapter live smoke를 수행한다.
3. 별도 단계로 private capture CLI와 provenance 입력·검증을 연결한다.
4. `PostgresAdapter`·PostgreSQL dialect/read-only 정책을 별도 구현하고, 승인된 DB에서 소수 질의를 검증한다.
5. gold 사람 검토와 독립 평가 스키마를 확보한 뒤 공정한 모델 비교·논문 실험을 진행한다.

### 다음 대화에 전달할 짧은 문장

> `docs/desktop-test-handoff.md`와 `docs/schema-adaptive-text-to-sql-plan.md`, `docs/human-validation-checkpoints.md`를 먼저 읽어줘. `feat/schema-adaptive-agent`에서 Phase 5-06까지 완료했지만 Ollama/PostgreSQL adaptive adapter는 아직 없다. 먼저 데스크톱의 환경·모델 tag·DB 읽기 권한·합성 질문 출력 형식만 smoke test하고, 실제 데이터 기록이나 성능 점수 확정은 하지 말아줘. 테스트 결과와 한계, 다음 작은 수정 단위를 문서에 남겨줘.

### 세션 기록 양식

```text
날짜 / OS·Python·Node·PostgreSQL·Ollama 버전 / Git 커밋:
모델 tag·digest(가능한 범위) / prompt·설정 버전:
DB 종류·버전·role의 읽기 권한·metadata 접근(비밀값 제외):
백엔드 tests 통과·실패·skip / 프론트 lint·build / 루트 pytest 수집:
Ollama 합성 질문 수 / 응답 형식·JSON parse 여부 / 오류 유형:
PostgreSQL 연결·SELECT 1·metadata·view 확인 결과:
사람 검토 E1~E5 및 L1~L6 상태(승인자를 추측하지 않음):
미해결 위험 / 다음 작업 / 재검토 필요 여부:
```

실험 기록에는 질문·응답·SQL·개인정보·접속 비밀값을 기본적으로 넣지 않는다. 실제 데이터가 필요한 경우 접근·보관·삭제 정책을 먼저 확정한다.
