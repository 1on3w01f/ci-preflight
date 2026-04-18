"""
Secrets committed check.

Detects when files that are likely to contain credentials, private keys, or
secrets are included in a pull request diff. CI pipelines with secret scanning
enabled will fail immediately; even without scanning, committing secrets is a
critical security incident that requires credential rotation.

Causal mechanism:
  Secret file in diff → secret scanner (GitHub Advanced Security, truffleHog,
  detect-secrets) triggers → pipeline blocked or failed at the scanning stage.
  Even without a scanner: credentials are now in git history permanently.

Detection strategy: filename pattern matching only (no content analysis).
This avoids false negatives from obfuscated filenames but may miss secrets
embedded in non-secret-looking files. Content analysis is a future enhancement.
"""

from typing import List
from ci_preflight.models import ChangeSet, Signal, Prediction

# Exact filenames that should never appear in a diff
EXACT_BLOCKED = {
    ".env",
    ".env.local",
    ".env.production",
    ".env.staging",
    ".env.development",
    "credentials.json",         # Google service account / OAuth
    "service_account.json",
    "secrets.json",
    "secrets.yaml",
    "secrets.yml",
    "id_rsa",
    "id_ed25519",
    "id_ecdsa",
    "id_dsa",
    ".netrc",
    "htpasswd",
    ".htpasswd",
    "kubeconfig",
    "terraform.tfvars",         # often contains secrets
    "terraform.tfvars.json",
}

# Suffixes that indicate private key or credential material
BLOCKED_SUFFIXES = (
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".jks",
    ".keystore",
    ".cer",
    ".crt",
    ".der",
    "-secrets.yaml",
    "-secrets.yml",
    ".secret",
)

# Path fragments — any file under these directories is flagged
BLOCKED_PATH_FRAGMENTS = (
    "/.ssh/",
    "/secrets/",
    "/private/",
)


def _is_secret_file(filename: str) -> bool:
    # Check exact filename (basename match)
    basename = filename.split("/")[-1]
    if basename in EXACT_BLOCKED:
        return True

    # Check suffix
    if any(filename.endswith(s) for s in BLOCKED_SUFFIXES):
        return True

    # Check path fragments
    if any(frag in filename for frag in BLOCKED_PATH_FRAGMENTS):
        return True

    return False


def check(changeset: ChangeSet) -> List[Prediction]:
    flagged = [f for f in changeset.changed_files if _is_secret_file(f)]
    if not flagged:
        return []

    signals = [
        Signal(
            id="secret_file_in_diff",
            description=f"Potential secret or credential file(s) detected in this PR: {', '.join(flagged)}",
        ),
        Signal(
            id="permanent_exposure_risk",
            description=(
                "Once committed, secrets are in git history permanently — even if the file "
                "is removed in a follow-up commit. Secret scanners (GitHub Advanced Security, "
                "truffleHog) will flag this and may block the pipeline. The credentials "
                "must be treated as compromised and rotated immediately."
            ),
        ),
    ]

    return [
        Prediction(
            failure_type="secret_exposure",
            violated_contract="no_secrets_contract",
            signals=signals,
            confidence=0.88,
            impact_stage="secret_scan",
            recommendation=(
                "1. Remove the file from the PR immediately — do NOT merge.\n"
                "2. Rotate the exposed credential now — treat it as compromised regardless.\n"
                "3. Add the file to .gitignore to prevent future commits.\n"
                "4. Use a secrets manager (AWS Secrets Manager, HashiCorp Vault, GitHub Secrets) "
                "to inject credentials at runtime rather than storing them in the repo.\n"
                "5. If already merged: use `git filter-repo` or BFG Repo Cleaner to purge "
                "the secret from git history, then force-push."
            ),
        )
    ]
