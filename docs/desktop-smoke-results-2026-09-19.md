# 데스크톱 smoke test 결과 (2026-09-19)

기준: `docs/desktop-test-handoff.md`. 합성 질문과 읽기 전용 DB 점검만 수행했다. 실제 질문·모델 응답·SQL·접속 문자열·식별 정보는 이 문서에 기록하지 않았다.

## 환경

- Git: `feat/schema-adaptive-agent`, `251dd91bb56c70cb677d2673a7451b1b47fc9d2d`; 시작 시 작업 트리 깨끗함.
- Windows 데스크톱, Python 3.13.7 (`.venv`), Node 24.15.0, npm 11.17.0, Docker CLI 29.5.3, Ollama 0.34.2.
- `psql` CLI 없음. PostgreSQL 점검은 `.venv`의 `psycopg2`로 수행.
- Docker daemon 접근은 현재 환경에서 거부됨. Compose 실행·컨테이너 통합은 검사하지 않음.
- `.venv`에 `pytest`가 없고 `sqlglot`이 30.11.0이라 처음에는 테스트 1건이 실패했다. `pytest` 9.1.1과 요구사항에 고정된 `sqlglot` 30.18.0을 `.venv`에 설치하고 재실행했다. 저장소 의존성 파일은 변경하지 않음.

## Ollama

- 로컬 `/api/tags` 응답 정상. 설치된 모델은 `text2sql_qwen:latest` 1개이며, 설정된 `OLLAMA_MODEL=text2sql_qwen`으로 호출 성공. 목록의 모델 ID 접두부: `7070730bf2f2`. 실제 weight의 사용 권한·출처는 확인하지 않음.
- 합성 수강 질문 3개와 첫 질문 반복 1회, 고정된 합성 schema context로 `/api/generate` 호출. 4/4 응답 존재, 모두 `SELECT` 문자열 포함, JSON parse 불가, Markdown fence 없음. 첫 호출 약 3.6초, 나머지 약 0.1~0.2초. 이는 출력 형식 관찰이며 정확도·성능 측정값이 아님.
- 기존 모델은 SQL을 반환한다. 새 `QueryIR` 생성 성공으로 해석하지 않음. VRAM·메모리는 측정하지 않음.

## PostgreSQL

- 설정된 로컬 DB에 연결, `SELECT 1` 성공. PostgreSQL 18.3. `information_schema`와 `pg_catalog` 메타데이터 조회 성공.
- 보이는 일반 테이블 8개, view 1개, PK 제약 8개, FK 제약 10개. `v_course_info` 존재, SELECT 권한 있음, 컬럼 20개. `SELECT 1 FROM v_course_info LIMIT 0` 성공; 실제 행은 조회하지 않음.
- 점검 세션에 `transaction_read_only=on`, `statement_timeout=5s` 적용. SSL은 사용 중이지 않음. 연결 계정은 superuser라 계정 자체의 최소 읽기 권한은 증명하지 못함. 운영 DB 권한 정책·실제 데이터 사용 범위도 확인되지 않음.
- DDL/DML 차단 시험은 실행하지 않음. PostgreSQL adaptive compiler/executor 실행 성공으로 해석하지 않음.

## 합성 평가·빌드

- `python -m pytest tests -q`: **254 passed, 3 skipped, 322 subtests passed** (`.venv` Python으로 실행).
- `python -m compileall -q backend tests`, `npm run lint`, `npm run build`: 모두 통과. build에서 오래된 Browserslist 데이터 경고가 나왔으나 빌드는 성공.
- 루트 `python -m pytest --collect-only -q`: **257 tests collected, 1 error**. `backend/test_correction.py`의 `ModuleNotFoundError: No module named 'keyword_extract'`로 중단. adaptive `tests/` 결과와 별개인 기존 수집 문제.
- `python -m backend.adaptive.evaluation --format review-table`: 최초 실행은 Windows 기본 `cp949`의 `UnicodeEncodeError`로 실패. `PYTHONIOENCODING=utf-8` 설정 후 통과; 40건 모두 `NEEDS_HUMAN_REVIEW`.
- `run-smoke`: 합성 gold oracle 120/120 실행·semantic match, 40/40 cross-schema 일관성. 보고서: `.schema-adaptive/reports/desktop-oracle-smoke-20260919.json` (gitignore 경로). 실제 모델 정확도 아님.

## 남은 확인과 다음 작은 작업

- E1·E2·E3·E4 및 L1~L7 사람 승인 상태는 이 smoke test로 변경되지 않음. E5의 이 데스크톱 환경 재현 결과만 위에 기록.
- 실제 모델의 `QueryIR` 출력, PostgreSQL adaptive 실행, 학교 간 일반화, 전체 서비스 end-to-end는 미검증.
- 다음 개발 단위: Ollama adapter의 JSON 반환 및 timeout/empty/provider 오류 변환을 합성 transport로 구현·시험. 실제 DB 질의 전에는 superuser가 아닌 승인된 읽기 전용 계정과 데이터 사용 범위를 확인.
