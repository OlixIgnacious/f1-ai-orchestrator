"""
Phase 3 backfill: populates f1_telemetry_summary, f1_stints, f1_lap_summary,
and f1_race_control from FastF1 for seasons 2018-2025.

FastF1 enforces a 500 calls/hour rate limit. This script tracks call count and
automatically sleeps until the hour resets before continuing.

Requirements before running:
  1. Schema migrations applied (run scripts/run_migrations.py).
  2. FASTF1_CACHE_DIR pointing to your GCS mount (or /tmp for local testing).
  3. AlloyDB reachable (direct IP or auth proxy).

Run on a Cloud Shell VM so it persists if your terminal disconnects:
    nohup python scripts/backfill_telemetry.py > backfill.log 2>&1 &

Usage:
    python scripts/backfill_telemetry.py [--seasons 2023 2024] [--dry-run]
"""

import os
import time
import argparse
import fastf1
import psycopg2
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

CACHE_DIR = os.getenv("FASTF1_CACHE_DIR", "/tmp/fastf1_cache")
os.makedirs(CACHE_DIR, exist_ok=True)
fastf1.Cache.enable_cache(CACHE_DIR)

SESSION_TYPE_MAP = {'R': 'Race', 'Q': 'Qualifying', 'S': 'Sprint'}

# FastF1 rate limit: 500 calls/hour. Each session.load() uses ~6-10 calls.
# We stay conservative at 450 to leave headroom.
RATE_LIMIT      = 450
_call_count     = 0
_window_start   = time.time()


def _rate_check():
    """Block if we're approaching the hourly rate limit, then reset."""
    global _call_count, _window_start
    _call_count += 1
    elapsed = time.time() - _window_start

    if _call_count >= RATE_LIMIT:
        if elapsed < 3600:
            wait = int(3600 - elapsed) + 10  # +10s buffer
            print(f"\n⏸  Rate limit approached ({_call_count} calls). "
                  f"Sleeping {wait}s until window resets...")
            time.sleep(wait)
        _call_count   = 0
        _window_start = time.time()
        print("▶  Resuming backfill.\n")


def get_conn():
    return psycopg2.connect(
        host=os.getenv("ALLOYDB_HOST", "127.0.0.1"),
        port=os.getenv("ALLOYDB_PORT", "5433"),
        database=os.getenv("ALLOYDB_DATABASE", "f1db"),
        user=os.getenv("ALLOYDB_USER", "postgres"),
        password=os.getenv("ALLOYDB_PASSWORD")
    )


def get_session_id(cur, year: int, race_name: str, session_type: str):
    cur.execute(
        "SELECT id FROM f1_sessions "
        "WHERE season=%s AND race_name ILIKE %s AND session_type=%s LIMIT 1",
        (year, f"%{race_name}%", session_type)
    )
    row = cur.fetchone()
    return row[0] if row else None


def already_backfilled(cur, session_id: str) -> bool:
    """Skip sessions that already have telemetry data (safe to re-run)."""
    cur.execute(
        "SELECT COUNT(*) FROM f1_telemetry_summary WHERE session_id = %s",
        (str(session_id),)
    )
    return cur.fetchone()[0] > 0


