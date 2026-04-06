"""
Celery tasks for CI Preflight.

run_preflight        — GitHub: token → check run spinner → diff → engine → check run result
run_preflight_ado    — ADO:    PAT → PR status pending → changed files → engine → PR status result

Both tasks share the same engine (_run_checks) and persistence (_save_predictions).
"""

import logging
import os

from app.worker import celery_app
from app import github, ado
from app.database import SessionLocal
from app.models import PredictionRecord
from ci_preflight.diff_parser import from_file_list
from ci_preflight.diff_parser import from_diff_text
from ci_preflight import dependency_contract
from ci_preflight.reporter import render

ADO_PAT = os.environ.get("ADO_PAT", "")

logger = logging.getLogger("ci_preflight.tasks")


def _run_checks(changeset):
    predictions = []
    predictions.extend(dependency_contract.check(changeset))
    # Add more checks here as they are built
    return predictions


def _build_check_output(predictions):
    """
    Returns (conclusion, title, summary, text) for the GitHub Check Run output.
    """
    if not predictions:
        return (
            "success",
            "All clear — no predicted failures",
            "CI Preflight found no contract violations in this PR.",
            "",
        )

    high = [p for p in predictions if p.severity() == "HIGH"]
    conclusion = "failure" if high else "neutral"

    count = len(predictions)
    title = f"{count} predicted failure{'s' if count > 1 else ''} found"
    summary = (
        f"CI Preflight detected **{count}** risk{'s' if count > 1 else ''}. "
        f"{'**{} HIGH severity** — merge blocked.'.format(len(high)) if high else 'No HIGH severity issues.'}"
    )
    text = render(predictions)

    return conclusion, title, summary, text


def _save_predictions(installation_id, repo_full_name, pr_number, head_sha, predictions):
    """Persist predictions to DB so they can be matched against CI outcomes later."""
    if not predictions:
        return
    with SessionLocal() as db:
        for p in predictions:
            db.add(PredictionRecord(
                installation_id=installation_id,
                repo_full_name=repo_full_name,
                pr_number=pr_number,
                head_sha=head_sha,
                check_type=p.violated_contract,
                failure_type=p.failure_type,
                confidence=p.confidence,
                severity=p.severity(),
            ))
        db.commit()
    logger.info("Saved %d prediction(s) for %s#%s", len(predictions), repo_full_name, pr_number)


@celery_app.task(
    bind=True,
    name="ci_preflight.run_preflight",
    max_retries=2,
    default_retry_delay=30,
)
def run_preflight(self, installation_id: int, repo_full_name: str, pr_number: int, head_sha: str):
    """
    Run CI Preflight checks on a PR and post the result as a GitHub Check Run.
    """
    owner, repo = repo_full_name.split("/", 1)
    check_run_id = None

    try:
        token = github.get_installation_token(installation_id)

        # Post "in_progress" immediately so the PR shows a spinner
        check_run_id = github.create_check_run(token, owner, repo, head_sha)

        diff = github.get_pr_diff(token, owner, repo, pr_number)
        changeset = from_diff_text(diff)

        logger.info(
            "PR #%s on %s — %d file(s) in diff",
            pr_number, repo_full_name, len(changeset.changed_files),
        )

        predictions = _run_checks(changeset)

        # Persist predictions before posting — data is valuable even if posting fails
        _save_predictions(installation_id, repo_full_name, pr_number, head_sha, predictions)

        conclusion, title, summary, text = _build_check_output(predictions)
        github.update_check_run(token, owner, repo, check_run_id, conclusion, title, summary, text)

    except Exception as exc:
        logger.exception("run_preflight failed for PR #%s on %s", pr_number, repo_full_name)

        if check_run_id:
            try:
                token = github.get_installation_token(installation_id)
                github.update_check_run(
                    token, owner, repo, check_run_id,
                    conclusion="failure",
                    title="CI Preflight encountered an error",
                    summary=f"An internal error occurred: `{exc}`",
                )
            except Exception:
                pass

        raise self.retry(exc=exc)


@celery_app.task(
    bind=True,
    name="ci_preflight.run_preflight_ado",
    max_retries=2,
    default_retry_delay=30,
)
def run_preflight_ado(
    self,
    org: str,
    project: str,
    repo_id: str,
    repo_name: str,
    pr_id: int,
    head_sha: str,
):
    """
    Run CI Preflight checks on an ADO pull request and post the result as a PR status.
    """
    repo_full_name = f"{org}/{project}/{repo_name}"
    pat = ADO_PAT

    if not pat:
        logger.error("ADO_PAT not configured — cannot process ADO PR #%s", pr_id)
        return

    try:
        # Post "pending" status immediately so the PR shows CI is running
        ado.post_pr_status(
            org, project, repo_id, pr_id, pat,
            state=ado.PENDING,
            description="CI Preflight is analysing your changes...",
        )

        # Fetch changed files via ADO Iterations API
        changed_files = ado.get_pr_changed_files(org, project, repo_id, pr_id, pat)
        changeset = from_file_list(changed_files)

        logger.info(
            "ADO PR #%s on %s — %d file(s) changed",
            pr_id, repo_full_name, len(changed_files),
        )

        predictions = _run_checks(changeset)

        # Persist predictions — use pr_id as pr_number, installation_id=0 for ADO
        _save_predictions(0, repo_full_name, pr_id, head_sha, predictions)

        # Post final status
        if not predictions:
            ado.post_pr_status(
                org, project, repo_id, pr_id, pat,
                state=ado.SUCCEEDED,
                description="CI Preflight: all clear — no contract violations found.",
            )
        else:
            high = [p for p in predictions if p.severity() == "HIGH"]
            count = len(predictions)
            description = (
                f"CI Preflight: {count} risk(s) found"
                + (f" — {len(high)} HIGH severity, merge blocked." if high else ".")
            )
            ado.post_pr_status(
                org, project, repo_id, pr_id, pat,
                state=ado.FAILED if high else ado.SUCCEEDED,
                description=description,
            )

    except Exception as exc:
        logger.exception("run_preflight_ado failed for ADO PR #%s on %s", pr_id, repo_full_name)

        try:
            ado.post_pr_status(
                org, project, repo_id, pr_id, pat,
                state=ado.ERROR,
                description=f"CI Preflight encountered an error: {exc}",
            )
        except Exception:
            pass

        raise self.retry(exc=exc)
