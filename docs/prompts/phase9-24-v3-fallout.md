# Phase 9-24 지침: ontology v3(개설 연도 사용 중단) 뒤 전체 pytest 실패 29건 정리

## 상황

- 9-16에서 공통 ontology를 v3으로 올리고 `CourseOffering.year`를 사용 중단했다(ADR-101, 사용자 결정). 당시 관련 테스트만 돌렸다.
- Claude가 커밋 전 전체 `pytest tests`를 돌렸더니(`--basetemp`를 저장소 밖 임시 폴더로) **29 failed, 2303 passed, 11 skipped**. 실패 파일(개수): `test_admin_test_queries`(1), `test_arm_live_cli`(3), `test_capture_targets`(1), `test_direct_sql_arm`(1), `test_mapping_carry_over`(4), `test_postgres_live_smoke`(3), `test_raw_columns_and_select`(1), `test_recorded_model`(1), `test_schema_compare`(2), `test_schema_variation_suite`(4), `test_synthetic_active_database`(1), `test_upload_ai_proposal`(4).
- 대표 오류: `assert 114 == 120`(oracle/benchmark 사례 수), `{'count': 190} != {'count': 200}`, `KeyError: 'CourseOffering.year'`, 항목 수 `15 == 16`/`14 == 15`, `'AI_UNAVAILABLE' == 'AI'`, `'MAPPING_FAILED' != 'QUERY_FAILED'`, `'NOT_UNDERSTOOD' == 'SUCCEEDED'`, 응답 400, `IndexError`.
- 기본 임시 폴더 `C:\Users\micea\AppData\Local\Temp\pytest-of-micea`는 권한 문제로 쓸 수 없다. pytest는 `--basetemp`를 작업 공간 안의 git-ignored 위치나 샌드박스 임시 위치로 지정해 돌린다(끝나면 지운다).

## 원칙 (실패마다 하나로 분류해서 처리)

1. **과거 기록 재현(평가·benchmark·oracle·gold 재생·녹화 replay·capture targets)**: gold·fixture·녹화가 만들어진 ontology 버전(v1/v2)으로 평가한다. 즉 오프라인 평가 경로는 현재 버전이 아니라 **기록이 선언한 버전**(`ontology.at_version(v)`)을 써서, 기존 수치(120건 등)가 그대로 재현되게 한다. gold·fixture·녹화 파일은 바꾸지 않는다. 기록에 버전이 없으면 v2로 본다(9-16 이전 기록). 실제 챗봇 HTTP를 부르는 평가(`blind_eval` 등)는 그대로 현재 runtime(v3)이다. 9-16 결과 문서의 "기대 결과가 달라지는 평가 문항" 절을 이 원칙에 맞게 고친다(오프라인 benchmark는 기록 버전으로 재현, 현재 챗봇으로 다시 재면 연도 문항은 거절).
2. **제품 동작이 9-16에서 의도대로 바뀐 것**(새 등록·검토의 항목 수, carry-over·schema compare에서 year가 빠짐 등): 테스트 기대를 v3 동작으로 고치고, 테스트 이름이나 주석에 이유를 한 줄 남긴다. 기존 v2 base에서 v3 target으로 carry-over할 때 year는 옮기지 않는 것이 맞다.
3. **충돌·회귀**(`KeyError: 'CourseOffering.year'`, `IndexError`, 정상 경로가 `AI_UNAVAILABLE`·`MAPPING_FAILED`·400·`NOT_UNDERSTOOD`로 바뀐 것 등): 테스트를 고치지 말고 코드를 고친다. 예: AI 제안에 사용 중단 필드가 섞여 와도 그 필드만 버리고 나머지는 쓰기, 필수 필드 목록에서 사용 중단 필드 제외, 시험 질의·live smoke 경로가 year를 가정하지 않기.

## 할 일

- 실패 29건을 하나씩 위 1·2·3으로 분류해 고친다. 분류표(테스트 이름 → 분류 → 한 줄 이유 → 고친 곳)를 `docs/phase9-16-retire-offering-year-results-2026-10-13.md` 끝에 "9-24 전체 테스트 정리" 절로 더한다.
- 12개 실패 파일 전부와 9-16·9-23에서 바꾼 파일의 테스트를 다시 돌려 모두 통과시킨다. 가능하면 `pytest tests -q`(전체, 약 40분)를 마지막에 한 번 돌려 결과를 적는다. 시간이 모자라면 실패 파일과 관련 파일까지만 돌리고 그렇다고 적는다.
- 사용 중단 원칙(ADR-101)이 바뀌는 것이 아니다. 제품 runtime은 v3 그대로다.

## 지킬 것

- gold `review_status`·gold·fixture·녹화 파일 수정 금지. 사용자 서버·데이터 안 건드림, live 모델 호출 없음, 커밋 없음, `backend/.env` 값 출력 없음. UTF-8로 쓴다.
- 문서: `docs/schema-adaptive-text-to-sql-plan.md` ADR-101에 "오프라인 평가는 기록 버전으로 재현" 한 줄, 변경 이력 2026-10-13 "9-24" 한 줄.
- 끝나면 분류 요약(1·2·3 각 개수), 바꾼 파일, 테스트 결과를 짧게 적는다.