def backfill_session(conn, year: int, event_name: str,
                     session_type: str, dry_run: bool):
    cur = conn.cursor()
    try:
        db_type    = SESSION_TYPE_MAP.get(session_type, session_type)
        session_id = get_session_id(cur, year, event_name, db_type)

        if not session_id:
            print(f"  ⚠  {session_type}: no session_id in DB — skipping")
            return

        if not dry_run and already_backfilled(cur, session_id):
            print(f"  ↩  {session_type}: already in DB — skipping")
            return

        print(f"  Loading {session_type}...", end=" ", flush=True)
        _rate_check()

        session = fastf1.get_session(year, event_name, session_type)
        session.load(
            telemetry=(session_type in ('R', 'Q')),
            weather=False,
            messages=(session_type == 'R')
        )

        if dry_run:
            print("DRY RUN — skipping inserts")
            return

        # Map FastF1 driver number → 3-letter code (matches f1_drivers.driver_id)
        num_to_code = {}
        if hasattr(session, 'results') and not session.results.empty:
            for _, row in session.results.iterrows():
                num  = str(row.get('DriverNumber', ''))
                code = str(row.get('Abbreviation', ''))
                if num and code:
                    num_to_code[num] = code

        # ── Telemetry summary ────────────────────────────────────────────────
        if session_type in ('R', 'Q') and not session.laps.empty:
            for drv in session.drivers:
                drv_code = num_to_code.get(str(drv), str(drv))
                try:
                    drv_laps = session.laps.pick_driver(drv)
                    if drv_laps.empty:
                        continue
                    fast_lap = drv_laps.pick_fastest()
                    if fast_lap is None:
                        continue
                    lap_time = fast_lap['LapTime']
                    if str(lap_time) in ('NaT', 'None', ''):
                        continue
                    tel = fast_lap.get_telemetry()
                    if tel.empty or 'Speed' not in tel.columns:
                        continue
                    # Savepoint per driver — a FK or constraint failure on one
                    # driver must not abort the whole transaction for the rest
                    cur.execute("SAVEPOINT sp_tel")
                    cur.execute("""
                        INSERT INTO f1_telemetry_summary
                        (session_id, driver_id, avg_speed, top_speed, avg_throttle,
                         avg_brake, avg_rpm, peak_rpm, fastest_lap_time)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (session_id, driver_id) DO NOTHING
                    """, (
                        str(session_id), drv_code,
                        float(tel['Speed'].mean()),    float(tel['Speed'].max()),
                        float(tel['Throttle'].mean()), float(tel['Brake'].mean()),
                        float(tel['RPM'].mean()),      float(tel['RPM'].max()),
                        str(lap_time)
                    ))
                    cur.execute("RELEASE SAVEPOINT sp_tel")
                except Exception as e:
                    cur.execute("ROLLBACK TO SAVEPOINT sp_tel")
                    print(f"\n  [SKIP tel {drv_code}]: {e}", flush=True)

        # ── Stints ───────────────────────────────────────────────────────────
        if not session.laps.empty:
            stints_df = (session.laps[['Driver', 'Stint', 'Compound', 'LapNumber']]
                         .drop_duplicates())
            for (drv_id, stint_num), grp in stints_df.groupby(['Driver', 'Stint']):
                try:
                    compound  = grp.iloc[0]['Compound']
                    start_lap = int(grp['LapNumber'].min())
                    end_lap   = int(grp['LapNumber'].max())
                    cur.execute("SAVEPOINT sp_stint")
                    cur.execute("""
                        INSERT INTO f1_stints
                        (session_id, driver_id, stint_number, compound,
                         start_lap, end_lap, lap_count)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (session_id, driver_id, stint_number) DO NOTHING
                    """, (str(session_id), drv_id, int(stint_num), compound,
                          start_lap, end_lap, end_lap - start_lap + 1))
                    cur.execute("RELEASE SAVEPOINT sp_stint")
                except Exception:
                    cur.execute("ROLLBACK TO SAVEPOINT sp_stint")

        # ── Lap summary ──────────────────────────────────────────────────────
        if not session.laps.empty:
            for _, lap in session.laps.iterrows():
                try:
                    cur.execute("""
                        INSERT INTO f1_lap_summary
                        (session_id, driver_id, lap_number, lap_time,
                         sector1_time, sector2_time, sector3_time,
                         is_valid, pit_in_lap, pit_out_lap, compound, tyre_age)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (session_id, driver_id, lap_number) DO NOTHING
                    """, (
                        str(session_id),
                        lap.get('Driver'),
                        lap.get('LapNumber'),
                        str(lap.get('LapTime', '')),
                        str(lap.get('Sector1Time', '')),
                        str(lap.get('Sector2Time', '')),
                        str(lap.get('Sector3Time', '')),
                        bool(lap.get('IsAccurate', False)),
                        lap.get('PitInTime') is not None and str(lap.get('PitInTime', '')) != 'NaT',
                        lap.get('PitOutTime') is not None and str(lap.get('PitOutTime', '')) != 'NaT',
                        lap.get('Compound'),
                        int(lap.get('TyreLife', 0) or 0),
                    ))
                except Exception:
                    pass

        # ── Race control messages ────────────────────────────────────────────
        if session_type == 'R':
            try:
                rc = session.race_control_messages
                if rc is not None and len(rc) > 0:
                    for _, msg in rc.iterrows():
                        cur.execute("""
                            INSERT INTO f1_race_control
                            (session_id, lap_number, timestamp,
                             flag_type, sector, message)
                            VALUES (%s, %s, %s, %s, %s, %s)
                        """, (
                            str(session_id),
                            int(msg.get('Lap', 0) or 0),
                            msg.get('Time'),
                            msg.get('Flag'),
                            msg.get('Sector'),
                            str(msg.get('Message', ''))
                        ))
            except Exception:
                pass

        conn.commit()
        print("✓")

    except Exception as e:
        conn.rollback()
        print(f"✗ {e}")
    finally:
        cur.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--seasons', nargs='+', type=int,
                        default=list(range(2018, 2026)),
                        help='Seasons to backfill e.g. --seasons 2023 2024')
    parser.add_argument('--dry-run', action='store_true',
                        help='Load FastF1 data but skip DB inserts')
    args = parser.parse_args()

    print(f"Starting backfill — seasons: {args.seasons}")
    print(f"Cache:    {CACHE_DIR}")
    print(f"DB:       {os.getenv('ALLOYDB_HOST')}:{os.getenv('ALLOYDB_PORT', '5433')}")
    print(f"Dry run:  {args.dry_run}")
    print(f"Rate cap: {RATE_LIMIT} calls/hour (auto-pauses at limit)\n")

    conn = get_conn()

    for year in args.seasons:
        print(f"\n{'='*50}")
        print(f"SEASON {year}")
        print(f"{'='*50}")
        try:
            schedule = fastf1.get_event_schedule(year, include_testing=False)
        except Exception as e:
            print(f"  ⚠ Could not fetch {year} schedule: {e}")
            continue

        for _, event in schedule.iterrows():
            event_name = event['EventName']
            print(f"\n{year} {event_name}")
            for session_type in ['R', 'Q']:
                backfill_session(conn, year, event_name,
                                 session_type, args.dry_run)

    conn.close()
    print(f"\nBackfill complete — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


if __name__ == "__main__":
    main()
