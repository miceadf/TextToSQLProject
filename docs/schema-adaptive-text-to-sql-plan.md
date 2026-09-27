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

**현재 개발 운영 원칙(2026-09-19):** 팀의 사람 판단 항목은 대기 상태로 기록하고, 지금 단계에서 결정을 재촉하거나 합성 데이터 기반 개발을 멈추지 않는다. 완전 합성 입력의 로컬 구현·테스트·private 검증을 먼저 진행한다. 실제 CNU 자료의 추가 사용·공유 전 E3, 실제 데이터 배포·외부 시연 전 L5, 모델 성능 수치 확정 전 L1·L3를 확인한다. AI가 승인 상태를 임의로 바꾸지 않는다. 이 관문이 필요한 작업에 도달하면 준비된 결과와 구체적인 결정 항목을 제시한다.

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

후속 개발 프롬프트는 먼저 독립적으로 검증 가능한 작은 작업으로 범위를 줄인다. 서브 에이전트는 기본값이 아니다. 여러 파일·계층의 독립 작업이나 별도 리뷰가 구현 품질을 실질적으로 높일 때만 사용하고, 역할·수정 파일·출력 범위를 제한한다. 작은 작업은 메인 에이전트가 구현과 검증을 맡는다. 서브 에이전트 사용이나 답변 압축만으로 품질 또는 사용량 절감을 보장한다고 가정하지 않는다.

사람·사용자 검증 시점과 승인 관문은 [`docs/human-validation-checkpoints.md`](human-validation-checkpoints.md)에 둔다. 후속 개발 프롬프트와 중간 점검은 자동 테스트로 확인한 범위, 아직 필요한 사람 확인, 실제 데이터·모델·논문 주장 전의 필수 관문을 구분한다. 정식 gold 승인은 개발 후에 할 수 있지만, 핵심 도메인 의미·데이터 사용 범위·독립 평가 스키마 확보 가능성은 가능한 한 일찍 확인한다.

### Phase 0. 기준선 보존 및 설계

- [x] 새 개발 브랜치 `feat/schema-adaptive-agent` 생성
- [x] 기존 프로젝트와 데이터셋 구조 분석
- [x] 연구 범위와 시스템 방향 문서화
- [ ] 기존 시스템 baseline 질문·정확도·응답시간 기록

### Phase 1. 데이터셋 정제와 Query IR

- [x] JSONL loader 구현
- [x] 중복·충돌 탐지 report 구현
- [x] SQL 정규화 규칙 정의
- [x] sqlglot 기반 기존 SQL AST 분석
- [x] 공통 Query IR Pydantic model 정의
- [x] 기존 SQL을 Query IR로 변환
- [x] 수동 검토가 필요한 변환 목록 생성
- [x] 실제 식별값 가명화 도구 구현

### Phase 2. 다중 스키마 경량 실험 환경

- [x] 동일 seed 데이터를 가진 SQLite DB 3개 작성
- [x] flat schema 작성
- [x] normalized schema 작성
- [x] legacy/abbreviated schema 작성
- [x] 공통 평가 질문 40개 초안 선정(독립 사람 검토 대기)
- [x] 기대 결과 집합 작성(정적·3스키마 검증, 사람 승인 대기)
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
- [x] `RecordedModel` 구현(오프라인 재생 코어와 read-only evaluation CLI; live 수집 제외)
- [x] recording capture session 코어 구현(메모리 내 조립; storage·provider 제외)
- [x] private recording bundle storage와 선언형 provenance manifest 구현(provider 수집 제외)
- [x] provider-neutral capture orchestrator와 typed failure 분류 구현(실제 provider 제외)
- [x] Ollama `QueryModel` adapter 최소 호출·오류 변환 구현(합성 transport만 검증)
- [x] 데스크톱 Ollama adapter 합성 live smoke 2건(형식·검증 경계만 확인; 성능 평가 제외)
- [x] 합성 provider private capture→bundle→strict load 연결과 Ollama 선언 provenance hash 계약
- [x] Phase 5-10 private target 파일과 opt-in live capture 경계 구현(합성 transport만 검증; 실제 recording 미실행)
- [x] Phase 5-11 호출·저장 없는 `--check` 공통 preflight와 E3·L5 미기입 검토 양식 구현
- [x] Phase 5-12 완전 합성 질문 2건의 로컬 Ollama private capture·strict replay 형식 검증(성능 평가 제외)
- [x] Phase 5-13 fine-tuned tag의 합성 40×3 잠정 capture·offline 평가 연결(gold 사람 승인 전, 확정 성능 제외)
- [x] Phase 5-14 `UNKNOWN_FIELD` 비노출 분류와 단일 v2 prompt 합성 120쌍 탐색 비교(v1 기본 유지; 확정 성능 제외)
- [x] Phase 5-15 v2 실패 비노출 감사·paired 비교 무결성 계약·독립 평가 세트 설계 기준 고정
- [x] Phase 5-16 독립 합성 평가 세트 30건 초안·자동 검증(사람 검토·동결 전; question holdout)
- [x] Phase 5-17 독립 세트 문항 정리·단독 커버리지·FakeModel oracle smoke(사람 검토·동결·실제 모델 평가 제외)
- [x] 모델 응답 JSON schema 검증
- [x] 경량 evaluation runner 구현(FakeModel·SQLite fixture, 실제 모델 평가 아님)
- [ ] 기존 fine-tuned 모델 평가
- [ ] base instruct 모델 평가
- [ ] 동적 스키마 prompt 평가
- [ ] Query IR 생성 방식 평가
- [ ] 필요 시 multi-schema LoRA 학습 및 평가

### Phase 6. Agent와 사용자 상호작용

- [x] 실행 오류 taxonomy 정의
- [x] Phase 6-01 합성 입력의 제한된 QueryIR repair 경계(최초 1회 + 검증 실패 시 repair 1회; 사용자 확인·관리자 검토 큐는 미구현)
- [x] Phase 6-02 합성 결과의 결정적 critique 분류(`RepairSummary` 전용 순수 함수; 성공은 `EXECUTED_UNVERIFIED`, 실패는 code·stage 고정 분류; 사용자 확인·관리자 검토 큐는 미구현)
- [x] Phase 6-03 합성 모호성 신호·사용자 확인 경계(`clarification-gate-v2`; caller 제공 명시적 신호만 pending, 정확한 한 선택 확인·별도 취소; 자동 감지·QueryIR 보정·하네스 자동 실행 없음)
- [x] Phase 6-04 합성 관리자 검토 큐 경계(`review-queue-v1`; `RepairSummary`를 내부 critique 분류 후 운영 실패 후보만 메모리 등록; 성공·입력 실패는 미등록; 영속성·UI·승인 없음)
- [x] Phase 6-05 합성 검토 큐의 private snapshot 저장·strict load(`review-queue-snapshot-v1`; 고정 메타데이터만 명시적 private 파일에 저장; 기존 recording bundle validator 재사용; 덮어쓰기·UI·승인 없음)
- [x] Phase 6-06 합성 agent workflow 통합(`agent-workflow-v1`; 상세 repair 실행 → 내부 critique → 운영 실패 후보 queue 등록을 한 번의 명시적 coordinator 호출로 수행; 최종 `HarnessResult` 보존, 성공·0행 성공과 `REQUEST_INVALID` 미등록; 파일·네트워크·Ollama·snapshot 자동 저장 없음)
- [x] Phase 6-07 합성 영속 검토 큐 lifecycle(`durable-review-workflow-v1`; strict snapshot 복원 → agent workflow → `ENQUEUED`일 때만 새 immutable snapshot 게시; overwrite·current pointer·실제 운영 DB/UI/승인 없음)
- [x] Phase 6-08 합성 모호성 감지와 확인 연결(`ambiguity-detector-v1`; versioned literal rule set의 결정적 단일 match → 기존 `clarification-gate-v2` prepare/resolve, ruleset drift·다중 match fail closed; QueryIR 반영·실제 UI 없음)
- [x] Phase 6-09 확인 선택의 합성 QueryIR constraint 전달(`clarification-query-constraint-v1`; 재확인 → semantic constraint → constraint-aware provider 1회 → QueryIR·선택 준수 검증; 실행·UI 없음)
- [x] Phase 6-10 확인 선택의 합성 read-only 실행 연결(`clarification-execution-adapter-v1`; constraint 재생성 → 내부 one-shot model → 기존 validation·compile·policy·execute 경로, UI·session/expiry 없음)
- [x] Phase 6-11 합성 clarification session 경계(`clarification-session-v1`; pending 발급 → TTL·원자적 일회 소비 → 취소 또는 Phase 6-10 실행, HTTP/UI·영속 저장 없음)
- [x] Phase 6-12 합성 clarification HTTP 경계(`clarification-http-v1`; server-owned context/dependency → start/submit → 고정 HTTP 오류·redacted 결과, legacy mount/UI 없음)
- [x] Phase 6-13 합성 clarification ASGI 프로토타입(`synthetic-clarification-prototype-v1`; canonical 합성 context·결정론 provider·임시 read-only SQLite → loopback 전용 실행, legacy/UI/실서비스 없음)
- [x] Phase 6-14 합성 clarification React 흐름(`/adaptive-prototype`; strict response parser·메모리 token·option/cancel/result 상태, legacy 화면 교체·browser 실검증 없음)
- [x] Phase 6-15 합성 clarification 통합 browser smoke와 팀 handoff(두 option·결과·취소·no-match·연결 실패·재제출 차단·legacy 경로 확인; 합성 내부 공유 전용)
- [x] Phase 6-16 합성 활성 DB 선택과 메인 목록·질의 동기화(legacy 기준선 보존, flat·legacy-schema 등록 DB, session 범위 선택, canonical catalog·clarification 동일 DB, browser smoke)
- [x] Phase 6-17 private SQLite backend 등록 경계(32 MiB 제한·standalone 형식·private immutable storage·read-only immutable 검사·schema scan·mapping proposal, 검토 전 실행 registry 격리)
- [x] Phase 6-18 loopback SQLite upload와 검토 대기 요약(메인 `DB 변경` raw-body upload·Phase 6-17 strict registration 연결·`PENDING_REVIEW` schema/mapping 읽기 전용 표시, 실행 registry 격리 유지)
- [x] Phase 6-19 private mapping 검토·명시적 publish·실행 활성화(`INIT/APPROVE/REMAP/PUBLISH`, immutable mapping version·activation commit, strict reload 뒤에만 registry/catalog/clarification 편입)
- [x] validate → execute → critique → repair loop 구현(합성 계약 범위; `DetailedRepairOutcome`과 `AgentWorkflowCoordinator`로 연결)
- [x] 모호한 질문 감지 및 사용자 확인(합성 내부 prototype 구현·browser smoke 범위; L4 사람 검토·실서비스 연결은 별도)
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

기존 학습 데이터셋의 PostgreSQL AST 분석 기반을 완료했다.

```text
DatasetRecord.sql 원문
→ PostgreSQL dialect parse
→ comment-free conservative canonical AST round-trip
→ canonical hash + type-aware literal-redacted shape hash
→ AST feature 추출
→ Query IR 후보 상태와 reason code 분류
→ versioned privacy-preserving JSON report
```

canonicalization은 identifier, qualification, alias, literal과 clause 순서를 보존하고 comment와 formatting만 fingerprint에서 제외한다. shape hash는 복사 AST의 string, integer, decimal, boolean, NULL literal만 서로 다른 placeholder로 바꾸며 실행 동등성이나 중복 정답 판정에는 사용하지 않는다. 기본 report에는 질문·SQL·literal 원문을 저장하지 않고 source line과 hash, feature, 분류 결과만 저장한다. 제한 preview도 literal-redacted shape만 허용한다.

feature count는 AST node occurrence를 단위로 한 번씩 집계한다. 분류 우선순위는 `UNSUPPORTED_OR_UNSAFE`, `MANUAL_REVIEW`, `IR_EXTENSION_REQUIRED`, `CURRENT_IR_CANDIDATE` 순서다. 이는 자동 변환 성공 판정이 아니라 다음 SQL→Query IR 변환기의 보수적인 후보 선별 결과다.

실제 5,385개 SQL 분석 결과:

- parse·canonical round-trip 성공: 5,385개, issue 0개
- 고유 canonical SQL: 5,085개; 원문 경계 공백 기준 고유 SQL 5,124개보다 39개 적으며 comment·formatting 차이가 합쳐진 결과
- literal-redacted shape: 1,667개
- `CURRENT_IR_CANDIDATE`: 3,395개
- `IR_EXTENSION_REQUIRED`: 1,115개
- `MANUAL_REVIEW`: 875개
- `UNSUPPORTED_OR_UNSAFE`: 0개
- aggregate 675개, `GROUP BY` 4개, `ORDER BY` 122개, `LIMIT` 130개
- JOIN, subquery, CTE, set operation, window function: 각각 0개
- 모든 산술 AST 포함 SQL: 357개; 기존 256개는 `capacity - enrolled` 형태만 센 더 좁은 정의

CLI 진입점:

```powershell
python -m backend.adaptive.dataset analyze-sql `
  --input "C:\path\to\dataset.csv" `
  --dataset-id cnu-original-v1 `
  --dialect postgres `
  --output ".schema-adaptive\reports\sql-analysis.json"
```

기존 학습 데이터셋의 SQL→Query IR 변환기와 수동 검토 목록을 완료했다.

```text
DatasetRecord + PostgreSQL AST compatibility
→ 승인된 legacy dataset schema profile 검증
→ courses physical column을 ontology semantic field로 변환
→ projection·AND filter·typed literal·LIMIT intent signature 구성
→ 기존 Query IR validator와 signature 동등성 검증
→ 공개 conversion report + opt-in private Query IR JSONL
```

`cnu-courses-v1` source profile은 운영용 Published Mapping과 별도인 학습 데이터 변환용 승인 metadata다. `courses`의 10개 물리 컬럼만 명시적으로 매핑하며 의미 손실이 발생하는 `lecture_time`, `class_mode`는 제외한다. 자동 변환은 analyzer의 `CURRENT_IR_CANDIDATE`에만 허용하고, double-quoted identifier를 문자열로 추측하거나 문자열 숫자를 강제 변환하지 않는다. 음수 numeric literal은 AST의 부호를 보존하며 decimal은 현재 Query IR의 `float`로 정확히 왕복할 수 있을 때만 허용한다.

source SQL의 1~200 정수 LIMIT은 그대로 보존한다. LIMIT이 없으면 실행 안전 기본값 50을 적용하고 `CONVERTED_WITH_EXECUTION_POLICY`와 `DEFAULT_LIMIT_APPLIED`로 exact 변환과 구분한다. 동일 질문에 서로 다른 SQL이 연결된 conflict group 전체는 자동 학습 artifact에서 차단한다. 공개 report는 source line, hash, 상태와 reason만 저장하고 질문·SQL·literal·Query IR value는 opt-in private JSONL에만 기록한다. private output은 저장소의 gitignore된 `.schema-adaptive` 아래로 제한하며 public report를 마지막에 설치한다.

실제 5,385개 record의 read-only 변환 결과:

- `CONVERTED_EXACT`: 11개
- `CONVERTED_WITH_EXECUTION_POLICY`: 3,201개
- `REVIEW_REQUIRED`: 1,059개
- `UNSUPPORTED`: 1,114개
- 자동 변환 합계: 3,212개(59.6%)
- conflict 영향 record: 6개; 모두 자동 변환 차단
- 주요 review 사유: double-quoted identifier 345개, non-literal filter 328개, unmapped source column 113개, literal type mismatch 72개, unsupported LIKE 28개

CLI 진입점:

```powershell
python -m backend.adaptive.dataset convert-ir `
  --input "C:\path\to\dataset.csv" `
  --dataset-id cnu-original-v1 `
  --profile backend/adaptive/dataset/profiles/cnu-courses-v1.yaml `
  --ontology backend/adaptive/ontology/course-domain.yaml `
  --output ".schema-adaptive\reports\ir-conversion.json"
