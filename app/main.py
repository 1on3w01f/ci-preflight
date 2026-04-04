"""
CI Preflight — GitHub App webhook server.

Receives GitHub PR webhook events, enqueues a Celery task,
and lets the worker post the check run result back to GitHub.
"""

import hashlib
import hmac
import json
import logging
import os

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from app.tasks import run_preflight

logger = logging.getLogger("ci_preflight.app")

app = FastAPI(title="CI Preflight", version="0.1.0")

WEBHOOK_SECRET = os.environ.get("GITHUB_WEBHOOK_SECRET", "")


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok"}


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
        action = payload.get("action", "")
        if action in ("opened", "synchronize", "reopened"):
            installation_id = payload["installation"]["id"]
            pr_number = payload["pull_request"]["number"]
            head_sha = payload["pull_request"]["head"]["sha"]
            repo_full_name = payload["repository"]["full_name"]

            logger.info(
                "PR #%s on %s (install=%s) — queuing preflight check",
                pr_number, repo_full_name, installation_id,
            )

            run_preflight.delay(installation_id, repo_full_name, pr_number, head_sha)

            return JSONResponse({"queued": True, "pr": pr_number})

    # Acknowledge all other events without processing
    return JSONResponse({"skipped": True, "event": event})


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------

def _verify_signature(body: bytes, signature_header: str) -> None:
    """Validate GitHub's HMAC-SHA256 webhook signature."""
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
