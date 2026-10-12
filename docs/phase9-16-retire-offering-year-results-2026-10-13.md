# Phase 9-16 결과 — 개설 연도 사용 중단 (2026-10-13)

사용자 지침 `docs/prompts/phase9-16-retire-offering-year.md`를 기존 미커밋 작업 위에 구현했다. 공통 ontology v3의 `CourseOffering.year`는 정의를 보존하고 사용 중단한다(ADR-101, 사용자 결정). 학기와 레거시 기준선은 유지한다. 버전 1·2 gold·fixture·dataset profile·기존 게시 파일 및 gold `review_status`는 수정하지 않았다.

현재 등록·규칙/AI 제안·검토 표·게시·값 사전/확장 대상·모델 prompt·QueryIR schema·값 힌트에서는 year가 빠진다. 기존 v2 mapping에 연결되어 있어도 현재 runtime은 unsupported다. 연도 질문은 `UNSUPPORTED`, 질문에 없는데 모델이 year를 생성하면 `UNKNOWN_FIELD`로 거절한다. 과거 draft의 required/pending year는 게시를 막지 않는다. SQLite 강의 목록은 year를 제외한 14개 항목을 사용하며, 기존 catalog DTO의 year key는 null로 유지한다.

## 검증

- backend 관련 18개 파일: 최종 확인한 **481 passed**, 22 subtests. 전체 pytest 없음.
  - ontology·mapping·chat·hint·fake AI·gold·fixture runner·variant benchmark 12개 파일: 305 passed, 22 subtests.
  - PostgreSQL 등록·SQLite 게시 2개 파일: 51 passed.
  - 확장·값 사전·PostgreSQL runtime·legacy adaptive profile 4개 파일: 124 passed; 마지막 adaptive smoke의 기존 `MAPPING_MISSING` 기대를 현재 `UNKNOWN_FIELD`로 갱신한 뒤 해당 테스트만 재실행하여 1 passed. 레거시 챗봇 구현은 변경하지 않았다.
- frontend 관련 5개 파일: **39 passed**. `npx tsc -b` 통과.
- `git diff --check` 통과. 변경 파일은 UTF-8.
- fake model/reader/executor/transport 및 임시 디렉터리만 사용했다. 테스트 프로세스에만 `.venv/phase916-temp` 아래 tempfile·mkdir mode=0777을 적용하고 plugin 자동 로딩/cacheprovider를 끄는 기존 Windows sandbox 우회를 사용했다. 등록 첫 실행에서 일시적인 500이 발생했다. 기존 Windows 임시 파일 접근 문제를 고려해 임시 경로 안의 rename PermissionError만 최대 20회/50ms 재시도한 최종 51개 실행은 통과했고, 재시도 3회를 관측했다. 첫 500과 해당 rename 오류의 동일 원인 여부는 별도 확정하지 않았다. 제품 권한 처리는 변경하지 않았다.
- 사용자 서버(8000·8123·5173), 사용자 `.schema-adaptive` 데이터, legacy DB를 조작하지 않았다. live 모델 호출·commit·`backend/.env` 값 출력 없음.
- AGENTS.md의 Luna/opencode 독립 리뷰는 사용자 no-live-model 지침과 충돌하므로 실행하지 않고 대기로 기록했다. 팀 공유 필요, 사람 관문 승인 없음.

## 기대 결과가 달라지는 평가 문항 — 기록 재현과 현재 챗봇 구분 (9-24 보완)

오프라인 benchmark·oracle·gold/녹화 replay·capture targets는 현재 runtime 버전이 아니라 기록이 선언한 `ontology.at_version(v)`로 재현한다. 선언이 없는 과거 기록은 v2로 본다. 금번 fixture gold는 v1을 선언하므로 연도 문항도 그대로 실행되며 core oracle 120건, extended oracle 200건의 기대값을 보존한다. 실제 챗봇 HTTP를 호출하는 `blind_eval` 등은 현재 v3 그대로이며 연도 질문을 거절한다. gold·fixture·녹화·review_status를 바꾸지 않는다.

