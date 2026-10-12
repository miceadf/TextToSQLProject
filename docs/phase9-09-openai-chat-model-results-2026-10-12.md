# Phase 9-09 결과: 챗봇 모델 선택지에 ChatGPT API 추가 (2026-10-12)

지침: [phase9-09-openai-chat-model.md](prompts/phase9-09-openai-chat-model.md) · 앞 단계: [9-08](phase9-08-chat-model-choice-results-2026-10-12.md)

> 사용자 결정(2026-10-12):
> - 로컬 7B 모델만으로는 한계가 있어 ChatGPT API 선택지를 더한다. API 키는 사용자가 넣는다.
> - 기본 모델은 `gpt-5-mini`다.
> - 값 힌트도 보낸다.
> - "Legacy 기준선"(예전 레거시 챗봇)은 그대로 둔다.

## 1. 쓰는 법

1. `backend/.env`에 한 줄을 넣는다. 키는 이 파일에만 둔다. 이 파일은 Git이 추적하지 않는다.
   ```text
   OPENAI_API_KEY=sk-...
   ```
2. 다른 모델을 쓰려면 한 줄을 더 넣는다(선택).
   ```text
   OPENAI_MODEL=gpt-5
   ```
3. adaptive 서버를 다시 켠다(`npm run dev:all`).
   - 상태 줄에 `chat_model.openai=enabled`가 나오면 켜진 것이다.
   - 키 모양이 이상하면 `disabled (INVALID_CONFIG)`가 나온다. 키 값은 출력하지 않는다.
4. 챗봇 창에서 QueryIR 경로 DB(예: "Legacy DB (QueryIR)")를 고르고, "모델"에서 "ChatGPT(외부 API) · gpt-5-mini"를 고른다.
   - "Legacy 기준선"은 예전 레거시 챗봇이라 모델 고르기가 없다. 의도한 것이고, 사용자 결정으로 그대로 둔다.
5. 평가 도구에서도 고를 수 있다.
   ```bash
   python -m backend.adaptive.evaluation.blind_eval run <평가 ID> --model openai
   ```

## 2. 만든 것

- `backend/adaptive/harness/openai_chat.py`: `OpenAIQueryModel`.
  - 질문당 Chat Completions 한 번(`https://api.openai.com/v1/chat/completions`). 재시도는 없다.
  - prompt는 일반 모델과 같은 `query-ir-v3`이고, 값 힌트도 같다.
  - 9-08의 QueryIR JSON schema를 `response_format`으로 보낸다. strict는 아니다(schema의 일부 키워드를 strict가 받지 않는다).
  - `temperature`는 보내지 않는다. gpt-5 계열은 기본값만 받는다.
  - 고정 주소만 쓴다. proxy와 redirect는 따르지 않고, 응답은 1MB까지 읽는다. TLS 인증서는 Python 기본 검증을 쓴다.
  - 실패는 고정 코드만 남는다.
    - 시간 초과(60초)는 `TIMEOUT`이다.
    - HTTP 오류, 거절(refusal), 이상한 모양은 `PROVIDER_ERROR`이다.
    - 빈 답은 `EMPTY_RESPONSE`이다.
    - 화면에는 "모델 응답 시간이 초과되었습니다" 또는 "QueryIR 모델을 사용할 수 없습니다"로 보인다.
  - JSON이 아닌 글은 다른 모델처럼 "질문을 이해하지 못함"이 된다.
- 선택지 `openai`("ChatGPT(외부 API)").
  - `OPENAI_API_KEY`가 있을 때만 맨 뒤에 붙는다. 기본 모델은 그대로 일반 모델이다.
  - Ollama가 없어도 ChatGPT 선택지만으로 챗봇이 켜진다.
  - 예전 명확화 흐름(SQLite 합성 DB)은 계속 로컬 Ollama 모델을 쓴다.
- 환경 점검: `OPENAI_API_KEY`가 있는지만 보인다.
- 화면: 선택지 목록을 서버에서 받으므로 자동으로 나온다. 모델 실패 안내만 고쳤다("Ollama가 켜져 있는지, ChatGPT라면 API 키와 사용 한도를 확인").

## 3. 보내는 것과 안 보내는 것

| 보냄 | 보내지 않음 |
| --- | --- |
| 질문, 공통 항목 목록(이름·형식·연산자·이름표), 값 힌트(질문 단어와 맞는 DB 값: 학과명·교수명·이수 구분 등), QueryIR schema | 물리 table·column 이름, DSN, DB 행 데이터, API 키 외의 `.env` 값 |

- 값 힌트에는 질문에 나온 교수명 같은 레거시 DB 값이 들어갈 수 있다. 사용자가 이를 알고 보내기로 정했다.
- 데모 자료를 밖에 낼 때의 교수명 처리(L5)는 기존 규칙을 그대로 따른다.

## 4. 확인

- 서버 테스트(`tests/adaptive/test_openai_chat_model.py`)는 가짜 전송으로 본다.
  - 요청 모양(주소, 헤더, 모델, prompt v3, 값 힌트, schema, `temperature` 없음, 물리 이름 없음)
  - JSON 읽기와 실패 코드 8가지
  - 키가 오류·repr·목록·답·상태 줄 어디에도 나오지 않음
  - 고정 주소 밖은 거부
  - 키가 없거나 모양이 이상할 때의 동작
  - 화면 고르기로 고른 ChatGPT만 한 번 불림
  - 명확화 흐름은 로컬 모델
- 실제 호출은 하지 않았다. 비용이 들므로 키는 사용자가 넣고 화면에서 시험한다.

## 5. 리뷰

`gpt-6-luna` high review-only(표준 입력을 닫고 단독 실행). 지적 3건을 코드로 확인하고 모두 고쳤다. 회귀 테스트를 더했다.

1. **시작 설정에 담긴 API 키가 repr로 보였다.** 출력되는 곳은 없었지만, 키를 `_Secret`으로 감싸 repr·str에 `***`만 보이게 했다.
2. **`raise ... from None`이어도 원래 예외가 `__context__`에 남았다.** 전송 오류에 요청 내용이 실려 있으면 따라가서 볼 수 있었다. 이제 실패는 except 밖에서 새로 만들어, 원래 예외가 남지 않는다.
3. **`finish_reason`을 보지 않았다.** 잘린 답(`length`), 걸러진 답(`content_filter`), 도구 호출(`tool_calls`)도 실행될 수 있었다. 이제 `stop`만 답으로 받는다.
