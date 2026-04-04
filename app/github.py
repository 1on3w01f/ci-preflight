"""
GitHub App client.

Handles:
  - JWT generation (signed with the App's private key)
  - Installation access token exchange
  - Fetching PR diffs
  - Creating and updating GitHub Check Runs
"""

import time
import os
import logging
from pathlib import Path

import httpx
import jwt as pyjwt

logger = logging.getLogger("ci_preflight.github")

GITHUB_API = "https://api.github.com"


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def generate_jwt() -> str:
    """
    Generate a short-lived JWT signed with the GitHub App's private key.
    Valid for 60 seconds — enough to exchange for an installation token.
    """
    app_id = os.environ["GITHUB_APP_ID"]
    key_path = os.environ.get("GITHUB_PRIVATE_KEY_PATH", "/secrets/github-app.pem")
    private_key = Path(key_path).read_text()

    now = int(time.time())
    payload = {
        "iat": now - 60,   # issued 60 s ago (clock skew tolerance)
        "exp": now + 600,  # expires in 10 min
        "iss": app_id,
    }
    return pyjwt.encode(payload, private_key, algorithm="RS256")


def get_installation_token(installation_id: int) -> str:
    """
    Exchange a GitHub App JWT for a short-lived installation access token.
    Installation tokens are valid for 1 hour.
    """
    app_jwt = generate_jwt()
    url = f"{GITHUB_API}/app/installations/{installation_id}/access_tokens"

    with httpx.Client() as client:
        resp = client.post(
            url,
            headers={
                "Authorization": f"Bearer {app_jwt}",
                "Accept": "application/vnd.github+json",
            },
        )
        resp.raise_for_status()
        return resp.json()["token"]


# ---------------------------------------------------------------------------
# PR diff
# ---------------------------------------------------------------------------

def get_pr_diff(token: str, owner: str, repo: str, pr_number: int) -> str:
    """
    Fetch the unified diff for a pull request.
    GitHub returns the diff when Accept: application/vnd.github.v3.diff is set.
    """
    url = f"{GITHUB_API}/repos/{owner}/{repo}/pulls/{pr_number}"

    with httpx.Client() as client:
        resp = client.get(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github.v3.diff",
            },
        )
        resp.raise_for_status()
        return resp.text


# ---------------------------------------------------------------------------
# Check Runs
# ---------------------------------------------------------------------------

def create_check_run(
    token: str,
    owner: str,
    repo: str,
    head_sha: str,
    name: str = "CI Preflight",
) -> int:
    """
    Create an in-progress check run on the commit.
    Returns the check run ID needed to update it later.
    """
    url = f"{GITHUB_API}/repos/{owner}/{repo}/check-runs"

    with httpx.Client() as client:
        resp = client.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
            json={
                "name": name,
                "head_sha": head_sha,
                "status": "in_progress",
            },
        )
        resp.raise_for_status()
        check_run_id = resp.json()["id"]
        logger.info("Created check run %s on %s/%s @ %s", check_run_id, owner, repo, head_sha[:7])
        return check_run_id


def update_check_run(
    token: str,
    owner: str,
    repo: str,
    check_run_id: int,
    conclusion: str,
    title: str,
    summary: str,
    text: str = "",
    name: str = "CI Preflight",
) -> None:
    """
    Update a check run with the final result.

    conclusion: "success" | "failure" | "neutral" | "cancelled" | "skipped"
    """
    url = f"{GITHUB_API}/repos/{owner}/{repo}/check-runs/{check_run_id}"

    with httpx.Client() as client:
        resp = client.patch(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
            json={
                "name": name,
                "status": "completed",
                "conclusion": conclusion,
                "output": {
                    "title": title,
                    "summary": summary,
                    "text": text,
                },
            },
        )
        resp.raise_for_status()
        logger.info(
            "Updated check run %s → %s (%s)",
            check_run_id, conclusion, title,
        )
