# 스키마 적응형 대학 수강정보 Text-to-SQL 에이전트 개발 문서

> 문서 상태: 기획 기준선(Baseline)
>
> 최초 작성일: 2026-09-04
>
> 현재 구현 브랜치: `feat/schema-adaptive-agent`
>
> 문서 목적: 팀 공유, 개발 의사결정, 졸업논문 기획, 실험 설계, 변경 이력 관리

## 1. 문서 사용 방법

이 문서는 향후 개발의 기준 문서이자 저장소 기반 프로젝트 메모리다. 설계나 범위가 변경되면 다음 규칙으로 갱신한다.

1. 변경된 설계를 관련 본문에 먼저 반영한다.
2. 중요한 선택은 `의사결정 기록`에 이유와 함께 추가한다.
3. 구현 완료 항목은 로드맵 체크박스에 표시한다.
4. 평가 수치와 실험 조건은 실행 환경, 모델, 데이터 버전과 함께 기록한다.
5. 문서 마지막의 `변경 이력`에 날짜와 요약을 추가한다.

이 문서에 적힌 내용과 구현이 다를 경우 구현을 임의로 정답으로 간주하지 않는다. 차이를 확인한 뒤 문서 또는 구현을 함께 수정한다.

## 2. 프로젝트 한 줄 정의

기존 시스템은 충남대학교 수강정보 스키마에 특화된 한국어 Text-to-SQL 서비스다. 확장 시스템은 대학 수강정보라는 도메인은 유지하면서, 학교마다 다른 관계형 데이터베이스 구조를 자동 분석하고 최소한의 관리자 교정을 거쳐 새로운 스키마에 적응하는 로컬 Text-to-SQL 에이전트를 목표로 한다.

제안 영문 명칭:

> SchemaPilot: A Local Schema-Adaptive Text-to-SQL Agent for Heterogeneous University Course Databases

## 3. 프로젝트 배경

### 3.1 기존 경연대회 주제

> 수강신청 도메인 특화 Text-to-SQL 기반 자연어 질의 시스템 개발

사용자가 한국어로 수강 과목을 질문하면 로컬 언어모델이 SQL을 생성하고 PostgreSQL에서 결과를 조회한다. React 화면에서는 과목 목록, 채팅형 검색, 시간표 시각화 등을 제공한다.

### 3.2 기존 시스템의 의미

기존 결과물은 단순 챗봇보다 다음 기술 요소를 포함한다.

- 한국어 수강정보 질의를 SQL로 변환
- 수강 도메인 전용 NER와 오타 보정
- Ollama 기반 로컬 Qwen2.5-Coder-7B 모델 사용
- SQL AST 검증과 `SELECT` 전용 안전장치
- Redis/KURE 임베딩 기반 유사 질의 캐시
- SQL 실행 실패 시 LLM 재생성
- PostgreSQL 결과 조회
- 과목 검색, 선수과목 경고, 수강정원 경고, 시간표 충돌 표시
- 학생 이수내역을 이용한 수강 완료 과목 제외
- 단계별 응답시간과 cache hit 로깅

### 3.3 기존 기술 구성

- 프론트엔드: React 19, TypeScript, Vite, Tailwind CSS
- 백엔드: FastAPI, Python
- 데이터베이스: PostgreSQL
- 모델 실행: Ollama
- 생성 모델: Qwen2.5-Coder-7B 기반 로컬 모델
- 전처리: Transformers 기반 커스텀 NER, RapidFuzz
- SQL 분석: sqlglot
- 캐시: Redis Stack, KURE-v1 embedding
- 개발·실행: npm scripts, Docker Compose

### 3.4 기존 핵심 처리 흐름

```text
사용자 한국어 질문
→ NER 및 오타 보정
→ 질문 마스킹
→ Redis 벡터 캐시 검색
→ cache miss이면 Ollama SQL 생성
→ LIMIT 추가 및 SQL 검증
→ PostgreSQL 실행
→ 실패하면 SQL 수정 모델 호출
→ 결과, SQL, 전처리 정보, 시간 측정값 반환
```

주요 구현 위치:

- API: `backend/main.py`
- 처리 파이프라인: `backend/process.py`
- 모델 호출: `backend/llm.py`
- 프롬프트: `backend/prompt.py`
- SQL 검증: `backend/validate.py`
- DB 실행: `backend/db.py`
- NER·오타 보정: `backend/keyword_extract.py`
- 벡터 캐시: `backend/vector_cache.py`
- 프론트 채팅: `src/components/ChatPopup.tsx`
- 과목·시간표 화면: `src/pages/MainPage.tsx`

## 4. 기존 시스템의 한계

### 4.1 물리 스키마 하드코딩

현재 프롬프트, 검증기, 마스킹, 결과 변환 로직이 `courses` 또는 `v_course_info` 및 고정 컬럼명에 결합되어 있다.

예:

- `subject_name`은 과목명
- `dept_name`은 학과명
- `category`는 이수구분
- `credit_hours`는 학점

다른 학교가 `sbjt_nm`, `dept_cd`, `isu_gb`, `pnt` 등을 사용하거나 테이블을 정규화하면 현재 시스템을 그대로 적용하기 어렵다.

### 4.2 단일 학교·단일 구조 학습

기존 모델 학습 데이터는 충남대학교 실제 수강 과목을 참고해 제작되었고 모든 예제가 하나의 `courses` 스키마를 사용한다. 따라서 모델이 동적 스키마를 읽는 능력보다 특정 테이블과 컬럼명을 암기했을 가능성이 있다.

### 4.3 실제 의미와 코드값 문제

스키마 이름만 읽어서는 다음 의미를 확정할 수 없다.

```text
isu_gb = '01'
status = 'C'
div1 = '3'
```

업무 용어, 코드값, 암묵적 JOIN 규칙은 관리자 또는 도메인 전문가 확인이 필요하다.

### 4.4 일반화 평가 부재

기존 시스템은 동일 스키마 내부 질문 정확도를 중심으로 개발되었다. 처음 보는 테이블 구조, 컬럼명, 정규화 형태, 학교별 용어체계에서의 성능은 아직 평가하지 않았다.

## 5. 기존 학습 데이터셋 분석

분석 대상은 사용자가 제공한 `dataset.csv`이며 실제 파일 형식은 행마다 JSON 객체가 저장된 JSONL이다.

### 5.1 기본 통계

- 전체 레코드: 5,385개
- 고유 질문: 5,354개
- 고유 SQL: 5,124개
- 고유 instruction: 1개
- 중복 질문 그룹: 19개
- 같은 질문에 서로 다른 SQL이 연결된 충돌 그룹: 3개
- 모든 SQL이 참조하는 테이블: `courses`
- JOIN 포함 SQL: 0개
- 서브쿼리 포함 SQL: 0개
- 집계 함수 포함 SQL: 675개
- `GROUP BY`: 4개
- `ORDER BY`: 122개
- `LIMIT`: 130개
- 산술식 `capacity - enrolled`: 256개

### 5.2 데이터셋의 가치

- 자연스러운 한국어 수강 질의 표현
- 실제 과목명, 학과명, 교수명, 이수구분 표현
- 단일·복수 조건 검색
- 정원, 수강인원, 잔여석 등 수강 도메인 연산
- 기존 모델과 확장 모델을 비교할 baseline

### 5.3 데이터셋의 한계

- 충남대학교 값과 용어에 대한 과적합 가능성
- 단일 `courses` 테이블 구조만 포함
- JOIN 및 복잡한 정규화 스키마 학습 불가
- 현재 운영 스키마인 `v_course_info`와 불일치
- 문자열 비교, `DISTINCT`, 비율 단위 등 일부 정답 기준 불일치
- 무작위 train/test 분할 시 과목명과 질문 템플릿 누수 가능

