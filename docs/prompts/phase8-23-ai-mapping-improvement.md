# Phase 8-23 지침: AI mapping 제안 개선

작성일: 2026-10-06. 사용자 결정: 진행.

## 1. 목적

- 화면 등록 때 AI가 항목마다 고르는 첫 후보가 더 자주 맞게 한다. 관리자가 고칠 양을 줄이는 것이 목표다.
- 관리자 검토는 그대로 필수다. 제안은 검토의 출발점일 뿐이다.

## 2. 측정

- 도구: `python -m backend.adaptive.evaluation.school_mapping_check --label <이름> [--repeat N] [--rules-only]`
  - 화면 등록과 같은 경로로 제안을 만든다: 규칙 → 예시 값 → 로컬 모델 → 후보 정렬.
  - 대상은 AI가 만든 개발용 schema 5개다: `other_school`, `wide_school`, `code_school`, `period_school`, `packed_school`.
  - 항목마다 첫 후보가 개발자 정답과 같은지 센다.
- FK로 이어진 column은 같은 값을 담으므로 둘 다 정답으로 본다(예: `gangjwa.gm_cd`와 `gyogwamok.gm_cd`). 8-21 등록 때의 "AI 정답 수"는 이것을 구분하지 않았다.
- 모델 출력이 흔들리므로 3번 반복해 합계로 본다.
- **이 수치는 개발 진행 확인용이다.** 정답과 schema 모두 AI가 만들었으므로 처음 보는 학교에 대한 적응성 근거가 아니다(ADR-086). 평가는 팀원 DB로 한다.

## 3. 바꿀 후보 (측정을 보고 고른다)

1. **예시 값 빠짐:** 값 종류가 10개를 넘는 column은 예시 값이 하나도 prompt에 들어가지 않는다. 이름·코드·교수명 column이 그렇다. 8-21의 표본 읽기로 10개 예시를 넣는다.
2. 그 밖의 원인은 기준선의 틀린 항목을 보고 정한다. 공통 ontology(항목 이름·별칭·설명)는 바꾸지 않는다(바꿔야 하면 사용자 결정).

## 4. 지킬 것

- 개발용 schema 5개에 맞춘 특수 규칙(이 schema의 column 이름을 코드에 넣는 것 등)은 만들지 않는다.
- 예시 값은 loopback 모델 prompt에만 들어가고 저장하지 않는다(8-12와 같음).
- prompt가 바뀌면 version을 올린다.
- push하지 않는다.
