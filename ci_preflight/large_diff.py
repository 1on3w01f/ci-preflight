"""
Large diff check.

PRs or commits that touch a very large number of files are statistically more
likely to cause CI failures: more surface area means more opportunities for
merge conflicts, broken imports, and test regressions.

Thresholds are tunable via the constants below.
"""

from typing import List
from ci_preflight.models import ChangeSet, Signal, Prediction

# Number of changed files that triggers each severity band.
THRESHOLD_MEDIUM = 50   # MEDIUM confidence
THRESHOLD_HIGH = 200    # HIGH confidence


def check(changeset: ChangeSet) -> List[Prediction]:
    count = len(changeset.changed_files)

    if count < THRESHOLD_MEDIUM:
        return []

    if count >= THRESHOLD_HIGH:
        confidence = 0.80
        severity_label = "very large"
    else:
        confidence = 0.60
        severity_label = "large"

    signals = [
        Signal(
            id="large_diff_detected",
            description=f"{count} files changed in this commit — {severity_label} diff",
        ),
        Signal(
            id="blast_radius_risk",
            description=(
                f"Diffs of this size have a higher rate of unexpected breakage: "
                f"merge conflicts, broken cross-file dependencies, and test failures "
                f"are all more likely when {count} files are touched at once."
            ),
        ),
    ]

    return [
        Prediction(
            failure_type="large_diff_instability",
            violated_contract="diff_size_contract",
            signals=signals,
            confidence=confidence,
            impact_stage="build_or_test",
            recommendation=(
                f"Consider splitting this change into smaller, focused commits or PRs. "
                f"Confirm all {count} changed files are intentional — mass-edits from "
                "auto-formatters or code-generation tools are common sources of noise."
            ),
        )
    ]
