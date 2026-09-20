# Phase 5-17: 독립 합성 세트 문항 정리·커버리지·oracle smoke

Phase 5-16에서 작성한 독립 합성 평가 세트 30건의 문항 표현을 점검하고, 기존 커버리지 도구와 `FakeModel` oracle smoke로 세트의 준비 상태를 확인해줘. **사람 검토·동결·실제 모델 평가는 이번 단계에 포함하지 않는다.** 기존 기능으로 충분하면 새 실행기나 보고서 형식을 만들지 마.

## 먼저 확인

- 시작 시 `git status --short`와 최근 Conventional Commit을 확인하고 기존 미커밋 변경을 보존한다.
- `AGENTS.md`, `docs/next-session-handoff.md`, `docs/schema-adaptive-text-to-sql-plan.md`의 Phase 5·ADR-028·다음 시작점·변경 이력, `docs/independent-evaluation-set-design.md`를 읽는다.
- `docs/human-validation-checkpoints.md`의 E2·E3·L1·L3·L5를 확인한다. 사람 결정을 지금 요청하거나 승인으로 표시하지 않는다.
- `backend/adaptive/evaluation/data/fixture_v1_independent_cases.yaml`, 기존 40건 YAML, `isolation.py`, `coverage.py`, `__main__.py`, `run_cli.py`, `runner.py`, `run_contracts.py`, 관련 테스트를 확인한다.
- 현재 도구가 `--input`으로 독립 세트 커버리지를 만들고 `run-smoke --gold`로 독립 세트를 실행할 수 있는지 확인한다. `run-recorded`는 실행하지 않는다.

## 작업 1: 문항 표현 점검

- P2-057의 “13:00 이후”는 `greater_than 13:00`과 경계 포함 여부가 모호하다. 의도한 배타적 조건이 한국어 질문에 분명히 드러나도록 고친다. 기존 gold IR과 기대 결과가 그대로 적절하면 질문만 수정한다.
- P2-059의 “요일에 'UE'가 들어가는”은 내부 요일 코드에 의존해 일반적인 학생 질문으로 부자연스럽다. 자연스러운 한국어 질문과 정확히 대응하는 허용 `QueryIR` 조건으로 고친다. `Schedule.day`의 의미와 seed 값을 확인하고 질문·gold IR·기대 결과·intent family가 서로 맞도록 한다. 단순 문구 교체로 의미가 맞지 않으면 필요한 필드만 함께 수정한다. 기존 개발 세트와의 intent 격리도 유지한다.
- 다른 문항에서도 질문과 `QueryIR`의 경계 조건·단위·코드 표현이 어긋난 곳이 있으면 같은 기준으로 **명백한 오류만** 정리하고 변경 이유를 기록한다. 과거 모델 오답에 맞춰 질문을 고르거나 새 질문을 추가하지 않는다.
- 수정 후 독립 세트 30건의 ID·benchmark ID, 기존 40건, `NEEDS_HUMAN_REVIEW`·reviewer 공란 상태를 보존한다. 초안 변경으로 gold set fingerprint가 달라지면 새로운 값을 기존 도구에서 계산하되 동결 fingerprint로 부르지 않는다. 검토 전 초안의 문구 수정에 불필요한 benchmark version 변경은 하지 않는다.

## 작업 2: 독립 세트 커버리지와 oracle smoke

- 기존 `load_evaluation_set()`의 정식 30~50건 계약으로 새 YAML을 로드한다. `require_full=False` 우회는 하지 않는다. `check_set_isolation()`을 다시 실행하고 flat·normalized·legacy의 gold IR 실행 결과가 명시된 기대 semantic rows와 일치하는지도 재검증한다.
- 기존 `build_coverage_report()` 또는 `python -m backend.adaptive.evaluation --input ...`을 재사용해 **독립 세트 단독** 커버리지 집계를 만든다. 최소한 case 수, 사용 semantic field·operator, filter 수 분포, 결과 0/1/여러 건, normalized JOIN 단계, review 상태와 `coverage_gaps`를 확인한다. 개발 세트와 합산하지 않고, 빈 범주·편중은 숨기지 않는다. 커버리지의 case별 원문 표나 전체 JSON을 공개 문서에 복사할 필요는 없다.
- 기존 `run-smoke --gold <독립 세트 경로> --output <임시 비추적 경로>`로 `FakeModel` oracle smoke를 실행한다. 세 variant × 30건 = 90 attempts, `config.oracle_smoke=true`, 모든 attempt의 구조·실행·semantic 비교 상태와 실패 코드를 확인한다. gold IR을 응답으로 주는 자기 일관성 점검임을 명시한다. 실패 시 모델 점수로 해석하지 말고 case·variant와 기존 고정 code를 이용해 원인을 조사한다.
- CLI에 범용적으로 재현되는 결함이 확인된 경우에만 작은 수정과 의미 있는 회귀 테스트를 추가한다. 새 모델 adapter, report schema, 평가 지표, 저장 경로 정책을 만들지 않는다. 검증용 임시 report는 Git에 추가하지 않는다.

## 테스트와 완료 기준

- 문항 수정에 맞는 테스트와 격리·cross-schema 테스트를 먼저 실행한다. 기존 커버리지 및 runner/CLI 관련 테스트도 실행한다.
- 독립 세트 단독 커버리지와 90-attempt oracle smoke를 실제로 실행해 결과를 확인한다. 기대와 다르면 실패 원인을 보고하고 성공으로 표시하지 않는다.
- 마지막에 `.venv/Scripts/python.exe -m pytest tests -q`, `.venv/Scripts/python.exe -m compileall -q backend tests`, `git diff --check`를 실행한다. Windows 기본 임시폴더 `PermissionError`가 재현되면 접근 가능한 `TEMP/TMP`와 필요한 pytest 옵션을 명시해 재실행하고, 실패한 명령과 우회 실행 결과를 구분해 보고한다.
- `docs/independent-evaluation-set-design.md`에는 수정 근거, **독립 세트만의 안전한 커버리지 집계**, oracle smoke 결과와 한계를 기록한다. `docs/schema-adaptive-text-to-sql-plan.md`의 관련 본문·체크박스·다음 시작점·변경 이력, `docs/next-session-handoff.md`를 실제 완료 범위에 맞게 갱신한다. 중요한 설계 결정이 바뀔 때만 ADR을 추가·수정한다.
- 완료 보고에는 변경 파일, 문항 수정 근거, 격리·3개 fixture·커버리지·oracle smoke 결과, 전체 테스트, 남은 위험을 적는다. 공개 가능한 합성 코드·문서만 최근 형식의 Conventional Commit으로 커밋하고 **push는 하지 않는다**.

## 경계

- 기존 40건 gold, Phase 5-13/14 결과, private bundle·recording, 실제 CNU 질문·DB 행·SQL·비밀값을 수정·노출하지 않는다.
- Ollama 호출, prompt 탐색, `run-recorded`, 실제 모델 점수, PostgreSQL·FastAPI/React·legacy 변경은 하지 않는다.
- 새 30건은 계속 **사람 검토·동결 전 question holdout 초안**이다. 같은 seed·ontology를 썼으므로 unseen school 또는 학교 간 일반화 검증으로 표현하지 않는다. Oracle smoke의 90건 성공을 모델 정확도로 발표하지 않는다.
- L1·E2 사람 검토와 동결은 남긴다. E3/L5·L1/L3의 팀 결정도 해당 실제 사용·성능 확정 관문까지 보류한다.