| 평가 세트 | 문항 | 연도 사용 | 오프라인 기록 재현 | 현재 챗봇 v3로 다시 측정 |
|---|---|---|---|---|
| `fixture_v1_cases.yaml` | P2-009 | 2026년 조건 및 year projection | v1 기존 정답 재현 | 정보 없음/거절 |
| `fixture_v1_cases.yaml` | P2-028 | 2026년 이후 조건 및 year projection(원래 0건) | v1 기존 0건 재현 | 정보 없음/거절 |
| `fixture_v1_independent_cases.yaml` | P2-049 | 2026년·1학기·교양 조건 | v1 기존 정답 재현 | 연도 조건 때문에 거절 |
| `fixture_v1_independent_cases.yaml` | P2-063 | 2026년·정원 35 조건 | v1 기존 정답 재현 | 연도 조건 때문에 거절 |
| synthetic variant benchmark | v09 | year=2025 조건 및 year projection | profile의 v1로 기존 결과 재현 | 정보 없음/거절 |
| synthetic variant benchmark | v19 | year=2026 및 semester=1 조건 | profile의 v1로 기존 결과 재현 | 정보 없음/거절 |

9-16 당시 현재 v3를 넣은 fixture runner의 38/40·variant `FAILED_UNKNOWN_FIELD` 기록은 당시 구현의 관측값이다. 9-24부터 오프라인 평가 정책을 위와 같이 바로잡았다. variant `grade-trap`처럼 원래 year 미지원인 경우는 기록 버전에서 `MAPPING_MISSING` 거절을 기존 scorer로 채점한다. 버전 선언 없는 variant 규칙/AI 매핑 benchmark와 학교형 매핑 check는 v2로 재현한다. 새 제품 등록·검토는 v3다. 이 검증은 oracle/fake 모델 기반이며 모델 성능 측정이나 사람 관문 승인이 아니다.

`legacy_chat_compare` q28 “2024년에 개설된 강의 알려줘”는 원래 `UNSUPPORTED_FIELD`이며 기대 결과 변화가 없다. 현재 adaptive 챗봇의 연도 거절과 모델 생성 year의 `UNKNOWN_FIELD`는 기존 v3 회귀 테스트로 유지한다.

## 이번 단계에서 변경한 파일

기존 미커밋 파일의 다른 변경은 보존했다. 아래는 이번 단계에서 실제로 편집하거나 추가한 파일만이다.

```text
backend/adaptive/ontology/contracts.py
backend/adaptive/ontology/course-domain.yaml
backend/adaptive/mapping/rules.py
backend/adaptive/mapping/ai_proposal.py
backend/adaptive/mapping/review.py
backend/adaptive/api/admin_test_queries.py
backend/adaptive/api/chat_query.py
backend/adaptive/api/legacy_view_profile.py
backend/adaptive/api/postgres_registration.py
backend/adaptive/api/postgres_runtime.py
backend/adaptive/api/review_publish.py
backend/adaptive/api/synthetic_registry.py
backend/adaptive/api/value_hints.py
backend/adaptive/registration/postgres_dictionaries.py
backend/adaptive/registration/postgres_extensions.py
backend/adaptive/registration/postgres_registrations.py
backend/adaptive/registration/reviews.py
backend/adaptive/evaluation/reference.py
backend/adaptive/evaluation/runner.py
backend/adaptive/evaluation/validator.py
src/adaptive-prototype/MappingReviewPanel.tsx
src/adaptive-prototype/reviewFieldName.ts
src/adaptive-prototype/testQueries.ts
src/adaptive-prototype/databases.ts
tests/adaptive/test_retire_offering_year.py
tests/adaptive/test_target_grade.py
tests/adaptive/test_rule_based_mapping.py
tests/adaptive/test_postgres_registration.py
tests/adaptive/test_private_mapping_publish.py
tests/adaptive/test_evaluation_runner.py
tests/adaptive/test_variant_benchmark.py
tests/adaptive/test_legacy_view_partial_mapping.py
tests/frontend/phase6-16-databases.test.ts
docs/schema-adaptive-text-to-sql-plan.md
docs/human-validation-checkpoints.md
docs/next-session-handoff.md
docs/phase9-16-retire-offering-year-results-2026-10-13.md
```

