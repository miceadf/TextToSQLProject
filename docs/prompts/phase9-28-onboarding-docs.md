# Phase 9-28 지침: 팀원 첫 실행·읽기용 문서와 주석 정리 (A, 문서·주석만)

## 배경 (2026-10-17 사용자 결정)

9-27에서 이름을 정리했다(`api/app_factory.py`, `api/database_registry.py`, `src/adaptive/`, `backend/scripts/demo_ner_correction.py` 등 — 실제 결과는 git 상태로 확인). 이제 문서와 주석을 현재 코드에 맞춘다. 코드 동작은 바꾸지 않는다.

## 바꿀 것

1. **`README.md` 새로 쓰기** (한국어, 200줄 이내):
   - 이 저장소가 무엇인지 3~4줄: 레거시 Text-to-SQL 챗봇(기준선, 미세 조정 모델, 포트 8000)과 스키마 적응형 QueryIR 시스템(관리자가 DB 등록·mapping 검토·게시, 챗봇이 공통 항목으로 질문 해석, 포트 8123). 흐름 그림(텍스트) 한 개.
   - 준비: Python 버전(`env_check` 기준), Node 버전, Ollama와 필요한 모델 이름(`qwen2.5-coder:7b-instruct`, 레거시 모델은 `models/Modelfile` 저장소 상대 경로), PostgreSQL.
   - 설치: `.venv` 만들기, 그 Python으로 `backend/requirements.txt`, requirements에 없는 것(예: `torch`, `pytest`)이 있으면 명시, `npm install`. Windows 기준 명령 + 다른 OS 대안 한 줄.
   - 설정: `backend/.env.example`을 복사해 `backend/.env`를 만들고 값 채우기. 변수 표(이름·필수 여부·용도만, 값 없음): `DATABASE_URL`, `OLLAMA_BASE_URL`, `OLLAMA_MODEL`, `CHAT_MODEL`, `MAPPING_MODEL`, `SHOW_SQL`, `REDIS_URL`(쓰면), `OPENAI_API_KEY`·`OPENAI_MODEL`(선택, 유료·외부 전송), `PG_SOURCE_<이름>`. 실제 쓰는 변수 이름은 `api/dev_launcher.py`와 레거시 백엔드 코드에서 확인.
   - 실행: `npm run dev:all` → 5173(화면), 8000(레거시), 8123(적응형). 서버 시작 전 `python -m backend.adaptive.env_check`(OK/WARN/FAIL 의미).
   - 사용 흐름 한 단락: 화면 "DB 변경" → 등록 → 검토·게시 → 사용할 DB 선택 → 챗봇. 자세한 건 `docs/teammate-evaluation-db-guide.md`.
   - 테스트: `.venv\Scripts\python.exe -m pytest tests -q`(약 40분, 임시 폴더 권한 오류 시 `--basetemp`), `node --test tests/frontend/*.test.ts`, `npx tsc -b`, `npm run build`. `npm run test:adaptive-prototype`은 일부만 돈다는 점.
   - 코드 둘러보기: 주요 폴더 표(`backend/`(레거시), `backend/adaptive/{api,registration,mapping,ontology,compiler,policy,harness,evaluation,catalog,relations}`, `src/adaptive/`, `src/pages/MainPage.tsx`, `tests/`, `docs/`) 각 한 줄.
   - Docker: 지금 compose는 레거시용이라는 점(실제 compose 내용을 확인해 사실대로).
   - 기존 README의 틀린 내용(개인 PC 절대 경로, `v_courses_info` 오타 등)은 고치거나 뺀다. 레거시 DB 구성 설명이 필요하면 사실만 짧게.
2. **`backend/.env.example` 추가**: 위 변수 이름과 한 줄 주석만, 값은 비우거나 안전한 예시(`OLLAMA_BASE_URL=http://127.0.0.1:11434` 같은 비밀 아닌 기본값만). DSN·키 예시에 실제 값처럼 보이는 문자열을 넣지 않는다. `.gitignore`가 `.env.example`을 무시하지 않는지 확인.
3. **`docs/README.md` 추가** (문서 목차): 처음 읽을 순서(루트 README → 계획 문서의 개요·현재 범위·구조 절 → `teammate-evaluation-db-guide.md` → `demo-procedure.md`), 평가 담당 문서(`human-validation-checkpoints.md`, `independent-evaluation-set-design.md` 등 실제 있는 것), `docs/prompts/`와 날짜별 결과 문서는 "작업 기록"이라는 설명, 9-27의 옛 이름 → 새 이름 표(역사 문서에 옛 이름이 나오므로).
4. **`docs/next-session-handoff.md` 맨 위**에 사람용 요약 절(현재 기능 / 실행·읽기 경로 / 남은 사람 작업 / 알려진 한계, 10줄 이내)을 더한다. 아래 기존 기록은 그대로.
5. **`docs/demo-procedure.md` 갱신**: 현재 화면(탭 3개, AI 추천 모두 승인, 표 훑어보기, 값 사전 가져오기, 시험 질의, 삭제)과 ontology v3(개설 연도 없음)에 맞춘다. 이전 관찰 수치는 "당시 관찰값"으로 표시.
6. **모듈 설명(주석)**:
   - 설명이 없는 핵심 모듈에 2~4줄: `backend/adaptive/mapping/{contracts,rules,review,repository,bridge,cli}.py`, `backend/adaptive/ontology/{contracts,loader}.py`(입력·출력·책임, 제안→검토→게시 흐름, ontology 버전별 활성 항목).
   - 지금 동작과 어긋나는 설명 고침: `api/chat_query.py` 맨 위("SQL 없음" ↔ 개발 옵션 SQL 표시), `api/dev_launcher.py` 맨 위(세 값만 재사용·같은 모델 → 현재 모델 선택지), `api/__init__.py`·`evaluation/__init__.py` 소개(초기 합성 기능만 설명), `api/postgres_runtime.py` "Phase 8-21: period numbers" 주석에 목적 한 줄, `src/adaptive/{contracts,state,mappingReview,postgresRegistration}.ts` 맨 위 역할 한 줄.
   - Phase 번호 주석은 지우지 않는다(이력 추적용). 기능 설명이 빠진 곳에만 설명을 더한다.
7. `docs/schema-adaptive-text-to-sql-plan.md` 변경 이력 2026-10-17 "9-27 이름 정리, 9-28 팀원용 문서·주석 정리" 한 줄씩, 구조 절에 모듈 이름이 나오면 새 이름으로.

## 확인과 지킬 것

- 코드 동작 변경 없음(주석·docstring·문서만). `python -m compileall -q backend`, `npx tsc -b`, `npx eslint src`가 통과해야 한다.
- README의 명령·변수·포트·파일 경로는 모두 실제 코드·설정과 대조해 맞는 것만 쓴다. 확인 못 한 내용은 쓰지 않는다.
- 비밀 값·DSN·키를 문서에 쓰지 않는다. `backend/.env`를 열어 값을 보지 않는다(변수 이름은 코드에서 확인).
- 서버·데이터 안 건드림, 모델 호출 없음, 커밋 없음. UTF-8로 쓴다.
- 끝나면 바꾼 파일과 확인 결과를 짧게 적는다.