### 5.4 데이터셋 활용 방침

기존 데이터셋은 폐기하지 않는다. 다음 네 계층으로 분리해 사용한다.

1. **CNU Original**: 기존 모델 재현과 충남대 내부 baseline
2. **CNU Anonymized**: 실제 교수·학과·과목 식별값을 가명화한 데이터
3. **Multi-schema Synthetic**: 동일 질문과 논리적 데이터를 서로 다른 물리 스키마로 변환한 데이터
4. **Unseen Test School**: 학습과 개발에 사용하지 않은 독립 스키마 기반 최종 평가 데이터

기존 SQL은 가능한 범위에서 AST로 분석해 물리 컬럼명이 없는 공통 Query IR로 변환한다.

## 6. 변경할 시스템의 목표

### 6.1 핵심 목표

- PostgreSQL 또는 SQLite DB의 테이블 구조 자동 수집
- 대학 수강정보 공통 ontology와 실제 스키마 자동 정렬
- 매핑 신뢰도와 근거 제공
- 불확실한 매핑에 대한 관리자 교정
- 승인된 매핑만 운영에 사용
- 자연어를 물리 SQL이 아닌 공통 Query IR로 변환
- Query IR과 학교별 매핑으로 실제 SQL 생성
- 안전한 시험 실행과 오류 기반 자동 수정
- 스키마 변경 감지 및 영향받은 매핑 재검토
- 서로 다른 학교형 스키마에서 결과 일관성 평가
- 로컬 추론과 개인정보 보호 유지

### 6.2 초기 비목표

- 모든 산업과 모든 관계형 DB 지원
- DDL/DML 생성 및 실행
- 관리자 확인이 전혀 없는 완전 자동 의미 추론
- 대규모 데이터 웨어하우스 전체 지원
- 여러 SQL dialect 동시 완성
- 추천 기능을 핵심 연구 기여로 삼는 것

초기 범위는 대학 수강정보 도메인, SQLite 개발 fixture, PostgreSQL 실제 통합, 읽기 전용 `SELECT` 질의로 제한한다.

## 7. 핵심 개념

### 7.1 Domain Ontology

학교별 물리 테이블과 독립된 공통 수강정보 개념이다.

```text
Course
CourseOffering
Instructor
Department
Schedule
Classroom
Enrollment
Prerequisite
Student
GraduationRequirement
```

예:

```text
Course.name
Course.credits
Course.category
Instructor.name
Department.name
Schedule.day
Schedule.start_time
```

### 7.2 Database Catalog

Schema Inspector가 생성하는 표준 메타데이터다.

```json
{
  "tables": [
    {
      "name": "sbjt_mst",
      "columns": [
        {
          "name": "pnt",
          "type": "INTEGER",
          "nullable": true,
          "sampleValues": [1, 2, 3]
        }
      ],
      "primaryKeys": [],
      "foreignKeys": []
    }
  ]
}
```

### 7.3 Mapping Profile

공통 ontology와 학교별 물리 스키마를 연결한다.

```yaml
Course.name: sbjt_mst.sbjt_nm
Course.credits: sbjt_mst.pnt
Course.category: sbjt_mst.isu_gb
Department.name: dept_mst.dept_nm
```

### 7.4 Query IR

자연어 의도를 DB 독립적인 구조로 표현한다.

```json
{
  "entity": "Course",
  "select": ["Course.name", "Course.credits"],
  "filters": [
    {
      "field": "Course.credits",
      "operator": "equals",
      "value": 3
    }
  ],
  "limit": 50
}
```

### 7.5 Verified Query

관리자가 결과를 확인한 자연어 질문, Query IR, SQL, 예상 결과의 묶음이다. 모델 few-shot context, 회귀 테스트, 스키마 변경 검증에 사용한다.

## 8. 제안 아키텍처

```text
                         ┌──────────────────────┐
새 DB 연결 ─────────────→│ Database Adapter     │
                         └──────────┬───────────┘
                                    ↓
                         ┌──────────────────────┐
                         │ Schema Inspector     │
                         └──────────┬───────────┘
                                    ↓
                         ┌──────────────────────┐
                         │ Database Catalog     │
                         └──────────┬───────────┘
                                    ↓
                         ┌──────────────────────┐
                         │ Schema Aligner       │
                         └──────────┬───────────┘
                                    ↓
                         ┌──────────────────────┐
                         │ Admin Review Gate    │
                         └──────────┬───────────┘
                                    ↓
                         ┌──────────────────────┐
                         │ Published Mapping    │
                         └──────────┬───────────┘
                                    │
사용자 질문 ─→ Query Understanding Model ─→ Query IR
                                    │          │
                                    └────┬─────┘
                                         ↓
                                  SQL Compiler
                                         ↓
                              AST/Policy Validator
                                         ↓
                            Read-only Test Execution
                                         ↓
                         Result Critic / Repair Agent
                                         ↓
                               결과·SQL·근거 반환
```

## 9. 관리자 개입 과정

관리자 개입은 부가 기능이 아니라 시스템 신뢰성과 논문 평가의 핵심이다.

### 9.1 최초 온보딩

Schema Aligner가 매핑 후보와 신뢰도, 근거를 생성한다.

```text
sbjt_mst.sbjt_nm → Course.name       98%
sbjt_mst.pnt     → Course.credits    91%
sbjt_mst.isu_gb  → Course.category   67%
sbjt_mst.div1    → Department.name   34%
```

관리자는 다음 행동을 수행할 수 있다.

- 제안 승인
- 다른 ontology field로 변경
- 질의 대상에서 제외
- 민감정보로 지정
- 컬럼 설명과 동의어 추가
- 코드값 의미 지정
- 누락된 테이블 관계 추가

### 9.2 매핑 상태

```text
PROPOSED
NEEDS_REVIEW
APPROVED
REJECTED
IGNORED
STALE
```

운영 질의는 검증 후 `PUBLISHED` mapping version만 사용한다.

### 9.3 신뢰도 정책 초기안

- 0.90 이상: 자동 승인 후보. 민감정보 및 핵심 JOIN은 관리자 확인 가능
- 0.60 이상 0.90 미만: 관리자 검토 필수
- 0.60 미만: 미매핑 또는 직접 지정

threshold는 고정 진리가 아니라 관리자 작업량과 정확도 사이의 관계를 분석하기 위한 실험 변수다.

### 9.4 운영 중 교정

잘못된 결과가 발생하면 원인을 다음처럼 분류한다.

- 컬럼 매핑 오류
- 값 코드 오류
- JOIN 관계 오류
- 업무 용어 오류
- 질문 모호성
- Query IR 생성 오류
- SQL compiler 또는 dialect 오류

교정 내용은 원인에 따라 Mapping Profile, Business Glossary, Verified Query Repository 중 적절한 곳에 저장한다. 잘못된 SQL을 무조건 예제로 저장하지 않는다.

### 9.5 스키마 변경 대응

메타데이터 fingerprint가 변경되면 영향을 받은 매핑만 `STALE`로 변경한다. 새 후보를 생성하고 관련 verified query를 재실행한 뒤 관리자가 승인한다.

## 10. 모델 전략

### 10.1 기존 모델의 위치

기존 fine-tuned 모델은 폐기하지 않는다.

- 경연대회 시스템 성능 재현
- 고정 `courses` 스키마 baseline
- 동적 스키마 입력에 대한 zero-shot 적응 실험
- 수강 도메인 한국어를 Query IR로 변환하는 모델의 초기 후보
- adaptive engine 실패 시 기존 충남대 환경 fallback 후보

