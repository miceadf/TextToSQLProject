# Phase 9-01 지침: L1 검토 표 준비

작성일: 2026-10-06. 사용자 결정: 진행(ADR-086 "L1 검토 표는 Phase 9 진입 때 준비", ADR-089 순서).

## 1. 목적

- L1은 gold 질문마다 사람이 확인하는 관문이다. 확인할 것은 한국어 뜻, gold 조건, 정답 행, 중복 행, 빈 결과다.
- AI는 검토 표만 준비한다. 판정은 사용자가 먼저 하고, 그다음 팀원이 한다. AI는 어떤 사례도 승인으로 바꾸지 않는다.

## 2. 대상

1. 합성 benchmark 70개(`fixture_v1_cases.yaml` 40개, `fixture_v1_independent_cases.yaml` 30개). 같은 합성 seed(개설 강좌 6개) 위에 있다.
2. 팀원 질문 파일(Phase 8-22). 표는 팀원의 private 폴더에만 만들고, 내용을 출력하지 않는다. AI는 평가 실행 전까지 열지 않는다.

## 3. 표

- Markdown(`docs/l1-review/fixture-v1-review.md`): 읽는 용도. 맨 앞에 seed 표, 그다음 사례마다 아래를 보인다.
  - 질문, gold 조건(한국어 이름), 보여 줄 항목, 정답 행 수와 행.
  - 자동 확인 메모: 빈 결과, 같은 내용 행, 질문 표현과 조건 값의 차이, 경계값(초과/이상), 같은 뜻 다른 표현 묶음.
- CSV(`docs/l1-review/fixture-v1-review.csv`, Excel용 UTF-8 BOM): 판정·의견·검토자·검토일 칸을 비워 둔다.
- 두 파일 모두 gold 파일 지문(SHA-256)을 담는다. gold가 바뀌면 이전 판정은 이어받지 않는다.

## 4. 판정 기록

- `python -m backend.adaptive.evaluation.l1_review record <CSV>`
  - 판정은 승인·수정·보류 중 하나다. 판정한 행에는 검토자를 적는다. 수정·보류에는 의견을 적는다.
  - 빈 판정은 "미검토"로 센다.
  - 기록(`<CSV>.record.json`)에는 gold 지문, 판정 개수, 사례별 판정이 들어간다.
- gold의 `review_status`는 이 도구가 바꾸지 않는다. 승인을 gold에 반영하는 것은 기록을 보고 사용자가 정한다.

## 5. 제약

- 합성 seed만 쓴다. 실제 학생 정보나 비밀값은 없다.
- push하지 않는다.
