"""
Load pre-downloaded FastF1 CSV files into AlloyDB.
Reads from data/ folder instead of pulling from FastF1 API (no rate limiting).

Applies all data quality fixes found during analysis:
  - fastest_lap_time: strips '0 days ' prefix for INTERVAL format
  - compound:         INTERMEDIATE → INTER, UNKNOWN → NULL
  - lap_number:       float → int
  - sector times:     NaT/NaN strings → NULL
  - sector (rc):      float NaN → NULL, cast to int
  - flag_type:        NaN → NULL

Uses FastF1 schedule to map event_name → round_number → f1_sessions.id
(avoids unreliable race name string matching across languages).

Usage:
    python scripts/load_csv_to_alloydb.py --years 2024         # test one year
    python scripts/load_csv_to_alloydb.py --years 2020 2021 2022 2023 2024 2025
    python scripts/load_csv_to_alloydb.py --dry-run --years 2024
"""

import os
import argparse
import fastf1
import pandas as pd
import psycopg2
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

DATA_DIR  = Path(__file__).parent.parent / "data"
CACHE_DIR = os.getenv("FASTF1_CACHE_DIR", "/tmp/fastf1_cache")
os.makedirs(CACHE_DIR, exist_ok=True)
fastf1.Cache.enable_cache(CACHE_DIR)


# ── Data quality helpers ──────────────────────────────────────────────────────

COMPOUND_MAP = {"INTERMEDIATE": "INTER", "UNKNOWN": None}

def fix_compound(val) -> str | None:
    if pd.isna(val):
        return None
    return COMPOUND_MAP.get(str(val), str(val))

def fix_interval(val) -> str | None:
    """Convert '0 days 00:01:31.447000' → '00:01:31.447' for PostgreSQL INTERVAL."""
    if pd.isna(val) or str(val) in ('NaT', 'NaN', '', 'None'):
        return None
    s = str(val)
    if s.startswith('0 days '):
        s = s[7:]          # strip '0 days '
    s = s.split('.')[0] + ('.' + s.split('.')[1][:3] if '.' in s else '')
    return s

def fix_int(val) -> int | None:
    if pd.isna(val):
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


# ── DB helpers ────────────────────────────────────────────────────────────────

def get_conn():
    return psycopg2.connect(
        host=os.getenv("ALLOYDB_HOST", "127.0.0.1"),
        port=os.getenv("ALLOYDB_PORT", "5432"),
        database=os.getenv("ALLOYDB_DATABASE", "f1db"),
        user=os.getenv("ALLOYDB_USER", "postgres"),
        password=os.getenv("ALLOYDB_PASSWORD"),
    )

SESSION_TYPE_MAP = {"R": "Race", "Q": "Qualifying", "S": "Sprint"}

def build_session_index(conn, year: int, schedule: pd.DataFrame) -> dict:
    """
    Returns {(event_name, session_type_short): session_uuid}
    Uses round number as the join key to avoid language/name mismatches.
    """
    cur = conn.cursor()
    cur.execute(
        "SELECT round, session_type, id FROM f1_sessions WHERE season = %s",
        (year,)
    )
    db_rows = cur.fetchall()
    cur.close()

    # round → session_type → uuid
    db_index: dict[tuple[int, str], str] = {}
    for round_num, stype, uid in db_rows:
        db_index[(round_num, stype)] = str(uid)

    # FastF1 event_name → round number
    ff1_round: dict[str, int] = {
        row["EventName"]: int(row["RoundNumber"])
        for _, row in schedule.iterrows()
    }

    # Build final lookup: (event_name, 'R'/'Q') → session_uuid
    result = {}
    for event_name, round_num in ff1_round.items():
        for short, long in SESSION_TYPE_MAP.items():
            key = (event_name, short)
            db_key = (round_num, long)
            if db_key in db_index:
                result[key] = db_index[db_key]

    return result


# ── Table loaders ─────────────────────────────────────────────────────────────

