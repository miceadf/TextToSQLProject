# Phase 5-19 구현 지침: 로컬 모델 IR·직접 SQL arm live paired 비교(잠정)

## 목표

Phase 5-18 계약으로 같은 로컬 모델 tag를 IR arm(`query-ir-v1`)과 SQL arm(`direct-sql-v1`)에 각각 연결해 개발 세트 40×3을 한 번씩 실행하고 paired count를 **잠정 관찰값**으로 기록한다. gold 40건이 `NEEDS_HUMAN_REVIEW`이므로 확정 성능·우열 주장이 아니다(L1/L3 전).

## 범위

1. 신규 `backend/adaptive/evaluation/arm_live_cli.py`와 테스트 `tests/adaptive/test_arm_live_cli.py`.
2. 메인이 로컬 Ollama로 `--check` 후 `--live-synthetic` 1회 실행, 결과 문서 `docs/phase5-19-live-arm-comparison-results-2026-09-28.md`.

기존 runner·Phase 5-18 모듈·runtime은 변경하지 않는다.

## CLI 계약

```text
python -m backend.adaptive.evaluation.arm_live_cli (--check | --live-synthetic)
    --ollama-model-tag TAG [--ollama-base-url URL] [--ollama-timeout SECONDS]
    [--repository-root PATH] --output-name NAME
```

- mode는 정확히 하나. Ollama 설정은 Phase 7-08 `parse_ollama_startup_config`로 검증하고 환경변수를 읽지 않는다. parser는 Phase 7-08과 같은 strict parser(반복·축약·unknown 거부, 입력 비반사).
- gold는 코드에 고정된 개발 세트 `fixture_v1_cases.yaml`만 쓴다. gold 경로 option은 없으며 holdout은 실행할 수 없다.
- 두 arm 모두 같은 config(tag·URL·timeout), temperature 0, attempt당 1회, retry 없음. IR arm은 `build_startup_ollama_model`, SQL arm은 `OllamaDirectSqlModel`.
- `model_label`은 tag의 `:`를 `-`로 바꾼 값, `model_run_id`는 `p519-ir-<label>`·`p519-sql-<label>`(패턴 불일치 시 설정 오류).
- 출력: `<repository-root>/.schema-adaptive/reports/<NAME>/`에 `ir-report.json`, `sql-report.json`, `comparison.json`. NAME은 `[a-z0-9][a-z0-9-]{0,63}`, 디렉터리가 이미 있으면 거부(no-overwrite). `.schema-adaptive`·`reports`·출력 디렉터리가 symlink이거나 저장소 밖으로 해석되면 거부한다. 세 파일은 같은 부모의 staging 디렉터리에 쓴 뒤 한 번의 rename으로 게시하고, 실패 시 staging을 지워 부분 결과를 남기지 않는다. report에는 5-18 계약대로 원문이 없다. mode flag도 반복을 거부한다.
- stdout에는 한 줄 JSON 요약만: 각 arm totals(IR: execution_success·semantic_correct, SQL: valid_sql·execution_success·execution_correct·abstain), paired totals와 variant별 count, 경고. 경로·URL은 출력하지 않는다.
- `--check`: 설정·gold·fixture bundle 준비만 하고 모델 호출 0회. fixture는 OS 임시 디렉터리에 만들고 정리하며 저장소·출력 경로 쓰기는 0회다(fixture 생성 가능 여부 확인이 check의 목적).
- 실패: 설정 오류 `{"status":"CONFIG_ERROR"}` exit 2, 그 외 `{"status":"RUN_ERROR"}` exit 1. 원문 예외 없음.

## 테스트 (synthetic transport만)

- `--check` 호출 0회·파일 0개.
- 두 arm에 synthetic transport 주입(IR은 gold IR, SQL은 oracle SQL 반환) 시 세 report가 생성되고 paired both_correct 120, stdout 한 줄.
- 기존 output 디렉터리·잘못된 NAME·mode 오류·잘못된 Ollama 설정은 고정 오류, 호출 0회.
- report·stdout에 질문·SQL·URL 비포함.

## live 실행

- 로컬 `/api/tags`의 정확한 tag, 기본 loopback URL, timeout 60초. 결과가 나빠도 재시도·prompt 조정 없이 그대로 기록한다.
- 결과 문서에는 환경(Ollama version, tag, digest), 두 arm의 지표, paired count, 해석 한계만 적는다.

## 사람 관문

완전 합성 fixture·질문만 사용한다(E3·L5 대상 없음). 수치는 L1(gold 검토)·L3(결과 검토) 전 잠정치이며 논문의 확정 성능이나 우열 결론으로 쓰지 않는다.