### 10.2 새 학교마다 재학습하지 않는 원칙

목표 구조는 학교별 모델이 아니라 하나의 공통 모델과 학교별 Mapping Profile이다.

```text
공통 모델
+ School A Mapping Profile
+ School B Mapping Profile
+ School C Mapping Profile
```

새 DB 적응은 모델 가중치 변경보다 Catalog, Mapping Profile, Business Glossary, Verified Query context 변경으로 처리한다.

### 10.3 필요한 경우의 재학습

재학습한다면 단일 학교 스키마가 아니라 여러 물리 스키마를 입력으로 포함하는 schema-conditioned training을 수행한다.

권장 학습 형태:

```text
schema + relations + safe sample values + natural-language question
→ Query IR
```

또는 비교군:

```text
schema + relations + safe sample values + natural-language question
→ SQL
```

최종 test schema는 학습과 데이터 생성 과정에서 제외한다.

### 10.4 모델 추상화

```python
class TextToSQLModel:
    def generate_ir(self, question, ontology, catalog, mappings):
        ...
```

예정 구현체:

- `FakeModel`: 단위 테스트용 고정 응답
- `RecordedModel`: 데스크톱에서 기록한 응답 재생
- `OllamaModel`: 로컬 모델 통합
- `OpenAICompatibleModel`: 필요 시 비교 실험용

## 11. 데이터 전략

### 11.1 지금 필요한 데이터

실제 다른 학교의 전체 수강 데이터는 초기 개발을 막는 필수조건이 아니다.

- 기존 CNU 기반 JSONL
- 가명화된 수강정보 값
- 동일 논리 데이터를 담은 SQLite 스키마 3개 이상
- 공통 ontology
- 대표 질문 및 기대 결과

### 11.2 모델 학습 전에 필요한 데이터

다중 스키마 학습 데이터가 필요하지만 반드시 실제 다른 학교 데이터일 필요는 없다. 기존 Query IR을 여러 물리 스키마 SQL로 compile해 생성할 수 있다.

### 11.3 최종 논문 평가 전에 필요한 데이터

최소 하나의 독립 unseen schema가 필요하다.

우선순위:

1. 사용 허가가 명확한 다른 대학 공개 데이터
2. 실제 구조를 참고해 가명화한 데이터
3. 개발자가 보지 않은 상태에서 다른 사람이 독립 설계한 대학 DB
4. 기존 스키마를 체계적으로 변형한 synthetic schema

실제 전체 행이 아니라 DDL, 관계, 코드 의미, 안전한 대표 행, 평가 질문과 예상 결과가 중요하다.

### 11.4 데이터 분할 원칙

- Entity-disjoint: test에는 학습에서 보지 않은 과목·교수·학과 사용
- Template-disjoint: test에는 학습에서 보지 않은 질문 표현 사용
- Schema-disjoint: test에는 학습에서 보지 않은 구조 사용
- Institution-disjoint: 가능하면 학습에서 보지 않은 학교 용어체계 사용

단순 무작위 행 분할만으로 일반화 성능을 주장하지 않는다.

## 12. 개발 환경 제약과 대응

### 12.1 현재 제약

- 현재 노트북 성능상 Ollama 모델 구동이 어려움
- 현재 노트북에 PostgreSQL 환경이 구성되어 있지 않음
- Ollama와 PostgreSQL 통합 테스트는 별도 데스크톱에서 수행한 경험이 있음

### 12.2 역할 분리

노트북에서 수행:

- JSONL 정제와 통계
- SQLite fixture
- ontology 및 Catalog 모델
- Schema Inspector
- 규칙 기반 Schema Aligner
- 관리자 CLI
- Query IR 및 SQL Compiler
- SQL validator
- 평가 runner
- 프론트엔드 및 관리 UI
- Mock/Recorded Model 테스트

데스크톱에서 수행:

- PostgreSQL integration test
- Ollama 모델 추론
- 모델별 정확도·지연·메모리 측정
- LLM 응답 fixture 기록
- 실제 CNU DB 검증

## 13. 개발 로드맵

### 로드맵 실행 원칙

Phase 번호는 기본적으로 선행 산출물의 의존 순서를 뜻한다. 지금까지는 노트북에서 검증 가능한 schema-adaptive vertical slice를 먼저 확보하기 위해 Phase 3~4의 Catalog, Mapping, JOIN, compiler, policy를 앞당겨 구현했다. 이후에는 뒤 Phase의 기능을 새로 시작하기 전에 앞 Phase의 미완료 항목을 작은 구현 단위로 닫는다.

Ollama, PostgreSQL, 기존 fine-tuned 모델처럼 데스크톱 환경이 필요한 항목은 완료로 표시하지 않는다. 노트북에서는 재현 가능한 입력·측정 계약과 fixture까지만 준비하고, 실제 수치 측정은 데스크톱 체크포인트로 명시적으로 남긴다. Phase 5 evaluation runner는 Phase 1 데이터 품질 기반과 Phase 2 공통 평가 질문·기대 결과가 준비된 뒤 시작한다.

### Phase 0. 기준선 보존 및 설계

- [x] 새 개발 브랜치 `feat/schema-adaptive-agent` 생성
- [x] 기존 프로젝트와 데이터셋 구조 분석
- [x] 연구 범위와 시스템 방향 문서화
- [ ] 기존 시스템 baseline 질문·정확도·응답시간 기록

### Phase 1. 데이터셋 정제와 Query IR

- [x] JSONL loader 구현
- [x] 중복·충돌 탐지 report 구현
- [ ] SQL 정규화 규칙 정의
- [ ] sqlglot 기반 기존 SQL AST 분석
- [x] 공통 Query IR Pydantic model 정의
- [ ] 기존 SQL을 Query IR로 변환
- [ ] 수동 검토가 필요한 변환 목록 생성
- [ ] 실제 식별값 가명화 도구 구현

### Phase 2. 다중 스키마 경량 실험 환경

- [x] 동일 seed 데이터를 가진 SQLite DB 3개 작성
- [x] flat schema 작성
- [x] normalized schema 작성
- [x] legacy/abbreviated schema 작성
- [ ] 공통 평가 질문 30~50개 선정
- [ ] 기대 결과 집합 작성
- [x] cross-schema result comparison 구현(fixture canonical row 기준)

### Phase 3. Catalog와 Schema Alignment

- [x] `DatabaseAdapter` 인터페이스 정의
- [x] `SQLiteAdapter` 구현
- [x] `DatabaseCatalog` 모델 정의
- [x] Schema Inspector 구현
- [x] course-domain ontology YAML 작성
- [x] 규칙 기반 mapping baseline 구현
- [x] 매핑 confidence와 evidence 생성
- [x] 관리자 교정 CLI 구현
- [x] mapping version 저장 및 publish 구현

### Phase 4. Query Compiler와 안전성

- [x] Query IR validator 구현
- [x] ontology relation graph 구현
- [x] 학교별 JOIN path 계산
- [x] Query IR → SQLite SQL compiler 구현(single-table 및 Catalog FK JOIN)
- [x] AST 기반 read-only policy 구현
- [x] LIMIT, timeout, 최대 결과 행 정책 구현
- [x] Published Mapping만 사용하는 규칙 구현

### Phase 5. 모델 비교 기반

