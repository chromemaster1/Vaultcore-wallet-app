"""
migrate_verification_fields.py
-------------------------------
One-off script to add the new bank/KYC verification columns to an existing
VaultCoreWallet database WITHOUT losing data.

If you're using Flask-Migrate/Alembic already, prefer:
    flask db migrate -m "add bank and kyc verification fields"
    flask db upgrade

If you're not (or your migrations have drifted, per the pattern in
_run_startup_migrations()), run this once against your DB instead:

    python migrate_verification_fields.py

It's idempotent — safe to run more than once, it checks for each column
before adding it. Reads SQLALCHEMY_DATABASE_URI from your .env (same
variable your app itself uses), defaulting to sqlite:///user.db if unset —
matching main.py's own default.
"""

import os
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import create_engine, text, inspect

_here = Path(__file__).parent
load_dotenv(dotenv_path=_here / '.env')

DATABASE_URL = os.getenv('SQLALCHEMY_DATABASE_URI', 'sqlite:///user.db')

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Flask-SQLAlchemy resolves a relative sqlite:///xxx.db path against the
# Flask *instance folder* (<app_root>/instance/xxx.db), NOT the current
# working directory. Plain SQLAlchemy (what this script uses) doesn't know
# that convention, so without this fix it silently creates/opens a
# different, empty database file relative to wherever you run the script from.
if DATABASE_URL.startswith("sqlite:///") and not DATABASE_URL.startswith("sqlite:////"):
    relative_path = DATABASE_URL.replace("sqlite:///", "", 1)
    instance_candidate = _here / "instance" / relative_path
    cwd_candidate = _here / relative_path
    if instance_candidate.exists() and not cwd_candidate.exists():
        DATABASE_URL = f"sqlite:///{instance_candidate.resolve()}"
    elif cwd_candidate.exists():
        DATABASE_URL = f"sqlite:///{cwd_candidate.resolve()}"
    elif instance_candidate.parent.exists():
        # Neither exists yet — prefer instance/ since that's where Flask-SQLAlchemy would create it
        DATABASE_URL = f"sqlite:///{instance_candidate.resolve()}"

print(f"Connecting to: {DATABASE_URL}")

engine = create_engine(DATABASE_URL)
inspector = inspect(engine)

COLUMNS = {
    "users": [
        ("is_foreign_user", "BOOLEAN NOT NULL DEFAULT FALSE"),
        ("kyc_status", "VARCHAR(20) NOT NULL DEFAULT 'unverified'"),
        ("kyc_provider", "VARCHAR(20) DEFAULT 'persona'"),
        ("kyc_inquiry_id", "VARCHAR(100)"),
        ("kyc_verified_name", "VARCHAR(200)"),
        ("kyc_verified_at", "TIMESTAMP"),
    ],
    "linked_banks": [
        ("match_score", "FLOAT"),
        ("flagged_for_review", "BOOLEAN NOT NULL DEFAULT FALSE"),
        ("reviewed_by", "INTEGER"),
        ("reviewed_at", "TIMESTAMP"),
        ("review_notes", "TEXT"),
    ],
}

with engine.begin() as conn:
    all_tables = inspector.get_table_names()
    if "users" not in all_tables:
        raise SystemExit(
            f"No 'users' table found in {DATABASE_URL}.\n"
            f"Tables found instead: {all_tables or '(none — this looks like an empty/new database file)'}\n\n"
            f"This usually means either:\n"
            f"  1. The app hasn't been run yet to create its tables (start main.py once, then re-run this), or\n"
            f"  2. Your real database lives somewhere else — run this to find it:\n"
            f"     find . -name '*.db' -not -path '*/.venv/*'\n"
            f"     then set SQLALCHEMY_DATABASE_URI in your .env to the absolute path, e.g.\n"
            f"     SQLALCHEMY_DATABASE_URI=sqlite:////Users/mac/Desktop/Vaultcore wallet app/instance/user.db"
        )

    for table, columns in COLUMNS.items():
        existing = {c["name"] for c in inspector.get_columns(table)}
        for col_name, col_def in columns:
            if col_name in existing:
                print(f"skip  {table}.{col_name} (already exists)")
                continue
            print(f"add   {table}.{col_name}")
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}"))

print("Done.")
