# Phase 9-09 지침: 챗봇 모델 선택지에 ChatGPT API 추가

## 배경

- 9-08에서 일반 모델(`qwen2.5-coder:7b-instruct`)은 레거시 질문에서 미세 조정 모델과 비슷했다. 다른 학교의 학과 약어 질문에서는 약했다.
- 2026-10-12 사용자 결정: 로컬 7B 모델만으로는 한계가 있다. ChatGPT API 선택지를 더한다. API 키는 사용자가 넣는다.
- 같은 날 사용자 결정:
  - 기본 모델은 `gpt-5-mini`다. `OPENAI_MODEL`로 바꿀 수 있다.
  - 값 힌트(질문 단어와 맞는 DB 값)도 보낸다. 다른 모델과 같은 조건으로 비교하기 위해서다.
  - "Legacy 기준선"(예전 레거시 챗봇)은 그대로 둔다. 모델 고르기는 QueryIR 경로에만 있다.

## 고칠 것

1. **선택지 `openai`("ChatGPT(외부 API)").**
   - `backend/.env`에 `OPENAI_API_KEY`가 있을 때만 생긴다.
   - 모델은 `OPENAI_MODEL`이고, 없으면 `gpt-5-mini`다.
2. **모델 호출.**
   - `https://api.openai.com/v1/chat/completions`로 질문당 한 번 부른다.
   - prompt는 일반 모델과 같은 `query-ir-v3`이고, 값 힌트도 같다.
   - QueryIR JSON schema를 `response_format`으로 보낸다(strict 아님).
   - 고정 주소만 쓴다. proxy와 redirect는 따르지 않고, 응답 크기에 상한을 둔다.
   - 실패는 고정 코드(`TIMEOUT`, `PROVIDER_ERROR`)로만 알린다.
   - 이후 QueryIR 검증·compiler·policy·executor는 그대로다.
3. **비밀 유지.**
   - API 키는 서버 안에서만 쓴다.
   - 화면, 상태 줄, 오류, 로그, 평가 기록에 나가지 않는다.
   - 환경 점검은 "있음"만 보인다.
4. **보내는 것.**
   - 보내는 것: 질문, 공통 항목 목록(이름·형식·연산자), 값 힌트.
   - 보내지 않는 것: 물리 table·column 이름, DSN.
5. 화면은 바꾸지 않는다. 선택지 목록을 서버에서 받으므로 자동으로 보인다.

## 확인

1. 가짜 전송으로 서버 테스트를 한다.
   - 요청 모양(주소, 헤더, 모델, schema, 힌트)
   - JSON 읽기
   - 실패 코드
   - 키가 어디에도 나오지 않음
   - 키 없을 때 선택지 없음
   - 화면 고르기로 고른 모델만 한 번 불림
2. 실제 호출은 사용자가 키를 넣은 뒤 화면에서 한다. 비용이 들므로 AI가 키를 넣거나 측정을 돌리지 않는다.
3. Luna review-only, 전체 pytest, compileall, diff check를 돌린다.
4. ADR-099와 문서를 갱신한다.
