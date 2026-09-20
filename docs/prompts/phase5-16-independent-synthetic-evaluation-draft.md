# Phase 5-16: 독립 합성 평가 세트 초안과 교차 검증

기존 40건 개발 세트와 분리된 합성 질문 30건의 평가 세트 초안을 작성하고 자동 검증해줘. 이번 단계는 질문·gold QueryIR·기대 결과의 작성과 합성 fixture 검증까지만 수행한다. Ollama 등 실제 모델 호출, 성능 평가, 사람 승인, 최종 동결은 하지 마.

## 먼저 확인

- 시작 시 `git status --short`와 최근 Conventional Commit 형식
- `AGENTS.md`
- `docs/schema-adaptive-text-to-sql-plan.md`의 Phase 5·평가 설계·다음 시작점·변경 이력
- `docs/next-session-handoff.md`
- `docs/independent-evaluation-set-design.md`
- `docs/human-validation-checkpoints.md`의 E3·L1·L3·L5
- `backend/adaptive/evaluation/contracts.py`, `loader.py`, `validator.py`, `reference.py`, `coverage.py`
- 기존 `backend/adaptive/evaluation/data/fixture_v1_cases.yaml`과 gold·cross-schema 테스트
- `backend/adaptive/fixtures/seed.py`, ontology, 합성 fixture builder

기존 40건과 Phase 5-13/14의 모델 결과는 변경하지 마. 새 질문을 만들 때 과거 모델의 오답 유형에 맞춰 질문을 선택하지 마. 기존 세트는 새 초안을 만든 후 겹침을 검사하는 데 사용해줘.

## 목표 1: 새 평가 세트 초안 30건

- 기존 `fixture-evaluation-set-v1` 계약을 재사용하고 별도 YAML 파일로 30건을 작성해줘. 기존 40건 파일은 수정하지 마.
- 새 `benchmark_id`를 사용하고 case ID도 기존 40건과 겹치지 않게 해줘. 예를 들어 `P2-041`부터 연속된 ID를 사용할 수 있다.
- `FixtureSeed v1`과 현재 ontology를 재사용한다. 따라서 이 세트는 **독립 질문 세트(question holdout)**이며 독립 학교나 unseen schema가 아니다.
- 각 case에 한국어 질문, 명시적인 gold QueryIR, typed 기대 행·offering key·cardinality·answerability, intent/paraphrase family, 작성 근거를 기록해줘. 정답을 런타임에 seed에서 자동 생성해 gold로 대체하지 마. 파일 안의 gold는 사람이 읽고 검토할 수 있어야 한다.
- 모든 case는 `NEEDS_HUMAN_REVIEW`이고 `reviewer_id`가 없어야 한다. 작성자가 스스로 승인하거나 `HUMAN_APPROVED`로 표시하지 마.
- `CourseOffering` root, 현재 semantic field와 허용 operator만 사용한다. 물리 table/column, 실제 CNU 질문·데이터·SQL을 포함하지 마.
- 결과가 0건·1건·여러 건인 질문과 단일·복수 filter, 서로 다른 semantic field를 포함해줘. 의미 없는 수치 채우기용 질문은 만들지 마.

## 목표 2: 기존 40건과의 교차 격리 검사

현재 `validate_evaluation_set()`은 한 세트 안의 일관성을 검사한다. 새 세트와 기존 세트 사이의 겹침을 검사하는 작은 재사용 함수를 추가해줘.

- 서로 다른 `benchmark_id`를 요구한다.
- case ID 중복을 거부한다.
- NFC 정규화와 공백 정리 후 같은 질문 문장을 거부한다.
- `paraphrase_family` ID 재사용을 거부한다.
- 이번 초안에서는 기존 `_intent_identity()` 기준의 동일 QueryIR intent도 거부한다.
- 검사 실패는 원문 질문이나 gold 값을 포함하지 않는 고정 code로 알린다.
- 이 자동 검사는 의미가 비슷한 다른 문장을 모두 찾아내지는 못한다. 독립적인 사람 검토가 남았다는 한계를 문서에 적어줘.
- 범용 validator나 기존 loader의 계약을 불필요하게 바꾸지 마.

## 목표 3: 합성 검증

- 새 30건이 기존 `load_evaluation_set()`과 `validate_evaluation_set()`의 **정식 30~50건 조건**을 통과해야 한다. 검증을 위해 `require_full=False`로 우회하지 마.
- seed fingerprint·ontology fingerprint·QueryIR fingerprint, 중복 질문, 물리 식별자 노출, 자료형·operator, offering key·기대 행을 기존 validator로 검사해줘.
- 동일 seed로 생성한 flat·normalized·legacy fixture에서 모든 gold IR이 안전하게 컴파일·실행되고 명시적인 기대 semantic 결과와 일치하는지 확인해줘. 기존 cross-schema 테스트의 실행 경로를 재사용해줘.
- 교차 검사 테스트에는 질문·case ID·paraphrase family·intent 각각의 충돌과 정상 분리를 포함해줘.
- 합성 검증 결과는 **gold와 fixture의 자기 일관성**이다. FakeModel을 사용한다면 oracle smoke라고만 표시하고 모델 정확도 수치로 발표하지 마.

## 문서와 사람 확인 경계

- `docs/independent-evaluation-set-design.md`에 새 세트의 상태를 “30건 작성·자동 검증 완료, 사람 검토·동결 전”으로 기록해줘. 작성 과정, 격리 기준, 자동 검사로 증명하지 못하는 부분을 간결하게 남겨줘.
- `docs/schema-adaptive-text-to-sql-plan.md`의 관련 본문·체크박스·다음 시작점·변경 이력과 `docs/next-session-handoff.md`를 실제 완료 범위에 맞게 갱신해줘. 평가 설계의 중요한 결정이 바뀌면 ADR도 갱신해줘.
- 기존 gold 40건과 새 30건 모두 `NEEDS_HUMAN_REVIEW`를 유지해줘. E3/L5·L1/L3 팀 결정을 지금 요청하지 마.
- 새 세트를 “동결”, “사람 승인”, “독립 성능 검증 완료”, “학교 간 일반화 검증”으로 표현하지 마. 다음 단계가 필요하다면 사람 결정 이전에 진행 가능한 합성 작업을 먼저 명시해줘.

## 제외 범위

- 실제 CNU 자료·private recording·`.env` 읽기 또는 출력
- Ollama 호출, 응답 저장, `run-recorded`, 새 모델 점수, prompt 선택·최적화
- 기존 40건 또는 Phase 5-13/14 결과 변경
- 모델 weight·Modelfile, PostgreSQL, FastAPI/React, legacy SQL 생성기 변경

## 검증·완료 보고

관련 테스트를 먼저 실행하고 마지막에 다음을 실행해줘.

- `.venv/Scripts/python.exe -m pytest tests -q`
- `.venv/Scripts/python.exe -m compileall -q backend tests`
- `git diff --check`

Windows 임시 폴더의 PermissionError가 재현되면 접근 가능한 `TEMP/TMP`를 사용한 사실을 정확히 보고해줘. 실패한 명령을 통과했다고 쓰지 마.

완료 보고에는 새 세트의 case 수와 상태, 교차 격리 검사 결과, 세 fixture 검증 결과, 테스트, 남은 사람 검토·동결 작업을 적어줘. 공개 가능한 합성 코드·문서만 과거 형식의 Conventional Commit으로 커밋하고 push는 하지 마.
