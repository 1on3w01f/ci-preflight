"""
CI config change check.

When the pipeline definition itself is modified, there is a high probability
the run will fail — syntax errors, missing variables, changed stage logic, etc.
This check flags any direct edit to a recognised CI configuration file.
"""

from typing import List
from ci_preflight.models import ChangeSet, Signal, Prediction

# File patterns that indicate a CI/CD pipeline definition was touched.
# Matched by exact filename or suffix so subdirectory paths are handled.
CI_CONFIG_PATTERNS = [
    # Azure DevOps
    ".azure-pipelines.yml",
    "azure-pipelines.yml",
    # GitHub Actions
    ".github/workflows/",
    # GitLab CI
    ".gitlab-ci.yml",
    # Bitbucket Pipelines
    "bitbucket-pipelines.yml",
    # CircleCI
    ".circleci/config.yml",
    # Jenkins
    "Jenkinsfile",
    # Generic
    "pipeline.yml",
    "pipeline.yaml",
]


def _is_ci_config(filename: str) -> bool:
    for pattern in CI_CONFIG_PATTERNS:
        if filename == pattern or filename.endswith("/" + pattern) or filename.startswith(pattern):
            return True
    return False


def check(changeset: ChangeSet) -> List[Prediction]:
    triggered = [f for f in changeset.changed_files if _is_ci_config(f)]
    if not triggered:
        return []

    signals = [
        Signal(
            id="ci_config_modified",
            description=f"Pipeline definition modified: {', '.join(triggered)}",
        ),
        Signal(
            id="self_referential_risk",
            description=(
                "Editing the CI config means the pipeline will run the untested version "
                "of itself — syntax errors or misconfigured steps will cause immediate failure."
            ),
        ),
    ]

    return [
        Prediction(
            failure_type="pipeline_definition_error",
            violated_contract="ci_config_stability_contract",
            signals=signals,
            confidence=0.80,
            impact_stage="pipeline_startup",
            recommendation=(
                "Review the pipeline YAML carefully before merging. "
                "Validate syntax locally with `az pipelines run --dry-run` (ADO) "
                "or the GitHub Actions linter. "
                "Consider testing pipeline changes on a dedicated branch first."
            ),
        )
    ]
