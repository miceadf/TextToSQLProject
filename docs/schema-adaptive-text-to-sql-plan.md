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

**현재 개발 운영 원칙(2026-10-01 갱신, ADR-086):** 실제 학교 DB는 없다는 전제(사용자 결정)로 관문을 재정의했다. 세부는 `docs/human-validation-checkpoints.md`의 2026-10-01 절을 따르고, 아래 2026-09-19 원칙은 그 이전 기록이다. **이전 원칙(2026-09-19):** 팀의 사람 판단 항목은 대기 상태로 기록하고, 지금 단계에서 결정을 재촉하거나 합성 데이터 기반 개발을 멈추지 않는다. 완전 합성 입력의 로컬 구현·테스트·private 검증을 먼저 진행한다. 실제 CNU 자료의 추가 사용·공유 전 E3, 실제 데이터 배포·외부 시연 전 L5, 모델 성능 수치 확정 전 L1·L3를 확인한다. AI가 승인 상태를 임의로 바꾸지 않는다. 이 관문이 필요한 작업에 도달하면 준비된 결과와 구체적인 결정 항목을 제시한다.

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

현재 공통 ontology는 버전 3이다(2026-10-13 사용자 결정, ADR-101). `CourseOffering.year` 정의는 `retired_in_version: 3`으로 보존하되 현재 등록·검토·게시·챗봇에서는 사용하지 않는다. 활성 항목은 `since_version <= v`이며 `retired_in_version`이 없거나 `> v`인 항목이다. 버전 1·2 기록은 자기 버전으로 읽고, 현재 runtime은 옛 mapping의 year 연결도 "이 DB에 없음"으로 본다. `CourseOffering.semester`는 유지한다.

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

Phase 7-12부터 PostgreSQL profile v2는 두 가지를 추가로 선언할 수 있다.

- 값 표현이 다른 column에 적용할 닫힌 목록의 변환. 예: 학점 `"3/3/0"` → 3, TIME → `"HH:MM"`.
- DB에 대응 column이 없는 canonical field의 명시적 미지원 목록.

변환 식은 코드의 고정 template에서만 나오며, AST policy가 exact 비교로 검증한다(ADR-068).

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

현재 구현 위치(2026-10-17, Phase 9-27): 앱 조립은 `backend/adaptive/api/app_factory.py`, DB 목록·catalog는 `backend/adaptive/api/database_registry.py`, 관리자 화면·client는 `src/adaptive/`, clarification 시연은 `src/adaptive/ClarificationDemoPage.tsx`다. NER 보정 예제는 `backend/scripts/demo_ner_correction.py`에 있다. 역사 문서의 옛 이름은 [문서 목차](README.md)의 이름 변경 표로 대조한다.

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

2026-10-13 Phase 9-17: 화면의 Legacy 기준선 선택용 내부 ID `legacy`는 새 PostgreSQL·SQLite 등록에서 기존 고정 입력 오류 코드로 거절하고 양식에서도 전송 전에 시스템 이름 안내로 막는다. 저장된 `legacy` 등록은 수정하지 않고 기존 loader의 ID 검증도 유지한다. DB 목록은 예약 선택 ID와 겹치거나 형식이 잘못된 개별 항목만 제외해 한 줄 경고와 나머지 목록을 표시한다. 최상위 계약·목록 크기·중복 ID 검사는 엄격하게 유지한다. 고정 합성 DB ID 두 개의 기존 등록 충돌 거절도 유지한다.

2026-10-13 Phase 9-18 사용자 요청: 관리자 표에서 ID를 그대로 입력해 확인하면 PostgreSQL 등록과 게시 profile만 삭제한다. 기존 등록된 `legacy`는 정리할 수 있으나 새 등록 예약 규칙은 유지한다. 등록 lock과 기존 private 경로 검증을 사용하고 symlink·Windows junction을 거절하며, live context·값 reader·목록에서 즉시 빠진다. 선택된 DB를 삭제하면 Legacy 기준선으로 돌아가고 열린 검토는 닫는다. SQLite·합성 DB·profile 전용 ID는 삭제하지 않고 평가 기록(`blind-evals`, `direct-sql-pg`)은 남긴다.

2026-10-13 Phase 9-20 사용자 결정: 새 PostgreSQL 등록에 다른 DB의 값 사전을 가져온다. 출처는 게시된 PostgreSQL 등록의 편집 사전과 서버 시작 `legacy-view`·`legacy-base` profile 사전이며, 목록에는 ID·이름·항목별 개수만 보낸다. 같은 공통 항목 중 대상의 `editable_fields`만 처리하고 실제 대상 칸에 저장 값이 있는지 확인한다. 없는 값·연결 안 된 항목·사용 중단 항목·별칭 충돌 등은 항목별 개수와 이유 코드로 보고한다. 같은 저장 값은 별칭을 중복 없이 합치고 대상 뜻을 유지한다. 검토자가 지정된 가져오기는 편집 중 사전만 저장하며, 기존 ‘사전 적용해 게시’로 반영한다. 기존 값 존재·별칭·사전 검증과 private 저장 경계를 재사용하는 관리자 편집 기능 확장으로 새 ADR은 없다.

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

2026-10-13 사용자 요청(Phase 9-15): 검토·게시 화면의 **AI 추천 모두 승인**은 검토자가 입력된 상태에서 대기(`PENDING`)·후보 있음 항목만 대상으로 한다. 확인 안내에 대상 수와 후보 없는 대기 항목 수를 표시하고, 승인하면 각 항목의 첫 후보 index를 기존 `APPROVE` API에 한 번씩 순서대로 보낸다. 실행 중 화면 버튼을 비활성화하고 `k/N 승인 중`을 표시하며, 각 응답으로 표를 갱신한다. 실패하면 즉시 멈춰 승인 수·실패 항목·기존 오류 안내를 표시한다. 완료 안내에는 후보 없는 항목 이름을 표시하며 확정·변경·없음 항목은 건드리지 않는다. 각 항목의 다시 정하기로 고칠 수 있고 게시는 자동으로 하지 않는다. 화면만 추가하며 9-06 숫자 칸·ADR-097 시간 칸 게시 규칙과 레거시 기준선은 유지한다. 기존 승인 동작 안의 편의 기능이므로 새 ADR은 없다.

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

2026-10-12 사용자 결정(ADR-098): 적응형 챗봇의 기본 모델은 일반 모델이다.
- 기본은 학습하지 않은 `qwen2.5-coder:7b-instruct`(`CHAT_MODEL`로 바꿀 수 있음)다.
- fine-tuned 모델은 "레거시 학습 모델(비교용)" 선택지로 남는다.
- 사용자가 질문마다 화면에서 고르고, 평가 도구도 `--model`로 고른다.
- 레거시 챗봇(8000)은 그대로 fine-tuned 모델을 쓴다.

2026-10-12 사용자 결정(ADR-099): `OPENAI_API_KEY`가 있으면 ChatGPT API(`OPENAI_MODEL`, 기본 `gpt-5-mini`)도 선택지가 된다.
- 비교용 외부 모델이다. 기본 모델은 바꾸지 않는다.

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

2026-10-13 Phase 9-21(사용자 결정, ADR-102): 적응형 챗봇은 한 번의 모델 호출과 기존 검증·실행 뒤, 성공 답에서 어떤 값·항목에도 연결되지 않은 질문 낱말을 최대 5개 알린다. 값 사전 별칭·기존 값/시간 힌트·QueryIR 조건과 닫힌 일반어·조사 규칙만 재사용하고 추가 조건·모델 호출·DB 읽기는 없다. 답을 막지 않으며 기존 거절과 레거시 기준선은 유지한다. 개발 30문항의 고정 조건 확인과 한계는 [결과 문서](phase9-21-unmatched-word-notice-results-2026-10-13.md)에 기록한다.

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
- [x] Phase 5-18 직접 SQL(동적 schema prompt) baseline arm과 QueryIR paired 비교 계약(평가 전용 raw SQL 정책·catalog 읽기 제한 executor·위치 무관 결과 비교, 합성 provider와 oracle 120/120 자기 일관성; 실제 모델 수치 없음)
- [x] Phase 5-19 로컬 fine-tuned tag의 IR·직접 SQL arm live paired 비교 잠정 기록(개발 세트 40×3; 모델 내장 CNU SYSTEM이 주입된 조건이라 5-20에서 정정)
- [x] Phase 5-20 Ollama 내장 SYSTEM 오염 제거(`alpaca-raw-no-system-v1`) 후 paired 재실행(IR 정답 93·SQL 정답 72, normalized에서 IR 31 대 SQL 11; L1/L3 전 확정 아님)
- [x] Phase 5-21 base instruct(`qwen2.5-coder:7b-instruct`, `model-template`) paired 실행 기록(SQL 정답 90, IR 0은 단일 code fence 출력과 arm 간 추출 규칙 비대칭 때문; 방식 비교 불가, 대칭 추출 재실행 결정 대기)
- [x] Phase 5-22 arm 간 대칭 code fence 추출(`single-fence-v1`)로 두 모델 재실행(fine-tune IR 93·SQL 72, base IR 78·SQL 90; IR은 schema 구조와 무관하게 일정, 직접 SQL은 정규화 schema에서 하락; 잠정)
- [x] Phase 5-23 주장 범위를 schema 변화에 대한 정확도 일관성으로 한정(ADR-061)하고 같은 seed 변형 `coded`·`hybrid`를 더한 `extended-v1` suite(ADR-062)로 두 모델 재실행(fine-tune IR 31·31·31·31·31(155/200)·SQL 30·11·31·8·10(90), base IR 26×5(130)·SQL 34·24·32·20·23(133); IR spread 0, 직접 SQL은 JOIN이 필요한 schema에서 하락; 잠정)
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

- [x] Phase 7-01 DBMS capability 계약과 PostgreSQL read-only schema inspector(`dbms-capability-v1`, 단일 schema·sample 없음·fake connector 검증)
- [x] `PostgresAdapter` schema inspection 구현
- [x] `pg_catalog` 기반 table/column/PK/FK 분석(실 PostgreSQL 검증은 별도)
- [x] Phase 7-02 PostgreSQL QueryIR compiler·AST policy·read-only executor(`%s`, postgres dialect, fake connector 통합)
- [x] Phase 7-03 완전 합성 PostgreSQL live integration smoke(inspect→mapping→compiler/policy/executor, 격리 schema·cleanup)
- [x] Phase 7-04 server-owned PostgreSQL profile의 adaptive registry/catalog/clarification runtime 조립
- [x] Phase 7-05 Git 비추적 private PostgreSQL startup profile strict load와 loopback CLI 주입
- [x] Phase 7-06 frontend strict registry의 PostgreSQL 선택·catalog·clarification ID 연결
- [x] Phase 7-07 확인된 clarification constraint를 기존 Ollama provider에 연결하는 합성 transport 경계
- [x] Phase 7-08 server-owned Ollama startup 설정과 명시적 provider 선택(loopback 전용 CLI option·proxy/redirect 차단 transport, synthetic transport 검증; live 호출 없음)
- [x] Phase 7-09 합성 prototype Ollama end-to-end live smoke(경계 `COMPLETED`; 모델 출력 4건 모두 reflection guard 거부, 성공 실행 경로 live 미확인)
- [x] Phase 7-10 runtime Ollama 요청 형식 명시(Ollama 활성화 시 `--ollama-request-format` 필수, config v2, 형식 단일 적용; no-system 재실행에서 성공 실행 경로 live 첫 통과)
- [x] Phase 7-11 요청 형식을 `OllamaQueryModel` 안으로 이동(생성 시점 transport fail-fast, 형식별 provenance 구분; `model-template` hash 유지)
- [x] Phase 7-12 legacy view 부분 mapping과 선언형 값 변환(profile v2, `unsupported_fields`, `SLASH_FIRST_NUMERIC`·`TIME_TO_HHMM`, view inspection opt-in, 등록·smoke CLI; legacy DB live smoke 통과)
- [x] Phase 7-13 legacy view 대상 챗봇 질의 endpoint(`adaptive-chat-query-v1`; Ollama QueryIR 1회 → 기존 실행 경로 → 서버 고정 카드 field, SQL·물리 이름 비노출, `UNSUPPORTED`·`NOT_UNDERSTOOD`, 시간 filter 값 정규화; legacy DB·로컬 Ollama live 확인)
- [x] Phase 7-14 `ChatPopup`의 DB별 경로 선택(legacy 기준선은 기존 경로 유지, postgres는 QueryIR, 그 밖의 합성 DB는 챗봇 미지원 안내), 부분 mapping catalog 409 구분, `dev:all` launcher의 legacy DSN·Ollama 재사용(browser 확인)
- [x] Phase 7-15 같은 질문의 legacy 챗봇 대 QueryIR 경로 비교 보고(개발자 작성 30문항, ONTOLOGY 범위 수용 가능 legacy 17·QueryIR 11·서버 정책 변형 13/22, 미지원 field 거절 QueryIR 3/3·legacy 0/3; L1 전 잠정)
- [x] Phase 7-16 챗봇 서버 정책 3종(limit 200 고정, 이름형 문자열 `contains`, profile 선언형 요일 정규화 `KOREAN_WEEKDAY`)과 PostgreSQL `contains` escape, 재비교(ONTOLOGY 수용 가능 QueryIR 11→14, legacy 17; L1 전 잠정)
- [x] Phase 7-17 기준 table 보존 LEFT JOIN(profile v3 `anchor_table`, anchor 모드 policy는 수식어 없는 `LEFT JOIN`만, 시작 시 전체 table 조합 경로 유일성 검사)과 legacy 기본 table 매핑 `legacy-base`(view와 전체 관계 6,846행·7-16 답변 22/22·30문항 key 집합 30/30 동일)
- [ ] 실제 CNU DB regression test
- [ ] SQL dialect 차이 처리
- [ ] 응답시간·GPU/메모리 사용량 기록

### Phase 8. 관리자 UI와 스키마 변화

- [ ] DB 등록·스캔 화면
- [ ] mapping proposal 검토 화면
- [x] 코드값 교정 화면(Phase 8-07 축소판: PostgreSQL 등록의 값 사전 편집·약어 후보·충돌 검사, ADR-082; 관계 교정은 미포함)
- [x] test query 및 결과 미리보기(Phase 8-04: 게시·활성 private 등록에서 서버 고정 test query 5개만, 기존 실행 경로·최대 5행·결과 hash; 합성 SQLite만 검증)
- [x] mapping publish·rollback 화면(Phase 8-01: 버전 이력·이전 immutable 버전 재활성화, 합성 SQLite만 검증)
- [x] schema fingerprint 비교(Phase 8-02: 등록 간 읽기 전용 비교·활성 mapping 영향, 합성 SQLite만 검증; PostgreSQL 연결은 순수 함수만 공유)
- [x] 영향받은 mapping을 `STALE` 처리(Phase 8-03: 새 등록 검토를 기준 활성 mapping 승계로 시작, 영향 field만 `STALE`·재검토; 합성 SQLite만 검증)
- [x] Phase 8-06 코드값·값 사전(`ValueDictionary`, profile v4 `value_dictionaries`, compiler·policy 공통 `bind_filter_value`, 결과 표시 이름, 챗봇 인식 값 완화 제외, 등록 시 저장 값 존재 확인; legacy 이수구분 사전으로 ONTOLOGY QueryIR 14→15)
- [x] Phase 8-08 합성 변형 benchmark(형식·코드값·의미·구조 12개 변형, `VariantMappingAnswer`, field·실행 점수, oracle 만점 검증)와 규칙 기반 mapping 기준선(field 163/180, 기준 table 1/12, 질문 205/240; oracle 180/180·240/240)
- [x] Phase 8-09 AI mapping 제안(`ai-mapping-v1.1`: 로컬 `qwen2.5-coder:7b-instruct`, schema 제한 출력, runtime 규칙 검증기, 분반 table anchor 규칙)과 규칙 기반 비교(질문 AI 223/240, 규칙 205, 규칙+anchor 206; 값 사전 0/3·의미 함정 0/2는 미해결; 합성·L1/L3 전)
- [x] Phase 8-10 AI mapping 제안 v2(저장 값 일치 검사, 값 사전 전용 단계, 관리자 column 설명 `admin-notes-v1`·코드표 직접 사전, 보류 변형 6개·사전 등록 hash; 기존 세트 223→232→235/240, 보류 세트 104→116/120; 자연어 설명은 의미 함정 미해결)
- [ ] Phase 8-11 DB별 ontology 확장 field와 매개변수형 변환(공통 ontology는 팀 관리, DB별 확장은 관리자가 데이터로 선언, 변환은 닫힌 목록을 매개변수형으로 확장; 첫 사례 이론·실습 시간; 챗봇 응답·화면의 field 목록 일반화) — 프로토타입 이후로 보류(ADR-078)
- [x] Phase 8-22 팀원 DB 평가 실행 도구(`blind_eval check|run|report`): 질문 파일 형식, 내용 비출력 형식 검사, 첫 실행 해시 잠금·실행 번호 선점·덮어쓰기 금지, loopback 전용(proxy·redirect 없음), 팀원용 안내 `docs/teammate-evaluation-db-guide.md`(ADR-093)
- [x] Phase 8-20 모르는 조건 처리: 공통 항목 밖 개념 목록(데이터 11개)과 확장 항목 대조로 `UNSUPPORTED`(`{concept, label}`), 0건이면 조건별 1행 probe로 `unmatched` 진단, 값 힌트 cache key에 mapping 버전(레거시 고정 9/9, 가상 학교 14/14)(ADR-088)
- [x] Phase 8-19 DB별 확장 항목(8-11 축소판): 관리자 선언(화면 패널), profile v5, runtime 검사, 챗봇 항목 목록·카드·값 힌트, 가상 학교 강의실(14/14)·레거시 강의실·수업방식 확인(ADR-087)
- [x] Phase 8-18 공통 항목 대상 학년(ontology 버전 2, 추가형 호환), 챗봇 카드 항목 일반화, SQLite 게시 필수 항목을 강의 목록 15개로, carry-over `NOT_IN_BASE`, 레거시 profile `target_year`(ADR-085)
- [x] Phase 8-17 다른 학교 합성 DB 화면 검증(`other_school`, 고정 질문 11/12), planner의 ontology 관계 우선, `HHMM_TEXT_TO_HHMM`, 여러 시간표 행 강의 목록, 약어 길이·게시 차단 안내(ADR-084)
- [x] Phase 8-16 등록 목록(다시 열기)·연결 신원 고정·등록 시 이름 규칙·학점 형식(`1/.5/1`), 처음부터 끝까지 browser smoke(레거시 DB 화면 등록 → 검토 → 약어 → 게시 → 챗봇 82건), 시연 절차 `docs/demo-procedure.md`(ADR-083)
- [x] Phase 8-07 축소판 값 사전 편집(코드표·약어, 규칙/AI 약어 후보, 충돌 막음·경고, 큰 column 정확 일치 확인, 게시 시 저장 값 재확인·변환 제외, 챗봇 select 와일드카드 제거; live `weekday-english` 0→2건·브라우저 확인; ADR-082)
- [ ] 후속: PostgreSQL 등록의 버전 되돌리기·test query·대기 등록 목록(업로드 SQLite DB 확장은 2026-10-02 사용자 결정으로 범위 밖, ADR-089)
- [ ] Phase 8-21 형식이 다른 PostgreSQL DB 대응 점검(공통코드 table, 교시 시간표, 한 칸에 묶인 시간표, 넓은 단일 table; 지침 `docs/prompts/phase8-21-postgres-format-variety.md`)
  - [x] D 넓은 단일 table: 코드 수정 없이 동작, 고정 질문 11/12(q11은 모델 흔들림), `wide_school` schema(2026-10-02)
  - [x] A 공통코드 table: 코드 column + 관리자 값 사전으로 q01~q12 12/12. 이름 일부("교양")로 코드 여러 개 찾기(`LIKE OR IN`, compiler·policy 공통 함수)와 공유 앞부분 힌트 추가 뒤 14/14, 레거시 SQL 불변(ADR-090, 2026-10-05)
  - [x] B 교시 시간표: 교시표 = `HH:MM` 이름의 값 사전, 시간 비교 → 교시 `IN` 목록(compiler·policy 공통 함수), 교시표 없이 정수 column에 연결한 시간 항목은 게시 차단, 고정 질문 11/13(틀린 2개는 모델 이해)(ADR-091, 2026-10-06)
  - [x] C 한 칸에 묶인 시간표(①): 닫힌 변환 `PACKED_WEEKDAY`(요일 포함 검색, 엄격한 값 형식일 때만), 시작·종료 시간은 "이 DB에 없음", 게시 때 값이 많으면 표본으로 이 변환만 판단, 13/13(시간 질문 2개는 기대대로 거절)(ADR-092, 2026-10-06). 문자열 해석으로 시간까지 꺼내는 ②는 사용자 결정 대기
- [x] Phase 8-15 화면에서 PostgreSQL 등록(서버 소유 연결 목록 `DATABASE_URL`·`PG_SOURCE_*`, schema 선택, 규칙/AI 제안, 같은 검토 API의 PostgreSQL 분기와 `IGNORE`, 게시 때 변환·기준 table·미지원 field 고정 규칙 결정, runtime 검증 뒤 profile 쌍 저장, 재시작 없이 추가하는 `LivePostgresContexts`, 재시작 시 재로드; 실제 PostgreSQL smoke 2개·브라우저 확인; ADR-081)
- [x] Phase 8-14 챗봇 실행 SQL 표시 개발 옵션(executor가 받은 문장 + bound 값 literal 보기용, 성공 답에만 `sql`, 옵션 끔이면 응답 불변, CLI `--show-sql`·launcher 기본 켬 `SHOW_SQL=0`; ADR-080)
- [ ] 후속: 공통 약어 묶음(대상 값이 있을 때만 적용), 이해 실패 질문에서 약어 후보 제안(ADR-080)
- [x] Phase 8-13 챗봇 값 힌트(DB별 값 목록 ≤500·단어 조각·별칭·조사·붙여 쓰기·fuzzy 힌트를 모델 호출 1회에 추가, ontology 기반 없는 field 이름 보정과 힌트 기반 field 이동, 약어는 DB별 값 사전 데이터; 새 질문 3→12/12, 7-15 해석 범위 15→19·레거시 17; ADR-079)
- [x] Phase 8-12 업로드 흐름에 AI mapping 제안 연결(`ai-mapping-v2` column 단계를 제품 모듈 `mapping/ai_proposal.py`로 이동·benchmark와 공유, AI 첫 후보 + 규칙 후보, `AI_PROPOSAL` 근거, 실패 시 규칙 대체, upload v2 `proposer`·review v2 `source`, 등록 threadpool 실행, `--mapping-model-tag`·launcher `MAPPING_MODEL`; 이름 변경 합성 DB live smoke AI 15/15·규칙 1/15)
- [x] verified query 자동 회귀 테스트(Phase 8-05: 서버 계산 결과 hash를 verified 기준선으로 기록, publish·재활성화 직후와 재활성화 전 비활성 버전에서 고정 query 회귀 비교; 경고만, 자동 차단 없음; 합성 SQLite만 검증)
- [x] Phase 8-01 mapping 버전 이력과 이전 버전 재활성화(`ACTIVATE`; activation만 교체, publish와 같은 실행 가능성 재검증, 최신 내용 재publish 시 최신 버전 재활성화, 활성 기록 `activated_by`·`activated_at`, frontend 2단계 확인; 합성 입력만)
- [x] Phase 8-02 등록 간 schema 비교와 활성 mapping 영향 분석(`catalog-diff-v1`·`registration-compare-v1`; table·column·FK 변화, field별 `UNCHANGED`/`TYPE_CHANGED`/`COLUMN_MISSING`/`TABLE_MISSING`, 그대로 옮긴 mapping의 compile·AST 검사 통과 여부; 파일 쓰기·실행 권한 변경·candidate SQL 실행 없음)
- [x] Phase 8-03 영향 field `STALE` 표시와 영향 없는 field 승계로 검토 시작(`CARRY_OVER`, `mapping-succession-v1` 기록; 첫 검토에만, 승계 field는 사유 있는 REMAP, 영향 field는 PENDING; publish 조건 불변)
- [x] Phase 8-04 게시·활성 mapping의 고정 test query 결과 미리보기(`admin-test-queries-v1`·`admin-test-query-result-v1`; query ID만 선택, QueryIR 검증·compiler·AST policy·read-only executor, semantic column·정렬 최대 5행·행 수·`result_sha256`, SQL·물리 이름 비노출)
- [x] Phase 8-05 verified query 기준선과 버전 회귀 비교(`verified-queries-v1`·`verified-query-regression-v1`; `MATCH`/`CHANGED`/`FAILED`/`UNVERIFIED`, 게시 digest 기록이 있는 비활성 버전도 고정 query로 확인, 행 값 저장 없음)

Phase 8 분할 계획(상세는 [`docs/prompts/phase8-01-mapping-version-rollback.md`](prompts/phase8-01-mapping-version-rollback.md)): 8-01 rollback → 8-02 schema fingerprint 비교 → 8-03 영향 field `STALE` 처리 → 8-04 publish된 mapping의 test query 미리보기 → 8-05 verified query 회귀 → 8-06 코드값·관계 교정(설계 ADR 선행) → 8-07 관리자 화면 통합. 실제 학교 DB는 L2 전 금지.

2026-09-29 개발 순서(사용자 승인, 상세는 변경 이력):

1. 7-17 기준 table 보존 LEFT JOIN과 기본 table 매핑.
2. 8-06 코드값·값 사전 설계와 구현(완료, ADR-074).
3. 합성 변형 benchmark(형식·코드값·의미·구조)와 규칙 기반 기준선 측정(완료, Phase 8-08·ADR-075).
4. AI mapping 제안과 규칙 기반 비교(닫힌 목록 제안, 관리자 검토 유지)(완료, Phase 8-09·ADR-076).
4a. 8-10 AI 제안 v2와 관리자 column 설명(완료, ADR-077).
4b. 8-11 DB별 ontology 확장 field와 매개변수형 변환(2026-09-30 프로토타입 이후로 보류, ADR-078).
4c. 8-12 업로드 흐름에 AI mapping 제안 연결(완료, ADR-078).
4d. 8-13 챗봇 값 힌트(완료, ADR-079; 사용자 요청으로 8-07보다 먼저).
4e. 8-14 챗봇 실행 SQL 표시(완료, ADR-080).
4f. 8-15 화면에서 PostgreSQL 등록(완료, ADR-081; 8-07 검토 화면을 두 DB 종류 공통으로 만들기 위해 먼저 함).
4g. 8-07 축소판 값 사전 편집(완료, ADR-082).
4h. 8-16 등록 목록·browser smoke·시연 절차(완료, ADR-083). 프로토타입 완성 단계(ADR-078) 종료.
4i. 8-17 다른 학교 합성 DB로 화면 흐름 검증(완료, ADR-084).
4j. 8-18 공통 항목 대상 학년·항목 목록 일반화(완료, ADR-085).
4k. 8-19 DB별 확장 항목(완료, ADR-087).
4l. 8-20 모르는 조건 처리(완료, ADR-088). 사용자가 정한 순서(8-18 → 8-19 → 8-20) 끝.
4m. 2026-10-02 사용자 결정(ADR-089): PostgreSQL 전용으로 개발하고, 다른 PostgreSQL DB에 대한 적응성만 범위로 삼는다. 업로드 SQLite DB 확장은 하지 않는다. 다음: 8-21 형식이 다른 PostgreSQL DB 대응 점검 → 팀원 DB 평가 실행 도구 → Phase 9.
4n. 8-22 팀원 DB 평가 실행 도구(완료, ADR-093).
4o. Phase 9 진입: L1 검토 표 준비(완료, ADR-094). 판정은 사용자 → 팀원. 다음: 사용자의 L1 판정, 팀원 모사 DB·질문 파일.
4p. 8-23 AI mapping 제안 개선(완료, ADR-095; 개발용 schema 5개 55→75/80, 개발 수치).
4q. 8-24 코드표에서 값 사전 가져오기(완료, 관리자 편의 기능, ADR 없음: 기존 값 사전 규칙 안의 입력 방법 추가).

4aa. Phase 9-09 챗봇 모델 선택지에 ChatGPT API 추가(완료, ADR-099; 키는 사용자가 `backend/.env`에 넣음, 실제 호출 측정은 사용자 시험 대기).

4ab. Phase 9-10 PostgreSQL 직접 SQL 비교군(평가 전용, ADR-100): [x] 구현·가짜 모델 targeted 검증; [x] Luna/opencode 독립 리뷰(제공된 리뷰 지적 4건 재현·고침, 9-10c); [x] live 측정(2026-10-12 사용자 승인으로 한 번, 로컬 7B; 직접 SQL 17/56·QueryIR 42/56, other_school QueryIR은 9-08 재사용; 개발 질문·L1/L3 전 잠정치, 사람 관문 승인 없음). [결과](phase9-10-direct-sql-results-2026-10-12.md).
4ac. Phase 9-11 게시된 PostgreSQL mapping 검토 응답 계약 고침(9-11b 보완): [x] review GET·INIT의 `published_at`을 초 단위 UTC `Z` 형식으로 정규화하고 활성 버전 `activatable: False` 유지; [x] 화면 parser에서 활성·재활성화 불가 조합 허용, 실행 불가 문구는 비활성 버전에만 표시; PostgreSQL 회귀 확인·ACTIVATE 미지원과 SQLite 응답·동작 유지; [x] 서버·frontend targeted 회귀 검증; [x] Luna/opencode 독립 리뷰(제공된 9-11~9-22 리뷰, 확인 지적은 9-23에서 고침).
4ad. Phase 9-12 DB 변경 관리 화면 재배치(사용자 요청): [x] 큰 창·사용할 DB/DB 등록/검토·게시 메뉴, PostgreSQL 등록 표, 항목 연결 표·선택 도구 접기·게시 영역; [x] 배치·한국어 안내만 변경, API·계약·검증 규칙·레거시 기준선 유지; [x] frontend 검증; [ ] 기존 스타일과 맞추기(다음 단계); [x] Luna/opencode 독립 리뷰(제공된 9-11~9-22 리뷰, 확인 지적은 9-23에서 고침).
4ae. Phase 9-13 PostgreSQL 시험 질의·기준 저장·회귀 확인(사용자 결정 B): [x] 게시된 현재 profile과 검증된 챗봇 context로 고정 QueryIR 실행, 없는 항목 제외·표시; [x] 등록 디렉터리에 검토자·hash·행 수만 기준 저장, 현재 게시 버전만 회귀 확인(다른 버전은 `POSTGRES_CURRENT_VERSION_ONLY`); [x] 활성 버전 `activatable: True`, 초 단위 게시 시각·SQLite 동작 유지; [x] targeted 검증(backend 51개, frontend 24개; SQLite upload setup 실패 2개는 재실행 통과); [x] Luna/opencode 독립 리뷰(제공된 9-11~9-22 리뷰, 확인 지적은 9-23에서 고침). PostgreSQL ACTIVATE·이전 버전 보관은 추가하지 않는다.
4af. Phase 9-14 검토·게시 탭 항목 연결 표 정리(사용자 요청): [x] 대기 줄의 세 동작·확정/없음 줄의 다시 정하기, 한 번에 한 줄만 편집; [x] 다른 칸 편집의 처음 값을 현재 연결 → 추천 → 빈 선택으로 고침, 빈 선택·빈 사유 저장 차단; [x] 검토자 한 번 입력·도구 공통 사용, 설명·게시 안내 정리; [x] frontend 161개·TypeScript·대상 ESLint·native config loader Vite build·diff check 통과(기본 Vite build는 Windows spawn EPERM); [x] Luna/opencode 독립 리뷰(제공된 9-11~9-22 리뷰, 확인 지적은 9-23에서 고침). API·계약·검증 규칙·레거시 기준선 유지, 스타일 통일·화면 확인은 별도.
4ag. Phase 9-15 AI 추천 모두 승인(사용자 요청, 화면만): [x] 대기·후보 있음 선택과 첫 후보 index, 확인·취소·순차 승인·진행 표시·버튼 비활성화; [x] 첫 실패에서 중단·기존 오류 안내, 응답별 표 갱신·후보 없는 항목 이름 표시; [x] JSX·한국어 인코딩·공통 항목 이름 참조 복구, 순수 선택·가짜 fetch 회귀 7개 포함 frontend 168개·TypeScript·대상 ESLint 통과; [x] Luna/opencode 독립 리뷰(제공된 9-11~9-22 리뷰, 확인 지적은 9-23에서 고침). 자동 게시 없음, 9-06 숫자 칸·ADR-097 시간 칸 게시 규칙·레거시 기준선 유지.
4ah. Phase 9-16 공통 ontology v3 개설 연도 사용 중단(ADR-101, 사용자 결정): [x] retirement 계약·v1/v2 기록 호환; [x] 등록·검토·게시·값 힌트·모델 목록·QueryIR schema·챗봇에서 year 제외, 현재 runtime의 연도 질문 거절; [x] 관련 targeted 테스트·frontend·TypeScript 검증; [x] Luna/opencode 독립 리뷰(제공된 9-11~9-22 리뷰, 확인 지적은 9-23에서 고침). gold·fixture·dataset profile·레거시 기준선·학기는 유지. [결과와 평가 영향](phase9-16-retire-offering-year-results-2026-10-13.md).
4ai. Phase 9-17 예약 DB ID와 목록 부분 실패(사용자 요청): [x] 새 PostgreSQL·SQLite 등록의 `legacy` 고정 입력 오류 거절·파일 생성 전 차단; [x] 양식 전송 전 안내·잘못된 개별 목록 항목만 제외와 경고, 최상위 계약·중복 ID 검사 유지; [x] 관련 backend 47개·frontend 29개·TypeScript·diff check 검증; [x] Luna/opencode 독립 리뷰(제공된 9-11~9-22 리뷰, 확인 지적은 9-23에서 고침). 저장된 `legacy`와 사용자 데이터·서버·레거시 기준선·기존 미커밋 작업 유지, 새 ADR 없음.
4aj. Phase 9-18 PostgreSQL 등록 삭제(사용자 요청): [x] 엄격한 DELETE API·등록 lock·등록/profile 두 폴더만 삭제·symlink/junction 거절; [x] live context·값 reader·DB/등록 목록에서 재시작 없이 제거, 기존 예약 ID legacy 정리 허용·다른 등록/평가 기록 보존; [x] 정확한 ID 확인·목록 갱신·선택 DB 삭제 시 Legacy 기준선·열린 검토 닫기; [x] 관련 targeted backend 57 passed·3 skipped(Windows symlink 권한), frontend 17 passed·TypeScript·대상 ESLint·diff check 검증; [x] Luna/opencode 독립 리뷰(제공된 9-11~9-22 리뷰, 확인 지적은 9-23에서 고침). 사용자 서버·데이터와 기존 미커밋 작업 유지.
4ak. Phase 9-20 다른 DB 값 사전 가져오기(사용자 결정): [x] 게시 등록·시작 profile 출처 목록, 대상 값 존재·편집 가능 항목·기존 별칭 검사, 별칭 합침·대상 뜻 유지·draft만 저장; [x] 출처 선택(자기 자신 제외)·공통 검토자·요약·사전 표 새로고침; [x] 관련 targeted backend 58개(실패한 기존 setup 2개 재실행 통과)·frontend 15개·TypeScript·대상 ESLint·diff check 검증; [x] Luna/opencode 독립 리뷰(제공된 9-11~9-22 리뷰, 확인 지적은 9-23에서 고침). 사용자 서버·데이터·레거시 기준선·기존 미커밋 작업 유지.
4al. Phase 9-21 연결되지 않은 낱말 알림(사용자 결정, ADR-102): [x] 닫힌 낱말 연결 규칙·성공 응답만 최대 5개 알림, 기존 호출 수·조건·검증·거절 유지; [x] 선택 필드 parser·결과 위 안내; [x] 관련 targeted backend 245개·frontend 19개·TypeScript·대상 ESLint 검증; [x] 개발 30문항 고정 판정(q12: 아직·자리, 나머지 알림 없음)·한계 기록; [x] Luna/opencode 독립 리뷰(제공된 9-11~9-22 리뷰, 확인 지적은 9-23에서 고침). [결과](phase9-21-unmatched-word-notice-results-2026-10-13.md).
4am. Phase 9-22 관리 창 스타일 통일·검토자 기억(2026-10-13 사용자 요청, frontend만): [x] 공용 버튼·입력·상자·표·상태 배지·메시지 스타일; [x] 앱 공통 localStorage 키와 읽기·쓰기 실패 허용, 하위 안내 클릭으로 맨 위 검토자 칸 스크롤·focus, 실제 비활성 조건의 title; [x] 관련 frontend 107개(새 회귀 6개 포함)·TypeScript·대상 ESLint 검증; [x] Luna/opencode 독립 리뷰(제공된 9-11~9-22 리뷰, 확인 지적은 9-23에서 고침); [ ] 화면 확인(Claude 담당). 전체 frontend 실행의 기존 registry parser 기대 불일치 6건은 이번 변경 밖이라 유지. API·동작·검증 규칙·레거시 기준선·기존 미커밋 작업 유지, 새 ADR·사람 관문 승인 없음.
4an. Phase 9-24 ontology v3 전체 테스트 정리: [x] 오프라인 평가의 기록 버전 재현·미선언 v2; [x] 현재 v3 기대 갱신·stale AI year 제거·smoke·v2→v3 carry-over 고침; [x] 최종 회귀 검증·29건 분류표(1:13/2:9/3:7; 전체 실행 후 정확한 실패 재실행 통과); [ ] Luna/opencode 독립 리뷰(사용자 no-live-model 제약으로 대기).
4z. Phase 9-08 챗봇 기본 모델을 일반 모델로, 모델 고르기(완료, ADR-098; 레거시 30문항 일반 23·학습 22, `other_school` 일반 10/12·학습 12/12, 개발 수치).
4y. Phase 9-07 게시 막힘 항목·이유 한 번에 보이기(완료; 응답에 `reasons` 추가, 화면은 항목마다 한국어 한 줄).
4x. Phase 9-06 리허설 문제 3 고침(완료, ADR-097 보완; 숫자 항목은 숫자 칸·검증된 변환일 때만 게시, 리허설 재실행 9/14·오류 0).
4w. Phase 9-05 리허설 문제 1·2 고침(완료, ADR-097; 레거시 36/36 불변, 리허설 재실행 9/14).
4v. Phase 9-04 팀원 평가 리허설 학교(완료, 개발용; `rehearsal_school` 새 저장 모양, 첫 실행 6/14, 발견 6건은 사용자 결정 대기, 코드 수정 없음).
4u. Phase 9-03 팀원용 환경 점검 명령 `python -m backend.adaptive.env_check`(완료, ADR 없음; L7 준비, L7 통과 아님). 8-25 후속: 질문 작성 가이드의 시간 문장을 QueryIR 화면에서만 바꿈(사용자 결정).
4t. Phase 9-02 L4 사용성 점검 진행 자료(완료, `docs/l4-usability/`; 진행·판단은 팀, ADR 없음).
4s. 8-25 챗봇 시간 표현 안정화(완료, ADR-096; 2026-10-06 사용자 결정으로 L1 전 예외).
4r. 팀 논의 자료(2026-10-06, `docs/team-discussion-brief-2026-10-06.md`): 만든 것, 숫자의 한계, 팀이 할 일(L1·평가 DB·L3·L4), 팀이 정할 것 6개. 결정·승인 없음. 팀 논의 전 다음 후보: 시간 표현 안정화(L1 전 예외 여부는 사용자·팀 결정), L4 진행 자료, 팀원 환경 점검 스크립트.
5a. 8-07 축소판에 약어 입력·규칙/AI 약어 후보·충돌 검사·관리자 승인과 업로드 DB의 QueryIR 챗봇 연결을 포함한다(ADR-080). 업로드 DB 부분은 ADR-089로 범위 밖.
5. 8-07 관리자 화면 통합 축소판(검토·코드표→값 사전·test query 미리보기·버전; 업로드 DB 변환·값 사전·부분 게시 포함)과 처음부터 끝까지 browser smoke·시연 절차.
6. prompt·field 별칭 개선은 L1 질문 세트 확보 뒤.
7. Phase 9 최종 평가는 L1/L3 뒤.

