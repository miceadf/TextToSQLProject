# 대학 수강정보 Text-to-SQL

한국어 질문으로 대학 수강정보를 찾는 React + FastAPI 프로젝트다.
레거시 챗봇은 기존 DB와 미세 조정 모델을 사용하는 기준선이며 포트 8000에서 실행한다.
스키마 적응형 시스템은 관리자가 DB를 등록하고 mapping을 검토·게시하면,
공통 항목으로 질문을 QueryIR로 해석해 학교별 SQL을 만드는 별도 서버(8123)다.

```text
관리자: DB 등록 → 공통 항목 연결 검토 → 게시
사용자: 화면(5173) → DB 선택 → 질문
                         ├─ Legacy 기준선 → 미세 조정 모델 → SQL → 기존 DB (8000)
                         └─ 적응형 → QueryIR → 게시 mapping → SQL → 선택 DB (8123)
```

## 준비

- Python 3.10 이상(`env_check` 기준, 개발 PC는 3.13).
- Node.js 24(개발 PC 기준)와 npm. 현재 Vite의 Node 요구 조건은 `^20.19.0 || >=22.12.0`이다.
- PostgreSQL과 읽기 권한이 있는 수강정보 DB. 레거시는 `v_course_info` 등 기존 스키마에 의존한다. 설치만으로 DB·자료가 만들어지지 않는다.
- 로컬 Ollama와 일반 모델 `qwen2.5-coder:7b-instruct`.
- 레거시 미세 조정 모델: [models/Modelfile](models/Modelfile)의 `FROM`이 가리키는 GGUF를 별도로 준비한다. 모델 파일은 Git에 포함되지 않는다.
- 레거시 NER 가중치도 `backend/course_custom_ner_model/`에 별도로 준비한다.

```powershell
ollama pull qwen2.5-coder:7b-instruct
ollama create text2sql-local -f models/Modelfile
```

`FROM`은 준비한 GGUF 경로에 맞춘다. `OLLAMA_MODEL`에는 생성한 레거시 모델 이름을 넣는다. Ollama 서비스가 실행 중이어야 한다.

## 설치 (저장소 루트, Windows PowerShell)

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
.venv\Scripts\python.exe -m pip install torch pytest
npm install
```

`torch`와 `pytest`는 현재 requirements에 없다. GPU 사용 시 환경에 맞는 torch 배포판을 선택한다.
다른 OS에서는 `.venv/bin/python`을 쓰고, Windows 경로가 고정된 npm 서버 스크립트 대신 해당 Python 모듈 명령과 `npm run dev:frontend`를 따로 실행한다.

## 설정

```powershell
Copy-Item backend/.env.example backend/.env
```

`backend/.env`에 본인 환경 값을 채운다. 파일·접속 문자열·키는 공유하거나 커밋하지 않는다.
변수 이름은 `backend/adaptive/api/dev_launcher.py`와 레거시 `backend/` 코드에서 확인할 수 있다.

| 변수 | 필수 여부 | 용도 |
|---|---|---|
| `DATABASE_URL` | 기본 실행 필수 | 레거시 PostgreSQL 및 기본 등록 연결 |
| `OLLAMA_BASE_URL` | 로컬 모델 사용 시 필수 | Ollama 주소; 적응형 launcher는 loopback만 허용 |
| `OLLAMA_MODEL` | 레거시 사용 시 필수 | 레거시 미세 조정 모델 및 적응형 비교 선택지 |
| `CHAT_MODEL` | 선택 | 적응형 일반 챗봇 모델 |
| `MAPPING_MODEL` | 선택 | DB 등록 시 mapping 제안 모델 |
| `SHOW_SQL` | 선택 | 적응형 개발용 SQL 표시 제어 |
| `REDIS_URL` | 레거시 캐시 사용 시 | Redis 연결; 적응형 launcher는 읽지 않음 |
| `OPENAI_API_KEY` | 선택 | 유료 외부 ChatGPT API 선택지 활성화 |
| `OPENAI_MODEL` | 선택 | 외부 ChatGPT API 모델 |
| `PG_SOURCE_<이름>` | 추가 DB 등록 시 | 서버가 보관하는 추가 PostgreSQL 연결 |

`CHAT_MODEL`·`MAPPING_MODEL`을 비우면 `qwen2.5-coder:7b-instruct`를 쓴다.
적응형 개발용 SQL 표시는 `SHOW_SQL=0`으로 끈다.
외부 API를 선택하면 질문·공통 항목·값 힌트가 OpenAI로 전송되며 비용이 발생한다.
`PG_SOURCE_` 뒤에는 연결 이름을 붙인다(예: `PG_SOURCE_OTHER_SCHOOL`). 접속 정보는 브라우저에 입력하지 않는다.

## 실행과 사용

서버 시작 전에 가상환경 Python으로 점검한다.

```powershell
.venv\Scripts\python.exe -m backend.adaptive.env_check
npm run dev:all
```

점검의 `OK`는 확인 성공, `WARN`은 선택 기능·환경 제약 확인 필요, `FAIL`은 필수 문제이며 종료 코드 1이다.
점검은 DB 읽기 연결·Ollama 모델 목록·포트와 임시 파일 생성/삭제를 확인한다. 질문 추론은 하지 않는다.
화면은 `http://localhost:5173`, 레거시는 `http://127.0.0.1:8000`, 적응형은 `http://127.0.0.1:8123`이다.

