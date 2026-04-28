"""
Phase 3 schema migration — creates all new tables and indexes in AlloyDB.

Run with the AlloyDB Auth Proxy active on 127.0.0.1:5433:
    python scripts/run_migrations.py

Use --dry-run to print SQL without executing:
    python scripts/run_migrations.py --dry-run
"""

import os
import sys
import argparse
import psycopg2
from dotenv import load_dotenv

load_dotenv()


MIGRATIONS = [
    # ── Extensions ────────────────────────────────────────────────────────────
    (
        "enable_vector",
        "CREATE EXTENSION IF NOT EXISTS vector",
    ),
    (
        "enable_alloydb_scann",
        "CREATE EXTENSION IF NOT EXISTS alloydb_scann",
    ),
    (
        "enable_google_ml",
        "CREATE EXTENSION IF NOT EXISTS google_ml_integration CASCADE",
    ),
    (
        "grant_embedding",
        "GRANT EXECUTE ON FUNCTION embedding TO postgres",
    ),

    # ── Telemetry Layer ───────────────────────────────────────────────────────
    (
        "create_f1_telemetry_summary",
        """
        CREATE TABLE IF NOT EXISTS f1_telemetry_summary (
            id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            session_id       UUID REFERENCES f1_sessions(id),
            driver_id        TEXT REFERENCES f1_drivers(driver_id),
            avg_speed        FLOAT,
            top_speed        FLOAT,
            avg_throttle     FLOAT,
            avg_brake        FLOAT,
            avg_rpm          FLOAT,
            peak_rpm         FLOAT,
            fastest_lap_time INTERVAL,
            created_at       TIMESTAMP DEFAULT NOW(),
            UNIQUE(session_id, driver_id)
        )
        """,
    ),
    (
        "create_f1_stints",
        """
        CREATE TABLE IF NOT EXISTS f1_stints (
            id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            session_id   UUID REFERENCES f1_sessions(id),
            driver_id    TEXT REFERENCES f1_drivers(driver_id),
            stint_number INTEGER,
            compound     TEXT,
            start_lap    INTEGER,
            end_lap      INTEGER,
            lap_count    INTEGER,
            UNIQUE(session_id, driver_id, stint_number)
        )
        """,
    ),

    # ── Steward Layer ─────────────────────────────────────────────────────────
    (
        "create_f1_lap_summary",
        """
        CREATE TABLE IF NOT EXISTS f1_lap_summary (
            id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            session_id     UUID REFERENCES f1_sessions(id),
            driver_id      TEXT REFERENCES f1_drivers(driver_id),
            lap_number     INTEGER,
            lap_time       INTERVAL,
            sector1_time   INTERVAL,
            sector2_time   INTERVAL,
            sector3_time   INTERVAL,
            is_valid       BOOLEAN,
            deleted_reason TEXT,
            pit_in_lap     BOOLEAN,
            pit_out_lap    BOOLEAN,
            compound       TEXT,
            tyre_age       INTEGER,
            UNIQUE(session_id, driver_id, lap_number)
        )
        """,
    ),
    (
        "create_f1_race_control",
        """
        CREATE TABLE IF NOT EXISTS f1_race_control (
            id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            session_id UUID REFERENCES f1_sessions(id),
            lap_number INTEGER,
            timestamp  TIMESTAMP,
            flag_type  TEXT,
            sector     INTEGER,
            message    TEXT,
            driver_id  TEXT
        )
        """,
    ),

    # ── RAG Layer (populated in Phase 4) ─────────────────────────────────────
    (
        "create_f1_regulations",
        """
        CREATE TABLE IF NOT EXISTS f1_regulations (
            id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            year           INTEGER,
            reg_type       TEXT,
            article_number TEXT,
            article_title  TEXT,
            content        TEXT,
            embedding      VECTOR(768),
            source_url     TEXT,
            created_at     TIMESTAMP DEFAULT NOW()
        )
        """,
    ),
    (
        "create_f1_decisions",
        """
        CREATE TABLE IF NOT EXISTS f1_decisions (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            race        TEXT,
            year        INTEGER,
            driver_id   TEXT,
            team_id     TEXT,
            incident    TEXT,
            ruling      TEXT,
            penalty     TEXT,
            article_ref TEXT,
            content     TEXT,
            embedding   VECTOR(768),
            created_at  TIMESTAMP DEFAULT NOW()
        )
        """,
    ),

]

# ScaNN indexes must be created AFTER Phase 4 data ingestion —
# AlloyDB rejects them on empty tables.
VECTOR_INDEXES = [
    (
        "create_regulations_idx",
        """
        CREATE INDEX IF NOT EXISTS regulations_embedding_idx
            ON f1_regulations USING scann (embedding cosine)
            WITH (num_leaves = 50)
        """,
    ),
    (
        "create_decisions_idx",
        """
        CREATE INDEX IF NOT EXISTS decisions_embedding_idx
            ON f1_decisions USING scann (embedding cosine)
            WITH (num_leaves = 30)
        """,
    ),
]


def get_conn():
    try:
        conn = psycopg2.connect(
            host=os.getenv("ALLOYDB_HOST", "127.0.0.1"),
            port=os.getenv("ALLOYDB_PORT", "5433"),
            database=os.getenv("ALLOYDB_DATABASE", "f1db"),
            user=os.getenv("ALLOYDB_USER", "postgres"),
            password=os.getenv("ALLOYDB_PASSWORD"),
        )
        conn.autocommit = True
        return conn
    except Exception as e:
        print(f"Connection failed: {e}")
        sys.exit(1)


def execute_batch(conn, steps: list, dry_run: bool, label: str):
    passed = failed = 0
    for name, sql in steps:
        clean = " ".join(sql.split())
        if dry_run:
            print(f"  [DRY RUN] {name}:\n    {clean[:100]}...\n")
            passed += 1
            continue
        try:
            cur = conn.cursor()
            cur.execute(sql)
            cur.close()
            print(f"  ✓ {name}")
            passed += 1
        except Exception as e:
            print(f"  ✗ {name}: {e}")
            failed += 1
    print(f"\n{label}: {passed} passed, {failed} failed")
    return failed


def run(dry_run: bool, indexes: bool):
    conn = None if dry_run else get_conn()

    print("\n── Schema migrations ─────────────────────────")
    schema_failures = execute_batch(conn, MIGRATIONS, dry_run, "Schema")

    if indexes:
        print("\n── Vector indexes (post-ingestion) ───────────")
        idx_failures = execute_batch(conn, VECTOR_INDEXES, dry_run, "Indexes")
    else:
        print("\n── Vector indexes skipped ────────────────────")
        print("  Run with --indexes after Phase 4 data ingestion.")
        idx_failures = 0

    if conn:
        conn.close()

    total_failures = schema_failures + idx_failures
    print(f"\n{'='*46}")
    if total_failures:
        print(f"Completed with {total_failures} failure(s).")
        sys.exit(1)
    else:
        print("All steps passed ✓")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Print SQL without executing")
    parser.add_argument("--indexes", action="store_true",
                        help="Also create ScaNN vector indexes (run after Phase 4 ingestion)")
    args = parser.parse_args()

    print(f"Running migrations  dry_run={args.dry_run}  indexes={args.indexes}")
    print(f"DB: {os.getenv('ALLOYDB_HOST', '127.0.0.1')}:{os.getenv('ALLOYDB_PORT', '5433')}\n")
    run(args.dry_run, args.indexes)