- [x] `TextToSQLModel` 역할의 `QueryModel` 인터페이스 정의
- [x] `FakeModel` 구현
- [ ] `RecordedModel` 구현
- [x] 모델 응답 JSON schema 검증
- [ ] evaluation runner 구현
- [ ] 기존 fine-tuned 모델 평가
- [ ] base instruct 모델 평가
- [ ] 동적 스키마 prompt 평가
- [ ] Query IR 생성 방식 평가
- [ ] 필요 시 multi-schema LoRA 학습 및 평가

### Phase 6. Agent와 사용자 상호작용

- [x] 실행 오류 taxonomy 정의
- [ ] validate → execute → critique → repair loop 구현
- [ ] 모호한 질문 감지 및 사용자 확인
- [ ] 운영 실패를 관리자 검토 큐에 저장
- [ ] 교정 내용을 mapping/glossary/verified query에 반영

### Phase 7. PostgreSQL·Ollama 통합

- [ ] `PostgresAdapter` 구현
- [ ] `information_schema` 및 `pg_catalog` 분석
- [ ] 데스크톱 Ollama provider 통합
- [ ] 실제 CNU DB regression test
- [ ] SQL dialect 차이 처리
- [ ] 응답시간·GPU/메모리 사용량 기록

### Phase 8. 관리자 UI와 스키마 변화

- [ ] DB 등록·스캔 화면
- [ ] mapping proposal 검토 화면
- [ ] 코드값·관계 교정 화면
- [ ] test query 및 결과 미리보기
- [ ] mapping publish·rollback 화면
- [ ] schema fingerprint 비교
- [ ] 영향받은 mapping을 `STALE` 처리
- [ ] verified query 자동 회귀 테스트

### Phase 9. 최종 평가와 논문

- [ ] 독립 unseen schema 확보
- [ ] baseline과 제안 시스템 비교
- [ ] confidence threshold ablation
- [ ] 관리자 개입량 측정
- [ ] cross-schema consistency 측정
- [ ] schema evolution 실험
- [ ] 개인정보 마스킹 수준별 정확도 실험
- [ ] 결과 표·그래프 생성
- [ ] 한계와 외적 타당성 정리

### 최소 실행 하네스 구현 상태

`backend/adaptive/harness`에 다음 경량 end-to-end 경로를 구현했다.

```text
HarnessRequest
→ Published Mapping 확인
→ FakeModel의 Query IR 생성
→ Pydantic 구조 검증 및 ontology/mapping 의미 검증
→ Published Mapping과 JoinPlan 기반 parameterized SQLite SQL 컴파일
→ SQL 정책 검사
→ read-only SQLite 실행
→ 결과 컬럼 검증 및 단계별 trace 반환
```

현재 하네스는 Ollama와 PostgreSQL 없이 실행 가능하다. SQL 안전성은 compiler-owned SQL, parameter binding, sqlglot AST allowlist, `mode=ro`, `PRAGMA query_only`, SQLite authorizer, timeout, 행 제한을 겹쳐 적용한다. 기존 single-table compiler와 정규식 `ConservativeSqlPolicy`는 baseline으로 유지하며, schema-adaptive compiler와 AST policy는 명시적으로 주입한다. 자동 repair와 실제 모델 provider는 후속 단계다.

### SQLite Schema Inspector 구현 상태

`backend/adaptive/catalog`에 DB 종류와 독립적인 `DatabaseAdapter` 계약과 SQLite 구현을 추가했다. SQLite 파일을 read-only로 열어 사용자 테이블, 컬럼 선언 타입과 affinity, nullable/default/generated 상태, 복합 PK 순서, FK 관계를 `DatabaseCatalog`로 변환한다.

샘플값 수집은 개인정보 노출을 줄이기 위해 기본적으로 비활성화한다. 명시적으로 활성화할 때만 컬럼별 개수와 문자열 길이를 제한한다. 스키마 fingerprint는 샘플값을 제외한 구조만으로 계산하여 데이터 변경과 스키마 변경을 구별한다.

### Course Ontology와 규칙 기반 Schema Aligner 구현 상태

`backend/adaptive/ontology/course-domain.yaml`에 1차 ontology를 정의했다. 현재 entity는 `Course`, `Department`, `Instructor`, `CourseOffering`, `Schedule`이며, 각 semantic field에 한국어 별칭, 물리 컬럼명 힌트, 자료형, 허용 연산자를 기록한다.

`backend/adaptive/mapping`의 baseline aligner는 컬럼명 0.70, 자료형 호환성 0.20, 테이블명 0.10의 결정적 점수로 후보를 정렬한다. 각 후보는 구성 점수와 매칭 근거를 보존한다. 기본 상태 구분은 다음과 같다.

- 0.90 이상: `AUTO_APPROVE_CANDIDATE`
- 0.60 이상 0.90 미만: `NEEDS_REVIEW`
- 0.60 미만: `UNCERTAIN`; 해당 semantic field는 unmapped 목록에도 포함

`AUTO_APPROVE_CANDIDATE`는 자동 publish를 뜻하지 않는다. 관리자 검토·수정과 명시적인 publish gate는 다음 개발 단계다. threshold와 가중치는 향후 여러 스키마 평가에서 조정할 실험 변수다.

### 현재 개발 체크포인트와 다음 시작점

2026-09-07 기준 관리자 교정과 publish gate까지 완료했다.

구현된 흐름:

```text
Mapping Proposal JSON
→ review draft 생성
→ list/show/approve/remap/reject/ignore
→ publish validation
→ immutable mapping-vNNNN.json + published.json
→ 실행 하네스 MappingProfile 변환
```

CLI 진입점:

```bash
python -m backend.adaptive.mapping --store .schema-adaptive <command>
```

publish는 모든 ontology field의 검토 완료, 활성 entity의 required field, 물리 컬럼 존재, 중복 매핑 부재, database/schema fingerprint/ontology version 일치를 요구한다. `init` 재실행은 기존 draft를 덮어쓰지 않는다.

동일 seed 기반 SQLite 스키마 3개 fixture를 완료했다.

```text
FixtureSeed v1 (6개 개설강좌, 15개 논리 필드)
├─ flat: course_offerings 단일 테이블
├─ normalized: departments/instructors/courses/course_offerings/schedules
└─ legacy: lecture_info + 약어 컬럼
```

`python -m backend.adaptive.fixtures --output <directory>` 명령은 세 SQLite DB, Catalog JSON, Mapping Proposal JSON을 생성한다. 각 실행은 새 generation 디렉터리를 사용하며, 모든 artifact 생성 후 `bundle.json`을 원자적으로 갱신한다. 중간 실패 시 기존 manifest가 부분 생성 artifact를 가리키지 않는다.

세 DB는 같은 canonical logical row 집합으로 역변환되는지 비교한다. normalized fixture는 PK·FK·1:1 schedule 관계까지 검사한다. Schema Aligner는 세 구조 모두에서 15개 semantic field의 예상 `(table, column)`을 confidence 0.90 이상 top candidate로 선택해야 한다.

ontology relation graph와 학교별 JOIN path 계산을 완료했다.

```text
Published Mapping의 semantic fields
→ 실제 physical table 집합 해석
→ Catalog FK 기반 무방향 탐색
→ 최소 relation-edge tree 계산
→ RESOLVED JoinPlan 또는 AMBIGUOUS alternatives
```

`backend/adaptive/relations`는 ontology entity relation, physical FK relation, JOIN step과 planning result를 Pydantic 계약으로 제공한다. composite FK의 여러 컬럼은 sequence 순서가 보존된 relation edge 하나로 묶는다. 탐색은 FK를 양방향으로 사용할 수 있지만, edge에는 항상 원래 child table → referenced parent table 방향과 컬럼 predicate를 남긴다.

