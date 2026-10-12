# Phase 9-16 지침: 공통 항목 "개설 연도"(CourseOffering.year) 사용 중단 — 공통 ontology 버전 3

## 사용자 결정 (2026-10-13, 공통 ontology 변경 = 사용자 결정으로 기록)

- 수강 강의를 찾을 때 개설 연도로 검색하는 경우는 없다. 공통 항목에서 "개설 연도"를 뺀다(레거시만 보고 내린 결론이 아님).
- 방법은 **A. 사용 중단**: 새 ontology 버전에서 사용 중단으로 표시해 등록·검토·AI 추천·챗봇에서 사라지게 하고, 이미 게시된 mapping과 평가 정답 데이터는 그대로 읽히게 둔다. 항목 정의 삭제(B)는 하지 않는다.
- **학기(CourseOffering.semester)는 그대로 둔다.**
- 배경: 레거시 등록에서 AI가 개설 연도를 `cnu_courses.course_year`(실제 값 1~5, 학년)에 연결했다. 연도 질문을 학년 칸으로 비교하는 조용한 오답 위험이었다.

## 현재 구조 (확인됨)

- `backend/adaptive/ontology/course-domain.yaml` `version: 2`, 필드마다 `since_version`(Phase 8-18: 버전은 필드를 더하기만 함). `CourseOffering.year`는 `required: true`.
- `ontology/contracts.py`: `since_version`, `accepts_version`, `fields_at(version)`.
- `CourseOffering.year` 참조: `api/chat_query.py`, `api/legacy_view_profile.py`, `api/synthetic_registry.py`, 평가 데이터·fixture(`evaluation/data/*.yaml`, `fixture_mapping.py`, `reference.py`, `variant_*`), `dataset/profiles/*.yaml`, frontend `reviewFieldName.ts`, `MappingReviewPanel.tsx`, `testQueries.ts`, 그리고 여러 테스트.

## 만들 것

1. **ontology 계약**: 필드에 `retired_in_version: int | None`(기본 None)를 더한다. ontology `version: 3`으로 올리고 `CourseOffering.year`에 `retired_in_version: 3`. 
   - "버전 v에서 쓰는 필드" = `since_version <= v` 이고 (`retired_in_version`이 없거나 `> v`).
   - `accepts_version`은 1~3을 받는다. v1·v2로 만든 기록(게시 mapping, 평가 gold, fixture)은 그대로 유효하다.
   - 사용 중단 필드는 `required`로 게시를 막지 않는다.
2. **현재 버전(3) 기준으로 사라지게**:
   - 새 등록의 규칙·AI mapping 제안, 검토 표(`review.fields`), 게시 검사, 값 사전·확장 항목 대상 목록에서 빠진다.
   - 챗봇: 모델에 주는 공통 항목 목록·QueryIR JSON schema(`queryir-schema-v1` 열거)·값 힌트 대상에서 빠진다. 질문이 연도를 말하면(예: "2024년에 개설된 강의") 기존 "이 DB에 없음/정보 없음" 거절과 같은 경로로 답한다(조용히 무시하지 않는다). 모델이 그래도 year를 내면 QueryIR 검증에서 기존 "지원하지 않는 항목"과 같이 처리한다.
   - **이미 게시된 mapping이 year를 연결해 두었어도** runtime(현재 ontology 3)에서는 year를 "이 DB에 없음"으로 취급한다(챗봇·시험 질의·게시 재검사 공통). 저장된 파일은 바꾸지 않는다.
   - frontend: 검토 표·이름표(`reviewFieldName.ts`)에서 year가 서버 응답에 없으면 자연히 안 보이게. 하드코딩된 목록이 있으면 정리한다.
3. **바꾸지 않을 것**: 평가 gold·fixture·데이터셋 profile의 year 기록(v1/v2 기록으로 유효), legacy 기준선 챗봇, 학기 항목, 다른 필드.
   - 평가 도구가 현재 버전으로 year 질문을 채점할 때 기대 결과가 달라지면(예: 합성 benchmark에서 year 조건이 있는 문항), 그 문항 목록과 이유를 보고서에 적고 gold는 고치지 않는다. AI는 gold `review_status`를 바꾸지 않는다.
4. **레거시 기준 확인**: `legacy_chat_compare` 30문항 중 q28 "2024년에 개설된 강의 알려줘"는 원래 거절이 정답(`UNSUPPORTED_FIELD`)이다. 이 경로가 계속 거절인지 테스트로 확인한다(모델 호출 없이 가능한 범위: 가짜 모델 출력 또는 기존 unknown-condition 경로).

## 테스트

- ontology: v1·v2·v3의 `fields_at`/활성 필드, retired 필드가 v3에서 빠짐, v2 기록 유효.
- 등록·검토·게시: 새 PostgreSQL/SQLite 검토에 year 없음, year가 연결된 v2 게시 mapping을 읽어도 runtime에서 year는 unsupported, `required`로 게시가 막히지 않음.
- 챗봇: 모델 항목 목록·JSON schema에 year 없음, 연도 질문은 거절 경로.
- 기존 관련 테스트 중 year를 쓰는 것은 의도에 맞게 고친다(현재 버전 동작을 확인하던 테스트는 v3 동작으로, 기록 호환을 확인하던 테스트는 그대로).
- 관련 테스트 파일만 실행한다(전체 pytest는 Claude가 커밋 전에 한 번). frontend는 관련 테스트와 `npx tsc -b`.

## 지킬 것

- 사용자 서버(8000·8123·5173)와 사용자 데이터(`.schema-adaptive/` 아래 등록·profile·평가 기록)를 건드리지 않는다. 테스트는 임시 디렉터리.
- legacy DB 읽기 전용, live 모델 호출 없음, 커밋 없음, `backend/.env` 값 출력 없음.
- 문서(`AGENTS.md` 규칙):
  - `docs/schema-adaptive-text-to-sql-plan.md`: ADR-101(공통 ontology v3, 개설 연도 사용 중단, **사용자 결정**, 이유, A안을 고른 이유, 학기는 유지), 로드맵 9-16, 변경 이력 2026-10-13, ontology를 설명하는 절이 있으면 v3 반영.
  - `docs/human-validation-checkpoints.md`: "공통 ontology v3(개설 연도 사용 중단)는 사용자 결정이며 팀 공유가 필요하다" 한 줄. 관문 승인 없음.
  - `docs/next-session-handoff.md` 한 줄.
- 끝나면 바꾼 파일, 테스트 결과, 기대 결과가 달라진 평가 문항(있으면)을 짧게 적는다.
