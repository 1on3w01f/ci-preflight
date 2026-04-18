"""
Missing database migration check.

When a database model or schema definition is changed, a corresponding migration
file must be created and committed. Skipping the migration means the database
schema will be out of sync with the application code, causing test failures and
runtime errors.

Causal mechanism:
  Model file changed → ORM expects schema change → migration not present
  → DB schema mismatch → test suite fails on DB operations

Frameworks covered:
  Django         models.py → migrations/*.py
  Alembic        models.py → alembic/versions/*.py
  Prisma         schema.prisma → prisma/migrations/*/migration.sql
  TypeORM        *.entity.ts → src/migrations/*.ts  (or similar)
  Rails          db/schema.rb or app/models/*.rb → db/migrate/*.rb
  Sequelize      models/*.js / models/*.ts → migrations/*.js / migrations/*.ts
"""

from typing import List
from ci_preflight.models import ChangeSet, Signal, Prediction

# (manifest_patterns, migration_patterns, ecosystem_name)
# manifest_patterns: suffixes or exact names that indicate model changes
# migration_patterns: suffixes or path fragments that indicate a migration was added
FRAMEWORK_RULES = [
    (
        # Django / Alembic (Python)
        ["models.py"],
        ["migrations/", "alembic/versions/"],
        "Django/Alembic",
        "Run `python manage.py makemigrations` (Django) or `alembic revision --autogenerate` (Alembic) and commit the generated file.",
    ),
    (
        # Prisma
        ["schema.prisma"],
        ["prisma/migrations/"],
        "Prisma",
        "Run `npx prisma migrate dev --name <name>` and commit the generated migration directory.",
    ),
    (
        # TypeORM
        [".entity.ts", ".entity.js"],
        ["migration", "migrations/"],
        "TypeORM",
        "Generate a migration with `typeorm migration:generate` and commit the file.",
    ),
    (
        # Rails
        ["db/schema.rb", "app/models/"],
        ["db/migrate/"],
        "Rails (ActiveRecord)",
        "Run `rails generate migration <Name>` and commit the migration file.",
    ),
    (
        # Sequelize
        ["models/", "src/models/"],
        ["migrations/"],
        "Sequelize",
        "Generate a migration with `sequelize migration:generate --name <name>` and commit the file.",
    ),
]


def _matches_any(filename: str, patterns: list[str]) -> bool:
    for p in patterns:
        if filename.endswith(p) or p in filename or filename == p:
            return True
    return False


def check(changeset: ChangeSet) -> List[Prediction]:
    predictions = []

    for manifest_patterns, migration_patterns, ecosystem, fix in FRAMEWORK_RULES:
        model_files = [
            f for f in changeset.changed_files if _matches_any(f, manifest_patterns)
        ]
        if not model_files:
            continue

        migration_files = [
            f for f in changeset.changed_files if _matches_any(f, migration_patterns)
        ]
        if migration_files:
            # Migration was included — no issue
            continue

        signals = [
            Signal(
                id="model_changed",
                description=f"{ecosystem} model/schema file(s) modified: {', '.join(model_files[:3])}",
            ),
            Signal(
                id="no_migration_found",
                description=(
                    f"No migration file was found in this changeset. "
                    f"Without a migration, the database schema will be out of sync "
                    f"with the updated model — test suite DB operations will fail."
                ),
            ),
        ]

        predictions.append(
            Prediction(
                failure_type="db_schema_mismatch",
                violated_contract="migration_contract",
                signals=signals,
                confidence=0.78,
                impact_stage="test",
                recommendation=fix,
            )
        )

    return predictions
