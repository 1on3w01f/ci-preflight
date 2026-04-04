from typing import List
from ci_preflight.models import ChangeSet, Signal, Prediction

# Contract: every dependency manifest must be accompanied by its lockfile when changed.
# Violating this means the pipeline may resolve different versions than expected.

DEPENDENCY_PAIRS = [
    {
        "manifest": "package.json",
        "lockfile": "package-lock.json",
        "ecosystem": "Node",
        "fix": "Run `npm install` to regenerate package-lock.json before pushing."
    },
    {
        "manifest": "yarn.lock",
        "lockfile": "yarn.lock",
        "ecosystem": "Yarn",
        "fix": "Run `yarn install` to update yarn.lock before pushing."
    },
    {
        "manifest": "go.mod",
        "lockfile": "go.sum",
        "ecosystem": "Go",
        "fix": "Run `go mod tidy` to update go.sum before pushing."
    },
    {
        "manifest": "requirements.txt",
        "lockfile": "requirements.lock",
        "ecosystem": "Python (pip-compile)",
        "fix": "Run `pip-compile` to regenerate requirements.lock before pushing."
    },
    {
        "manifest": "Pipfile",
        "lockfile": "Pipfile.lock",
        "ecosystem": "Python (Pipenv)",
        "fix": "Run `pipenv lock` to update Pipfile.lock before pushing."
    },
]


def check(changeset: ChangeSet) -> List[Prediction]:
    predictions = []

    for pair in DEPENDENCY_PAIRS:
        manifest = pair["manifest"]
        lockfile = pair["lockfile"]

        manifest_changed = changeset.has_file(manifest)
        lockfile_changed = changeset.has_file(lockfile)

        if manifest_changed and not lockfile_changed:
            signals = [
                Signal(
                    id="dependency_manifest_modified",
                    description=f"{manifest} was modified"
                ),
                Signal(
                    id="lockfile_not_updated",
                    description=f"{lockfile} was not updated alongside {manifest}"
                ),
            ]

            predictions.append(
                Prediction(
                    failure_type="dependency_resolution_failure",
                    violated_contract="dependency_lock_contract",
                    signals=signals,
                    confidence=0.85,
                    impact_stage="build",
                    recommendation=pair["fix"]
                )
            )

    return predictions
