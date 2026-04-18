"""
Merge conflict markers check.

Detects unresolved Git merge conflict markers left in the diff. When a developer
resolves a merge conflict incorrectly — or forgets to resolve it entirely — the
conflict markers (<<<<<<<, =======, >>>>>>>) remain in the source files and are
committed as literal text.

Causal mechanism:
  Conflict markers in source → syntax error in nearly every language
  → compiler/interpreter/linter fails immediately → CI fails at build or lint stage.
  This is one of the highest-confidence failure signals: if the markers are there,
  the build will fail.

Detection: scans added lines in the unified diff for `<<<<<<< ` and `>>>>>>> `
markers. Populated by from_diff_text() in diff_parser.py.
Note: from_file_list() and from_git() do not populate this field — the check
only fires when the full diff text is available (GitHub App path).
"""

from typing import List
from ci_preflight.models import ChangeSet, Signal, Prediction


def check(changeset: ChangeSet) -> List[Prediction]:
    if not changeset.has_conflict_markers:
        return []

    signals = [
        Signal(
            id="conflict_markers_in_diff",
            description=(
                "Unresolved Git merge conflict markers (<<<<<<<, =======, >>>>>>>) "
                "were found in the added lines of this PR's diff."
            ),
        ),
        Signal(
            id="syntax_failure_certain",
            description=(
                "Conflict markers are not valid syntax in any programming language. "
                "The build will fail immediately at compile, parse, or lint stage — "
                "before any tests run."
            ),
        ),
    ]

    return [
        Prediction(
            failure_type="unresolved_merge_conflict",
            violated_contract="clean_merge_contract",
            signals=signals,
            confidence=0.97,
            impact_stage="build",
            recommendation=(
                "Search the diff for `<<<<<<<` and resolve all conflict markers before pushing.\n"
                "Run `git diff --check` locally to find all remaining conflict markers.\n"
                "Most editors highlight unresolved conflicts — check for any files "
                "still showing merge conflict decorations."
            ),
        )
    ]
