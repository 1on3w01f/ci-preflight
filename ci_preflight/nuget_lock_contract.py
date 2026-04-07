"""
NuGet lock contract check.

When a .csproj (or Directory.Packages.props) is modified, the NuGet package
resolution may produce different transitive dependencies on the next restore.
If the repo uses packages.lock.json (RestoreLockedMode), failing to update
the lock file will cause the build to fail outright.

If the repo does NOT use lock files, this check fires at MEDIUM confidence as
a signal that non-deterministic dependency resolution is occurring and the
team should consider enabling RestoreLockedMode.

Causal mechanism:
  .csproj changed → `dotnet restore` re-resolves packages → different versions
  may be selected → compilation or runtime failures downstream.

NuGet lock files reference:
  https://learn.microsoft.com/en-us/nuget/consume-packages/package-references-in-project-files#locking-dependencies
"""

from typing import List
from ci_preflight.models import ChangeSet, Signal, Prediction

# Files that control NuGet package resolution.
NUGET_MANIFEST_SUFFIXES = [".csproj", ".vbproj", ".fsproj"]
NUGET_CENTRAL_MANIFESTS = ["Directory.Packages.props", "Directory.Build.props"]
NUGET_LOCKFILE = "packages.lock.json"
GLOBAL_JSON = "global.json"


def _csproj_changed(changeset: ChangeSet) -> list[str]:
    changed = []
    for suffix in NUGET_MANIFEST_SUFFIXES:
        changed.extend(changeset.files_matching(suffix))
    for name in NUGET_CENTRAL_MANIFESTS:
        if changeset.has_file(name) or any(f.endswith("/" + name) for f in changeset.changed_files):
            changed.append(name)
    return changed


def _lockfiles_updated(changeset: ChangeSet) -> list[str]:
    return [f for f in changeset.changed_files if f.endswith(NUGET_LOCKFILE)]


def check(changeset: ChangeSet) -> List[Prediction]:
    manifests = _csproj_changed(changeset)
    if not manifests:
        return []

    lockfiles_updated = _lockfiles_updated(changeset)
    global_json_changed = changeset.has_file(GLOBAL_JSON) or any(
        f.endswith("/" + GLOBAL_JSON) for f in changeset.changed_files
    )

    # global.json change alone (SDK version bump) is a separate signal
    if not manifests and global_json_changed:
        return []

    signals = [
        Signal(
            id="nuget_manifest_modified",
            description=f"NuGet project file(s) modified: {', '.join(manifests)}",
        ),
    ]

    if global_json_changed:
        signals.append(Signal(
            id="global_json_changed",
            description=(
                "global.json was also changed — the .NET SDK version is being pinned "
                "or changed, which can alter package compatibility and resolution."
            ),
        ))

    if lockfiles_updated:
        # Lock files were updated — this is correct behaviour, no prediction needed.
        return []

    if not lockfiles_updated:
        signals.append(Signal(
            id="nuget_lockfile_not_updated",
            description=(
                f"No packages.lock.json was updated alongside the project file change. "
                f"If RestoreLockedMode is enabled, the build will fail at restore. "
                f"If not enabled, package resolution is non-deterministic."
            ),
        ))

    # Confidence: HIGH only if we see evidence the repo is already using NuGet lock
    # files (i.e., some other packages.lock.json appears in this changeset, indicating
    # the project has RestoreLockedMode enabled and the lock file simply wasn't updated
    # for this project). If no lock files appear at all, we drop to MEDIUM — the repo
    # may not have lock files enabled, so the build may still succeed via fresh resolve.
    any_lock_file_in_repo = any(
        f.endswith(NUGET_LOCKFILE) for f in changeset.changed_files
    )
    confidence = 0.82 if any_lock_file_in_repo else 0.60

    return [
        Prediction(
            failure_type="nuget_resolution_failure",
            violated_contract="nuget_lock_contract",
            signals=signals,
            confidence=confidence,
            impact_stage="restore",
            recommendation=(
                "Option 1 (if using RestoreLockedMode): run `dotnet restore` locally "
                "and commit the updated packages.lock.json alongside your .csproj changes.\n"
                "Option 2 (if not using lock files): enable deterministic restore by adding "
                "<RestorePackagesWithLockFile>true</RestorePackagesWithLockFile> to your "
                ".csproj and committing the generated packages.lock.json. "
                "This prevents 'works on my machine' package version drift."
            ),
        )
    ]
