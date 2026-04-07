"""
Seed the database with labeled training data from historical ADO pipeline runs.

Reads a CSV of build records, fetches changed files from a local git clone
for each commit SHA, runs the preflight engine, and stores PredictionRecord +
CIOutcome pairs so the /stats endpoint can report accuracy immediately.

Usage:
    cd /home/node01/Documents/ci-preflight-ado
    DATABASE_URL=postgresql://preflight:preflight@localhost:5432/preflight \
    python -m scripts.seed_from_history \
        --csv ~/cpa_pipeline_runs.csv \
        --repo ~/Documents/cpa \
        --org causewayltd \
        --project CPA \
        --repo-name cpa

Dry-run (no DB writes):
    ... --dry-run
"""

import argparse
import csv
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap: make sure project root is on sys.path so app/ci_preflight import
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import init_db, SessionLocal
from app.models import PredictionRecord, CIOutcome
from ci_preflight.diff_parser import from_file_list
from ci_preflight import dependency_contract, ci_config_change, large_diff, nuget_lock_contract

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RESULT_MAP = {
    "succeeded": "success",
    "failed": "failure",
    "partiallySucceeded": "failure",
    "canceled": "cancelled",
}


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def get_changed_files(repo_path: str, sha: str) -> list[str]:
    """Return files changed in `sha` vs its parent. Empty list on any error."""
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{sha}^1", sha],
        capture_output=True,
        text=True,
        cwd=repo_path,
    )
    if result.returncode != 0:
        return []
    return [f.strip() for f in result.stdout.splitlines() if f.strip()]


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

def run_checks(files: list[str]):
    changeset = from_file_list(files)
    predictions = []
    predictions.extend(dependency_contract.check(changeset))
    predictions.extend(nuget_lock_contract.check(changeset))
    predictions.extend(ci_config_change.check(changeset))
    predictions.extend(large_diff.check(changeset))
    return predictions


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Seed DB from historical ADO pipeline runs")
    parser.add_argument("--csv", required=True, help="Path to the pipeline runs CSV")
    parser.add_argument("--repo", required=True, help="Path to local git clone of the source repo")
    parser.add_argument("--org", required=True, help="ADO org name (for repo_full_name)")
    parser.add_argument("--project", required=True, help="ADO project name")
    parser.add_argument("--repo-name", required=True, help="Repository name")
    parser.add_argument("--dry-run", action="store_true", help="Parse and run engine but skip DB writes")
    args = parser.parse_args()

    csv_path = Path(args.csv).expanduser()
    repo_path = str(Path(args.repo).expanduser())
    repo_full_name = f"{args.org}/{args.project}/{args.repo_name}"

    if not csv_path.exists():
        print(f"ERROR: CSV not found: {csv_path}")
        sys.exit(1)

    if not args.dry_run:
        init_db()

    rows = []
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)

    print(f"Loaded {len(rows)} build records from {csv_path.name}")
    print(f"Repo: {repo_full_name}  |  Local clone: {repo_path}")
    print(f"Dry run: {args.dry_run}\n")

    stats = {
        "processed": 0,
        "skipped_no_files": 0,
        "skipped_unknown_result": 0,
        "skipped_already_exists": 0,
        "predictions_saved": 0,
        "outcomes_saved": 0,
    }

    # For accuracy report at the end
    accuracy = defaultdict(lambda: {"tp": 0, "fp": 0, "total_predicted": 0, "total_builds": 0})

    with SessionLocal() as db:
        for row in rows:
            sha = row.get("sourceVersion", "").strip()
            result = row.get("result", "").strip()
            build_number = row.get("buildNumber", "").strip()

            conclusion = RESULT_MAP.get(result)
            if not conclusion or conclusion == "cancelled":
                stats["skipped_unknown_result"] += 1
                continue

            # Skip if we already have an outcome for this sha
            if not args.dry_run:
                existing = db.query(CIOutcome).filter_by(
                    repo_full_name=repo_full_name,
                    head_sha=sha,
                ).first()
                if existing:
                    stats["skipped_already_exists"] += 1
                    continue

            files = get_changed_files(repo_path, sha)
            if not files:
                stats["skipped_no_files"] += 1
                print(f"  [{build_number}] {sha[:7]} — no changed files (merge commit or missing SHA), skipping")
                continue

            predictions = run_checks(files)

            print(
                f"  [{build_number}] {sha[:7]}  result={conclusion:<8}  "
                f"files={len(files):<4}  predictions={len(predictions)}"
            )

            # Accumulate accuracy stats
            for p in predictions:
                key = f"{p.violated_contract} ({p.severity()})"
                accuracy[key]["total_predicted"] += 1
                if conclusion == "failure":
                    accuracy[key]["tp"] += 1
                elif conclusion == "success":
                    accuracy[key]["fp"] += 1

            if not args.dry_run:
                # Store predictions
                for p in predictions:
                    db.add(PredictionRecord(
                        installation_id=0,
                        repo_full_name=repo_full_name,
                        pr_number=0,
                        head_sha=sha,
                        check_type=p.violated_contract,
                        failure_type=p.failure_type,
                        confidence=p.confidence,
                        severity=p.severity(),
                    ))
                stats["predictions_saved"] += len(predictions)

                # Store CI outcome
                db.add(CIOutcome(
                    repo_full_name=repo_full_name,
                    head_sha=sha,
                    conclusion=conclusion,
                    ci_app_name="ADO/seed",
                ))
                stats["outcomes_saved"] += 1
                db.commit()

            stats["processed"] += 1

    # ---------------------------------------------------------------------------
    # Summary
    # ---------------------------------------------------------------------------
    print("\n" + "=" * 60)
    print("SEED COMPLETE")
    print("=" * 60)
    print(f"  Processed:          {stats['processed']}")
    print(f"  Skipped (no files): {stats['skipped_no_files']}")
    print(f"  Skipped (unknown):  {stats['skipped_unknown_result']}")
    print(f"  Skipped (dupes):    {stats['skipped_already_exists']}")
    if not args.dry_run:
        print(f"  Predictions saved:  {stats['predictions_saved']}")
        print(f"  Outcomes saved:     {stats['outcomes_saved']}")

    if accuracy:
        print("\nACCURACY PREVIEW (predictions vs actual CI outcome):")
        print(f"  {'Check':<45} {'Predicted':>9}  {'TP':>5}  {'FP':>5}  {'Acc':>6}")
        print("  " + "-" * 75)
        for check, counts in sorted(accuracy.items()):
            tp = counts["tp"]
            fp = counts["fp"]
            total = tp + fp
            acc = f"{round(tp / total * 100)}%" if total > 0 else "n/a"
            print(f"  {check:<45} {counts['total_predicted']:>9}  {tp:>5}  {fp:>5}  {acc:>6}")
    else:
        print("\nNo predictions triggered — the engine found no contract violations in this dataset.")
        print("This is expected if the repo doesn't have lockfile-change patterns in its history.")


if __name__ == "__main__":
    main()
