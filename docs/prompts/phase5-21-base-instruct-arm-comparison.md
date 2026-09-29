# Phase 5-21 실행 지침: base instruct 모델 IR·직접 SQL arm paired 비교(잠정)

## 목표

Phase 5-20은 CNU 단일 schema로 fine-tune된 `text2sql_qwen:latest`만 비교해, QueryIR 경로의 이점이 방식 자체 때문인지 fine-tune 편향 때문인지 분리할 수 없었다(§15.1-3). 같은 계열 base instruct 모델로 같은 두 arm을 실행해 이를 분리하는 첫 관찰을 기록한다.

## 조건

- 모델: `qwen2.5-coder:7b-instruct`(Ollama 공식 레지스트리, Q4_K_M, 사용자 승인, 저장 경로 `C:/Users/micea/.ollama/models`).
- 등록 TEMPLATE은 ChatML이고 기본 SYSTEM은 일반 문구(`You are Qwen, created by Alibaba Cloud. You are a helpful assistant.`)다. CNU schema 같은 도메인 내용은 없다.
- 요청 형식: 사용자 결정에 따라 `model-template`(공식 기본 system 포함, base 모델의 자연스러운 사용 조건). fine-tune 모델의 5-20 조건(`alpaca-raw-no-system-v1`, system 없음)과 system 조건이 다르다는 점을 결과에 명시한다.
- 나머지는 5-19/5-20과 동일: 개발 세트 40×3, `query-ir-v1`·`direct-sql-v1`, temperature 0, timeout 60초, 호출 1회·retry 없음, holdout 미실행.

## 실행

코드 변경 없이 기존 CLI를 사용한다.

```text
python -m backend.adaptive.evaluation.arm_live_cli --check|--live-synthetic \
    --ollama-model-tag qwen2.5-coder:7b-instruct --ollama-timeout 60 \
    --ollama-request-format model-template --output-name <name>
```

결과가 나빠도 prompt·추출·guard를 조정하지 않는다. 실패 구성은 원문 없는 구조 분류로만 진단한다.

## 기록

결과 문서 `docs/phase5-21-base-instruct-arm-comparison-results-2026-09-29.md`에 환경(버전·tag·digest·TEMPLATE/SYSTEM 요약), 두 arm 지표, paired count, 5-20 fine-tune 결과와의 대조, 해석 한계를 적는다. 수치는 L1/L3 전 잠정치다.
