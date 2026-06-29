"""FastAPI application: submission, scoring, and leaderboard endpoints."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .config import STATIC_DIR, settings
from .scoring import (
    ScoringError,
    parse_submission_csv,
    result_to_detail,
    score_submission,
)

app = FastAPI(title="LLM Answer Evaluation Leaderboard")


@app.on_event("startup")
def _startup() -> None:
    db.init_db()


@app.get("/api/config")
def get_config() -> dict:
    return {
        "max_submissions": settings.max_submissions,
        "judge_count": settings.judge_count,
        "poll_seconds": settings.leaderboard_poll_seconds,
    }


@app.get("/api/leaderboard")
def leaderboard() -> dict:
    return {"leaderboard": db.get_leaderboard()}


@app.get("/api/me/{employee_id}")
def me(employee_id: str) -> dict:
    summary = db.get_user_summary(employee_id.strip())
    return {"summary": summary}


@app.post("/api/submit")
async def submit(
    employee_id: str = Form(...),
    display_name: str = Form(""),
    file: UploadFile = File(...),
) -> JSONResponse:
    employee_id = employee_id.strip()
    if not employee_id:
        raise HTTPException(status_code=400, detail="사번을 입력하세요.")

    display_name = display_name.strip() or employee_id

    # Enforce per-employee submission cap.
    used = db.count_submissions(employee_id)
    if used >= settings.max_submissions:
        raise HTTPException(
            status_code=400,
            detail=(
                f"제출 한도({settings.max_submissions}회)를 초과했습니다. "
                f"({employee_id} 님은 이미 {used}회 제출)"
            ),
        )

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="업로드된 파일이 비어 있습니다.")

    try:
        pairs = parse_submission_csv(raw)
        result = await score_submission(pairs)
    except ScoringError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"채점 중 오류: {exc}")

    submission_index = used + 1
    created_at = datetime.now(timezone.utc).isoformat()
    detail = result_to_detail(result)

    db.insert_submission(
        employee_id=employee_id,
        display_name=display_name,
        submission_index=submission_index,
        total_score=result.total_score,
        detail=detail,
        created_at=created_at,
    )

    summary = db.get_user_summary(employee_id)
    return JSONResponse(
        {
            "employee_id": employee_id,
            "display_name": display_name,
            "submission_score": round(result.total_score, 1),
            "submission_index": submission_index,
            "remaining_submissions": settings.max_submissions - submission_index,
            "best_score": summary["score"] if summary else round(result.total_score, 1),
            "rank": summary["rank"] if summary else None,
            "items": [
                {
                    "problem_no": it.problem_no,
                    "score": round(it.score, 1),
                    "matched_reference": it.matched_reference,
                }
                for it in result.items
            ],
            "warnings": result.warnings,
        }
    )


# --- Static frontend (mounted last so /api/* takes precedence) ---------------
@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
