"""
CI Preflight — GitHub App webhook server.

Handles:
  - pull_request events       → enqueue preflight check
  - check_suite.completed     → record actual CI outcome (for accuracy tracking)
  - installation events       → record/remove installs in DB
  - installation_repositories → track repo additions/removals
"""

import hashlib
import hmac
import json
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func

from app.database import init_db, SessionLocal
from app.models import Installation, Repository, PredictionRecord, CIOutcome
from app.tasks import run_preflight
from app.ado_routes import router as ado_router

logger = logging.getLogger("ci_preflight.app")

WEBHOOK_SECRET = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
GITHUB_APP_ID = os.environ.get("GITHUB_APP_ID", "")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info("Database tables ready")
    yield


app = FastAPI(title="CI Preflight", version="0.1.0", lifespan=lifespan)
app.include_router(ado_router)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Installs overview
# ---------------------------------------------------------------------------

@app.get("/installs")
async def list_installs():
    with SessionLocal() as db:
        installs = db.query(Installation).all()
        return [
            {
                "id": i.id,
                "account": i.account_login,
                "type": i.account_type,
                "repos": [r.full_name for r in i.repositories],
                "installed_at": i.installed_at.isoformat(),
            }
            for i in installs
        ]


# ---------------------------------------------------------------------------
# Accuracy stats
# ---------------------------------------------------------------------------

@app.get("/stats")
async def stats():
    """
    Join predictions to CI outcomes to show true/false positive rates per check type.
    A prediction is a true positive when CI failed on that commit.
    A prediction is a false positive when CI passed on that commit.
    """
    with SessionLocal() as db:
        rows = (
            db.query(
                PredictionRecord.check_type,
                PredictionRecord.severity,
                CIOutcome.conclusion,
                func.count().label("count"),
            )
            .join(
                CIOutcome,
                (CIOutcome.repo_full_name == PredictionRecord.repo_full_name)
                & (CIOutcome.head_sha == PredictionRecord.head_sha),
            )
            .group_by(
                PredictionRecord.check_type,
                PredictionRecord.severity,
                CIOutcome.conclusion,
            )
            .all()
        )

        total_predictions = db.query(func.count(PredictionRecord.id)).scalar()
        total_outcomes = db.query(func.count(CIOutcome.id)).scalar()
        matched = db.query(func.count(PredictionRecord.id)).join(
            CIOutcome,
            (CIOutcome.repo_full_name == PredictionRecord.repo_full_name)
            & (CIOutcome.head_sha == PredictionRecord.head_sha),
        ).scalar()

        breakdown = {}
        for check_type, severity, conclusion, count in rows:
            key = f"{check_type} ({severity})"
            if key not in breakdown:
                breakdown[key] = {"true_positives": 0, "false_positives": 0, "other": 0}
            if conclusion == "failure":
                breakdown[key]["true_positives"] += count
            elif conclusion == "success":
                breakdown[key]["false_positives"] += count
            else:
                breakdown[key]["other"] += count

        # Add accuracy % per check type
        for key, counts in breakdown.items():
            tp = counts["true_positives"]
            fp = counts["false_positives"]
            total = tp + fp
            counts["accuracy"] = f"{round(tp / total * 100)}%" if total > 0 else "n/a"

        return {
            "total_predictions": total_predictions,
            "total_outcomes_recorded": total_outcomes,
            "matched": matched,
            "breakdown": breakdown,
        }


# ---------------------------------------------------------------------------
# Webhook receiver
# ---------------------------------------------------------------------------

@app.post("/webhook")
async def github_webhook(
    request: Request,
    x_github_event: str = Header(default=""),
    x_hub_signature_256: str = Header(default=""),
):
    body = await request.body()
    _verify_signature(body, x_hub_signature_256)

    event = x_github_event
    payload = json.loads(body)

    if event == "pull_request":
        return _handle_pull_request(payload)

    if event == "check_suite":
        return _handle_check_suite(payload)

    if event == "installation":
        return _handle_installation(payload)

    if event == "installation_repositories":
        return _handle_installation_repositories(payload)

    return JSONResponse({"skipped": True, "event": event})


# ---------------------------------------------------------------------------
# Event handlers
# ---------------------------------------------------------------------------

def _handle_pull_request(payload: dict) -> JSONResponse:
    action = payload.get("action", "")
    if action not in ("opened", "synchronize", "reopened"):
        return JSONResponse({"skipped": True, "action": action})

    installation_id = payload["installation"]["id"]
    pr_number = payload["pull_request"]["number"]
    head_sha = payload["pull_request"]["head"]["sha"]
    repo_full_name = payload["repository"]["full_name"]

    logger.info("PR #%s on %s — queuing preflight check", pr_number, repo_full_name)
    run_preflight.delay(installation_id, repo_full_name, pr_number, head_sha)

    return JSONResponse({"queued": True, "pr": pr_number})