## 9-24 전체 테스트 정리

### 분류 기준과 집계

1 = 과거 기록 버전 재현, 2 = ADR-101에 따른 현재 v3 기대 갱신, 3 = 정상 경로를 깨뜨린 회귀/충돌. 제품 runtime·HTTP 평가·레거시 기준선은 유지했다.

프롬프트의 12개 파일별 건수 합은 26이다. 마지막 전체 pytest에서 목록에 없던 `test_multi_schema_fixtures.py`의 flat/normalized/legacy 3개 subtest가 추가로 `KeyError: CourseOffering.year`를 재현했다. 이 셋을 더한 원래 v3 정리 대상 **29건은 1: 13 / 2: 9 / 3: 7**이다. 실제 13개 파일/26개 테스트 함수·파라미터 사례 + 3개 subtest로 구분한다. 원래 기대 배열·gold·fixture 정의를 바꾸지 않고 해당 테스트의 임시 bundle 생성에만 과거 v2 ontology를 지정했다.

12파일 초기 재현은 **26 failed, 193 passed, 1 skipped, 3 setup errors, 28 subtests passed**(336.39초)였다. 일부 원래 실패는 업로드 setup 500에 가려졌고 `test_fresh_init_draft_can_be_replaced_but_reviewed_one_cannot`, `test_damaged_record_is_hidden_and_does_not_affect_execution`, `test_base_version_change_between_compare_and_copy_conflicts`에서 추가 임시 업로드 오류를 관측했다. 이 실행 환경 관측은 위 29건 집계에 넣지 않는다. 후속 targeted/전체 실행의 검토 준비 500도 최종 검증 절에 별도로 기록한다.

모든 테스트 이름은 `tests/adaptive/` 기준이다. 수정 위치의 backend 경로는 `backend/adaptive/` 기준이다.