비용은 서로 다른 physical relation edge 수다. 필요한 테이블을 연결하는 최소 edge set이 둘 이상이면 하나를 임의 선택하지 않고 `AMBIGUOUS`와 정렬된 대안들을 반환한다. 단절 여부는 search limit 적용 전에 확인한다. cycle은 이미 연결된 테이블을 다시 확장하지 않으며, 비정상 composite FK, stale profile, 없는 semantic field/table, search limit 초과는 구조화된 오류 코드로 실패한다.

normalized fixture는 Catalog의 FK 4개와 직접·다단계·역방향 경로를 검증한다. flat과 legacy fixture는 여러 semantic field를 요청해도 required table이 하나이므로 빈 JOIN path를 반환한다. synthetic graph로 composite FK, cycle, disconnected graph, 동률 최단 경로를 별도 검증한다.

JOIN-capable SQLite SQL compiler를 완료했다.

```text
Query IR + Published Mapping + RESOLVED JoinPlan
→ anchor table AS t0
→ JoinPlan step 순서대로 t1, t2, ... 할당
→ Catalog FK의 원래 source = target predicate 생성
→ qualified SELECT/WHERE + parameterized LIMIT
→ read-only SQLite 실행
```

`backend/adaptive/compiler`의 core compiler는 `JoinCompilationRequest`만 입력받는다. table alias는 anchor와 traversal 순서로 `t0`, `t1`, ...을 할당한다. 역방향으로 탐색한 edge도 ON predicate는 FK 원래 child column과 parent column 방향을 사용하며 composite FK의 모든 column pair를 순서대로 `AND` 연결한다. 모든 table·column identifier는 double quote escaping하고, filter 값과 LIMIT는 기존 순서대로 parameter binding한다.

`SchemaAdaptiveSQLiteCompiler`는 기존 `ExecutionHarness` compiler protocol에 연결되는 adapter다. 생성 시 받은 Published Mapping과 실행 시 runtime mapping의 database, version, field 전체가 같아야 한다. path가 `AMBIGUOUS`이거나 plan이 mapping과 불일치하거나 self JOIN이면 SQL을 생성하지 않는다. 기존 `SingleTableSQLiteCompiler`는 기본값으로 남겨 legacy baseline 동작을 보존한다.

동일 Query IR을 flat·normalized·legacy fixture에서 실행하여 semantic result row가 같은지 검증했다. flat과 legacy는 JOIN 0개, normalized는 필요한 FK JOIN 4개를 생성한다. 현재 Query IR에는 정렬 표현이 없으므로 `ORDER BY`를 임의 생성하지 않았다. 정렬 지원은 Query IR 연산 범위를 명시적으로 확장할 때 함께 구현한다.

sqlglot 기반 AST read-only policy를 완료했다.

```text
CompiledQuery + Query IR + Published Mapping + RESOLVED JoinPlan
→ SQLite dialect parse
→ 단일 SELECT와 compiler AST shape 확인
→ projection/filter/table/column/JOIN allowlist 검증
→ placeholder 값·순서·타입과 parameterized LIMIT 검증
→ SQLite read-only executor
```

`backend/adaptive/policy`의 `AstPolicyContext`는 원본 계약만 저장한다. 허용 table과 column은 Published Mapping 및 JoinPlan에서 계산해 중복 설정 drift를 막는다. `AstReadOnlyPolicy`는 sqlglot 30.18.0에서 검증한 AST 구조를 default-deny로 검사한다. DML/DDL, 다중·빈 statement, CTE, subquery, set operation, ORDER BY, GROUP BY, HAVING, DISTINCT, 함수·산술·CASE, OR/NOT, 주석, table modifier를 현재 범위에서 거부한다.

JOIN table·순서·alias와 ON predicate는 JoinPlan과 정확히 같아야 한다. composite FK predicate는 원래 방향과 순서대로 정확히 한 번씩 있어야 한다. filter placeholder는 Query IR 순서와 값·타입이 일치해야 하고 마지막 parameter는 Query IR LIMIT여야 한다. 기존 single-table compiler의 unqualified column은 JOIN이 없는 legacy compatibility 경로에서만 허용한다.

안전성 계층의 책임은 다음과 같다.

- `ConservativeSqlPolicy`: sqlglot 없이 쓸 수 있는 정규식 baseline
- `AstReadOnlyPolicy`: Query IR·Published Mapping·JoinPlan 기반 구조 및 의미 allowlist
- `SQLiteReadOnlyExecutor`: AST 우회 여부와 무관한 read-only DB 권한, authorizer, timeout, row limit

flat·normalized·legacy fixture의 compiler SQL이 모두 AST policy를 통과하고 같은 semantic result를 반환하는지 검증했다. parse/token 오류는 `SQL_PARSE_ERROR`로 변환되어 harness 밖으로 빠져나가지 않는다.

기존 학습 데이터셋 JSONL loader와 데이터 품질 report를 완료했다.

```text
dataset.csv(JSONL bytes)
→ line-by-line UTF-8 decode + SHA-256
→ instruction/input/output record validation
→ valid record와 structured issue 분리
→ narrow identity normalization
→ duplicate/conflict 집계
→ versioned privacy-preserving JSON report
```

`backend.adaptive.dataset`은 `.csv` 확장자에 의존하지 않고 실제 JSONL 내용을 binary streaming으로 읽는다. 첫 행 UTF-8 BOM을 허용하고 빈 행, 잘못된 UTF-8·Unicode scalar·JSON, 객체가 아닌 값, 필수 field 누락과 타입 오류를 줄 번호가 포함된 issue로 남긴 뒤 다음 행 분석을 계속한다. 원본 instruction·질문·SQL은 변경하지 않는다.

중복 식별 key는 Unicode NFC, CRLF/CR 줄바꿈 통일, 앞뒤 공백 제거만 적용한다. SQL 대소문자, 내부 공백, quote와 literal은 합치지 않는다. 기본 report는 원문 대신 domain-separated SHA-256과 source line 목록을 저장하고, 제한된 preview는 명시적 CLI 옵션에서만 생성한다. conflict의 SQL variant preview에도 별도 상한을 적용한다. report에는 입력 절대 경로와 실행 시각을 넣지 않아 같은 입력과 설정에서 같은 core가 생성된다. 기존 report는 기본적으로 덮어쓰지 않으며 `--force`에서만 원자 교체한다. input과 output이 같은 경로나 hardlink를 가리키면 `--force`와 관계없이 거부한다.

실제 `dataset.csv`를 read-only로 분석한 결과는 다음과 같다.

- 파일 SHA-256: `66e6f2ed2e19cc09e702d0a4f0e696881b25d236deafcfea1d98bbe58221b8b1`
- 전체·유효 레코드: 5,385개·5,385개, issue 0개
- 고유 instruction·질문·SQL: 1개·5,354개·5,124개
- 반복 질문 그룹: 19개
- 동일 질문·동일 SQL duplicate group: 16개
- 동일 원문 record duplicate group: 16개
- 동일 질문·서로 다른 SQL conflict group: 3개
- identity normalization으로 key가 달라진 record: 0개

문서의 기존 “중복 질문 그룹 19개”는 반복된 질문 전체를 뜻한다. 그중 16개는 같은 SQL이 반복되고 3개는 서로 다른 SQL이 연결된 충돌이다.

CLI 진입점:

```powershell
python -m backend.adaptive.dataset inspect `
  --input "C:\path\to\dataset.csv" `
  --dataset-id cnu-original-v1 `
  --output ".schema-adaptive\reports\dataset-quality.json"
