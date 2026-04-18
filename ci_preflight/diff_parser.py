import subprocess
from pathlib import Path
from ci_preflight.models import ChangeSet


def from_git(repo_path: str = ".") -> ChangeSet:
    """
    Extracts changed files from git diff against the previous commit.
    Works for local testing. The webhook server uses from_patch_file().
    """
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD~1", "HEAD"],
            capture_output=True,
            text=True,
            cwd=repo_path
        )
        files = [f.strip() for f in result.stdout.strip().split("\n") if f.strip()]
        return ChangeSet(changed_files=files)

    except Exception as e:
        print(f"[diff_parser] git error: {e}")
        return ChangeSet()


def from_patch_file(patch_path: str) -> ChangeSet:
    """
    Extracts changed files from a unified diff / .patch file.
    The GitHub webhook server fetches the PR diff and passes it here.
    """
    files = []
    path = Path(patch_path)

    if not path.exists():
        print(f"[diff_parser] patch file not found: {patch_path}")
        return ChangeSet()

    with open(path, "r") as f:
        for line in f:
            # Standard unified diff format: +++ b/path/to/file
            if line.startswith("+++ b/"):
                filename = line[6:].strip()
                files.append(filename)

    return ChangeSet(changed_files=files)


def from_diff_text(diff: str) -> ChangeSet:
    """
    Extracts changed files from a raw unified diff string.
    Also detects deleted files and unresolved merge conflict markers.
    Used by the webhook server when the GitHub API returns the PR diff directly.
    """
    files = []
    deleted = []
    has_conflicts = False
    lines = diff.splitlines()

    for i, line in enumerate(lines):
        if line.startswith("+++ b/"):
            files.append(line[6:].strip())
        elif line.startswith("--- a/") and i + 1 < len(lines) and lines[i + 1] == "+++ /dev/null":
            deleted.append(line[6:].strip())
        # Conflict marker added in this diff (line starts with + then the marker)
        elif line.startswith("+<<<<<<< ") or line.startswith("+>>>>>>> "):
            has_conflicts = True

    return ChangeSet(changed_files=files, deleted_files=deleted, has_conflict_markers=has_conflicts)


def from_file_list(files: list) -> ChangeSet:
    """
    Accepts a plain list of filenames.
    Useful for tests and synthetic scenarios.
    """
    return ChangeSet(changed_files=files)