```

`pseudonymize-ir`는 opt-in private converted JSONL을 입력으로 받아 `cnu-pseudonymization-v1` 정책과 외부 key file을 적용한다. `Course.name/code`, `Department.name`, `Instructor.name`은 field별 namespace HMAC token으로 치환하고 나머지 6개 초기 field는 보존한다. 질문·instruction·Query IR의 대응을 한 record 전체에서 확인하지 못하면 부분 결과를 쓰지 않고 로컬 review로 보낸다. 출력의 source line·SQL hash·중복 group id는 별도 HMAC lineage token으로 바꾸며, 공개 summary에는 집계·fingerprint·잔여 위험만 기록한다. 생성물은 gitignore된 `.schema-adaptive` 아래의 불변 generation과 마지막 manifest로 묶는다. 이는 **가명화**이지 재식별 위험이 없는 익명화나 외부 공개 승인이 아니다. 보존하는 `Course.category`와 `CourseOffering.section`, filter로 드러나지 않는 질문 속 고유명사는 공개 전 별도 검토가 필요하다.

실제 원본 5,385건에서 변환된 3,212건을 저장소 밖 원본과 임시 메모리 key로 이어받아 read-only 검증했다. 자동 출력 가능 2,528건, 수동 검토 684건, 성공 출력의 알려진 sensitive value 잔존 0건이었다. 주요 검토 사유는 질문-IR 대응 불명확 542건, 겹치는 문자열 match 517건, filter 값의 질문·instruction 부재 176건이다(복수 사유 중복 집계). 수치는 현재 lexicon과 보수적 match 정책에 따른 결과이지 개인정보 안전성의 증명이 아니다. 실제 private artifact·key·가명 출력은 이 검증에서 파일로 저장하지 않았다.

CLI는 이전 `convert-ir --private-output` 결과와 저장소 밖 32바이트 이상 binary key를 사용한다. 예: `python -m backend.adaptive.dataset pseudonymize-ir --input .schema-adaptive/private/cnu-query-ir-v1.jsonl --dataset-id cnu-original-v1 --profile backend/adaptive/dataset/profiles/cnu-pseudonymization-v1.yaml --ontology backend/adaptive/ontology/course-domain.yaml --key-file C:\private\pseudonym-key.bin --key-id key-v1 --output-dir .schema-adaptive/pseudonymized/cnu-v1`. 출력은 기본 덮어쓰기 금지, `--force`에서만 manifest를 원자 교체한다. 공개 가능 여부와 key 보관·회전은 별도 운영 결정으로 남긴다.

Phase 2의 synthetic 공통 평가 질문·gold Query IR·기대 semantic 결과 초안을 완료했다. `backend/adaptive/evaluation/data/fixture_v1_cases.yaml`은 `FixtureSeed v1`(개설강좌 6건)의 40개 한국어 질문을 고정한다. 수동으로 선택한 offering key와 typed semantic row를 별도 seed-only 검증기가 확인하며, 같은 gold IR을 flat·normalized·legacy 스키마에서 compile·AST 검사·read-only 실행해 결과 멀티셋을 비교한다. seed·ontology 내용과 현재 Query IR JSON 계약을 각각 fingerprint로 고정한다. 물리 SQL 출력에서 gold를 역생성하지 않는다.

현재 coverage는 15개 field 전체, `equals`·`contains`·네 가지 크기 비교, 정규화 스키마 JOIN 0~4단계, 빈 결과 2건·한 행 14건·여러 행 24건이다. 질문의 과목/분반 단위를 맞추기 위해 course-only projection이 개설강좌를 축약할 수 있는 사례에는 분반을 명시했다. `python -m backend.adaptive.evaluation --format json`은 결정적 machine-readable coverage를, `--format review-table`은 검토 표를 출력한다. 두 출력 모두 모델 정확도가 아니다. 모든 40건은 `NEEDS_HUMAN_REVIEW`이며 독립적인 사람의 질문·정답 승인 전에는 논문의 확정 gold로 간주하지 않는다. seed가 6건뿐이고 연도·학기가 각각 한 값이며, 개발용 세 스키마는 unseen school이 아니다.

질문·IR·기대 결과의 의미가 바뀌면 기존 benchmark version을 조용히 재사용하지 않는다. JSON-schema fingerprint는 Pydantic 사용자 정의 validator의 동작까지 포착하지 않으므로 Query IR validator 의미가 바뀔 때는 Query IR 계약 및 benchmark version을 명시적으로 검토·갱신한다.

Phase 5의 경량 evaluation runner를 FakeModel·gold IR·세 SQLite fixture에 연결했다. `backend/adaptive/evaluation/runner.py`는 기존 `ExecutionHarness`의 schema-adaptive compiler, AST policy, read-only executor를 그대로 통과시킨다. gold·ontology·Query IR 계약과 실제 DB Catalog/Published Mapping의 fingerprint drift는 실행 전 차단한다. 사례×스키마별 결정적 request ID, typed IR 구조 일치, 중복을 보존한 semantic row 결과, 모델·하네스 오류 단계와 코드를 기록하되 기본 report에는 SQL·모델 응답 원문·결과 행·실행 시간·임시 경로를 저장하지 않는다.

분모는 예정된 모든 `(case_id, variant)` attempt다. 구조적 IR 일치, 하네스 실행 성공, semantic 정답, projection 불일치·비교 오류는 서로 다른 지표이며, semantic 정답은 전체 예정 attempt와 비교 가능 attempt 두 분모로 함께 기록한다. 세 스키마의 모델 IR 및 semantic 결과 일관성은 case 단위로 따로 보고하고 불완전 실행은 미평가로 남긴다. coverage tag별 집계는 field·operator·JOIN·cardinality 등 서로 겹치는 축이므로 모든 tag의 수를 합산해 전체 분모로 해석하지 않는다.

gold를 그대로 반환하는 CLI `python -m backend.adaptive.evaluation run-smoke --output .schema-adaptive/reports/fixture-runner-smoke.json`은 **oracle smoke**이며 모델 정확도 측정이 아니다. 40개 gold 모두 사람 검토 대기 상태라 이 report를 논문의 확정 성능으로 쓰지 않는다. 실제 모델·PostgreSQL 평가와 기존 시스템 baseline 정확도·응답시간 측정은 데스크톱 체크포인트로 남긴다. Phase 1 가명화 review 684건은 자동 학습 자료로 승인하지 않는다.

`backend/adaptive/harness/recorded.py`에 versioned JSON recording 계약과 `RecordedModel` 오프라인 재생 코어를 구현했다. 각 response는 결정적 request ID, database ID, 질문 원문의 UTF-8 SHA-256, 전체 runtime Mapping Profile의 canonical JSON SHA-256과 일치할 때만 원래 JSON 타입 그대로 재생된다. 누락, context 불일치, 기록된 provider 실패는 원문을 포함하지 않는 `RECORDED_*` code로 runner에 남는다. malformed JSON, 중복 object key·request ID, non-finite number, 잘못된 version과 field type은 전체 파일 load 단계에서 거부한다.

`python -m backend.adaptive.evaluation run-recorded`는 기존 private recording을 읽기 전용으로 불러와 같은 evaluation runner에서 재생한다. `model_run_id`는 recording에서만 가져오고 `oracle_smoke=False`로 고정한다. 누락·기록 실패·context 불일치는 전체 실행을 중단하지 않고 각 attempt에 남기며, report와 summary에는 offline replay 및 recording provenance 미검증 경고를 기록한다. 입력·출력 same-file과 덮어쓰기를 차단하고 실제 payload·경로는 report에 복제하지 않는다. 이 CLI도 live 모델 실행, recording 출처 또는 모델 성능을 증명하지 않는다.

`RecordingCaptureSession`은 provider나 파일 I/O 없이 명시적 성공 JSON·실패 코드를 기존 `ModelRecording`으로 조립한다. request와 runtime mapping에서 context fingerprint를 직접 계산하고 capture 순서를 보존한다. JSON으로 재현할 수 없는 값, 비유한 숫자, 잘못된 실패 코드, 중복 request ID와 finalize 이후 변경을 안전한 code로 거부한다. 성공 payload는 보정하지 않고 방어적으로 복사하며, finalize 결과는 기존 `RecordedModel`에서 그대로 재생할 수 있다. 이 코어는 provider provenance를 증명하거나 예외를 실패 종류로 추측하지 않는다.

`recording-bundle-v1`은 완성된 `ModelRecording`을 `.schema-adaptive` 아래 content-addressed immutable generation에 저장하고 `manifest.json`을 마지막 commit marker로 게시한다. manifest에는 payload 대신 byte hash·크기·record count·model run ID와 `DECLARED_UNVERIFIED` provenance만 기록한다. strict loader는 recording 계약과 generation/provenance fingerprint를 다시 계산하며 path traversal, symlink·Windows junction/reparse point, hardlink, git-tracked target, 불완전·변조 bundle을 차단한다. 이 provenance는 caller의 선언을 재현 가능하게 묶을 뿐 모델 출처를 독립적으로 증명하지 않는다.

`capture_recording()`은 기존 `QueryModel`을 provider-neutral 호출 경계로 재사용한다. 전체 target의 type·request ID 중복·request/mapping database 일치를 호출 전에 검증하고, 입력 순서대로 provider를 정확히 한 번씩 호출한다. 성공 JSON은 보정 없이 capture session에 전달하고 typed timeout·empty·cancelled·provider failure와 예상 밖 예외·비 JSON 반환은 payload 비노출 실패로 기록한 뒤 다음 target을 계속한다. provider에는 request·ontology·mapping의 깊은 복사본을 전달하며 gold fallback, retry, concurrency, 저장과 provenance 생성은 수행하지 않는다.

`OllamaQueryModel`은 명시적 base URL·모델 tag·timeout으로 `/api/generate`를 한 번 호출한다. `query-ir-v1` 결정적 프롬프트에는 질문, runtime ontology의 entity·field·타입·허용 연산자, Published Mapping과 교집합인 semantic field 이름만 넣는다. `stream: false`, `temperature: 0`을 고정하고 물리 mapping은 보내지 않는다. 성공 envelope의 JSON 값은 타입 그대로 반환하며 비 JSON SQL·설명문은 원문 문자열로 반환해 후속 Query IR 검증에서 판정한다. 중복 key·비유한 JSON, malformed envelope와 HTTP 오류는 안전한 `PROVIDER_ERROR`, 빈 응답은 `EMPTY_RESPONSE`, timeout은 `TIMEOUT`으로 분류한다. 주입 transport가 실제 cancellation을 알릴 때만 `CANCELLED`로 기록한다. 기본 동기 urllib transport는 능동 취소 신호를 지원하지 않는다. 합성 transport와 orchestrator·harness 연계만 검증했으며 실제 Ollama 호출, 응답 저장, 모델 성능 측정은 하지 않았다.

데스크톱에서 `text2sql_qwen:latest`를 정확한 tag로 지정해 합성 질문 2건의 adapter live smoke를 수행했다. 두 응답 모두 JSON object로 파싱되고 현재 QueryIR 구조 및 semantic field/operator 검증을 통과했다. 이는 해당 합성 프롬프트·입력의 형식 관찰이며, 이전의 SQL 전용 프롬프트에서 SQL 텍스트를 반환한 결과를 뒤집거나 모델 정확도를 증명하지 않는다. 원문 응답·질문·프롬프트는 저장하지 않았다. 안전한 요약은 [`docs/ollama-adapter-live-smoke-results-2026-09-19.md`](ollama-adapter-live-smoke-results-2026-09-19.md)에 둔다. Phase 7의 전체 Ollama provider 통합과 PostgreSQL·평가 실험은 미완료다.

`capture_private_bundle()`은 전체 target, model run ID, provenance, private 저장 경로를 provider 호출 전에 검증한 뒤 기존 `capture_recording()`과 `write_recording_bundle()`을 순서대로 연결한다. 저장 경로 preflight는 기존 storage validator를 공유하고 writer가 다시 검사한다. 수동 CLI는 명시적 `--synthetic`과 `FakeModel` fixture만 허용하며 실제 Ollama 호출 기능은 없다. 합성 결과를 strict loader와 `RecordedModel`로 재생해 순서·JSON 타입·typed failure를 확인했다.

`OllamaQueryModel.declared_provenance()`는 실제 adapter의 versioned prompt template UTF-8 bytes를 SHA-256으로 해시한다. 전송 설정 hash는 `{"options":{"temperature":0},"stream":false,"timeout_seconds":<float>}`의 UTF-8 canonical JSON(`sort_keys=True`, `ensure_ascii=False`, 공백 없는 separator, `allow_nan=False`)에서 계산한다. 모델 tag는 `model_id`, 호출자가 제공한 Ollama version은 `provider_version`, 선택적 `/api/tags` digest는 `model_artifact_sha256`에 별도로 둔다. base URL과 요청별 질문·ontology·mapping·응답은 provenance에 넣지 않는다. 모든 값은 `DECLARED_UNVERIFIED`이며 모델 artifact를 독립 증명하지 않는다.

Phase 5-10에서 `python -m backend.adaptive.harness.live_capture` 수동 진입점을 추가했다. `--live`가 없으면 호출·저장이 없으며, 입력은 `.schema-adaptive` 아래 Git 비추적 JSON 파일의 `capture-targets-v1` 계약으로 제한한다. 최상위 key는 `schema_version`, `targets`이고 각 target은 기존 `CaptureTarget`의 `request`·`ontology`·`mapping` JSON이다. 필수 field와 타입, 중복 JSON key·request ID, 전체 batch의 database 일치와 `PUBLISHED` 상태, model run ID·선언 provenance·private output/overwrite 정책을 첫 호출 전에 검사한다. 성공 시 target당 한 번만 Ollama adapter를 호출하고 기존 private bundle에 게시한다. CLI는 고정 상태·code, 건수와 선언 provenance fingerprint만 출력한다. synthetic CLI와 legacy 경로는 유지했다. 구현 범위는 [`docs/prompts/phase5-10-live-capture-boundary.md`](prompts/phase5-10-live-capture-boundary.md)에 둔다.

Phase 5-11에서 `--check`를 추가하고 `--live`와 상호 배타적으로 만들었다. 두 모드는 동일한 `preflight_live_capture()`와 `preflight_private_capture()`에서 private target·모델 설정·선언 provenance·run ID·output 저장 정책을 검증한다. `--check`는 target 건수와 선언 provenance fingerprint만 반환하며 provider 호출이나 파일 생성이 없다. live 경로는 검증된 batch를 기존 capture/storage로 전달하고 writer가 경로를 다시 검사한다. E3·L5 결정 양식은 [`docs/live-capture-human-review.md`](live-capture-human-review.md)에 `PENDING_HUMAN_REVIEW` 상태로 두었다.

Phase 5-12에서 완전 합성 target 2건의 `--check`를 먼저 통과시킨 뒤 로컬 `text2sql_qwen:latest`에 `--live`를 한 번 실행했다. 성공 2건·실패 0건을 Git 비추적 private bundle에만 기록했다. strict loader가 manifest·hash·건수·선언 provenance를 검증했고 `RecordedModel`이 두 응답을 입력 순서로 재생했다. 둘 다 JSON object와 현재 QueryIR 구조·semantic 검증을 통과했다. 이는 합성 입력의 전송·저장·재생 형식 검증이며 정확도 또는 40×3 점수가 아니다. 공개 가능한 요약은 [`docs/phase5-12-synthetic-live-capture-results-2026-09-19.md`](phase5-12-synthetic-live-capture-results-2026-09-19.md)에 둔다.

Phase 5-13에서 기존 benchmark v1과 세 합성 fixture의 검증된 runtime context로 `capture-targets-v1` 120건을 생성하는 작은 도구를 추가했다. runner의 결정적 request ID와 case×variant 순서를 그대로 사용하며 gold IR·기대 결과는 target에 넣지 않는다. 로컬 `text2sql_qwen:latest`에 `--check` 후 `--live` 1회로 120건을 수집했고 전부 capture 성공했다. Private bundle strict load 후 합성 SQLite fixture에서 `run-recorded`를 오프라인 재생해 120 attempt를 모두 분모에 남겼다. 탐색 집계는 구조 exact 51/120, 하네스 실행 99/120, semantic 일치 90/120이며 `UNKNOWN_FIELD` 21건·`SEMANTIC_MISMATCH` 9건이다. **Gold 40건은 모두 `NEEDS_HUMAN_REVIEW`이고 이 수치는 확정 fine-tuned 모델 성능이 아니다.** 안전한 요약은 [`docs/phase5-13-finetuned-synthetic-exploratory-results-2026-09-19.md`](phase5-13-finetuned-synthetic-exploratory-results-2026-09-19.md)에 둔다. 기존 fine-tuned 모델 평가 로드맵 항목은 미완료로 유지한다.

Phase 5-14에서 v1 private `UNKNOWN_FIELD` 21건을 비노출로 분류했다. ontology 밖 12, entity 접두사 불일치 5, 물리 column 형태 4건이었다. 허용 semantic field의 정확한 복사를 강조한 명시적 `query-ir-v2`를 한 번 시험했다. 동일 합성 40×3 개발 세트에서 v2의 구조 exact 0/120, 실행 42/120, semantic 일치 36/120, `UNKNOWN_FIELD` 72/120으로 v1보다 악화했다. V1을 기본값으로 유지하고 이 가설은 채택하지 않는다. 세부 paired 집계와 한계는 [`docs/phase5-14-versioned-queryir-prompt-trial-results-2026-09-19.md`](phase5-14-versioned-queryir-prompt-trial-results-2026-09-19.md)에 둔다. 기존 fine-tuned 모델 평가와 동적 스키마 prompt 평가의 확정 완료 항목은 여전히 미완료다.

Phase 6-02에서 `RepairSummary` 전용 결정적 critique 분류(`critique-disposition-v1`)를 합성 입력으로 구현했다. 성공(최초·repair 후·0행 포함)은 `EXECUTED_UNVERIFIED`로만 분류하고 `repair_attempted`·`recovered_after_repair`로 최초 성공·repair 회복·repair 후 실패를 구분하며 정답·만족·정확도를 추론하지 않는다. 실패는 최종 code·stage 허용 조합으로 `REQUEST_INVALID`·`MODEL_REVIEW`(IR 오류 및 `REPAIR_PROVIDER_ERROR`의 stage 없음 포함)·`CONTEXT_REVIEW`(DB·mapping·JOIN)·`SAFETY_BLOCKED`(SQL 정책)·`EXECUTION_FAILURE`(timeout·실행·결과 검증)·`INTERNAL_REVIEW`(`SQL_COMPILE_ERROR` 등)로 분류한다. 위조 stage·알 수 없는 code·모순 status·code·repair 횟수는 고정 code(`CRITIQUE_INVALID_SUMMARY`)로 거부한다. repair 없이 최종 결과가 초기 결과와 다르면 위조로 거부하고, repair가 표시된 요약은 초기 실패가 Phase 6-01 재시도 허용 쌍일 때만 받는다. 자동 사용자 확인·관리자 queue 등록은 하지 않으며, `INVALID_REQUEST`를 질문 모호성으로 취급하지 않는다. Phase 6-01 재시도 목록·최대 1회·context 격리와 `ExecutionHarness`·`QueryModel`·legacy 경로는 변경하지 않았다. 전체 loop·모호 질문 확인·관리자 검토 큐는 미완료다. Phase 6-02 범위는 [`docs/prompts/phase6-02-synthetic-critique-disposition.md`](prompts/phase6-02-synthetic-critique-disposition.md)에 둔다.

Phase 6-04에서 `RepairSummary`와 내부 `critique_repair_summary()` 호출을 연결해 운영 실패 후보만 등록하는 메모리 전용 검토 큐(`backend/adaptive/harness/review_queue.py`, `review-queue-v1`)를 합성 범위로 구현했다. `InMemoryReviewQueue.enqueue()`는 원본 `RepairSummary`와 명시적 run/request ID를 받아 내부에서 critique 분류하며, caller가 `CritiqueResult`를 주입하거나 disposition을 조작하는 API는 없다. `EXECUTED_UNVERIFIED`·`REQUEST_INVALID`은 `NOT_ELIGIBLE`로 미등록하고 `MODEL_REVIEW`·`CONTEXT_REVIEW`·`SAFETY_BLOCKED`·`EXECUTION_FAILURE`·`INTERNAL_REVIEW`만 `ENQUEUED` 후보로 둔다(`REPAIR_PROVIDER_ERROR`는 기존 critique 분류인 `MODEL_REVIEW`를 따른다). 항목은 version, run/request ID의 domain-separated SHA-256(canonical UTF-8), 최종 고정 code·stage·disposition, repair 시도/회복 flag, 안정 idempotency key만 가지며 질문·QueryIR·SQL·행·원문 예외·물리 mapping·path·secret·raw ID는 저장·반환·로그하지 않는다. 같은 run/request의 같은 결과는 `ALREADY_PRESENT`로 1개 유지, 다른 결과는 고정 `REVIEW_QUEUE_CONFLICT`로 거부하며 기존 항목을 덮어쓰지 않는다. 입력 ID 오류는 `REVIEW_QUEUE_INVALID_INPUT`로 등록 전 차단하고, critique 실패는 기존 `CRITIQUE_INVALID_SUMMARY` 그대로 전파하며 큐는 변하지 않는다. 반환·목록은 방어적 복사이며 첫 등록 순서를 유지한다. disk/DB/network/Ollama/private bundle 접근 0회이며 영속성·동시성·다프로세스 dedupe·관리자 작업 상태·승인·publish·재시도 지시를 보장하지 않는다. 실제 운영 큐 저장과 전체 loop 체크박스는 Phase 6-06에서 합성 계약으로 닫았다. Phase 6-04 범위는 [`docs/prompts/phase6-04-synthetic-review-queue.md`](prompts/phase6-04-synthetic-review-queue.md)에 둔다.

Phase 6-06에서 `BoundedRepairCoordinator`에 최종 `HarnessResult`를 보존하는 상세 계약(`DetailedRepairOutcome`)과 작은 `AgentWorkflowCoordinator`(`backend/adaptive/harness/agent_workflow.py`, `agent-workflow-v1`)를 합성 범위로 구현했다. 기존 `run()`은 새 `run_detailed()`에 위임하며 호출 예산(모델 1회·repair 최대 1회·재실행 없음)을 바꾸지 않는다. terminal 초기 실패와 repair 후 성공/실패는 해당 최종 harness result를 그대로 보존하고, `REPAIR_PROVIDER_ERROR`는 가짜 code/result 없이 `final_result=None`으로 부재를 명시한다. workflow는 명시적 `model_run_id`·request·ontology·Published mapping·model·repair provider·주입 queue를 받아 상세 repair 실행 → 내부 `critique_repair_summary()` → 운영 실패 후보 등록을 순서대로 수행하며, caller의 `CritiqueResult`/disposition 주입 API는 없다. 성공·0행 성공과 `REQUEST_INVALID`은 기존 큐 계약대로 미등록하며 정답·만족·정확도를 추론하지 않는다. 실행 전 `model_run_id`·(valid request의) queue 호환 request ID·queue/model/provider/ontology/mapping 타입을 단일 공개 helper(`validate_queue_identifier`)로 검증해 모델·repair·DB 실행 후 실패를 막고, queue conflict·내부 계약 불일치는 고정 code(`AGENT_WORKFLOW_CONFLICT`·`AGENT_WORKFLOW_INTERNAL_ERROR`)로 fail closed하며 기존 entry를 보존한다. 실행 후 queue 등록이 실패할 수 있는 잔여 경계(읽기 전용 호출은 이미 소비되고 queue는 보존)와 side effect를 모듈 docstring에 문서화했다. 자동 snapshot write·overwrite·retry·clarification·QueryIR 보정·mapping/glossary/verified-query 변경은 없다. 합성 테스트(`tests/adaptive/test_agent_workflow.py` 45개: 기본 31개+위조·불일치 회귀)로 호출 횟수·최종 결과 보존·queue side effect·비노출·방어적 복사를 검증했다. `모호한 질문 감지 및 사용자 확인` 전체 항목, 실제 운영 영속 큐, 교정 반영은 미완료로 유지한다. Phase 6-06 범위는 [`docs/prompts/phase6-06-synthetic-agent-workflow.md`](prompts/phase6-06-synthetic-agent-workflow.md)에 둔다.

Phase 6-07에서 `review-queue-snapshot-v1` strict load와 `AgentWorkflowCoordinator`를 잇는 합성 영속 lifecycle(`backend/adaptive/harness/durable_review.py`, `durable-review-workflow-v1`)을 구현했다. caller가 queue·critique·disposition을 주입하지 못하게 하고, 현재 snapshot을 전체 계약으로 재검증해 새 `InMemoryReviewQueue`로 방어적으로 복원한 뒤 기존 workflow를 한 번 실행한다. `ENQUEUED`만 caller가 지정한 서로 다른 새 private snapshot에 no-overwrite 원자 게시하고 `ALREADY_PRESENT`·`NOT_ELIGIBLE`은 파일을 쓰지 않는다. source/next 경로는 모델·provider·executor 호출 전에 공유 private path validator로 검사하며, conflict·입력·내부·저장 실패를 고정 code로 분리한다. 반환은 기존 `AgentWorkflowResult`, `WRITTEN`/`UNCHANGED`, content hash·entry count만 추가하고 raw path·질문·SQL·행·raw ID를 복사하지 않는다. 독립 검토에서 private validator와 queue 내부 상태의 교차 모듈 직접 접근을 공개 검증 경계와 queue 소유 복원 helper로 교체하고, workflow 내부 오류 오분류를 수정했으며, 게시 후 재읽기·결과 생성 실패 경계를 없앴다. hard-link 게시 후 temp 정리 실패는 새 target을 롤백하고 고정 storage 오류로 닫는다. overwrite·current pointer·동시성/잠금·자동 retry·실제 운영 DB/UI/승인은 없고 check-then-use race 비보장과 실행 후 게시 실패 side effect는 남는다. 합성 테스트 22개와 인접 회귀 101개(1개 skip)를 통과했다. Phase 6-07 범위는 [`docs/prompts/phase6-07-synthetic-durable-review-queue.md`](prompts/phase6-07-synthetic-durable-review-queue.md)에 둔다.

Phase 6-08에서 versioned caller rule set의 literal trigger만 사용하는 합성 모호성 감지 경계(`backend/adaptive/harness/ambiguity_detector.py`, `ambiguity-detector-v1`)를 구현했다. NFKC·casefold·whitespace 정규화 후 0 match는 통과, 정확히 1개 rule match는 결정적 `AmbiguitySignal`, 2개 이상은 고정 conflict로 닫고, 기존 `clarification-gate-v2`의 `prepare`·`resolve`를 재사용한다. 모든 공개 입력은 dump-then-validate로 재검증하고 Published mapping·database 일치·ontology semantic ref만 허용한다. option ID는 canonical ruleset 전체 fingerprint를 포함한 domain-separated SHA-256이므로 rule/trigger/option drift는 과거 pending 확인에 실패하며, 입력 순서만 바뀐 동등 ruleset은 같은 ID를 유지한다. 원문 질문·raw ID·rule/option key·trigger·물리 mapping·SQL·행은 반환·오류·로그에 넣지 않는다. 이는 literal 규칙 탐지일 뿐 자연어 의미 이해가 아니며 QueryIR 반영·실행·실제 UI·session/expiry는 없다. 전체 `모호한 질문 감지 및 사용자 확인` 항목은 미완료로 유지한다. Phase 6-08 범위는 [`docs/prompts/phase6-08-synthetic-ambiguity-detection.md`](prompts/phase6-08-synthetic-ambiguity-detection.md)에 둔다.

Phase 6-09에서 확인된 선택을 후속 생성에 전달하는 합성 경계(`backend/adaptive/harness/clarification_query_constraint.py`, `clarification-query-constraint-v1`)를 구현했다. caller는 result/signal/semantic ref/constraint를 주입하지 못하고 원래 request·ontology·Published mapping·ruleset·pending·choice와 constraint-aware provider만 전달한다. coordinator는 기존 detector/clarification으로 재감지·재확인하고 opaque 선택 fingerprint를 정확히 한 ontology entity/field로 역매핑한 뒤 request/database·ontology/mapping fingerprint와 semantic ref만 가진 frozen constraint를 provider의 방어적 복사본에 정확히 1회 전달한다. provider 반환은 `QueryIR` 재검증과 기존 `validate_query_ir()`를 통과해야 하며, entity 선택은 `QueryIR.entity` 정확 일치, field 선택은 select/filter 최소 1회 사용을 강제한다. 알려진 raw request/database/rule/trigger/option/physical 이름의 QueryIR 반사는 반환 전 차단하고 semantic 이름과 우연히 같은 물리 이름은 과차단하지 않는다. 독립 검토에서 constraint 중복 필드를 제거하고 고정 violation code와 reflection 경계를 보완했다. 이는 정답·사용자 만족 판정이 아니며 compile/execute/repair/UI/session/expiry는 없다. 전체 `모호한 질문 감지 및 사용자 확인` 항목은 미완료로 유지한다. Phase 6-09 범위는 [`docs/prompts/phase6-09-clarification-query-constraint.md`](prompts/phase6-09-clarification-query-constraint.md)에 둔다.

Phase 6-10에서 확인 선택으로 생성·검증된 QueryIR를 기존 읽기 전용 실행 경로에 연결하는 합성 adapter(`backend/adaptive/harness/clarification_execution_adapter.py`, `clarification-execution-adapter-v1`)를 구현했다. 공개 입력은 원래 context/ruleset/pending/choice, constraint-aware provider, read-only executor뿐이고 QueryIR·constraint·model·결과·SQL 주입 API는 없다. Phase 6-09 결과를 내부 one-shot model에만 담아 동일한 request/ontology/mapping일 때 한 번 반환하고 기존 `ExecutionHarness`의 request/context 검증, QueryIR 재검증, compile, SQL policy, read-only execute, result 검증을 그대로 통과시킨다. compile 이후 기존 실패 code/stage와 QueryIR는 보존하고, 초기 단계 실패·QueryIR 불일치·request fingerprint 불일치·모순된 성공/실패 nested 결과는 고정 adapter 내부 오류로 거부한다. 독립 검토에서 이 nested 불변식을 반환 모델 자체에도 강제했다. 실제 UI·session/expiry·repair·Ollama·PostgreSQL·CNU 자료는 연결하지 않았고 전체 `모호한 질문 감지 및 사용자 확인` 항목은 미완료로 유지한다. 다음 작은 범위는 pending 발급과 choice 제출·실행을 잇는 합성 API/session 경계다. Phase 6-10 범위는 [`docs/prompts/phase6-10-clarification-execution-adapter.md`](prompts/phase6-10-clarification-execution-adapter.md)에 둔다.

Phase 6-11에서 감지·pending 발급과 선택 제출·Phase 6-10 실행을 framework-neutral 메모리 session(`backend/adaptive/harness/clarification_session.py`, `clarification-session-v1`)으로 연결했다. `start`는 원래 context를 재검증·snapshot하고 no-match는 `NOT_REQUIRED`, 단일 match는 opaque bearer token과 기존 pending을 반환한다. 내부에는 domain-separated token hash만 key로 저장하고 1초~1일 TTL을 주입 monotonic clock에 묶는다. `submit`은 유효 choice와 active token을 lock 안에서 downstream 전에 원자적으로 소비하므로 동시 제출 중 하나만 실행되며, 취소·unknown option·provider/compile/policy/executor 실패 뒤에도 재활성화하지 않는다. 취소는 기존 detector/gate 재확인만 수행하고, 확인은 저장 context로 Phase 6-10을 호출한다. 독립 검토에서 clock 실패 시 미소비, lazy expiry raw-context 잔류, 짧은 합성 token 허용, package export 누락과 deadline 정밀도 경계를 보완했다. active record는 raw question/context를 프로세스 메모리에 보관하며 후속 활동 시 만료 record를 lazy 제거하지만 즉시 안전 삭제·background cleanup·restart/cross-process exactly-once는 주장하지 않는다. 실제 HTTP/UI·인증·rate limit·영속 저장은 없으므로 전체 `모호한 질문 감지 및 사용자 확인`은 미완료다. Phase 6-11 범위는 [`docs/prompts/phase6-11-clarification-session-boundary.md`](prompts/phase6-11-clarification-session-boundary.md)에 둔다.

Phase 6-12에서 Phase 6-11을 legacy app과 분리된 FastAPI sub-application(`backend/adaptive/api/clarification_http.py`, `clarification-http-v1`)의 두 POST endpoint로 노출했다. server-owned loader가 request의 database ID에 맞는 runtime ontology·Published mapping·detector rules를 공급하며 caller는 context/pending/IR/model/executor를 주입하지 못한다. 같은 coordinator/provider/executor가 app lifetime 동안 재사용된다. start 응답은 status와 실제 submit 가능한 opaque option만, submit 성공은 columns/JSON rows만, 실행 실패는 기존 fixed code/stage만 노출하고 SQL·QueryIR·constraint·trace·raw ID·fingerprint·내부 message는 제거한다. malformed body부터 session/constraint/provider/internal 오류까지 단일 고정 code→HTTP status 표로 변환하며 예외 원문을 응답·stdout/log에 반영하지 않는다. 독립 검토에서 Phase 6-11이 fingerprint된 pending option을 반환해 직접 submit할 수 없던 결함을 수정해 감지 option과 pending binding을 결과 자체에 결합했고, HTTP 재감지를 제거했다. token/options nested 위조, non-JSON/비유한 row, row-column 불일치, 임의 error code/stage도 차단했다. legacy `backend/main.py`는 import·수정·mount하지 않았다. 실제 auth/TLS/proxy log·React UI·Ollama·PostgreSQL은 미연결이므로 전체 사용자 확인 항목은 미완료다. Phase 6-12 범위는 [`docs/prompts/phase6-12-clarification-http-boundary.md`](prompts/phase6-12-clarification-http-boundary.md)에 둔다.

Phase 6-13에서 Phase 6-12 factory를 실행 가능한 합성 ASGI 프로토타입(`backend/adaptive/api/synthetic_prototype.py`, `synthetic-clarification-prototype-v1`)으로 조립했다. 고정 database ID 하나에 `DEFAULT_SEED` 기반 임시 flat SQLite, 기존 canonical semantic field `Course.name`·`Instructor.name`, Published mapping, literal detector rule, 선택 field만 반환하는 결정론 provider를 결합했다. context loader는 매 호출 방어적 복사본을 반환하고 SQLite 질의는 기존 read-only executor·compiler·SQL policy를 그대로 통과한다. `python -m backend.adaptive.api.synthetic_prototype --port 8123`은 host를 `127.0.0.1`로 고정하고 reload/access log를 끄며 `.env`·실제 DB URL·Ollama 설정을 읽지 않는다. 독립 검토에서 CLI 실행 시 임시 DB 이중 생성, invalid port 원문 반사, 서버 시작 실패 시 cleanup 지연을 수정하고 canonical ontology 명칭을 재사용하게 했다. 신규 7개와 Phase 6-08~12 관련 총 92개 테스트를 통과했다. 실제 서버 장시간 운용·브라우저 UI·인증/rate limit·영속 session·실제 Ollama/PostgreSQL/CNU 자료는 포함하지 않는다. 재현 절차는 [`docs/synthetic-clarification-prototype.md`](synthetic-clarification-prototype.md), 구현 범위는 [`docs/prompts/phase6-13-synthetic-clarification-asgi-prototype.md`](prompts/phase6-13-synthetic-clarification-asgi-prototype.md)에 둔다.

Phase 6-14에서 Phase 6-13 endpoint를 쓰는 별도 합성 React 화면(`src/adaptive-prototype/`, `/adaptive-prototype`)을 추가했다. start `NOT_REQUIRED`/`PENDING`, option 선택, cancel, submit 성공·실패를 명시적 상태로 분리하고 response를 strict runtime parser로 재검증한다. token은 pending 동안 ref 메모리에만 두고 submit/cancel 직전 제거하며 storage·cookie·auth header·원문 오류 반사를 사용하지 않는다. operation ID와 `AbortController`로 stale response와 reset/unmount를 처리하고 결과는 backend column 순서의 generic table로만 표시한다. Vite는 `/api/adaptive`만 별도 loopback target으로 proxy하고 legacy `/api/v1`과 기존 `App`은 유지한다. 독립 검토에서 pending 중 start/reset으로 session이 고아가 되는 상태, 선택 전 token 조기 삭제, 느슨한 submit 및 execution code/stage 검증, legacy와 adaptive proxy 환경변수 충돌, system Python 실행 script, unknown error suffix 오분류를 수정했다. Node 전용 16개, 변경 파일 ESLint, frontend build, Phase 6-13 backend 7개 테스트를 통과했다. 실제 browser 조작·frontend/backend 실기동 smoke는 아직 수행하지 않아 전체 `모호한 질문 감지 및 사용자 확인`은 미완료다. 구현 범위는 [`docs/prompts/phase6-14-synthetic-clarification-react-flow.md`](prompts/phase6-14-synthetic-clarification-react-flow.md)에 둔다.

Phase 6-15에서 backend+Vite loopback을 실제 기동하고 설치된 Chrome headless의 DOM·network 경로로 `/adaptive-prototype`을 검증했다. 두 option, 강의명·교수자명 각 6행 결과, cancel, no-match, terminal 재제출 control 제거, legacy `/` 유지가 통과했다. adaptive proxy를 사용하지 않는 loopback port로 분리한 Vite에서도 backend 연결 실패가 token·SQL·경로·endpoint·원시 응답 없이 일반 오류로 닫혔다. 독립 검토에서 비-2xx 비-JSON proxy 오류를 response 계약 오류로 오분류하던 client를 일반 server 오류로 수정하고 회귀 테스트를 추가했다. 팀 공유 명령·관찰표·안전 경계는 [`docs/synthetic-clarification-team-handoff.md`](synthetic-clarification-team-handoff.md)에 둔다. 이 증거로 합성 내부 prototype의 `모호한 질문 감지 및 사용자 확인` 구현 항목을 완료하지만 L4 사람 사용성 검토, 실제 auth/provider/DB, 외부 시연 승인은 포함하지 않는다. 구현 지침은 [`docs/prompts/phase6-15-synthetic-prototype-browser-smoke.md`](prompts/phase6-15-synthetic-prototype-browser-smoke.md)에 둔다.

Phase 6-16에서 메인 화면에 `DB 변경` 흐름을 추가하고 legacy 기준선과 서버 소유 합성 flat·legacy-schema DB 두 개를 선택할 수 있게 했다. 활성 선택은 backend 전역 상태가 아니라 `sessionStorage`에 두고 매번 strict registry 응답으로 재검증한다. 합성 선택 시 canonical course catalog와 clarification start가 같은 DB ID를 사용하고, schema fingerprint drift·미등록 DB·unsafe compile·변조 응답은 redacted 고정 오류로 닫는다. DB 변경은 선택 강의·경고·hover·modal 상태와 진행 요청을 무효화하며 legacy API 실패도 빈 성공으로 숨기지 않는다. 실제 Chrome smoke에서 두 DB 각 6개 동일 canonical 강의, reload 선택 유지, 같은 DB clarification 6행, 중복·미등록 query fail-closed, legacy backend 부재 오류·재시도가 통과했다. 실제 CNU DB·PostgreSQL·Ollama·파일 업로드·mapping publish·L4 승인은 포함하지 않는다. 구현 지침은 [`docs/prompts/phase6-16-active-database-selection.md`](prompts/phase6-16-active-database-selection.md)에 둔다.

Phase 6-17에서 사용자가 제공한 standalone SQLite 파일을 `.schema-adaptive/registered-databases`에 immutable/no-overwrite registration으로 격리하는 backend core를 추가했다. source는 단일 read fd에서 header·32 MiB 상한·SHA-256·streaming copy를 처리하고 sidecar/WAL·redirect/hardlink를 거부하며, private 복사본만 `mode=ro&immutable=1`·`query_only`·`quick_check`로 검사한다. 표본값 없는 기존 `SQLiteAdapter` catalog와 rule-based mapping proposal을 생성하고 `registration.json`을 `PENDING_REVIEW` commit marker로 게시한다. strict loader는 허용 파일 집합·크기/hash·exact JSON 계약·database/schema/ontology 일치와 DB 재검사를 수행한다. 독립 검토에서 redirect parent 선검증, source identity, JSON extra/coercion, bounded load, 파일 fsync를 보강했다. 이 등록은 Phase 6-16 fixed registry를 변경하지 않아 검토·publish 전에는 course catalog·clarification에서 선택할 수 없다. 구현 지침은 [`docs/prompts/phase6-17-private-sqlite-registration.md`](prompts/phase6-17-private-sqlite-registration.md)에 둔다.

Phase 6-18에서 loopback prototype의 메인 `DB 변경` modal에 standalone SQLite 등록 흐름을 추가했다. 브라우저는 `application/x-sqlite3` raw body를 보내고 HTTP 경계가 선언·실측 32 MiB를 streaming으로 제한해 exclusive 임시 파일에 기록한 뒤 기존 Phase 6-17 `register_database()`와 `load_registration()`만 호출한다. 성공 응답은 strict reload의 `PENDING_REVIEW` manifest와 sample 없는 schema/mapping proposal metadata로만 만들며 path·row/sample·SQL·SQLite 상세 오류를 제거한다. UI는 schema와 상위 mapping 후보를 읽기 전용으로 보여주고 active/pending DB 선택, sessionStorage, course catalog, clarification link를 바꾸지 않는다. 독립 `gpt-6-luna` review-only 검토와 메인 재현에서 modal 취소 후 `uploading` 고착을 수정했고, 메인 검토에서 strict response count/status 검증과 빈 query segment 거부를 보강했다. 관련 backend 31개 통과·1개 환경 skip, frontend 17개·ESLint·build가 통과했다. 실제 browser upload smoke는 수행하지 않았으며 mapping 수정·승인·publish와 실행 registry 편입은 포함하지 않는다. 구현 지침은 [`docs/prompts/phase6-18-loopback-sqlite-upload.md`](prompts/phase6-18-loopback-sqlite-upload.md)에 둔다.

Phase 6-19에서 Phase 6-17 registration의 server-owned proposal을 private review draft로 열고 `APPROVE`/`REMAP`한 뒤 명시적 `PUBLISH`가 성공한 경우에만 실행 권한을 부여했다. review 저장소는 registration과 분리된 Git ignored 경로에서 ID lock, strict JSON, immutable `mapping-vNNNN.json`, mutable review pointer, manifest-last `activation.json`을 사용한다. 실행 registry는 매 요청 strict activation을 재검증해 유효 private DB만 결정적으로 병합하며, catalog와 clarification은 해당 Published Mapping, physical relation graph, schema-adaptive compiler/AST policy, read-only SQLite executor를 사용한다. `activation.json`만 실행 권한의 원자 commit record이므로 재게시 중 pointer가 먼저 바뀌어도 기존 activation은 계속 유효하고, draft-only·미완료·변조 artifact는 registry에서 숨긴다. UI는 업로드 요약 아래에서 검토·publish를 수행하며 publish 성공 응답 직후 registry를 재조회하되 active DB를 자동 변경하지 않는다. 독립 `gpt-6-luna` high 검토에서 재게시 원자성, HTTP review body의 사후 크기 검사, publish 후 detail 조회 실패의 성공 상태 롤백을 확인해 각각 activation-authority 분리, streaming 64 KiB 상한, best-effort detail refresh로 수정했다. 신규 backend 13개, 관련 backend 36개·1 skip·7 subtests, frontend 27개, ESLint·build가 통과했다. 실제 browser 통합 smoke, 실제 학교 DB·PostgreSQL·Ollama·L2/L4 승인은 수행하지 않았다. 다음 작은 범위는 Phase 6-20의 합성 browser end-to-end smoke와 팀 handoff 갱신이다. 구현 지침은 [`docs/prompts/phase6-19-private-mapping-publish-activation.md`](prompts/phase6-19-private-mapping-publish-activation.md)에 둔다.

다음 시작점은 Phase 6-19로, 합성 private registration의 mapping proposal을 검토 draft로 열어 수정·승인하고 명시적으로 publish한 뒤에만 Phase 6-16 실행 registry에 편입하는 권한 상승 흐름을 별도 구현한다. 실제 학교 DB mapping publish·질의에는 L2를 적용하며, 이 합성 로컬 단계에서 실제 CNU 자료를 사용하지 않는다. PostgreSQL은 Phase 7에서 별도 adapter로 시작한다. L4는 팀 사용성 검토 전, E3/L5는 실제 CNU 자료 사용·배포 관문에서 계속 대기한다.

완료된 adapter 계약은 [`docs/prompts/phase5-07-ollama-querymodel-adapter.md`](prompts/phase5-07-ollama-querymodel-adapter.md)에, 합성 live smoke의 수행 지침은 [`docs/prompts/phase5-08-ollama-adapter-live-smoke.md`](prompts/phase5-08-ollama-adapter-live-smoke.md)에 둔다. 모델이 반환한 SQL 등 비 JSON 텍스트는 원문 문자열로 보존해 후속 Query IR 검증 실패로 드러내며, provider 호출 실패와 혼합하지 않는다.

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

### ADR-017: SQL fingerprint와 Query IR 후보 분류는 보수적으로 분리

- 날짜: 2026-09-11
- 상태: 채택
- 결정: PostgreSQL AST를 동일 dialect로 직렬화한 canonical hash와 literal type만 남긴 shape hash를 구분한다. 원문 SQL은 변경하지 않으며 두 hash 모두 실행 동등성의 근거로 사용하지 않는다. 현재 Query IR 호환성은 자동 변환 성공이 아닌 네 단계 후보 분류로 기록하고 모호한 projection, LIKE, literal 처리는 수동 검토로 보낸다.
- 이유: formatting 차이를 제거하면서 identifier·literal·조건 순서의 의미를 보존하고, 현재 IR이 표현하지 못하는 구문을 조용히 손실하지 않으며, 후속 변환기의 자동 처리 범위와 관리자 검토량을 실측하기 위해서다.

### ADR-018: Legacy SQL 변환은 승인 source profile과 실행 정책 provenance를 분리

- 날짜: 2026-09-11
- 상태: 채택
- 결정: 학습 데이터 변환에는 운영 Published Mapping과 별도인 versioned·승인된 source schema profile만 사용한다. analyzer의 `CURRENT_IR_CANDIDATE`만 자동 변환하고 source intent와 생성 Query IR signature가 일치해야 성공으로 기록한다. 원본에 LIMIT이 없을 때 적용한 안전 기본값은 exact 변환과 구분하며, 질문별 SQL conflict는 자동 학습 artifact에서 차단한다. 공개 report에는 원문과 Query IR value를 넣지 않고 private artifact는 gitignore된 전용 경로에 opt-in으로만 쓴다.
- 이유: 특정 학교의 물리 컬럼을 이름 유사도로 재추측하거나 기존 오류를 자동 보정하면 의미 손실이 학습 데이터에 섞인다. source 의미, 실행 안전 정책, 운영 mapping lifecycle과 개인정보 경계를 분리해야 변환 성공률과 사람 검토량을 논문에서 재현 가능하게 측정할 수 있다.

### ADR-019: 실제 식별값은 field별 HMAC token으로 가명화하고 불확실한 문장은 격리

- 날짜: 2026-09-12
- 상태: 채택
- 결정: 승인된 semantic field 정책과 외부 binary secret key를 사용해 NFC 값에 domain-separated HMAC-SHA-256을 적용한다. 동일 surface의 namespace 충돌, 겹침, 질문·instruction·IR 대응 불일치 또는 알려진 원본 값 잔존은 record 전체를 review로 보낸다. 원본 line/hash/group id는 공개 가명 artifact에 복사하지 않고 keyed lineage로 보존하며 private 생성물은 manifest-last bundle로 저장한다. `Course.category`와 `CourseOffering.section`은 초기 보존하되 공개 전 재검토한다.
- 이유: 단순 hash나 부분 치환은 사전 대입·질문/정답 불일치·원본 데이터 연결 위험이 있다. 보수적 실패와 keyed lineage는 재현성을 유지하면서 식별값 노출을 줄이지만, filter에 없는 고유명사나 외부 데이터에 의한 재식별까지 제거하지는 못한다.

### ADR-020: Synthetic gold는 seed 기반 독립 정답과 사람 승인 상태를 분리

- 날짜: 2026-09-13
- 상태: 채택
- 결정: Phase 2 평가 세트는 `FixtureSeed v1`, ontology 내용, Query IR JSON 계약 fingerprint에 고정한다. 각 한국어 질문에 gold Query IR, 사람이 선택한 개설강좌 key, typed semantic row를 기록하고 seed-only 해석기와 세 물리 스키마 실행으로 각각 검증한다. 결과는 행 순서가 아닌 중복을 보존한 multiset으로 비교한다. 자동 검증 성공과 독립 사람의 의미 승인은 별도 상태이며 현재 모든 사례는 `NEEDS_HUMAN_REVIEW`다.
- 이유: compiler 실행 결과를 정답으로 복제하면 같은 구현 오류를 놓칠 수 있고, 6건 synthetic fixture에서 테스트 통과를 실제 대학 일반화나 최종 사람 승인으로 오인하기 쉽다. 정답 근거·한계·검토 상태를 분리해야 후속 모델 비교 수치가 해석 가능하다.

### ADR-021: 경량 runner는 전체 attempt 분모와 oracle 표기를 고정

- 날짜: 2026-09-13
- 상태: 채택
- 결정: 모든 사례×스키마 attempt를 분모에 포함하고 구조적 IR, semantic 정답, 실행 성공, 비교 불가와 실패 단계를 분리한다. row 순서는 무시하되 typed 값과 중복 행 수는 보존한다. gold를 응답으로 제공하는 FakeModel 실행은 `oracle_smoke`로만 표시하며, gold 사람 승인 전에는 연구 성능 지표로 사용하지 않는다. report core에서 SQL·응답 원문·행·측정 시간·임시 경로를 제외한다.
- 이유: 성공 사례만 집계하거나 oracle 재생을 모델 성능으로 오인하면 실제 일반화 능력이 과대평가된다. 결정적·비공개 기본 report가 있어야 이후 동일 조건의 모델 응답을 안전하게 비교할 수 있다.

### ADR-022: 사람 검증은 위험별 시점과 사용 관문으로 분리

- 날짜: 2026-09-14
- 상태: 채택
- 결정: 도메인 의미·데이터 사용 조건·독립 평가 스키마 확보 가능성은 조기에 소수 사례로 확인한다. 40개 gold의 정식 독립 승인과 실제 DB mapping·보안·사용자 검증은 각각 모델 성능 확정, 운영 질의, 외부 공개 전에 완료한다. 자동 테스트와 AI의 자체 검토를 사람 승인으로 대체하지 않는다. 세부 항목과 기록 형식은 `docs/human-validation-checkpoints.md`에 둔다.
- 이유: 모든 검토를 개발 시작 전 요구하면 진행이 불필요하게 막히지만, 모든 검토를 논문 직전으로 미루면 잘못된 의미·자료 사용·일반화 주장을 늦게 발견해 재작업과 위험이 커진다.

### ADR-023: RecordedModel은 exact context-bound JSON을 보수적으로 재생

- 날짜: 2026-09-15
- 상태: 채택
- 결정: recording은 request ID·database ID·질문 SHA-256·전체 runtime mapping SHA-256에 묶고, 성공 응답의 JSON 값은 수정하지 않고 재생한다. 누락·불일치·기록된 실패는 gold fallback 없이 통제된 오류로 남긴다. loader는 엄격한 version·type·중복·유한값 검증을 수행하며 실제 recording은 private artifact로 취급한다.
- 이유: 다른 질문이나 mapping에서 과거 응답을 재사용하면 평가 결과가 오염되고, 잘못된 Query IR을 자동 보정하면 실제 모델 실패를 숨긴다. 원문 비노출 실패와 exact replay가 있어야 노트북에서 모델 동작을 안전하게 재현할 수 있다.

### ADR-024: private recording은 불변 generation과 선언형 provenance로 저장

- 날짜: 2026-09-18
- 상태: 채택
- 결정: recording bytes와 canonical provenance를 content-addressed generation에 저장하고 검증 완료 후 manifest를 마지막에 원자 게시한다. provenance는 `DECLARED_UNVERIFIED`로 고정하며 prompt·설정 원문 대신 SHA-256만 보존한다. loader는 bundle 경계, Git 비추적 private 경로, symlink·junction·hardlink와 모든 hash·count·version을 다시 검증한다.
- Phase 5-09 보충: Ollama prompt hash는 요청 값을 채우기 전의 versioned template bytes, generation config hash는 실제 `stream`·`options`·timeout의 canonical JSON bytes를 사용한다. model tag와 선언 digest는 별도 provenance field이며 base URL은 포함하지 않는다.
- 이유: 실제 모델 응답을 private artifact로 유지하면서도 부분 저장과 변조·경로 탈출을 탐지하고 동일 입력을 재현해야 한다. 선언 metadata를 출처 증명으로 과장하지 않아야 이후 평가 결과의 신뢰 범위를 명확히 설명할 수 있다.

### ADR-025: live capture는 private target 파일과 명시적 실행 flag로 한정

- 날짜: 2026-09-19
- 상태: 채택
- 결정: `capture-targets-v1` 입력은 `.schema-adaptive` 아래 Git 비추적 파일에서만 읽는다. caller가 정확한 모델 tag·URL·timeout·Ollama version·선택적 digest·run ID·출력 경로를 제공하고 `--live`를 명시해야 호출한다. 전체 batch·provenance·저장 경로를 첫 호출 전에 검사하고 기존 bundle의 기본 덮어쓰기를 금지한다. 실행 flag는 E3·L5 사람 승인이 아니며 이번 단계의 검증은 합성 transport로만 수행한다.
- 이유: 실제 질문·응답의 우발적 전송·저장을 막고, 모델 추측이나 부분 batch 저장이 recording의 비교 조건을 흐리지 않게 한다. 입력과 결과의 private 경계를 명확히 유지한다.
- Phase 5-11 보충: `--check`와 `--live`는 같은 preflight 결과를 사용한다. `--check`는 호출·저장 없이 계약만 확인하며 E3·L5 사람 승인을 대체하지 않는다.
- Phase 5-12 범위 명확화: Git 비추적 private 경로에 완전 합성 질문·ontology·mapping으로 수행하는 로컬 수집은 E3·L5 팀 결정을 기다리는 동안 개발 검증으로 허용한다. 실제 CNU 자료의 추가 사용·공유(E3), 실제 데이터 배포·외부 시연(L5)은 별도 관문이다. 합성 응답 recording은 성능·공개 승인 증거가 아니다.

### ADR-026: QueryIR prompt 버전은 명시적으로 선택하고 v1을 기본값으로 유지

- 날짜: 2026-09-19
- 상태: 채택
- 결정: `query-ir-v1` template와 기본 선택을 보존한다. `query-ir-v2`는 명시적 선택일 때만 사용하며 선택한 template bytes로 prompt hash를 계산한다. 실제 동일한 전송 설정의 generation config hash는 버전과 무관하게 같다. V2의 합성 개발 세트 단일 비교가 악화했으므로 기본값을 바꾸지 않는다.
- 이유: prompt 변형의 provenance를 분리하고, 실패한 탐색 결과를 제품 경로에 자동 적용하지 않기 위해서다. 실제 모델 성능 확정에는 승인 gold와 독립 검토가 필요하다.

### ADR-027: paired 비교 계약은 benchmark·seed·fixture fingerprint까지 확장

- 날짜: 2026-09-19
- 상태: 채택
- 결정: `paired_prompt_summary()`의 비교 계약을 gold/ontology/QueryIR fingerprint에서 benchmark_id·benchmark_version·seed_fingerprint·fixture metadata fingerprint·variant 순서까지 확장한다. model_run_id는 의도적으로 검사하지 않는다 (v1/v2는 서로 다른 run ID를 사용한다).
- 이유: 두 결과가 같은 fixture·benchmark에서 유래했는지를 fingerprint로 독립 검증하지 않으면, 서로 다른 조건의 결과가 우연히 같은 크기(120건)로 묶여 비교될 수 있다. 원문 비노출 error code로 거부한다.
- Phase 5-15 보충: v2 `UNKNOWN_FIELD` 증가(21→72)의 원인 분류를 고정 범주·집계로 재확인하고, 독립 합성 평가 세트의 설계 기준을 별도 문서에 고정한다.
- Phase 5-15 보완(2026-09-20): `paired_prompt_summary()`는 strict-loaded bundle 2건을 필수 입력으로 요구한다. 비교 recording은 각 bundle 자신의 `recording`에서만 가져오며 별도 recording 인자를 받지 않으므로, request ID만 맞추고 fingerprint·payload를 바꾼 recording으로 바꿔치기할 수 없다. 각 실행 안에서 report `config.model_run_id`·manifest·bundle recording의 일치, manifest provenance의 provider kind/id/version·정확한 model tag(`model_id`; report `model_label` 사용 금지)·model digest·generation config hash 비교, case×variant별 `question_sha256`·`mapping_sha256`·database 일치, report↔recording request ID 집합의 정확한 대응을 검증한다. prompt template hash 동일성은 강제하지 않는다(v1/v2가 다르다). 증명 입력이 없으면 고정 code(`PAIRED_PROVENANCE_MISSING` 등)로 거부하고 성공 집계를 반환하지 않는다. 종전의 누락 집계 허용 permissive 경로는 제거했다. 한계: `LoadedRecordingBundle` 타입을 받는 것만으로 strict load를 증명하지 않으며, 호출자는 `load_recording_bundle()` 검증 결과를 전달해야 한다. provenance는 `DECLARED_UNVERIFIED`이며 모델 weight를 독립 증명하지 않는다. report는 bundle manifest에 암호학적으로 결합되지 않으며, 결합은 run ID·request ID 집합·fingerprint 일치에 한정된다. raw response payload는 비교하지 않으며 결과 비교는 report에서 가져온다. 불일치 오류에는 원문·경로를 포함하지 않는다.
- 재검증(2026-09-20): private bundle 3개 모두 strict load한 읽기 전용 재검증에서 v2 120 attempts 중 `UNKNOWN_FIELD` 72건을 `audit_summary()`로 재집계했다. 범주는 `ENTITY_PREFIX_MISMATCH` 46, `OUTSIDE_ONTOLOGY` 6, `PHYSICAL_COLUMN_FORM` 5, `OTHER` 15, `CASING_NOTATION` 0, `UNCLASSIFIABLE` 0이며 합계 72건이다. 중복 참조 27, 혼합 범주 attempt 50이다. 실제 bundle 기반 paired 비교도 v1 21건·v2 72건으로 확인했다. 이전 Muse Spark 실행 환경에서는 generation 디렉터리 읽기 `PermissionError`로 strict load가 차단되어 미검증으로 기록한 바 있다. 이 수치는 미승인 합성 개발 세트의 진단 집계이며 모델 성능 확정 수치가 아니다.

### ADR-028: 독립 평가 세트는 question holdout 초안으로 분리하고 동결 전으로 유지

- 날짜: 2026-09-20
- 상태: 채택
- 결정: 개발 세트(`fixture-v1-common-questions`, P2-001~040)와 분리된 새 benchmark(`fixture-v1-independent-questions`, P2-041~070) 30건을 초안으로 작성한다. 같은 `FixtureSeed v1`·ontology를 재사용하므로 question holdout이며 unseen school이 아니다. `check_set_isolation()`으로 benchmark ID 분리·case ID·정규화 질문 문장·paraphrase family·`_intent_identity()` 기준 intent 겹침을 고정 code로 거부한다. 자동 검사는 의미 유사 문장을 모두 잡지 못하므로 사람 검토·동결 전 상태를 유지한다.
- 이유: 개발 세트에서 prompt를 선택한 뒤 같은 세트로 반복 비교하면 과적합 선택이 일반화 성능으로 오인된다. 질문 격리를 자동 검사로 강제하되, 의미 검토와 동결은 사람 관문(L1·E2)으로 남겨야 후속 수치가 해석 가능하다.

### ADR-029: repair 재시도는 검증 실패 code·stage 쌍에만 최대 1회로 제한

- 날짜: 2026-09-20
- 상태: 채택
- 결정: `BoundedRepairCoordinator`는 기존 `ExecutionHarness`를 그대로 재사용하며 최초 1회 + repair 1회만 실행한다. `MODEL_INVALID_OUTPUT`은 `IR_GENERATED`에서만, `UNKNOWN_ENTITY`·`UNKNOWN_FIELD`·`INVALID_OPERATOR`·`INVALID_VALUE_TYPE`은 `IR_VALIDATED`에서만 repair하며 code와 stage를 함께 검사한다. 그 외 실패·provider 예외·취소는 재시도하지 않는다. 최초 실행 전에 request·ontology·mapping의 깊은 복사 스냅샷을 고정하고, 최초 모델·repair provider·두 번째 실행에는 스냅샷의 복사본만 전달하므로 어느 쪽의 변이도 다른 시도나 호출자 객체에 영향을 주지 않는다. 최초·repair 모델은 격리 래퍼(`_IsolatedModel`)를 거쳐 각 harness 실행의 검증·컴파일·실행 컨텍스트와 분리된 복사본만 받으므로, 모델이 입력을 변이해도 harness가 검증하는 컨텍스트는 원본 스냅샷 그대로이다. repair provider에는 스냅샷 복사본(request·ontology·mapping)과 고정 실패 code만 전달하고, 후보는 전체 안전 경로를 다시 통과해야 한다. 결과 요약에는 SQL·행·원문을 복사하지 않으며 repair 성공을 최초 모델 정확도로 세지 않는다.
- 이유: 무제한 재시도는 실패 원인을 가리고 안전 검증을 우회할 유인을 만든다. code·stage 쌍으로 재시도 범위를 고정하면 오답 교정 실험이 재현 가능하고, critique·사용자 확인·관리자 검토 큐 등 Phase 6 나머지와 분리된다.

### ADR-030: critique 분류는 성공 여부와 무관하게 고정 code·stage 쌍으로 닫음

- 날짜: 2026-09-20
- 상태: 채택
- 결정: `critique_repair_summary()`는 `RepairSummary`만 입력받는 순수 함수로 두고 `critique-disposition-v1`을 고정한다. 성공은 최초·repair 후·0행을 모두 `EXECUTED_UNVERIFIED`로 분류하고 `repair_attempted`·`recovered_after_repair`로 최초 성공·repair 회복·repair 후 실패를 구분하며 정답·만족·정확도를 추론하지 않는다. `repaired`라는 단일 flag는 repair provider 실패나 repair 후 실패를 회복으로 오독하게 하므로 쓰지 않는다. 실패는 `HarnessErrorCode` 28종과 `REPAIR_PROVIDER_ERROR`(stage 없음)의 허용 (code, stage) 조합을 `REQUEST_INVALID`·`MODEL_REVIEW`·`CONTEXT_REVIEW`·`SAFETY_BLOCKED`·`EXECUTION_FAILURE`·`INTERNAL_REVIEW` 중 하나로 명시 분류한다. 위조 stage·알 수 없는 code·모순된 status·code·repair 횟수는 고정 code(`CRITIQUE_INVALID_SUMMARY`)로 거부하며 원문·질문·SQL·행·경로를 반영·기록하지 않는다. repair가 없으면 최종 결과는 초기 결과와 같아야 하고, repair가 표시된 요약은 초기 실패가 Phase 6-01 재시도 허용 쌍(`is_repairable` 기준)일 때만 받는다. 입력·정책·context·실행·내부 실패 뒤의 repair 표시는 위조로 거부한다. 자동 사용자 확인·관리자 queue 등록·재시도 지시·성능 report 경로를 만들지 않고, `INVALID_REQUEST`를 모호성으로 취급하지 않으며 ambiguity 신호와 확인 계약은 다음 단계로 남긴다.
- 이유: code만 보고 분류하면 stage 위조나 새 code가 조용히 성공·사용자 질문으로 바뀌고, 성공 분류가 정확도 증거로 오용될 수 있다. repair 없는 요약의 최종·초기 불일치나 재시도 불가 실패 뒤의 repair 표시는 coordinator가 만들 수 없는 불가능한 요약이므로 받아주면 위조 입력이 정상 분류로 둔갑한다. 고정 분류표와 fail-closed 거부가 있어야 합성 후속 처리 제안이 재현 가능하고, 전체 loop·모호 질문 확인·관리자 검토 큐와 분리된다.

### ADR-031: clarification 확인은 명시적 신호의 stateless 순수 계약으로 분리

- 날짜: 2026-09-20
- 상태: 채택
- 결정: `clarification-gate-v2`은 caller가 제공한 명시적 `AmbiguitySignal`(서로 다른 option ID·semantic 식별자의 2개 이상 후보, 물리 이름·SQL·행·자유 답변 없음)만 `prepare`로 pending에 묶는다. 신호가 없으면 pending을 만들거나 호출을 막지 않는다. pending·결과에는 원문·원시 식별자·물리 mapping·SQL·행·경로·secret을 저장하지 않고 질문 SHA-256, request·database 식별자의 domain-separated SHA-256, ontology/mapping 결정적 fingerprint, 허용 option만 저장한다(식별자 문자열이 임의 경로·secret일 수 있으므로 원문 보관 금지). `resolve`는 원본 caller 신호를 다시 요구하며 정렬된 option ID·semantic_ref 전체가 pending과 정확히 일치할 때만 진행한다. 이는 신뢰 caller 신호와의 일관성 확인이며 서명·세션 저장소 같은 암호학적 진위 증명이 아니다. 그 외 drift는 `CLARIFICATION_CONTEXT_MISMATCH`로, 미지·빈·다중·자유 텍스트 선택은 `CLARIFICATION_INVALID_CHOICE`로, 신호·형태 위반은 `CLARIFICATION_INVALID_SIGNAL`로 fail-closed한다. 정확히 한 개의 제시 option ID만 `CONFIRMED`, 명시적 취소만 `CANCELLED`이며 기본 선택을 고르지 않는다. 모든 공개 경계는 인스턴스를 plain JSON으로 직렬화한 뒤 재검증하므로 `model_copy(update=...)`·`model_construct` 위조가 고정 code로 거부된다. Pydantic `ValidationError` 원문은 외부에 노출하지 않고 고정 code만 전달한다. 질문 변경·QueryIR 보정·생성, 하네스·repair 자동 실행, UI·관리자 큐·저장소·DB·네트워크·Ollama·legacy 변경, `CritiqueResult`·오류 taxonomy 변경, 기존 실패의 자동 승격을 하지 않는다. pending은 stateless bearer 값이며 한 번만 쓰는 보증을 주장하지 않는다(재사용 가능, 만료·폐기 없음).
- 이유: pending만 믿고 확인하면 호출자가 모르는 후보 추가·삭제·교체가 `CONFIRMED`로 둔갑하고, 원시 식별자 보관과 인스턴스 재검증 생략은 경로·secret 노출과 위조 우회를 만든다. 자동 감지나 화면 없이도 신호 일관성·비노출·위조 거부를 재현 가능하게 고정해야 전체 loop·관리자 큐와 분리된 합성 검증이 가능하고, 모호성 추측이 기존 실패 분류를 오염시키지 않기 때문이다.
- 독립 리뷰 보완(2026-09-20): 위 세 결함(pending 단독 신뢰, 원시 식별자 보관, 인스턴스 재검증 생략)을 수정하고 `resolve`에 caller 신호 재제출, 식별자 fingerprint 저장, dump-then-validate 경계를 적용. 합성 테스트 35개로 검증.
- 후속 결함 수정(2026-09-20): `clarification-gate-v2`는 caller option ID도 domain-separated SHA-256으로 변환해 pending·결과에 원문을 넣지 않는다. `resolve`는 caller의 원래 ID를 입력받아 fingerprint로 비교하고 결과에는 fingerprint만 돌려준다. v1 pending은 v2에서 거부한다. low-entropy ID의 해시 추측까지 막는 인증 수단은 아니며, 실제 UI·저장소 연계는 별도 설계한다. `critique_repair_summary()`도 위조된 `RepairSummary` 인스턴스를 경고 비노출 재검증하고 2회 repair 주장·잘못된 code 타입을 고정 오류로 거부한다.

### ADR-032: 운영 실패 검토 후보는 내부 critique 분류의 메모리 등록으로만 다룸

- 날짜: 2026-09-20
- 상태: 채택
- 결정: `InMemoryReviewQueue`는 원본 `RepairSummary`와 명시적 run/request ID만 입력받고 내부에서 `critique_repair_summary()`를 호출해 분류한다. caller의 `CritiqueResult` 주입·disposition 조작 API를 두지 않는다. `EXECUTED_UNVERIFIED`·`REQUEST_INVALID`은 `NOT_ELIGIBLE` 미등록, `MODEL_REVIEW`·`CONTEXT_REVIEW`·`SAFETY_BLOCKED`·`EXECUTION_FAILURE`·`INTERNAL_REVIEW`만 등록 후보로 둔다. 항목은 `review-queue-v1` version, run/request ID의 domain-separated SHA-256(canonical UTF-8), 최종 고정 code·stage·disposition, repair 시도/회복 flag, 안정 idempotency key만 저장한다. 같은 run/request의 같은 결과는 `ALREADY_PRESENT`, 다른 결과는 고정 `REVIEW_QUEUE_CONFLICT`로 거부하며 덮어쓰지 않는다. ID 오류는 `REVIEW_QUEUE_INVALID_INPUT`로 등록 전 차단하고 critique 실패는 `CRITIQUE_INVALID_SUMMARY` 그대로 두며 큐를 바꾸지 않는다. coordinator·critique·clarification·harness 동작을 바꾸지 않고, disk/DB/network/Ollama/private 접근 없이 메모리 전용으로 둔다.
- 이유: caller가 분류 결과를 직접 넣으면 정상 분류로 둔갑한 위조 disposition이 검토 큐에 들어가고, 원문·raw ID 보관이 경로·secret 노출을 만들며, 같은 키의 다른 결과 덮어쓰기가 실패 원인을 지운다. 내부 분류·고정 메타데이터·충돌 거부·방어적 복사로 합성 후속 처리 제안을 재현 가능하게 고정해야 전체 loop·영속 저장·관리자 승인 흐름과 분리된 검증이 가능하기 때문이다.

### ADR-033: 합성 검토 큐 snapshot은 고정 메타데이터 단일 파일과 공유 private 경로 검증으로만 다룸

- 날짜: 2026-09-20
- 상태: 채택
- 결정: `review-queue-snapshot-v1`은 `InMemoryReviewQueue.list_entries()`의 고정 필드만 명시적 `.schema-adaptive` 아래 단일 JSON 파일에 저장한다. payload는 schema/storage-tool/queue version, 항목 수, 순서 있는 항목을 담고 `payload_sha256`을 제외한 canonical JSON bytes(UTF-8, `sort_keys=True`, compact separators, `ensure_ascii=False`, `allow_nan=False`)의 SHA-256을 싣는다. 내용 hash는 변조 감지일 뿐 작성자 인증이 아니다. 저장 전 private 경로·Git ignored/tracked·symlink/junction/reparse·hardlink 검사는 기존 recording bundle의 동일 validator(`preflight_private_snapshot_path`·`read_private_snapshot_bytes` 공개 wrapper 경유)를 재사용하며 별도의 약한 경로 검사 복제본을 두지 않는다. no-overwrite가 기본값이며 기존 snapshot은 바이트 보존 뒤 고정 오류로 거부한다. 임시 파일 fsync 후 원자 게시하고 실패 시 임시 파일을 정리한다. `overwrite`·in-place update·실제 운영 동시성/잠금은 구현하지 않는다. strict loader는 duplicate key·비유한 값·추가/누락 필드·count/hash 불일치·중복 run/request 조합·중복 idempotency key·미지 code·stage·disposition·version을 고정 code로 거부하고 파일 안의 경로를 신뢰하지 않으며 내부 queue를 자동 수정하지 않는다.
- 이유: snapshot마다 새 경로 검사를 복제하면 약한 검사가 실제 private 경계로 들어가 tracked·symlink·hardlink 우회를 놓칠 수 있다. 단일 공유 validator·단일 파일·no-overwrite·canonical hash·fail-closed strict load로 합성 영속 경계를 재현 가능하게 고정해야 실제 운영 큐 저장·관리자 UI·승인 흐름과 분리된 검증이 가능하기 때문이다.
- 독립 리뷰 보완(2026-09-20): code·stage·disposition을 각각 검증하면 알려진 code와 어울리는 eligible disposition을 바꿔 hash·idempotency key까지 다시 서명한 위조 snapshot이 strict load를 통과한다. 저장 항목의 (code, stage)가 단일 critique 분류표(`disposition_for`)에서 저장된 disposition과 정확히 일치해야 하며, `INVALID_REQUEST`의 운영 실패 둔갑, stage가 있는 `REPAIR_PROVIDER_ERROR`, 성공에서만 가능한 `recovered_after_repair=True`를 고정 `INVALID_CONTRACT`로 거부한다. 같은 의미 검증을 저장 전에도 적용해 위조 항목이 파일에 닿기 전에 차단한다. 분류표는 critique 모듈이 소유하고 snapshot은 공개 validator로만 재사용하므로 taxonomy 복제본을 두지 않는다. private 경로는 부모 생성 후와 게시 직전에 다시 검사해 redirect-swap 노출을 좁히지만, check-then-use race 자체를 보장하지는 않는다.

### ADR-034: 합성 agent workflow는 최종 결과 보존과 실행 후 queue 실패를 분리해 닫음

- 날짜: 2026-09-21
- 상태: 채택
- 결정: `BoundedRepairCoordinator.run_detailed()`는 재실행 없이 최종 `HarnessResult`를 `RepairSummary`와 함께 반환하는 frozen `DetailedRepairOutcome`을 둔다. 기존 `run()`은 같은 예산으로 새 API에 위임한다. terminal 초기 실패·repair 후 성공/실패는 해당 최종 결과를 그대로 보존하고, provider 예외의 `REPAIR_PROVIDER_ERROR`는 가짜 code/result 없이 `final_result=None`으로 부재를 명시한다. `DetailedRepairOutcome`은 dump 재검증한 nested summary/result의 최종 status/stage/code/succeeded 일치까지 강제하며(provider-error iff 결과 부재) 위조 nested·`model_copy` 변조·중복 검증을 고정 오류로 거부한다. `AgentWorkflowCoordinator`는 명시적 `model_run_id`·request·ontology·Published mapping·model·repair provider·주입 queue로 상세 실행 → 내부 critique → queue 등록만 수행하고, `CritiqueResult`/disposition 주입 API·snapshot write·retry·clarification·QueryIR 보정·mapping 변경을 두지 않는다. `AgentWorkflowResult`는 단일 출처(`critique_repair_summary`, `DetailedRepairOutcome`)로 critique 일치·최종 결과 일치·`NOT_ELIGIBLE`(성공 또는 `REQUEST_INVALID`, entry 없음)·적격 등록(entry의 code/stage/disposition/flag 일치)의 fail-closed 불변식을 강제하고 위조 nested를 dump 재검증한다. 위조·변이된 `HarnessRequest` 인스턴스는 평문 정규화 후 기존 `INVALID_REQUEST`·`NOT_ELIGIBLE`·무호출 경로를 따르며 `INTERNAL_ERROR`로 돌리지 않는다. 실행 전 ID·타입 검증을 단일 공개 helper(`validate_queue_identifier`)로 재사용하며, queue conflict·내부 불일치는 고정 code로 fail closed하고 기존 entry를 보존한다. 실행 후 queue 등록 실패 시 읽기 전용 호출은 이미 소비되고 queue는 보존되는 잔여 경계를 모듈 docstring에 문서화한다.
- 이유: 최종 결과를 버리면 성공 행·0행 성공과 실패 code/stage의 재현 근거가 사라지고, provider 실패를 가짜 harness 결과로 메우면 `MODEL_REVIEW` 분류가 오염된다. 실행 전 검증을 복제하면 약한 ID 검사가 운영 경계로 들어가고, 실행 후 queue 실패를 조용히 성공으로 돌리면 실패 원인이 지워진다. 결과 보존·단일 ID 출처·실행 후 실패 분리를 고정해야 합성 전체 loop를 실제 운영 영속 큐·승인 흐름과 분리해 검증할 수 있기 때문이다.

### ADR-035: 합성 영속 큐는 명시적 source/next immutable snapshot lifecycle로 제한

- 날짜: 2026-09-21
- 상태: 채택
- 결정: `durable-review-workflow-v1`은 `None` 또는 명시적 current snapshot을 strict load·전체 재검증해 새 메모리 queue로 복원하고 기존 `AgentWorkflowCoordinator`를 정확히 한 번 실행한다. `ENQUEUED`일 때만 caller가 지정한 서로 다른 next private path에 기존 `review-queue-snapshot-v1`을 no-overwrite로 게시하며 `ALREADY_PRESENT`·`NOT_ELIGIBLE`은 쓰지 않는다. source와 기존 target bytes를 덮어쓰지 않고 current pointer·자동 세대 선택·retry·DB·잠금·관리자 상태를 만들지 않는다. 경로·복원 오류는 실행 전 고정 입력 오류, workflow conflict·내부 오류와 게시 오류는 별도 고정 code로 분리한다. snapshot hash/result는 게시 전에 결정적으로 완성해 게시 후 재읽기나 결과 생성 실패가 성공한 write를 실패로 오인하지 않게 한다. hard-link 게시 뒤 temp 제거가 실패하면 새 target link를 롤백한 뒤 storage 오류를 반환한다. 실행 뒤 게시 실패에서는 모델/읽기 전용 실행 side effect가 이미 소비될 수 있으며 check-then-use race를 제거했다고 주장하지 않는다.
- 이유: 기존 단일 snapshot을 덮어쓰면 Phase 6-05 no-overwrite·감사 가능성을 깨고, 자동 current pointer는 원자 전환·동시 writer·복구 정책을 한 단계에 과도하게 묶는다. 명시적 source/next와 변경 시에만 새 snapshot을 쓰는 계약이면 재시작 복원·idempotency·충돌·변조 거부를 합성으로 검증하면서 실제 운영 저장소와 승인 흐름을 분리할 수 있다.

### ADR-036: 합성 ambiguity 감지는 versioned literal ruleset과 전체 drift binding으로 제한

- 날짜: 2026-09-22
- 상태: 채택
- 결정: `ambiguity-detector-v1`은 caller가 제공한 frozen rule set을 dump-then-validate하고 NFKC·casefold·whitespace 정규화한 literal trigger 포함 여부만 검사한다. 0 match는 `None`, 정확히 1개 match만 기존 `clarification-gate-v2`에 전달하며 여러 rule match는 합치지 않고 고정 conflict로 거부한다. option ID는 version과 정규화된 전체 rule/trigger/option 정보를 순서 독립 canonical fingerprint로 묶은 domain-separated SHA-256으로 만들고, resolve 때 동일 입력으로 재감지한다. 따라서 rule set의 의미 있는 drift는 기존 pending과 불일치하고 순서만 다른 동등 입력은 안정적이다. 모델 추론·오류 taxonomy 기반 자동 추정·QueryIR 수정·실행·UI·DB·network는 이 경계에 넣지 않는다.
- 이유: 자유형 의미 추론을 지금 결합하면 오탐과 모델 변동을 사용자 확인 계약에 섞고, matched rule의 option만 pending에 묶으면 trigger나 미매치 rule drift가 같은 선택으로 조용히 확인될 수 있다. literal 규칙과 전체 ruleset binding을 고정하면 합성 단계에서 재현 가능한 감지·확인 흐름과 fail-closed drift 검증을 확보하면서 실제 사용자 UX와 QueryIR 반영을 별도 단계로 남길 수 있다.

### ADR-037: 확인 선택은 별도 constraint-aware 생성 경계에서 재확인하고 QueryIR 준수를 강제

- 날짜: 2026-09-22
- 상태: 채택
- 결정: `clarification-query-constraint-v1`은 기존 `QueryModel`·`ExecutionHarness`를 변경하지 않고 별도 provider protocol과 coordinator로 둔다. caller가 확인 result나 semantic ref를 주입하지 못하게 하고, 기존 detector/gate를 다시 실행해 선택 fingerprint와 정확히 일치하는 entity/field만 최소 fingerprint constraint로 만든다. provider는 방어적 context 복사와 constraint를 한 번만 받고, 반환은 기존 QueryIR validator 후 entity 정확 일치 또는 field select/filter 포함 규칙을 통과해야 한다. known raw boundary 문자열 반사는 고정 invalid-output 오류로 거부한다. 생성 결과는 실행·compile·repair로 자동 전달하지 않는다.
- 이유: 확인 결과를 단순 semantic 문자열로 받으면 caller가 선택을 바꾸거나 stale pending을 우회할 수 있고, prompt에 constraint를 전달하기만 하면 provider가 이를 무시한 QueryIR도 정상 경로로 들어간다. 재확인·명시적 provider 입력·사후 준수 검증을 한 경계로 묶으면 선택의 출처와 적용을 합성으로 검증하면서 기존 실행 계약과 실제 UI를 독립적으로 유지할 수 있다.

### ADR-038: 확인된 QueryIR 실행은 내부 one-shot model과 기존 하네스를 통해서만 수행

- 날짜: 2026-09-22
- 상태: 채택
- 결정: `clarification-execution-adapter-v1`은 Phase 6-09의 재확인·constraint-aware 생성을 먼저 수행하고 검증된 QueryIR 하나를 내부 전용 one-shot `QueryModel`에 담아 기존 `ExecutionHarness`를 정확히 한 번 호출한다. caller의 QueryIR·constraint·model·`HarnessResult`·SQL 주입 API는 두지 않는다. one-shot model은 adapter가 고정한 request/ontology/mapping과 값이 정확히 같을 때만 방어적 QueryIR 복사본을 한 번 반환한다. 기존 request/context/QueryIR 검증, compiler, SQL policy, read-only executor, result 검증을 우회하지 않는다. compile 이후 하네스 실패는 기존 code/stage와 QueryIR를 보존하지만, 이미 통과한 request/context/IR 단계의 실패, QueryIR 또는 request fingerprint 불일치, 성공/오류 상태 모순은 adapter 내부 오류로 fail closed한다. 결과 모델 자체도 같은 nested 불변식을 강제한다. repair·재생성·UI·session/expiry·운영 DB 연결은 포함하지 않는다.
- 이유: 검증된 QueryIR를 executor에 직접 전달하면 기존 하네스의 재검증·compiler·SQL policy·result 검증을 우회하고, caller가 model이나 결과를 주입하게 두면 확인 선택과 실제 실행 사이의 출처 결합이 깨진다. 내부 one-shot bridge와 기존 하네스 재사용은 중복 실행 구현 없이 선택 제약과 읽기 전용 실행을 한 번 연결하며, 실제 사용자 session/API와 운영 provider를 다음 경계로 분리한다.

### ADR-039: clarification session은 hashed bearer token과 원자적 at-most-once dispatch로 제한

- 날짜: 2026-09-26
- 상태: 채택
- 결정: `clarification-session-v1`은 framework-neutral 메모리 coordinator의 `start`와 `submit`만 제공한다. `start`는 감지 context를 snapshot하고 단일 ambiguity에만 기본 32-byte `token_urlsafe` bearer를 발급하며 내부에는 domain-separated token hash와 raw context record를 TTL 동안 저장한다. `submit`은 구조가 유효한 choice와 active token을 lock 아래에서 먼저 제거·terminal tombstone으로 전환한 다음 취소 재확인 또는 Phase 6-10 실행을 호출한다. 따라서 동시 제출과 downstream 실패 후 재실행은 차단하지만 프로세스 crash exactly-once는 주장하지 않는다. 만료 record는 submit과 후속 start/submit 활동에서 lazy 제거한다. 결과는 pending/execution의 request·database·ontology·mapping fingerprint 결합을 자체 검증한다. 실제 HTTP/UI, 인증·인가, rate limit, durable/cross-process 저장, background cleanup은 포함하지 않는다.
- 이유: stateless pending만 전달하면 재사용·만료·동시 중복 실행을 막을 수 없고, 실행 완료 후 token을 소비하면 두 요청이 모두 read side effect에 도달할 수 있다. raw bearer token을 저장하지 않고 downstream 전 원자 소비하면 합성 prototype에서 최소한의 재사용·동시성 경계를 확보하면서 운영 session 저장소와 웹 보안 결정을 별도 단계로 남길 수 있다.

### ADR-040: clarification HTTP는 독립 sub-app과 redacted 고정 응답으로 제한

- 날짜: 2026-09-26
- 상태: 채택
- 결정: `clarification-http-v1`은 legacy app에 자동 mount하지 않는 FastAPI factory로 만들고 정확히 start/submit POST만 노출한다. app 생성 시 하나의 session coordinator, server context loader, provider, read-only executor와 선택적 compiler/policy를 검증·고정한다. caller request는 start의 request ID/database ID/question과 submit의 bearer token/opaque choice/cancel뿐이다. start는 선택 가능한 ambiguity option ID를 반환하고 submit은 성공 columns/JSON rows 또는 실패 fixed code/stage만 반환한다. SQL·QueryIR·constraint·trace·raw ID·fingerprint·내부 message는 HTTP에서 제거한다. 모든 알려진 오류는 단일 code→HTTP status 표를 따르고 미지 예외는 fixed 500으로 닫는다. Phase 6-11 start 결과는 selectable option과 내부 pending의 fingerprint 결합을 자체 검증하도록 보완한다.
- 이유: 기존 legacy app에 바로 연결하면 warmup·auth·실제 DB/model side effect와 합성 계약이 섞이고, raw harness 결과를 반환하면 SQL·trace·식별자와 내부 오류가 사용자 경계로 노출된다. 독립 factory·server-owned context·최소 응답을 먼저 고정하면 HTTP 의미와 redaction을 재현 가능하게 검증하면서 실제 mount·인증·운영 provider 결정을 뒤로 분리할 수 있다.

### ADR-041: 실행 가능한 clarification prototype은 canonical 합성 dependency와 loopback CLI로 제한

- 날짜: 2026-09-26
- 상태: 채택
- 결정: `synthetic-clarification-prototype-v1`은 Phase 6-12 factory에 고정 synthetic database ID, 기존 canonical semantic field의 최소 ontology·Published mapping, versioned literal rule, 선택 constraint만 유효 QueryIR로 바꾸는 결정론 provider, `DEFAULT_SEED` 기반 임시 flat SQLite와 기존 read-only executor를 조립한다. 임시 DB는 app lifecycle과 process exit에 묶어 정리하고 package import는 prototype `app`을 재수출하지 않는다. module CLI는 host를 `127.0.0.1`로 고정하고 제한된 port만 받으며 reload/access log를 끈다. legacy app, `.env`, 실제 provider·PostgreSQL·CNU 자료, 인증·UI를 연결하지 않는다.
- 이유: HTTP factory만으로는 팀원이 실제 start/submit 흐름을 재현하기 어렵지만 운영 dependency를 곧바로 연결하면 합성 계약 검증과 배포 보안 결정이 섞인다. canonical semantic 이름과 기존 read-only 경로를 쓰는 고정 합성 조립은 schema-adaptive 경계를 보존하면서 재현 가능한 backend prototype을 제공하고, loopback-only 진입점은 무인증 prototype의 노출 범위를 제한한다.

### ADR-042: 합성 clarification UI는 별도 route와 strict client state로 격리

- 날짜: 2026-09-26
- 상태: 채택
- 결정: Phase 6-14 UI는 기존 legacy `App`을 교체하지 않고 `/adaptive-prototype`에서만 렌더한다. 같은 origin의 `/api/adaptive` proxy로 Phase 6-13 loopback backend만 호출하며 `/api/v1` proxy와 auth/storage를 재사용하지 않는다. 모든 response는 exact version·상태·필드·option/token 형식·fixed execution code/stage·row/column 일치를 runtime에서 검사한다. session token은 pending 상태의 메모리에만 두고 유효 submit/cancel 직전 제거하며 복원하지 않는다. pending 중 새 start/reset은 막고 cancel로 소비하게 하며, reset/unmount는 진행 중 fetch를 abort하고 operation identity가 stale response 반영을 막는다. unknown semantic ref·error code·column 이름은 원문 대신 일반 label/message/header로 표시한다.
- 이유: legacy 단일 chat request에 다단계 clarification을 억지로 섞으면 기존 기준선과 token 수명 경계가 깨지고, TypeScript 타입만 믿으면 변조·drift response가 UI state를 오염시킬 수 있다. 별도 route·proxy·strict parser와 메모리 전용 token 상태는 합성 browser prototype을 작은 경계로 검증하면서 실제 auth·배포·운영 UI 결정을 뒤로 남긴다.

### ADR-043: 팀 공유 준비 완료는 합성 loopback browser 증거로 한정

- 날짜: 2026-09-27
- 상태: 채택
- 결정: Phase 6-15의 내부 팀 공유 준비 기준은 단일 loopback 실행 명령, 실제 Chrome DOM·network smoke의 두 option·두 결과 분기·cancel·no-match·terminal 재제출 차단·legacy route 유지, 그리고 backend 연결 실패의 redacted 일반 오류까지다. HTTP 비-2xx가 비-JSON이면 raw body를 반사하거나 정상 response 계약 오류로 표시하지 않고 code 없는 일반 server 오류로 닫는다. browser smoke와 기존 자동 token 재사용 `409` 검증은 서로 보완 증거로 기록한다. 이 완료 표시는 합성 fixture·무인증·비영속 session의 내부 논의용 상태에만 적용하며 L4, 실제 auth/provider/PostgreSQL/CNU 데이터, 배포·외부 시연을 승인하지 않는다.
- 이유: 팀원이 실행 가능한 통합 흐름과 실패 표현을 직접 확인해야 prototype 논의가 가능하지만, 기계적 browser 통과만으로 사용자 이해·실데이터 안전·운영 준비를 주장하면 검증 범위를 넘는다. 성공·실패 재현 기준과 사람 관문을 분리하면 prototype은 지금 공유하면서 후속 설계 결정을 과장 없이 받을 수 있다.

### ADR-044: 활성 DB는 session 범위 선택이며 registry·Published Mapping이 실행 권한을 결정

- 날짜: 2026-09-27
- 상태: 채택
- 결정: 메인 화면의 활성 DB는 backend process 전역 mutable pointer로 저장하지 않고 browser `sessionStorage`에 둔다. legacy 기준선은 기존 `/api/v1/courses`를 그대로 사용하고, 합성 경로는 서버 소유 strict registry에 등록된 flat·legacy-schema SQLite만 허용한다. registry 응답으로 저장값을 재검증한 뒤 canonical course catalog와 clarification start에 같은 DB ID를 명시적으로 전달한다. catalog는 caller SQL·경로·mapping을 받지 않고 Published 상태의 서버 mapping, parameterized compiler, SQL policy, read-only executor를 통과한다. schema fingerprint drift, 미등록·중복 ID, 응답 계약 불일치는 redacted 오류로 닫고 DB 변경 시 이전 DB에 종속된 UI 상태와 요청을 폐기한다. 파일 DB 등록·PostgreSQL·전역 current DB·publish UI는 포함하지 않는다.
- 이유: 서버 전역 선택은 여러 사용자 요청을 교차 오염시키고, main catalog와 Text-to-SQL이 서로 다른 DB를 보면 prototype의 핵심 주장을 검증할 수 없다. session 범위의 명시적 DB ID와 서버 registry·Published Mapping gate를 결합하면 legacy 기준선을 유지하면서 두 물리 schema가 같은 canonical 화면과 질의 결과를 제공함을 안전하게 보여줄 수 있다.

### ADR-045: private SQLite 등록은 immutable quarantine이며 publish와 실행 권한을 분리

- 날짜: 2026-09-27
- 상태: 채택
- 결정: 사용자 제공 SQLite는 Git ignored `.schema-adaptive/registered-databases` 아래 database ID별 immutable registration으로만 저장한다. source는 한 번 연 fd에서 32 MiB 상한과 hash를 강제하고 standalone header·sidecar/WAL·redirect/hardlink를 검사한다. private copy는 read-only immutable SQLite 검사, sample 없는 schema scan, 기존 rule-based aligner만 통과하며 `registration.json`을 마지막 commit marker로 게시한다. loader는 exact 네 파일, bounded size/hash, strict contract, fingerprint/ontology 일치와 재검사를 수행한다. 성공 상태는 항상 `PENDING_REVIEW`이고 기존 Phase 6-16 registry나 Published Mapping을 자동 변경하지 않는다.
- 이유: 파일을 받은 즉시 실행 대상으로 취급하면 경로 우회·불완전 WAL snapshot·민감 sample 유출·동시 overwrite뿐 아니라 미검토 mapping으로 잘못된 SQL을 실행할 수 있다. 저장·검사·proposal 생성을 quarantine 단계로 닫고 사람 검토·명시적 publish를 별도 권한 상승으로 두면 파일 등록 기능을 개발하면서도 기존 합성 실행 경계와 legacy 기준선을 보존할 수 있다.

### ADR-046: loopback upload는 bounded raw body이며 성공 응답도 strict quarantine reload에서만 만든다

- 날짜: 2026-09-27
- 상태: 채택
- 결정: synthetic prototype의 SQLite upload는 multipart 대신 `application/x-sqlite3` raw body 하나와 strict `database_id`·`label` query만 받는다. HTTP 경계가 `Content-Length`와 실제 stream 모두에 32 MiB 상한을 적용하고 exclusive 임시 regular file을 성공·실패·취소 뒤 정리한다. 그 파일은 Phase 6-17 `register_database()`에 전달하고, 공개 성공 응답은 `load_registration()` strict reload가 확인한 `PENDING_REVIEW` manifest·sample 없는 catalog·mapping proposal 요약에서만 만든다. 고정 오류 code 외 내부 경로·message를 노출하지 않으며 upload 성공은 active DB, session storage, Phase 6-16 registry·catalog·clarification을 변경하지 않는다.
- 이유: multipart parser가 endpoint 전에 큰 body를 spool하거나 UI 성공 상태가 실행 권한으로 오인되면 Phase 6-17의 크기 제한과 quarantine 분리가 약해진다. bounded raw stream, strict reload 기반 DTO, registry 불변을 함께 고정하면 브라우저 파일 선택을 제공하면서도 registration과 publish/execute 권한 상승을 분리할 수 있다.

### ADR-047: private DB 실행 권한은 activation manifest의 원자 commit으로만 상승

- 날짜: 2026-09-27
- 상태: 채택
- 결정: Phase 6-19 review HTTP는 server-owned Phase 6-17 registration과 proposal만 다시 읽어 `INIT/APPROVE/REMAP/PUBLISH`를 수행하며 caller의 draft·mapping·version·path·SQL을 받지 않는다. 모든 canonical field가 승인·remap되고 catalog·ontology·fingerprint·physical graph·compiler·AST policy 검증을 통과하면 immutable mapping version을 쓴 뒤 `activation.json`을 마지막 원자 commit record로 교체한다. `current.json`은 review 조회 pointer일 뿐 실행 권한이 아니며, 재게시 도중 먼저 바뀌거나 process가 중단돼도 strict loader는 이전 activation이 가리키는 immutable mapping을 계속 사용한다. registry·catalog·clarification은 매번 activation과 registration을 strict reload하며 draft-only·누락·변조 항목은 개별 fail-closed로 숨긴다. UI publish 성공은 strict reload를 통과한 publish 응답으로 확정하고 registry를 재조회하되 선택은 자동 변경하지 않는다.
- 이유: registration·검토 완료·실행 가능 상태를 하나로 취급하면 미검토 DB가 노출되고, 여러 mutable pointer를 동시에 commit하려 하면 중단 시 기존 정상 DB까지 registry에서 사라질 수 있다. 단일 activation commit과 immutable mapping을 실행 권한의 기준으로 두면 publish 전 격리, crash-safe 재게시, 재시작 복원, 명시적 사용자 선택을 동시에 보존할 수 있다.

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
- 다음 개발용 범위와 완료 기준을 `docs/prompts/phase1-01-jsonl-loader-quality-report.md`에 기록

### 2026-09-10

- versioned dataset record·issue·quality report Pydantic 계약 구현
- `.csv` 확장자의 JSONL을 binary streaming으로 읽는 UTF-8/BOM loader와 content fingerprint 구현
- malformed line 이후 분석 지속과 line-level 오류 taxonomy 구현
- exact record, 반복 질문, 동일 질문·SQL duplicate, 질문별 SQL conflict를 구분하는 결정적 report 구현
- 원문 비공개 기본값과 제한 preview, 기존 report 덮어쓰기 차단·원자 저장 CLI 구현
- 실제 5,385개 데이터셋의 기존 통계 재현 및 중복 질문 19개를 duplicate 16개·conflict 3개로 세분화
- 다음 개발 시작점을 SQL 정규화 규칙과 sqlglot 기반 기존 SQL AST 분석으로 이동
- 다음 개발용 범위와 완료 기준을 `docs/prompts/phase1-02-sql-normalization-ast-analysis.md`에 기록

### 2026-09-11

- PostgreSQL dialect 기반 SQL parser와 comment-free conservative canonical round-trip 구현
- canonical SQL hash와 type-aware literal-redacted shape hash를 분리하고 원본 AST 불변성 검증
- AST node 기반 table·column·projection·predicate·function·clause·JOIN·subquery·operator·literal feature 추출
- 현재 Query IR 후보를 네 상태와 결정적 reason code로 분류
- 원문 비공개 기본값, 제한된 redacted preview, version metadata를 갖는 SQL analysis report와 `analyze-sql` CLI 구현
- 실제 5,385개 SQL 모두 분석: issue 0개, canonical 5,085개, shape 1,667개
- 기존 핵심 통계를 재현하고 모든 산술 AST 357개와 기존 특정 뺄셈 256개의 정의 차이를 기록
- 다음 개발 시작점을 기존 SQL→Query IR 변환기와 수동 검토 목록으로 이동
- 개발 프롬프트 파일명에 Phase 내 작성 순서 번호를 추가하고 기존 문서 참조 갱신
- SQL→Query IR 변환과 수동 검토 목록 구현 범위를 `docs/prompts/phase1-03-sql-to-query-ir-conversion.md`에 기록
- 승인된 `cnu-courses-v1` source profile과 10개 physical→semantic mapping 구현
- analyzer 후보 gate, typed literal, contains LIKE, LIMIT provenance와 intent signature 검증을 갖는 SQL→Query IR 변환기 구현
- 질문별 SQL conflict 자동 변환 차단과 결정적 수동 검토 reason taxonomy 구현
- 원문 비공개 conversion report, opt-in private JSONL, 경로 충돌·덮어쓰기·부분 저장 안전장치와 `convert-ir` CLI 구현
- 실제 5,385개 record 변환: exact 11개, 실행 LIMIT 정책 적용 3,201개, review 1,059개, unsupported 1,114개
- 다음 개발 시작점을 Phase 1의 실제 식별값 가명화 도구로 이동
- 식별값 가명화의 HMAC token, field policy, question-IR 동기화, lineage와 leak audit 범위를 `docs/prompts/phase1-04-identifier-pseudonymization.md`에 기록

### 2026-09-12

- versioned semantic field 가명화 profile, 외부 secret key 검증, 결정적 HMAC token과 keyed lineage 계약 구현
- private converted JSONL loader, 보수적 질문·instruction·IR 동시 변환, 충돌·겹침·잔존값 review gate 구현
- aggregate summary, 로컬 review, 불변 generation과 manifest-last 저장, `pseudonymize-ir` CLI 구현
- 실제 3,212개 변환 record를 메모리에서 검증: 출력 가능 2,528개, review 684개, 알려진 값 잔존 0개
- 다음 시작점을 Phase 2 공통 평가 질문·기대 semantic result 계약으로 이동
- Phase 2 공통 평가 질문·기대 semantic result의 범위와 완료 기준을 `docs/prompts/phase2-01-common-evaluation-questions.md`에 기록

### 2026-09-13

- `FixtureSeed v1`의 40개 한국어 공통 질문과 gold Query IR·기대 semantic row·offering provenance 초안 작성
- seed·ontology·Query IR 계약 fingerprint 고정, 독립 seed-only filter/정답 검증, 세 SQLite 스키마의 gold 결과 multiset 동등성 검증
- 15개 field 전체와 JOIN 0~4단계, 빈 결과 2건·한 행 14건·여러 행 24건을 coverage report로 기록
- 40건 모두 `NEEDS_HUMAN_REVIEW`로 유지하고 다음 시작점을 경량 evaluation runner로 이동
- Phase 5 경량 evaluation runner의 지표 분모, oracle smoke와 실제 모델 성능의 구분, 실패·비교 불가 처리 범위를 `docs/prompts/phase5-01-lightweight-evaluation-runner.md`에 기록
- FakeModel 주입 runner와 `run-smoke` CLI, 40×3 attempt의 구조적 IR·semantic 결과·실행 상태·일관성 집계 및 기본 비공개 report 구현
- oracle smoke와 사람 검토 대기를 명시하고 다음 시작점을 `RecordedModel` 안전 재생 계약으로 이동

### 2026-09-14

- 후속 프롬프트는 작은 완료 단위로 분할하고, 서브 에이전트는 독립 구현·리뷰가 실질적으로 필요한 경우에만 사용하도록 로드맵 실행 원칙 갱신
- 조기 설계 확인과 완료 후 필수 승인 관문을 나눈 `docs/human-validation-checkpoints.md` 작성; 후속 프롬프트·중간 점검의 사람 검증 안내 원칙 및 ADR-022 추가

### 2026-09-15

- strict versioned recording 계약, privacy-safe loader와 exact context-bound `RecordedModel` 오프라인 재생 코어 구현
- 기록 성공 JSON 타입 보존, missing/context mismatch/provider failure의 통제된 runner code와 invalid output 원문 비노출 검증
- 다음 시작점을 recording 수집·실행 CLI 및 model/config provenance 검증으로 이동; 실제 모델·PostgreSQL 실행은 데스크톱 단계로 유지
- 다음 작업을 읽기 전용 recorded evaluation CLI와 이후 live recording 수집기로 분할하고 `docs/prompts/phase5-03-recorded-evaluation-cli.md` 작성

### 2026-09-18

- `run-recorded` 읽기 전용 CLI로 private recording, `RecordedModel`, 기존 evaluation runner 연결
- recording의 run ID 강제 사용, offline replay·provenance 미검증 경고, 입력·출력 same-file 및 원자 저장 정책 검증
- 누락·기록 실패·context mismatch가 예정 attempt에서 유지되는 합성 CLI 회귀 테스트 추가
- 다음 시작점을 live recording writer/capture 계약과 수집 provenance로 이동
- 다음 작업을 메모리 내 capture session, private storage·provenance manifest, provider 수집 CLI로 추가 분할하고 `docs/prompts/phase5-04-recording-capture-session-core.md` 작성
- `recorded-model-v1`과 호환되는 메모리 내 `RecordingCaptureSession` 구현
- JSON 타입·capture 순서·context hash 보존, 방어적 복사, 중복·비정상 payload·sealed session의 안전한 거부 검증
- 다음 시작점을 private recording atomic storage와 provenance manifest 계약으로 이동
- private storage·선언형 provenance manifest의 범위와 안전 조건을 `docs/prompts/phase5-05-private-recording-storage-provenance.md`에 기록
- `recording-bundle-v1` 계약과 content-addressed immutable generation, manifest-last private storage 및 strict loader 구현
- recording·manifest 변조, path traversal, symlink·Windows junction/reparse point, hardlink, tracked target과 부분 저장을 차단하는 합성 회귀 테스트 추가
- provenance를 `DECLARED_UNVERIFIED` 선언 metadata로 제한하고 다음 시작점을 provider-neutral capture orchestrator와 명시적 실패 분류 계약으로 이동
- provider 호출·storage·CLI를 제외한 작은 후속 범위를 `docs/prompts/phase5-06-provider-neutral-capture-orchestrator.md`에 기록
- 기존 `QueryModel`과 `RecordingCaptureSession`을 연결하는 provider-neutral `capture_recording()` orchestration 코어 구현
- batch preflight, target당 단일 호출, JSON 타입·순서 보존, typed/예상 밖 실패 후 계속 진행과 caller context 불변성 검증
- 다음 시작점을 실제 저장·CLI와 분리된 Ollama adapter의 최소 호출·오류 변환 계약으로 이동

### 2026-09-19

- 데스크톱 이전 시 프로젝트 목적, 구현·미검증 경계, Ollama·PostgreSQL의 분리 smoke test, 사람 승인 관문과 기록 양식을 `docs/desktop-test-handoff.md`에 정리
- Phase 5-07 Ollama `QueryModel` adapter의 최소 호출·오류 변환과 합성 transport 검증 범위를 `docs/prompts/phase5-07-ollama-querymodel-adapter.md`에 기록. SQL 텍스트를 provider 실패나 유효 Query IR로 보정하지 않는 경계를 명시
- Phase 5-07 Ollama `QueryModel` adapter를 별도 구현하고 합성 transport의 단일 호출·JSON 타입·비 JSON 문자열·typed failure·orchestrator 및 harness 연계를 검증. 다음 시작점을 소수 합성 질문 live smoke로 이동; 실제 Ollama·DB·40×3 평가는 미실행
- Phase 5-08의 opt-in 합성 Ollama live smoke, 응답 비저장 형식 분류, 환경 미준비 시 중단 기준을 `docs/prompts/phase5-08-ollama-adapter-live-smoke.md`에 기록. 실제 live 호출은 아직 미실행
- opt-in 합성 Ollama smoke 진입점과 비저장 형식 관찰값을 구현하고 합성 transport 검증 후 로컬 모델을 2회 호출. 두 응답 모두 JSON object·QueryIR 구조·semantic field/operator 검증 통과; 정확도·40×3·DB 실행으로 해석하지 않음. 결과는 `docs/ollama-adapter-live-smoke-results-2026-09-19.md`에 안전한 요약만 기록
- 다음 시작점을 합성 provider의 private capture→bundle 연결과 Ollama provenance hash 계약으로 좁히고 `docs/prompts/phase5-09-private-capture-provenance-synthetic.md`에 범위·테스트·E3/L5 관문을 기록. 실제 응답 저장은 미실행
- Phase 5-09 합성 provider capture→private bundle→strict load 연결과 동일 storage path preflight, synthetic-only opt-in CLI 구현. Ollama versioned template·실제 전송 설정의 선언 provenance hash를 고정하고 실제 Ollama·CNU 응답 저장 없이 검증. 다음 시작점을 승인 관문이 있는 live capture 입력·실행 경계로 이동
- Phase 5-10의 private target 입력·명시적 live 실행 경계를 합성 테스트로만 구현하는 범위를 `docs/prompts/phase5-10-live-capture-boundary.md`에 기록하고 새 대화용 최신 인수인계를 `docs/next-session-handoff.md`에 정리. 실제 응답 수집 전 E3·L5 관문 유지
- Phase 5-10 `capture-targets-v1` private 입력 loader와 별도 opt-in Ollama capture CLI 구현. 전체 batch·provenance·저장 경로 사전 검증, 합성 transport의 정확한 단일 호출·typed failure·strict load·비노출 출력을 확인. 실제 Ollama 호출과 실제 응답 저장 0회; E3·L5 확인은 후속 단계로 유지
- Phase 5-10의 커밋·전용 테스트 14개를 가볍게 재확인하고, Phase 5-11의 호출·저장 없는 사전 점검과 E3·L5 사람 검토 자료 범위를 `docs/prompts/phase5-11-capture-preflight-approval.md`에 기록. 실제 수집 승인과 모델 성능 검증은 미완료
- Phase 5-11 `--check`/`--live` 공통 preflight와 상호 배타적 CLI 모드 구현. 합성 transport로 호출·저장 없는 점검, 사전 오류 차단, 기존 manifest 보존과 live 회귀 확인. E3·L5 미기입 검토 양식 작성; 실제 Ollama 호출·응답 저장 없이 `PENDING_HUMAN_REVIEW` 유지
- Phase 5-11 구현의 공통 preflight와 검토 양식을 확인하고 `tests` 298개 통과·3개 건너뜀, compileall·커밋 diff 검사를 재확인. Phase 5-12를 합성 질문 2건의 로컬 private capture로 정의; 실제 수집은 아직 미실행
- 팀 결정은 개발 중 모든 단계의 중단점이 아니라는 기존 체크포인트를 재확인. Phase 5-12의 합성 로컬 수집을 E3·L5 선행 승인 대상으로 과도하게 묶은 문구를 수정하고 실제 CNU 자료 사용·공유 및 배포·외부 시연 관문으로 한정
- 사용자의 팀 협업 순서를 반영해 합성 개발 중 사람 판단을 조기 요청하지 않는 운영 원칙을 `AGENTS.md`, 계획 문서, 새 대화 인수인계에 명시. E3/L5와 L1/L3는 해당 실제 사용·공유·성능 주장 직전의 미완료 관문으로 유지
- Phase 5-12 완전 합성 target 2건으로 `--check` 후 로컬 Ollama `--live` 1회 실행. private recording 성공 2건을 strict loader·`RecordedModel`로 검증하고 JSON object·현재 QueryIR 구조/semantic 유효 상태를 안전한 집계로 기록. 실제 CNU 자료·DB·40×3 평가는 사용하지 않았고 사람 관문은 대기 유지
- Phase 5-12 공개 요약과 현 private bundle의 선언 provenance fingerprint·strict load 건수(2건 성공)를 재확인하고 관련 테스트 33개 및 전체 `tests` 298개 통과·3개 건너뜀을 확인. 다음 단계를 기존 fine-tuned 모델의 합성 40×3 잠정 평가 연결로 수정; 실제 모델 정확도 확정은 미완료
- Phase 5의 미완료 비교 실험보다 오프라인 재생 smoke를 먼저 제안한 우선순위를 재검토. 수집·strict load·`run-recorded` 기반이 이미 있으므로 Phase 5-13을 첫 미완료 항목의 합성 탐색 평가로 이동하고 사람 검토 전 확정 성능 주장 금지를 유지
- Phase 5-13 합성 fixture 40건×3스키마의 deterministic private target 생성, `--check` 후 로컬 Ollama 120건 capture, strict bundle load와 `run-recorded` 오프라인 평가 연결. 전체 120 attempt에서 구조 exact 51, 실행 성공 99, semantic 일치 90; gold 전부 사람 검토 대기로 유지하고 fine-tuned 모델 평가 완료 체크는 보류
- Phase 5-13 커밋·공개 잠정 집계와 private bundle strict load 120건 성공·0건 provider 실패를 재확인하고 관련 테스트 8개, 전체 `tests` 303개 통과·3개 건너뜀을 확인. 로컬에는 fine-tuned tag만 있어 Phase 5-14를 `UNKNOWN_FIELD` 비노출 분류와 단일 versioned 동적 schema prompt 탐색 비교로 정의; 확정 성능·base instruct 비교는 미실행
- Phase 5-14 v1 `UNKNOWN_FIELD` 21건을 private 비노출로 분류(ontology 밖 12, 접두사 5, 물리 형태 4). 명시적 `query-ir-v2`를 같은 합성 40×3 target에 `--check` 후 120회 수집·strict load·offline paired 비교. 구조 exact 51→0, 실행 99→42, semantic 90→36, `UNKNOWN_FIELD` 21→72로 악화해 v1 기본 유지. Gold 승인·확정 성능·사람 관문은 미완료 유지
- Phase 5-14 공개 비교와 private v1/v2 bundle의 각 120건 strict load, 잠정 상세 report 집계를 재확인하고 관련 테스트 34개·전체 `tests` 309개 통과, 3개 건너뜀을 확인. Phase 5-15를 v2 실패 비노출 감사·paired 비교 context 검증·독립 합성 평가 기준 고정으로 정의; 새 모델 호출과 확정 성능 주장은 미실행
- Phase 5-15 v2 `UNKNOWN_FIELD` 비노출 감사 범주(6개 고정 상수)와 `audit_summary()` 함수 추가, paired 비교 계약을 benchmark_id·version·seed_fingerprint·fixture metadata fingerprint까지 보강. 독립 합성 평가 세트 설계 기준 문서 작성. 관련 테스트 17개·전체 `tests` 315개 통과, 3개 건너뜀. ADR-027 추가. gold 40건 `NEEDS_HUMAN_REVIEW` 유지, 새 prompt 탐색·live 호출·확정 성능 발표 없음
- Phase 5-15 보완 paired 비교 무결성 계약(bundle 필수·model_run_id 자기일치·provenance 비교·fingerprint·request ID 정확대응)과 합성 테스트를 추가하고 관련 테스트로 회귀를 확인. report-level 재집계로 v1 `UNKNOWN_FIELD` 21건·v2 72건, manifest 각 120건, target 각 120건을 확인. strict-load v2 범주 재감사는 당시 실행 환경의 private bundle recording 접근 차단(`PRIVATE_BUNDLE_UNREADABLE`)으로 미검증으로 남긴다. gold 40건 `NEEDS_HUMAN_REVIEW` 유지, 새 prompt 탐색·live 호출·확정 성능 발표 없음
- Phase 5-15 최종 보완으로 비교 recording을 bundle 자신의 `recording`에서만 가져오도록 API를 단순화해 바꿔치기 경로를 제거하고, bundle 내용 변조 거부와 실제 계약 모델 기반 합성 테스트를 추가. Codex 읽기 전용 재검증에서 private bundle 3개 strict load 후 v2 `UNKNOWN_FIELD` 72건 범주 재집계(접두사 불일치 46, ontology 밖 6, 물리 형태 5, 기타 15, 표기 차이 0, 판별 불가 0; 중복 27, 혼합 50)와 실제 bundle 기반 paired 비교(v1 21건·v2 72건)를 확인. 미승인 합성 개발 세트의 진단 집계이며 확정 성능 아님. gold 40건 `NEEDS_HUMAN_REVIEW` 유지
- Phase 5-16 독립 합성 평가 세트 30건 초안(P2-041~070, `fixture-v1-independent-questions`) 작성. 정식 30~50건 조건·seed/ontology/QueryIR fingerprint·물리 식별자·offering key·기대 행을 기존 validator로 통과하고 flat·normalized·legacy에서 gold IR 90회(30×3) 실행이 기대 semantic 결과와 일치함을 확인. `check_set_isolation()`으로 개발 세트와의 benchmark·case ID·질문 문장·paraphrase family·intent 겹침 분리를 고정 code로 검증. question holdout이며 동결·승인·성능 검증 아님. 기존 40건과 새 30건 모두 `NEEDS_HUMAN_REVIEW` 유지, ADR-028 추가
- Phase 5-17 독립 세트 문항 정리(P2-057 경계 배타 표현·P2-059 요일 코드 의존 제거, IR·기대 결과 정합 유지, 격리 유지)와 단독 커버리지(30건·15개 field 전부 사용·결과 0/1/여러 건·JOIN 0~3단계·gaps 3건) 및 FakeModel oracle smoke 90건 전건 일치(`oracle_smoke=true`, 모델 점수 아님) 확인. 제품 코드 변경 없음. 새 30건은 사람 검토·동결 전 question holdout 초안 유지
- Phase 6-01 합성 입력의 제한된 QueryIR repair 경계(`BoundedRepairCoordinator`, 최초 1회 + 검증 실패 시 repair 1회) 구현. repair provider에는 request 복사본·ontology·mapping·고정 실패 code만 전달하고 후보는 전체 안전 경로를 다시 통과. `MODEL_INVALID_OUTPUT`은 `IR_GENERATED`에서만, 4개 semantic 오류는 `IR_VALIDATED`에서만 허용하고 그 외·provider 예외·취소는 재시도 없음. 합성 테스트에서 호출 횟수·안전성·비노출을 검증. critique·사용자 확인·관리자 검토 큐는 미구현. ADR-029 추가. 기존 40건과 새 30건 `NEEDS_HUMAN_REVIEW` 유지
- Phase 6-01 결함 수정: repair 경로가 호출자 소유 ontology·mapping 원본을 provider와 두 번째 실행에 그대로 전달해 provider의 변이(`mapping.fields` 비우기 등)가 두 번째 시도를 `MAPPING_MISSING`으로 바꾸던 문제를 수정. 최초 실행 전에 request·ontology·mapping 깊은 복사 스냅샷을 고정하고 세 전달 지점(최초 모델·provider·두 번째 실행)에 스냅샷 복사본만 전달. 최초 모델과 provider가 request·ontology·mapping을 변이해도 원본과 다른 시도가 그대로임을 합성 회귀 테스트로 검증. ADR-029 반영
- Phase 6-01 결함 추가 수정: 스냅샷 복사본을 harness에 넘겨도 harness가 같은 객체를 모델과 검증에 공유하므로, 유효 IR을 반환하면서 `mapping.fields`를 비우는 최초 모델이 첫 시도를 terminal `MAPPING_MISSING`으로 만들던 문제를 수정. 모델을 격리 래퍼(`_IsolatedModel`)로 감싸 harness 검증 컨텍스트와 분리된 복사본만 모델에 전달. 변이 후 유효 IR 반환 시 원본 컨텍스트로 성공·repair 미호출임을 합성 회귀 테스트로 검증. `ExecutionHarness`·`QueryModel` 계약 변경 없음
- Phase 6-02 합성 결과의 결정적 critique 분류(`backend/adaptive/harness/critique.py`, `critique-disposition-v1`) 구현. 성공은 `EXECUTED_UNVERIFIED`+`repair_attempted`·`recovered_after_repair` 구분, 실패는 28종 code·`REPAIR_PROVIDER_ERROR`의 허용 (code, stage) 조합으로 6개 disposition 분류, 위조·미지·모순 입력은 `CRITIQUE_INVALID_SUMMARY`로 거부. 실제 coordinator 결과와 전 code 고정 테스트(`tests/adaptive/test_critique_disposition.py`)로 검증. 독립 리뷰에서 repair 없는 요약의 최종·초기 불일치와 repair 호출 없는 `REPAIR_PROVIDER_ERROR` 수락 결함을 발견해 명시적 불변식(repair 없으면 최종=초기, provider 실패는 repair 시도 필요, repair 표시는 Phase 6-01 재시도 허용 쌍에서만)과 회귀 테스트를 추가하고 결과 flag를 `repair_attempted`·`recovered_after_repair`로 바로잡음. 사용자 확인·관리자 큐·재시도 변경 없음. ADR-030 갱신. 기존 40건과 새 30건 `NEEDS_HUMAN_REVIEW` 유지
- Phase 6-03 합성 모호성 신호·사용자 확인 경계(`backend/adaptive/harness/clarification.py`, `clarification-gate-v1`) 구현. caller 제공 명시적 신호(2개 이상 서로 다른 option ID·semantic 식별자, 물리·SQL·행·자유 답변 없음)만 `prepare`로 pending에 묶고, 신호 없으면 pending·차단 없음. pending·결과·예외에 원문·원시 식별자·물리·SQL·행·경로·secret 비노출(식별자는 domain-separated hash만 저장). `resolve`는 caller 원본 신호를 재요구해 정렬 option ID·semantic_ref 전체 일치를 확인하고 정확히 한 개 선택만 `CONFIRMED`, 명시적 취소만 `CANCELLED`. 자동 감지·QueryIR 보정·하네스 자동 실행·UI·관리자 큐·저장소·DB·네트워크·Ollama·legacy·`CritiqueResult` 변경 없음. 합성 테스트(`tests/adaptive/test_clarification_gate.py`) 23개로 검증. `모호한 질문 감지 및 사용자 확인` 전체 항목은 자동 감지·실제 상호작용 미구현으로 미완료 유지. ADR-031 추가. 기존 40건과 새 30건 `NEEDS_HUMAN_REVIEW` 유지. 실제 CNU 자료·private bundle 미사용
- Phase 6-03 독립 리뷰 보완: `resolve`의 pending 단독 신뢰, pending·결과의 원시 식별자 보관, 공개 경계의 인스턴스 재검증 생략 결함을 수정. `resolve`에 caller 신호 재제출과 정렬 option 전체 비교(일관성 확인이며 암호학적 진위 증명 아님), request·database 식별자의 domain-separated fingerprint 저장, 전 경계 dump-then-validate 재검증을 적용. 합성 테스트 35개로 검증(pending 후보 추가·삭제·교체·semantic 불일치, 경로·secret형 식별자 비노출, `model_copy(update=...)`·`model_construct` 위조 거부). ADR-031 보완. 범위·관문·gold 상태 변경 없음. 실제 CNU 자료·private bundle 미사용
- Phase 6-03 위조 인스턴스의 Pydantic serializer 경고에 입력값이 담기는 노출 경로를 차단하고, 경로형 악성 선택값에서 경고가 발생하지 않는 회귀 테스트를 추가. 합성 전용 테스트 35개 유지
- 중간 점검 결함 수정: Phase 6-02의 위조 repair 횟수·code 타입을 재검증해 고정 오류로 거부하고, Phase 6-03의 caller option ID 원문 보관을 없애기 위해 `clarification-gate-v2`로 갱신. 이전 v1 pending은 받지 않으며 실제 UI·저장소 migration 대상은 없다. 합성 회귀 테스트 추가
- Phase 6-04 합성 관리자 검토 큐 경계(`backend/adaptive/harness/review_queue.py`, `review-queue-v1`) 구현. 실제 coordinator 요약의 성공·입력 실패 미등록과 운영 실패 5개 disposition 등록, 동일 항목 재등록·충돌 거부, 위조 요약·ID 오류 차단, 반환·목록 방어적 복사, 경로·secret형 ID 비노출을 합성 테스트(`tests/adaptive/test_review_queue.py` 24개)로 검증. coordinator·critique·clarification·harness 동작 변경 없음. 실제 운영 큐 저장·관리자 UI·사람 승인·full loop는 미완료 유지. ADR-032 추가. 40건·30건 `NEEDS_HUMAN_REVIEW`와 E3/L5·L1/L3 대기 유지. 실제 CNU 자료·private bundle·Ollama·PostgreSQL 미사용

### 2026-09-20

- Phase 6-05 독립 리뷰 보완: strict-load의 code·stage·disposition 개별 검증 간극을 수정. critique 단일 분류표의 공개 validator(`disposition_for`)로 저장 triple 일치를 저장 전·strict load 모두에서 강제하고, `INVALID_REQUEST` 운영 둔갑·stage 있는 `REPAIR_PROVIDER_ERROR`·`recovered_after_repair=True`를 고정 `INVALID_CONTRACT`로 거부. hash·idempotency key까지 재서명한 위조 snapshot 거부와 저장 전 차단의 합성 테스트 5개 추가. private 경로를 부모 생성 후·게시 직전에 재검사하고 race 비보장 경계를 문서화. 임시 파일 삭제 자체가 실패해도 경로가 담긴 OS 예외를 노출하지 않도록 하고 합성 테스트 1개를 추가. ADR-033 보완. 관문·gold 상태 변경 없음. 실제 CNU 자료·private recording bundle 미사용
- Phase 6-05 합성 검토 큐의 private snapshot 저장·strict load(`backend/adaptive/harness/review_queue_storage.py`, `review-queue-snapshot-v1`) 구현. `list_entries()` 고정 메타데이터만 명시적 private 단일 JSON에 저장하고 queue version·항목 수·순서·canonical payload SHA-256으로 strict load. 저장 전 경로 검사는 기존 recording bundle 동일 validator의 최소 공개 wrapper(`preflight_private_snapshot_path`·`read_private_snapshot_bytes`)를 재사용하고 별도 약한 검사를 두지 않음. no-overwrite 기본·원자 게시·임시 정리, 빈 큐 명시 저장 허용. 합성 테스트(`tests/adaptive/test_review_queue_snapshot.py` 27개: 순서·hash·거부 조건·경로·비노출) 검증. 기존 bundle 저장·legacy 동작 변경 없음. `운영 실패를 관리자 검토 큐에 저장` 전체 항목은 실제 운영 연결 전까지 미완료 유지. ADR-033 추가. 40건·30건 `NEEDS_HUMAN_REVIEW`와 E3/L5·L1/L3 대기 유지. 실제 CNU 자료·private recording bundle·Ollama·PostgreSQL·FastAPI/React·모델 점수 미사용

### 2026-09-21

- Phase 6-06 합성 agent workflow 통합(`backend/adaptive/harness/agent_workflow.py`, `agent-workflow-v1`) 구현. `DetailedRepairOutcome`으로 최종 `HarnessResult` 보존(기존 `run()`은 `run_detailed()`에 위임, 호출 예산·동작 유지; `REPAIR_PROVIDER_ERROR`는 `final_result=None`). 명시적 `model_run_id`·request·ontology·Published mapping·model·repair provider·주입 queue로 상세 실행 → 내부 critique → 운영 실패 후보 등록을 한 번의 호출로 수행하고 `CritiqueResult` 주입 API를 두지 않음. 성공·0행 성공·`REQUEST_INVALID` 미등록, queue ID 단일 공개 helper(`validate_queue_identifier`) 실행 전 재사용, conflict·내부 불일치 고정 code fail closed와 실행 후 queue 실패 잔여 경계 문서화. 합성 테스트(`tests/adaptive/test_agent_workflow.py` 31개) 검증. `validate → execute → critique → repair loop 구현`만 완료 처리하고 모호한 질문 감지·실제 운영 영속 큐·교정 반영은 미완료 유지. ADR-034 추가. 40건·30건 `NEEDS_HUMAN_REVIEW`와 E3/L5·L1/L3 대기 유지. 실제 Ollama·PostgreSQL·CNU 자료·UI·운영 영속 큐 미사용
- 독립 리뷰 후속 수정: `DetailedRepairOutcome`에 dump 재검증 nested 일치(최종 status/stage/code/succeeded, provider-error iff 결과 부재)를 강제하고 미사용 helper를 제거. `AgentWorkflowResult`에 단일 출처 불변식(critique 일치·최종 결과 일치·`NOT_ELIGIBLE`/적격 entry 일치)과 위조 nested dump 재검증을 추가하고, 성공·`REQUEST_INVALID` 같은 비운영 결과를 조작된 `ENQUEUED` 상태로 표시하는 역방향 불일치도 거부한다. 위조·변이 `HarnessRequest` 인스턴스는 평문 정규화 후 기존 `INVALID_REQUEST`·`NOT_ELIGIBLE`·무호출 경로로 처리. 위조·불일치 회귀 테스트를 `tests/adaptive/test_agent_workflow.py`에 추가(45개). 고정 code·비노출 유지, 범위 확장 없음. ADR-034 보완
- Phase 6-07 합성 영속 검토 큐 lifecycle(`durable-review-workflow-v1`) 구현. strict snapshot 복원 → 기존 agent workflow 1회 → `ENQUEUED`일 때만 새 immutable snapshot 게시를 연결하고 동일 entry·성공·입력 실패는 무쓰기 처리. source/next shared private path preflight, no-overwrite, 위조·중복·hash/count/version 거부, conflict·입력·내부·storage 고정 오류, 비노출 반환을 합성 테스트 22개로 검증. 독립 검토에서 private 내부 접근 제거, 내부 오류 오분류 수정, 게시 후 재읽기/결과 생성 실패 경계 제거, hard-link temp 정리 실패 target 롤백을 추가. 인접 queue/snapshot/workflow 회귀 101개 통과·1개 skip. ADR-035 추가. 실제 Ollama·PostgreSQL·CNU 자료·UI·운영 DB/current pointer·승인 흐름·성능 측정 없음. 40건·30건 `NEEDS_HUMAN_REVIEW`와 E3/L5·L1/L3 대기 유지

### 2026-09-22

- Phase 6-08 합성 ambiguity 감지(`ambiguity-detector-v1`)와 기존 `clarification-gate-v2` 연결 구현. versioned literal ruleset 검증, 0/1/multi match 분기, opaque option ID, prepare/resolve 재감지, 위조·context/ruleset drift·비노출 경계를 합성 테스트로 검증. 독립 검토에서 canonical 전체 ruleset fingerprint를 option ID에 결합해 여전히 match되는 trigger 변경과 미매치 rule 변경도 과거 pending을 확인하지 못하게 하고 package 공개 API를 추가. 상위 모호성 항목은 QueryIR 반영·실제 UI 미구현으로 미완료 유지. ADR-036 추가. 실제 Ollama·PostgreSQL·CNU 자료·성능 측정 없음. E3/L5·L1/L3 대기 유지
- Phase 6-09 확인 선택의 합성 QueryIR constraint 전달(`clarification-query-constraint-v1`) 구현. 기존 detector/gate 재확인, opaque 선택의 semantic ref 승격, 방어적 context 복사와 constraint-aware provider 1회 호출, QueryIR 구조·ontology·선택 준수 검증을 연결. 독립 검토에서 constraint를 최소 fingerprint 필드로 축소하고 violation code 중복을 수정했으며 known raw context reflection 차단과 semantic/physical 이름 겹침 과차단 회귀를 추가. 관련 테스트 83개 통과. 실행·compile·repair·UI·실제 DB/Ollama/CNU 자료·성능 측정 없음. 상위 모호성 항목과 E3/L5·L1/L3 대기 유지. ADR-037 추가
- Phase 6-10 확인 선택의 합성 read-only 실행 adapter(`clarification-execution-adapter-v1`) 구현. Phase 6-09 재확인·제약 생성 뒤 검증된 QueryIR를 내부 one-shot model로 기존 `ExecutionHarness`에 전달해 validation·compile·SQL policy·read-only execute·result validation을 우회 없이 수행. compile 이후 기존 실패 code/stage·QueryIR 보존, 초기 단계/불일치 nested 결과는 고정 내부 오류로 차단. 독립 검토에서 결과 모델 자체에 QueryIR·request fingerprint·status/error 불변식을 추가하고 직접 위조 회귀를 보완. 신규 12개·인접 105개 테스트 통과. UI·session/expiry·repair·실제 Ollama/PostgreSQL/CNU 자료·성능 측정 없음. 상위 모호성 항목과 E3/L5·L1/L3 대기 유지. ADR-038 추가

### 2026-09-26

- Phase 6-11 합성 clarification session(`clarification-session-v1`) 구현. `start`의 no-match/단일 pending과 `submit`의 TTL·취소·원자적 일회 소비·동시 제출 차단을 기존 Phase 6-08~10 경로에 연결. raw token은 저장하지 않고 hash tombstone만 유지하며 active raw context의 메모리·lazy expiry·crash exactly-once 한계를 문서화. 독립 검토에서 clock 실패 후 재사용 가능, lazy expiry 정리, 최소 token 길이, deadline 정밀도, package export를 수정하고 회귀 추가. 신규 13개와 Phase 6-08/09/10 포함 71개 관련 테스트 통과. HTTP/UI·영속 session·실제 Ollama/PostgreSQL/CNU 자료·성능 측정 없음. 상위 모호성 항목과 E3/L5·L1/L3 대기 유지. ADR-039 추가
- Phase 6-12 합성 clarification HTTP sub-app(`clarification-http-v1`) 구현. server-owned context loader와 app-lifetime coordinator/provider/executor를 두 POST endpoint에 연결하고 malformed/session/constraint/provider/internal 오류의 fixed HTTP 매핑과 start/submit redaction을 검증. 독립 검토에서 Phase 6-11 option 이중 fingerprint로 start 응답 선택을 submit할 수 없던 결함을 selectable option↔pending binding으로 수정하고 HTTP 재감지를 제거했으며 token/nested/row/error 불변식을 강화. 신규 HTTP 13개, session 14개와 Phase 6-08~12 관련 총 122개 통과. legacy app·React·auth·실제 Ollama/PostgreSQL/CNU 자료·성능 측정 없음. 상위 사용자 확인 항목과 E3/L5·L1/L3 대기 유지. ADR-040 추가
- Phase 6-13 합성 clarification ASGI 프로토타입(`synthetic-clarification-prototype-v1`) 구현. `DEFAULT_SEED` 임시 flat SQLite, canonical `Course.name`·`Instructor.name` ontology/Published mapping, literal rule, 결정론 constraint provider를 Phase 6-12 factory에 조립하고 loopback-only module CLI와 재현 문서를 추가. 독립 검토에서 CLI의 임시 DB 이중 생성·invalid port 반사·실패 cleanup 지연과 비canonical semantic 이름을 수정. 신규 7개와 Phase 6-08~12 관련 92개 테스트 통과. legacy app·React·인증·실제 Ollama/PostgreSQL/CNU 자료·성능 측정 없음. 상위 사용자 확인 항목과 E3/L5·L1/L3 대기 유지. ADR-041 추가
- Phase 6-14 별도 합성 clarification React 흐름(`/adaptive-prototype`) 구현. strict start/submit/error parser, option·cancel·결과 상태, 메모리 token 폐기, stale/abort 처리, generic result table과 synthetic 경고를 추가하고 `/api/adaptive` loopback proxy·동시 실행 script를 연결. 독립 검토에서 pending session 고아 상태, token 조기 삭제, submit/code/stage 검증, proxy 환경변수 충돌, system Python script, unknown error suffix 오분류를 수정. Node 16개·변경 파일 ESLint·frontend build·Phase 6-13 backend 7개 통과. browser 실기동 smoke 전이므로 상위 사용자 확인 항목은 미완료. legacy app·실제 Ollama/PostgreSQL/CNU 자료·성능 측정 없음. E3/L5·L1/L3 대기 유지. ADR-042 추가

### 2026-09-27

- Phase 6-15 합성 clarification 통합 browser smoke와 팀 공유 handoff 완료. backend+Vite loopback 실제 기동 후 Chrome headless DOM·network로 option 두 갈래, 각 6행 결과, cancel, no-match, terminal 재제출 차단, legacy `/`를 확인하고 별도 실패 proxy로 redacted 일반 오류를 검증. 독립 검토에서 비-2xx 비-JSON proxy 오류의 계약 오류 오분류를 일반 server 오류로 수정하고 회귀 테스트 추가. 합성 내부 범위의 `모호한 질문 감지 및 사용자 확인` 항목을 완료 처리하되 L4·실제 auth/provider/PostgreSQL/CNU 자료·배포·외부 시연·성능 확정은 미완료. ADR-043 및 팀 handoff 추가
- Phase 6-16 합성 활성 DB 선택과 메인 course catalog·clarification 동기화 완료. legacy 기준선은 기존 API로 보존하고 flat·legacy-schema 합성 DB를 server registry에 등록했으며, sessionStorage 선택을 registry로 재검증해 두 경로에 같은 DB ID를 전달. read-only compiler/policy/executor, canonical `CourseOffering.*` semantic refs, schema fingerprint drift, strict response·redaction, stale/abort·상태 초기화 계약을 구현. 독립 통합 검토에서 저장 선택 소실, 중복 query 허용, legacy 침묵 실패, 요청 경합, schema drift 간극을 수정. 관련 backend 39개·frontend 24개, ESLint/build와 Chrome browser smoke 7개 흐름 통과. 실제 CNU/PostgreSQL/Ollama·파일 업로드·mapping publish·L4 승인 없음. ADR-044 추가
- Phase 6-17 private SQLite backend 등록 quarantine 완료. 단일-fd 32 MiB bounded copy, SQLite header·sidecar/WAL·read-only immutable quick check, Git ignored private containment, ID lock·staging rename no-overwrite, sample 없는 catalog·mapping proposal, `PENDING_REVIEW` manifest와 strict 재검사를 구현. 독립 검토에서 redirect parent 생성 순서와 broken redirect, source identity, exact JSON·bounded read, artifact fsync를 수정. 신규 18개와 catalog/mapping/Phase 6-16 관련 15개 통과, source symlink 생성 권한 없는 환경 1개 skip. 실행 registry·mapping publish·upload/UI·실제 CNU/PostgreSQL/Ollama·L4 승인 없음. ADR-045 추가
- Phase 6-18 loopback SQLite upload와 검토 대기 요약 완료. 메인 `DB 변경` modal에서 raw SQLite body를 선언·실측 32 MiB로 제한해 임시 파일에 streaming하고 Phase 6-17 register→strict load를 호출한다. 성공은 `PENDING_REVIEW` schema·mapping metadata만 읽기 전용 표시하며 active DB·sessionStorage·Phase 6-16 registry/catalog/clarification은 불변이다. `gpt-6-luna` high review-only 지적을 메인에서 재현해 modal 취소 후 `uploading` 고착을 수정하고, strict response 집계/status 및 빈 query segment fail-closed를 보강했다. 관련 backend 31개 통과·1개 기존 Windows symlink 권한 skip, frontend 17개·ESLint·build 통과. browser 실기동 upload smoke는 미수행. mapping 수정·승인·publish·실행 registry 편입, 실제 CNU/PostgreSQL/Ollama·L4 승인 없음. ADR-046 추가
- Phase 6-19 private mapping 검토·publish·활성화 완료. registration proposal을 private draft로 열어 모든 canonical field를 승인/remap하고 명시적 publish한 경우에만 immutable mapping과 activation manifest를 게시하며, strict activation reload 뒤 registry·catalog·clarification에 편입한다. publish 전·변조 항목은 숨기고 active 선택은 자동 변경하지 않는다. `gpt-6-luna` high review-only에서 확인한 재게시 중단 시 기존 활성 DB 소실, review body 사후 크기 검사, publish 성공 뒤 detail 실패의 registry 갱신 누락을 activation 단일 실행 권한, streaming 상한, 성공/보조 refresh 분리로 수정했다. 신규 backend 13개, 관련 backend 36개·1 skip·7 subtests, frontend 27개·ESLint·build 통과. 실제 browser 통합 smoke·실제 CNU/PostgreSQL/Ollama·L2/L4 승인 없음. ADR-047 추가