| 테스트 이름 | 분류 | 한 줄 이유 | 고친 곳/처리 |
|---|---:|---|---|
| `test_admin_test_queries.py::test_every_query_matches_seed_on_three_schemas` | 2 | v1 fixture의 year REMAP을 현재 v3 검토로 보내던 준비 코드 제거 | `test_admin_test_queries.py::_publish_fixture` |
| `test_arm_live_cli.py::test_live_run_with_oracle_transport_writes_three_reports` | 1 | 선언된 gold v1로 두 평가 arm을 재현해 120건 유지 | `evaluation/runner.py, direct_sql.py` |
| `test_arm_live_cli.py::test_no_system_format_applies_to_both_arms` | 1 | 선언된 gold v1로 두 평가 arm을 재현해 120건 유지 | `evaluation/runner.py, direct_sql.py` |
| `test_arm_live_cli.py::test_ir_extract_rule_controls_fenced_ir[single-fence-v1-120]` | 1 | 선언된 gold v1로 두 평가 arm을 재현해 120건 유지 | `evaluation/runner.py, direct_sql.py` |
| `test_capture_targets.py::test_targets_match_runner_context_and_exclude_gold` | 1 | capture의 모델 문맥도 gold의 v1 항목 집합을 사용 | `evaluation/capture_targets.py, test_capture_targets.py` |
| `test_direct_sql_arm.py::test_oracle_sql_arm_is_self_consistent_and_pairs_with_ir_oracle` | 1 | IR/SQL oracle의 gold 버전을 일치시켜 120쌍 재현 | `evaluation/runner.py, direct_sql.py` |
| `test_mapping_carry_over.py::test_same_schema_carries_every_field_and_publishes` | 2 | v3 carried 항목은 year를 뺀 14개 | `test_mapping_carry_over.py` |
| `test_mapping_carry_over.py::test_renamed_column_leaves_only_that_field_stale` | 2 | v3에서 이름 변경까지 제외하면 REMAPPED 13개 | `test_mapping_carry_over.py` |
| `test_mapping_carry_over.py::test_structure_change_marks_every_field_stale` | 2 | v3 stale 항목은 year를 뺀 15개 | `test_mapping_carry_over.py` |
| `test_mapping_carry_over.py::test_a_version_one_base_carries_over_to_a_version_two_target` | 2 | 현재 target은 v3이며 retired year를 옮기지 않음; 테스트 이름 갱신 | `test_mapping_carry_over.py` |
| `test_postgres_live_smoke.py::CleanupAndRedactionTest::test_success_cleanup_once_and_safe_summary` | 3 | smoke가 retired year 후보를 필수로 요구해 정상 경로가 실패 | `harness/postgres_live_smoke.py::SMOKE_EXPECTED_MAPPING` |
| `test_postgres_live_smoke.py::CatalogMappingQueryTest::test_mapping_top_candidates_and_no_publish` | 3 | retired year 후보 조회의 KeyError 제거 | `harness/postgres_live_smoke.py::SMOKE_EXPECTED_MAPPING` |
| `test_postgres_live_smoke.py::CatalogMappingQueryTest::test_query_mismatch_fails_closed` | 3 | 매핑 단계에서 잘못 중단되지 않아 QUERY_FAILED 검사 도달 | `harness/postgres_live_smoke.py::SMOKE_EXPECTED_MAPPING` |
| `test_raw_columns_and_select.py::test_an_unasked_missing_select_field_is_dropped_not_declined` | 2 | ADR-101의 모델 생성 year UNKNOWN_FIELD가 의도된 v3 동작; 일반 미지원 select 제거 검사는 유지 | `test_raw_columns_and_select.py` |
| `test_recorded_model.py::RecordedModelRunnerTests::test_runner_preserves_recorded_failures_missing_attempts_and_privacy` | 1 | gold v1로 녹화 mapping hash와 year 실행을 재현해 기존 37건 유지 | `evaluation/runner.py` |
| `test_schema_compare.py::test_same_schema_carries_over` | 2 | 현재 v3 field impact는 year를 뺀 14개 | `test_schema_compare.py` |
| `test_schema_compare.py::test_dropped_foreign_key_blocks_carry_over` | 2 | historical fixture의 year를 현재 검토에서 REMAP하지 않음; FK 차단 기대 유지 | `test_schema_compare.py::_publish_hybrid` |
| `test_schema_variation_suite.py::test_oracle_smoke_is_complete_on_all_five_variants` | 1 | gold v1의 extended 200건·40문항 정답/일관성을 fake transport로 재현 | `evaluation/runner.py, direct_sql.py` |
| `test_schema_variation_suite.py::test_schema_consistency_counts` | 1 | gold v1의 extended 200건·40문항 정답/일관성을 fake transport로 재현 | `evaluation/runner.py, direct_sql.py` |
| `test_schema_variation_suite.py::test_extended_live_run_with_oracle_transport[extra0-p523-ext-ir-synthetic-p519-latest.ext]` | 1 | gold v1의 extended 200건·40문항 정답/일관성을 fake transport로 재현 | `evaluation/runner.py, direct_sql.py` |
| `test_schema_variation_suite.py::test_extended_live_run_with_oracle_transport[extra1-p523-ext-nosys-fence-ir-synthetic-p519-latest.nosys.fence.ext]` | 1 | gold v1의 extended 200건·40문항 정답/일관성을 fake transport로 재현 | `evaluation/runner.py, direct_sql.py` |
| `test_synthetic_active_database.py::test_registry_lists_two_databases_strict` | 2 | 현재 catalog projection에서 year가 빠지는 것이 의도된 v3 동작 | `test_synthetic_active_database.py` |
| `test_upload_ai_proposal.py::test_ai_choice_is_first_with_rule_score_and_ai_evidence` | 3 | stale AI year key만 버리고 나머지 후보/점수/근거 유지; retired 항목 검사는 v3로 갱신 | `mapping/ai_proposal.py, test_upload_ai_proposal.py(첫 테스트의 retired 항목만)` |
| `test_upload_ai_proposal.py::test_none_and_reused_columns_keep_the_rule_candidates` | 3 | retired key 때문에 유효한 AI 전체 응답이 버려지던 회귀 | `mapping/ai_proposal.py, test_upload_ai_proposal.py(첫 테스트의 retired 항목만)` |
| `test_upload_ai_proposal.py::test_examples_reach_the_prompt_but_never_the_stored_files` | 3 | 유효한 AI 후보가 사라져 IndexError가 나던 회귀 | `mapping/ai_proposal.py, test_upload_ai_proposal.py(첫 테스트의 retired 항목만)` |
| `test_upload_ai_proposal.py::test_ai_proposal_upload_review_publish_and_catalog` | 3 | retired key만 제거해 정상 경로의 AI_UNAVAILABLE 회귀 해결 | `mapping/ai_proposal.py, test_upload_ai_proposal.py(첫 테스트의 retired 항목만)` |
| `test_multi_schema_fixtures.py::MultiSchemaFixtureTest::test_aligner_finds_all_expected_semantic_fields [variant=FLAT]` | 1 | 빠진 3개 subtest: 버전 선언 없는 과거 fixture 매핑 baseline은 v2로 생성/재현; 기존 year 기대 유지 | `test_multi_schema_fixtures.py::setUp/test_rebuild_is_repeatable (ontology 인자만)` |
| `test_multi_schema_fixtures.py::MultiSchemaFixtureTest::test_aligner_finds_all_expected_semantic_fields [variant=NORMALIZED]` | 1 | 빠진 3개 subtest: 버전 선언 없는 과거 fixture 매핑 baseline은 v2로 생성/재현; 기존 year 기대 유지 | `test_multi_schema_fixtures.py::setUp/test_rebuild_is_repeatable (ontology 인자만)` |
| `test_multi_schema_fixtures.py::MultiSchemaFixtureTest::test_aligner_finds_all_expected_semantic_fields [variant=LEGACY]` | 1 | 빠진 3개 subtest: 버전 선언 없는 과거 fixture 매핑 baseline은 v2로 생성/재현; 기존 year 기대 유지 | `test_multi_schema_fixtures.py::setUp/test_rebuild_is_repeatable (ontology 인자만)` |

