"""Scoring engine.

Three independent LLM judges score each answer against the admin-defined rules
(``eval-rule.md``) and reference answer (``reference_answers.json``). Per-item
score is the mean of the judges; the submission total is the mean across items.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import re
from dataclasses import dataclass, field
from typing import Optional

from .config import (
    EVAL_RULE_PATH,
    REFERENCE_ANSWERS_PATH,
    settings,
)
from .llm_client import get_client, get_model_name

# Distinct evaluator personas give the judges genuinely different perspectives
# instead of relying on sampling temperature for variation.
JUDGE_PERSONAS = [
    "You are a strict, detail-oriented grader who rewards precision, correct "
    "terminology, and completeness, and penalizes vague or partially correct answers.",
    "You are a pragmatic grader who focuses on whether the answer demonstrates "
    "correct understanding and would be acceptable to a domain expert, tolerating "
    "minor wording differences.",
    "You are a holistic grader who weighs clarity, structure, and reasoning "
    "alongside correctness, judging the overall quality of the response.",
]


class ScoringError(RuntimeError):
    """Raised when a submission cannot be scored at all."""


@dataclass
class JudgeResult:
    judge: int
    score: Optional[float]  # None if this judge failed
    rationale: str = ""
    error: Optional[str] = None


@dataclass
class ItemResult:
    problem_no: str
    user_answer: str
    matched_reference: bool
    score: float
    judges: list[JudgeResult] = field(default_factory=list)
    warning: Optional[str] = None


@dataclass
class SubmissionResult:
    total_score: float
    items: list[ItemResult]
    warnings: list[str]


# --------------------------------------------------------------------------- #
# CSV parsing
# --------------------------------------------------------------------------- #

_PROBLEM_KEYS = {"problem_no", "problem", "problem_number", "question_no",
                 "question", "no", "id", "문제번호", "번호"}
_ANSWER_KEYS = {"answer", "response", "user_answer", "답변", "답안"}


def _normalize_problem_no(value: str) -> str:
    """Normalize '1', '1.0', 'Q1', ' 01 ' -> a clean string key."""
    value = (value or "").strip()
    m = re.search(r"\d+", value)
    if m:
        return str(int(m.group()))  # drop leading zeros / decimals
    return value


def parse_submission_csv(raw: bytes) -> list[tuple[str, str]]:
    """Parse uploaded CSV into [(problem_no, answer), ...].

    Accepts headered CSV (problem_no/answer columns, several aliases) or a
    simple two-column CSV without a header. Raises ScoringError if unusable.
    """
    text = raw.decode("utf-8-sig", errors="replace")
    rows = list(csv.reader(io.StringIO(text)))
    rows = [r for r in rows if any(c.strip() for c in r)]  # drop blank rows
    if not rows:
        raise ScoringError("CSV가 비어 있습니다.")

    header = [c.strip().lower() for c in rows[0]]
    has_header = any(h in _PROBLEM_KEYS for h in header) and any(
        h in _ANSWER_KEYS for h in header
    )

    pairs: list[tuple[str, str]] = []
    if has_header:
        p_idx = next(i for i, h in enumerate(header) if h in _PROBLEM_KEYS)
        a_idx = next(i for i, h in enumerate(header) if h in _ANSWER_KEYS)
        data_rows = rows[1:]
    else:
        # Assume first column = problem number, second = answer.
        p_idx, a_idx = 0, 1
        data_rows = rows

    for r in data_rows:
        if len(r) <= max(p_idx, a_idx):
            continue
        problem_no = _normalize_problem_no(r[p_idx])
        # Answer is the last logical column. If an unquoted answer contained
        # commas, csv split it into extra cells — rejoin everything from the
        # answer column onward so commas survive.
        answer = ",".join(r[a_idx:]).strip()
        if not problem_no:
            continue
        pairs.append((problem_no, answer))

    if not pairs:
        raise ScoringError(
            "CSV에서 (문제번호, 답변)을 읽지 못했습니다. "
            "헤더를 'problem_no,answer' 로 하거나 두 개의 열로 작성하세요."
        )
    return pairs


# --------------------------------------------------------------------------- #
# Admin data loading
# --------------------------------------------------------------------------- #

def load_eval_rule() -> str:
    if not EVAL_RULE_PATH.exists():
        raise ScoringError(f"채점 기준 파일이 없습니다: {EVAL_RULE_PATH}")
    return EVAL_RULE_PATH.read_text(encoding="utf-8")


def load_reference_answers() -> dict[str, str]:
    if not REFERENCE_ANSWERS_PATH.exists():
        raise ScoringError(f"모범답안 파일이 없습니다: {REFERENCE_ANSWERS_PATH}")
    data = json.loads(REFERENCE_ANSWERS_PATH.read_text(encoding="utf-8"))
    # Normalize keys so '01', '1', 1 all match.
    return {_normalize_problem_no(str(k)): str(v) for k, v in data.items()}


# --------------------------------------------------------------------------- #
# LLM judging
# --------------------------------------------------------------------------- #

def _build_messages(
    persona: str, eval_rule: str, problem_no: str,
    reference: str, user_answer: str,
) -> list[dict]:
    system = (
        f"{persona}\n\n"
        "You grade a user's answer against the official grading rules and the "
        "reference (model) answer. Return ONLY a JSON object of the form "
        '{\"score\": <number 0-100>, \"rationale\": \"<short reason>\"}. '
        "The score is an integer or decimal from 0 (completely wrong) to 100 "
        "(fully correct per the rules). Be objective and consistent."
    )
    user = (
        "## Grading rules (eval-rule.md)\n"
        f"{eval_rule}\n\n"
        f"## Problem number\n{problem_no}\n\n"
        "## Reference (model) answer\n"
        f"{reference}\n\n"
        "## User's answer\n"
        f"{user_answer if user_answer else '(빈 답변)'}\n\n"
        "Score the user's answer now. Respond with the JSON object only."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _clamp_score(value) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"점수를 숫자로 해석할 수 없습니다: {value!r}")
    return max(0.0, min(100.0, score))


async def _run_judge(
    judge_index: int, messages: list[dict], sem: asyncio.Semaphore,
) -> JudgeResult:
    client = get_client()
    model = get_model_name()
    last_err: Optional[str] = None

    for attempt in range(settings.judge_max_retries):
        try:
            async with sem:
                resp = await client.chat.completions.create(
                    model=model,
                    messages=messages,
                    response_format={"type": "json_object"},
                )
            content = resp.choices[0].message.content or "{}"
            data = json.loads(content)
            score = _clamp_score(data.get("score"))
            rationale = str(data.get("rationale", "")).strip()
            return JudgeResult(judge=judge_index, score=score, rationale=rationale)
        except Exception as exc:  # noqa: BLE001 - surface as judge-level failure
            last_err = f"{type(exc).__name__}: {exc}"
            if attempt < settings.judge_max_retries - 1:
                await asyncio.sleep(2 ** attempt)  # 1s, 2s, 4s ...

    return JudgeResult(judge=judge_index, score=None, error=last_err)


async def _score_item(
    problem_no: str, user_answer: str, eval_rule: str,
    references: dict[str, str], sem: asyncio.Semaphore,
) -> ItemResult:
    reference = references.get(problem_no)
    if reference is None:
        return ItemResult(
            problem_no=problem_no,
            user_answer=user_answer,
            matched_reference=False,
            score=0.0,
            warning=f"문제 {problem_no}: 모범답안이 없어 0점 처리되었습니다.",
        )

    judge_count = max(1, settings.judge_count)
    tasks = []
    for i in range(judge_count):
        persona = JUDGE_PERSONAS[i % len(JUDGE_PERSONAS)]
        messages = _build_messages(persona, eval_rule, problem_no, reference, user_answer)
        tasks.append(_run_judge(i + 1, messages, sem))
    judges = await asyncio.gather(*tasks)

    valid = [j.score for j in judges if j.score is not None]
    warning = None
    if not valid:
        raise ScoringError(
            f"문제 {problem_no}: 모든 심사위원 채점에 실패했습니다 "
            f"(마지막 오류: {judges[-1].error})."
        )
    if len(valid) < judge_count:
        warning = (
            f"문제 {problem_no}: {judge_count}명 중 {len(valid)}명만 채점에 "
            "성공하여 해당 평균을 사용했습니다."
        )

    item_score = sum(valid) / len(valid)
    return ItemResult(
        problem_no=problem_no,
        user_answer=user_answer,
        matched_reference=True,
        score=item_score,
        judges=judges,
        warning=warning,
    )


async def score_submission(pairs: list[tuple[str, str]]) -> SubmissionResult:
    """Score all (problem_no, answer) pairs and return the aggregate result."""
    eval_rule = load_eval_rule()
    references = load_reference_answers()
    sem = asyncio.Semaphore(max(1, settings.max_concurrency))

    item_tasks = [
        _score_item(pn, ans, eval_rule, references, sem) for pn, ans in pairs
    ]
    items = await asyncio.gather(*item_tasks)

    total = sum(it.score for it in items) / len(items) if items else 0.0
    warnings = [it.warning for it in items if it.warning]
    return SubmissionResult(total_score=total, items=items, warnings=warnings)


def result_to_detail(result: SubmissionResult) -> dict:
    """Serialize a SubmissionResult for storage in detail_json."""
    return {
        "total_score": round(result.total_score, 2),
        "judge_count": settings.judge_count,
        "model": get_model_name(),
        "items": [
            {
                "problem_no": it.problem_no,
                "matched_reference": it.matched_reference,
                "score": round(it.score, 2),
                "judges": [
                    {
                        "judge": j.judge,
                        "score": j.score,
                        "rationale": j.rationale,
                        "error": j.error,
                    }
                    for j in it.judges
                ],
                "warning": it.warning,
            }
            for it in result.items
        ],
        "warnings": result.warnings,
    }
