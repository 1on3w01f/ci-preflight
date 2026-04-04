"""
CI Preflight CLI — predict pipeline failures before CI runs.

Usage:
    # Analyse last commit in current repo
    python main.py

    # Analyse a specific repo path
    python main.py --repo /path/to/repo

    # Analyse from a patch file (e.g. GitHub PR diff)
    python main.py --patch path/to/changes.patch

    # Analyse an explicit file list (useful for testing)
    python main.py --files package.json src/app.py
"""

import argparse
import sys

from ci_preflight.diff_parser import from_git, from_patch_file, from_file_list
from ci_preflight import dependency_contract
from ci_preflight.reporter import print_report


def run(changeset):
    predictions = []

    # --- registered checks ---
    # Add new contract checks here as you build them
    predictions.extend(dependency_contract.check(changeset))

    return predictions


def main():
    parser = argparse.ArgumentParser(
        description="CI Preflight — predict pipeline failures before CI runs."
    )
    parser.add_argument("--repo",  default=".", help="Path to git repo (default: current dir)")
    parser.add_argument("--patch", default=None, help="Path to a .patch file")
    parser.add_argument("--files", nargs="+", default=None, help="Explicit list of changed files")

    args = parser.parse_args()

    if args.files:
        changeset = from_file_list(args.files)
    elif args.patch:
        changeset = from_patch_file(args.patch)
    else:
        changeset = from_git(args.repo)

    print(f"\nFiles in scope: {changeset.changed_files}\n")

    predictions = run(changeset)
    print_report(predictions)

    # Exit 1 if HIGH severity predictions exist — blocks CI
    if any(p.severity() == "HIGH" for p in predictions):
        sys.exit(1)


if __name__ == "__main__":
    main()