추가 회귀: `test_offline_ontology_version.py`의 기록 버전/미선언 v2·fingerprint·잘못된 선언 거절·stale AI 값 제거/원본 불변/활성 schema 엄격성·학교형 benchmark의 fake adapter 검사, `test_v2_year_mapping_does_not_affect_v3_compare_or_carry_over`의 실제 v2 year 연결→year 물리 칸 이름 변경→v3 비교/이월/게시 및 base 파일 불변 검사. 기존 현재 v3 챗봇 연도 거절 테스트는 그대로 유지한다.

### 9-24에서 변경한 파일

이 목록은 작업 시작 시점의 미커밋 상태와 비교한 9-24 변경만이며, gold·fixture·녹화 파일은 없다.

```text
backend/adaptive/evaluation/ai_mapping.py
backend/adaptive/evaluation/capture_targets.py
backend/adaptive/evaluation/contracts.py
backend/adaptive/evaluation/direct_sql.py
backend/adaptive/evaluation/ontology_version.py
backend/adaptive/evaluation/runner.py
backend/adaptive/evaluation/school_mapping_check.py
backend/adaptive/evaluation/variant_benchmark.py
backend/adaptive/harness/postgres_live_smoke.py
backend/adaptive/mapping/ai_proposal.py
backend/adaptive/registration/reviews.py
docs/phase9-16-retire-offering-year-results-2026-10-13.md
docs/schema-adaptive-text-to-sql-plan.md
tests/adaptive/test_admin_test_queries.py
tests/adaptive/test_capture_targets.py
tests/adaptive/test_evaluation_runner.py
tests/adaptive/test_mapping_carry_over.py
tests/adaptive/test_multi_schema_fixtures.py
tests/adaptive/test_offline_ontology_version.py
tests/adaptive/test_raw_columns_and_select.py
tests/adaptive/test_schema_compare.py
tests/adaptive/test_synthetic_active_database.py
tests/adaptive/test_upload_ai_proposal.py
tests/adaptive/test_variant_benchmark.py
```

