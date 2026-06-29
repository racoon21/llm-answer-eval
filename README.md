# LLM 답변 평가 리더보드

사용자가 **사번**과 **답변 CSV**를 업로드하면, 관리자가 사전에 정의한 채점 기준
(`admin/eval-rule.md`)과 모범답안(`admin/reference_answers.json`)에 근거해 **3명의 LLM
전문가가 독립적으로 채점**하고 그 **평균 점수**를 산출합니다. 점수는 SQLite에 저장되고
**실시간 리더보드**로 표시됩니다.

- 인당 **최대 4회** 제출, 리더보드에는 **최고 점수** 반영
- 채점 LLM은 **OpenAI / Azure OpenAI 호환** 인터페이스 (환경변수로 전환)

## 빠른 시작

```bash
python -m venv .venv && source .venv/bin/activate   # (선택)
pip install -r requirements.txt

cp .env.example .env        # 키 입력 (OpenAI 또는 Azure)
uvicorn app.main:app --reload
```

브라우저에서 http://localhost:8000 접속 → 사번 입력 → `sample_submission.csv` 업로드 → 채점.

## LLM 공급자 설정

`.env` 에서 분기됩니다 (`app/llm_client.py`).

- **OpenAI**: `OPENAI_API_KEY` + `LLM_MODEL=gpt-4o-mini`
  (OpenAI 호환 게이트웨이는 `OPENAI_BASE_URL` 지정)
- **Azure OpenAI**: `AZURE_OPENAI_ENDPOINT` + `AZURE_OPENAI_API_KEY`
  + `AZURE_OPENAI_API_VERSION` 설정 시 자동으로 Azure 클라이언트 사용.
  이때 `LLM_MODEL` 에는 **배포(deployment) 이름**을 입력.

`AZURE_OPENAI_ENDPOINT` 가 설정되어 있으면 Azure, 아니면 OpenAI 클라이언트가 선택됩니다.
호출부는 동일한 `chat.completions.create` 인터페이스만 사용하므로 벤더 교체 시 코드 수정이
필요 없습니다.

## 관리자 가이드

채점 대상과 기준은 두 파일로 관리합니다.

| 파일 | 역할 |
|------|------|
| `admin/eval-rule.md` | 채점 기준·점수 척도(0–100). 매 문항마다 3명의 심사위원에게 전문 전달 |
| `admin/reference_answers.json` | `{"문제번호": "모범답안", ...}`. 사용자 답변과 대비해 채점 |

문제번호 키는 `"1"`, `"01"`, `1` 모두 동일하게 정규화됩니다. 모범답안이 없는 문제번호는
0점 처리되고 경고가 표시됩니다.

## 사용자 제출 CSV 형식

헤더 있는 형식(권장):

```csv
problem_no,answer
1,사용자의 1번 답변...
2,사용자의 2번 답변...
```

헤더가 없으면 **1열=문제번호, 2열=답변** 으로 간주합니다. (`problem_no`/`answer` 외에
`번호`/`답변`, `question`/`response` 등의 별칭도 인식)

## 채점 방식

- 문항마다 서로 다른 관점의 **3명(`JUDGE_COUNT`) 심사위원**이 병렬 채점 → **평균**
- 제출 총점 = 전 문항 점수의 평균(0–100)
- 일부 심사위원 실패 시 성공한 심사위원 평균 사용, 전부 실패 시 에러
- 동시성은 `MAX_CONCURRENCY` 세마포어로 제한 (레이트리밋 보호)

## API

| 메서드 | 경로 | 설명 |
|--------|------|------|
| POST | `/api/submit` | `employee_id`, `display_name?`, `file`(CSV) 제출·채점 |
| GET | `/api/leaderboard` | 사번별 최고점 랭킹 |
| GET | `/api/me/{employee_id}` | 현재 사용자 요약(최고점/순위/제출 횟수) |
| GET | `/api/config` | 프론트엔드용 설정값 |

## 데이터 저장

SQLite `data/scores.db` 의 `submissions` 테이블에 제출별 총점과 문항·심사위원별 상세
(`detail_json`)가 저장됩니다. `data/` 와 `.env` 는 git 에 커밋되지 않습니다.