def load_telemetry(conn, df: pd.DataFrame, session_index: dict, dry_run: bool) -> dict:
    cur = conn.cursor()
    counts = {"inserted": 0, "skipped": 0, "no_session": 0, "err": 0}

    for _, row in df.iterrows():
        sid = session_index.get((row["event_name"], row["session_type"]))
        if not sid:
            counts["no_session"] += 1
            continue

        lap_time = fix_interval(row["fastest_lap_time"])
        if lap_time is None:
            counts["skipped"] += 1
            continue

        if dry_run:
            counts["inserted"] += 1
            continue

        try:
            cur.execute("SAVEPOINT sp")
            cur.execute("""
                INSERT INTO f1_telemetry_summary
                (session_id, driver_id, avg_speed, top_speed, avg_throttle,
                 avg_brake, avg_rpm, peak_rpm, fastest_lap_time)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (session_id, driver_id) DO NOTHING
            """, (sid, row["driver_code"],
                  float(row["avg_speed"]),    float(row["top_speed"]),
                  float(row["avg_throttle"]), float(row["avg_brake"]),
                  int(row["avg_rpm"]),         int(row["peak_rpm"]),
                  lap_time))
            cur.execute("RELEASE SAVEPOINT sp")
            counts["inserted"] += 1
        except Exception as e:
            cur.execute("ROLLBACK TO SAVEPOINT sp")
            counts["err"] += 1

    if not dry_run:
        conn.commit()
    cur.close()
    return counts


def load_stints(conn, df: pd.DataFrame, session_index: dict, dry_run: bool) -> dict:
    cur = conn.cursor()
    counts = {"inserted": 0, "skipped": 0, "no_session": 0, "err": 0}

    for _, row in df.iterrows():
        sid = session_index.get((row["event_name"], row["session_type"]))
        if not sid:
            counts["no_session"] += 1
            continue

        compound = fix_compound(row["compound"])
        # UNKNOWN compounds — skip (no meaningful data)
        if compound is None and str(row.get("compound", "")) == "UNKNOWN":
            counts["skipped"] += 1
            continue

        if dry_run:
            counts["inserted"] += 1
            continue

        try:
            cur.execute("SAVEPOINT sp")
            cur.execute("""
                INSERT INTO f1_stints
                (session_id, driver_id, stint_number, compound,
                 start_lap, end_lap, lap_count)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (session_id, driver_id, stint_number) DO NOTHING
            """, (sid, row["driver_code"],
                  int(row["stint_number"]), compound,
                  int(row["start_lap"]),    int(row["end_lap"]),
                  int(row["lap_count"])))
            cur.execute("RELEASE SAVEPOINT sp")
            counts["inserted"] += 1
        except Exception as e:
            cur.execute("ROLLBACK TO SAVEPOINT sp")
            counts["err"] += 1

    if not dry_run:
        conn.commit()
    cur.close()
    return counts


def load_lap_summary(conn, df: pd.DataFrame, session_index: dict, dry_run: bool) -> dict:
    cur = conn.cursor()
    counts = {"inserted": 0, "no_session": 0, "err": 0}

    for _, row in df.iterrows():
        sid = session_index.get((row["event_name"], row["session_type"]))
        if not sid:
            counts["no_session"] += 1
            continue

        lap_num = fix_int(row["lap_number"])
        if lap_num is None:
            continue

        if dry_run:
            counts["inserted"] += 1
            continue

        try:
            cur.execute("SAVEPOINT sp")
            cur.execute("""
                INSERT INTO f1_lap_summary
                (session_id, driver_id, lap_number, lap_time,
                 sector1_time, sector2_time, sector3_time,
                 is_valid, pit_in_lap, pit_out_lap, compound, tyre_age)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (session_id, driver_id, lap_number) DO NOTHING
            """, (sid, row["driver_code"], lap_num,
                  fix_interval(row["lap_time"]),
                  fix_interval(row["sector1_time"]),
                  fix_interval(row["sector2_time"]),
                  fix_interval(row["sector3_time"]),
                  bool(row["is_valid"]),
                  bool(row["pit_in_lap"]),
                  bool(row["pit_out_lap"]),
                  fix_compound(row["compound"]),
                  fix_int(row["tyre_age"]) or 0))
            cur.execute("RELEASE SAVEPOINT sp")
            counts["inserted"] += 1
        except Exception as e:
            cur.execute("ROLLBACK TO SAVEPOINT sp")
            counts["err"] += 1

    if not dry_run:
        conn.commit()
    cur.close()
    return counts