학년 필드 추가는 팀 결정 사항이다. 개수 요청("3개만") 반영은 보류한다.

### Phase 9. 최종 평가와 논문

- [x] Phase 9-27: 팀원 안내를 위한 앱 조립·DB 목록 모듈, 적응형 frontend 폴더·clarification 시연 화면·NER 예제 이름 정리
- [x] Phase 9-28: 팀원 첫 실행 README·환경 예시·문서 목차·인수인계·현재 시연 절차와 핵심 모듈 설명 정리(문서·주석만)
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

`backend/adaptive/ontology/course-domain.yaml`에 공통 ontology를 정의했다. 현재 버전은 3이며 `CourseOffering.year`는 `retired_in_version: 3`으로 사용 중단한다(ADR-101, 사용자 결정). `since_version`·`retired_in_version`으로 버전별 활성 항목을 정하고 버전 1·2 기록과 학기는 유지한다. 현재 entity는 `Course`, `Department`, `Instructor`, `CourseOffering`, `Schedule`이며, 각 semantic field에 한국어 별칭, 물리 컬럼명 힌트, 자료형, 허용 연산자를 기록한다.

`backend/adaptive/mapping`의 baseline aligner는 컬럼명 0.70, 자료형 호환성 0.20, 테이블명 0.10의 결정적 점수로 후보를 정렬한다. 각 후보는 구성 점수와 매칭 근거를 보존한다. 기본 상태 구분은 다음과 같다.

- 0.90 이상: `AUTO_APPROVE_CANDIDATE`
- 0.60 이상 0.90 미만: `NEEDS_REVIEW`
- 0.60 미만: `UNCERTAIN`; 해당 semantic field는 unmapped 목록에도 포함

`AUTO_APPROVE_CANDIDATE`는 자동 publish를 뜻하지 않는다. 관리자 검토·수정과 명시적인 publish gate는 다음 개발 단계다. threshold와 가중치는 향후 여러 스키마 평가에서 조정할 실험 변수다.

### 현재 개발 체크포인트와 다음 시작점

2026-10-17 Phase 9-27 현재 코드 위치: 적응형 FastAPI 앱 조립은 `backend/adaptive/api/app_factory.py`, DB 목록·course catalog는 `backend/adaptive/api/database_registry.py`, 공용 적응형 frontend는 `src/adaptive/`, clarification 시연 화면은 `src/adaptive/ClarificationDemoPage.tsx`다. NER 보정 예제는 `backend/scripts/demo_ner_correction.py`로 옮겼고 `python -m backend.scripts.demo_ner_correction`으로 실행한다. 앱 내부 state 속성의 접두사도 `adaptive_app_`로 맞췄다. 계약 버전·DB ID·HTTP/브라우저 URL·localStorage 키·저장 형식·npm 스크립트 이름은 유지하며 호환 shim은 두지 않는다. 동작 변경 없는 이름 정리라 새 ADR은 없다. 아래 날짜별 구현 기록과 ADR 본문의 당시 경로는 보존한다.

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

Phase 7-01에서 `sqlite`·`postgres`만 허용하는 불변 `dbms-capability-v1` 계약과 PostgreSQL schema inspector를 추가했다. PostgreSQL은 현재 schema inspection만 지원하며 compiler·policy·executor는 fail-closed 상태다. inspector는 server-owned DSN, PostgreSQL 12 이상, 단일 허용 schema(기본 `public`), read-only transaction, 2초 statement timeout, parameterized `pg_catalog` query, sample 미수집을 강제한다. ordinary/partitioned table, column·generated/identity, 복합 PK/FK 순서와 기존 logical affinity를 생성하며 다른 schema를 가리키는 FK는 제외한다. 오류는 입력 ID·DSN·원문 예외를 반사하지 않는다. 독립 `gpt-6-luna` high 검토의 입력 반사, cross-schema FK, shallow-frozen feature list를 메인에서 확인·수정했다. fake psycopg2-style connector와 SQLite/mapping/relation 회귀 39개·5 subtests, 전체 pytest 725개·5 skip·452 subtests, compileall, diff check가 통과했다. 실 PostgreSQL·CNU 자료·Ollama·runtime 질의·UI는 사용하지 않았다. 구현 지침은 [`docs/prompts/phase7-01-postgres-adapter-capabilities.md`](prompts/phase7-01-postgres-adapter-capabilities.md)에 둔다.

Phase 7-02에서 기존 QueryIR의 `select`·`filters`·`joins`·`limit` subset을 PostgreSQL double-quoted identifier와 psycopg2 `%s` parameter로 컴파일하는 join-capable compiler를 추가했다. `sqlglot` postgres dialect AST policy는 한 개 SELECT와 정확한 Published Mapping·join plan·projection·filter·anonymous placeholder·parameterized LIMIT만 허용한다. executor는 server-owned `PostgresConnectionConfig`, PostgreSQL 12+, read-only transaction, config/executor 중 더 짧은 statement timeout, 최대 200행, SQLSTATE `57014` timeout 분류, 성공·실패 cleanup과 고정 redaction을 강제한다. libpq connect timeout은 정수 초로 올림한다. `ExecutionHarness`의 compiler/executor 타입을 protocol로 일반화하고 PostgreSQL capability의 compiler/policy/executor를 합성 component 준비 상태로 올렸다. 독립 `gpt-6-luna` high 검토에서 실제 timeout 미적용과 주입 `HarnessFailure` redaction 우회를 확인해 수정했다. fake connector 관련 82개·69 subtests와 전체 pytest 747개·5 skip·459 subtests, compileall, diff check가 통과했다. capability 활성화는 실제 server 연결이나 registry/UI 노출을 의미하지 않으며 raw SQL fallback, 실제 PostgreSQL·CNU·Ollama는 사용하지 않았다. 구현 지침은 [`docs/prompts/phase7-02-postgres-query-runtime.md`](prompts/phase7-02-postgres-query-runtime.md)에 둔다.

Phase 7-03에서 `DEFAULT_SEED` 기반 정규화 PostgreSQL fixture와 opt-in CLI를 추가하고, 별도 로컬 PostgreSQL 18 임시 cluster에서 inspector→mapping proposal→relation graph→PostgreSQL compiler/policy/read-only executor를 두 QueryIR로 실제 연결했다. 생성 schema는 제한된 이름·quoted identifier·bound value만 사용하고 성공·실패 뒤 제거한다. live 검증에서 드러난 PostgreSQL 18 다중 배열 `unnest` 호환 문제를 `generate_subscripts` 기반 FK ordinal 결합으로 수정했고, executor는 검사한 단일 schema를 transaction-local `search_path`로 고정한다. Luna review-only의 setup 실패 cleanup과 느슨한 FK 검증을 rollback·idempotent cleanup·정확한 FK tuple 비교로 보완했다. 실제 CNU·기존 PostgreSQL service data·Ollama·registry/UI는 사용하지 않았다. 구현 지침은 [`docs/prompts/phase7-03-synthetic-postgres-live-smoke.md`](prompts/phase7-03-synthetic-postgres-live-smoke.md)에 둔다.

Phase 7-04에서 Python server code만 만들 수 있는 immutable PostgreSQL profile과 startup validation을 추가했다. profile은 redacted `PostgresConnectionConfig`와 승인된 15-field `PublishedMappingProfile`을 묶으며 startup에서 sample 없는 catalog, database ID·ontology version·schema fingerprint, table/column, 중복 physical mapping, relation graph를 검증한다. 검증된 profile은 기존 registry에 `schema=postgres`로 나타나고 catalog와 clarification이 database ID 기반 compiler/policy/executor router를 사용한다. context load와 실행 직전마다 재검사해 drift를 차단한다. 독립 Luna 검토의 compiler/policy 예외 redaction과 catalog의 executor router 우회를 수정했고, 실제 PostgreSQL 18 HTTP smoke에서 `NUMERIC`의 `Decimal` JSON 직렬화 결함을 찾아 validated DTO JSON dump로 보완했다. 기본 앱은 profile 0개·PostgreSQL 연결 0회다. 실제 CNU·Ollama·UI 변경은 없다. 구현 지침은 [`docs/prompts/phase7-04-server-owned-postgres-runtime-registry.md`](prompts/phase7-04-server-owned-postgres-runtime-registry.md)에 둔다.

Phase 7-05에서 loopback prototype CLI가 반복 가능한 database ID로 `.schema-adaptive/postgres-profiles/<id>/profile.json`과 `mapping.json`만 찾도록 고정했다. 기존 Git ignored·untracked private path guard를 재사용하고 두 파일을 256 KiB bounded read, UTF-8·duplicate key·exact shape·type, mapping SHA-256, 명시적 `PUBLISHED`, timezone-aware publish time으로 strict load한다. DSN은 제한된 이름의 별도 process environment에서만 읽고 profile·mapping·CLI 오류에는 반사하지 않는다. 성공 profile만 Phase 7-04 factory에 전달하며 option이 없으면 private 파일·환경변수·PostgreSQL 접근 0회인 기존 synthetic-only 동작을 유지한다. Luna review-only의 residual risk와 메인 독립 검토에서 공용 private reader가 제한 적용 전에 전체 파일을 읽는 문제를 확인해 하위 호환 `max_bytes` bounded read로 수정했다. 관련 51개 통과·2 skip, 전체 pytest 780개·5 skip·470 subtests, compileall, diff check가 통과했다. 실제 PostgreSQL·CNU·Ollama·frontend는 사용하거나 변경하지 않았다. 구현 지침은 [`docs/prompts/phase7-05-private-postgres-startup.md`](prompts/phase7-05-private-postgres-startup.md)에 둔다.

Phase 7-06에서 frontend strict registry의 허용 schema를 `flat | legacy | private | postgres`로 확장했다. PostgreSQL entry는 Phase 7-04와 같은 ASCII safe label만 허용하며 private DB의 한국어 label 예외는 확대하지 않는다. 메인 `DB 변경`의 기존 server-owned registry radio·명시적 적용·상태 초기화·sessionStorage 재검증을 그대로 재사용하고, 같은 database ID를 course catalog query와 prototype URL·clarification body에 전달한다. 저장된 ID가 registry에서 사라지거나 registry가 invalid/unavailable하면 legacy로 fail closed한다. DSN·환경변수 이름·host·port·physical schema/mapping/SQL을 browser state·URL·DOM에 추가하지 않았다. Luna가 제기한 JavaScript `$` trailing newline 가능성은 현재 Node에서 LF·CR·U+2028·U+2029 모두 재현되지 않아 적용하지 않았다. 관련 Node 30개, 변경 파일 ESLint, frontend build, 전체 pytest 780개·5 skip·470 subtests, compileall, diff check가 통과했다. 실제 PostgreSQL profile browser smoke·Ollama·CNU 자료는 사용하지 않았다. 구현 지침은 [`docs/prompts/phase7-06-postgres-frontend-selection.md`](prompts/phase7-06-postgres-frontend-selection.md)에 둔다.

Phase 7-07에서 기존 `OllamaQueryModel`을 clarification의 `ConstraintAwareProvider` 경계에 연결했다. 확인된 entity/field 하나에 맞춰 ontology와 Published Mapping의 semantic context만 축소하고 모델을 정확히 한 번 호출한다. 모델 출력은 보정하지 않고 기존 QueryIR 검증·compiler·AST policy·DB별 executor를 그대로 통과한다. factory가 model을 명시적으로 주입한 경우에만 이 provider를 사용하고, 기본 synthetic provider는 유지한다. 독립 `gpt-6-luna` high review-only에서 nested ontology/mapping extra key가 strict 재검증을 통과하는 결함을 확인해 exact nested key 검사와 회귀 테스트를 추가했다. 관련 113개와 전체 pytest 816개·5 skip·470 subtests, compileall, diff check가 통과했다. live Ollama·CLI 설정·raw SQL fallback·재학습·실제 PostgreSQL/CNU 자료·frontend는 사용하거나 변경하지 않았다. 구현 지침은 [`docs/prompts/phase7-07-ollama-constraint-provider.md`](prompts/phase7-07-ollama-constraint-provider.md)에 둔다.

Phase 7-08에서 loopback prototype CLI에 `--ollama-model-tag`·`--ollama-base-url`·`--ollama-timeout`을 추가했다. tag가 있을 때만 `ollama-startup-config-v1` strict 설정(127.0.0.1/[::1] loopback URL, 1~120초, `query-ir-v1` 고정)으로 기존 `OllamaQueryModel`을 조립해 Phase 7-07 factory에 주입하며, startup과 clarification start에서는 호출하지 않는다. 주입 transport는 빈 `ProxyHandler`·redirect 거부·1 MiB 응답 상한을 적용하고 오류를 고정 code 또는 원문 없는 `TimeoutError`로 바꾼다. 반복·축약·unknown option은 고정 문구로 fail closed하고, option이 없으면 factory를 인자 없이 호출한다. 상세 결과는 변경 이력에, 구현 지침은 [`docs/prompts/phase7-08-server-owned-ollama-startup.md`](prompts/phase7-08-server-owned-ollama-startup.md)에 둔다.

Phase 7-09에서 opt-in CLI `backend.adaptive.api.ollama_prototype_smoke`(`--check`|`--live-synthetic`)를 추가하고 로컬 `text2sql_qwen:latest`로 합성 case 6건을 in-process 실행했다. 경계는 `COMPLETED`(호출 4/4, start·cancel·no-match 0회, strict shape·redaction 유지)였으나 submit 4건 모두 `CONSTRAINT_INVALID_QUERY_IR`였다. 비노출 진단 결과 모델이 확인된 field 하나의 축소 context에서 질문 문자열 전체를 `equals` filter 값으로 복사했고, Phase 6-09 reflection guard가 설계대로 거부했다. guard는 완화하지 않았고 성공 실행 경로의 live 통과는 미확인으로 남긴다. 결과는 [`docs/phase7-09-ollama-prototype-live-smoke-results-2026-09-28.md`](phase7-09-ollama-prototype-live-smoke-results-2026-09-28.md), 지침은 [`docs/prompts/phase7-09-ollama-prototype-live-smoke.md`](prompts/phase7-09-ollama-prototype-live-smoke.md)에 둔다.

Phase 5-18에서 평가 전용 직접 SQL arm과 QueryIR arm의 paired 비교 계약을 추가했다(상세는 변경 이력·ADR-057, 지침은 [`docs/prompts/phase5-18-direct-sql-baseline-arm.md`](prompts/phase5-18-direct-sql-baseline-arm.md)). Phase 5-19의 첫 live 비교는 `text2sql_qwen:latest` Modelfile에 내장된 CNU `courses` schema SYSTEM이 모든 호출에 주입된 조건이었다. Phase 5-20에서 등록 TEMPLATE을 SYSTEM 없이 byte 재현하는 `alpaca-raw-no-system-v1` 요청 형식을 두 arm에 같게 적용해 재실행했고, 잠정 paired count는 IR 정답 93/120, SQL 정답 72/120(둘 다 68, IR만 25, SQL만 4, 둘 다 오답 23)이었다. flat·legacy에서는 두 arm이 거의 같고(31·30, 31·31) normalized에서 IR 31 대 SQL 11로 차이가 JOIN이 필요한 schema에 집중되었다. 결과와 한계는 [`docs/phase5-20-decontaminated-arm-comparison-results-2026-09-29.md`](phase5-20-decontaminated-arm-comparison-results-2026-09-29.md), 정정 전 기록은 [`docs/phase5-19-live-arm-comparison-results-2026-09-28.md`](phase5-19-live-arm-comparison-results-2026-09-28.md)에 둔다. Phase 7-10에서 runtime도 요청 형식을 명시하게 했다. prototype·smoke CLI는 Ollama를 켤 때 `--ollama-request-format`이 필수이고, `OllamaStartupConfig` v2가 형식을 담아 runtime model과 평가 SQL provider가 한 번만 적용한다. no-system 형식으로 7-09 smoke를 재실행해 성공 실행 경로가 처음 live로 통과했으나 `Course.name` 2건은 질문 전체를 `equals` filter로 복사해 여전히 guard에서 거부되었다(지침 [`docs/prompts/phase7-10-runtime-ollama-request-format.md`](prompts/phase7-10-runtime-ollama-request-format.md), 7-09 결과 문서 추가 기록). Phase 7-11에서 Codex 독립 검증이 찾은 두 결함(no-system 형식의 non-callable transport 지연 실패, 형식이 달라도 같은 provenance)을 요청 형식 적용을 `OllamaQueryModel` 생성자로 옮겨 닫았다(지침 [`docs/prompts/phase7-11-request-format-in-model.md`](prompts/phase7-11-request-format-in-model.md)). Phase 5-21에서 사용자 승인으로 `qwen2.5-coder:7b-instruct`를 Ollama 기본 저장소(`C:/Users/micea/.ollama/models`)에 받고 `model-template` 형식으로 두 arm을 실행했다. SQL arm은 정답 90/120(normalized 24/40)이었으나 IR arm은 0/120이었다. 비노출 진단에서 base 모델이 IR 출력 40건 모두를 단일 Markdown code fence로 감쌌고 안쪽 28건은 유효 QueryIR이었다. SQL arm 추출은 단일 fence를 벗기지만 IR arm은 벗기지 않는 비대칭 때문이므로 이 실행은 방식 비교로 쓸 수 없다([`docs/phase5-21-base-instruct-arm-comparison-results-2026-09-29.md`](phase5-21-base-instruct-arm-comparison-results-2026-09-29.md)). Phase 5-22에서 사용자 결정대로 평가 계층의 IR arm에도 SQL arm과 같은 단일 code fence 규칙(`single-fence-v1`)을 적용해 두 모델을 재실행했다. fine-tune은 IR 93·SQL 72(5-20과 동일), base는 IR 78(26/26/26)·SQL 90(34/24/32)이었다. Phase 5-23에서 모델 비교 주장을 'schema 구조·이름 변화에 대한 정확도 일관성'으로 한정하고(ADR-061, §16.4), 이름 축과 구조 축을 분리하는 합성 변형 `coded`(normalized 구조·약어 이름)와 `hybrid`(2-table 부분 비정규화)를 `extended-v1` suite로 추가했다(ADR-062, §15.3). 기본 `core-v1`은 이전과 같다. 두 모델 재실행에서 IR arm은 5개 schema 모두 같은 정답 수(spread 0)였고, 직접 SQL arm은 JOIN이 필요한 normalized·coded·hybrid에서 떨어졌다(fine-tune spread 23, base 14). 결과는 [`docs/phase5-23-schema-variation-results-2026-09-29.md`](phase5-23-schema-variation-results-2026-09-29.md)에 둔다. 다음 시작점은 base 모델의 QueryIR 출력 구조 실패(variant당 8) 진단 또는 L1 gold 검토 준비이며, 확정 수치는 L1·L3 뒤다. Phase 8-01에서 관리자 UI 첫 단계로 mapping 버전 이력과 이전 immutable 버전 재활성화를 추가했다(ADR-063). `activation.json`만 교체하고 `current.json`(최신 publish)과 mapping 파일은 바꾸지 않으며, 대상 버전은 publish와 같은 schema fingerprint·ontology·coverage·실행 가능성 검증을 다시 통과해야 한다. registry·catalog·clarification은 요청마다 activation을 strict reload하므로 다음 요청부터 재활성화 버전을 쓴다. Phase 8-02에서 새 파일을 새 database ID로 등록했을 때 기준(게시·활성) 등록과 schema를 비교하는 읽기 전용 기능을 추가했다(ADR-064). 결과는 table·column·FK 변화, 활성 mapping field별 영향, 기준 mapping을 그대로 옮겼을 때 compile·AST policy 검사 통과 여부이며 어떤 파일도 쓰지 않는다. Phase 8-03에서 새 등록의 첫 검토를 기준 등록의 활성 mapping 승계로 시작하는 `CARRY_OVER`를 추가했다(ADR-065). 8-02 비교에서 `UNCHANGED`인 field만 기준 column으로 사유와 함께 REMAP하고, 타입 변경·컬럼 없음·테이블 없음 field는 PENDING으로 두고 `succession.json`에 `STALE`로 기록한다. 승계는 draft만 바꾸며 publish 조건(모든 field 검토, 실행 가능성)은 그대로다. Phase 8-04에서 게시·활성화된 private 등록에서만 서버가 소유한 고정 test query 5개를 기존 실행 경로로 돌려 결과 일부와 hash를 보여주는 미리보기를 추가했다(ADR-066). compiler가 select field의 JOIN tree로 행 단위를 정하므로 모든 test query가 개설강좌 단위 field를 포함해, 같은 seed의 flat·coded·hybrid에서 결과 hash가 같다. Phase 8-05에서 관리자가 확인한 test query 결과를 서버가 계산한 hash로 verified 기준선에 기록하고, publish·재활성화 직후 새 활성 버전과 재활성화 전 비활성 게시 버전에서 같은 고정 query를 다시 실행해 기준선과 비교하는 회귀를 추가했다(ADR-067). 결과는 경고 정보이며 publish·재활성화를 막지 않는다. Phase 8 다음 시작점은 8-06 코드값·관계 교정의 설계 ADR과 8-07 관리자 화면 통합이며, 8-01~8-05 관리자 흐름의 browser smoke가 남아 있다. IR arm은 두 모델 모두 schema 구조와 무관하게 정답 수가 같았고 직접 SQL은 정규화 schema에서 떨어졌으며, base에서는 단일 table schema에서 직접 SQL이 더 높았다. fine-tune의 큰 IR 우위에는 CNU fine-tune이 동적 schema SQL을 약화시킨 효과가 섞인 것으로 보인다([`docs/phase5-22-symmetric-extraction-results-2026-09-29.md`](phase5-22-symmetric-extraction-results-2026-09-29.md)). 다음 시작점은 방어 가능한 주장 범위를 'schema 구조 변화에 대한 정확도 일관성'으로 좁히는 평가 설계 정리와, base 모델에 대한 QueryIR prompt 적합성(출력 shape 실패 24건) 검토이며, 확정 수치는 L1 gold 검토·L3 결과 검토 뒤다. 확정 수치는 L1 gold 검토·L3 결과 검토 뒤다.

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

### 15.1.1 Phase 9-10 PostgreSQL 직접 SQL 비교 (ADR-100)

2026-10-12 사용자 결정: 같은 등록 PostgreSQL DB에서 직접 SQL과 QueryIR을 비교한다. 직접 SQL은 등록 catalog(table·column·자료형·PK/FK·schema), column당 최대 10개 예시(값당 40자), 낮은 cardinality 글자 column의 실제 값 힌트를 받는다. 관리자 mapping·변환·값 사전(약어)은 쓰지 않는다. QueryIR은 기존 게시 mapping·값 사전·compiler 경로를 쓴다. 정확도와 함께 온보딩 비용이 다른 조건임을 명시한다.

개발 질문은 레거시 30 + `other_school` q01~q12 12 + `rehearsal_school` 14 = 56개다. QueryIR 규칙을 맞춘 개발 질문이라 A(QueryIR) 쪽에 유리하다. 학교 간 일반화·제품 구조 판정 근거는 에이전트가 미리 보지 않은 팀원 DB 첫 실행이며 L1/L3 승인을 대신하지 않는다.

평가 CLI: `python -m backend.adaptive.evaluation.direct_sql_pg run --set <legacy|other_school|blind:ID> --database ID --model general`. 같은 날 사용자 결정으로 OpenAI 측정 허용을 철회했다(추가 비용 없음; ChatGPT Plus는 API 사용량을 포함하지 않음). 비교는 로컬 `general`로만 하며 QueryIR arm은 `general`·`legacy-tuned`만 받는다. 9-08 other_school 실행의 재사용 가능한 CLI가 없어 `--arm queryir --base-url <loopback>`로 동일 채점과 지연 요약을 만든다. PostgreSQL AST 정책, READ ONLY executor·statement timeout·200행 상한, 질문당 한 모델 호출·재시도 없음을 적용한다. 원래 SQL 검사 뒤 `SELECT * FROM (<sql>) AS direct_sql_result LIMIT 200`으로 실행하고 원래 SQL hash를 유지한다. 200행도 SUCCEEDED·row_count=200·limit=200으로 기존 상한 채점에 넘긴다. 따옴표 없는 schema·table·CTE 이름은 소문자로 접고 따옴표 있는 이름은 그대로 비교한다. schema로 한정한 함수는 모두 거절하고, Anonymous 함수는 코드에 고정한 PostgreSQL 내장 함수 허용 목록만 받는다(typed 함수는 기존 금지 이름 검사 유지). wrapper는 원래 SQL 앞뒤에 줄바꿈을 두어 끝의 `--` 주석 밖에서 닫는다. 기존 SQLite arm과 제품은 유지한다.

이 CLI의 legacy 거절 규칙은 `blind_eval`과 같고(`UNSUPPORTED`·`NOT_UNDERSTOOD`), 9-08 `legacy_chat_compare` 수치와 직접 비교하지 않는다.

레거시 QueryIR의 profile과 기준 SQL 실행 연결을 따로 고를 때 `--reference-database p910-legacy`를 쓴다(질문은 `legacy-view`, 기준 SQL은 새로 등록한 public schema 연결).

기록은 `.schema-adaptive/`에 코드·개수·sha256·시간만 저장한다(SQL·모델 원문·행 값·prompt·키 없음). blind는 기존 잠금 hash·run 번호를 공유하고 `arm: direct-sql`로 구분한다. arm 없는 과거 기록은 QueryIR로 읽고 report는 arm별로 보여 준다. 지표: 맞음, SUCCEEDED 조용한 오답, 생성 실패, 정책 거절, 실행 실패, UNKNOWN 거절의 맞음/틀림, 상한 도달, 질문별 elapsed_ms와 중앙값·p90. 기존 QueryIR blind/legacy 기록은 HTTP 왕복 elapsed_ms만 추가한다. blind 지연은 ask 반환 직후 채점 전에 재며, 응답에 elapsed_ms가 있으면 그 값을 쓴다.

가짜 모델·reader·executor·전송으로 검증했고, 9-10c에서 제공된 Luna 리뷰의 지적 4건을 재현·고쳤다. live 측정은 2026-10-12 사용자 승인으로 테스트 서버 8124에서 한 번 수행했고 측정 뒤 종료했다. 로컬 `qwen2.5-coder:7b-instruct`(temperature 0)로 직접 SQL 17/56·QueryIR 42/56이며, other_school QueryIR 10/12는 9-08 기록을 재사용했다(응답 시간 없음). 레거시 QueryIR 26/30은 9-08의 정확 18·상한 부분 집합 5에 거절 정답 3을 더한 채점이다. [결과 문서](phase9-10-direct-sql-results-2026-10-12.md)에 조건·지표·실패 분류를 기록했다. 개발 질문·L1/L3 전 잠정치이며 사람 관문 승인 없음. 이번 문서 작업은 새 측정·모델 호출(Luna/opencode 포함)·서버 조작·테스트·commit 없이 제공된 결과만 기록한다. 사용자 서버 변경·레거시 DB 쓰기·새 등록 게시·관리자 작업 없음.

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

2026-09-29(Phase 5-23) 구현 상태: 같은 seed의 합성 SQLite fixture 5종을 `extended-v1` suite로 둔다(ADR-062). 이름 축(영문 snake·약어·영문 동의어)과 구조 축(단일 table·2-table 부분 비정규화·5-table 정규화)을 분리해 배치했다.

| variant | 구조 | 이름 | §15.3 대응 |
| --- | --- | --- | --- |
| flat | 단일 table | 영문 snake | 기준 |
| normalized | 5-table 정규화, FK | 영문 snake | 테이블 분리 |
| legacy | 단일 table | 약어(`sbjt_nm` 등) | 컬럼 이름 변경 |
| coded | normalized와 같은 5-table·FK | 약어(`tb_sbjt` 등) | 이름 변경 + 테이블 분리 |
| hybrid | 2-table(과목 + 분반 행에 학과·교수·시간) | 영문 동의어(`subject_title` 등) | 컬럼의 다른 테이블 이동, 테이블 병합 |

코드값 변경, FK 없는 관계, view, 컬럼 타입 변경은 ontology value mapping·FK 추론·L2 검토가 필요해 이번 fixture 범위에서 제외했다. 일관성 지표는 arm별 variant 정답 수의 최대−최소(`spread`)와 모든 variant에서 정답인 case 수(`all_variants_correct`)이며 평가 전용 `schema-consistency-v1`로 기록한다. 기본 `core-v1`(flat·normalized·legacy)의 bundle·run ID·report는 이전과 같다.

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
6. QueryIR 방식은 같은 질문의 정확도를 물리 schema 구조·이름 변화에 대해 직접 SQL 생성보다 일정하게 유지하는가? (Phase 5-23 추가, ADR-061)

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

모델 비교 주장 범위(2026-09-29, ADR-061):

- 하지 않을 주장: "QueryIR 방식이 직접 SQL 생성보다 정확하다"는 일반 우열. Phase 5-22에서 base instruct 모델은 전체 정답이 직접 SQL 90 대 IR 78이었고, fine-tune 모델의 IR 우위에는 CNU 단일 schema fine-tune이 동적 schema SQL을 약화시킨 효과가 섞여 있다.
- 방어할 주장: QueryIR 방식은 모델이 물리 schema를 보지 않으므로 **같은 질문의 정확도가 물리 schema 구조·이름 변화에 대해 일정**하고, 직접 SQL 생성은 JOIN이 필요한 schema에서 정확도가 떨어진다. 근거 지표는 variant별 정답, `spread`, `all_variants_correct`, paired count다.
- 모든 수치는 합성 fixture(개발자 작성, 같은 seed)와 L1/L3 전 gold 기준 잠정치이며, 독립 unseen schema(E4·L6) 없이 학교 간 일반화를 주장하지 않는다.

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
  - 2026-10-12 사용자 결정(ADR-099): ChatGPT 선택지로 보내는 것은 질문, 공통 항목 목록, 값 힌트(질문 단어와 맞는 DB 값)다.
  - 물리 이름·DSN·DB 행은 보내지 않는다. API 키는 서버 안에서만 쓴다.
- Phase 9-10 OpenAI 전송 예외는 같은 날 사용자 결정으로 철회했다(추가 API 비용 없음). OpenAI 측정과 직접 SQL OpenAI 경로를 제거한다. 제품 챗봇 ADR-099 범위는 그대로다.
- 교수명 등 공개 데이터도 재배포 조건 확인 후 가명화 여부 결정
- SQL, mapping version, reviewer, 실행 결과 감사 로그 유지

## 18. 주요 위험과 대응

### 위험 1. 범위 과다

대응: PostgreSQL, SQLite, 대학 수강정보, 읽기 전용 분석 질의로 초기 범위를 제한한다.

### 위험 2. 합성 스키마 편향

대응: 최종 평가에 독립적으로 작성되거나 실제 공개 자료에서 유래한 unseen schema를 포함한다.

### 위험 3. CNU 데이터 암기

대응: entity/template/schema/institution 단위로 데이터를 분리하고 가명화·다중 스키마 증강을 수행한다.

관찰(2026-10-12, Phase 9-08): 이 위험이 실제로 나타났다.
- 레거시로 미세 조정한 챗봇 모델이 리허설 학교의 "컴융 전공", "소웨 전공"을 리허설 학교에 없는 레거시 과목 코드(`1101-7019`, `1101-7001`)로 바꿨다.
- 대응(ADR-098): 적응형 챗봇의 기본을 일반 모델로 바꾸고, 미세 조정 모델은 비교용 선택지로 둔다.

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

### ADR-048: 초기 DBMS 지원은 SQLite·PostgreSQL이며 QueryIR와 capability로 실행 권한을 분리

- 날짜: 2026-09-27
- 상태: 채택
- 결정: 초기 지원 dialect는 `sqlite`와 `postgres`로 제한하고 알 수 없는 dialect는 fallback 없이 거부한다. 모델 경계는 기존 DB 독립 QueryIR을 유지하며 raw SQL runtime fallback을 두지 않는다. versioned immutable capability가 schema inspection·compiler·policy·executor 준비 상태와 QueryIR subset·행/시간 상한을 명시한다. Phase 7-01의 PostgreSQL capability는 inspector만 활성화하며, server-owned 설정의 PostgreSQL 12+ 단일 schema를 read-only·parameterized `pg_catalog` query로 sample 없이 검사한다. compiler·policy·executor가 완성되기 전 PostgreSQL 질의 실행은 허용하지 않는다.
- 이유: DB 연결 가능 여부와 안전한 질의 실행 준비 여부를 같은 플래그로 취급하면 미완성 dialect가 실행 경로에 노출된다. QueryIR을 공통 의미 경계로 두고 DBMS별 capability를 단계적으로 올리면 Ollama 재학습이나 raw SQL 신뢰 없이 dialect 차이를 adapter에서 통제할 수 있다.

### ADR-049: PostgreSQL 실행은 compiler-owned `%s` SQL과 삼중 read-only 경계를 사용

- 날짜: 2026-09-27
- 상태: 채택
- 결정: PostgreSQL runtime은 기존 DB 독립 QueryIR을 deterministic join plan과 Published Mapping으로 컴파일하며, identifier는 double quote escaping하고 filter 값과 LIMIT은 psycopg2 `%s` parameter로만 전달한다. `sqlglot` postgres dialect allowlist가 compiler output을 QueryIR·mapping·join plan과 재대조한 뒤, executor가 server-owned profile의 startup read-only option, transaction `READ ONLY`, bounded statement timeout을 겹쳐 적용한다. executor는 PostgreSQL 12+, 최대 200행, SQLSTATE `57014`만 timeout으로 분류하고 외부 connector/cursor/fetch 예외를 고정 redacted 오류로 변환한다. capability의 compiler/policy/executor `true`는 fake connector로 검증한 component 준비 상태이며 registry 노출·실서버 검증과 분리한다.
- 이유: DBMS별 raw SQL을 모델에 맡기면 dialect와 안전 정책을 검증하기 어렵고, read-only 설정 하나만으로는 잘못된 SQL·권한·timeout·결과 크기 위험을 함께 닫을 수 없다. QueryIR→compiler→AST policy→DB read-only의 독립 경계를 유지하면 SQLite와 같은 의미 계약을 PostgreSQL parameter style로 확장하면서 미완성 통합 노출을 막을 수 있다.

### ADR-050: PostgreSQL live smoke는 완전 합성 격리 schema와 명시적 opt-in으로만 수행

- 날짜: 2026-09-28
- 상태: 채택
- 결정: Phase 7-03 live smoke는 `--live-synthetic`와 전용 loopback DSN이 함께 있을 때만 실행하며 DB 이름을 `schema_adaptive_synthetic`으로 고정한다. 매 실행마다 제한된 무작위 schema를 만들고 identifier는 `psycopg2.sql.Identifier`, 값은 `%s`로 전달한다. fixture setup 실패는 rollback한 뒤 exact schema에 `DROP SCHEMA IF EXISTS ... CASCADE`를 한 번만 시도한다. inspector catalog의 table·PK·FK·sample 부재를 정확히 확인하고 동일 schema를 executor의 transaction-local `search_path`로 고정한 뒤 두 고정 QueryIR 결과를 검증한다. 공개 출력은 고정 상태·집계·fingerprint만 포함한다.
- 이유: 실제 학교 DB나 기존 service database로 첫 통합을 검증하면 데이터 사용 관문과 구현 결함 진단이 섞인다. 완전 합성 DB와 실행별 schema 격리, strict preflight, cleanup, redaction을 함께 고정하면 실제 driver/catalog/dialect 차이는 찾으면서 기존 데이터·credential·runtime registry에는 영향을 주지 않는다.

### ADR-051: PostgreSQL 실행 노출은 server-owned 승인 profile과 요청 시 drift 재검사를 요구

