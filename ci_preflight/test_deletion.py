"""
Test file deletion check.

When test files are deleted from a PR, CI pipelines with coverage thresholds
will fail — total coverage drops as test code is removed but the threshold
stays fixed. Even without coverage gates, deleting tests silently reduces the
safety net for the affected module.

Causal mechanism:
  Test files deleted → total test count drops → coverage % falls
  → coverage threshold check fails → CI reports failure at test stage.

Detection uses the deleted_files field populated by from_diff_text().
This check only fires on the GitHub App path where full diff text is available.
It does not fire for file-list-only changesets (ADO path, seed script).

Frameworks / naming conventions covered:
  pytest         test_*.py, *_test.py
  Jest / Vitest  *.test.js/ts/jsx/tsx, *.spec.js/ts/jsx/tsx
  Go             *_test.go
  JUnit          *Test.java, *Tests.java
  RSpec          *_spec.rb
  .NET xUnit     *Tests.cs, *Test.cs
  Mocha          test/*.js, test/*.ts
"""

from typing import List
from ci_preflight.models import ChangeSet, Signal, Prediction

TEST_SUFFIXES = (
    # Python
    "_test.py",
    # Jest / Vitest
    ".test.js", ".test.ts", ".test.jsx", ".test.tsx",
    ".spec.js", ".spec.ts", ".spec.jsx", ".spec.tsx",
    # Go
    "_test.go",
    # JUnit
    "Test.java", "Tests.java",
    # RSpec
    "_spec.rb",
    # .NET
    "Tests.cs", "Test.cs",
)

TEST_PREFIXES = (
    "test_",   # pytest
)


def _is_test_file(filename: str) -> bool:
    basename = filename.split("/")[-1]
    if any(basename.endswith(s) for s in TEST_SUFFIXES):
        return True
    if any(basename.startswith(p) for p in TEST_PREFIXES):
        return True
    return False


def check(changeset: ChangeSet) -> List[Prediction]:
    deleted_tests = [f for f in changeset.deleted_files if _is_test_file(f)]
    if not deleted_tests:
        return []

    count = len(deleted_tests)
    noun = "file" if count == 1 else "files"

    signals = [
        Signal(
            id="test_files_deleted",
            description=(
                f"{count} test {noun} deleted in this PR: "
                f"{', '.join(deleted_tests[:5])}"
                + (" and more." if count > 5 else ".")
            ),
        ),
        Signal(
            id="coverage_threshold_risk",
            description=(
                "Deleting test files reduces total test count and line coverage. "
                "Repositories with a minimum coverage threshold configured in CI "
                "will fail the coverage gate — even if all remaining tests pass."
            ),
        ),
    ]

    return [
        Prediction(
            failure_type="coverage_threshold_failure",
            violated_contract="test_coverage_contract",
            signals=signals,
            confidence=0.68,
            impact_stage="test",
            recommendation=(
                f"Verify that deleting {count} test {noun} is intentional:\n"
                "1. If the corresponding source was also deleted — confirm the test "
                "deletion is part of the same cleanup and coverage will not drop.\n"
                "2. If the source still exists — confirm why its tests are being removed "
                "and whether coverage thresholds will still be met.\n"
                "3. Run your test suite locally with coverage reporting to check the "
                "impact before pushing: `pytest --cov` / `jest --coverage` / `go test -cover ./...`"
            ),
        )
    ]