def _handle_check_suite(payload: dict) -> JSONResponse:
    """
    Record the actual CI outcome for a commit.
    Ignores check suites from CI Preflight itself (would be circular).
    Only records completed suites with a definitive conclusion.
    """
    action = payload.get("action", "")
    if action != "completed":
        return JSONResponse({"skipped": True, "action": action})

    suite = payload["check_suite"]
    conclusion = suite.get("conclusion")
    if conclusion not in ("success", "failure", "timed_out", "cancelled"):
        return JSONResponse({"skipped": True, "conclusion": conclusion})

    # Skip our own check suite to avoid circular labeling
    suite_app_id = str(suite.get("app", {}).get("id", ""))
    if suite_app_id == GITHUB_APP_ID:
        return JSONResponse({"skipped": True, "reason": "own check suite"})

    head_sha = suite["head_sha"]
    repo_full_name = payload["repository"]["full_name"]
    ci_app_name = suite.get("app", {}).get("name", "unknown")

    with SessionLocal() as db:
        # One outcome per (repo, sha) — first CI system to complete wins
        existing = db.query(CIOutcome).filter_by(
            repo_full_name=repo_full_name,
            head_sha=head_sha,
        ).first()

        if not existing:
            db.add(CIOutcome(
                repo_full_name=repo_full_name,
                head_sha=head_sha,
                conclusion=conclusion,
                ci_app_name=ci_app_name,
            ))
            db.commit()
            logger.info(
                "CI outcome recorded: %s %s → %s (via %s)",
                repo_full_name, head_sha[:7], conclusion, ci_app_name,
            )

    return JSONResponse({"recorded": True, "conclusion": conclusion})


def _handle_installation(payload: dict) -> JSONResponse:
    action = payload.get("action", "")
    install_data = payload["installation"]
    installation_id = install_data["id"]
    account = install_data["account"]

    with SessionLocal() as db:
        if action == "created":
            install = Installation(
                id=installation_id,
                account_login=account["login"],
                account_type=account["type"],
            )
            db.add(install)

            for repo in payload.get("repositories", []):
                db.add(Repository(
                    id=repo["id"],
                    full_name=repo["full_name"],
                    installation_id=installation_id,
                ))

            db.commit()
            logger.info(
                "App installed by %s (%s) — %d repo(s)",
                account["login"], account["type"], len(payload.get("repositories", [])),
            )
            return JSONResponse({"recorded": True, "account": account["login"]})

        if action == "deleted":
            install = db.query(Installation).filter_by(id=installation_id).first()
            if install:
                db.delete(install)
                db.commit()
            logger.info("App uninstalled by %s", account["login"])
            return JSONResponse({"removed": True, "account": account["login"]})

    return JSONResponse({"skipped": True, "action": action})


def _handle_installation_repositories(payload: dict) -> JSONResponse:
    action = payload.get("action", "")
    installation_id = payload["installation"]["id"]

    with SessionLocal() as db:
        if action == "added":
            for repo in payload.get("repositories_added", []):
                existing = db.query(Repository).filter_by(id=repo["id"]).first()
                if not existing:
                    db.add(Repository(
                        id=repo["id"],
                        full_name=repo["full_name"],
                        installation_id=installation_id,
                    ))
            db.commit()
            added = [r["full_name"] for r in payload.get("repositories_added", [])]
            logger.info("Repos added to install %s: %s", installation_id, added)
            return JSONResponse({"added": added})

        if action == "removed":
            for repo in payload.get("repositories_removed", []):
                db.query(Repository).filter_by(id=repo["id"]).delete()
            db.commit()
            removed = [r["full_name"] for r in payload.get("repositories_removed", [])]
            logger.info("Repos removed from install %s: %s", installation_id, removed)
            return JSONResponse({"removed": removed})

    return JSONResponse({"skipped": True, "action": action})


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------

def _verify_signature(body: bytes, signature_header: str) -> None:
    if not WEBHOOK_SECRET:
        logger.warning("GITHUB_WEBHOOK_SECRET not set — skipping signature check")
        return

    if not signature_header.startswith("sha256="):
        raise HTTPException(status_code=401, detail="Missing or malformed signature")

    expected = "sha256=" + hmac.new(
        WEBHOOK_SECRET.encode(), body, hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected, signature_header):
        raise HTTPException(status_code=401, detail="Invalid signature")