def load_race_control(conn, df: pd.DataFrame, session_index: dict, dry_run: bool) -> dict:
    cur = conn.cursor()
    counts = {"inserted": 0, "no_session": 0, "err": 0}

    for _, row in df.iterrows():
        # race_control CSV only has Race sessions
        sid = session_index.get((row["event_name"], "R"))
        if not sid:
            counts["no_session"] += 1
            continue

        flag_type = None if pd.isna(row.get("flag_type")) else str(row["flag_type"])
        sector    = fix_int(row.get("sector"))

        if dry_run:
            counts["inserted"] += 1
            continue

        try:
            cur.execute("SAVEPOINT sp")
            cur.execute("""
                INSERT INTO f1_race_control
                (session_id, lap_number, flag_type, sector, message)
                VALUES (%s,%s,%s,%s,%s)
            """, (sid, fix_int(row["lap_number"]),
                  flag_type, sector, str(row["message"])))
            cur.execute("RELEASE SAVEPOINT sp")
            counts["inserted"] += 1
        except Exception as e:
            cur.execute("ROLLBACK TO SAVEPOINT sp")
            counts["err"] += 1

    if not dry_run:
        conn.commit()
    cur.close()
    return counts


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--years",   nargs="+", type=int,
                        default=[2020, 2021, 2022, 2023, 2024, 2025])
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate without inserting")
    args = parser.parse_args()

    # Load CSVs once
    print("Loading CSV files from data/...")
    tel = pd.read_csv(DATA_DIR / "telemetry_summary.csv")
    st  = pd.read_csv(DATA_DIR / "stints.csv")
    lap = pd.read_csv(DATA_DIR / "lap_summary.csv")
    rc  = pd.read_csv(DATA_DIR / "race_control.csv")
    print(f"  telemetry:   {len(tel):,} rows")
    print(f"  stints:      {len(st):,} rows")
    print(f"  lap_summary: {len(lap):,} rows")
    print(f"  race_control:{len(rc):,} rows")

    conn = get_conn()
    grand_totals = {"tel": 0, "stints": 0, "laps": 0, "rc": 0, "no_session": 0, "err": 0}

    for year in args.years:
        print(f"\n{'='*55}")
        print(f"YEAR {year}  (dry_run={args.dry_run})")
        print(f"{'='*55}")

        # Build session lookup via round number
        try:
            schedule = fastf1.get_event_schedule(year, include_testing=False)
        except Exception as e:
            print(f"  ⚠ Could not fetch {year} schedule: {e}")
            continue

        session_index = build_session_index(conn, year, schedule)
        print(f"  Session index built: {len(session_index)} entries")

        # Filter CSVs to this year
        t = tel[tel["year"] == year]
        s = st[st["year"] == year]
        l = lap[lap["year"] == year]
        r = rc[rc["year"] == year]
        print(f"  Rows this year — tel:{len(t)} stints:{len(s)} laps:{len(l)} rc:{len(r)}")

        print(f"\n  Loading telemetry_summary...")
        c = load_telemetry(conn, t, session_index, args.dry_run)
        print(f"  → inserted:{c['inserted']} skipped:{c['skipped']} no_session:{c['no_session']} err:{c['err']}")
        grand_totals["tel"] += c["inserted"]
        grand_totals["no_session"] += c["no_session"]
        grand_totals["err"] += c["err"]

        print(f"  Loading stints...")
        c = load_stints(conn, s, session_index, args.dry_run)
        print(f"  → inserted:{c['inserted']} skipped:{c['skipped']} no_session:{c['no_session']} err:{c['err']}")
        grand_totals["stints"] += c["inserted"]

        print(f"  Loading lap_summary...")
        c = load_lap_summary(conn, l, session_index, args.dry_run)
        print(f"  → inserted:{c['inserted']} no_session:{c['no_session']} err:{c['err']}")
        grand_totals["laps"] += c["inserted"]

        print(f"  Loading race_control...")
        c = load_race_control(conn, r, session_index, args.dry_run)
        print(f"  → inserted:{c['inserted']} no_session:{c['no_session']} err:{c['err']}")
        grand_totals["rc"] += c["inserted"]

    conn.close()

    print(f"\n{'='*55}")
    print("GRAND TOTALS")
    print(f"{'='*55}")
    print(f"  telemetry_summary: {grand_totals['tel']:,}")
    print(f"  stints:            {grand_totals['stints']:,}")
    print(f"  lap_summary:       {grand_totals['laps']:,}")
    print(f"  race_control:      {grand_totals['rc']:,}")
    print(f"  no_session_match:  {grand_totals['no_session']:,}")
    print(f"  errors:            {grand_totals['err']:,}")
    print(f"\n{'DRY RUN — nothing inserted' if args.dry_run else 'Done.'}")


if __name__ == "__main__":
    main()