- 날짜: 2026-09-28
- 상태: 채택
- 결정: PostgreSQL profile은 HTTP 입력이 아니라 server Python 경계에서만 생성하며 database ID, 안전한 label, 방어적으로 복사한 connection config, 승인된 immutable Published Mapping을 묶는다. startup inspection이 ID·dialect·ontology version·fingerprint·canonical 15-field 물리 mapping·relation graph를 모두 확인한 profile만 registry에 추가한다. catalog와 clarification은 database ID router로 PostgreSQL compiler·policy·executor를 선택하며 context load와 실제 execute 직전에 fingerprint를 다시 검사한다. compiler·policy·executor·driver 예외는 fixed redacted 오류로 변환하고 기본 app에는 profile을 주입하지 않아 PostgreSQL 연결을 만들지 않는다.
- 이유: registry 표시만으로 DBMS 실행 권한을 올리거나 startup 시점 검증만 신뢰하면 client injection과 schema drift 사이에서 오래된 mapping·dialect가 실행될 수 있다. server-owned 승인 profile, DB별 runtime router, 실행 직전 재검사를 함께 적용하면 SQLite baseline을 보존하면서 PostgreSQL 실행 권한과 credential을 HTTP 경계 밖에 둘 수 있다.

### ADR-052: PostgreSQL startup은 고정 private artifact와 별도 DSN 환경변수에서만 profile을 조립

- 날짜: 2026-09-28
- 상태: 채택
- 결정: loopback prototype CLI만 반복 가능한 database ID를 받아 저장소 안의 고정 `.schema-adaptive/postgres-profiles/<id>/profile.json`·`mapping.json`을 기존 Git ignored·untracked private guard로 읽는다. 두 JSON은 256 KiB bounded read, duplicate key와 extra/coercion 거부, exact database ID, mapping SHA-256, 명시적 `PUBLISHED`를 요구한다. profile metadata에는 제한된 DSN 환경변수 이름만 두고 실제 DSN은 process environment에서 분리해 읽는다. 모든 profile이 strict load된 뒤에만 Phase 7-04 factory에 전달하며, option이 없으면 loader·environment·PostgreSQL 접근을 하지 않는다. 공개 실패는 고정 redacted 문구로 정규화한다.
- 이유: DSN과 mapping을 CLI/HTTP 입력이나 하나의 파일에 합치면 credential 노출·path injection·미승인 mapping 실행 위험이 커진다. 고정 private 경로, content hash, strict published artifact, 별도 secret source, all-or-nothing startup을 함께 요구하면 browser가 credential을 다루지 않으면서 승인 profile만 registry에 편입할 수 있다.

### ADR-053: frontend는 registry가 선언한 PostgreSQL ID만 기존 선택 흐름으로 전달

- 날짜: 2026-09-28
- 상태: 채택
- 결정: frontend strict registry는 기존 exact entry shape를 유지한 채 `schema=postgres` literal만 추가로 허용한다. PostgreSQL label은 backend profile과 같은 ASCII safe 형식을 요구하고, 항목을 하드코딩하거나 browser에서 profile·credential을 만들지 않는다. 메인 화면과 prototype은 registry membership으로 선택·복원을 재검증하고, 명시적 적용 뒤 기존 상태 초기화를 수행하며, 동일 database ID만 catalog와 clarification에 전달한다. registry에서 사라진 ID와 invalid/unavailable registry는 legacy 기준선으로 되돌린다.
- 이유: DBMS별 frontend 실행 분기를 새로 만들면 browser가 dialect·credential·mapping 권한을 갖거나 catalog와 clarification이 서로 다른 DB를 사용할 위험이 생긴다. server-owned registry ID만 기존 공통 흐름에 통과시키면 PostgreSQL 실행 권한은 backend에 남고 SQLite·legacy 상태 전이를 회귀 없이 재사용할 수 있다.

### ADR-054: Ollama는 확인된 semantic constraint 안에서 QueryIR만 생성하고 기존 실행 경계를 우회하지 않음

- 날짜: 2026-09-28
- 상태: 채택
- 결정: adaptive clarification은 server-owned factory가 명시적으로 주입한 기존 `OllamaQueryModel`을 `ConstraintAwareProvider`로 감싼다. provider는 request·ontology·Published Mapping·constraint를 strict 재검증하고, 확인된 entity 또는 field에 필요한 semantic context만 전달해 모델을 정확히 한 번 호출한다. 모델 응답은 수정하거나 raw SQL로 대체하지 않고 기존 QueryIR constraint 검증, compiler, AST policy, DB별 read-only executor에 맡긴다. provider 미주입 기본 app은 기존 결정론 provider와 network 0회를 유지한다.
- 이유: 모델이 전체 schema나 physical identifier를 보거나 DBMS별 SQL을 직접 만들게 하면 확인 선택의 의미 제한과 dialect·정책 경계가 약해진다. semantic context 최소화와 기존 실행 파이프라인 재사용은 SQLite/PostgreSQL에 같은 DB 독립 QueryIR 계약을 적용하고 provider 오류·잘못된 출력을 fail closed로 유지한다.

### ADR-055: Ollama 활성화는 loopback 전용 CLI option과 proxy·redirect 없는 transport로만 허용

- 날짜: 2026-09-28
- 상태: 채택
- 결정: loopback prototype CLI의 `--ollama-model-tag`가 있을 때만 Ollama provider를 켠다. base URL은 `http://127.0.0.1:<port>`와 `http://[::1]:<port>`만, timeout은 1~120초 10진 문자열만, prompt는 `query-ir-v1` 고정이다. option 반복·축약·unknown 인자는 고정 문구로 fail closed하고 입력을 반사하지 않는다. 설정은 환경변수·파일에서 읽지 않는다. startup은 기존 `OllamaQueryModel`에 빈 `ProxyHandler`와 redirect 거부 handler, 1 MiB 응답 상한을 가진 loopback transport를 주입해 Phase 7-07 factory에 넘길 뿐 호출하지 않는다. option이 없으면 factory를 인자 없이 호출해 결정론 provider와 network 0회를 유지한다.
- 이유: 기본 urllib 경로는 환경·시스템 proxy를 따르고 POST redirect를 외부 host GET으로 따라갈 수 있어, loopback 주소만 검사해도 질문이 담긴 prompt가 host 밖으로 나갈 수 있다. 활성화를 명시적 server CLI로 한정하고 transport 경계에서 proxy·redirect·응답 크기를 막으면 browser·HTTP가 모델 endpoint를 고를 수 없고, 기존 adapter·constraint·실행 경계를 바꾸지 않고도 live 통합 전에 설정 경계를 합성 검증할 수 있다.

### ADR-056: live smoke는 경계 판정과 모델 출력 관찰을 분리하고 guard를 결과에 맞춰 완화하지 않음

- 날짜: 2026-09-28
- 상태: 채택
- 결정: prototype live smoke의 `COMPLETED`는 호출 예산, strict 응답 shape·고정 오류 taxonomy, canonical 성공 column, redaction만으로 판정한다. 성공 수와 `columns_match_constraint`는 관찰값으로 따로 보고하며, Phase 6-09 계약(선택 field가 select 또는 filter에 쓰이면 준수)보다 좁은 기준을 경계에 넣지 않는다. live 모델 출력이 reflection guard 등 기존 검증에서 거부되어도 guard나 prompt를 같은 단계에서 바꾸지 않고, 원인은 원문을 남기지 않는 구조 분류로만 기록한다.
- 이유: 경계 검증과 모델 품질을 한 상태값에 섞으면 모델이 틀릴 때 안전 경계 결함처럼 보이거나, 반대로 경계를 맞추려고 guard를 완화하게 된다. 두 축을 분리하면 live 통합의 안전성은 확정하면서 모델·prompt 개선은 L1/L3 관문을 거치는 평가 단계에서 비교 가능한 방식으로 다룰 수 있다.

### ADR-057: 직접 SQL baseline은 평가 전용 경로에서 동일 분모·위치 무관 결과 비교로만 QueryIR과 짝짓는다

- 날짜: 2026-09-28
- 상태: 채택
- 결정: §15.1 "기존 모델 + 동적 schema prompt" 비교군은 `backend/adaptive/evaluation`의 평가 전용 모듈에서만 raw SQL을 실행한다. prompt(`direct-sql-v1`)는 fixture Catalog의 table·column·PK·FK와 질문만 담고 도메인 힌트·sample·gold를 넣지 않는다. 출력 추출은 fence·label·끝 `;`만 제거하고 SQL을 보정하지 않는다. 정책은 단일 read-only SELECT, 조상 WITH 범위의 CTE 또는 catalog table만 허용하고, 실행은 기존 read-only executor에 catalog table 읽기 allowlist를 더한 평가 전용 executor로 한다. 결과는 column 이름 없이 NULL을 구별한 정렬 값 tuple의 행 multiset으로 gold와 비교한다. paired 비교는 benchmark·gold·seed·variant·catalog fingerprint·oracle 여부·전체 schedule·결정적 request ID가 모두 맞을 때만 만들며 discordant count만 기록한다. runtime 모듈은 이 경로를 import하지 않는다.
- 이유: QueryIR 방식의 효과를 주장하려면 같은 모델·질문·fixture·분모에서 직접 SQL 방식과 짝지어 비교해야 한다. 동시에 raw SQL 실행이 runtime으로 새거나, 추출·비교 규칙이 한 arm에 유리하게 보정되거나, 서로 다른 run이 짝지어지면 비교 자체가 무의미해진다. 평가 전용 격리, 보정 없는 추출, 방어 2중화, 엄격한 pairing identity로 이 위험을 닫는다.

### ADR-058: 평가용 Ollama 호출은 요청 형식을 명시하고 모델 내장 SYSTEM을 prompt에 섞지 않는다

- 날짜: 2026-09-29
- 상태: 채택
- 결정: Ollama 평가 호출은 `model-template`(등록 TEMPLATE·SYSTEM 적용)과 `alpaca-raw-no-system-v1`(`raw: true`, 등록 TEMPLATE을 SYSTEM 빈 값으로 byte 재현) 중 하나를 명시하고, 한 비교의 모든 arm에 같은 형식을 쓴다. 기본값은 이전 run 재현을 위해 `model-template`으로 두되, 비교 결과로 쓰는 run은 내장 SYSTEM을 제거한 형식을 사용한다. 형식이 다른 run은 label·run ID prefix로 구별해 짝지어지지 않게 한다. 등록 TEMPLATE·SYSTEM·parameter는 `ollama show`와 blob byte로 확인하고 token 수 비교로 주입 여부를 검증한다.
- 이유: `text2sql_qwen`의 내장 SYSTEM은 CNU `courses` schema를 담고 있어, 명시하지 않은 호출은 동적 schema 비교를 오염시키고 모델 암기처럼 보이는 잘못된 해석을 만든다(Phase 5-19). prompt 버전만 고정하고 서버 측 template·system을 확인하지 않으면 재현 가능한 비교가 아니다.


### ADR-059: runtime Ollama 호출은 요청 형식을 명시해야만 활성화한다

- 날짜: 2026-09-29
- 상태: 채택
- 결정: 요청 형식 모듈을 runtime harness로 옮기고 `OllamaStartupConfig` v2에 `request_format`(`model-template`|`alpaca-raw-no-system-v1`)을 둔다. loopback prototype과 smoke CLI는 Ollama를 켤 때 형식을 반드시 명시해야 하며 누락·빈 값·알 수 없는 값·반복은 고정 문구로 거부한다. 형식은 `build_startup_ollama_model`과 평가 SQL provider에서 config로부터 정확히 한 번만 적용하고, 호출자는 transport를 미리 감싸지 않는다. 모든 모델에 no-system을 기본값으로 두지 않는다. 기존 호출자를 위한 parser 기본값(`model-template`)과 5-19/5-20 평가 label·run ID는 유지한다.
- 이유: `alpaca-raw-no-system-v1`은 등록 TEMPLATE이 Alpaca인 모델에서만 유효하므로 암묵적 기본값은 다른 모델의 prompt를 깨뜨리고, 반대로 `model-template` 암묵 기본값은 `text2sql_qwen`의 CNU schema SYSTEM을 runtime에 계속 섞는다. 운영자가 `ollama show`로 확인한 형식을 명시하게 하면 두 위험을 모두 드러내고, 단일 적용 지점은 이중 감싸기와 arm 간 불일치를 막는다.


### ADR-060: 요청 형식은 모델 생성자에서 한 번 적용하고 provenance에 선언한다

- 날짜: 2026-09-29
- 상태: 채택
- 결정: `OllamaQueryModel`이 `request_format`을 받아 원본 transport callable 검사 뒤 생성자에서 한 번만 적용한다. `apply_request_format`은 형식과 무관하게 원본 transport를 먼저 검사한다. `declared_provenance`의 generation config는 `model-template`일 때 기존 값을 그대로 유지하고, 그 외 형식은 형식 ID·`raw`·재현 template SHA-256을 추가해 서로 다른 hash를 갖게 한다. URL·질문·prompt·SYSTEM 원문은 넣지 않는다. startup builder는 transport를 감싸지 않고 형식을 모델에 넘긴다.
- 이유: 형식을 transport wrapper에서만 적용하면 wrapper가 잘못된 원본 transport를 가리고, 모델이 형식을 몰라 오염 조건과 no-system 조건의 recording을 같은 provenance로 기록하게 된다(Codex 독립 검증). 적용 지점과 선언 지점을 모델 하나로 모으면 두 문제를 함께 닫고, `model-template` hash 유지로 5-15 paired 계약과 기존 bundle 호환을 지킨다.

### ADR-061: 모델 비교 주장은 schema 변화에 대한 정확도 일관성으로 한정한다

- 날짜: 2026-09-29
- 상태: 채택
- 결정: QueryIR 방식과 직접 SQL 생성의 비교에서 일반 정확도 우열을 주장하지 않는다. 주장은 같은 질문에 대한 정확도가 물리 schema 구조·이름 변화에 대해 얼마나 일정한가로 한정하고, arm별 variant 정답·`spread`·`all_variants_correct`와 paired count로 기록한다. 이 비교를 위해 fixture를 이름 축과 구조 축이 분리되도록 늘린다(ADR-062). 연구 질문 6과 §16.4에 반영한다.
- 이유: Phase 5-22에서 IR arm은 두 모델 모두 세 fixture에서 정답 수가 같았지만(31·31·31, 26·26·26) base 모델 전체 정답은 직접 SQL이 높았다(90 대 78). 일반 우열 주장은 모델·prompt 탐색 이력에 따라 뒤집히지만, schema 변화에 대한 불변성은 QueryIR 설계(모델이 물리 schema를 보지 않고 compiler가 JOIN을 만든다)에서 직접 따라 나오며 연구 주제인 schema 적응성과 맞는다.

### ADR-062: 확장 fixture는 두 고정 suite로만 선택하고 core 기본 동작을 바꾸지 않는다

- 날짜: 2026-09-29
- 상태: 채택
- 결정: 같은 seed의 새 SQLite 변형 `coded`(normalized 구조 + 약어 이름)와 `hybrid`(2-table 부분 비정규화 + 영문 동의어 이름)를 추가한다. fixture bundle의 variant 집합은 `core-v1`(flat·normalized·legacy)과 `extended-v1`(core + coded·hybrid) 두 suite 중 하나와 정확히 같아야 하며, builder 기본값은 core다. runner는 bundle suite를 다시 확인하고 설정 variant가 bundle에 없으면 실패한다. 새 변형의 Published Mapping은 평가 전용 `fixture_mapping`에 명시적으로 두고 catalog와 대조한다. run report schema version은 유지하고(literal 값 확장만), case cross-schema consistency는 3개 이상 variant 설정에서만 계산한다. 평가 CLI는 `--fixture-suite`로 고르고, extended run은 label `.ext`·run ID prefix `p523-ext`로 core run과 섞이지 않는다. runtime registry·prototype·capture target은 core 그대로다.
- 이유: 기존 3종은 이름 축과 구조 축이 섞여 있어 직접 SQL의 normalized 하락이 JOIN 때문인지 이름 때문인지 분리할 수 없다. 임의 variant 조합을 허용하면 run 간 분모·consistency가 달라져 비교가 흔들리므로 고정 suite만 허용하고, core 기본값을 유지해 5-19~5-22 결과와 recording 재현성을 지킨다.

### ADR-063: mapping rollback은 activation 기록만 이전 immutable 버전으로 바꾸고 publish 수준으로 재검증한다

- 날짜: 2026-09-29
- 상태: 채택
- 결정: 관리자 rollback은 새 mapping을 만들지 않고 `activation.json`이 이미 게시된 `mapping-vNNNN.json` 중 하나를 가리키게 한다. 대상은 `current.json`이 게시 시점에 기록한 버전별 digest(`published_versions`)에 있는 버전만이고 파일 bytes가 그 digest와 같아야 하며(중단된 publish의 orphan·교체된 파일 거부, 8-01 이전 포인터는 포인터 버전만 인정), 등록 schema fingerprint·현재 ontology version·canonical 전체 coverage·catalog 실행 가능성을 publish와 같은 기준으로 다시 통과해야 한다. mapping 파일·`current.json`·등록 파일은 바꾸지 않는다. 같은 버전 재활성화는 파일을 쓰지 않는다. 최신 draft 내용을 다시 publish하면 새 버전을 만들지 않고 최신 버전을 재활성화해 "publish하면 최신 검토 결과가 실행된다"를 유지하며, 중단된 activation 기록도 이 경로로 복구한다. activation 기록에 선택 필드 `activated_by`·`activated_at`을 두고 이전 기록(필드 없음)은 그대로 읽되 명시적 null은 거부한다. HTTP는 `ACTIVATE` strict body와 publish와 같은 고정 응답만 쓰고, 상세 응답에는 버전 메타데이터(게시자·게시 시각·활성·최신·재활성화 가능)만 넣는다. frontend는 버전 선택 뒤 별도 확인 버튼과 검토자 입력이 있어야 요청한다. 시작된 clarification session은 시작 시점 mapping을 보관하므로, private DB compile 직전에 session mapping의 버전·field가 현재 활성 mapping과 같지 않으면 `MAPPING_NOT_PUBLISHED`로 거부한다.
- 이유: 이미 검토·게시된 immutable 버전을 가리키는 방식은 새 검토 없이 실행 권한을 만들지 않으면서 잘못된 publish를 즉시 되돌릴 수 있다. 재검증을 빼면 ontology 변경·파일 변조·게시되지 않은 중단 파일이 실행될 수 있고, `current.json`까지 바꾸면 publish idempotency와 최신 검토 이력이 섞인다.

### ADR-064: schema 변화는 새 등록과 기준 활성 등록의 읽기 전용 비교로 드러내고 자동 승계하지 않는다

- 날짜: 2026-09-29
- 상태: 채택
- 결정: 등록 불변성을 유지하기 위해 schema가 바뀐 DB는 새 database ID로 등록하고, 관리자가 기준(게시·활성) 등록을 골라 비교한다. 비교는 catalog 구조 metadata(table, column의 declared type·affinity·nullable·PK 위치, FK)만 이름 정확 일치로 대조하고 이름 변경은 추정하지 않는다. sample 값·default SQL은 읽거나 반환하지 않는다. 활성 mapping의 field마다 영향 상태를 계산하고, 모든 field가 존재하고 ontology version이 같을 때만 기준 mapping을 candidate ID·fingerprint로 옮겨 canonical coverage·compile·AST policy 검사를 수행해 `carry_over_executable`로 보고한다. 비교는 등록·검토·activation artifact를 바꾸지 않고(strict reload가 기존처럼 만들었다 지우는 임시 검증 사본만 생긴다) 실행 권한을 바꾸지 않으며 candidate에서 SQL을 실행하지 않는다. 결과를 근거로 한 승계·`STALE` 표시는 8-03에서 명시적 검토 행위로 다룬다.
- 이유: 기존 등록 파일을 교체하면 불변 등록·검토 이력·activation 계약이 깨진다. 비교를 읽기 전용으로 분리하면 영향 범위를 먼저 확인하고, 영향 없는 field 승계와 영향 field 재검토를 별도 검토 단계에서 설계할 수 있다. 자동 승계는 FK·타입·의미 변화가 있는 경우 잘못된 실행 권한을 만들 수 있다.

### ADR-065: schema가 바뀐 등록은 영향 없는 field만 사유와 함께 승계하고 영향 field는 STALE로 재검토한다

- 날짜: 2026-09-29
- 상태: 채택
- 결정: 관리자가 기준(게시·활성) 등록을 명시적으로 골라 새 등록의 **첫** 검토를 시작할 때만 승계를 허용한다. 8-02 비교에서 column 이름·declared type·affinity·nullable이 모두 같은 field만 기준 활성 mapping의 table·column으로 REMAP하고 `carried over from <base> mapping-vN; schema unchanged` 사유와 요청자를 남긴다. 타입 변경·컬럼 없음·테이블 없음 field는 PENDING으로 두고 `succession.json`(`mapping-succession-v1`: 기준 ID·활성 버전·mapping SHA-256·fingerprint, 승계·STALE field)에 기록한다. ontology version이 다르면 승계하지 않는다. 검토 행위가 있는 draft·게시된 등록·다른 기준으로의 재승계는 거부하고, 같은 요청 재시도는 idempotent이며 draft만 쓰이고 기록이 없는 중단 상태는 같은 요청으로 완성한다. 승계는 draft만 바꾸고 publish 조건과 실행 권한 경계는 그대로이며, 기록은 표시용이라 손상 시 생략되고 실행에 영향을 주지 않는다.
- 이유: 전체 재검토는 변화가 작은 schema에서 관리자 작업량을 불필요하게 늘리고(연구 질문 3·4), 자동 승계·자동 publish는 의미 변화가 있는 column에 잘못된 실행 권한을 줄 수 있다. 구조가 같은 field만 명시적 행위로 승계하고 나머지를 STALE로 드러내면 작업량을 줄이면서 검토 책임과 publish gate를 유지한다.

### ADR-066: 관리자 결과 미리보기는 서버 고정 test query를 활성 mapping에서만 기존 실행 경로로 돌린다

- 날짜: 2026-09-29
- 상태: 채택
- 결정: 관리자 test query는 서버 코드에 고정된 filter 없는 QueryIR 5개(`admin-test-queries-v1`)이고 브라우저는 query ID만 고른다. 대상은 strict activation load가 성공하는 게시·활성 private 등록뿐이며, 고정 IR을 돌려주는 `FakeModel`과 기존 QueryIR 검증·`SchemaAdaptiveSQLiteCompiler`·`SchemaAdaptiveAstPolicy`·활성 DB만 가진 read-only executor를 거친다. 응답은 semantic column 이름, 값 기준으로 결정적 정렬한 최대 5행, 전체 행 수(200 상한), NULL·타입을 구분한 전체 결과 SHA-256이며 SQL·물리 이름·경로는 없다. compiler가 select field의 JOIN tree로 행 단위를 정하므로 모든 query는 개설강좌 단위 field를 포함한다.
- 이유: 임의 QueryIR·SQL 입력을 허용하면 관리자 화면이 raw 질의 통로가 되고, 미게시·비활성 mapping 실행은 publish gate를 우회한다. 고정 query와 기존 실행 경로만 쓰면 게시된 mapping의 실제 동작을 안전하게 확인할 수 있고, 행 단위를 고정한 결과 hash는 버전 간·schema 간 회귀 비교(8-05)의 기준이 된다.

### ADR-067: verified 기준선은 서버 계산 hash만 기록하고 회귀는 비활성 게시 버전에도 고정 query로만 실행한다

- 날짜: 2026-09-29
- 상태: 채택
- 결정: 관리자 VERIFY 요청은 query ID와 검토자만 보내고, 서버가 활성 버전에서 고정 query를 실행한 결과 hash·행 수·버전을 `verified-queries.json`(`verified-queries-v1`, query당 최신 1개, 행 값 없음)에 원자적으로 기록한다. 회귀는 모든 고정 query를 대상 버전에서 실행해 기준선과 비교하며(`MATCH`·`CHANGED`·`FAILED`·`UNVERIFIED`), 대상은 활성 버전 또는 `current.json` 게시 digest 기록에 있고 bytes가 같으며 재활성화 검사를 통과하는 비활성 버전이다. 비활성 버전 실행은 관리자 회귀의 고정 query에 한하고 activation·사용자 질의 경로는 바꾸지 않는다. 회귀 결과는 경고이며 publish·재활성화를 자동으로 막거나 되돌리지 않는다. 손상된 기준선은 없는 것으로 보고 명시적 VERIFY가 교체한다.
- 이유: 클라이언트가 hash를 보내면 기준선이 실제 실행 결과와 무관해질 수 있다. 재활성화 전에 대상 버전을 확인하려면 비활성 게시 버전 실행이 필요하지만, 이미 검토·게시되고 digest로 고정된 mapping에 고정 query만 허용하면 새 실행 권한이나 raw 질의 통로가 생기지 않는다. 자동 차단은 의도된 mapping 변경(예: 컬럼 교정)도 막으므로 판단을 관리자에게 남긴다.

### ADR-068: legacy 챗봇 비교는 같은 view에 부분 mapping하고 값 차이는 닫힌 목록의 선언형 변환으로 흡수한다

- 날짜: 2026-09-29
- 상태: 채택
- 결정: legacy 챗봇의 대상 view `v_course_info`를 PostgreSQL private profile v2(`postgres-runtime-profile-v2`)로 QueryIR 경로에 등록한다. 구성은 다음과 같다.
  - mapping과 미지원: canonical 15개 중 12개를 mapping하고, 대응 column이 없는 3개(`CourseOffering.year`·`semester`·`Department.code`)는 명시적 `unsupported_fields`로 둔다. mapping ∪ 미지원 = canonical이고 둘은 겹치지 않아야 한다. v1 profile은 여전히 15개 전체를 요구한다.
  - 값 변환: 표현 차이는 `FieldTransform` 닫힌 목록으로만 처리한다. `SLASH_FIRST_NUMERIC`(`CAST(SPLIT_PART(col,'/',1) AS NUMERIC)`)과 `TIME_TO_HHMM`(`TO_CHAR(col,'HH24:MI')`)이며, 식은 코드의 고정 template에서만 나온다.
  - 변환 적용 조건: ontology 타입·column 선언 타입과 모두 호환되어야 하고, 비교 연산자만 허용한다.
  - AST policy: 변환 위치의 subtree가 mapping column에 선언 변환을 적용해 파싱한 AST와 정확히 같을 때만 허용한다. 그 subtree만 노드 allowlist에서 제외하고, 그 밖의 함수·Cast·literal은 계속 거부한다.
  - inspector: `include_views`를 명시적으로 켤 때만 view를 포함한다. 기본 query와 fingerprint는 바뀌지 않는다.
- 이유:
  - legacy 챗봇과 같은 view를 쓰면, 두 경로의 결과 차이가 질문 이해 방식에서만 생긴다.
  - 기본 table에 mapping하면 LEFT JOIN이 없어 시간표 없는 강좌(632행)가 빠진다. 이는 별도 단계(B)로 둔다. (Phase 7-17에서 해결: ADR-073, `legacy-base` profile.)
  - 값 형식 차이는 실제 DB에 흔하다. 하지만 모델이나 관리자가 SQL 식을 쓰게 하면 policy가 검증할 수 없다. 닫힌 목록과 exact AST 비교는 검증 가능성을 유지하면서 표현 차이를 흡수한다.
  - 미지원 field를 명시 목록으로만 허용해야 누락 mapping과 의도된 미지원이 구분된다.
- 한계:
  - column 구성이 같은 채 view 정의만 바뀌면 fingerprint가 바뀌지 않는다.
  - 이 mapping은 팀이 구성한 legacy DB에 대한 개발자 작성 mapping이며, 실제 학교 DB의 L2 검토가 아니다.
  - AI가 변환을 제안하는 기능은 이후 단계다.

### ADR-069: 챗봇 경로는 모델의 filter·limit만 쓰고 표시 column은 서버 고정 카드 field로 정한다

- 날짜: 2026-09-29
- 상태: 채택
- 결정: `POST /api/adaptive/v1/chat/query`는 검증된 PostgreSQL context에서만 질문 하나를 처리하며, 모델 호출은 정확히 1회다. 처리 순서는 다음과 같다.
  1. 모델 출력 QueryIR의 구조를 검증한다.
  2. ontology에 없는 field를 참조하면 `NOT_UNDERSTOOD`다.
  3. profile의 미지원 field를 참조하면 실행 없이 `UNSUPPORTED`와 field label을 돌려준다.
  4. filter 값이 질문 원문을 포함하거나, 요청 경계 문자열과 같거나, 물리 identifier를 단어로 포함하면 `NOT_UNDERSTOOD`다.
  5. 표현할 수 없는 변환 filter 값(초가 0이 아닌 시간, `오후` 같은 비시각 문자열)은 `NOT_UNDERSTOOD`다.
  6. 통과한 QueryIR은 select만 매핑된 canonical 카드 field 전체로 바꾸고, entity·filter·limit은 그대로 기존 harness(검증·compiler·AST policy·read-only executor)로 실행한다.
  - 응답: canonical 카드 key, 행, 그리고 이해한 filter의 label·연산자·값이다. SQL과 물리 이름은 보내지 않는다.
  - 변환 계약 확장: `TIME_TO_HHMM` filter 값은 compiler와 policy가 같은 규칙으로 `HH:MM`으로 정규화하고, 표현할 수 없는 값은 양쪽 모두 거부한다.
- 이유:
  - legacy 챗봇은 view의 모든 column을 조회해 카드로 보여준다. 새 경로도 표시 column을 같게 두어야 결과 비교가 filter 해석 차이로 좁혀진다.
  - 표시 column 선택은 화면 규칙이므로 모델에 맡길 이유가 없고, 서버 고정 목록은 filter를 추가하거나 바꾸지 않는다.
  - understood filter는 SQL 대신 "어떻게 이해했는지"를 보여주는 설명이다. 모델 값을 그대로 되돌려주므로, 물리 이름·질문 반사를 실행 전에 막아야 한다.
  - 시간 값을 분 단위 text로 비교하는 계약에서 초 단위·비시각 값을 문자열로 비교하면 결과가 조용히 틀린다.
- 한계:
  - 모델이 문자열 field에 `equals`로 구절 전체를 넣는 경향(예: `김 교수님`)이 남아 0행이 나온다. legacy prompt는 문자열 비교에 LIKE를 강제한다. 서버 완화 규칙이나 prompt 변경 여부는 7-15 비교 뒤 사용자 결정으로 둔다.

### ADR-070: 챗봇 경로는 선택된 DB 종류로 정하고, dev:all은 legacy backend의 .env를 같은 DB·모델에 한해 재사용한다

- 날짜: 2026-09-29
- 상태: 채택
- 결정:
  - `ChatPopup`은 `DB 변경` 선택에 따라 경로를 정한다.
    - Legacy 기준선: 기존 `/api/v1/chat/query`
    - registry `schema === 'postgres'`: QueryIR endpoint
    - 그 밖의 합성·private SQLite: "챗봇 미지원" 안내만 한다. legacy DB로 질의하지 않는다.
  - 대상 DB가 바뀌면 진행 중 요청을 layout effect에서 동기 무효화한다. 두 경로 모두 요청 시점 대상과 다른 응답은 버린다.
  - 부분 mapping PostgreSQL의 catalog는 DB 조회 없이 409 `SYNTHETIC_CATALOG_UNSUPPORTED`로 답한다. 화면은 이 code일 때만 챗봇 사용 안내를 보인다.
  - `npm run dev:all`은 `backend/adaptive/api/dev_launcher.py`를 쓴다.
    - launcher는 `backend/.env`의 `DATABASE_URL`, `OLLAMA_BASE_URL`, `OLLAMA_MODEL`만 읽는다.
    - private legacy-view profile이 있고 DSN 환경변수가 비어 있으면, DSN을 프로세스 안에서만 그 변수로 넘긴다.
    - Ollama는 loopback일 때만 no-system 형식으로 켠다.
    - profile 오류, DB 검증 실패, 모델 구성 실패는 각자 해당 부분만 끄고, 고정 상태 한 줄만 출력한다.
    - 기존 `dev:adaptive-prototype`은 바꾸지 않는다.
- 이유:
  - legacy 챗봇과 비교하려면 두 경로가 같은 DB와 같은 모델(`text2sql_qwen`)을 써야 한다. legacy backend가 이미 소유한 설정을 재사용하면 팀원이 별도 환경변수를 맞출 필요가 없다.
  - 합성 DB를 골라도 legacy DB로 질의하던 기존 동작은 사용자가 지적한 혼동의 원인이었다.
  - catalog 미지원과 일시 오류를 같은 code로 두면, 완전 mapping DB의 장애가 안내 문구에 가려 재시도가 사라진다.
- 한계:
  - launcher는 개발 편의 경로이며 배포 경로가 아니다.
  - 부분 mapping DB의 목록 화면은 없다.
  - QueryIR 경로의 limit(모델 기본 50)과 문자열 `equals` 경향은 7-15 비교 뒤 정한다.

### ADR-071: legacy 대 QueryIR 비교는 개설강좌 key 집합으로 채점하고, 서버 정책은 같은 모델 출력의 재실행으로 평가한다

- 날짜: 2026-09-29
- 상태: 채택
- 결정:
  - 같은 질문을 두 챗봇에 보내고, 답을 개설강좌 key(과목 코드, 분반) 집합으로 줄여 개발자 작성 기준 SQL의 key 집합과 비교한다.
  - 기준 SQL은 고정 형태만 허용한다: view 하나에서 `DISTINCT` key만 `WHERE`로 고르는 단일 SELECT이며, AST로 검증한다.
  - 결과 상한에 도달했을 때만 정답 부분집합을 수용한다(`CORRECT_SUBSET`). 상한 없이 빠진 결과는 `INCOMPLETE`다. legacy의 상한은 실행된 SQL의 마지막 LIMIT로 읽는다.
  - DB에 없는 field 질문은 거절이 정답이다.
  - limit·문자열 비교 같은 서버 정책 후보는 모델을 다시 부르지 않는다. 같은 모델 출력(이해한 filter)을 기존 `answer_chat_query` 경로로 재실행해 평가한다.
  - 원자료는 Git 비추적 저장소에만 두고, 요약은 원자료에서 다시 채점한다.
- 이유:
  - 두 경로의 표시 행 수와 행 단위(시간표 확장 여부)가 달라, 행 비교는 공정하지 않다.
  - 상한 도달 여부를 무시하면, 조건을 빠뜨린 답이 정답 일부로 집계된다(Luna 지적).
  - 정책 후보를 모델 재호출로 평가하면 모델 변동과 정책 효과가 섞인다.
- 한계: 질문·기준은 개발자 작성이라 L1 전 잠정치다. 상한 안의 우연한 일치는 여전히 부분집합으로 보일 수 있다.

### ADR-072: 챗봇 표시 규칙은 서버가 정하고, DB별 값 표기 차이는 profile 선언형 변환으로 흡수한다

