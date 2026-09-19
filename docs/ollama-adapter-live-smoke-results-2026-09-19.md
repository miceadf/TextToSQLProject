# Ollama adapter 합성 live smoke 결과 (2026-09-19)

기준: `docs/prompts/phase5-08-ollama-adapter-live-smoke.md`. 합성 질문만 로컬 Ollama에 보냈다. 질문·모델 응답·프롬프트·HTTP body·실제 SQL·DB 행·접속 정보는 저장하거나 이 문서에 기록하지 않았다.

## 환경과 실행

- Windows 데스크톱, `.venv` Python 3.13.7, 로컬 Ollama 0.34.2. 실제 버전·모델 사용 가능 여부는 실행 직전 CLI와 `/api/tags`에서 확인했다.
- 확인된 정확한 모델 tag: `text2sql_qwen:latest`; `/api/tags` digest: `7070730bf2f29aa3e7cbb9b0b566b1c73fd93bfeab41363e4a1f128730a3cb5a`. 모델 weight의 출처·사용 권한은 확인하지 않았다.
- 명시적 `--live`, loopback base URL, timeout 30초, prompt version `query-ir-v1`. 합성 `Ontology`·`PUBLISHED MappingProfile`, 합성 질문 2건을 각 1회 호출했다. retry·fallback·DB 실행·recording 저장은 없었다.

## 안전한 형식 관찰

| case ID | provider 상태 | JSON 파싱·타입 | QueryIR 구조 | semantic field/operator | 경과 시간 |
| --- | --- | --- | --- | --- | ---: |
| `synthetic-01` | 성공 | 성공·object | 유효 | 유효 | 약 6.6초 |
| `synthetic-02` | 성공 | 성공·object | 유효 | 유효 | 약 0.3초 |

provider 실패 코드는 없었다. 이 결과는 **두 합성 입력에서 adapter의 실제 HTTP 전송과 형식 분류가 동작했다**는 뜻이다. 기존 데스크톱 smoke에서 같은 모델이 다른 프롬프트로 SQL 텍스트를 반환한 관찰과 양립한다. QueryIR 의미 정확도, 실제 데이터 질의 정확도, 40×3 점수, 학교 간 일반화나 안정적인 지연은 증명하지 않는다.

## 검증 경계와 다음 작업

- opt-in 경계, 단일 호출, JSON 문자열과 비 JSON 텍스트의 구분, 구조·semantic 오류 코드, 출력 비노출, provider 실패 후 중단을 합성 transport 테스트로 확인했다.
- 실제 응답을 기록·저장·공유하기 전 E3·L5, 모델 성능 수치를 확정하기 전 L1·L3 사람 확인이 필요하다. gold 40건은 모두 `NEEDS_HUMAN_REVIEW` 상태다.
- 다음 작은 작업은 별도 범위의 private live capture CLI·provenance 연결을 설계하고, 실제 응답을 저장하기 전에 데이터·보안 승인 범위를 확인하는 것이다. PostgreSQL 통합과 40×3 평가는 별도 단계다.
