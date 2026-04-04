"""
Celery tasks for CI Preflight.

run_preflight is the core task:
  1. Get a GitHub installation access token
  2. Post an "in_progress" check run so the PR shows a spinner immediately
  3. Fetch the PR diff from GitHub
  4. Parse the diff → ChangeSet
  5. Run all registered checks → List[Prediction]
  6. Build a human-readable report
  7. Update the check run with pass / fail conclusion
"""

import logging

from app.worker import celery_app
from app import github
from ci_preflight.diff_parser import from_diff_text
from ci_preflight import dependency_contract
from ci_preflight.reporter import render

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
        conclusion, title, summary, text = _build_check_output(predictions)

        github.update_check_run(token, owner, repo, check_run_id, conclusion, title, summary, text)

    except Exception as exc:
        logger.exception("run_preflight failed for PR #%s on %s", pr_number, repo_full_name)

        # Mark the check as failed so the PR isn't left with a dangling spinner
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
                pass  # best-effort; don't mask the original exception

        raise self.retry(exc=exc)
