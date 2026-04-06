"""
Azure DevOps API client.

Handles:
  - PAT-based authentication
  - Fetching PR changed files via the Iterations API
  - Posting and updating PR statuses (the ADO equivalent of GitHub Check Runs)

ADO API reference: https://learn.microsoft.com/en-us/rest/api/azure/devops/git/
"""

import base64
import logging
from typing import List

import httpx

logger = logging.getLogger("ci_preflight.ado")

ADO_API_VERSION = "7.0"


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def _auth_header(pat: str) -> dict:
    """
    ADO uses HTTP Basic auth with a PAT.
    Username is empty; the PAT is the password.
    """
    token = base64.b64encode(f":{pat}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _base_url(org: str) -> str:
    return f"https://dev.azure.com/{org}"


# ---------------------------------------------------------------------------
# PR changed files
# ---------------------------------------------------------------------------

def get_pr_changed_files(
    org: str,
    project: str,
    repo_id: str,
    pr_id: int,
    pat: str,
) -> List[str]:
    """
    Returns the list of files changed in a PR using the Iterations API.

    ADO tracks PR changes per "iteration" (each push to the PR branch).
    We fetch the latest iteration and return all changed file paths.
    """
    headers = {**_auth_header(pat), "Content-Type": "application/json"}
    base = _base_url(org)

    # Step 1: get the latest iteration ID
    iterations_url = (
        f"{base}/{project}/_apis/git/repositories/{repo_id}"
        f"/pullRequests/{pr_id}/iterations?api-version={ADO_API_VERSION}"
    )
    with httpx.Client() as client:
        resp = client.get(iterations_url, headers=headers)
        resp.raise_for_status()
        iterations = resp.json().get("value", [])

    if not iterations:
        logger.warning("No iterations found for PR #%s in %s/%s", pr_id, org, project)
        return []

    latest_iteration = iterations[-1]["id"]

    # Step 2: get changed files for that iteration
    changes_url = (
        f"{base}/{project}/_apis/git/repositories/{repo_id}"
        f"/pullRequests/{pr_id}/iterations/{latest_iteration}"
        f"/changes?api-version={ADO_API_VERSION}"
    )
    with httpx.Client() as client:
        resp = client.get(changes_url, headers=headers)
        resp.raise_for_status()
        entries = resp.json().get("changeEntries", [])

    # Paths come back as "/src/app.py" — strip the leading slash
    files = [
        entry["item"]["path"].lstrip("/")
        for entry in entries
        if "item" in entry and "path" in entry["item"]
    ]

    logger.info(
        "PR #%s in %s/%s — %d file(s) changed (iteration %s)",
        pr_id, org, project, len(files), latest_iteration,
    )
    return files


# ---------------------------------------------------------------------------
# PR status (ADO equivalent of GitHub Check Runs)
# ---------------------------------------------------------------------------

# ADO PR status states
PENDING  = "pending"
SUCCEEDED = "succeeded"
FAILED   = "failed"
ERROR    = "error"


def post_pr_status(
    org: str,
    project: str,
    repo_id: str,
    pr_id: int,
    pat: str,
    state: str,
    description: str,
    target_url: str = "",
) -> None:
    """
    Post or update a status on an ADO pull request.

    state: "pending" | "succeeded" | "failed" | "error"
    description: short message shown on the PR (max ~140 chars)
    target_url: optional link to detailed report
    """
    url = (
        f"{_base_url(org)}/{project}/_apis/git/repositories/{repo_id}"
        f"/pullRequests/{pr_id}/statuses?api-version={ADO_API_VERSION}"
    )
    headers = {**_auth_header(pat), "Content-Type": "application/json"}

    body = {
        "state": state,
        "description": description,
        "context": {
            "name": "ci-preflight",
            "genre": "continuous-integration",
        },
    }
    if target_url:
        body["targetUrl"] = target_url

    with httpx.Client() as client:
        resp = client.post(url, headers=headers, json=body)
        resp.raise_for_status()

    logger.info("ADO PR #%s status → %s (%s)", pr_id, state, description)