```

다음 개발 요청 시 **Phase 1의 SQL 정규화 규칙과 sqlglot 기반 기존 SQL AST 분석**부터 시작한다. 이번 단계에서 생성한 loader record를 입력으로 사용하되 SQL을 아직 Query IR로 변환하지 않고, 지원 구문 분포와 변환 가능성·수동 검토 사유를 먼저 구조화한다. 이후 SQL→Query IR 변환, 가명화, Phase 2 공통 평가 질문·기대 결과, Phase 5 evaluation runner 순서로 진행한다. 기존 시스템 baseline 실제 정확도·응답시간은 동일한 공통 질문 집합을 사용해 데스크톱에서 측정한다.

## 14. 첫 구현 단위

개발 착수 시 우선순위는 다음과 같다.

1. 기존 JSONL loader와 데이터 품질 report
2. 공통 Query IR 모델
3. 기존 SQL → Query IR 변환기
4. 동일 데이터를 가진 SQLite 스키마 3개
5. SQLite Schema Inspector와 `catalog.json`
6. 규칙 기반 mapping baseline
7. 관리자 교정 CLI
8. Query IR → SQL Compiler

첫 번째 완료 가능한 end-to-end 시나리오:

```text
SQLite DB 입력
→ catalog.json 생성
→ ontology mapping 후보 생성
→ 관리자가 CLI에서 교정·승인
→ mapping profile publish
→ 고정 Query IR compile
→ SQL 실행
→ 세 스키마 결과 일치 확인
```

이 단계까지 Ollama와 PostgreSQL은 필요 없다.

## 15. 평가 설계

### 15.1 비교 시스템

1. 기존 CNU fine-tuned 모델과 고정 schema prompt
2. 기존 모델과 동적 schema prompt
3. base instruct 모델과 동적 schema prompt
4. 규칙 기반 Schema Aligner + 직접 SQL 생성
5. ontology/Query IR/Compiler 기반 제안 시스템
6. 필요 시 multi-schema fine-tuned 모델

### 15.2 주요 지표

- Execution Accuracy
- Valid SQL Rate
- Query IR Accuracy
- Schema Linking Recall
- Mapping Accuracy
- Cross-schema Consistency
- Execution Repair Success Rate
- Unsafe Query Blocking Rate
- 신규 DB 온보딩 시간
- 관리자 확인·수정 횟수
- 평균 응답시간
- peak memory 또는 VRAM 사용량

### 15.3 스키마 변화 실험

- 컬럼 이름 변경
- 컬럼 타입 변경
- 컬럼의 다른 테이블 이동
- 테이블 분리
- 테이블 병합
- FK 추가·삭제
- 코드값 변경
- view 추가·삭제

변경 전후 같은 질문의 결과 일치율과 자동 복구에 필요한 관리자 작업량을 측정한다.

## 16. 논문 방향

### 16.1 추천 제목

> 이질적 대학 수강정보 데이터베이스에서 능동형 스키마 정렬과 스키마 변화 감지를 이용한 로컬 Text-to-SQL 에이전트

영문:

> A Local Text-to-SQL Agent with Active Schema Alignment and Schema Evolution Awareness for Heterogeneous University Course Databases

### 16.2 핵심 연구 질문

1. 단일 대학에서 생성한 학습 데이터로 학습된 모델은 다른 물리 스키마에 얼마나 일반화되는가?
2. 대학 수강정보 ontology와 Query IR이 스키마 종속성을 줄이는가?
3. 신뢰도 기반 관리자 교정이 최소 작업으로 mapping 정확도를 개선하는가?
4. schema evolution 감지와 선택적 재검토가 전체 재설정보다 효율적인가?
5. 로컬 모델과 제한된 메타데이터만으로 실용적인 정확도를 달성할 수 있는가?

### 16.3 예상 연구 기여

- 대학 수강정보 공통 ontology
- 자동 physical-to-semantic schema alignment
- 신뢰도 기반 active admin review
- 스키마 변화 대응 및 verified query 회귀검증
- 동일 데이터·다른 스키마를 이용한 평가 환경
- 한국어 질문, 영문·행정 약어 스키마 사이의 grounding 평가
- 단일 대학 데이터 편향과 완화 방법 분석

### 16.4 주장 범위

피할 표현:

> 모든 대학의 모든 수강신청 데이터베이스를 완전 자동으로 지원한다.

권장 표현:

> 관계형 데이터베이스로 구성된 대학 수강정보 시스템을 대상으로, 스키마 메타데이터와 제한된 샘플값을 분석하고 불확실한 의미만 관리자가 확인하도록 하여 최소 설정으로 Text-to-SQL 서비스를 구성한다.

## 17. 보안·개인정보 원칙

- DB 연결 계정은 read-only
- 단일 `SELECT`만 허용
- statement timeout과 행 제한
- AST 기반 허용 테이블·컬럼 검사
- 개인정보 후보 컬럼 자동 탐지 및 관리자 확인
- 학생정보는 사용자 권한 범위로 row-level 제한
- 모델 context에 전체 원본 행을 무분별하게 포함하지 않음
- 샘플값 수와 길이 제한
- 외부 모델 실험 시 민감 데이터 제거
- 교수명 등 공개 데이터도 재배포 조건 확인 후 가명화 여부 결정
- SQL, mapping version, reviewer, 실행 결과 감사 로그 유지

## 18. 주요 위험과 대응

### 위험 1. 범위 과다

대응: PostgreSQL, SQLite, 대학 수강정보, 읽기 전용 분석 질의로 초기 범위를 제한한다.

### 위험 2. 합성 스키마 편향

대응: 최종 평가에 독립적으로 작성되거나 실제 공개 자료에서 유래한 unseen schema를 포함한다.

### 위험 3. CNU 데이터 암기

대응: entity/template/schema/institution 단위로 데이터를 분리하고 가명화·다중 스키마 증강을 수행한다.

### 위험 4. 자동 매핑 오류

대응: confidence, evidence, admin review gate, mapping version, rollback을 사용한다.

### 위험 5. 모델 비교 불공정

대응: 같은 질문, schema context, token budget, timeout, decoding 설정을 기록하고 실행 결과 기준으로 비교한다.

### 위험 6. 노트북 자원 부족

대응: SQLite, Mock/Recorded Model, fixture 기반 테스트를 기본으로 하고 무거운 통합은 데스크톱에서 수행한다.

## 19. 미해결 질문

- 최초 지원할 ontology entity와 field의 정확한 범위
- Query IR이 지원할 SQL 연산 범위
- 실제 다른 대학 평가 스키마 확보 방법과 사용 조건
- 기존 5,385개 SQL 중 자동 IR 변환 가능 비율
- administrator confidence threshold 기본값
- PostgreSQL 외 dialect를 논문 범위에 포함할지 여부
- 기존 fine-tuned 모델을 IR 생성기로 추가 학습할지 여부
- 실제 관리자 사용자 평가를 진행할 수 있는지 여부
- mapping profile 저장 형식: JSON/YAML 또는 DB 테이블
- schema evolution 자동 publish 허용 범위

## 20. 의사결정 기록

### ADR-001: 수강 추천보다 Text-to-SQL 적응성을 핵심으로 유지

- 날짜: 2026-09-04
- 상태: 채택
- 결정: 강의 추천 기능을 주 연구 기여로 확장하지 않고, 수강정보 도메인 내 schema-adaptive Text-to-SQL을 핵심 주제로 삼는다.
- 이유: 기존 경연대회와 졸업 프로젝트의 핵심 정체성을 유지하고 더 명확한 연구 문제를 제공한다.

### ADR-002: 모든 산업 DB보다 이질적 대학 수강 DB를 목표로 제한

- 날짜: 2026-09-04
- 상태: 채택
- 결정: 범용 DB 전체가 아닌 대학 수강정보 관계형 DB에 우선 적용한다.
- 이유: 개발 범위와 평가 가능성을 확보하면서 기존 데이터와 도메인 지식을 재사용할 수 있다.

### ADR-003: 직접 NL-to-SQL보다 ontology와 Query IR 계층 도입

- 날짜: 2026-09-04
- 상태: 제안 채택, 구현 검증 필요
- 결정: 자연어를 공통 Query IR로 해석하고 deterministic compiler가 학교별 SQL을 생성한다.
- 이유: 특정 스키마 암기를 줄이고 결과를 검증·설명하기 쉽다.

### ADR-004: 관리자 교정을 필수적인 review gate로 설계

- 날짜: 2026-09-04
- 상태: 채택
- 결정: 불확실한 schema mapping과 코드 의미는 관리자 확인 후 publish한다.
- 이유: 물리 스키마만으로 업무 의미를 완전히 추론할 수 없으며, 관리자 작업량 자체가 평가 지표가 된다.

### ADR-005: 노트북에서는 SQLite와 Mock/Recorded Model 우선

- 날짜: 2026-09-04
- 상태: 채택
- 결정: Ollama와 PostgreSQL 없이 개발 가능한 코어를 먼저 완성한다.
- 이유: 현재 노트북 자원 제약이 핵심 개발을 막지 않도록 한다.

### ADR-006: 기존 CNU 데이터셋은 baseline과 IR 원천 데이터로 사용

- 날짜: 2026-09-04
- 상태: 채택
- 결정: 기존 데이터셋을 범용 적응 학습셋으로 직접 간주하지 않는다. 정제, 가명화, IR 변환, 다중 스키마 확장 후 사용한다.
- 이유: 단일 학교 값과 단일 물리 스키마에 대한 과적합 위험이 있다.

### ADR-007: 최소 실행 하네스는 compiler-owned SQL과 SQLite 방어 계층으로 시작

- 날짜: 2026-09-04
- 상태: 채택, AST 정책으로 확장 예정
- 결정: 첫 하네스는 외부 모델과 PostgreSQL 없이 `FakeModel → Query IR → single-table SQLite SQL → read-only 실행` 경로를 고정한다. SQL은 하네스 compiler가 생성한 parameterized `SELECT`만 허용한다.
- 이유: 현재 노트북에서도 반복 가능한 실행 기준선을 먼저 확보하고, 미설치된 `sqlglot`에 의존하지 않으면서 SQL 정책과 DB read-only 경계를 함께 검증하기 위해서다.

### ADR-008: 스키마 샘플값은 opt-in이며 fingerprint에서 제외

- 날짜: 2026-09-04
- 상태: 채택
- 결정: Schema Inspector는 기본적으로 메타데이터만 수집한다. 샘플값은 호출자가 명시한 경우에만 제한적으로 수집하며 스키마 fingerprint 계산에는 포함하지 않는다.
- 이유: 불필요한 개인정보 노출을 줄이고, 행 데이터 변경 때문에 mapping을 `STALE`로 잘못 판단하는 일을 방지하기 위해서다.

### ADR-009: 규칙 기반 점수는 설명 가능한 baseline이며 자동 publish하지 않음

- 날짜: 2026-09-04
- 상태: 채택, 가중치 실험 예정
- 결정: 첫 Schema Aligner는 컬럼명·자료형·테이블명의 결정적 가중합으로 confidence와 evidence를 생성한다. 높은 점수도 승인 후보일 뿐이며 관리자 review gate 전에는 운영 Mapping으로 publish하지 않는다.
- 이유: LLM 없이 반복 가능한 비교 기준을 확보하면서 이름이 비슷하지만 업무 의미가 다른 컬럼의 오매핑을 관리자가 차단할 수 있어야 하기 때문이다.

### ADR-010: Mapping publish는 명시적 검토와 불변 version을 요구

- 날짜: 2026-09-07
- 상태: 채택
- 결정: 모든 ontology field를 관리자가 `APPROVED`, `REMAPPED`, `REJECTED`, `IGNORED` 중 하나로 결정해야 publish할 수 있다. 활성 entity의 required field는 반드시 물리 컬럼에 매핑한다. publish 결과는 `mapping-vNNNN.json`으로 보존하고 `published.json`은 최신 version을 가리킨다.
- 이유: 높은 confidence를 운영 승인으로 오해하지 않게 하고, 관리자 개입량·수정 이유·version 이력을 논문 평가와 회귀 테스트에 사용할 수 있어야 하기 때문이다.

### ADR-011: 다중 스키마 fixture는 동일 논리 seed와 원자 manifest를 사용

- 날짜: 2026-09-07
- 상태: 채택
- 결정: flat, normalized, legacy/abbreviated fixture는 하나의 versioned `FixtureSeed`에서 생성한다. build마다 불변 generation 디렉터리를 만들고 모든 DB·Catalog·Proposal 생성 후 `bundle.json`을 원자 교체한다.
- 이유: 스키마 구조만 실험 변수로 유지하고, 반복 생성 중 실패해 서로 다른 generation의 artifact가 섞이는 일을 막기 위해서다.

### ADR-012: JOIN path는 Catalog FK 기반 최소 edge tree로 계산

- 날짜: 2026-09-07
- 상태: 채택
- 결정: Published Mapping으로 semantic field를 physical table에 연결한 뒤, Catalog FK를 무방향으로 탐색하여 필요한 table들을 잇는 최소 relation-edge tree를 계산한다. FK의 원래 child→parent 방향과 composite column pair 순서는 edge에 보존한다. 최소 edge set이 둘 이상이면 하나를 선택하지 않고 `AMBIGUOUS`로 반환한다.
- 이유: 이름 추측이나 모델 판단 없이 실제 DB 제약을 JOIN 근거로 사용하고, 역방향 탐색을 지원하면서도 SQL predicate를 손실하지 않으며, 모호한 경로를 숨기지 않기 위해서다.

### ADR-013: JOIN SQL alias는 JoinPlan traversal 순서로 할당

- 날짜: 2026-09-07
- 상태: 채택
- 결정: anchor table을 `t0`으로 두고 JoinPlan step의 `traversal_to` 순서대로 `t1`, `t2`, ...을 할당한다. ON predicate는 traversal 방향이 아니라 relation에 보존된 FK source·target 방향으로 생성한다. `AMBIGUOUS` path와 self JOIN은 현재 compiler가 명시적으로 거부한다.
- 이유: 학교별 table 이름과 관계없이 SQL 출력을 결정적으로 유지하고, 역방향 탐색이 FK predicate 의미를 뒤집지 않도록 하며, 지원하지 않는 모호성이나 동일 table 재등장을 조용히 잘못 컴파일하지 않기 위해서다.

### ADR-014: AST policy는 compiler 출력 shape를 default-deny로 검증

- 날짜: 2026-09-08
- 상태: 채택
- 결정: sqlglot 30.18.0과 SQLite dialect를 사용해 Query IR, Published Mapping, JoinPlan에서 허용되는 정확한 SELECT AST shape만 통과시킨다. 허용되지 않은 node, clause, table modifier, 주석은 기본 거부한다. 정규식 policy와 SQLite executor 방어 계층은 유지한다.
- 이유: compiler 소유 SQL도 변조나 구현 오류를 독립적으로 탐지하고, table·column·JOIN predicate와 parameter 의미를 실행 전에 검증하면서 parser 변화에 따른 보안 회귀를 version pin과 테스트로 통제하기 위해서다.

### ADR-015: 미완료 선행 Phase를 평가 runner보다 먼저 닫음

- 날짜: 2026-09-08
- 상태: 채택
- 결정: Phase 3~4의 schema-adaptive vertical slice를 확보한 이후에는 Phase 5 evaluation runner를 바로 구현하지 않는다. Phase 1의 데이터 loader·품질 report·SQL 변환 기반과 Phase 2의 공통 평가 질문·기대 결과를 먼저 준비한다. 노트북에서 실행할 수 없는 기존 모델 baseline 수치 측정은 동일 평가 자료와 측정 계약을 준비한 뒤 데스크톱 체크포인트에서 수행하며, 실행 전까지 완료로 표시하지 않는다.
- 이유: 평가 입력과 정답 기준이 없는 runner는 의미 있는 성능 비교를 만들지 못하고, 기존 데이터의 중복·충돌·누수가 정리되지 않으면 논문의 baseline과 제안 시스템 비교가 왜곡될 수 있기 때문이다.

### ADR-016: 품질 report는 좁은 identity 정규화와 비공개 원문을 기본으로 함

- 날짜: 2026-09-10
- 상태: 채택
- 결정: loader는 원본 문자열을 보존하고, 중복·충돌 식별 key에만 Unicode NFC, 줄바꿈 통일, 앞뒤 공백 제거를 적용한다. SQL 의미, 대소문자, 내부 공백은 다음 AST 분석 전까지 정규화하지 않는다. 기본 report는 질문·SQL 원문과 입력 절대 경로 대신 hash와 source line을 저장하며, preview는 명시적인 개수·길이 제한에서만 허용한다.
- 이유: 초기 품질 분석이 SQL 의미가 다른 사례를 잘못 병합하지 않게 하고, 실제 대학 데이터를 분석한 report 자체가 원문 데이터 복제본이 되는 것을 방지하면서도 문제 행을 재현 가능하게 찾아야 하기 때문이다.

## 21. 변경 이력

### 2026-09-04

- 최초 문서 작성
- 기존 프로젝트, 학습 데이터셋, 제안 시스템, 관리자 개입, 모델 전략 정리
- 경량 개발 환경과 단계별 로드맵 정의
- 논문 연구 질문, 평가 지표, 위험과 의사결정 기록 추가
- `feat/schema-adaptive-agent` 브랜치 생성
- Query IR 계약, FakeModel, validator, single-table compiler, read-only SQLite executor, trace/error 결과를 포함한 최소 실행 하네스 구현
- 정상·실패·SQL 쓰기 차단을 검증하는 단위 테스트 9개 추가
- `DatabaseAdapter`, `DatabaseCatalog`, read-only `SQLiteAdapter` 기반 Schema Inspector 구현
- PK·FK·타입 affinity·선택적 샘플·스키마 fingerprint 테스트 4개 추가
- versioned course-domain ontology YAML과 안전한 loader 구현
- 컬럼명·자료형·테이블명 근거를 제공하는 규칙 기반 Schema Aligner 구현
- flat 및 abbreviated SQLite 스키마의 mapping baseline 테스트 4개 추가
- 다음 개발 시작점을 관리자 교정 CLI와 versioned Mapping Profile publish로 고정

### 2026-09-07

- Mapping Proposal과 review draft의 원자적 JSON 저장·불러오기 구현
- 후보 승인·수동 remap·거절·ignore·조회 관리자 CLI 구현
- 미검토 field, required field 누락, 존재하지 않는 컬럼, 중복 컬럼, fingerprint/version 불일치 publish 차단
- 불변 `mapping-vNNNN.json`, 최신 `published.json`, 실행 하네스 변환 구현
- 기존 draft를 `init`으로 덮어쓰는 동작 차단
- 다음 개발 시작점을 동일 seed 기반 SQLite 스키마 3개 fixture로 이동
- 가명화된 공통 `FixtureSeed v1`과 flat·normalized·legacy SQLite builder 구현
- 세 DB의 15개 논리 필드와 6개 개설강좌 동등성 검증
- generation별 DB·Catalog·Mapping Proposal 생성 및 원자 `bundle.json` commit 구현
- 세 Catalog의 테이블·PK·FK·schedule cardinality와 15개 mapping top candidate 검증
- legacy `prof_nm` ontology hint 추가
- 다음 개발 시작점을 ontology relation graph와 JOIN path 계산으로 이동
- ontology entity relation과 physical FK relation Pydantic 계약 추가
- Catalog FK를 결정적 graph로 변환하고 composite FK를 단일 edge로 보존
- Published Mapping 기반 최소 JOIN path planner와 명시적 ambiguity 결과 구현
- normalized fixture의 직접·다단계·역방향 JOIN path와 flat·legacy 빈 path 검증
- malformed composite FK, cycle, disconnected graph, 동률 최단 경로, profile/search-limit 오류 회귀 테스트 추가
- 다음 개발 시작점을 JOIN-capable SQL compiler로 이동
- Published Mapping과 RESOLVED JoinPlan을 입력으로 받는 JOIN-capable SQLite compiler 구현
- JoinPlan traversal 순서의 결정적 `t0...tn` alias와 qualified SELECT/WHERE 생성
- 역방향 traversal에서도 FK 원래 방향을 보존하고 composite predicate 전체를 `AND`로 컴파일
- schema-adaptive compiler adapter를 기존 ExecutionHarness protocol에 연결하고 single-table baseline 보존
- flat·normalized·legacy에서 동일 Query IR의 실행 결과 동등성 검증
- ambiguity, invalid/disconnected plan, self JOIN, runtime mapping drift, identifier escaping 차단 테스트 추가
- 현재 Query IR에 없는 ORDER BY 생성을 보류하고 다음 개발 시작점을 AST read-only policy로 이동

### 2026-09-08

- sqlglot 30.18.0 기반 SQLite AST read-only policy와 Pydantic context 구현
- Published Mapping과 JoinPlan에서 table·column allowlist를 파생해 중복 설정 제거
- projection, WHERE, JOIN 순서·방향·composite predicate, placeholder와 LIMIT 의미 검증
- DML/DDL, 다중 statement, CTE, subquery, set operation, 미지원 clause와 주석 차단
- parse/token 오류의 구조화된 실패와 table modifier default-deny 추가
- 기존 정규식 policy와 SQLite read-only authorizer·timeout 방어 계층 보존
- flat·normalized·legacy compiler SQL의 AST 검증 및 결과 동등성 확인
- Phase 순서를 의존성 기준으로 적용하고 하드웨어 의존 항목은 별도 데스크톱 체크포인트로 유지하도록 로드맵 실행 원칙 명시
- 다음 개발 시작점을 Phase 1의 JSONL loader와 데이터 품질 report로 수정
- 다음 개발용 범위와 완료 기준을 `docs/prompts/phase1-jsonl-loader-quality-report.md`에 기록

### 2026-09-10

- versioned dataset record·issue·quality report Pydantic 계약 구현
- `.csv` 확장자의 JSONL을 binary streaming으로 읽는 UTF-8/BOM loader와 content fingerprint 구현
- malformed line 이후 분석 지속과 line-level 오류 taxonomy 구현
- exact record, 반복 질문, 동일 질문·SQL duplicate, 질문별 SQL conflict를 구분하는 결정적 report 구현
- 원문 비공개 기본값과 제한 preview, 기존 report 덮어쓰기 차단·원자 저장 CLI 구현
- 실제 5,385개 데이터셋의 기존 통계 재현 및 중복 질문 19개를 duplicate 16개·conflict 3개로 세분화
- 다음 개발 시작점을 SQL 정규화 규칙과 sqlglot 기반 기존 SQL AST 분석으로 이동