### 최종 검증 (2026-10-13)

- **전체 `pytest tests -q`: 2341 passed, 11 skipped, 9 failed, 608 subtests passed**, 2046.05초(34분 6초). 한 번 실행했다. 실패 9개는 뒤늦게 확인한 역사적 fixture 매핑 3 subtest와 검토 준비 단계의 `MAPPING_REVIEW_INTERNAL_ERROR` 6개였다.
- fixture 테스트는 기존 기대 배열과 데이터 정의를 유지하고 임시 bundle 생성/재생성의 ontology 인자만 v2로 지정했다. 이후 **그 전체 테스트 파일 + 위 6개 정확한 실패 사례: 10 passed, 12 subtests passed**, 67.43초. 제품 코드·기대값을 추가로 바꾸지 않은 6개 검토 준비 오류는 재실행에서 재현되지 않아 원인을 확정하지 않았다. 전체 실행 자체가 한 번에 green이었다고 보고하지 않는다. 12개 지정 파일·추가 fixture 파일·9-16/9-23 관련 backend 테스트는 전체 실행 및 정확한 실패 재실행을 합쳐 모두 통과를 확인했다.
- 전체 실행에서 재시도한 6개: `test_dictionary_import.py::test_invalid_imports_are_refused[overrides5]`, `test_mapping_version_rollback.py::test_interrupted_activation_is_repaired_by_republish`, `test_postgres_registration.py::test_register_review_publish_and_serve_without_restart`, `test_schema_compare.py::test_invalid_requests_fail_closed`, `test_value_dictionary_editor.py::test_editable_fields_and_values`, `test_value_dictionary_editor.py::test_a_too_short_alias_is_not_reported_as_a_collision`.
- 선행 focused 검증: replay·variant benchmark·smoke·AI·capture 5개 파일 **121 passed, 17 subtests passed**; 등록/비교/시험 질의/current runtime/신규 회귀 6개 파일 **70 passed, 2 temporary upload failures**(해당 사례는 뒤 전체 실행에서 통과); 독립 신규 버전/AI 회귀 파일 **17 passed**. 기존 비교 함수를 파일 수정 없이 별도 테스트 프로세스 메모리에만 로드했을 때 새 v2→v3 year 회귀는 `carry_over_executable=False`로 실패하고 수정된 함수로는 통과했다.
- 관련 frontend 7개 파일 **56 passed**; 로컬 TypeScript `tsc -b` 통과. UTF-8 strict decode와 `git diff --check` 통과.
- pytest plugin 자동 로딩/cacheprovider를 끄고, 테스트 프로세스에만 workspace의 git-ignored `.venv` 아래 `--basetemp`·`tempfile.tempdir`·그 아래 mkdir mode=0777을 적용했다. 기존 Windows 임시 파일 문제 대응으로 테스트 임시 경로 안의 실제 `os.rename` PermissionError만 최대 20회/50ms 재시도했다(전체 실행 7회, 최종 재실행 1회 관측). 최종 재실행은 더 짧은 `.venv/p924-<pid>` 경로를 사용했다. 제품 파일 권한/저장 정책은 이 문제 때문에 바꾸지 않았다. 검증 뒤 이번 작업의 basetemp·진단/보조 파일을 모두 삭제했다.
- 사용자 서버·사용자 `.schema-adaptive` 데이터·레거시 기준선·기존 미커밋 변경을 유지했다. gold·fixture 데이터/정의·녹화·gold review_status 수정, live 모델 호출, commit, `backend/.env` 값 출력 없음. fixture **테스트 소스의 ontology 인자** 변경과 원본 fixture 데이터/정의 변경을 구분한다. AGENTS.md의 Luna/opencode 독립 리뷰는 사용자 no-live-model 제약 때문에 실행하지 않았고 대기다. 사람 관문 승인 없음.
