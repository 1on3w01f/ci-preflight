"""
Submodule drift check.

When .gitmodules is modified, the submodule configuration has changed — a new
submodule was added, a URL was updated, or a submodule was removed. CI pipelines
that run `git submodule update --init --recursive` will fail if:
  - The new submodule URL is inaccessible from the CI runner
  - The referenced commit no longer exists at the new URL
  - The submodule was removed from .gitmodules but the directory wasn't cleaned up

Causal mechanism:
  .gitmodules changed → CI does `git submodule update --init`
  → submodule at new URL/commit may not be reachable → pipeline fails at checkout
"""

from typing import List
from ci_preflight.models import ChangeSet, Signal, Prediction


def check(changeset: ChangeSet) -> List[Prediction]:
    gitmodules_changed = any(
        f == ".gitmodules" or f.endswith("/.gitmodules")
        for f in changeset.changed_files
    )
    if not gitmodules_changed:
        return []

    signals = [
        Signal(
            id="gitmodules_modified",
            description=(
                ".gitmodules was modified — a submodule URL, path, or branch has changed, "
                "or a submodule was added or removed."
            ),
        ),
        Signal(
            id="ci_submodule_checkout_risk",
            description=(
                "CI pipelines running `git submodule update --init --recursive` will attempt "
                "to checkout the submodule at the new URL and commit. If the URL is "
                "inaccessible from the CI runner or the commit doesn't exist, the pipeline "
                "will fail at the repository checkout stage — before any build step runs."
            ),
        ),
    ]

    return [
        Prediction(
            failure_type="submodule_checkout_failure",
            violated_contract="submodule_sync_contract",
            signals=signals,
            confidence=0.72,
            impact_stage="checkout",
            recommendation=(
                "1. Verify the submodule URL is accessible from your CI runner "
                "(check network/firewall rules and SSH key access).\n"
                "2. Confirm the referenced commit exists in the submodule repo: "
                "`git submodule status` should show no `+` or `-` prefixes.\n"
                "3. Run `git submodule update --init --recursive` locally before pushing "
                "to confirm the checkout succeeds."
            ),
        )
    ]
