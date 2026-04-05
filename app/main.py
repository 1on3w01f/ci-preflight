"""
CI Preflight — GitHub App webhook server.

Handles:
  - pull_request events → enqueue preflight check
  - installation events → record/remove installs in DB
  - installation_repositories events → track repo additions/removals
"""

import hashlib
import hmac
import json
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from app.database import init_db, get_db, SessionLocal
from app.models import Installation, Repository
from app.tasks import run_preflight

logger = logging.getLogger("ci_preflight.app")

WEBHOOK_SECRET = os.environ.get("GITHUB_WEBHOOK_SECRET", "")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info("Database tables ready")
    yield


app = FastAPI(title="CI Preflight", version="0.1.0", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Installs overview (simple debug endpoint — lock this down before public launch)
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

            # Store any repos included at install time
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
