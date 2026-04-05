# CI Preflight

**Predict CI/CD pipeline failures before they happen.**

CI Preflight runs on every pull request and flags contract violations that will break your build — before CI even starts. No config, no setup, one click to install.

[![CI](https://github.com/1on3w01f/ci-preflight/actions/workflows/ci.yml/badge.svg)](https://github.com/1on3w01f/ci-preflight/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

---

## The problem

Your CI pipeline fails. You wait 8 minutes to find out `package-lock.json` is out of sync with `package.json`. You fix it, push again, wait another 8 minutes. This is a solved problem — it just hasn't been automated yet.

CI Preflight catches these before the pipeline runs.

---

## What it looks like

When you open a PR, CI Preflight posts a check run with its findings:

```
====================================================
  CI PREFLIGHT RISK REPORT
====================================================

  1 predicted failure(s) found.

  [1] HIGH  —  DEPENDENCY RESOLUTION FAILURE
      Contract   : dependency_lock_contract
      Stage      : build
      Confidence : 85%

      Signals detected:
        •  package.json was modified
        •  package-lock.json was not updated alongside package.json

      Recommendation:
        Run `npm install` to regenerate package-lock.json before pushing.

  ------------------------------------------------

====================================================
```

HIGH severity predictions block the merge. MEDIUM and LOW are advisory.

---

## Checks

| Check | What it catches | Ecosystems |
|---|---|---|
| **Dependency lock contract** | Manifest changed without updating lockfile | Node, Yarn, Go, Python (pip-compile, Pipenv) |

More checks are in development. [Open an issue](https://github.com/1on3w01f/ci-preflight/issues) to request one.

---

## Install the GitHub App

> One click. No config. Works on any repo.

**[Install CI Preflight →](https://github.com/apps/ci-preflight-dev)**

CI Preflight will post a check run on every pull request automatically.

---

## CLI usage

Run CI Preflight locally against your last commit:

```bash
pip install -r requirements.txt

# Analyse last commit
python main.py

# Analyse from a patch file
python main.py --patch path/to/changes.patch

# Analyse an explicit file list
python main.py --files package.json src/app.py
```

Exits with code `1` if HIGH severity predictions are found — useful for pre-push hooks or local CI scripts.

---

## How it works

```
PR opened
  → GitHub sends webhook to CI Preflight server
  → Worker fetches the PR diff via GitHub API
  → Engine runs contract checks against the changed files
  → Check run posted back to the PR (pass / fail + report)
```

The engine is a collection of **contracts** — rules about what must be true for a changeset to be safe. Each contract produces zero or more **predictions**, each with a confidence score, the signals that triggered it, and a recommendation to fix it.

---

## Self-hosting

```bash
git clone https://github.com/1on3w01f/ci-preflight.git
cd ci-preflight

# Set env vars
cp .env.example .env   # fill in GITHUB_APP_ID, GITHUB_WEBHOOK_SECRET

# Run the full stack
docker compose up --build
```

You'll need a [GitHub App](https://docs.github.com/en/apps/creating-github-apps/about-creating-github-apps/about-creating-github-apps) with `checks: write` and `pull_requests: read` permissions.

---

## Roadmap

- [ ] Terraform plan contract — flag infra changes without a plan output
- [ ] Test coverage contract — flag source changes without test file changes
- [ ] Analytics dashboard — per-repo prediction history and accuracy
- [ ] Stripe billing — free tier (3 repos), Starter ($19/mo), Team ($49/mo)

---

## Contributing

The engine is designed to be extended. Adding a new check takes ~30 lines:

1. Create `ci_preflight/your_contract.py`
2. Implement `check(changeset: ChangeSet) -> List[Prediction]`
3. Register it in `main.py`

PRs welcome.

---

## License

MIT