- 날짜: 2026-09-29
- 상태: 채택(사용자 결정, 7-15 비교 뒤)
- 결정:
  - QueryIR 챗봇 경로는 결과 limit을 서버 상한 200으로 고정한다.
  - 과목명·학과명·교원명·이수구분의 `equals`를 `contains`로 바꾼다. 재작성은 원본 QueryIR의 구조 검증·미지원 판정·반사 guard 뒤에 하고, 결과는 기존 검증·compiler·AST policy·executor를 다시 거친다.
  - 요일 표기는 DB마다 다르므로 전역 규칙으로 두지 않는다. 대신 profile이 선언하는 닫힌 목록 변환 `KOREAN_WEEKDAY`로 둔다.
    - SQL은 identity이고, filter 값만 한 글자 요일로 정규화한다. 모르는 값은 거부한다.
    - 변환별 허용 연산자를 둔다: 요일은 `equals`·`not_equals`·`contains`, 기존 변환은 비교 연산자만.
  - PostgreSQL `contains`는 `\`·`%`·`_`를 escape한다(PostgreSQL LIKE 기본 escape 문자 사용).
- 이유:
  - 모델의 limit은 prompt 예시값(50)을 그대로 따랐다. 표시 행 수는 화면 규칙이다.
  - 사용자는 학과명·과목명을 부분으로 입력한다(`수학과` → `자연과학대학 수학과`).
  - 7-15의 같은 모델 출력 재실행으로 효과를 먼저 측정했고(11→13→14 추정), 적용 뒤 재비교가 추정과 일치했다(14).
  - 요일 정규화를 profile 선언으로 두면, 다른 표기를 쓰는 DB에 잘못 적용되지 않는다.
- 한계:
  - "3개만" 같은 개수 요청은 반영하지 않는다.
  - `contains` 완화로 `전공`이 `전공(핵심)`·`전공(기초)`를 모두 포함하는 것은 의도된 동작이다.
  - SQLite 경로의 `contains` escape는 이번 범위 밖이다.

### ADR-073: 결과 행의 기준 table(anchor)을 profile이 선언하면 모든 JOIN을 anchor에서 시작하는 LEFT JOIN으로 쓴다

- 날짜: 2026-09-29
- 상태: 채택(사용자 결정, 개발 순서 1번)
- 결정:
  - private PostgreSQL profile `postgres-runtime-profile-v3`는 v2에 필수 `anchor_table`을 더한다. v1·v2는 바꾸지 않는다.
  - anchor가 있으면 planner는 anchor를 항상 required table에 넣고 계획 시작점으로 고정한다. compiler는 모든 단계를 `LEFT JOIN`으로 쓴다.
  - AST policy는 anchor 모드에서 수식어 없는 `LEFT JOIN`만, 비 anchor 모드에서 기본·`INNER JOIN`만 허용한다. anchor는 compiler와 policy가 같은 profile 값에서 받아 계획을 각각 세운다.
  - 시작 검증은 anchor가 매핑된 table인지, 매핑된 table(최대 8개)의 모든 부분집합에서 anchor 고정 계획이 하나로 정해지는지 확인한다. 아니면 fail closed.
- 이유:
  - INNER JOIN만 쓰면 선택적 관계(시간표 없는 강좌 등)의 행이 오류 없이 사라진다. legacy 기본 table에서는 6,846행 중 632행이 빠지고 7-16 답변 22개 중 6개가 달라졌다.
  - "결과 한 행 = anchor 한 행"은 view 정의가 표현하던 의미를 profile 선언으로 옮긴 것이다. 모든 단계를 LEFT로 두면 nullable 정보 없이도 anchor 행 보존이 보장된다. FK 무결성이 있으면 부모 방향 LEFT는 INNER와 같다.
  - anchor 고정은 같은 table 쌍을 잇는 다른 경로(필수과목 table 등)로 생기는 모호성도 없앤다. 시작 시 전체 부분집합을 확인해 질의 시점의 모호성 실패를 막는다.
- 한계:
  - anchor는 자동 추론하지 않는다.
  - FK 무결성이 깨진 부모 방향 행은 NULL로 남는다.
  - ORDER BY가 없어 행 상한에 걸리면 부분집합이 달라질 수 있다.
  - SQLite 경로는 INNER 그대로다.

### ADR-074: DB마다 다른 값 목록은 사람이 검토한 field별 값 사전으로 흡수하고, 값 전체 일치만 저장 값으로 바꾼다

- 날짜: 2026-09-30
- 상태: 채택(개발 순서 2번)
- 결정:
  - `ValueDictionary`는 저장 값·표시 이름·다른 표기의 닫힌 대응표다.
    - 조회 key는 NFKC·casefold·공백 제거로 만들고, 한 key는 한 저장 값으로만 이어진다.
    - 사전에 없는 값은 `pass`(그대로) 또는 `reject`(거부)로 처리한다.
  - filter 값은 compiler와 AST policy가 같은 `bind_filter_value`로 정한다. 값 전체가 key일 때만 바꾸며 부분 치환은 없다.
  - 결과 행의 저장 값은 표시 이름으로 바꿔 보여준다.
  - 챗봇 동작:
    - 이해 조건은 표시 이름으로 보여준다.
    - 사전이 인식한 값에는 부분 일치 완화를 하지 않는다.
    - `reject` 사전에 없는 값은 `NOT_UNDERSTOOD`다.
  - 선언은 private profile `postgres-runtime-profile-v4`(v2 + 비울 수 있는 `anchor_table` + `value_dictionaries`)에 둔다.
    - 시작 검증: 매핑된 문자열 field, 값 변환과 겹치지 않음, 문자열 계열 column.
    - 시작 검증은 DB 값을 읽지 않는다. 대신 등록 CLI가 저장 값이 실제 column에 있는지 읽기 전용으로 확인한다.
- 이유:
  - 규칙형 변환(ADR-068·072)은 형식이 규칙적인 값만 다룬다. 이수구분처럼 값 목록 자체가 DB마다 다른 경우(`전공(핵심)`, `01`)는 사람이 검토한 대응표가 필요하다.
  - 값 전체 일치만 바꾸면 policy가 기대 값을 결정적으로 다시 계산할 수 있다. 부분 치환은 의도하지 않은 검색어 변형을 만든다.
  - 인식된 값은 정확히 한 저장 값을 뜻한다. 이를 부분 일치로 완화하면 짧은 코드가 긴 코드와 겹친다.
  - 이 형식은 이후 AI mapping 제안의 출력 형식이기도 하다. AI 제안도 사람 검토 뒤 이 사전으로만 반영된다.
- 한계:
  - 1:N 표기(`전공` → 세 값)는 다루지 않는다.
  - 관리자 화면 편집과 SQLite·게시 mapping 계약 반영은 8-07이다.
  - FK 없는 관계 선언은 별도 단계다.
  - legacy 사전은 개발자 선언이며 L2 검토가 아니다.

### ADR-075: mapping 제안은 합성 변형 benchmark의 field 점수와 실행 점수로 평가하고, 정답 답안의 만점을 benchmark 자체의 검증 조건으로 둔다

- 날짜: 2026-09-30
- 상태: 채택(개발 순서 3번, 사용자 승인으로 로컬 전용 DB `schema_adaptive_synthetic` 생성)
- 결정:
  - `variant-benchmark-v1`은 하나의 seed(개설강좌 12개)를 12개 변형으로 저장한다.
    - 축: 구조 3, 형식 2, 코드값 3, 의미 3, 복합 1.
    - 정답(위치, FK 사본, 변환 허용 집합, 값 사전, 기준 table)은 변형 spec의 column 주석에서 자동으로 만든다.
  - mapping 답안 형식 `VariantMappingAnswer`: field별 위치·변환·값 사전과 기준 table. AI 제안도 이 형식으로 낸다.
  - 점수는 두 가지다.
    - field 점수(오프라인).
    - 실행 점수(live): gold QueryIR 20개를 답안으로 만든 runtime profile과 기존 실행 경로로 돌려, compiler와 독립된 seed 참조 평가와 행 multiset을 비교한다.
  - 실행이 무효가 되는 조건: 정답 답안(oracle)이 field·실행 모두 만점이 아닌 경우, 정리 실패, 오류. 이때 결과로 쓰지 않는다.
  - 규칙 기반 기준선은 현재 aligner를 바꾸지 않고 쓴다(신뢰도 순 배정, 중복 column 회피, 0.60 미만 미지원).
- 이유:
  - AI 제안을 보기 전에 평가 기준을 고정해야 benchmark를 AI에 맞추는 일을 막을 수 있다.
  - field 점수만으로는 틀린 부분이 실제 답에 미치는 영향을 알 수 없다. 실행 점수만으로는 원인을 알 수 없다. 그래서 둘을 함께 쓴다.
  - oracle 만점 조건은 변형·정답·참조 평가·실행 경로 사이의 불일치를 드러낸다.
- 결과(`vb-run1`):
  - oracle: field 180/180, 질문 240/240.
  - 규칙: field 163/180, 기준 table 1/12, 질문 205/240.
  - 규칙이 틀린 이유는 네 가지다: 변환·값 사전·기준 table을 제안하지 않음, 이름이 같지만 뜻이 다른 column을 구분하지 못함.
- 한계:
  - 합성·개발자 작성이다.
  - 약어 변형의 이름 절반은 ontology hint에 이미 있다.
  - 규칙은 sample 값을 쓰지 않는다.
  - 자연어 이해는 평가하지 않는다.

### ADR-076: AI mapping 제안은 모델 제안 → runtime 규칙 검증기 → 고정 anchor 규칙으로 만들고, 파이프라인은 실행 전에 버전으로 고정한다

- 날짜: 2026-09-30
- 상태: 채택(개발 순서 4번)
- 결정:
  - 모델: 로컬 `qwen2.5-coder:7b-instruct`, loopback transport, temperature 0, JSON schema 제한 출력.
  - 호출 구성: column 선택을 변형당 1회, 값 처리(닫힌 목록 변환, 표준 값 목록이 있는 field의 값 사전)를 매핑된 field마다 1회 부른다.
  - 검증기: 출력은 schema를 만족해야 한다. 변환·사전은 runtime과 같은 규칙과 같은 사전 모델로 확인하고, 통과하지 못한 부분만 버린다.
  - 호출 실패나 schema 위반이 하나라도 있는 제안은 채점하지 않고 다시 실행한다.
  - 기준 table: 분반 table 규칙. 같은 규칙을 붙인 `rules+anchor`와 비교해 기여를 나눈다.
  - 표준 값 목록 `canonical-values-v1`은 제안기 입력으로만 둔다. ontology는 고치지 않는다.
  - 파이프라인은 실행 전에 버전으로 고정한다. 결과를 보고 바꾸면 새 버전으로, 보류 변형과 함께 평가한다.
- 이유:
  - 7B 모델이 한 번에 전부를 내면 형식 오류가 쌓인다. 단계를 나누고 schema enum으로 선택지를 실제 column·닫힌 목록·표본 값으로 제한하면, 모델은 판단만 하고 형식은 보장된다.
  - 검증기가 runtime 규칙을 미리 적용해야 한 field의 오류가 변형 전체의 profile 거부로 번지지 않는다.
  - 같은 benchmark로 파이프라인을 반복 튜닝하면 benchmark에 맞춘 결과가 된다.
- 결과(v1·v1.1 같은 답안):
  - 질문 정답: AI 223/240, 규칙 205, 규칙+anchor 206, oracle 240.
  - AI는 형식(40/40)·약어 이름·legacy-like에서 나아졌다.
  - 값 사전은 0/3(빈 사전)이다.
  - 영어 요일에 `KOREAN_WEEKDAY`를 골랐다.
  - 의미 함정 두 개(학년·강의실 정원)는 하나도 피하지 못했다.
- 한계:
  - 모델 하나, 한 번 실행.
  - AI는 저장 값 예시와 표준 값 목록을 받고 규칙은 받지 않는다.
  - 합성 benchmark다.

### ADR-077: AI 제안 v2는 저장 값 일치 검사와 값 사전 전용 단계를 쓰고, 관리자 설명은 사유가 있는 column에만 받으며 보류 변형과 사전 등록으로 평가한다

- 날짜: 2026-09-30
- 상태: 채택(사용자 결정: v2 → 8-11 → 8-07)
- 결정:
  - v2 검증기는 변환이 저장 값 예시와 맞을 때만 받는다(한 글자 요일, `숫자/숫자…`, 예시 없음은 불가).
  - 값 사전은 표준 값 목록 밖 예시가 있을 때만 별도 호출로 받는다. 예시 값마다 표준 값을 하나씩 고르게 한다.
  - 관리자 column 설명(`admin-notes-v1`)은 사유(`ABBREVIATION`·`CODE`·`PACKED`·`MISLEADING`)가 있는 column에만 쓴다.
    - 설명은 prompt에 데이터로 넣는다.
    - 코드표의 뜻이 표준 값과 정확히 같고 표가 완전하면 모델 없이 사전으로 쓴다.
  - 파이프라인을 고친 뒤의 평가는 실행 전에 고정한 보류 변형과 함께 한다. 고정 시점의 파일 hash를 기록한다.
- 이유:
  - 8-09 실패 중 값 표기 차이는 값 예시로 확인할 수 있다. 확인 가능한 것은 모델 판단에 맡기지 않고 검증기로 걸러야 한다.
  - 같은 benchmark로 반복 개선하면 benchmark에 맞춘 결과가 된다.
- 결과:
  - 질문 정답:
    - 기존 세트: v1.1 223 → v2 232 → v2+설명 235/240.
    - 보류 세트: v1.1 104 → v2 116, v2+설명 116/120. 규칙은 0(과목 코드 미매핑).
  - 코드값 축은 두 세트 모두 만점이다.
  - 자연어 설명은 column 선택의 의미 함정(학년, 강의실 정원, 주당 시간)을 하나도 고치지 못했다. 효과는 코드표 직접 사전(+3)뿐이다.
- 결론: 관리자 입력은 자유 문장보다 구조화된 형식(코드표, 제외 표시, 형식 선택)이 결정적으로 반영된다. 8-07 관리자 화면은 구조화 입력을 우선한다.
- 한계:
  - 모델 하나, 한 번 실행.
  - 보류 변형은 8-09 실패 유형을 본 뒤 설계했다.
  - 설명은 개발자가 규칙에 따라 썼다.
  - 모든 질문이 과목 코드를 select해 실행 점수가 식별 field에 민감하다.

### ADR-078: 합성 benchmark 개선을 멈추고 프로토타입을 완성하며, 업로드 DB의 mapping 초안은 로컬 모델의 column 선택을 첫 후보로 둔다

- 날짜: 2026-09-30
- 상태: 채택(사용자 결정: 프로토타입 우선, 이론·실습 시간 제외)
- 배경:
  - 8-08~8-10은 원래 table을 변형한 합성 benchmark 점수를 올리는 데 집중했다.
  - 사용자 지적으로 일부 함정이 비현실적이었음을 확인했다.
    - `capacity`는 보통 수강 정원인데, 함정 변형에서는 강의실 좌석으로 두었다.
    - `credit_hours`는 기존 세트에서는 `학점/이론/실습` 문자열, 보류 세트에서는 "주당 수업 시간 합계"로 세트마다 뜻이 달랐다.
  - 그래서 ADR-077의 "자연어 설명은 의미 함정을 못 고친다"는 근거는 현실적인 `course_year` 2건으로 줄어든다. 코드표 효과는 영향이 없다.
- 결정:
  - 범위 조정:
    - benchmark 개선, 새 보류 세트, 8-11 일반화 설계, prompt 개선, 추가 모델 비교는 프로토타입 이후로 미룬다.
    - 순서: 8-12 업로드 AI 제안 연결 → 8-07 축소판 관리자 화면 → 처음부터 끝까지 browser smoke와 시연 절차.
  - 8-12 설계:
    - 업로드 시 `ai-mapping-v2` column 단계만 한 번 호출한다. prompt 함수는 benchmark와 같다.
    - AI 선택은 첫 후보가 되고 규칙 후보가 뒤따른다.
    - confidence는 같은 column의 규칙 점수를 그대로 쓴다. AI 선택만으로는 자동 승인 후보가 되지 않는다.
    - 근거에 `AI_PROPOSAL`을 더한다.
    - 모델 실패·schema 위반·계약 불일치는 규칙 제안으로 대체하고 등록은 계속한다.
    - 저장 값 예시는 prompt에만 쓰고 저장·응답하지 않는다.
    - 업로드 DB의 변환·값 사전·부분 게시는 8-07에서 다룬다.
- 이유:
  - 프로토타입의 부족한 부분은 benchmark 점수가 아니라, AI 제안이 제품 흐름에 없고 관리자 화면이 연결되지 않은 점이다.
  - 제품 prompt와 benchmark prompt를 하나로 두어야 측정한 것과 쓰는 것이 같다.
- 결과:
  - 이름이 낯선 합성 DB(flat fixture 열 이름 변경) 1회 live smoke: AI 첫 후보 15/15, 규칙 첫 후보 1/15, 호출 13초.
  - AI 첫 후보만 승인해 게시하면 원래 flat DB와 같은 강의 목록이 나온다(fake model 회귀 테스트).
- 한계:
  - live smoke는 개발자가 만든 DB 1개, 1회 실행이며 성능 주장이 아니다.
  - 모델이 고른 column이 변환(예: `3/3/0`)이 필요한 경우에도 업로드 경로는 column만 게시하므로 관리자가 걸러야 한다.

### ADR-079: 챗봇은 등록된 DB의 실제 값으로 질문을 해석하도록 돕되, 방식은 공통 코드로·약어는 DB별 데이터로 둔다

- 날짜: 2026-09-30
- 상태: 채택
- 사용자 결정:
  - 기능하게 만드는 것이 먼저다. 실제 학교 데이터 노출 방지는 당분간 고려 대상에서 뺀다.
  - 모델을 특정 DB에 묶지 않는 원칙은 유지한다.
- 배경:
  - 레거시 DB 챗봇이 "컴융 전공", "학과공통" 같은 질문을 풀지 못했다.
  - 모델 prompt에는 field 이름만 있고 DB 값이 없었다. 모델은 레거시 fine-tuning 기억으로 없는 field(`Course.department`)를 지어냈다.
  - 레거시 시스템은 NER + 코드에 박힌 약어 표 + DB 값 fuzzy 매칭으로 이를 처리했다.
- 결정:
  - 등록된 DB에서 값이 500개 이하인 text field의 값을 읽는다(읽기 전용, 고정 SQL 한 모양, 첫 질문 때, fingerprint별 캐시).
  - 질문을 저장 값·단어 조각·별칭·조사 제거·붙여 쓰기 분리·긴 단어 fuzzy로 검사해 `표현 → field·값` 힌트를 만들고, 한 번의 모델 호출에 넣는다.
  - 모델 출력은 두 가지 닫힌 규칙으로만 고친다. 조건은 새로 만들지 않는다.
    - ontology 이름 힌트로 하나로 정해지는 없는 field 이름을 고친다.
    - 힌트로 하나로 정해지는 값의 field를 옮긴다.
  - 보정 뒤에도 기존 검증 경로를 모두 거친다.
  - 약어·별칭은 DB별 값 사전(ADR-074) 데이터로만 둔다. 레거시 약어 표는 레거시 profile로 옮겼다. 공통 모듈에 DB 값이 없음을 테스트로 고정한다.
  - 값 읽기에 실패하면 별칭 힌트만 남기고, 60초 뒤 다시 읽는다.
- 이유:
  - 약어는 규칙으로 알아낼 수 없고, DB마다 다르다. 코드나 모델 재학습에 넣으면 그 DB에 묶인다.
  - 실행 시점 데이터로 주면 DB가 바뀌어도 코드·모델이 그대로다.
- 결과(개발자 질문, 잠정):
  - 새 질문 12개: 도입 전 3 → 보정만 6 → 힌트+보정 12. 약어 데이터를 빼면 7.
  - 7-15 비교 30문항: 해석 범위 수용 가능 15 → 19(레거시 17). 해석 범위 질문의 퇴보는 없다.
- 영향과 한계:
  - 저장 값이 로컬 모델 prompt에 들어간다. 실제 CNU 자료에 쓰기 전 E3 판단이 필요하다(기록만 하고 막지 않는다).
  - DB 값 속 지시문이 모델에 영향을 줄 수 있다. 출력 검증은 모양만 막는다.
  - 새 DB의 약어는 관리자 입력(8-07)이 필요하다.

### ADR-080: 챗봇 실행 SQL은 서버 개발 옵션으로만 보여 주고, DB별 약어는 자동 후보 + 관리자 승인으로 채운다

- 날짜: 2026-09-30
- 상태: 채택(사용자 결정)
- 결정 1: 실행 SQL 표시
  - 레거시 챗봇처럼 QueryIR 경로도 실행 SQL을 볼 수 있어야 결과를 끝까지 확인할 수 있다.
  - 서버 옵션(`--show-sql`, dev launcher는 기본 켬·`SHOW_SQL=0`으로 끔)을 켰을 때만 성공 답에 `sql`을 붙인다.
  - 보여 주는 것은 executor가 실제로 받은 문장에 bound 값을 SQL literal로 채운 보기용 문장이다. 실행은 계속 parameter binding이다.
  - 옵션을 끄면 응답은 이전과 같다. 기존의 "브라우저에 SQL을 보내지 않는다" 원칙은 옵션이 꺼진 상태의 기본으로 남는다.
- 결정 2: DB별 약어
  - 약어는 학교·커뮤니티마다 달라 DB별 데이터로 둔다(ADR-079).
  - 관리자 부담은 단계적으로 줄인다.
    - 공통 방식(값 조각·오타)이 약어 없이도 상당 부분을 푼다.
    - 8-07에서는 약어 입력 화면, 규칙 후보(단어별 첫 글자)와 로컬 AI 후보, 충돌 검사(다른 값·약어·일반 단어와 겹침), 관리자 승인을 만든다.
    - 후속: 여러 학교가 함께 쓰는 공통 약어 묶음(대상 값이 있을 때만 적용). 이해 실패·0건 질문의 모르는 단어로 약어 후보 제안(질문 기록 저장이 필요하므로 개인정보 판단 필요).
- 영향: 옵션을 켜면 브라우저에 물리 table·column 이름과 값이 보인다. 외부 시연·실제 자료 사용 전에는 끄거나 L5/E3 판단이 필요하다.

### ADR-081: PostgreSQL은 서버 소유 연결 목록에서 화면으로 등록하고, 게시하면 실행 중인 서버에 바로 붙인다

- 날짜: 2026-09-30
- 상태: 채택(사용자 결정: 접속 정보는 서버 연결 목록 방식, 8-15/8-07 순서는 개발자 판단)
- 결정:
  - 연결 목록은 `backend/.env`에서 만든다. `DATABASE_URL`은 `default`, `PG_SOURCE_<NAME>`은 `<name>`이다. 브라우저에는 연결 ID와 이름만 보낸다. 저장 profile에는 DSN 환경변수 이름만 둔다.
  - 등록은 읽기 전용 구조 확인 + 규칙/AI 제안이다. 결과는 immutable private 저장, strict reload.
  - 검토 API 경로와 응답 모양은 SQLite와 같게 두고, 서버가 등록 종류로 나눈다. 새 동작 `IGNORE`로 없는 field를 빼고 게시할 수 있다(PostgreSQL). SQLite 게시 조건은 그대로다.
  - 변환·기준 table·미지원 field는 게시 때 고정 규칙으로 정한다. 모델은 쓰지 않는다.
  - 실제 연결로 기존 runtime 검증을 통과한 profile만 쓴다. 두 파일은 함께 바뀌거나 이전 상태로 돌아간다.
  - 검증된 context는 모든 router가 같은 참조로 보는 추가 전용 집합(`LivePostgresContexts`)에 넣는다. 서버 DB 수 상한을 지킨다.
  - 재시작 때 launcher가 게시된 등록을 다시 붙인다.
- 순서 판단: 8-15를 8-07보다 먼저 했다. 8-07 검토 화면(코드표·약어·변환 선택·부분 게시)을 SQLite·PostgreSQL 공통으로 한 번에 만들기 위해서다.
- 한계: 값 사전 입력, 버전 되돌리기, test query, view 등록은 8-07 이후다.

### ADR-082: 값 사전은 관리자가 화면에서 편집하고, 약어 후보는 규칙·AI가 내되 충돌 검사와 승인을 거쳐 게시 때 적용한다

- 날짜: 2026-09-30
- 상태: 채택(사용자 결정: 8-07 진행, 약어는 자동 후보 + 관리자 승인 ADR-080)
- 결정:
  - 범위: 화면 등록 PostgreSQL DB의 코드표(`stored`→`label`)와 약어(`aliases`)를 등록 폴더의 `dictionaries.json`에서 편집하고, 게시 때 profile v4 `value_dictionaries`로 넣는다.
  - 편집 대상은 runtime 규칙과 같다: 승인·재지정된 문자열 field, text column, 식별 field 제외. 사전이 있는 field에는 변환을 붙이지 않는다.
  - 후보는 한국어 공통 규칙(2글자 묶음의 첫 글자)과, 요청할 때만 부르는 로컬 AI(schema 제한 출력)로 만든다. 후보는 저장하지 않는다.
  - 충돌 검사: 다른 저장 값·단어 조각·다른 항목 표기와 같으면 막는다. 다른 text 값 속에 들어 있으면 경고한다. 목록에 없는 큰 column은 정확 일치 조회로 확인한다.
  - 게시 때 저장 값 존재를 다시 확인한다. 확인할 수 없으면 막는다.
  - 챗봇 보정에 알려진 개체의 select 와일드카드 제거를 더했다. select는 고정 카드로 바뀌므로 결과는 달라지지 않는다.
- 이유: 코드값과 약어는 규칙으로 알 수 없는 DB별 지식이다(ADR-079). 관리자 입력을 구조화하고 기계적으로 검사해야 확실히 반영된다(8-10 결론).
- 한계: SQLite 업로드 DB는 아직 대상이 아니다. 규칙 후보에는 어색한 것도 섞인다.

### ADR-083: 화면 등록 PostgreSQL은 등록 때의 연결 신원에 묶고, 프로토타입은 처음부터 끝까지 browser smoke와 시연 절차로 마무리한다

- 날짜: 2026-09-30
- 상태: 채택(사용자 결정: 진행)
- 결정:
  - 등록 기록에 연결 신원(비밀번호를 뺀 host·port·DB·user의 SHA-256)을 넣는다. 게시와 재시작 때 다르면 `SOURCE_CHANGED`로 막는다.
  - PostgreSQL 등록 이름은 runtime 이름 규칙(영문·숫자·공백·`()_.-`)을 등록 때 바로 적용한다.
  - 등록 목록으로 검토를 다시 연다. 손상되었거나 게시할 수 없는 등록은 목록에서 뺀다.
  - 시연은 `docs/demo-procedure.md`의 10단계로 한다. 외부 시연 전 `SHOW_SQL=0`과 E3/L5 판단을 둔다.
- 이유: 같은 연결 이름이 다른 DB를 가리키면, 구조 fingerprint만으로는 다른 DB에 게시되는 것을 막지 못한다. 게시 단계에서야 막히는 이름은 관리자에게 막다른 길이다.
- 결과: 레거시 DB를 화면에서 등록해 챗봇으로 질문하는 흐름을 브라우저로 끝까지 확인했다("컴융 전공 과목" 82건). smoke에서 이름 규칙·학점 형식 결함 2건을 찾아 고쳤다.

### ADR-084: 레거시가 아닌 합성 학교 DB로 화면 흐름을 검증하고, 드러난 결함은 어느 DB에나 해당하는 규칙으로만 고친다

- 날짜: 2026-09-30
- 상태: 채택(사용자 결정: 진행)
- 결정:
  - 로컬 합성 DB `schema_adaptive_synthetic`에 레거시와 이름·저장 방식이 다른 가상 학교 schema `other_school`을 만들어 시연용으로 남긴다.
  - 질문 12개와 정답은 실행 전에 생성기 module에 고정한다.
  - join planner는 길이가 같은 계획이 여러 개일 때만 개입한다. 모든 FK가 같은 entity이거나 ontology에서 직접 관계인 entity를 잇는 계획이 정확히 하나면 그것을 고른다. 그 밖에는 여전히 모호하다고 보고 막는다.
  - 닫힌 변환 목록에 `HHMM_TEXT_TO_HHMM`(4자리 글자 시간)을 추가한다. 저장 값 표본이 모두 맞을 때만 붙는다.
  - 강의 목록은 한 강좌의 여러 시간표 행을 받는다. 요일·시간 외 값이 모두 같아야 하며, 받은 행은 강좌 하나로 합친다.
  - 약어 길이 오류(`ALIAS_LENGTH`)와 runtime 게시 차단은 원인에 맞게 안내한다.
  - 이 schema의 이름이나 값은 코드에 넣지 않는다. 질문 하나를 맞히기 위한 규칙("1학년 대상")도 넣지 않는다.
- 이유: 화면에서 끝까지 확인한 DB가 챗봇 모델의 fine-tuning 대상인 레거시뿐이라, "특정 DB에 묶이지 않는다"는 주장을 확인할 수 없었다.
- 결과:
  - 결함 4건을 찾아 고쳤다. 막히던 게시, 글자 시간 비교, 주 2회 수업 목록 실패, 잘못된 안내다.
  - AI 첫 후보는 15개 중 13개가 맞았다.
  - 고정 질문은 11/12를 맞혔다. 남은 1개는 ontology에 없는 조건이다.

### ADR-085: 공통 ontology는 버전을 올려 항목을 더하기만 하고, 옛 버전 기록은 그대로 유효하다

- 날짜: 2026-10-01
- 상태: 채택(사용자 결정: 대상 학년 추가 → DB별 확장 → 모르는 조건 처리 순서로 진행)
- 결정:
  - 공통 ontology 버전 2에 `Course.target_grade`(대상 학년)를 추가한다. 항목에는 `since_version`을 단다.
  - 버전 1로 만든 dataset, 평가 세트, draft, 게시 mapping은 유효하다(`accepts_version`, `fields_at`, `at_version`).
  - 옛 mapping은 자기 버전의 항목 집합으로 검사한다. 그 뒤에 생긴 항목은 "이 DB에 없음"으로 본다(챗봇 `UNSUPPORTED`).
  - 서버보다 새 버전의 mapping은 거부한다.
  - 강의 목록은 15개 항목 계약을 유지한다. 챗봇 카드는 ontology 순서로 매핑된 항목을 보인다.
  - SQLite 업로드 게시는 강의 목록 15개만 필수다.
  - carry-over는 기준에 없던 항목을 `NOT_IN_BASE`로 기록하고, 기준 버전 ≤ 새 등록 버전이면 허용한다.
  - 레거시 개발자 profile은 대상 학년을 `target_year`에 연결한다.
- 이유:
  - "1학년 과목"은 흔한 질문이고 레거시 챗봇도 답했다.
  - 레거시와 8-17 가상 학교 모두 이 정보가 있다.
  - 버전을 올릴 때 옛 기록이 모두 무효가 되면 항목 하나 추가도 큰 이전 작업이 된다.
- 결과: 레거시 "컴융 3학년 전공 과목" 33건, 가상 학교 고정 질문 12/12.
- 한계와 팀 공유:
  - 이미 화면 등록한 PostgreSQL DB의 draft 올리기는 만들지 않았다. 새 ID로 다시 등록한다.
  - 공통 ontology 추가는 팀 논의 때 공유할 항목이다(같은 날 ADR-086에서 사용자 결정으로 확정).

### ADR-086: 실제 학교 DB는 없다는 전제로 관문을 재정의한다

- 날짜: 2026-10-01
- 상태: 채택(사용자 결정)
- 결정:
  - 실제 학교 DB는 받지 않는다고 본다. 개발·평가 데이터는 레거시 DB(공개 수강편람 엑셀 기반, 팀 구성)와 다른 학교를 흉내 낸 DB뿐이다.
  - E3: 레거시 데이터와 그 파생 데이터셋을 사용하되, 원본 재배포는 금지한다.
  - L5: 시연은 자유롭게 하고 출처를 밝힌다. 학교 밖에 공개하는 자료에서는 교수명을 가린다.
  - L2: 관리자 검토 화면으로 대신한다.
  - L1/L3: 사용자와 팀원이 검토한다.
  - L4: 그대로 둔다.
  - 독립 학교 평가: 팀원이 흉내 내어 만든 DB 2~3개로 한다. 질문·정답은 만든 사람이 고정하고, AI는 평가 전까지 보지 않는다.
  - 공통 ontology 변경은 사용자 결정으로 확정한다.
- 이유: 실제 학교 DB 확보가 불가능해서, 개인정보·사용 권한 관문은 대부분 대상이 사라졌다. 남는 위험은 자기 데이터로 자기 시스템을 평가하는 확증 편향이다.
- 결과:
  - 개발을 막는 사람 관문은 없다.
  - 남은 사람 작업은 L1/L3 검토, L4 사용성 평가, 평가용 모사 DB 제작이다.
  - 실제 학교 자료가 생기면 다시 본다.

### ADR-087: DB별 확장 항목은 관리자가 데이터로 선언하고, 공통 항목과 같은 실행 경로를 탄다

- 날짜: 2026-10-01
- 상태: 채택(사용자 결정: 진행, 8-11 축소판)
- 결정:
  - 화면 등록 PostgreSQL DB에 관리자가 확장 항목(`<Entity>.ext_<key>`, 이름, 글자·정수·숫자)을 선언한다.
  - 한 column은 한 항목에만 쓰고, 최대 10개다.
  - 게시하면 mapping과 profile v5에 들어간다.
  - runtime은 이름·연결·타입을 검사하고, 공통 항목·unsupported·변환·값 사전과 겹치는 것을 거부한다.
  - 챗봇의 항목 목록은 공통 + 확장이다. 모델 prompt에서는 확장 항목에만 이름을 붙인다.
  - 결과 카드는 `ext_<key>` 열과 이름 목록을 보인다. 글자 확장 항목은 포함 검색과 값 힌트를 쓴다.
  - SQL은 기존 compiler·policy·executor를 그대로 거친다.
  - 변환·값 사전·강의 목록 표시는 이번 범위에서 뺀다.
- 이유: 강의실, 수업방식, 단과대학처럼 학교마다 다른 정보를 공통 ontology에 다 넣을 수는 없다. 코드 수정 없이 DB마다 더할 수 있어야 한다.
- 결과:
  - 가상 학교 고정 질문 14/14(강의실 2개 추가).
  - 레거시 화면 등록에서 강의실·수업방식 질문이 동작한다.
  - 값만 있는 질문은 값 힌트(값 500개 이하)에 의존한다는 한계가 있다.

### ADR-088: 그 DB가 모르는 조건은 실행하지 않고 알리며, 0건은 조건별로 진단한다

- 날짜: 2026-10-01
- 상태: 채택(사용자 결정: 진행, 8-18 → 8-19 → 8-20 순서)
- 결정:
  - 공통 항목 밖 개념(강의실, 수업 방식, 여석 등)을 데이터 목록(`outside-concepts.yaml`)으로 둔다. 공통 ontology의 이름·별칭과 겹치면 목록을 거부한다.
  - 질문이 그 개념을 말하고 그 DB에 해당 확장 항목이 없으면, 모델 출력과 상관없이 실행하지 않고 `UNSUPPORTED`로 답한다. 모델이 찾은 더 긴 값의 일부인 말(과목명 "건물에너지" 등)은 개념으로 보지 않는다.
  - 결과가 0건이면 같음·포함 조건을 하나씩 `limit 1` QueryIR로 같은 실행 경로에서 다시 돌린다(최대 4번). 혼자서도 0행인 조건을 알린다.
  - 모델 호출은 그대로 한 번이다. 조건을 더하지 않는다.
- 이유:
  - 8-19 레거시 확인에서 "교111에서 하는 수업"이 분반·과목 코드 조건으로 바뀌어 0건이 나왔다. 사용자는 "그런 수업이 없다"로 오해할 수 있다.
  - 값 목록(500개 이하) 판단은 값이 많은 항목(과목 코드)에서 쓸 수 없었다. DB에 직접 묻는 방식은 값 목록이 오래되어도 틀리지 않는다.
- 결과:
  - 레거시 고정 기대 9/9. 기존 질문(컴융 전공 82건, 3학년 33건 등)은 그대로다.
  - 가상 학교 14/14.
  - 실측 중 값 힌트 cache가 다시 게시해도 갱신되지 않는 버그(8-07·8-19부터)를 찾아 고쳤다.
  - 목록에 없는 말이 다른 항목에 들어가 결과가 0건이 아니면 알리지 못한다는 한계가 남는다.

### ADR-089: 개발 범위를 PostgreSQL DB 간 적응성으로 좁힌다

- 날짜: 2026-10-02
- 상태: 채택(사용자 결정)
- 결정:
  - 이번 개발은 PostgreSQL 전용이다. 범위는 "처음 보는 PostgreSQL DB에 적응하는가"이다.
  - 업로드 SQLite DB의 QueryIR 챗봇·값 사전·변환·부분 게시는 하지 않는다.
  - 이미 만든 SQLite 업로드·검토 기능과 테스트는 지우지 않고 그대로 둔다(동작 보장 범위만 기존 수준).
- 이유: 화면 등록의 주 경로가 PostgreSQL이고, 팀원의 평가 DB도 PostgreSQL로 만든다. 남은 사용량을 적응성 검증에 쓴다.
- 결과: 다음 단계는 8-21 형식이 다른 PostgreSQL DB 대응 점검, 팀원 DB 평가 실행 도구, Phase 9 순서다.

### ADR-090: 코드로 저장된 항목은 이름 일부로도 찾고, SQL 모양은 compiler와 policy가 같은 함수로 정한다

- 날짜: 2026-10-05
- 상태: 채택(8-21 진행, 사용자 결정 범위 안)
- 결정:
  - 값 사전이 있는 항목의 "포함" 조건은 이름(저장 값과 다른 것)에 그 말이 들어 있는 코드도 찾는다. SQL은 `(column LIKE %s OR column IN (%s, ...))`이다.
  - 모양은 `contains_label_values` 하나가 정하고, compiler와 AST policy가 모두 이 함수를 부른다. policy는 그 조건에서만, 같은 column과 정확한 개수의 placeholder로만 이 모양을 허용한다.
  - 별칭은 일부로 찾지 않는다. 이름을 저장한 DB는 SQL이 바뀌지 않는다.
  - 공유 앞부분("전공")은 값 힌트로 준다.
- 이유: 공통코드 table을 쓰는 학교에서 "교양 과목"처럼 이름 일부로 묻는 질문이 0건이었다(8-21 A 첫 실행). 코드 column은 이름의 글자를 담지 않는다.
- 결과: `code_school` 14/14. 레거시 결과·SQL 불변. 공통코드 table에서 값 사전 자동 가져오기와 코드형 column AI 제안(9/16)은 후속이다.

### ADR-091: 교시 번호는 관리자가 값 사전으로 넣은 교시표로 시각에 연결한다

- 날짜: 2026-10-06
- 상태: 채택(8-21 진행, 사용자 결정 범위 안)
- 결정:
  - 시간 항목의 값 사전에서 이름이 모두 `HH:MM`이면 교시표다(1 → 09:00). 새 변환 종류나 profile 항목을 만들지 않는다.
  - 시간 비교는 `column IN (조건에 맞는 교시)`로 바꾼다. 모양은 `time_label_values` 하나가 정하고 compiler와 AST policy가 같이 쓴다.
  - 맞는 교시가 없으면 SQL 없이 0건과 이유를 답한다.
  - 시간 항목을 교시표 없이 정수 column에 연결하면 게시를 막는다(runtime도 거부).
  - 교시표 항목은 DB에 있는 값 대신 형식(0 없는 정수, `HH:MM`, 별칭 없음)으로 검사한다.
- 이유:
  - 8-21 B 첫 실행에서 정수 교시 column이 그대로 게시되었고, "오후 3시 이후"가 HTTP 500이었다.
  - 값 사전을 쓰면 저장·게시·관리자 화면·카드 표시를 그대로 쓸 수 있다. 변환을 매개변수형으로 만드는 것보다 바뀌는 곳이 적다.
- 결과:
  - `period_school` 11/13. 교시 계산은 정확했고, 틀린 2개는 모델 이해 문제다.
  - 레거시 결과는 그대로다.
  - 교시 번호로 묻는 질문("1교시 수업")은 다루지 않는다.

### ADR-092: 한 칸에 묶인 시간표는 요일만 포함 검색으로 지원한다

- 날짜: 2026-10-06
- 상태: 채택(8-21 지침 C ①, 사용자 결정 범위 안)
- 결정:
  - 닫힌 변환 `PACKED_WEEKDAY`를 추가한다. "월요일"을 "월"로 바꾸어 포함으로 찾고, 연산자는 포함만 허용한다.
  - 서버 규칙은 모든 값이 "요일 + 숫자·시각 기호" 꼴일 때만 이 변환을 붙인다.
  - 시작·종료 시간은 관리자가 "이 DB에 없음"으로 둔다. 시간 질문은 unsupported로 답한다.
  - 게시 때 값 종류가 50개를 넘으면 표본 50개를 읽는다. 표본으로는 이 변환만 고를 수 있다.
  - 문자열을 해석해 시간까지 꺼내는 변환(②)은 만들지 않는다.
- 이유:
  - 묶인 시간표에서 "월요일 수업"이 같음 비교로 0건이었다.
  - 값 종류가 많은 column에는 변환이 아예 붙지 않았다.
  - ②는 문자열 해석 SQL이 복잡하고 위험해서, 필요할 때 사용자 결정으로 미룬다.
- 결과: `packed_school` 13/13. 기존 변환은 지금처럼 전체 값으로만 정한다.

### ADR-093: 팀원 DB 평가는 질문 파일을 잠그고 첫 실행을 지우지 않는 도구로 한다

- 날짜: 2026-10-06
- 상태: 채택(ADR-086 규칙의 구현, ADR-089 순서)
- 결정:
  - 팀원이 질문과 정답(과목 코드 또는 거절)을 `.schema-adaptive/blind-evals/<ID>/questions.json`에 고정한다.
  - `check`는 내용을 출력하지 않는다.
  - `run`은 화면과 같은 챗봇 API로 묻는다. 첫 실행 때 질문 해시를 잠그고, 실행마다 번호를 먼저 잡아 따로 저장한다.
  - 해시가 다르면 실행을 거부한다. 기록에는 결과 행 대신 상태와 개수만 남긴다.
  - 도구는 DB에 직접 접속하지 않고, loopback 밖으로 요청하지 않는다.
- 이유: "AI는 평가 전까지 보지 않는다", "첫 실행을 그대로 남긴다"는 규칙을 사람의 주의가 아니라 도구가 지키게 한다.
- 결과:
  - 개발용 `code_school`로 동작을 확인했다(15/15, 평가 수치 아님).
  - 팀원용 안내 문서를 추가했다.

### ADR-094: L1 검토 표는 도구로 만들고, 판정은 사람만 하며 gold는 도구가 바꾸지 않는다

- 날짜: 2026-10-06
- 상태: 채택(ADR-086 "L1 표는 Phase 9 진입 때 준비"의 구현)
- 결정:
  - 합성 benchmark 70개의 검토 표를 Markdown(읽기)과 CSV(판정 입력)로 만든다. 내용은 seed 표, 질문, gold 조건(한국어 이름), 정답 행, 자동 확인 메모다.
  - CSV의 모든 행에 gold 지문을 넣는다.
  - `record`는 판정 형식을 확인한다. 열과 질문·gold 내용이 현재 gold와 같아야 하고, 판정에는 검토자·검토일이, 수정·보류에는 의견이 있어야 한다. 확인한 판정은 기록 파일로 남긴다.
  - gold의 `review_status`는 바꾸지 않는다. 기록을 gold에 반영할지는 사용자가 정한다.
  - 팀원 질문 파일의 표는 그 private 폴더에만 만들고 내용을 출력하지 않는다.
- 이유: L1은 사람의 판정이다. AI는 판정에 필요한 자료와 형식만 준비하고, 오래된 표나 내용 없는 표가 승인으로 기록되지 않게 막는다.
- 결과: `docs/l1-review/fixture-v1-review.md`·`.csv`(70행, 지문 `a7ff9d8f45b0…`).

### ADR-095: 화면 등록의 AI mapping 제안은 예시 값 표본, 로마자 이름, 수강편람 관례를 쓰는 v3로 한다

- 날짜: 2026-10-06
- 상태: 채택(사용자 결정: 진행)
- 결정:
  - 예시 값은 값 종류가 많아도 10개 표본을 넣는다. 저장하지 않고 loopback 모델 prompt에만 넣는다.
  - prompt v3(`ai-mapping-v3`, 화면 등록용)는 항목 이름·별칭을 한국어와 로마자(국어 로마자 표기법 계산)로 함께 보인다.
  - 한국 수강편람 DB 관례 4줄(로마자·줄임말 이름, 코드 table, 교시 번호, 묶인 시간표)을 넣는다.
  - 예시 값은 JSON 목록으로 보여 지시처럼 읽히지 않게 한다.
  - FK가 하나도 없는 table의 선택은, 과반이 한 FK 묶음에 있을 때만 버린다.
  - benchmark용 v1.1·v2 prompt는 그대로 둔다. 관리자 검토는 그대로 필수다.
- 이유: 8-21에서 형식이 다른 DB마다 AI 첫 후보가 8~14/16으로 흔들렸다. 원인은 세 가지였다.
  - 예시 값이 빠졌다.
  - 로마자 이름을 못 읽었다.
  - 코드·교시·묶인 시간표 관례를 몰랐다.
- 결과:
  - 개발용 schema 5개에서 1회당 55→75/80(3회·2회 반복 같은 결과).
  - 레거시 화면 등록 제안은 8-19와 같다.
  - 개발자가 만든 schema·정답이므로 적응성 근거가 아니다. 팀원 DB 평가(Phase 9)에서 다시 본다.

### ADR-096: 챗봇의 시간 표현은 닫힌 한국어 규칙으로 읽어 힌트로 주고, 빠지면 더한다

- 날짜: 2026-10-06
- 상태: 채택(사용자 결정: "질문 이해 개선은 L1 뒤" 원칙의 예외로 지금 진행)
- 결정:
  - 질문의 시간 표현(`오전`·`오후`, `N시`·`N시 반`·`HH:MM`, `이후`·`부터`·`전`·`이전`·`까지`, `시작`·`끝나는`)을 닫힌 규칙으로 읽는다. 학교·DB 데이터가 아니라 언어 규칙이다.
  - 확실하지 않으면 읽지 않는다: 시작·종료가 갈리는 `N시 전`, 모르는 방향, 범위 기호, 부정 말, 겹치는 시간이 없는 조건, 조건 셋 이상, 자정.
  - 읽은 조건은 8-13 값 힌트 줄에 연산자와 함께 넣는다. 모델 호출은 한 번이다.
  - 모델의 시작·종료 시간 조건 개수가 같으면 field·연산자·값을 맞추고, 시간 조건이 없으면 더한다. 8-13의 "조건을 더하지 않는다"에 대한 유일한 예외다.
  - 그 DB에 없는 시간 항목이면 모델 출력과 관계없이 "정보 없음"이다.
  - 고친 출력도 QueryIR 검증·compiler·policy·read-only executor를 그대로 지난다.
- 이유: 8-20·8-21에서 "오전" 누락, 시작을 종료로 이해, "이후"를 초과로 받는 흔들림이 반복되었다. temperature 0에서도 같은 질문의 답이 바뀌었다.
- 결과:
  - `other_school` 19/19, 시간 질문 반복 20/20, `packed_school` 19/19.
  - `period_school` 18/19. 남은 1개는 정답 기준 차이(교시 올림)이고 챗봇 답은 그 DB 데이터로 맞다.
  - 레거시 고정 질문 결과는 그대로다.
  - 개발용 질문·DB 위의 잠정치다. L1 질문 세트와 팀원 DB 평가에서 다시 본다.

### ADR-097: 글자 칸의 시간 항목은 HH:MM일 때만 게시하고, 말하지 않은 select의 없는 항목은 빼고 답한다

- 2026-10-10 보완(Phase 9-06, 사용자 결정): 숫자 항목(학점·대상 학년·개설 연도·정원·수강 인원)은 숫자 형식 칸이거나 검증된 글자→숫자 변환(`SLASH_FIRST_NUMERIC`)이 있을 때만 게시한다. 글자→숫자·시간 변환이 정해진 칸에 빈 글자가 있으면 막는다. 도메인 형식 숫자 칸은 막히는 한계가 있다.

- 날짜: 2026-10-08
- 상태: 채택(사용자 결정: 기존 레거시 결과가 바뀌지 않는 범위에서 9-04 문제 1·2를 고친다)
- 결정:
  - 시간 항목이 변환 없이 글자 칸에 남으면 그 칸의 값이 모두 0을 채운 `HH:MM`이어야 게시한다. 아니면 그 항목으로 게시를 막는다. `time` 형식 칸, `0900` 꼴, 교시표 항목은 그대로다.
  - select에만 있고 질문이 이름·별칭으로 말하지 않은 "이 DB에 없음" 항목은 거절하지 않고 select에서 뺀다. 질문이 말하거나 조건에 쓰면 거절한다. select가 비면 쓸 수 있는 카드 항목 하나를 넣는다.
- 이유: 9-04 리허설에서 `09:00-10:15` 범위 글자 칸이 글자 비교로 조용히 틀린 답(정답 79과목에 17과목 섞임)을 냈다. 또 모델이 select에 분반을 자주 넣어, 분반이 없는 DB에서 답 대부분이 거절되었다. 챗봇 카드는 select를 어차피 고정 항목으로 바꾸므로, 말하지 않은 select 항목으로 거절할 이유가 없다(8-20의 "모델이 혼자 더한 항목은 탓하지 않는다"와 같은 원리).
- 결과: 레거시 비교 30문항과 고정 확인 6개가 고치기 전·후 36/36 같았다(상태·행 수·과목 코드·거절 사유). 리허설 학교 재실행 9/14(첫 실행 6/14). 개발용 수치다.

### ADR-098: 적응형 챗봇의 기본 모델은 일반 모델로 하고, 쓸 모델은 사용자가 고른다

- 날짜: 2026-10-12
- 상태: 채택(사용자 결정)
  - 특정 DB로 학습한 모델을 적응형 챗봇의 기본으로 쓰지 않는다. 규칙으로 덮지 말고 모델을 바꾼다.
  - 어떤 모델을 쓸지 사용자가 고를 수 있게 한다.
- 결정:
  - 서버가 챗봇 모델 선택지를 정한다. 브라우저는 선택지 id만 보내고, 모르는 id는 거절한다.
    - `general`(기본): `CHAT_MODEL`, 없으면 `qwen2.5-coder:7b-instruct`.
    - `legacy-tuned`(비교용): `OLLAMA_MODEL`. 요청은 이전과 바이트까지 같다.
  - `GET /api/adaptive/v1/chat/models`가 선택지(id, 이름, 모델 이름)를 알려 준다. 답 형식은 그대로다.
  - 일반 모델에만 세 가지를 준다. 미세 조정 모델이 학습으로 익힌 것을 일반 모델에는 말로 알려 주는 것이다.
    1. QueryIR JSON schema(Ollama structured output, `queryir-schema-v1`). 공통 항목 이름, 연산자, 값 형식, 조회 항목 중복 금지만 정한다. 조건은 12개까지다(되풀이 출력이 시간 초과까지 가는 것을 막는다).
    2. prompt `query-ir-v3`: v2에 "질문이 말한 조건만", "값 힌트의 항목과 값을 쓴다"를 더했다.
    3. 8-13 값 힌트 고침의 확장: 식별 항목(코드·분반)에 넣은 값이 값 힌트 하나와 정확히 같으면 그 힌트의 항목으로 옮긴다. 이 고침은 두 모델 모두에 적용된다.
  - 평가 도구 `blind_eval run --model <id>`. 기록의 모델은 `.env`가 아니라 서버가 알려 준 id와 이름이다.
  - 레거시 챗봇(8000)은 그대로 `OLLAMA_MODEL`을 쓴다.
- 이유: 미세 조정 모델이 리허설 학교에서 레거시 과목 코드를 지어냈다(위험 3). 계획 10.2절의 "하나의 공통 모델 + 학교별 지도" 원칙과도 맞다.
- 결과(개발 수치, L1/L3 전):

  | 질문 세트 | 일반 모델 | 레거시 학습 모델 |
  | --- | --- | --- |
  | 레거시 30문항 | 23 맞음 | 22 맞음 |
  | 리허설 학교(사용자 등록) | 6/14 | 6/14 |
  | `other_school` | 10/12 | 12/12 |

  - `other_school`에서 일반 모델이 틀린 2문항은 모두 학과 약어다. 힌트를 따르지 않고 조건을 지어냈다.
  - 레거시 학습 모델의 레거시 답은 9-08 전후로 같다.
- 남은 판단: 기본 모델을 계속 일반 모델로 둘지는 팀원 DB 평가를 두 모델로 각각 실행한 뒤 다시 본다. 다른 일반 모델은 내려받기가 필요해 시험하지 않았다.

### ADR-099: ChatGPT API를 챗봇 모델 선택지로 더한다

- 날짜: 2026-10-12
- 상태: 채택(사용자 결정)
  - 로컬 7B 모델만으로는 한계가 있다. ChatGPT API 선택지를 더하고 키는 사용자가 넣는다.
  - 기본 모델은 `gpt-5-mini`다.
  - 값 힌트도 보낸다.
  - "Legacy 기준선"은 그대로 둔다.
- 결정:
  - `OPENAI_API_KEY`가 있을 때만 선택지 `openai`("ChatGPT(외부 API)")를 맨 뒤에 둔다.
    - 모델은 `OPENAI_MODEL`, 없으면 `gpt-5-mini`다.
    - 기본 선택지는 그대로 일반 모델이다.
  - 질문당 Chat Completions 한 번을 부른다. 재시도는 없다.
    - prompt `query-ir-v3`와 값 힌트를 쓰고, QueryIR JSON schema를 non-strict `response_format`으로 보낸다.
    - 결과는 다른 모델과 같은 QueryIR 검증·compiler·policy·executor를 지난다.
  - 고정 HTTPS 주소만 쓴다. proxy와 redirect는 따르지 않고 응답 크기에 상한을 둔다. 실패는 고정 코드다.
  - API 키는 요청 header에만 쓴다. 화면, 상태 줄, 오류, repr, 평가 기록에는 나가지 않는다.
  - 예전 명확화 흐름은 로컬 Ollama 모델에만 둔다.
- 이유: 9-08에서 로컬 일반 모델은 다른 학교의 약어 질문에 약했다(`other_school` 10/12). 더 큰 모델과 비교할 선택지가 필요하다.
- 예외 범위: 지금까지 모델 호출은 loopback Ollama만이었다(ADR-059 계열). 이 선택지만 외부로 나간다.
  - 보내는 것: 질문, 공통 항목 목록, 값 힌트.
  - 보내지 않는 것: 물리 이름, DSN, DB 행.
- 결과: 가짜 전송 테스트만 했다. 실제 호출과 비교 수치는 사용자가 키를 넣은 뒤 잰다.

### ADR-100: 등록 PostgreSQL 직접 SQL 비교군을 평가 전용으로 추가한다

- 날짜: 2026-10-12
- 상태: 채택(사용자 결정, 지침 `docs/prompts/phase9-10-direct-sql-postgres.md`)
- 사용자 결정 1~4: 9-10 진행; catalog + column별 예시 최대 10개/40자 + 실제 값 힌트; 결정 3은 같은 날 사용자가 철회: 추가 비용 없음(ChatGPT Plus는 API 사용량을 포함하지 않음). OpenAI 측정 제외, 직접 SQL OpenAI 경로 제거; 레거시 30·other_school 12·리허설 14 개발 질문 사용.
- 결정: 별도 평가 모듈만 추가한다. 제품 HTTP·prototype·명확화·챗봇에서 import하지 않는다(ADR-048/049 유지). 기존 SQLite 직접 SQL, Legacy 기준선, ontology, 제품 QueryIR·API·화면을 보존한다.
- 저장된 등록 loader·source identity를 재사용한다. sample=True 읽기를 정렬한 뒤 제한하고 숫자·날짜·시간도 포함한다. 낮은 cardinality 실제 글자 값에 ValueIndex 전체 값·단어 조각·가까운 철자 일치를 적용하며 관리자 값 사전은 사용하지 않는다. 이유: 직접 SQL 쪽의 "등록만" 비용을 재기 위함이다.
- 모델: 로컬 `general`로만 비교한다. CLI 직접 SQL arm은 `general`만, QueryIR arm은 `general`·`legacy-tuned`만 허용한다. 질문당 한 호출·재시도 없음. 제품 ChatGPT 선택지는 ADR-099 그대로다.
- SQL 정책: 한 SELECT/집합 SELECT, 등록 schema/catalog table 또는 자기 CTE만; 쓰기·DDL·SET·COPY·트랜잭션·위험 함수 금지. schema로 한정한 함수 호출은 모두 거절하며 Anonymous 함수는 고정 PostgreSQL 내장 허용 목록만 받는다(typed 함수는 기존 금지 이름 검사 유지). 따옴표 없는 schema·table·CTE 식별자는 소문자로 접고 따옴표 있는 이름은 그대로 비교한다. 원래 SQL 검사 뒤 200행 wrapper로 등록 연결 READ ONLY executor·timeout을 적용하고 원래 SQL hash를 유지한다. 200행도 SUCCEEDED·row_count=200·limit=200으로 기존 course_code/section 상한 채점기를 쓴다.
- 기록: 원문·행 값·secret 없는 코드·개수·fingerprint·elapsed_ms, blind 잠금/번호 공유·arm 보고. 가짜 모델·전송 테스트와 9-10c에서 제공된 Luna 리뷰 지적 4건의 재현·수정을 거쳤다. CLI legacy 거절 채점은 blind_eval과 같은 DECLINE_STATUSES를 쓴다(legacy_chat_compare 자체 채점 유지). blind 지연은 채점 전 측정하고 응답 elapsed_ms가 있으면 우선한다. 사람 관문 승인 없음.
- 결과(2026-10-12 사용자 승인, 한 번): 로컬 `qwen2.5-coder:7b-instruct`·temperature 0·질문당 한 호출. 직접 SQL / QueryIR은 레거시 10/30 / 26/30, other_school 5/12 / 10/12(9-08 재사용, 응답 시간 없음), 리허설 2/14 / 6/14, 합 17/56 / 42/56. 레거시 QueryIR 26은 9-08의 정확 18·상한 부분 집합 5 + 거절 정답 3이다. 테스트 서버 8124는 측정 뒤 종료, 사용자 서버는 건드리지 않음. 새 등록 `p910-legacy`·`p910-other`는 PENDING_REVIEW·미게시, 리허설은 기존 `rehearsal-school` 사용. [결과 문서](phase9-10-direct-sql-results-2026-10-12.md)에 전체 지표와 약 39건 실패 분류를 기록했다. 개발 질문·L1/L3 전 잠정치이며 적응성 근거·사람 관문 승인·제품 구조 결정이 아니다. 이번 문서 기록에서 새 모델 호출·테스트·서버 조작·commit 없음.

### ADR-101: 공통 ontology v3에서 개설 연도를 사용 중단하고 과거 기록은 보존한다

- 날짜: 2026-10-13
- 상태: 채택(사용자 결정). 팀 공유 필요, 사람 관문 승인 없음.
- 결정: 방법 A(사용 중단). 공통 ontology를 버전 3으로 올리고 `CourseOffering.year`에 `retired_in_version: 3`을 기록한다. 정의 삭제(B)는 하지 않는다. `CourseOffering.semester`와 다른 항목은 유지한다.
- 이유: 사용자 판단으로 수강 강의를 개설 연도로 찾는 경우가 없다(레거시만 보고 내린 결론이 아님). 레거시 등록 AI가 개설 연도를 `cnu_courses.course_year`(실제 학년 1~5)에 연결해 연도 질문을 학년과 비교하는 조용한 오답 위험도 확인되었다.
- A안을 고른 이유: v1/v2 게시 mapping·평가 gold·fixture·dataset profile을 수정하거나 폐기하지 않고 그대로 읽기 위해 정의와 버전별 항목 집합을 보존한다. 현재 활성 항목은 `since_version <= v` 및 (`retired_in_version` 없음 또는 `> v`)으로 정한다. ADR-085의 추가 전용 정책을 retirement 허용으로 보완한다.
- 현재 동작: 새 규칙·AI 제안·검토 표·게시 검사·값 사전·확장 항목 대상·모델 항목 목록·QueryIR schema·값 힌트에서 year가 빠진다. 옛 mapping에 연결이 있어도 현재 runtime에서는 unsupported다. 연도 질문은 정보 없음, 모델이 다시 year를 생성하면 UNKNOWN_FIELD로 거절한다. SQLite 강의 목록 필수 projection은 14개이며 catalog DTO의 기존 year key는 null로 유지한다.
- 평가(9-24 보완): 오프라인 평가는 기록 버전(`ontology.at_version(v)`, 미선언 기록은 v2)으로 재현한다. fixture gold·oracle·녹화 replay·capture targets·benchmark의 기존 정답과 수치는 유지한다. 실제 챗봇 HTTP 평가(`blind_eval` 등)는 현재 v3이며 P2-009·P2-028·P2-049·P2-063 및 variant v09·v19의 연도 질문을 다시 측정하면 거절 대상이다. gold·fixture·녹화·review_status는 수정하지 않는다. legacy_chat_compare q28은 원래 UNSUPPORTED_FIELD이며 정답 변화가 없다.
- 검증·한계: fake model/reader/executor와 임시 디렉터리 targeted 테스트만 실행한다. live 모델·사용자 서버·사용자 .schema-adaptive 데이터·commit 없음. Luna/opencode review는 no-live-model 사용자 제약으로 대기한다.

### ADR-102: 연결되지 않은 낱말은 답을 막지 않고 알린다

- 날짜: 2026-10-13
- 상태: 채택(사용자 결정), 사람 관문 승인 없음.
- 이유: 값 사전 없는 DB에서 “컴융 전공 보여줘”의 컴융 조건을 모델이 조용히 버리고 전공 전체를 조회했다. 이해한 조건을 직접 읽어야만 조건 누락을 알아챌 수 있었다.
- 결정: 성공 응답에서만 질문의 공백 단위 낱말을 닫힌 규칙으로 확인하고 연결되지 않은 2글자 이상 한글·영문 낱말을 `unmatched_terms` 최대 5개로 알린다. 조사·어미, 코드 상수 일반어, 공통·확장 항목과 엔터티 이름·별칭, 기존 값 힌트, QueryIR 조건 값·연산, 시간 규칙과 숫자·단위를 사용한다. 특정 DB 줄임말이나 특정 질문 예외를 코드에 넣지 않는다.
- 동작: 화면 결과 위에 누락 낱말과 짧은 값 사전 안내를 표시한다. 알림만 추가하며 답을 막거나 조건을 만들지 않는다. 기존 outside-concept 거절·UNSUPPORTED·NOT_UNDERSTOOD·빈 결과 진단을 유지한다. 선택 필드 parser로 기존 응답도 허용한다. 레거시 8000 챗봇은 유지한다.
- 경계·한계: 추가 모델 호출·DB 읽기 없이 기존 힌트와 검증·compiler·policy·executor 뒤의 조건을 재사용한다. 낱말 연결 여부는 조건 완전성이나 모델 정확도의 증명이 아니다. 값 힌트가 찾은 낱말은 조건이 빠져도 연결됨으로 본다(사용자 지침). 30문항 확인은 공개된 개발 질문의 reference SQL 기반 고정 조건으로만 수행한 잠정 결과이며 L1/L3 승인이나 미관측 학교 평가가 아니다. [결과](phase9-21-unmatched-word-notice-results-2026-10-13.md).

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
- Phase 7-01 DBMS capability와 PostgreSQL read-only schema inspector 완료. `sqlite`·`postgres` 외 dialect를 거부하고 PostgreSQL은 inspector만 지원 상태로 명시했다. server-owned DSN, PostgreSQL 12+, 단일 schema, read-only transaction·timeout, parameterized `pg_catalog`, sample 미수집, deterministic catalog/fingerprint를 fake connector로 검증했다. Luna review-only에서 확인한 미등록 ID 반사, cross-schema FK, mutable feature list를 고정 오류·참조 schema filter·tuple로 수정했다. 관련 39개·5 subtests와 전체 pytest 725개·5 skip·452 subtests, compileall, diff check 통과. 실 PostgreSQL/CNU/Ollama/runtime 질의/UI 없음. ADR-048 추가
- Phase 7-02 PostgreSQL QueryIR runtime component 완료. join-capable `%s` compiler, postgres dialect AST allowlist, PostgreSQL 12+ read-only executor를 기존 ExecutionHarness protocol에 연결하고 capability compiler/policy/executor를 준비 상태로 전환했다. executor는 더 짧은 statement timeout, 200행 상한, SQLSTATE `57014`, cleanup·고정 redaction을 적용하고 libpq connect timeout을 정수로 전달한다. Luna review-only의 timeout 미적용과 외부 `HarnessFailure` redaction 우회를 수정했다. 관련 82개·69 subtests와 전체 pytest 747개·5 skip·459 subtests, compileall, diff check 통과. 실제 server/CNU/Ollama/registry/UI 없음. ADR-049 추가

### 2026-09-28

- Phase 7-03 완전 합성 PostgreSQL live integration smoke 완료. opt-in loopback DSN preflight, 실행별 격리 schema fixture, sample 없는 inspector·mapping proposal·physical graph, 두 QueryIR의 compiler/policy/read-only execution, 고정 JSON/redaction, 성공·실패 cleanup을 구현했다. Luna review-only의 aborted transaction cleanup과 FK 검증 결함을 rollback·idempotent drop·정확 tuple 비교로 수정했고, 실제 PostgreSQL 18 smoke에서 발견한 schema-qualified 다중 `unnest` 비호환과 executor schema 미고정을 `generate_subscripts`와 transaction-local `search_path`로 수정했다. 관련 57개·18 subtests, 전체 pytest 762개·5 skip·470 subtests, compileall, diff check 통과. 임시 cluster와 schema는 제거했고 실제 CNU·기존 PostgreSQL 데이터·Ollama·registry/UI는 사용하지 않았다. ADR-050 추가
- Phase 7-04 server-owned PostgreSQL runtime registry 조립 완료. strict immutable profile, startup catalog/mapping/graph 검증, registry `postgres` entry, catalog·clarification DB별 compiler/policy/executor routing, context load·실행 직전 drift 차단, credential·physical identifier·SQL·driver 오류 redaction을 구현했다. Luna review-only의 compiler/policy 예외 반사와 catalog executor 우회를 수정했고, 실제 PostgreSQL 18 HTTP smoke에서 registry 200·canonical catalog 6행·clarification 성공을 확인하며 `Decimal` JSON 직렬화 결함을 수정했다. 관련 신규·회귀 90개·7 subtests 및 private 회귀 45개·1 skip, 전체 pytest 772개·5 skip·470 subtests, compileall, diff check 통과. 임시 cluster/schema 제거 완료. 기본 app은 profile 0개이며 실제 CNU·Ollama·frontend 변경은 없다. ADR-051 추가
- Phase 7-05 private PostgreSQL startup profile 완료. CLI database ID를 고정 Git ignored·untracked profile/mapping 경로에만 연결하고, 256 KiB bounded read·strict JSON·mapping SHA-256·명시적 `PUBLISHED`·별도 DSN 환경변수로 Phase 7-04 runtime profile을 조립한다. option 없는 기본 app은 private/environment/PostgreSQL 접근 0회다. Luna review-only actionable 결함은 없었고, Luna residual risk와 메인 독립 검토에서 확인한 제한 전 전체 파일 read를 공용 private reader의 하위 호환 `max_bytes`로 수정했다. 관련 51개·2 skip, 전체 pytest 780개·5 skip·470 subtests, compileall, diff check 통과. 실제 PostgreSQL/CNU/Ollama/frontend 변경 없음. ADR-052 추가
- Phase 7-06 PostgreSQL frontend 선택 연결 완료. strict registry parser에 `postgres` literal과 backend 동등 ASCII label 계약을 추가하고, 기존 server-owned radio·명시적 적용·sessionStorage 재검증·상태 초기화·catalog/clarification 동일 ID 전달을 재사용했다. DSN·physical metadata·SQL은 browser에 추가하지 않았다. Luna의 trailing newline 정규식 지적은 Node에서 LF·CR·U+2028·U+2029 모두 재현되지 않아 미적용했다. 관련 Node 30개, ESLint, frontend build, 전체 pytest 780개·5 skip·470 subtests, compileall, diff check 통과. 실제 PostgreSQL browser smoke·Ollama·CNU 자료 없음. ADR-053 추가
- Phase 7-07 Ollama clarification constraint provider 완료. 확인된 entity/field에 맞춰 ontology·Published Mapping semantic context를 축소하고 기존 `OllamaQueryModel`을 1회 호출하며, 응답은 기존 QueryIR validation/compiler/policy/executor 경계를 그대로 통과한다. server-owned factory 명시 주입만 허용하고 기본 synthetic provider·network 0회를 유지했다. Luna review-only에서 확인한 nested extra-key 허용을 strict nested shape 검사로 수정했다. 관련 113개와 전체 pytest 816개·5 skip·470 subtests, compileall, diff check 통과. live Ollama·CLI 설정·raw SQL fallback·재학습·실제 PostgreSQL/CNU·frontend 변경 없음. ADR-054 추가
- Phase 7-08 server-owned Ollama startup 완료. 신규 `backend/adaptive/api/ollama_startup.py`가 CLI 원문 문자열을 `ollama-startup-config-v1`(127.0.0.1/[::1] loopback URL, `SAFE_LABEL_PATTERN` ASCII tag, 1~120초 10진 timeout, `query-ir-v1` 고정)로 strict 검증하고, 기존 `OllamaQueryModel`에 빈 `ProxyHandler`·redirect 거부·exact URL·1 MiB 응답 상한 transport를 주입한다. prototype CLI는 `--ollama-model-tag`가 있을 때만 model을 Phase 7-07 factory에 넘기고 반복·축약·unknown option을 고정 문구로 거부하며, option이 없으면 factory 인자 없음·환경변수·network 0회를 유지한다. app state에는 `provider_kind`만 추가했다. 이번 단계는 Claude Code가 직접 구현했고 `gpt-6-luna` high review-only(OpenCode read-only `plan` agent)는 actionable 결함 없음으로 보고했다. Luna 잔여 위험 중 실제 IPv6 loopback 연결 미검증은 `[::1]` 임시 server 테스트로 보완했다. Windows에서 닫힌 loopback port 연결은 refused 대신 timeout으로 분류될 수 있음을 테스트에 반영했다. 관련 169개·10 subtests 통과 뒤 IPv6 테스트를 추가해 신규 파일 99개 재통과, 전체 pytest 915개·5 skip·470 subtests, compileall, diff check 통과. 테스트는 sandbox 제약으로 `--basetemp`를 scratchpad로 지정해 실행했다. live Ollama·model 조회·warm-up·raw SQL fallback·재학습·실제 PostgreSQL/CNU·frontend·legacy 변경 없음. ADR-055 추가
- Phase 7-09 합성 prototype Ollama live smoke 완료. 신규 `backend/adaptive/api/ollama_prototype_smoke.py`가 Phase 7-08 설정·loopback transport(호출 수만 세는 wrapper)·prototype factory를 in-process `TestClient`로 구동해 flat·legacy × `Course.name`·`Instructor.name` submit 4건, cancel, no-match를 1회씩 실행하고 한 줄 JSON(case별 고정 outcome·호출 수·경과, 성공 수 관찰)만 출력한다. `gpt-6-luna` high review-only(OpenCode `plan`, 첫 실행은 init 단계에서 멈춰 종료 후 stdin을 닫고 1회 재실행)의 지적 2건 중 `columns_match_constraint` 불일치를 경계 실패로 보라는 항목은 Phase 6-09가 filter 사용도 준수로 보므로 기각하고 관찰값으로 명시했으며, 대신 성공 column canonical 검사와 physical 이름 비노출 검사를 추가했다. stdout 한 줄·실제 token/option ID/행 값 비노출 테스트 보강 지적은 반영했다. 관련 167개 통과. 로컬 Ollama `0.34.4`·`text2sql_qwen:latest`로 `--check`(호출 0회)와 `--live-synthetic` 1회를 실행해 경계 `COMPLETED`(호출 4/4)를 확인했으나 submit 4건 모두 `CONSTRAINT_INVALID_QUERY_IR`였다. 진단 호출 3건의 비노출 구조 분류와 offline 재현으로 모델이 질문 전체를 `equals` filter 값으로 복사해 Phase 6-09 reflection guard가 설계대로 거부한 것임을 확인했다. guard·prompt는 바꾸지 않았고 성공 실행 경로의 live 통과는 미확인이다. 전체 pytest 932개·5 skip·470 subtests, compileall, diff check 통과. live 호출은 합성 입력 7회(smoke 4, 진단 3)이며 응답 저장·실제 PostgreSQL/CNU·frontend·legacy 변경 없음. ADR-056 추가
- Phase 5-18 직접 SQL baseline arm 완료. 신규 `evaluation/direct_sql.py`(prompt `direct-sql-v1`·추출 `direct-sql-extract-v1`·raw SQL 정책·catalog 읽기 제한 executor·`positional-row-multiset-v1` 비교·`direct-sql-run-v1` report), `evaluation/arm_comparison.py`(`arm-comparison-v1` paired count), `evaluation/direct_sql_ollama.py`(Phase 7-08 loopback transport 재사용 1회 호출 provider)를 추가했다. gold IR을 기존 compiler로 컴파일해 literal을 넣은 oracle SQL로 40×3 120 attempt가 모두 `CORRECT`였고 IR oracle과 짝지으면 both_correct 120이다(자기 일관성, 모델 점수 아님). `gpt-6-luna` high review-only 지적 5건을 모두 재현·확인해 수정했다: 중첩 CTE 이름으로 `sqlite_master` 참조를 위장하는 정책 우회(조상 WITH 범위 검사·`sqlite_` 접두 거부·executor `SQLITE_READ` allowlist), `None`과 문자열 `'NULL'` 동일 정규화(NULL tag 분리), oracle 여부가 다른 report pairing, 양쪽을 같게 자른 부분 schedule·request ID 미검증 pairing, import 격리 테스트의 imported name 누락. sqlglot 30의 WITH arg key가 `with_`인 점도 반영했다. 관련 76개·126 subtests, 전체 pytest 982개·5 skip·470 subtests, compileall, diff check 통과. live 호출·holdout 30건 실행·실제 CNU/학교 DB·runtime 변경 없음. ADR-057 추가
- Phase 5-19 live paired 비교 완료(잠정). 신규 `evaluation/arm_live_cli.py`가 고정 개발 세트 40×3을 같은 Phase 7-08 설정으로 IR arm(`query-ir-v1`)·SQL arm(`direct-sql-v1`)에 1회씩 실행하고 세 report를 Git 비추적 `.schema-adaptive/reports/<name>/`에 staging 뒤 단일 rename으로 게시한다. `gpt-6-luna` high review-only 4건 중 reports symlink 탈출(+Windows junction), 게시 실패 시 부분 파일, mode flag 반복 허용 3건을 확인·수정했고, `--check`의 임시 fixture 쓰기는 check 목적상 유지하고 계약 문구를 바로잡았다. 테스트 작성 중 SQL provider가 transport factory를 이름으로 묶어 monkeypatch가 우회된 결함을 발견해 module lookup으로 고쳤으며, 그 실패 실행 1회가 존재하지 않는 합성 tag로 로컬 Ollama에 120회 요청했음(server log 전부 404, 생성 없음)을 기록한다. 로컬 Ollama `0.34.4`·`text2sql_qwen:latest` live 1회(240 호출) 결과 IR 정답 90·실행 99·구조 exact 51(5-13과 동일), SQL 정답 17·실행 29·정책 통과 69, paired 둘 다 12·IR만 78·SQL만 5·둘 다 오답 25. 진단 호출 120회의 비노출 분류로 SQL `UNKNOWN_TABLE` 51건 전부 기존 CNU table 참조, `EXECUTION_ERROR` 40건 전부 `no such column`을 확인했다. 전체 pytest 999개·6 skip·470 subtests, compileall, diff check 통과. gold 미승인이라 확정 성능·우열 주장 아님, holdout 30건 미실행, 실제 CNU/학교 DB·runtime 변경 없음

### 2026-09-29

- Phase 5-20 Ollama 내장 SYSTEM 오염 제거와 재실행 완료. `ollama show`와 blob byte로 `text2sql_qwen:latest`의 CRLF Alpaca TEMPLATE과 CNU `courses` schema SYSTEM(한국어는 CP949 byte)을 확인했고, 입력 token 수(기본 161 대 SYSTEM 없는 raw 47, raw 재현 template+임의 SYSTEM 49 = Ollama `system` 지정 49)로 주입과 byte 재현을 검증했다. 신규 `evaluation/ollama_request_format.py`(`model-template`·`alpaca-raw-no-system-v1`)와 `arm_live_cli --ollama-request-format`을 추가해 두 arm에 같은 형식을 적용한다. `gpt-6-luna` high review-only가 지적한 tag·형식 조합의 label·run ID 충돌을 확인해 비기본 형식에 `p520-nosys-` prefix를 두었고 기본 형식 ID는 5-19와 같다. 재실행 결과 IR 정답 93·실행 111·구조 exact 51, SQL 정답 72·실행 92·정책 통과 120, paired 둘 다 68·IR만 25·SQL만 4·둘 다 오답 23이며 최종 코드 재실행에서 모든 수치가 같았다. normalized SQL 실패의 비노출 진단(40 호출)은 `no such column` 27·ambiguous 1이었다. Phase 5-19 결과 문서의 'CNU schema 암기' 해석을 오염 가능성으로 정정했고, 5-08·5-12~5-14·7-09 live 호출도 같은 SYSTEM 주입 조건이었음을 기록한다. 전체 pytest 1004개·6 skip·470 subtests, compileall, diff check 통과. live 호출은 합성 입력으로 token 검증 12회, 비교 480회(재현 포함), 진단 40회이며 runtime·실제 CNU/학교 DB 변경 없음. ADR-058 추가
- Phase 7-10 runtime Ollama 요청 형식 명시 완료. `ollama_request_format`을 `harness`로 옮기고(evaluation은 re-export), `OllamaStartupConfig`를 v2로 올려 `request_format`을 담았다. `build_startup_ollama_model`과 `OllamaDirectSqlModel`이 config 형식을 한 번만 적용하며 `arm_live_cli`는 미리 감싸지 않는다. prototype·smoke CLI는 Ollama 활성화 시 `--ollama-request-format` 필수다. `gpt-6-luna` high review-only 지적 1건(`arm_live_cli`의 `or` 기본값이 빈 형식을 `model-template`로 바꿈)을 확인해 `None`일 때만 기본값을 쓰게 고쳤고 같은 패턴의 빈 `--repository-root`도 거부한다. 관련 258개·1 skip·10 subtests, 전체 pytest 1013개·6 skip·470 subtests, compileall, diff check 통과. no-system 형식으로 7-09 smoke를 `--check`·`--live-synthetic` 1회 재실행해 경계 `COMPLETED`, `Instructor.name` 2건 실행 성공(0행)으로 성공 실행 경로가 처음 live 통과했고, `Course.name` 2건은 질문 전체 `equals` filter로 guard 거부가 유지됨을 진단 4 호출로 확인했다. guard·prompt·frontend·legacy·PostgreSQL 변경 없음. ADR-059 추가
- Phase 7-11 요청 형식을 모델 안으로 이동 완료. Codex 독립 검증(226a633 기준 관련 252개·전체 1013개 통과, no-system live smoke `COMPLETED` 재현)이 보고한 두 결함을 재현했다: no-system 형식에서 non-callable transport가 startup에서 거부되지 않음, 형식이 달라도 `declared_provenance`가 같음. `apply_request_format` 진입 callable 검사, `OllamaQueryModel(request_format=...)` 생성자 단일 적용, no-system 전용 provenance 필드(형식 ID·raw·template SHA-256)로 닫았고 `model-template` provenance(`generation_config_sha256=48ba68c9…`)는 변경 전 값과 같음을 테스트로 고정했다. `gpt-6-luna` high review-only는 actionable 결함 없음. 관련 293개·1 skip·10 subtests 통과. 전체 pytest는 첫 실행(Luna 검토와 동시 실행)에서 1건 실패했으나 이름을 포착하지 못했고, 재실행에서 1018개·6 skip·470 subtests 전부 통과, 시간 민감 테스트 124개 3회 반복도 모두 통과해 부하 관련 flake 후보로 기록한다. compileall, diff check 통과. 5-12·5-13·5-14 결과 문서에 내장 SYSTEM 주입 조건이었다는 추가 기록을 넣었다. live 호출·다운로드·guard·prompt 변경 없음. ADR-060 추가
- Phase 5-21 base instruct 모델 paired 실행 완료(잠정). 사용자 승인으로 `ollama pull qwen2.5-coder:7b-instruct`(4.7 GB, Q4_K_M, blob `60e05f21…`)를 기본 저장소에 받았고, ChatML TEMPLATE과 일반 기본 SYSTEM을 확인한 뒤 사용자 결정대로 `model-template` 형식으로 `arm_live_cli`를 `--check`·`--live-synthetic` 1회 실행했다(코드 변경 없음, 240 호출). SQL arm 정답 90·실행 102·정책 통과 108·기권 12(normalized 24/40), IR arm 0/120 전부 `MODEL_INVALID_OUTPUT`. 비노출 진단 40 호출에서 IR 출력 40건 모두 단일 code fence로만 감싸져 있었고 안쪽은 유효 QueryIR 28·구조 실패 8·semantic 실패 4였다. SQL arm 추출(`direct-sql-extract-v1`)은 fence를 벗기고 IR arm은 벗기지 않는 비대칭이 원인이라 IR 0은 방식 비교로 쓰지 않는다. 재실행 방식은 사용자 결정 대기. 수치는 L1/L3 전 잠정치
- Phase 5-22 대칭 추출 재실행 완료(잠정). `direct_sql.find_code_fences`를 두 arm이 공유하고, 평가 전용 `evaluation/ir_extract.py`(`single-fence-v1`: 문자열에 fence가 정확히 1개일 때만 안쪽을 adapter와 같은 strict JSON 규칙으로 파싱, 그 외 원본 유지)와 `arm_live_cli --ir-extract`(기본 `none`, `single-fence-v1`은 label `.fence`·run ID prefix `p522-`)를 추가했다. `gpt-6-luna` high review-only 지적 2건(fence 안 깊은 중첩 JSON의 `RecursionError` 전파, runtime 격리 테스트의 `import` 분기 누락)을 재현·수정했다. 같은 `RecursionError` 미처리가 runtime `OllamaQueryModel` JSON 파싱에도 있으나 상위에서 고정 오류로 fail closed되므로 이번 범위에서 바꾸지 않고 잔여 위험으로 기록한다. 관련 96개·1 skip, 전체 pytest 1040개·6 skip·470 subtests, compileall, diff check 통과. live 480 호출 결과 fine-tune IR 93·SQL 72(5-20과 동일), base IR 78·실행 84·구조 exact 36·SQL 90, paired base 둘 다 70·IR만 8·SQL만 20·둘 다 오답 22. 수치는 L1/L3 전 잠정치
- Phase 5-23 주장 범위 한정과 schema 변형 확장 완료(잠정). 계획 문서 §15.3·§16.2(연구 질문 6)·§16.4를 갱신하고 ADR-061·ADR-062를 추가했다. `fixtures/coded.py`·`hybrid.py`, `FIXTURE_SUITES`(`core-v1`·`extended-v1`)와 bundle suite 검증, `build_fixture_bundle(variants=...)`(기본 core), runner의 suite 재확인·설정 variant 존재 검사, run contract literal 확장(schema version 유지), 3개 이상 variant에서만 case consistency 계산, 평가 전용 `schema_consistency`(`schema-consistency-v1`: variant별 정답·spread·all-variant correct, 고정 suite 전체일 때만), `arm_live_cli --fixture-suite`(extended는 label `.ext`·run ID prefix `p523-ext`, report에 `consistency.json` 추가)를 구현했다. `gpt-6-luna` high review-only 지적 2건 중 '3개 미만 variant에서도 일관성 산출'은 재현 후 suite 전체 요구로 수정했고, 'runner 설정의 임의 variant 부분집합 허용'은 5-23 이전부터 있던 subset 실행 계약이라 유지하되 일관성 지표에서는 제외된다. 전체 pytest(tests) 1067개·6 skip·610 subtests, compileall·diff check 통과. live 800 호출: core 3종 수치는 5-22와 동일하게 재현, fine-tune IR 31·31·31·31·31(155/200)·SQL 30·11·31·8·10(90), base IR 26×5(130)·SQL 34·24·32·20·23(133), paired fine-tune 둘 다 85·IR만 70·SQL만 5·둘 다 오답 40, base 104·26·29·41. 수치는 L1/L3 전 잠정치
- Phase 8-01 mapping 버전 이력과 이전 버전 재활성화 완료(합성). Phase 8을 8-01~8-07로 분할했다([`docs/prompts/phase8-01-mapping-version-rollback.md`](prompts/phase8-01-mapping-version-rollback.md)). storage `activate_mapping_version`·`list_mapping_versions`, HTTP `ACTIVATE`와 GET `active_mapping_version`·`mapping_versions`, frontend 버전 이력·2단계 확인 재활성화를 추가했고, 최신 내용 재publish 시 최신 버전 재활성화(중단된 activation 복구 포함), activation 기록 선택 필드 `activated_by`·`activated_at`을 넣었다(ADR-063). 자체 테스트에서 strict 비교가 8-01 이전 manifest(새 필드 없음)를 거부하는 회귀를 발견해 manifest·pointer만 None 필드를 제외해 비교하도록 고쳤다. `gpt-6-luna` high review-only 지적 5건을 모두 재현·수정했다: (1) 중단된 publish의 orphan 버전이 포인터 이하로 밀려 재활성화됨, (2) 같은 버전 번호로 교체된 유효 mapping 파일이 재활성화됨 → `current.json`에 게시 버전별 digest 기록(`published_versions`)을 두고 기록·digest가 일치하는 버전만 재활성화·표시, (3) 재활성화 전에 시작된 clarification session이 이전 mapping으로 실행됨 → private DB compile 직전 활성 mapping 일치 guard, (4) 버전 50개 초과 시 활성 버전이 목록에서 빠져 활성 없음으로 표시됨 → 활성 버전 항상 포함, (5) frontend가 latest 표시 없는 이력을 허용 → 비어 있지 않은 이력은 latest 정확히 1개 요구. 관련 backend 40개·frontend 18개, 전체 pytest(tests) 1094개·6 skip·610 subtests, tsc·eslint·compileall·diff check 통과. prototype compiler가 guard wrapper로 바뀌어 기존 타입 단정 테스트 1건을 내부 compiler 확인으로 고쳤다. 관리자 흐름의 browser smoke는 실제 로컬 private storage에 합성 DB가 영구 등록되므로 이번에는 실행하지 않았다. 실제 학교 DB·live 모델 호출 없음
- Phase 8-02 등록 간 schema 비교 완료(합성, 읽기 전용). 순수 `catalog/compare.py`(`catalog-diff-v1`: table·column·FK 변화, 이름 정확 일치, sample·default 비노출, 500개 상한), `compare_registrations`(기준은 게시·활성 등록, 대상은 검토 대기 포함 등록, field별 영향과 그대로 옮긴 mapping의 coverage·compile·AST 검사 통과 여부), `GET /api/adaptive/v1/registrations/compare`(`registration-compare-v1`, 두 query key만 허용), 업로드 결과 아래 `SchemaComparePanel`(registry의 private 활성 DB 중 자기 자신 제외)을 추가했다(ADR-064). `gpt-6-luna` high review-only 지적 3건 중 frontend parser가 before/after와 모순되는 column 변경(같은 nullable의 `NULLABILITY_CHANGED` 등)을 허용하던 결함과 candidate 저장소 오류를 404로 반환하던 결함을 재현·수정했다. '비교가 임시 파일을 쓴다'는 지적은 기존 strict registration reload가 모든 registry 요청에서 만들었다 지우는 임시 검증 사본이라 유지하고, 문서 표현을 '영구 artifact 불변'으로 정확히 했다. 관련 backend 11개·frontend 6개, 전체 pytest(tests) 1105개·6 skip·610 subtests, tsc·eslint·compileall·diff check 통과. 실제 학교 DB·PostgreSQL 연결·live 모델 호출 없음
- Phase 8-03 영향 field STALE 표시·승계 검토 시작 완료(합성). storage `carry_over_review`·`load_succession`, HTTP `CARRY_OVER`와 detail `succession`, 검토 시작 화면의 기준 DB 승계 시작과 field별 `STALE`·승계 표시를 추가했다(ADR-065, [`docs/prompts/phase8-03-stale-carry-over.md`](prompts/phase8-03-stale-carry-over.md)). `gpt-6-luna` high review-only 지적 3건을 모두 재현·수정했다: (1) 비교와 복사 사이에 기준이 재publish되면 다른 활성 버전으로 분류·복사됨 → 비교 버전과 복사 버전 일치·승계 column 존재 재확인, (2) 승계 기록을 최종 경로에 직접 써서 중단 시 잘린 파일이 남아 재시도로 복구 불가 → 임시 파일 뒤 no-overwrite link로 원자적 생성, (3) field 분류가 빠진 기록(예: 승계·STALE 모두 빈 목록)도 표시됨 → 승계∪STALE가 draft 전체 field와 정확히 같아야 표시(backend·frontend 모두). 관련 backend 12개·frontend 4개, 전체 pytest(tests) 1117개·6 skip·610 subtests, tsc·eslint·compileall·diff check 통과. 실제 학교 DB·live 모델 호출 없음
- Phase 8-04 게시·활성 mapping의 고정 test query 미리보기 완료(합성). `api/admin_test_queries.py`(고정 QueryIR 5개, 기존 실행 경로, 최대 5행·행 수·결과 hash), `TestQueryPanel`을 추가했다(ADR-066, [`docs/prompts/phase8-04-test-query-preview.md`](prompts/phase8-04-test-query-preview.md)). 구현 중 compiler가 entity가 아니라 select field의 JOIN tree로 행 단위를 정해 과목·학과만 select하면 schema마다 행 수가 달라지는 것을 확인하고, 모든 test query에 개설강좌 단위 field를 넣어 flat·coded·hybrid에서 같은 hash가 나오게 했다. `gpt-6-luna` high review-only 지적 4건을 모두 재현 또는 코드 확인 후 수정했다: (1) 큰 정수를 float로 비교해 정렬 key가 같아지면 DB 행 순서가 hash에 반영됨 → 정확한 숫자 비교와 tagged 값 tie-breaker, (2) frontend가 고정 query와 다른 column(비canonical 이름 포함)을 받아들임 → canonical field 집합과 선택 query의 select 일치 요구, (3) 재활성화 성공 뒤 상세 갱신이 실패하면 이전 버전 미리보기가 남음 → publish·재활성화마다 미리보기 재생성, 확인된 활성 버전과 다르면 숨김(React 컴포넌트 테스트 환경이 없어 tsc·lint로만 확인), (4) 저장소 오류를 404로 반환 → 500. 관련 backend 7개·frontend 5개, 전체 pytest(tests) 1124개·6 skip·610 subtests, tsc·eslint·compileall·diff check 통과. 실제 학교 DB·live 모델 호출 없음
- Phase 8-05 verified query 기준선·버전 회귀 비교 완료(합성). storage `load_published_version`·`verified-queries.json` 기록, `admin_test_queries`의 공통 실행 함수 분리와 VERIFY·회귀 route, 결과 기록 버튼·버전별 회귀 확인·publish·재활성화 직후 자동 회귀 UI를 추가했다(ADR-067, [`docs/prompts/phase8-05-verified-query-regression.md`](prompts/phase8-05-verified-query-regression.md)). review 디렉터리 허용 파일명을 `_REVIEW_FILENAMES` 하나로 모았다. `gpt-6-luna` high review-only 지적 3건을 재현·수정했다: (1) 원자적 교체 중 프로세스가 죽어 남은 mkstemp staging 파일(`*.json.<random>.tmp`) 때문에 activation 로드가 거부됨(draft·activation 쓰기에도 있던 기존 위험) → 허용 파일명의 staging 잔여물만 무시하고 그 외 모르는 파일은 계속 fail closed, (2) frontend가 고정 query 일부만 담긴 회귀 결과를 받아들임 → 고정 5개 전체·서버 순서 요구, (3) 행 수가 다른 `MATCH`를 받아들임 → `MATCH`는 행 수 일치 요구. 관련 backend 32개·frontend 5개, 전체 pytest(tests) 1131개·6 skip·610 subtests, tsc·eslint·compileall·diff check 통과. 실제 학교 DB·live 모델 호출 없음
- 팀 논의용 전체 실행 명령 추가. `package.json`에 `dev:backend:venv`(legacy backend를 venv Python으로, reload 없이)와 `dev:all`(legacy `:8000`·합성 prototype `:8123`·Vite `:5173` 동시 실행)을 추가했다. 기존 `dev:backend`·`dev:full`·`dev:adaptive-full`은 그대로 둔다(`dev:backend`는 시스템 Python을 쓰며 이 환경의 시스템 Python에는 legacy 의존성이 없다). 사용자 확인: legacy 강좌 DB는 실제 충남대 테이블이 아니라 팀이 구성한 유사 스키마·데이터다(`docs/human-validation-checkpoints.md`에 기록, E3/L5 상태는 변경하지 않음). 앱 내 browser로 legacy 목록 200건·legacy assistant 질의·합성 DB 전환·clarification 결과를 확인했고, SQLite 업로드 이후 관리자 흐름은 확인하지 않았다. venv의 transformers는 고정 버전(4.46.3)이 아닌 5.8.1이지만 legacy backend 시작과 assistant 질의는 정상 동작했다
- Phase 7-12 legacy view 부분 mapping과 선언형 값 변환 완료. 사용자 결정에 따라 legacy 챗봇의 대상 view `v_course_info`를 QueryIR 경로에 등록했다(A, 부분 mapping, 15개 field 비교). 주요 변경은 다음과 같다. (1) `mapping/transforms.py`의 닫힌 목록 `FieldTransform`(`SLASH_FIRST_NUMERIC`, `TIME_TO_HHMM`)과 결과 정규화. (2) PostgreSQL compiler의 변환식 렌더링과 변환 field의 `contains` 거부. (3) AST policy의 exact 변환 AST 비교와, 그 subtree로 한정한 allowlist 예외. (4) inspector의 `include_views` opt-in(기본 query·fingerprint 불변). (5) runtime profile의 `field_transforms`·`unsupported_fields` 검증: 합집합=canonical, 교집합 없음, ontology·column 타입 호환. `unsupported_fields()` 노출, 변환 결과값 정규화 포함. (6) startup profile v2 strict load(v1 불변). (7) `api/legacy_view_profile.py` CLI(`--check`, `--write --live`, `--smoke --live`; 출력은 고정 code와 수치뿐). 부분 profile의 catalog는 15개 전체를 요구하므로 닫힌다(화면 연결은 7-14). `gpt-6-luna` high review-only는 AST policy 우회 경로를 찾지 못했고 3건을 지적했다. CLI parse 오류가 인자를 그대로 출력하는 문제와 반쯤 교체된 profile 쌍을 `UNCHANGED`로 보고하는 문제는 재현한 뒤 고쳤다(고정 code parser, 재생성 byte 비교). 테스트 공백(projection·predicate 개수·순서 변화, v1 profile 로드)은 보강했다. 동시 `--write` 경쟁은 단일 운영자 개발 CLI라 고치지 않고 잔여 위험으로 둔다. 신규 53개와 기존 PostgreSQL·policy·compiler 103개 테스트, 전체 pytest 1184개·6 skip·610 subtests, compileall, diff check가 통과했다. legacy DB 확인은 다음과 같다(읽기 전용 연결, 구조·집계만 확인). view는 6,846행·개설강좌 4,003개이고, 시간표 없는 행은 632개다. `credit_hours`는 전부 `학점/이론/실습` 형식이며, `course_year`는 학년(1~5)이라 연도가 아니다. `--write --live`와 `--smoke --live`는 고정 query 5개 모두 기대대로 통과했다(목록·학점=3·오후 시작·이수구분 포함은 200행 상한이고 형식 검사 통과, 연도 질의는 `MAPPING_MISSING`). scratch 대조에서 한도 아래 조건 3개(학점 1.5, 금요일 18시 이후, 정원 150 초과)는 view 직접 SQL과 결과 multiset이 같았다(75·33·42행). 참고로 legacy prompt가 지시하는 `credit_hours = '3'` 비교는 0행이고, 변환 기준 학점 3은 4,253행이다. 7-15 비교의 관찰 후보이며 성능 주장이 아니다. mapping은 팀이 구성한 legacy DB에 대한 개발자 작성 mapping이며 L2 검토가 아니다. legacy backend·view·legacy DB 변경 없음. ADR-068 추가
- Phase 7-13 legacy view 대상 QueryIR 챗봇 질의 endpoint 완료. `api/chat_query.py`의 `POST /api/adaptive/v1/chat/query`(`adaptive-chat-query-v1`)를 prototype에 등록했다. 검증된 PostgreSQL context 전용이며, Ollama가 없으면 503이다. 모델 호출은 1회이고 처리는 다음과 같다: 구조 검증, 미지원 field는 실행 없이 `UNSUPPORTED`와 label, 반사 guard, 서버 고정 카드 projection, 기존 harness 실행. 응답은 카드 행과 이해한 filter(label·연산자·값)다. live 확인 중 시간 filter 값 `"14:00:00"`이 `HH:MM` 문자열 비교에서 어긋나는 결함을 발견했다. 그래서 `normalize_filter_value`를 변환 계약에 넣고 compiler·policy에 같게 적용했다. `gpt-6-luna` high review-only 지적 2건을 재현해 고쳤다. (1) understood 값에 물리 identifier·SQL 문자열이 되돌아갈 수 있는 문제: 단어 단위 identifier·질문 포함 반사 거부. (2) 초가 0이 아닌 시간 값의 정밀도 불일치: 표현 불가 값은 compiler·policy·챗봇 모두 거부. live 확인(legacy DB 읽기 전용, `text2sql_qwen:latest`·`alpaca-raw-no-system-v1`, 8개 질문, 상태·행 수만 기록) 결과는 다음과 같다. `SUCCEEDED` 6건 중 4건은 50행(모델 limit)이고, 2건은 0행이다: `김 교수님`을 `equals`로 넣은 경우, `오후`를 14:00~15:00으로 해석한 경우. 연도·학기 질문 2건은 `UNSUPPORTED`다. 모델 성능 주장이 아니다. 7-12 smoke도 계속 통과한다. 관련 테스트 152개(66 subtests), 전체 pytest 1233개·6 skip·610 subtests, compileall, diff check 통과. 문자열 `equals` 경향의 처리 방식은 7-15 비교 뒤 결정한다. legacy·frontend 변경 없음. ADR-069 추가
- Phase 7-14 ChatPopup의 DB별 경로 선택과 `dev:all` 연결 완료. `src/adaptive-prototype/chatQuery.ts`에 경로 선택, `adaptive-chat-query-v1` strict parser·client, 카드 key 변환, 안내 문구를 추가했다. `ChatPopup`은 현재 경로를 표시한다. QueryIR 경로에서는 카드·시간표와 함께 SQL 대신 "이해한 조건"을 보이고, 수강 이력 제외를 비활성화한다. 합성·private SQLite DB는 legacy로 질의하지 않고 미지원을 안내한다. 부분 mapping PostgreSQL catalog는 409 `SYNTHETIC_CATALOG_UNSUPPORTED`로 구분하고, 화면은 이때만 챗봇 안내를 보인다. `backend/adaptive/api/dev_launcher.py`와 `dev:adaptive-prototype:legacy` script를 추가했고, `dev:all`이 이를 쓴다. launcher는 legacy `.env`의 세 key만 읽어 legacy-view DSN(프로세스 안)과 loopback Ollama를 켜며, 부분별로 독립 강등한다. `gpt-6-luna` high review-only 지적 6건 중 5건을 재현해 고쳤다. (1) launcher가 모델 실패를 DB 실패로 처리해 둘 다 끌 수 있던 문제: profile 로드·모델 구성·조립을 분리. (2) 완전 mapping PostgreSQL의 일시 catalog 오류도 챗봇 안내로 가리던 문제: 409 전용 code. (3) DB 전환 직후 이전 응답이 새 화면에 붙을 수 있던 경합: layout effect 무효화와 legacy 경로 대상 확인. (4) 결과 문자열 길이 제한 불일치: 서버 1,000자. (5) client 제어 문자 검증. 완전 mapping DB의 연도·학기 카드 상세 미표시는 legacy view에 해당 field가 없어 보류했다. browser 확인(`dev:all`)은 다음과 같다. launcher 상태 `legacy-view=enabled, ollama=enabled`. 합성 flat → 챗봇 미지원 표시. Legacy DB (QueryIR) → 목록 자리 안내(409)와 재시도 버튼 없음, `3학점 전공 과목 알려줘` 50건과 이해한 조건 `학점 = 3`, 연도 질문은 미지원 안내, SQL 패널 없음. Legacy 기준선 → 기존 SQL 경로·SQL 패널, 같은 질문 `교양 과목 중에 금요일 수업`에서 legacy 190행(119과목) 대 QueryIR 50행(모델 limit). 상위 카드 순서와 표시 형식은 같았다. 행 수 차이는 7-15의 limit 정책 항목이다. 관련 Python 테스트 125개, frontend 13개 파일 전체(7-14 신규 8개 포함), tsc·eslint(src 전체), 전체 pytest 1246개·6 skip·610 subtests, compileall, diff check 통과. legacy backend·DB 변경 없음. ADR-070 추가
- Phase 7-15 같은 질문의 legacy 챗봇 대 QueryIR 경로 비교 완료. `backend/adaptive/evaluation/legacy_chat_compare.py`와 질문 세트 `data/legacy-chat-compare-v1.json`을 추가했다. 질문 세트는 개발자 작성 30문항이다(ONTOLOGY 22, OUTSIDE_SCOPE 5, UNSUPPORTED_FIELD 3, 결과 확인 전 고정). `gpt-6-luna` high review-only를 live 실행 전에 받아 지적 9건을 모두 재현해 고쳤다: 기준 SQL AST 형태 검증, redirect 차단, 상한 도달 확인 뒤에만 부분집합 수용, legacy 실제 LIMIT 기록, NULL 분반 보존, 요약 출력에서 질문 원문 제외, 배타적 파일 생성, DB 연결 timeout, 정책 재실행 테스트. live 실행(`dev:all`, 로컬 Ollama, 1회) 결과, ONTOLOGY 범위 수용 가능 문항은 legacy 17, QueryIR 11, `+limit200` 10, `+limit200+contains` 13이다. 요일 정규화를 더한 오프라인 추정은 14다. 미지원 field 거절은 QueryIR 3/3, legacy 0/3이다(legacy는 `2024년`을 4학년으로 조회한 경우 포함). legacy는 학점 형식 불일치(`credit_hours = '3'`)로 3문항이 0행이었다. QueryIR의 주된 실패는 없는 field 이름 생성(`Course.department` 등), 요일·이수구분 값 형식, 부분 학과명 `equals`, 조건 해석 오류다. ontology 밖 질문(학년·선수과목 등)은 legacy 4/5, QueryIR 0~1/5다. 결과는 `docs/phase7-15-legacy-vs-queryir-comparison-results-2026-09-29.md`에 두었다. limit·문자열 완화·코드값 정규화·field 별칭·ontology 확장은 사용자 결정 항목으로 남긴다. 비교 모듈 테스트 38개, 전체 pytest 1284개·6 skip·610 subtests, compileall, diff check 통과. 서버 정책·legacy 변경 없음. 모델 성능 확정 수치가 아니다(L1·L3 전). ADR-071 추가
- Phase 7-16 챗봇 서버 정책 3종 적용과 재비교 완료(사용자 결정). `chat_query.py`에 `CHAT_ROW_LIMIT = 200`과 이름형 문자열 `contains` 완화(`CONTAINS_FIELDS`)를 넣었다. understood 표시는 실제 실행 조건과 같다. `mapping/transforms.py`에 `KOREAN_WEEKDAY`(identity SQL, 요일 값 정규화, 모르는 값 거부)와 변환별 허용 연산자 `transform_operators()`를 추가했다. legacy-view profile에 `Schedule.day`로 선언했고, private profile은 `--write --live --replace`로 갱신했다(fingerprint 불변, smoke 5/5). `gpt-6-luna` high review-only 지적 1건(PostgreSQL `contains`의 LIKE 와일드카드 미escape)을 재현해 고쳤다. compiler와 policy가 같은 `postgres_contains_pattern`을 쓰며, 실제 PostgreSQL에서 escape 동작을 확인했다. 재비교(`p716-run1`, 모델 출력과 legacy SQL은 7-15와 문항별로 동일) 결과, ONTOLOGY 수용 가능 문항은 QueryIR 11→14, legacy 17이다. 개선 문항은 q05·q19·q21·q22이고, limit 200으로 q02·q11·q14·q18이 `EXACT`가 되었다. q20·q24는 50행 상한이 가리던 오류가 드러났다. 둘 다 맞힌 문항 11, QueryIR만 3(학점 형식 2, legacy key 없는 조회 1), legacy만 6(없는 field 이름, 이수구분 값 형식, 조건 해석, 과도한 값 지정)이다. 결과는 `docs/phase7-16-chat-policies-recompare-results-2026-09-29.md`에 두었다. 관련 테스트 224개(77 subtests), frontend 13개 파일 전체, 전체 pytest 1302개·6 skip·610 subtests, compileall, diff check 통과. legacy·frontend 변경 없음. ADR-072 추가
- 2026-09-29 개발 순서 결정(사용자 승인). 순서는 다음과 같다: 7-17 기준 table 보존 LEFT JOIN, 8-06 코드값·값 사전, 합성 변형 benchmark와 규칙 기반 기준선, AI mapping 제안 비교, 8-07 관리자 화면 통합, L1 뒤 prompt·별칭 개선, L1/L3 뒤 Phase 9. 이유는 두 가지다. JOIN 한계와 mapping 오류가 섞이면 AI 비교의 원인을 가를 수 없다. 또 benchmark를 AI보다 먼저 고정해야 공정하다. Phase 8 로드맵에 benchmark·AI 제안 항목을 추가했다.
- Phase 7-17 기준 table 보존 LEFT JOIN 완료(ADR-073). `plan_join_path(anchor_table=)`, compiler `LEFT JOIN`, AST policy anchor 모드(수식어 없는 `LEFT JOIN`만), profile v3 `anchor_table`, 시작 시 매핑 table 전체 부분집합 경로 유일성 검사를 추가했다. `legacy_view_profile.py`를 spec 구조(`--target view|base`)로 일반화하고 legacy 기본 table 4개에 `legacy-base` private profile을 등록했다(smoke 5/5). view와 같은 12 field·변환 4·미지원 3이며, 학과 코드는 JOIN 차이만 보려고 미지원으로 유지했다. anchor 없이는 과목+학과 경로가 모호하다(개설강좌 경유 대 필수과목 table 경유). 실제 DB 동등성 검증 결과다. 카드 전체 관계는 view와 multiset이 같다(6,846행, 시간표 없는 632행 포함). 7-16 답변 22문항을 상한 없이 재실행해도 22/22 같다. INNER JOIN 반례에서는 6,214행으로 줄고 6문항의 답이 달라진다. 같은 30문항 live 비교(`p717-base-run1`)에서는 모델 출력·legacy SQL·판정·key 집합이 모두 30/30 같았다. ONTOLOGY 수용 가능 QueryIR 14, legacy 17이다. `dev:all` launcher는 legacy profile별로 degrade한다. 비교 도구에는 `--queryir-database`를 추가했다. `gpt-6-luna` high review-only 지적 3건을 확인했다. 비교 도구의 기준 SQL이 선택 대상과 다른 DSN 변수를 읽던 문제와, 등록 시 시간 변환 column에 `timestamp`가 통과하던 문제는 재현해 고쳤고 회귀 테스트를 추가했다. 계획 문서의 7-12 전제 갱신 누락은 ADR-068에 해결 표시를 달아 반영했다. 결과는 `docs/phase7-17-anchor-left-join-results-2026-09-29.md`에 두었다. 관련 테스트 208개, 전체 pytest 1339개·6 skip·610 subtests, compileall, diff check 통과. legacy·frontend 변경 없음. ADR-073 추가
- 2026-09-30 Phase 8-06 코드값·값 사전 완료(ADR-074). `mapping/values.py`에 `ValueDictionary`·`bind_filter_value`를 추가했다. compiler와 AST policy가 같은 bind 값을 쓰고, runtime은 결과 저장 값을 표시 이름으로 바꾼다. 챗봇은 표시 이름으로 이해 조건을 보여주고, 인식된 값에는 `contains` 완화를 하지 않으며, `reject` 사전 밖 값은 `NOT_UNDERSTOOD`로 답한다. private profile v4(nullable `anchor_table` + `value_dictionaries`)와 시작 검증을 추가했다. 등록 CLI는 저장 값 존재를 읽기 전용으로 확인한다(`VALUE_MISSING`). legacy 두 profile을 v4로 다시 등록했다(이수구분 9개 값 확인, fingerprint 불변, smoke 5/5). 같은 30문항 live 비교(`p806-base-run1`)에서 모델 응답 상태와 legacy SQL은 7-17과 같았다. q06만 `EMPTY`→`CORRECT_SUBSET`으로 바뀌어 ONTOLOGY 수용 가능이 QueryIR 14→15, legacy 17이 되었다. 남은 격차 5문항은 field 이름·조건 해석·과도한 값 지정으로, prompt·별칭 단계(L1 뒤)의 대상이다. `gpt-6-luna` high review-only 지적 3건을 모두 재현해 고쳤고 회귀 테스트를 추가했다. 문자열 column 타입을 접두사로 판정하던 것은 내장 타입 이름 정확 일치로 바꿨다. 식별 field(과목 코드·분반·학과 코드)의 사전은 결과 key를 바꾸므로 금지했다. 양방향 서식 문자는 거부한다. 결과는 `docs/phase8-06-value-dictionary-results-2026-09-30.md`에 두었다. 관련 테스트 261개, 전체 pytest 1392개·6 skip·610 subtests, compileall, diff check 통과. legacy·frontend 변경 없음. ADR-074 추가
- 2026-09-30 Phase 8-08 합성 변형 benchmark와 규칙 기반 기준선 완료(ADR-075). 사용자 승인으로 로컬 PostgreSQL에 전용 DB `schema_adaptive_synthetic`을 만들었다. 실행마다 무작위 schema를 쓰고 지운다. `evaluation/variant_specs.py`(seed, 12개 변형, 정답 자동 생성, spec catalog)와 `evaluation/variant_benchmark.py`(답안 형식, field 점수, seed 참조 평가, 규칙 기준선, live 실행, CLI `--check`·`--live-synthetic`)를 추가했다. live 실행(`vb-run1`) 결과는 다음과 같다. inspect catalog는 12개 변형 모두 spec catalog와 같았다. oracle은 field 180/180·질문 240/240, 규칙은 field 163/180·기준 table 1/12·질문 205/240이다. 축별 질문은 구조 59/60, 형식 34/40, 코드값 51/60, 의미 50/60, 복합 11/20이다. `gpt-6-luna` high review-only 지적 4건을 모두 재현해 고쳤고 회귀 테스트를 추가했다. 정리 실패와 oracle 미만점은 실행 실패로 처리하고, DB·inspect 오류는 답안 거부와 구분한다. limit에 잘리는 질문은 참조 평가가 거부한다. 결과는 `docs/phase8-08-variant-benchmark-results-2026-09-30.md`에 두었다. benchmark 테스트 48개, 전체 pytest 1440개·6 skip·610 subtests, compileall, diff check 통과. legacy·frontend·runtime 변경 없음. ADR-075 추가
- 2026-09-30 Phase 8-09 AI mapping 제안과 규칙 기반 비교 완료(ADR-076). `evaluation/ai_mapping.py`(column 선택 1회 + field별 값 처리, JSON schema 제한, runtime 규칙 검증기, 분반 table anchor, 저장 제안 재사용)를 추가했다. `variant_benchmark.py`에는 `rules+anchor` 비교군, `--ai-proposals`, 일반화된 집계를 더했다. v1 실행 결과는 질문 AI 223/240, 규칙 205, 규칙+anchor 206, oracle 240이다. 축별로는 형식 40/40, 코드값 51/60(규칙과 같음), 의미 56/60, 복합 16/20이다. AI는 값 사전을 하나도 내지 않았고, 영어 요일에 `KOREAN_WEEKDAY`를 골랐으며, 의미 함정 두 개를 모두 틀렸다. `gpt-6-luna` high review-only 지적 4건을 모두 재현해 고쳤고, `ai-mapping-v1.1`로 올렸다. 고친 것: 변환 설명의 예시가 benchmark 값과 같던 누설, 모델 출력 schema 미검사, 실패 호출의 채점, runtime 사전 모델과의 불일치. v1.1 재실행 답안은 12개 모두 v1과 같았다. v2 후보(저장 값 일치 검사, 값 사전 전용 단계)는 보류 변형과 함께 평가해야 하므로 아직 실행하지 않았다. 결과는 `docs/phase8-09-ai-mapping-results-2026-09-30.md`에 두었다. AI·benchmark 테스트 93개, 전체 pytest 1485개·6 skip·610 subtests, compileall, diff check 통과. legacy·frontend·runtime 변경 없음. ADR-076 추가
- 2026-09-30 개발 순서 갱신(사용자 결정): AI 제안 v2(8-10) → DB별 ontology 확장 field·매개변수형 변환(8-11) → 8-07. 8-11을 둔 이유는 두 가지다. ontology 추가가 코드 수정을 요구하지 않게 해야 한다(공통/DB별 확장/닫힌 변환의 세 층). 첫 사례는 `3/3/0`의 이론·실습 시간이다.
- 2026-09-30 Phase 8-10 AI 제안 v2 완료(ADR-077). `variant_specs.py`에 관리자 설명 `Note`(사유·코드표), 기존 세트 설명 28개, 보류 변형 6개(`HOLDOUT_VARIANTS`, 설명 11개)를 추가했다. `ai_mapping.py` v2에는 저장 값 일치 검사, 값 사전 전용 단계, 설명 입력, 코드표 직접 사전을 넣었고, v1.1은 버전 인자로 재현한다. `variant_benchmark.py`에는 `--suite`, 반복 `--ai-proposals`, 거절 수 집계를 더했다. 모델 실행 전에 세 파일 hash를 기록했다(2026-09-30T04:53:49Z). 결과: 기존 세트 규칙 205, v1.1 223, v2 232, v2+설명 235/240. 보류 세트 규칙 0, v1.1 104, v2 116, v2+설명 116/120. oracle은 두 세트 모두 만점이다. 코드값 축은 두 세트 모두 만점이다. 자연어 설명은 의미 함정 column 선택을 고치지 못했고, 효과는 코드표 직접 사전뿐이다. `gpt-6-luna` high review-only 지적 6건을 재현·반영했다. 반영한 것: 코드표 완전성·중복, 예시 없음, 코드 길이, 답안·trace 일치, 설명 사유 선언, 거절 수 보고. 규칙 밖 설명 1개를 제거한 뒤 해당 조건을 다시 실행했고, 답안은 같았다. 결과는 `docs/phase8-10-ai-mapping-v2-results-2026-09-30.md`에 두었다. AI·benchmark 테스트 169개, 전체 pytest 1561개·6 skip·610 subtests, compileall, diff check 통과. legacy·frontend·runtime 변경 없음. ADR-077 추가
- 2026-09-30 범위 조정(사용자 결정, ADR-078): 합성 benchmark가 원래 table에 과적합되고 있다는 지적을 받아 benchmark 개선과 8-11 일반화 설계를 프로토타입 이후로 미뤘다. 이론·실습 시간은 넣지 않는다. 프로토타입 순서는 8-12 → 8-07 축소판 → browser smoke·시연 절차다. 8-10 함정 가운데 `capacity`(강의실 좌석)와 `credit_hours`(세트마다 다른 뜻)가 비현실적이었다는 점을 결과 문서에 적었다.
- 2026-09-30 Phase 8-12 업로드 흐름 AI mapping 제안 연결 완료(ADR-078). `OllamaJsonModel`·`schema_conforms`·column prompt 조립을 `backend/adaptive/mapping/ai_proposal.py`로 옮겼다. benchmark는 이를 가져다 쓰며 prompt 문자열은 옮기기 전과 같다(전 변형·버전 hash 비교, 테스트 고정). `register_database(mapping_proposer=)`는 메모리 sample로 모델을 한 번 부르고, 계약 검사를 통과한 제안만 저장하며, 실패하면 규칙 제안을 쓴다. 업로드 응답은 `private-sqlite-upload-v2`(`mapping.proposer`: `AI`/`RULES`/`AI_UNAVAILABLE`), 검토 응답은 `private-mapping-review-v2`(후보 `source`)다. 등록은 threadpool에서 실행한다. CLI `--mapping-model-tag`, dev launcher는 loopback이면 기본 켬(`MAPPING_MODEL`로 tag 변경, 상태 `mapping_ai`). 화면에는 제안 출처와 후보별 AI 표시를 넣었다. `gpt-6-luna` high review-only 지적 1건(검토 후보 `review_status`를 아무 문자열이나 받음, 6-19부터)을 재현해 알려진 상태만 받도록 고쳤다. live smoke(이름 변경 합성 DB 1개): AI 첫 후보 15/15, 규칙 1/15, 13초. 결과는 `docs/phase8-12-ai-proposal-upload-results-2026-09-30.md`에 두었다. 관련 backend 204개와 신규 33개, frontend Node 93개·ESLint·tsc·vite build, 전체 pytest 1588개·6 skip·610 subtests(뒤에 추가한 CLI 테스트 6개는 신규 33개 실행에 포함), compileall, diff check 통과. legacy·PostgreSQL 실행 경로 변경 없음. ADR-078 추가
- 2026-09-30 방침 조정(사용자 결정, ADR-079): 기능 구현을 우선해 실제 학교 데이터 노출 방지는 당분간 고려 대상에서 뺐다. 모델을 특정 DB에 묶지 않는 원칙은 유지한다. 약어·fuzzy는 방식(공통 코드)과 지식(DB별 데이터)으로 나눈다.
- 2026-09-30 Phase 8-13 챗봇 값 힌트 완료(ADR-079). 새 모듈 `backend/adaptive/api/value_hints.py`(값 목록·질문 검사·출력 보정)와 `backend/adaptive/harness/postgres_values.py`(읽기 전용 DISTINCT reader, 너무 많음/실패 구분)를 추가했다. `OllamaQueryModel`은 힌트가 있을 때만 Context 다음에 힌트 한 줄을 넣고, 힌트가 없으면 prompt가 이전과 같다. 챗봇은 힌트를 넣은 모델 호출 1회 뒤 보정하고, 기존 검증 경로를 모두 거친다. 레거시 약어(`컴융`·`컴융부`, `전핵` 등 7개)를 레거시 profile 값 사전으로 옮겼다. 뺀 것은 `전기`·`인공지능`·`컴공`이다. 두 private profile은 다시 썼고 smoke를 통과했다. 결과: 새 질문 12개에서 도입 전 3 → 보정만 6 → 힌트+보정 12, 약어 데이터 제외 7. 7-15 30문항에서 해석 범위 수용 가능 15 → 19(레거시 17), 해석 범위 퇴보 없음. `gpt-6-luna` high review-only 지적 6건을 재현했다. 실패 캐시 영구화와 200자 초과 힌트로 인한 챗봇 실패, 이 2건을 고쳤다. field 보정의 상시 동작, 별칭 힌트 유지, 포함 검색 정책(7-16), prompt 속 DB 값의 영향은 결정·한계로 기록했다. 결과는 `docs/phase8-13-chat-value-hints-results-2026-09-30.md`에 두었다. 전체 실행에서 새 테스트가 DB driver를 먼저 import해 import 격리 테스트가 깨진 것을 찾아, reader가 식별자를 직접 quote하고 driver는 연결할 때만 쓰도록 고쳤다. 관련 테스트(값 힌트 35개 포함), 전체 pytest 1629개·6 skip·610 subtests, compileall, diff check 통과. legacy DB는 읽기만 했다. ADR-079 추가
- 2026-09-30 Phase 8-14 챗봇 실행 SQL 표시 완료(ADR-080, 사용자 결정). `chat_query.py`는 executor가 받은 compile 결과를 기록하고, 옵션이 켜져 있으면 bound 값을 SQL literal로 채운 보기용 문장을 성공 답의 `sql`에 넣는다. literal 규칙: 문자열 `'...'`, 역슬래시가 있으면 `E'...'`, 숫자, `TRUE`/`FALSE`, `%%`→`%`. 길이 상한은 UTF-16 기준 20,000이다. `synthetic_prototype --show-sql`, dev launcher 기본 켬(`SHOW_SQL=0`으로 끔, 상태 `chat_sql`). frontend는 성공 응답에서만 선택 key `sql`을 받고, 기존 SQL 패널에 "Executed SQL (QueryIR · 값은 보기용으로 채워 넣음)"로 보여 준다. live 확인에서 `컴융 전공`은 `dept_name = '공과대학 컴퓨터융합학부' AND category LIKE '%전공%'`로 나왔다. "월요일 오후 3학점 교양"에서는 모델이 오후 조건을 빠뜨린 것이 SQL로 드러났다. `gpt-6-luna` high review-only 지적 2건을 재현·수정했다. 역슬래시 값 표기(`E''`)와 길이 계산 단위(UTF-16)다. DB별 약어 방향(자동 후보 + 관리자 승인, 후속 공통 묶음·사용 기록 제안)을 로드맵에 넣었다. SQL 표시 테스트 10개, frontend Node 94개·ESLint·tsc·vite build, 전체 pytest 1639개·6 skip·610 subtests, compileall, diff check 통과. ADR-080 추가
- 2026-09-30 Phase 8-15 화면에서 PostgreSQL 등록 완료(ADR-081, 사용자 결정: 서버 연결 목록). 새 모듈: `backend/adaptive/registration/postgres_registrations.py`(연결·등록·검토·게시 계획·profile 쌍), `backend/adaptive/api/postgres_registration.py`(연결·schema·등록 route, 검토 분기, 실행 중 추가). `LivePostgresContexts`는 router·챗봇·목록·clarification loader가 같은 참조로 보고, 목록은 게시된 PostgreSQL DB를 함께 보여 준다. 검토에 `IGNORE`를 추가했다(SQLite 게시 조건은 불변). dev launcher는 `PG_SOURCE_*`를 읽고, 게시된 등록을 재시작 때 다시 붙인다(연결이 없으면 `SOURCE_MISSING`). 화면: "DB 변경" 창에 PostgreSQL 등록 영역, 검토 패널에 "이 DB에 없음". 창에 최대 높이·스크롤을 넣었다. 실제 PostgreSQL smoke 2개(legacy-like, credits-slash; 임시 schema 생성·삭제)와 브라우저 확인(레거시 `public` 등록 → AI 제안 → 검토 시작, 확인 뒤 파일 삭제)을 했다. `gpt-6-luna` high review-only 지적 4건을 재현·수정했다: clarification loader 공유, DB 수 상한, profile 쌍 원자성, 목록 스냅샷. 결과는 `docs/phase8-15-postgres-registration-results-2026-09-30.md`에 두었다. PostgreSQL 등록 테스트 25개, frontend Node 99개·ESLint·tsc·vite build, 전체 pytest 1664개·6 skip·610 subtests, compileall, diff check 통과. legacy DB는 읽기만 했다. ADR-081 추가
- 2026-09-30 Phase 8-07 축소판 값 사전 편집 완료(ADR-082). 새 모듈 `backend/adaptive/registration/postgres_dictionaries.py`(저장·편집 대상·규칙/AI 후보·충돌 검사)와 `/api/adaptive/v1/postgres/dictionaries`(GET, POST SET/REMOVE/SUGGEST)를 추가했다. 게시가 사전을 적용하고 저장 값을 다시 확인한다. reader에 정확 일치 확인 `present`를 더했다. 화면에 `ValueDictionaryPanel`을 추가했다: field·저장 값 선택, 뜻·약어 입력, 규칙/규칙+AI 후보, 저장 전 게시 차단, 새로고침. 챗봇 보정에 select 와일드카드 제거를 더했다. live(`weekday-english` 임시 schema): "월요일 수업" 사전 없이 0건 → 코드표 게시 후 2건. 브라우저에서 레거시 `public` 등록 → 학과명 승인 → `컴융` 저장·규칙 후보까지 확인했다. 임시 파일은 지웠다. `gpt-6-luna` high review-only 지적 6건을 재현·수정했다: 큰 column 존재 확인, 큰 column 충돌, 편집 대상에서 빠진 항목 삭제, 후보 field 기억, 저장 전 게시 차단, 검토자 길이. 결과는 `docs/phase8-07-value-dictionary-editor-results-2026-09-30.md`에 두었다. 값 사전 테스트 24개, frontend Node 103개·ESLint·tsc·vite build, 전체 pytest 1691개·6 skip·610 subtests, compileall, diff check 통과. legacy DB는 읽기만 했다. ADR-082 추가
- 2026-09-30 Phase 8-16 등록 목록·browser smoke·시연 절차 완료(ADR-083). `GET /api/adaptive/v1/postgres/registrations`와 화면 "등록된 PostgreSQL DB"(열기)를 추가했다. 등록 기록에 연결 신원을 넣어 게시·재시작 때 대조한다(`SOURCE_CHANGED`). PostgreSQL 이름은 runtime 규칙을 등록 때 적용한다(`INVALID_LABEL`). 학점 형식은 앞 0 생략(`1/.5/1`)을 허용한다. browser smoke(새 코드를 8124·5174 포트에 띄움)에서 레거시 `public`을 등록 → 13개 승인·2개 무시(학년 `course_year`, 수강 기록 `enrollment.semester`) → `컴융` 약어 → 게시 v1 → 새로고침 뒤 다시 열기 → DB 선택 → "컴융 전공 과목" 82건·SQL 확인 → "학과공통"·"월요일 오전" 확인까지 진행했다. 끝나고 파일을 지웠다. smoke에서 이름 규칙 불일치와 학점 형식 결함을 찾아 고쳤다. `gpt-6-luna` high review-only 지적 4건(연결 바꿔치기, 손상 draft 목록, 목록 이름 규칙, 연결 없음 시 목록 숨김)을 재현·수정했다. 시연 절차는 `docs/demo-procedure.md`, 결과는 `docs/phase8-16-registration-list-and-smoke-results-2026-09-30.md`에 두었다. 관련 테스트(PostgreSQL 등록·값 사전 54개), frontend Node 107개·ESLint·tsc·vite build, 전체 pytest 1696개·6 skip·610 subtests, compileall, diff check 통과. legacy DB는 읽기만 했다. ADR-083 추가
- 2026-09-30 Phase 8-17 다른 학교 합성 DB 화면 검증 완료(ADR-084). `backend/adaptive/fixtures/other_school.py`로 로컬 합성 DB에 schema `other_school`을 만들었다(강좌 102개, 시간표 143행; 로마자 약어 이름, 이수 구분 코드 table, 학기 코드, `MON` 요일, `char(4)` 시간, 분리된 학점, 교수 소속 학과, 주 2회 수업). 확인용 서버(8124·5174)에서 화면으로 등록 → AI 첫 후보 13/15 → 학기·교수명 remap → 약어 `소웨`·`전전`·`기공`과 학기·요일 코드 이름 → 게시 → 강의 목록·챗봇을 확인했다. 결함 4건을 고쳤다. 첫째, 교수→학과 FK 때문에 모호해 게시가 막혔다. planner가 길이가 같을 때 ontology 직접 관계만으로 된 유일한 계획을 고르게 했다. 둘째, `0900` 시간을 글자로 비교했다. `HHMM_TEXT_TO_HHMM` 변환을 추가했다. 셋째, 주 2회 수업이 있으면 강의 목록이 계약 위반이었다. 같은 강좌 행을 합치게 했고, 한국어 요일도 시간표에 놓는다. 넷째, 약어 길이와 runtime 게시 차단 안내가 원인과 달랐다. 실행 전에 고정한 질문 12개를 11/12 맞혔다. 실패한 "1학년 대상"은 ontology에 없는 조건을 이수 구분에 넣은 것이며, 한계로 기록했다. `gpt-6-luna` high review-only 지적 2건 중 fixture의 `DROP SCHEMA ... CASCADE`를 자기 table만 지우는 방식으로 고쳤고 합성 DB에서 재현했다. AI mapping 평가 prompt의 변환 목록은 hash로 고정한 것이라 그대로 두었다. 결과는 `docs/phase8-17-other-school-results-2026-09-30.md`에 두었다. 관련 테스트(합성 학교·planner·변환·값 사전), frontend Node 113개·ESLint(변경 파일)·tsc·vite build, 전체 pytest 1719개·6 skip·610 subtests(fixture drop 수정 뒤 해당 테스트 8개 재실행), compileall, diff check 통과. legacy DB는 읽지 않았다. 합성 schema는 시연용으로 남겼다. ADR-084 추가
- 2026-10-01 Phase 8-18 공통 항목 대상 학년·항목 목록 일반화 완료(ADR-085). 공통 ontology를 버전 2로 올려 `Course.target_grade`를 추가했다. 버전 1 기록은 모두 유효하다(`since_version`, `accepts_version`, `fields_at`, `at_version`). runtime은 옛 mapping을 자기 버전 항목으로 검사하고, 새 항목은 unsupported로 둔다. 챗봇 카드는 `CARD_FIELDS`(ontology 순서)를 쓰고, 강의 목록은 15개를 유지한다. SQLite 게시는 강의 목록 15개만 필수로 하고, 차단 목록도 이 기준으로 보인다. carry-over는 `NOT_IN_BASE`를 기록하고 옛 버전 기준을 허용한다. 레거시 profile(view `target_year`, base `subject.target_year`)을 다시 만들었다(legacy DB 읽기만, smoke 통과). 확인용 서버에서 레거시 "1학년 과목"·"컴융 3학년 전공 과목"(33건)·"2학년 교양 과목"(14건)을 확인했다. 8-17 가상 학교를 다시 등록해 고정 질문 12/12를 맞혔다. q12 정답은 잘못된 전제(대상 학년 없음)로 정했던 것이라 1학년 과목으로 바꿨다. AI는 대상 학년을 학기 코드로 잘못 제안했다. `gpt-6-luna` high review-only 지적 4건 중 3건을 고쳤다(평가 세트 fingerprint, dataset profile 새 항목, carry-over 버전). 기존 PostgreSQL 등록의 draft 올리기는 범위에서 빼고 한계로 기록했다. 결과는 `docs/phase8-18-target-grade-results-2026-10-01.md`에 두었다. 전체 pytest 1730개 통과·4개 실패(버전 1을 고정한 테스트 2개) → 고친 뒤 해당 파일 19개 재실행 통과, 6 skip·606 subtests. frontend Node 115개·ESLint·tsc·vite build, compileall, diff check 통과. legacy DB는 읽기만 했다. ADR-085 추가
- 2026-10-01 사용자 결정으로 데이터 전제와 사람 관문을 재정의했다(ADR-086). 실제 학교 DB는 받지 않는다고 보고, 레거시 DB(이전 학기 공개 수강편람 엑셀 기반)와 흉내 낸 DB만 쓴다. E3은 원본 재배포 금지 조건으로 사용, L5는 시연 자유(출처 표기, 학교 밖 공개 자료에서는 교수명 가림), L2는 관리자 검토 화면, L1/L3는 사용자와 팀원 검토, L4는 유지로 정했다. 독립 학교 평가는 팀원이 만든 모사 DB로 하며, AI는 평가 전까지 보지 않는다. 공통 ontology 변경은 사용자 결정으로 확정한다(8-18 대상 학년 확정). `AGENTS.md`의 운영 원칙과 `docs/human-validation-checkpoints.md`를 갱신했다. L1 검토 표는 Phase 9 진입 때 준비한다. 다음 개발은 8-19 DB별 확장 항목이다.
- 2026-10-01 Phase 8-19 DB별 확장 항목 완료(ADR-087, 8-11 축소판). `mapping/extensions.py`, `registration/postgres_extensions.py`, `GET/POST /api/adaptive/v1/postgres/extensions`, 화면 "확장 항목" 패널을 추가했다. 게시하면 mapping과 profile v5(`extension_fields`)에 들어가고 서버 재시작 때 다시 읽는다. runtime은 확장 항목의 이름·연결·타입·겹침을 검사한다. 챗봇은 DB별 항목 목록(공통 + 확장)을 쓴다. 확장 항목에만 prompt label을 붙이고, 카드에 `ext_<key>` 열과 `extensions` 이름 목록을 보인다. 글자 확장 항목은 포함 검색과 값 힌트를 쓴다. 가상 학교 DB에 강의실 column을 추가하고 q13·q14를 고정해 14/14를 맞혔다. 화면 양식으로 단과대학을 추가해 "IT대학 과목" 57행을 확인했다. 처음에는 값 힌트가 확장 항목을 몰라 0행이었고, 고쳤다. 레거시 DB를 화면 경로로 등록해 강의실(교111 19행, DB 직접 조회와 일치)·수업방식을 확인했다. 값만 있는 질문("교111에서 하는 수업")은 값이 500개를 넘어 힌트가 없어 틀렸고, 한계로 기록했다. AI mapping 첫 후보는 catalog가 바뀌자 16개 중 10개만 맞았다(8-18은 13개). `gpt-6-luna` high review-only 지적 2건(live-capture label 형식, data_type 목록 500)을 재현·수정했다. 결과는 `docs/phase8-19-db-extension-fields-results-2026-10-01.md`에 두었다. 전체 pytest 1741개 통과·2개 실패(Luna 지적 1번 live-capture 형식, 수정 뒤 capture 테스트 25개 재실행 통과), 6 skip·611 subtests. 확장 항목 테스트 15개, frontend Node 119개·ESLint·tsc 통과, compileall, diff check 통과. legacy DB는 읽기만 했다. ADR-087 추가
- 2026-10-01 Phase 8-20 모르는 조건 처리 완료(ADR-088). 공통 항목 밖 개념 목록 `backend/adaptive/ontology/outside-concepts.yaml`(데이터 11개, 공통 ontology 이름·별칭과 겹침 거부)과 `api/unknown_conditions.py`를 추가했다. 질문이 그 DB에 확장 항목이 없는 개념을 말하면 모델 출력과 상관없이 실행하지 않고 `UNSUPPORTED`(`{concept, label}`)로 답한다. 모델이 찾은 더 긴 값의 일부인 말은 제외한다. 결과가 0건이면 같음·포함 조건을 하나씩 `limit 1`로 같은 실행 경로에서 다시 돌려 `unmatched`로 알린다. 처음에는 값 힌트 값 목록으로 판단했으나, 레거시에서 모델이 "교111"을 과목 코드(값 500개 초과)에 넣어 판단할 수 없어 바꿨다. 화면은 두 경우의 문구를 보인다. 실측 중 값 힌트 cache가 schema fingerprint로만 key를 잡아, 다시 게시해 확장 항목·약어를 더해도 서버 재시작 전까지 예전 힌트를 쓰는 버그(8-07·8-19부터)를 찾아 mapping 버전을 key에 넣었다. 레거시 개발자 profile 고정 기대 9/9(개념 7개 UNSUPPORTED, 0건 진단 2개), 기존 질문 결과 불변. 가상 학교는 확장 항목 없이 "IT관 303호" UNSUPPORTED, 확장 항목 선언 뒤 고정 질문 14/14. `gpt-6-luna` high review-only 지적 3건(호실 패턴의 공백, 여러 단어 값 면제, 숫자 진단과 지침 불일치)을 재현해 앞의 둘은 코드를, 셋째는 지침을 고쳤다. 결과는 `docs/phase8-20-unknown-conditions-results-2026-10-01.md`에 두었다. 전체 pytest 1789개 통과·6 skip·611 subtests(Luna 수정 전), Luna 수정 뒤 관련 테스트 169개 재실행 통과. 8-20 테스트 47개, frontend Node 125개·ESLint(src·tests/frontend)·tsc·vite build 통과, compileall, diff check 통과. legacy DB는 읽기만 했다. ADR-088 추가
- 2026-10-02 사용자 결정으로 개발 범위를 PostgreSQL DB 간 적응성으로 좁혔다(ADR-089). 업로드 SQLite DB 확장은 범위 밖으로 표시했다. 기존 SQLite 기능은 그대로 둔다. 다음 단계 8-21(형식이 다른 PostgreSQL DB 대응 점검)을 로드맵에 추가했다. 문서 변경만이라 테스트는 생략했다. ADR-089 추가
- 2026-10-02 Phase 8-21 지침(`docs/prompts/phase8-21-postgres-format-variety.md`)을 쓰고 형식 D(넓은 단일 table)를 확인했다. 합성 DB에 `other_school` 내용을 한 table로 모은 `wide_school` schema를 만들고 화면 API로 등록·검토·게시했다. 코드 수정 없이 동작했고 AI 첫 후보 14/16, 고정 질문 11/12였다(q11은 같은 질문에서 맞는 답과 시간 조건을 뺀 답이 번갈아 나오는 모델 흔들림). 결과는 `docs/phase8-21-wide-table-results-2026-10-02.md`. 코드 변경이 없어 테스트는 생략했다. 형식 A·B·C는 다음 사용량 주기에 한다
- 2026-10-05 Phase 8-21 형식 A(공통코드 table) 완료(ADR-090). 합성 DB `code_school`(이수구분·요일·학기를 공통코드 table 하나에, FK 없음)을 화면 API로 등록했다. 관리자가 코드 column에 연결하고 값 사전을 입력하면 q01~q12 12/12였다. 실행 전에 고정한 a01 "소웨 전공 과목"·a02 "교양 과목"은 실패했다. 이름 일부가 코드 column에서 `LIKE`로 0건이었고, 모델이 "전공"을 뺐다. `contains_label_values`로 compiler·AST policy가 같은 `(LIKE OR IN)` 모양을 쓰게 하고, 공유 앞부분 값 힌트를 더해 14/14가 되었다. `gpt-6-luna` high 지적 1건(별칭 일부로 레거시 결과가 늘어남)을 재현해, 코드 항목만·이름만 찾도록 고쳤다. 레거시 SQL과 결과(82·33건)는 그대로다. AI mapping 첫 후보는 9/16이었다(코드 column 후보 없음). 결과는 `docs/phase8-21-code-table-results-2026-10-05.md`. 전체 pytest 1804개 통과·6 skip·611 subtests(Luna 수정 전), 수정 뒤 관련 테스트 250개 재실행 통과, 8-21 테스트 13개, compileall·diff check 통과. 코드 변경이 backend뿐이라 frontend 검사는 생략했다
- 2026-10-06 Phase 8-21 형식 B(교시 시간표) 완료(ADR-091). 합성 DB `period_school`(시간표를 30분 교시 번호로 저장)을 화면 API로 등록했다. 첫 실행에서 정수 교시 column이 변환 없이 게시되었고, "오후 3시 이후"는 SQL 오류로 HTTP 500이었다. 시간 항목의 값 사전 중 이름이 모두 `HH:MM`인 것을 교시표로 보고, 시간 비교를 `IN (교시 목록)`으로 바꿨다(`time_label_values`, compiler·policy 공통). 맞는 교시가 없으면 SQL 없이 0건과 이유를 답한다. 교시표가 없으면 게시를 막고 항목을 알린다. 교시표 항목은 형식으로 검사하며, 값 사전 패널은 교시표 항목에 자유 입력을 준다. 값 읽기는 column을 글자로 읽는다. 고정 질문 11/13(q11 시간 조건 누락, b01 종료 시간으로 이해: 모두 모델 이해). "금요일 12시 이전에 시작하는 수업"은 정답 7개였다. 레거시 82·200·33건은 그대로다. `gpt-6-luna` high 지적 1건(0을 붙인 교시 번호가 카드 표시를 깨뜨림)을 재현해 고쳤다. 결과는 `docs/phase8-21-period-results-2026-10-06.md`. 전체 pytest 1826개 통과·6 skip·611 subtests(Luna 수정 전), 수정 뒤 관련 테스트 180개 재실행 통과, 8-21 B 테스트 21개, compileall·diff check 통과. 코드 변경이 backend뿐이라 frontend 검사는 생략했다
- 2026-10-06 Phase 8-21 형식 C(한 칸에 묶인 시간표) ① 완료(ADR-092). 합성 DB `packed_school`(강좌마다 `월 13:30-14:45, 수 …` 글자 한 칸)을 화면 API로 등록했다. 시작·종료 시간은 "이 DB에 없음"으로 두었다. 첫 실행 12/13이었고, q05 "월요일 수업"이 같음 비교로 0건이었다. 닫힌 변환 `PACKED_WEEKDAY`를 추가했다. 요일은 포함으로 찾고, 엄격한 값 형식일 때만 서버 규칙이 이 변환을 붙인다. 게시 때 값이 50종 넘는 column은 표본으로 이 변환만 판단한다. 결과는 13/13이다(q11·b01 시간 질문은 기대대로 거절). `gpt-6-luna` high 지적 1건(표본이 숫자 변환 결정에도 쓰여 실패할 수 있음)을 재현해, 표본으로는 이 변환만 고르게 고쳤다. 레거시 82·200건은 그대로다. 결과는 `docs/phase8-21-packed-results-2026-10-06.md`. 8-21의 형식 A·B·C·D 점검을 마쳤다. 전체 pytest 1838개 통과·6 skip·611 subtests(Luna 수정 전), 수정 뒤 관련 테스트 64개 재실행 통과, 8-21 C 테스트 13개, compileall·diff check 통과. 코드 변경이 backend뿐이라 frontend 검사는 생략했다
- 2026-10-06 Phase 8-22 팀원 DB 평가 실행 도구 완료(ADR-093). `backend/adaptive/evaluation/blind_eval.py`(`check|run|report`)를 추가했다. 질문 파일 형식은 `blind-eval-questions-v1`이고 정답은 과목 코드 또는 거절이다. check는 내용을 출력하지 않는다. run은 첫 실행 때 해시를 잠그고 실행 번호를 먼저 잡으며, 실행을 덮어쓰지 않는다. 기록에는 commit·모델·게시 버전·결과 개수가 남는다. report는 빠진·다른·미완료 실행을 경고한다. 요청은 loopback 전용이고 proxy·redirect를 쓰지 않는다. 팀원용 안내는 `docs/teammate-evaluation-db-guide.md`다. 개발용 `code_school`로 첫 실행·재실행 15/15를 확인했고, 평가 폴더와 등록은 지웠다. `gpt-6-luna` high 지적 5건(proxy·redirect, 잠금 삭제, 동시 실행, 링크 폴더, 빠진 실행 보고)을 재현해 모두 고쳤다. 결과는 `docs/phase8-22-blind-evaluation-results-2026-10-06.md`. 전체 pytest 1863개 통과·6 skip·611 subtests(Luna 수정 전), 수정 뒤 평가 도구 테스트 28개 통과·1 skip(symlink 권한 없음), compileall·diff check 통과
- 2026-10-06 Phase 9 진입: L1 검토 표 준비 완료(ADR-094). `backend/adaptive/evaluation/l1_review.py`(`build`, `record`, `--blind`)와 `docs/l1-review/fixture-v1-review.md`·`.csv`(합성 benchmark 70개, seed 표, 한국어 gold 조건, 정답 행, 자동 확인 메모: 빈 결과·같은 내용 행·질문 표현과 조건 값의 차이·경계값·같은 뜻 묶음)를 추가했다. 판정은 사용자 먼저, 그다음 팀원이 한다. `record`는 형식만 확인해 기록하고 gold의 review_status는 바꾸지 않는다. 팀원 질문 파일의 표는 private 폴더에만 만들고 출력하지 않는다. `gpt-6-luna` high 지적 5건(다른 gold의 표 기록, 내용 없는 표 승인, 링크로 private 밖 쓰기, Excel 수식 주입, 검토일 누락)을 재현해 모두 고쳤다. 지침은 `docs/prompts/phase9-01-l1-review-table.md`. 전체 pytest 1878개 통과·7 skip·611 subtests(Luna 수정 전), 수정 뒤 L1 도구 테스트 15개 통과·1 skip(symlink 권한 없음), compileall·diff check 통과
- 2026-10-06 Phase 8-23 AI mapping 제안 개선(ADR-095). 측정 도구 `backend/adaptive/evaluation/school_mapping_check.py`(화면 등록과 같은 경로, 개발용 schema 5개, FK로 이어진 column은 같은 정답)를 추가했다. 기준선은 1회당 55/80(규칙만 5/80)이었다. 예시 값 표본(10개)으로 65/80, prompt v3(한국어·로마자 항목 이름, 수강편람 관례 4줄)로 75/80이 되었다. join할 수 없는 선택 제거와 리뷰 수정 뒤에도 75/80이다. 남은 틀림은 코드형 학기, 교시 시작·종료, 묶인 시간표 요일/시작 위치다. 레거시 public 제안은 8-19와 같다(14개, 알려진 함정 2개). `gpt-6-luna` high 지적 3건(예시 값 prompt 주입, 숫자 column 검사의 정당한 선택 제거, FK 묶음 검사의 view·동률 문제)을 재현해 고쳤다. benchmark v1.1·v2 prompt는 그대로다. 결과는 `docs/phase8-23-ai-mapping-results-2026-10-06.md`. 전체 pytest 1898개 통과·8 skip·611 subtests(Luna 수정 전), 수정 뒤 관련 테스트 204개 재실행 통과, compileall·diff check 통과
- 2026-10-06 Phase 8-24 코드표에서 값 사전 가져오기 완료. 값 사전 API에 `IMPORT`(field, 코드표 table·코드·이름 column, 선택 구분 column·값)를 추가했다. 읽기 전용 `pairs` 읽기(64쌍까지)를 쓰고, 그 field 데이터에 있는 코드만 넣으며 기존 약어는 둔다. 한 코드·두 이름, 같은 이름, 64개 초과, 교시표 field는 거절한다. 화면 값 사전 패널에 "코드표에서 가져오기"를 붙였다. `code_school`을 이수구분·요일·학기 사전 입력 없이 등록하고 공통코드 세 그룹(5·5·2개)을 가져와 게시하자, 코드 관련 고정 질문이 8/8이었다. 화면에서도 가져오기를 확인했다. `gpt-6-luna` high 지적 3건(맞는 코드가 없을 때 충돌로 거절, parser의 개수 상한, 한국어·공백 이름으로 table 목록 실패 — 8-19 확장 항목 parser 포함)을 재현해 고쳤다. 결과는 `docs/phase8-24-code-table-import-results-2026-10-06.md`. 전체 pytest 1917개 통과·8 skip·611 subtests(Luna 수정 전), 수정 뒤 관련 backend 41개·frontend Node 131개 통과, ESLint(src·tests/frontend)·tsc·vite build·compileall·diff check 통과
- 2026-10-06 팀 논의 자료 작성. 팀원 논의 전에 공유할 수 있게 `docs/team-discussion-brief-2026-10-06.md`에 지금까지 만든 것(관리자 등록~게시, 챗봇 처리 순서, 평가 도구), 확인한 DB 형식과 잠정 수치, 숫자의 한계, 팀에게 부탁할 일(L1 판정, 평가용 DB, L3, L4), 팀이 정할 것(평가 DB 분담, 묶인 시간표 시간 해석, 공통 항목, 공통 항목 밖 개념 목록, 시간 표현 안정화 시점, L4)을 정리했다. 결정이나 사람 관문 승인을 담지 않는다. `gpt-6-luna` high 사실 확인 지적 2건(게시 검사를 "실행 검사"로 과장, 0건 진단의 조건 제한 누락)을 코드로 확인해 고쳤다. 코드 변경 없음, diff check 통과
- 2026-10-06 Phase 8-25 챗봇 시간 표현 안정화 완료(ADR-096, 사용자 결정으로 L1 전 예외). `backend/adaptive/api/time_hints.py`가 질문의 시간 표현을 닫힌 한국어 규칙으로 읽는다. 읽은 조건은 값 힌트 줄에 연산자와 함께 넣고(연산자 힌트가 없으면 prompt 불변), 모델의 시작·종료 시간 조건을 맞추거나 빠졌으면 더한다. 그 DB에 없는 시간 항목이면 "정보 없음"이다. 힌트를 받지 않는 모델에는 힌트 없이 고침만 한다. 실행 전에 시간 질문 7개와 정답을 고정했다. 테스트 포트에서 `other_school` 19/19(반복 20/20), `period_school` 18/19(교시 올림으로 고정 정답과 다른 t03, 챗봇 답은 그 DB 기준 정답), `packed_school` 19/19였고, 레거시 200·82·33건은 그대로였다. `gpt-6-luna` high 지적 4건(부정·모순 표현, 밤 12시, 위치 전용 `value_hints`, field별 허용 연산자)을 재현해 고쳤다. 기존 테스트 하나는 시간 값 검증 사례를 시간 말이 없는 질문으로 옮겼다(시간 말이 있으면 이제 고쳐짐). 결과는 `docs/phase8-25-time-expressions-results-2026-10-06.md`. 전체 pytest 1981개 통과·8 skip·611 subtests(Luna 수정 뒤), compileall·diff check 통과
- 2026-10-06 Phase 9-02 L4 사용성 점검 진행 자료 작성. `docs/l4-usability/`에 진행자 안내(준비·`SHOW_SQL=0`·소개 대본·과제별 관찰 요점·알려진 한계·되돌리기), 질문 문장 없는 과제 카드(학생 S1~S8, 선택 관리자 A1~A4: 지어낸 `other_school` 등록·검토·약어·게시), 관찰 기록 CSV, 사후 질문, 결과 요약 양식(심각도·처리·관문 기록 줄)을 두었다. 개인정보는 기록하지 않고 채운 원본은 git 밖(`.schema-adaptive/l4/`)에 둔다. 진행과 통과 판단은 팀이 한다. 진행 중 발견: 챗봇의 질문 작성 가이드 문구("오전 2시는 2시, 오후 2시는 14시")가 8-25 뒤 QueryIR의 시간 해석과 다르다(진행자 안내의 알려진 한계에 적음, 레거시 화면과 공유하는 문구라 이번에 고치지 않음). `gpt-6-luna` high 지적 3건(켜 있는 서버에서는 폴더 삭제로 등록이 지워지지 않음, ID·Label 규칙, S8이 S7의 실패 답 뒤에 대상이 없을 수 있음)을 코드로 확인해 고쳤다. 코드 변경 없음, diff check 통과
- 2026-10-06 Phase 9-03 팀원용 환경 점검 명령과 8-25 가이드 문장 후속. (1) 사용자 결정으로 챗봇 "질문 작성 가이드"의 시간 문장을 QueryIR 화면에서만 8-25 규칙에 맞게 바꿨다(`QUERYIR_TIME_GUIDE`). 레거시 화면 문장은 그대로이며, 테스트 포트 화면에서 두 경우를 확인했다. (2) `python -m backend.adaptive.env_check`: Python·`.venv`·패키지(`.venv` Python 기준, torch 포함), Node·`node_modules`, `backend/.env` 키, 레거시 DB·`PG_SOURCE_*`·합성 DSN 읽기 전용 접속, Ollama(loopback, proxy·redirect 없음)와 모델, 포트, `.schema-adaptive` 쓰기를 확인하고 해결 방법을 보인다. DSN·비밀번호는 출력하지 않고 접속 실패는 오류 종류만 보인다. 표준 라이브러리만 먼저 import해 패키지가 없어도 돈다. 팀원 안내에 명령을 넣었다. 첫 `gpt-6-luna` 리뷰는 opencode가 출력 없이 멈춰 약 3.5시간 뒤 사용자 질문으로 발견해 중단했고, 20분 제한으로 다시 돌렸다. 지적 8건 중 6건(redirect를 따라감, 읽기 전용 설정 실패 때 연결 누수, 패키지를 부른 Python 기준으로 검사, torch 누락, POSIX에서 dev:all 경로 불일치, node_modules 앞부분 일치)을 확인해 고쳤고, 1건(모델·연결 이름 표시)은 의도된 동작이라 지침 문구만 고쳤으며, 1건(npm 테스트 스크립트 목록)은 기존 관례(`node --test tests/frontend/*.test.ts`)라 두었다. 실제 실행에서 시스템 Python으로 돌리면 DB 확인을 못 하는 것을 보고 WARN과 `.venv` 실행 안내로 바꿨다. 전체 pytest 1996개 통과·8 skip·611 subtests(env check 리뷰 수정 전 코드), 수정 뒤 env check 테스트 22개 통과(다른 코드는 이 모듈을 쓰지 않음), frontend Node 133개·tsc·ESLint(src·tests/frontend)·vite build·compileall·diff check 통과
- 2026-10-08 중간 점검 자료 작성(`docs/midterm-review-2026-10-08.md`, 팀원 공유용 쉬운 설명: 만든 것, 잠정 수치와 한계, 더 고려할 것, 팀이 정할 것 8개, 앞으로의 순서). 결정·승인 없음. `gpt-6-luna` 사실 확인은 opencode가 출력 없이 20분 제한에 걸려 결과가 없었고, 대신 수치·날짜·절차를 저장소 문서와 로컬 모델 목록으로 직접 대조했다. 코드 변경 없음
- 2026-10-08 opencode 리뷰 멈춤 원인 확인. `opencode run`은 표준 입력이 pipe면 EOF까지 읽고 시작한다. 닫히지 않는 입력에서는 모델 호출 전(`init`·`cleanup` 로그 뒤)에 멈춘다(재현: 열린 pipe 45초 제한 초과, `< /dev/null`은 6초에 응답). 리뷰 명령은 `timeout 1200 opencode run ... < /dev/null`로 돌린다. 코드 변경 없음
- 2026-10-08 중간 점검 자료 `gpt-6-luna` 사실 확인 재실행(표준 입력을 닫고 실행, 정상 종료). 지적 5건(기존 모델의 재학습 단정, 공통 항목이 모든 학교에 있다는 표현, 없는 정보 거절의 범위, 0건 진단의 조건 제한, "모든 평가 데이터는 모사 데이터"의 범위)을 문서·코드로 확인해 문서와 공유 페이지를 고쳤다. 수치·날짜 오류와 비밀 노출은 없었다. AGENTS.md에 리뷰 명령(`< /dev/null`, `timeout`)을 적었다
- 2026-10-08 Phase 9-04 팀원 평가 리허설. 사용자 요청으로 AI가 새 합성 학교 `rehearsal_school`(`backend/adaptive/fixtures/rehearsal_school.py`, `other_school` 과목을 학점 `3-2-2`, 학기 `2026-1`, 시간 `09:00-10:15`, 분반이 붙은 과목코드, 영문 이수구분 코드로 저장)을 만들고 팀원 평가 과정을 그대로 해 봤다. 질문 14개를 실행 전에 고정하고 예상을 먼저 적었다. 등록: AI 첫 후보 15/16, 값 사전 10건, 게시는 "한 칸은 한 항목만" 규칙으로 연도·시작 시간이 막혀 "이 DB에 없음"으로 둔 뒤 성공. `blind_eval` 첫 실행 6/14(실패 6·오류 2). 발견: (1) 종료 시간이 범위 글자 칸에 변환 없이 게시되어 글자 비교로 조용히 틀린 답(정답 79과목에 17과목 섞임), (2) 모델이 select에 분반만 넣으면 분반 없는 DB에서 답 전체 거절, (3) 학점 글자 칸이 게시되고 실행 때 HTTP 500, (4) 분반이 붙은 과목코드 같음 비교 0건, (5) 연도·학기 한 칸, (6) 게시 막힘 이유 미표시. 지침대로 코드는 고치지 않았고 처리 후보를 결과 문서에 적었다(사용자 결정 대기). `gpt-6-luna` high 지적(게시 막힘 원인, 처리 후보 세 가지 보완, fixture 테스트 보강)을 확인해 반영했다. 테스트 등록은 지우고 schema와 평가 기록(git 밖)은 남겼다. 결과는 `docs/phase9-04-rehearsal-results-2026-10-08.md`. 새 fixture 테스트가 psycopg2를 불러 기존 import 격리 테스트를 깨뜨린 것을 전체 실행에서 발견해 가짜 `sql` 객체로 바꿨다. 전체 pytest 2008개 통과·8 skip·611 subtests, compileall·diff check 통과. 코드 변경은 fixture 모듈과 테스트뿐
- 2026-10-08 Phase 9-05 리허설 문제 1·2 고침(ADR-097, 사용자 결정). (1) `plan_publish`: 변환 없이 글자 칸에 남은 시간 항목은 값이 모두 `HH:MM`이어야 게시(`time` 형식·`0900` 꼴·교시표는 그대로). (2) `chat_query._drop_unasked_select`: select에만 있고 질문이 말하지 않은 "이 DB에 없음" 항목은 빼고 답한다(말하거나 조건에 쓰면 거절, select가 비면 카드 항목 하나). 레거시 시간 칸은 `time` 형식이라 (1) 대상 밖임을 읽기 전용으로 확인했다. 고치기 전·후 테스트 포트에서 레거시 비교 30문항+고정 6개가 36/36 같았다. 리허설 학교를 다시 등록하자 종료 시간이 게시에서 막혔고, 재실행(`run-002`) 9/14였다(r04·r09·r12 통과, 종료 시간 질문은 조용한 오답 대신 "정보 없음"). 남은 것: 문제 3~6, 게시 막힘이 단계별로 나뉘어 나옴. `gpt-6-luna` high 지적 3건(이름 없이 묻는 연도, 레거시 결과 변화 범위, 빈 글자 시간 값)을 확인해 연도 표현 목록 보완·빈 글자 검사를 더했고, 레거시 변화는 이전에 "정보 없음"이던 답에만 해당함을 기록했다. 보완 뒤 레거시 36/36 다시 같음. 결과는 `docs/phase9-05-raw-time-and-unasked-select-results-2026-10-08.md`. 전체 pytest 2025개 통과·8 skip·611 subtests(사용량 소진으로 첫 실행이 중단되어 2026-10-09에 다시 실행), compileall·diff check 통과
- 2026-10-10 Phase 9-06 리허설 문제 3 고침(ADR-097 보완, 사용자 결정). `plan_publish`: 공통 숫자 항목은 숫자 형식 칸(`_NUMERIC_TYPE`)이거나 검증된 변환이 있을 때만 게시하고, `SLASH_FIRST_NUMERIC`·`HHMM_TEXT_TO_HHMM`이 정해진 칸에 빈 글자가 있으면 막는다. 레거시 확인(읽기 전용): public schema 숫자 칸은 `integer`, 학점은 `3/3/0` 글자(빈 글자 0). 화면 등록 경로로 레거시 public schema와 `other_school`이 모두 1번에 게시되었고, 레거시 답(3학점·컴융 전공 82·컴융 3학년 전공 33·월요일 오전 200·정원 60명 이상 200)은 그대로였다. 리허설 학교는 학점이 게시에서 막혀 "이 DB에 없음"이 되었고, 재실행(`run-003`) 9/14·오류 0(학점 질문 HTTP 500 → "학점 정보 없음"). `gpt-6-luna` high 지적 3건(빈 글자, 도메인 형식, 값 사전 예외)을 확인해 2건을 고치고 도메인 형식은 한계로 기록했다. 남은 것: 문제 4~6, 게시 막힘이 단계별로 나옴. 결과는 `docs/phase9-06-numeric-columns-results-2026-10-10.md`. 전체 pytest 2037개 통과·8 skip·611 subtests, compileall·diff check 통과
- 2026-10-10 Phase 9-07 게시 막힘 항목·이유 한 번에 보이기(사용자 결정, 리허설 문제 6). `plan_publish`가 모든 검사를 끝까지 돌려 막힌 항목마다 이유(`BLOCK_REASONS` 10종)를 모으고, 값 사전 낡음도 같은 목록에 넣는다. 게시 거절 응답은 기존 `fields`를 그대로 두고 `reasons`(항목, 한국어 이름, 이유, 같은 칸이면 다른 항목)를 더했다. 런타임 검증 실패와 SQLite 경로는 그대로다. 화면(`mappingReview.ts`)은 `reasons`를 엄격히 읽어(알려진 코드만, 항목마다 하나) 항목마다 이유와 할 일을 한 줄씩 보이고, 세 패널의 오류 칸이 줄바꿈을 보인다. 리허설 학교 게시 한 번에 막힌 항목 넷(학점, 개설 연도, 종료 시간, 시작 시간)이 이유와 함께 나왔다(이전에는 게시 세 번). 화면(5174→8124)에서도 확인했다. `gpt-6-luna` high 지적 3건(낡은 값 사전이 다른 막힘을 가림, 모르는 이유 코드, 빠지거나 겹친 이유)을 고쳤다. 결과는 `docs/phase9-07-publish-block-reasons-results-2026-10-10.md`. 전체 pytest 2042개 통과·8 skip·611 subtests, frontend Node 139개·tsc·ESLint(src·tests/frontend)·vite build·compileall·diff check 통과
- 2026-10-12 Phase 9-08 챗봇 기본 모델을 일반 모델로 바꾸고 모델 고르기(ADR-098, 사용자 결정). 원인: 레거시로 미세 조정한 모델이 리허설 학교의 "컴융·소웨 전공"을 없는 레거시 과목 코드로 바꿈(위험 3). 서버가 선택지를 정한다: `general`(기본, `CHAT_MODEL` 또는 `qwen2.5-coder:7b-instruct`, model template + QueryIR JSON schema `queryir-schema-v1` + prompt `query-ir-v3`), `legacy-tuned`(`OLLAMA_MODEL`, 요청 불변). `GET /api/adaptive/v1/chat/models`, 질문 요청의 선택 `model`(id만, 모르는 id 거절), 화면 "모델" 고르기, `blind_eval run --model <id>`(기록은 서버가 알려 준 모델). 8-13 값 힌트 고침을 넓혀 식별 항목(코드·분반)의 값이 힌트 하나와 같으면 옮긴다. 레거시 챗봇은 그대로. 잠정 수치: 레거시 30문항 일반 23·학습 22(학습 모델 답은 전후 같음), 리허설(사용자 등록) 6/14·6/14, `other_school` 10/12·12/12(일반 모델은 학과 약어에 약함). `gpt-6-luna` high 지적 5건: 3건 고침(조건 상한 6→12, 조회 항목 중복 금지, 평가 도구가 모델 목록을 못 읽으면 멈춤), 1건 재현 안 됨(JS 정규식), 1건 의도(모델 이름 표시). 결과는 `docs/phase9-08-chat-model-choice-results-2026-10-12.md`. 전체 pytest 2088개 통과·8 skip·611 subtests, frontend Node 147개·tsc·ESLint(src·tests/frontend)·vite build·compileall·diff check 통과
- 2026-10-12 Phase 9-09 챗봇 모델 선택지에 ChatGPT API 추가(ADR-099, 사용자 결정: 기본 `gpt-5-mini`, 값 힌트 전송, Legacy 기준선 그대로). `harness/openai_chat.py` `OpenAIQueryModel`: 질문당 Chat Completions 한 번, prompt `query-ir-v3`·값 힌트·QueryIR schema(non-strict), 고정 HTTPS 주소·proxy/redirect 없음·응답 상한, `finish_reason == "stop"`만 답, 실패는 고정 코드. `OPENAI_API_KEY`가 있을 때만 선택지 `openai`(맨 뒤, 기본은 그대로 일반 모델). 키는 요청 header에만 쓰고 화면·상태 줄·오류·repr·평가 기록에 나가지 않는다. 명확화 흐름은 로컬 Ollama. 화면의 모델 실패 안내에 ChatGPT 키·한도를 더했다. `gpt-6-luna` high 지적 3건(시작 설정 repr의 키, `__context__`에 남는 원래 예외, `finish_reason` 미확인) 모두 고침. 실제 호출 측정은 사용자가 키를 넣은 뒤. 결과는 `docs/phase9-09-openai-chat-model-results-2026-10-12.md`. 전체 pytest 2116개 통과·8 skip·611 subtests, frontend Node 148개·tsc·ESLint·vite build·compileall·diff check 통과

- 2026-10-12 Phase 9-10 등록 PostgreSQL 직접 SQL 평가군 추가(ADR-100, 사용자 결정). catalog·10개/40자 예시·실제 값 힌트(관리자 값 사전 없음), PostgreSQL SQL 정책·read-only executor, general/OpenAI 한 호출, 기존 세 채점기, 비노출 private 기록·blind 잠금/번호/arm 보고, QueryIR HTTP elapsed_ms와 other_school 12문항 비교 CLI. 제품·Legacy·SQLite arm 유지. 지정 targeted 테스트만 실행; 전체 pytest·live 모델·서버 변경·commit 없음. live 측정은 사용자 승인 뒤 한 번, Luna/opencode 독립 리뷰는 no-live-model 제약으로 대기. 사람 관문 미승인.

  - Phase 9-10 검증: 지정된 6개 파일의 targeted pytest 234 passed·2 skipped(기존 Windows link 권한 skip). 새 테스트는 fake reader/model/executor/transport만 사용했다. Windows sandbox의 Python 3.13 mode=0700 임시 디렉터리 ACL 제한을 피해 테스트에만 tempfile을 private workspace로 옮기고 그 아래 mkdir만 mode=0777로 실행했다. 기존 SQLite CLI 테스트의 간헐 실패는 report 게시 os.rename의 PermissionError로 재현했다. 테스트 임시 경로 안에서만 해당 오류를 최대 20번(50ms 간격) 재시도한 최종 실행은 전건 통과했다. 제품 권한 처리·SQLite arm은 변경하지 않는다. 전체 pytest·live 모델·opencode·서버 조작 없음.

- 2026-10-12 Phase 9-10b 검토 보완(사용자 지침 `docs/prompts/phase9-10b-review-fixes.md`). 같은 날 사용자 결정으로 OpenAI 평가 허용을 철회하고 직접 SQL OpenAI 모델·분기·테스트를 제거했다(추가 API 비용 없음; 제품 ADR-099 유지). CLI arm별 모델 제한, 원래 SQL 검사 뒤 200행 wrapper 실행·원래 SQL hash 유지, PostgreSQL schema/table/CTE 식별자 대소문자 규칙을 고쳤다. 지정 targeted 파일만 검증하며 live 모델·서버 조작·commit 없음. Luna/opencode는 no-live-model 제약으로 대기, 사람 관문 승인 없음.
  - 검증: 지정된 6개 파일(`test_direct_sql_pg`, `test_direct_sql_arm`, `test_arm_live_cli`, `test_blind_eval`, `test_legacy_chat_compare`, `test_openai_chat_model`) targeted pytest 243 passed·2 skipped(기존 Windows link 권한 skip). pytest plugin 자동 로딩을 끄고 테스트에만 private workspace 임시 경로 아래 mkdir mode=0777을 적용했다(기존 Windows sandbox ACL 제한 우회). 초기 기본 실행은 출력 없이 중단했고, 우회 후 새 회귀 테스트의 gold codes 150개 상한을 맞춘 뒤 최종 6개 파일이 통과했다. 제품 권한 처리 변경 없음.

- 2026-10-12 9-10c Luna 지적 4건 고침: schema 한정·미허용 Anonymous 함수 거절, 끝 주석을 보존하는 줄바꿈 wrapper, CLI legacy의 blind_eval 공통 거절 채점, 채점 전 blind 지연 측정·응답 elapsed_ms 우선. 각 회귀 테스트로 재현·검증, 제공된 Luna 리뷰 로드맵 완료. 지정 3개 파일(test_direct_sql_pg·test_blind_eval·test_legacy_chat_compare) targeted pytest 155 passed·1 skipped(기존 Windows link 권한 skip). 테스트에만 tempfile을 private workspace로 옮기고 그 아래 mkdir mode=0777을 적용했다(Windows sandbox ACL 제한 우회; 제품 권한 처리 유지). 새 live 모델 호출·서버 조작·commit 없음.

- 2026-10-12 Phase 9-10 측정 결과 기록(사용자 지침 docs/prompts/phase9-10d-results-doc.md, 문서만). 사용자 승인으로 한 번 측정한 로컬 qwen2.5-coder:7b-instruct·temperature 0·질문당 한 호출 결과를 docs/phase9-10-direct-sql-results-2026-10-12.md에 기록했다. 직접 SQL / QueryIR: 레거시 10/30 / 26/30, other_school 5/12 / 10/12(9-08 재사용·응답 시간 없음), 리허설 2/14 / 6/14, 합 17/56 / 42/56. 레거시 QueryIR 26은 정확 18·상한 부분 집합 5·거절 정답 3이다. Claude가 제공한 측정 요약과 실패 재생성 분류를 기록했으며 분류 개수는 대략적인 값이다. ADR-100·15.1.1(--reference-database)·로드맵 live 측정 완료·사람 관문 메모·인수인계를 갱신했다. 테스트 서버 8124는 측정 뒤 종료, 사용자 서버 변경 없음; p910-legacy·p910-other는 PENDING_REVIEW·미게시. 개발 질문·L1/L3 전 잠정치, 적응성 근거·사람 관문 승인·제품 구조 결정 없음. 이번 작업은 코드 변경·새 측정·모델 호출(Luna/opencode 포함)·서버 조작·테스트·commit 없이 문서만 갱신했다.
- 2026-10-12 Phase 9-11 게시된 PostgreSQL 등록의 "mapping 검토 시작" 형식 오류 고침(사용자 지침 `docs/prompts/phase9-11-published-review-contract.md`, `docs/prompts/phase9-11b-activatable.md`로 보완). review GET·INIT의 마이크로초 `published_at`만 초 단위 UTC `Z`로 정규화하고 저장 파일·저장 형식은 유지한다. 최초 미커밋 구현의 `activatable: True`는 SQLite 저장소만 읽는 회귀 확인 버튼을 노출하므로 원래 `False`로 되돌렸다. 화면 parser는 활성·`activatable: false`를 허용하고 다른 검사는 유지하며, 실행 불가 문구는 비활성 버전에만 표시한다. 활성 PostgreSQL 버전은 실행 중으로 보이고 회귀 확인·되돌리기 버튼은 숨긴다. SQLite 응답·동작과 PostgreSQL ACTIVATE 미지원은 유지한다. 최초 검증은 PostgreSQL 34 passed·frontend 22 passed였고, 보완 후 targeted 결과는 아래 9-11b 항목에 기록한다. 사용자 서버·사용자 `.schema-adaptive` 데이터 변경·live 모델 호출·commit·`backend/.env` 값 출력 없음. Luna/opencode 독립 리뷰는 사용자 no-live-model 제약으로 대기; 기존 기능 지원 범위 안의 계약 보완이라 새 ADR 없음.

- 2026-10-12 Phase 9-11b targeted 검증: `test_postgres_registration.py` 34 passed; 기존 SQLite `test_mapping_version_rollback.py`는 첫 실행 24 passed·3 failed(이력 검사 전 fixture 업로드에서 `SQLITE_UPLOAD_INTERNAL_ERROR`), 실패한 3개만 코드 변경 없이 재실행하여 3 passed. 원인 오류를 포착하기 위한 임시 rename 진단도 재실행에서 오류를 재현하지 못했으므로 제품 코드는 추가 변경하지 않았다. frontend review·version history·9-11 계약 3개 파일 23 passed; diff check 통과. `.tmp-phase9-11-tests`는 이미 없음을 확인했고 임시 경로는 기존 git-ignored `.venv/phase9-11b-tests`에 두었다. 기본 `.pytest_cache` 임시 경로 생성은 Windows 접근 오류로 실패하여 `.venv`로 옮겼으며, 테스트 프로세스에서만 임시 경로 아래 mkdir mode=0777·tempfile 경로 지정·pytest plugin 자동 로딩 및 cacheprovider 비활성화를 적용했다. Node는 `--test-isolation=none`으로 실행했다. 사용자 서버·사용자 데이터·live 모델 호출·commit·환경 값 출력 없음; Luna/opencode 독립 리뷰는 사용자 제약으로 대기.

- 2026-10-12 Phase 9-12 사용자 요청(`docs/prompts/phase9-12-db-admin-layout.md`): DB 변경 창을 크게 하고 사용할 DB/DB 등록/검토·게시로 나눴다. PostgreSQL 등록 양식·목록은 2열·표, 검토는 항목 연결 표와 접는 선택 도구, 게시 막힘 이유는 게시 영역에 한 줄씩 표시한다. SQLite 등록은 기타 접힌 영역에 보존했다. 배치·문구만 변경했고 기존 스타일 맞추기는 다음 단계다. API·계약·규칙·챗봇·adaptive-prototype 페이지·레거시 기준선과 기존 미커밋 작업 유지, 새 ADR·사람 관문 승인 없음. `npx tsc -b`와 frontend Node 152개 통과; 기본 `npm run lint`는 기존 임시·private 데이터 폴더 접근 EPERM으로 중단, `npx eslint src tests/frontend` 및 `.pytest_cache`·`.venv`·`.schema-adaptive`를 제외한 `npm run lint` 통과; 기본 `npx vite build`는 config 로딩 spawn EPERM으로 중단, `npx vite build --configLoader native` 통과; diff check 통과. 서버 조작·live 모델 호출·commit·환경 값 출력 없음. Luna/opencode 리뷰는 사용자 제약으로 대기하며 화면 확인은 Claude가 별도로 한다.
- 2026-10-13 Phase 9-13 사용자 결정 B(`docs/prompts/phase9-13-postgres-test-queries.md`): PostgreSQL 등록의 고정 시험 질의·hash 기준 저장·현재 버전만 회귀 확인을 기존 검증된 챗봇 실행 경로로 추가하고, 없는 항목 표시와 활성 버전 `activatable: True`를 반영했다. 관련 backend 3개 파일 최초 48 passed·2 failed(SQLite upload setup `SQLITE_UPLOAD_INTERNAL_ERROR`), 실패 2개 targeted 재실행 통과·추가 preview/Decimal/검토자 테스트 1 passed로 총 51개 통과; frontend 4개 파일 24 passed, TypeScript·대상 ESLint·diff check 통과. Windows sandbox에서 테스트 프로세스에만 tempfile 경로를 새 `.venv` 임시 디렉터리로 지정하고 그 아래 `os.mkdir` mode=0777 적용; 마지막 setup 재실행의 임시 경로 한정 rename 재시도 진단은 실제 retry 0회로 통과, upload 오류 원인은 확정하지 않았으며 제품 권한 처리는 변경하지 않았다. 기존 SQLite·레거시·미커밋 작업 유지; 사용자 서버·데이터 변경, live 모델 호출, commit, 환경 값·DSN 출력 없음. Luna/opencode 리뷰는 사용자 no-live-model 제약으로 대기, 사람 관문 승인 없음.

- 2026-10-13 Phase 9-14 사용자 지침(`docs/prompts/phase9-14-review-row-cleanup.md`): 검토·게시의 항목 연결 표를 한 줄씩 펼치는 동작으로 정리하고, 다른 칸 편집 드롭다운이 현재 연결 대신 첫 테이블·칸(dept.college)을 고르던 결함을 현재 연결 → 추천 → 빈 선택 기본값으로 고쳤다. 검토자 한 번 입력을 값 사전·확장 항목·시험 질의 기준 저장·게시에서 공유하고 설명·게시 안내를 정리했다. frontend 161개(새 회귀 7개)·tsc·대상 ESLint·diff check 통과; 기본 vite build는 Windows spawn EPERM으로 실패, --configLoader native 통과. API·계약·검증 규칙·레거시·기존 미커밋 작업 유지, 새 ADR·사람 관문 승인 없음. 서버·사용자 데이터 조작, live 모델 호출, commit, backend/.env 값 출력 없음; Luna/opencode 독립 리뷰는 사용자 no-live-model 제약으로 대기, 화면 확인은 Claude가 한다.

- 2026-10-13 Phase 9-15 사용자 요청(`docs/prompts/phase9-15-bulk-approve.md`): 화면만 AI 추천 모두 승인 추가, 대기·후보 있음 항목의 첫 후보를 확인 후 순차 APPROVE하고 진행 중 버튼 비활성화·응답별 표 갱신·첫 실패 중단·후보 없는 항목 이름 안내. 중단된 구현의 JSX·한국어 문자열·항목 이름 참조를 복구하고 UTF-8로 저장했다. 순수 선택·가짜 fetch 회귀 7개 포함 frontend 168개, npx tsc -b·관련 ESLint 통과. 자동 게시 없음, 9-06 숫자 칸·ADR-097 시간 칸 게시 규칙·레거시·기존 미커밋 작업 유지; 서버·사용자 데이터 조작·live 모델 호출·commit·backend/.env 값 출력 없음. Luna/opencode 독립 리뷰는 사용자 no-live-model 제약으로 대기, 새 ADR·사람 관문 승인 없음.

- 2026-10-13 Phase 9-16 사용자 결정(`docs/prompts/phase9-16-retire-offering-year.md`): 공통 ontology v3의 CourseOffering.year를 정의 삭제 없이 사용 중단했다(ADR-101). v1/v2 기록·gold·fixture·dataset profile·학기·레거시 기준선은 보존하고 현재 등록·검토·게시·모델 목록·schema·값 힌트·챗봇에서는 제외한다. 기존 게시 mapping의 year도 현재 runtime에서 unsupported, q28 거절 유지. 현재 평가 영향 6문항과 targeted 검증 결과는 `docs/phase9-16-retire-offering-year-results-2026-10-13.md`. 사용자 서버·사용자 데이터·live 모델·commit·backend/.env 값 출력 없음. Luna/opencode 독립 리뷰는 사용자 제약으로 대기, 사람 관문 승인 없음.

- 2026-10-13 Phase 9-17 사용자 지침(`docs/prompts/phase9-17-reserved-database-id.md`): 새 PostgreSQL·SQLite 등록에서 예약 선택 ID `legacy`를 파일 생성 전 기존 고정 입력 오류 코드로 거절하고 화면 전송 전 시스템 이름 안내를 추가했다. 목록은 예약 ID·잘못된 개별 항목만 제외해 나머지와 한 줄 경고를 표시하며 최상위 계약·중복 ID 검사는 유지한다. 관련 targeted backend 47 passed·frontend 29 passed, npx tsc -b·diff check 통과. 최초 기본 backend 실행은 출력 없이 중단했고 테스트 프로세스에서만 tempfile 경로를 새 `.venv` 임시 디렉터리로 지정하고 그 아래 mkdir mode=0777을 적용해 전건 통과했다(Windows sandbox ACL 우회; 제품 권한 처리 유지). 저장된 `legacy`·사용자 `.schema-adaptive` 데이터·서버·레거시 기준선·기존 미커밋 작업 유지; live 모델 호출·commit·backend/.env 값 출력 없음. Luna/opencode 독립 리뷰는 사용자 no-live-model 제약으로 대기, 기존 계약 내 오류 처리 보완으로 새 ADR·사람 관문 승인 없음.

- 2026-10-13 Phase 9-18 사용자 요청(`docs/prompts/phase9-18-delete-registration.md`): PostgreSQL 등록만 엄격한 DELETE API·등록 lock·검증된 private 경로로 등록/profile 두 폴더에서 삭제하고 live context·값 reader·목록에서 즉시 제거한다. 저장된 legacy 정리 허용, profile 전용·합성·SQLite 등록 거절, symlink·Windows junction 차단, 평가 기록은 남김. 화면은 정확한 ID 확인·성공 후 목록 갱신·선택 DB의 Legacy 기준선 복귀·열린 검토 닫기를 추가했다. 관련 targeted backend는 최초 53 passed·3 skipped·기존 등록 setup 1 failed, 실패 항목과 갱신한 삭제 테스트만 재실행하여 19 passed·3 skipped로 전체 57개 통과 확인(Windows junction 3개 통과, symlink 3개는 권한 skip); frontend 17 passed·npx tsc -b·대상 ESLint·diff check 통과. 테스트 프로세스에서만 새 .venv 임시 경로·그 아래 mkdir mode=0777·pytest plugin/cache 비활성화를 적용했으며, setup 재실행은 제품 변경 없이 통과해 원인을 확정하지 않았다. 사용자 서버·사용자 .schema-adaptive 데이터·기존 미커밋 작업 유지, live 모델 호출·commit·backend/.env 값 출력 없음. Luna/opencode 독립 리뷰는 사용자 no-live-model 제약으로 대기; 기존 관리자 등록 생명주기 안의 요청 구현으로 새 ADR·사람 관문 승인 없음.

- 2026-10-13 Phase 9-19 사용자 지침(`docs/prompts/phase9-19-database-id-message.md`): PostgreSQL·SQLite 등록의 ID·표시 이름 검증을 입력 오류로 구분하고 ID 규칙 상시 안내·즉시 오류 표시·잘못된 ID의 등록 비활성화를 추가했다. 예약 ID·실제 응답 형식 오류 문구는 유지. 관련 frontend 4개 파일 29 passed·npx tsc -b·diff check 통과. UTF-8 저장, 기존 미커밋 작업 유지; 서버·사용자 데이터 변경·live 모델 호출·commit 없음. Luna/opencode 독립 리뷰는 사용자 no-live-model 제약으로 대기.

- 2026-10-13 Phase 9-20 사용자 결정(docs/prompts/phase9-20-copy-dictionaries.md): 게시된 PostgreSQL 등록·시작 legacy profile의 값 사전 출처 목록과 COPY API, 대상 저장 값 존재·편집 가능 항목·별칭 검증, 별칭 합침·대상 뜻 유지·편집 중 사전만 저장, 화면 출처 선택·공통 검토자·요약·표 새로고침을 구현했다. 관련 targeted backend 58개 통과(첫 56개 실행 54 passed·기존 mapping 검토 setup 2 failed, 실패한 2개만 재실행 2 passed; 추가 pending mapping·게시 target 보존 2 passed), frontend 15 passed·npx tsc -b·대상 ESLint·diff check·7개 파일 UTF-8 확인 통과. 추가 2개 테스트도 첫 임시 등록 setup에서 실패한 뒤 새 임시 경로 재실행으로 통과했으며 원인은 확정하지 않고 제품 권한 처리는 변경하지 않았다. 테스트에만 .venv 아래 임시 경로·mkdir mode=0777을 적용했다. 기존 미커밋 작업·레거시 기준선 유지, 사용자 서버·사용자 데이터 변경·live 모델 호출·commit·backend/.env 값 출력 없음. Luna/opencode 독립 리뷰는 사용자 no-live-model 제약으로 대기, 새 ADR·사람 관문 승인 없음.

- 2026-10-13 Phase 9-21 사용자 결정(`docs/prompts/phase9-21-unmatched-word-notice.md`, ADR-102): 성공 답에만 닫힌 낱말 연결 규칙으로 `unmatched_terms` 최대 5개·결과 위 값 사전 안내를 추가했다. 호출 수·조건·검증·compiler·policy·executor·outside-concept 거절·빈 결과 진단·레거시와 Phase 9-20 포함 기존 미커밋 작업 유지. 관련 backend 기존 198개·새 테스트 최종 47개로 총 245개 통과(최초 새 테스트 기대 오류 3개 수정), frontend 19 passed·TypeScript·대상 ESLint·diff check·8개 파일 UTF-8 검증 통과. 개발 30문항 reference SQL 기반 고정 조건에서 q12만 아직·자리 알림, 나머지 29개는 없음(5개 거절 포함). [잠정 결과와 한계](phase9-21-unmatched-word-notice-results-2026-10-13.md). 테스트 프로세스만 새 `.venv` 임시 경로·mkdir mode=0777 적용, 출력 없이 지연된 기본 pytest·collect-only 테스트 세션만 종료. 사용자 서버·사용자 데이터 접근/변경·live 모델 호출·commit·환경 값 출력 없음. Luna/opencode 독립 리뷰는 사용자 no-live-model 제약으로 대기, 사람 관문 승인 없음.

- 2026-10-13 Phase 9-22 사용자 요청(`docs/prompts/phase9-22-admin-style.md`): 관리 창 스타일 통일·검토자 기억과 클릭 가능한 검토자 안내·비활성 사유를 frontend에만 추가했다. 관련 frontend 107 passed(새 회귀 6개), `npx tsc -b`·관련 ESLint 통과; 전체 frontend에서 기존 registry parser의 부분 실패 정책과 오래된 기대가 다른 6건은 수정하지 않았다. API·검증·기존 미커밋 작업 유지, 서버·사용자 데이터 조작·live 모델 호출·commit 없음; Luna/opencode 리뷰는 사용자 제약으로 대기, 화면 확인은 Claude 담당.

- 2026-10-13 Phase 9-23 Luna 지적 4건 고침 + reference-database 테스트: 등록 삭제의 값 힌트 cache 제거, Python 3.10/3.11 junction 호환, 사용 중단 필드 검토 변경 거절, 일반 요청 어미 알림 제외를 회귀 검증했다(targeted 6개 파일 총 266 passed·3 skipped·7 subtests passed; 최초 임시 등록/검토 쓰기 실패 2건과 새 호환 테스트 모사 오류는 targeted 재실행 통과, 개발 30문항 고정 판정 유지). 제공된 9-11~9-22 Luna 리뷰 완료 반영; 새 live 모델 호출·사용자 서버·사용자 데이터 변경·commit 없음.

- 2026-10-13 Phase 9-24 사용자 지침(`docs/prompts/phase9-24-v3-fallout.md`): 오프라인 평가·gold/녹화 replay·capture·benchmark는 선언 버전(미선언 v2)으로 재현하며 ADR-101·9-16 평가 영향 절을 보완했다. stale AI year만 제거, 현재 smoke의 year 의존 제거, v2→v3 비교/이월 필터와 회귀 검증, v3 테스트 기대를 정리했다. 원문 목록의 26건에 누락된 fixture 3 subtest를 더한 29건 분류는 1:13/2:9/3:7. 전체 pytest 한 번은 2341 passed·11 skipped·9 failed·608 subtests passed(34분 6초); 남은 역사적 fixture 3 subtest의 v2 생성 인자를 고친 뒤 해당 전체 파일과 검토 setup 500 6건을 정확히 재실행하여 10 passed·12 subtests passed로 전건 통과 확인. setup 오류는 재현되지 않아 제품 저장 정책 변경 없음. 관련 frontend 56개·TypeScript·UTF-8·diff check 통과, workspace basetemp/보조 파일 삭제. 결과와 변경 파일은 `docs/phase9-16-retire-offering-year-results-2026-10-13.md`의 9-24 절. 기존 미커밋 변경·현재 runtime v3·레거시·gold/fixture/녹화 유지, 사용자 서버/데이터 변경·live 모델·commit·환경 값 출력 없음. Luna/opencode 리뷰는 사용자 제약으로 대기, 사람 관문 승인 없음.

- 2026-10-14 9-25 팀원 안내서 갱신: 현재 DB 관리 탭·등록/삭제·검토/게시·값 사전·ontology v3·챗봇 알림·평가 담당 구분·등록 비용 기록을 반영했다(문서만, UTF-8; 코드·서버·모델 실행·commit 없음).

- 2026-10-14 9-26 사용자 지침(`docs/prompts/phase9-26-dictionary-refresh.md`): frontend에서 검토 detail revision을 값 사전·확장 항목에 공유하여 승인·변경·없음·일괄 승인·게시 뒤 자동 새로고침하고, 저장하지 않은 편집은 보존하며 새로고침 안내를 표시한다. 두 도구의 게시 성공 뒤 mapping 최신 버전도 다시 읽으며 기존 새로고침 버튼을 유지한다. `npx tsc -b`·관련 ESLint·전체 frontend 206개 통과(Node 기본 실행은 sandbox spawn EPERM으로 중단, `--test-isolation=none` 재실행 통과). UTF-8·diff check 확인, API·레거시·기존 미커밋 변경 유지; 서버·사용자 데이터 조작·live 모델 호출·commit 없음. Luna/opencode 독립 리뷰는 사용자 no-live-model 제약으로 대기, 새 ADR·사람 관문 승인 없음.

- 2026-10-17 Phase 9-27 사용자 지침(`docs/prompts/phase9-27-renames.md`): `git mv`로 앱 조립 모듈을 `app_factory.py`, DB 목록·catalog 모듈을 `database_registry.py`, frontend 폴더를 `src/adaptive/`, 시연 화면을 `ClarificationDemoPage.tsx`, NER 보정 예제를 `backend/scripts/demo_ner_correction.py`로 옮겼다. import·CLI·monkeypatch·테스트 문자열과 현재 실행 안내를 갱신하고 내부 app state 접두사는 `adaptive_app_`로 맞췄다. 예제는 main guard 안에서 패키지 import·명시적 참조 값만 쓰며, 참조 0건을 확인한 기본 SVG 2개를 삭제했다. 계약 버전·DB ID·HTTP/브라우저 URL·localStorage 키·저장 형식·npm 스크립트 이름과 역사 기록은 보존한다. `.git` 쓰기 금지 환경이라 `.venv/phase9-27-tools/git-index`의 별도 index로 `git mv`를 실행했으며 실제 index는 변경하지 않았다. TypeScript·src ESLint·frontend 206개·compileall·UTF-8·diff check·예제 실행/무실행 import 검증 통과. Node 기본 실행과 기본 build는 spawn EPERM, Node `--test-isolation=none` 및 build `--configLoader native` 통과. Backend 관련 25개 파일 최초 결과는 593 passed·24 skipped·6 failed·3 errors(15분 26초); 실패/오류 9건은 모두 임시 fixture 등록·업로드 중 500이었고, 정확한 9건만 새 임시 경로에서 코드 변경 없이 재실행하여 9 passed(1분 43초), 합계 602개 통과를 확인했다. 일시적 등록 실패의 근본 원인은 확정하지 않고 제품 코드는 추가 변경하지 않았다. 24 skip 중 20개는 서버 시작 금지에 따라 임시 HTTP listener를 여는 transport 테스트를 제외한 것이고 나머지 4개는 기존 skip이다. 테스트 프로세스에만 `.venv` 아래 basetemp·tempfile 경로 및 그 아래 mkdir mode=0777·pytest plugin/cache 비활성화를 적용했다. 앱 조립·registry 모듈 AST와 이동한 frontend 29개 파일이 이름·설명 변경 외에 동일함도 확인했다. 전체 pytest는 실행하지 않았다. 사용자 서버·사용자 데이터·live 모델 호출·commit·backend/.env 값 출력 없음. Luna/opencode 독립 리뷰는 사용자 no-live-model 제약으로 대기, 기존 기준선·미커밋 의존성 수정 유지, 새 ADR·사람 관문 승인 없음.

- 2026-10-17 9-27 이름 정리: 앱 조립·DB 목록·frontend·NER 예제의 현재 경로를 구조 절과 문서 목차에 명시했다. 상세 구현·검증은 위 Phase 9-27 기록을 따른다.

- 2026-10-17 9-28 팀원용 문서·주석 정리: 루트 README·환경 예시·문서 목차, 사람용 인수인계 요약, 현재 관리자 화면·ontology v3 시연 절차와 핵심 모듈 설명을 갱신했다. 문서·주석만 변경하며 기존 미커밋 9-27 이름 정리와 레거시 기준선을 보존한다. compileall·npx tsc -b·npx eslint src·diff check 통과, Python 13개 모듈은 docstring 제외 AST 동일·TypeScript 4개 파일은 첫 주석 추가만 확인했다. 변경 파일 UTF-8·문서 링크·README 200줄 제한·환경 예시의 비밀 값 없음과 Git ignore 제외·기존 인수인계 본문 보존을 확인했다. 전체 pytest·frontend 테스트·제품 build·환경 점검 실행은 이번 문서 단계의 필수 확인 대상이 아니므로 수행하지 않았다. 서버·모델 호출·commit·backend/.env 열람 없음. Luna/opencode 독립 리뷰는 사용자 모델 호출 금지로 실행하지 않았다. 새 설계 결정·사람 관문 승인 없음.
