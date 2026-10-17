# Phase 9-27 지침: 팀원이 읽기 쉽게 이름 정리 (B·C, 코드)

## 배경 (2026-10-17 사용자 결정)

팀원이 저장소를 받아 처음 실행·읽을 때 헷갈리는 이름을 정리한다. 동작은 바꾸지 않는다. Codex 읽기 전용 감사(`feat/schema-adaptive-agent`, 커밋 `bbb38b3` 기준)의 지적 중 사용자가 B·C 전부를 승인했다.

## 바꿀 것

### B. 작은 이동·삭제
1. `backend/test_correction.py`(assertion 없는 예제 스크립트, 저장소 루트에서 `pytest`를 치면 `ModuleNotFoundError: keyword_extract`로 수집 실패) → `backend/scripts/demo_ner_correction.py`로 옮긴다. `if __name__ == "__main__":` 아래로 실행 코드를 넣고, import는 `python -m backend.scripts.demo_ner_correction`로 돌 수 있게 패키지 기준으로 고친다(필요하면 `backend/scripts/__init__.py`). 이 파일을 import하는 곳은 없다. 문서 참조가 있으면 새 경로로.
2. 참조 없는 `src/assets/vite.svg`, `src/assets/react.svg` 삭제(삭제 전에 참조 0개를 다시 확인).
3. `src/adaptive-prototype/AdaptivePrototypePage.tsx` → 역할에 맞는 이름(실제 역할을 코드로 확인해 정한다; 합성 clarification 시연 화면이면 `ClarificationDemoPage.tsx`). import 1곳(`src/main.tsx`)과 테스트 문자열 참조 갱신.

### C. 큰 이름 변경
1. `backend/adaptive/api/synthetic_prototype.py` → `backend/adaptive/api/app_factory.py` (dev 서버의 적응형 FastAPI 앱 조립: 합성 기본 구성 + PostgreSQL 등록·QueryIR 챗봇).
2. `backend/adaptive/api/synthetic_registry.py` → `backend/adaptive/api/database_registry.py` (DB 목록·course catalog; 합성 DB와 게시된 PostgreSQL DB를 함께 다룸).
3. `src/adaptive-prototype/` → `src/adaptive/`.

각 변경에서:
- 모든 import(제품·테스트), `package.json` 스크립트, `python -m ...` CLI 경로, 테스트 안의 모듈 경로 문자열·monkeypatch 대상 문자열, `vite`/`tsconfig`/eslint 설정의 경로를 함께 바꾼다.
- **바꾸지 말 것**: 저장된 계약 버전 문자열(예: `synthetic-database-registry-v1`), DB ID(`synth-clarification-prototype` 등), HTTP 경로(`/api/adaptive/v1/...`), 브라우저 URL 경로(`/adaptive-prototype` 등 페이지 경로), `localStorage` 키, `.schema-adaptive/` 저장 형식. 이것들은 사용자 데이터·북마크·기록과 연결된다.
- 옛 모듈 이름으로 import하는 외부 사용자는 없으므로 호환 shim은 두지 않는다. 단 `npm run` 스크립트 이름(`dev:all`, `dev:adaptive-prototype` 등)은 사용자가 쓰므로 **스크립트 이름은 그대로** 두고 내용만 새 경로로.
- 이름이 바뀐 모듈의 맨 위 설명(docstring/주석)에서 "synthetic-only", "no PostgreSQL" 같은 지금과 다른 설명을 실제 역할로 고친다(설명 정리는 다음 단계에서도 하지만, 이름과 설명이 어긋나지 않게 여기서 최소한 맞춘다).
- 역사 문서(`docs/prompts/`, 날짜별 결과 문서, 계획 문서의 지난 변경 이력·ADR 본문)는 고치지 않는다(그 시점 기록).

## 확인

- `git grep`으로 옛 이름(`synthetic_prototype`, `synthetic_registry`, `adaptive-prototype/`, `AdaptivePrototypePage`, `test_correction`)이 코드·설정·현재 안내 문서(README·팀원 안내서·demo-procedure)에 남지 않았는지 확인한다. 역사 문서와 URL 경로 `/adaptive-prototype`는 예외로 목록에 적는다.
- `npx tsc -b`, `npx eslint src`, `node --test tests/frontend/*.test.ts`(EPERM이면 `--test-isolation=none`), `npm run build`.
- 백엔드: 바뀐 모듈을 import하는 테스트 파일 전부 + `python -m compileall -q backend`. 전체 pytest는 Claude가 마지막에 한 번 돌린다. pytest는 `--basetemp`를 git-ignored 위치로.
- 사용자 서버(8000·8123·5173)·데이터 안 건드림, live 모델 호출 없음, 커밋 없음, `backend/.env` 값 출력 없음. UTF-8로 쓴다. 파일 이동은 `git mv`로.
- 끝나면 바꾼 이름 표(옛 → 새, 영향 파일 수), 남겨 둔 옛 이름 예외 목록, 테스트 결과를 짧게 적는다.