화면 **DB 변경** → **DB 등록**에서 PostgreSQL schema 등록 → **검토·게시**에서 공통 항목 연결과 값 사전을 확인하고 게시 → **사용할 DB**에서 선택·적용 → 챗봇으로 질문한다. 현재 ontology v3에는 개설 연도가 없고 학기는 유지한다. 자세한 절차는 [팀원 DB 안내](docs/teammate-evaluation-db-guide.md), 시연 순서는 [시연 절차](docs/demo-procedure.md)를 본다.

## 테스트

```powershell
.venv\Scripts\python.exe -m pytest tests -q
npm run test:frontend
npx tsc -b
npm run build
```

전체 Python 테스트는 약 40분 걸릴 수 있다. 임시 폴더 권한 오류 시 새 경로를 지정해 실행한다: `.venv\Scripts\python.exe -m pytest tests -q --basetemp=.venv/pytest-onboarding-temp`.
`npm run test:frontend`는 `node --test tests/frontend/*.test.ts`와 같다. frontend 테스트는 vitest가 아니라 Node 테스트 실행기로 돌린다.

## 코드 둘러보기

| 경로 | 역할 |
|---|---|
| `backend/` | 레거시 API·SQL 생성·NER·캐시 기준선 |
| `backend/adaptive/api/` | `app_factory.py` 앱 조립, `database_registry.py` DB 목록, 관리자·챗봇 API |
| `backend/adaptive/registration/` | private DB 등록·검토·게시·삭제와 저장 |
| `backend/adaptive/mapping/` | 공통 항목 연결 제안·검토·게시 계약과 저장소 |
| `backend/adaptive/ontology/` | 공통 수강정보 항목과 버전별 활성 항목 |
| `backend/adaptive/compiler/` | QueryIR과 mapping을 SQL로 컴파일 |
| `backend/adaptive/policy/` | SQL AST와 읽기 전용 실행 정책 |
| `backend/adaptive/harness/` | QueryIR 검증·컴파일·실행 파이프라인 |
| `backend/adaptive/evaluation/` | gold·비교군·독립 평가 도구 |
| `backend/adaptive/catalog/` | DB schema·table·column 메타데이터 |
| `backend/adaptive/relations/` | 관계 그래프와 JOIN 경로 |
| `src/adaptive/` | 관리자 화면·적응형 API client·응답 검증 |
| `src/pages/MainPage.tsx` | DB 선택·관리 진입점과 강의·시간표 화면 |
| `tests/` | backend·frontend 회귀 테스트 |
| `docs/` | [읽기 목차](docs/README.md), 설계·평가·작업 기록 |

## Docker와 자료 범위

현재 `docker-compose.yml`은 레거시용 frontend(5173)·backend(8000)·Redis(6379)만 구성한다.
PostgreSQL·Ollama·적응형 서버(8123)는 포함하지 않으며, Compose의 비어 있는 연결 설정을 별도로 채워야 한다.
세 서버를 함께 쓰는 팀원 첫 실행은 위 Windows `dev:all` 경로를 따른다.

개발 자료는 이전 학기 수강편람을 바탕으로 팀이 만든 레거시 DB와 공개 수강정보를 모사한 DB다.
원본 엑셀·DB dump는 게시하지 않는다. 외부 공개 자료에서는 출처를 밝히고 교수명을 가린다.
최종 성능 주장 전 L1/L3, 개발 외부인 사용성 L4 등 남은 사람 작업은 [사람 검토 기준](docs/human-validation-checkpoints.md)을 따른다.
