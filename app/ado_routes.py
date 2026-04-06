"""
Azure DevOps webhook router.

ADO webhooks are configured in:
  Project Settings → Service Hooks → Web Hooks

Subscribe to:
  - git.pullrequest.created
  - git.pullrequest.updated
  - build.complete  (for CI outcome tracking)

Security: set a basic auth username/password in the ADO service hook config.
Set ADO_WEBHOOK_USER and ADO_WEBHOOK_PASSWORD in your environment to validate them.
"""

import base64
import logging
import os

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from app.database import SessionLocal
from app.models import CIOutcome
from app.tasks import run_preflight_ado

logger = logging.getLogger("ci_preflight.ado_routes")

router = APIRouter(prefix="/webhook/ado", tags=["ado"])

ADO_WEBHOOK_USER = os.environ.get("ADO_WEBHOOK_USER", "")
ADO_WEBHOOK_PASSWORD = os.environ.get("ADO_WEBHOOK_PASSWORD", "")


# ---------------------------------------------------------------------------
# Webhook receiver
# ---------------------------------------------------------------------------

@router.post("")
async def ado_webhook(
    request: Request,
    authorization: str = Header(default=""),
):
    _verify_basic_auth(authorization)

    payload = await request.json()
    event_type = payload.get("eventType", "")

    if event_type in ("git.pullrequest.created", "git.pullrequest.updated"):
        return _handle_pr_event(payload)

    if event_type == "build.complete":
        return _handle_build_complete(payload)

    return JSONResponse({"skipped": True, "eventType": event_type})


# ---------------------------------------------------------------------------
# Event handlers
# ---------------------------------------------------------------------------

def _handle_pr_event(payload: dict) -> JSONResponse:
    """
    Triggered when a PR is created or updated (new push to the source branch).
    Extracts PR context and enqueues the preflight Celery task.
    """
    resource = payload["resource"]
    pr_id = resource["pullRequestId"]
    head_sha = resource["lastMergeSourceCommit"]["commitId"]
    repo = resource["repository"]
    repo_id = repo["id"]
    repo_name = repo["name"]
    project = repo["project"]["name"]

    # Extract org from the account base URL: "https://dev.azure.com/myorg/"
    org = (
        payload.get("resourceContainers", {})
        .get("account", {})
        .get("baseUrl", "")
        .rstrip("/")
        .split("/")[-1]
    )

    if not org:
        logger.error("Could not determine ADO org from webhook payload")
        raise HTTPException(status_code=400, detail="Could not determine ADO organization")

    repo_full_name = f"{org}/{project}/{repo_name}"

    logger.info(
        "ADO PR #%s on %s — queuing preflight check",
        pr_id, repo_full_name,
    )

    run_preflight_ado.delay(org, project, repo_id, repo_name, pr_id, head_sha)

    return JSONResponse({"queued": True, "pr": pr_id, "repo": repo_full_name})


def _handle_build_complete(payload: dict) -> JSONResponse:
    """
    Capture the CI outcome from an ADO pipeline run.
    Matches to stored predictions via head_sha (sourceVersion).
    """
    resource = payload["resource"]
    result = resource.get("result", "")
    head_sha = resource.get("sourceVersion", "")
    repo_name = resource.get("repository", {}).get("name", "unknown")
    pipeline_name = resource.get("definition", {}).get("name", "unknown")

    org = (
        payload.get("resourceContainers", {})
        .get("account", {})
        .get("baseUrl", "")
        .rstrip("/")
        .split("/")[-1]
    )
    project = (
        payload.get("resourceContainers", {})
        .get("project", {})
        .get("name", "unknown")
    )

    repo_full_name = f"{org}/{project}/{repo_name}"

    # Normalise ADO result → our conclusion vocabulary
    conclusion_map = {
        "succeeded": "success",
        "failed": "failure",
        "canceled": "cancelled",
        "partiallySucceeded": "failure",
    }
    conclusion = conclusion_map.get(result)

    if not conclusion or not head_sha:
        return JSONResponse({"skipped": True, "result": result})

    with SessionLocal() as db:
        existing = db.query(CIOutcome).filter_by(
            repo_full_name=repo_full_name,
            head_sha=head_sha,
        ).first()

        if not existing:
            db.add(CIOutcome(
                repo_full_name=repo_full_name,
                head_sha=head_sha,
                conclusion=conclusion,
                ci_app_name=f"ADO/{pipeline_name}",
            ))
            db.commit()
            logger.info(
                "ADO CI outcome recorded: %s %s → %s (pipeline: %s)",
                repo_full_name, head_sha[:7], conclusion, pipeline_name,
            )

    return JSONResponse({"recorded": True, "conclusion": conclusion})


# ---------------------------------------------------------------------------
# Basic auth validation
# ---------------------------------------------------------------------------

def _verify_basic_auth(authorization: str) -> None:
    """
    Validates the Basic auth credentials ADO sends with each webhook.
    If ADO_WEBHOOK_USER / ADO_WEBHOOK_PASSWORD are not set, skips validation (dev mode).
    """
    if not ADO_WEBHOOK_USER or not ADO_WEBHOOK_PASSWORD:
        logger.warning("ADO_WEBHOOK_USER/PASSWORD not set — skipping auth check")
        return

    if not authorization.startswith("Basic "):
        raise HTTPException(status_code=401, detail="Missing Basic auth credentials")

    try:
        decoded = base64.b64decode(authorization[6:]).decode()
        user, password = decoded.split(":", 1)
    except Exception:
        raise HTTPException(status_code=401, detail="Malformed Basic auth header")

    if user != ADO_WEBHOOK_USER or password != ADO_WEBHOOK_PASSWORD:
        raise HTTPException(status_code=401, detail="Invalid credentials")
