"""
Download FastF1 data to local CSV files for inspection and quality analysis.

Saves to data/ folder:
  data/telemetry_summary.csv  — fastest-lap stats per driver per session
  data/stints.csv             — pit stop / tyre stint data per driver per session
  data/lap_summary.csv        — lap-by-lap times and validity
  data/race_control.csv       — flags, safety car, penalty messages

Usage:
    python scripts/download_fastf1_to_csv.py [--seasons 2023 2024] [--max-races 5]

--max-races limits races per season (useful for a quick sample run).
Omit to download everything.
"""

import os
import time
import argparse
import fastf1
import pandas as pd
from pathlib import Path

CACHE_DIR = os.getenv("FASTF1_CACHE_DIR", "/tmp/fastf1_cache")
DATA_DIR  = Path(__file__).parent.parent / "data"

os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(DATA_DIR,  exist_ok=True)
fastf1.Cache.enable_cache(CACHE_DIR)

# ── Rate limiter (FastF1 API: 500 calls/hour) ─────────────────────────────────
_RATE_LIMIT    = 450          # stay under the 500/h cap
_call_count    = 0
_window_start  = time.time()

def _rate_check():
    """Block if approaching the hourly API call limit, then reset."""
    global _call_count, _window_start
    _call_count += 1
    elapsed = time.time() - _window_start
    if _call_count >= _RATE_LIMIT:
        if elapsed < 3600:
            wait = int(3600 - elapsed) + 10
            print(f"\n⏸  Rate limit reached ({_call_count} calls). "
                  f"Sleeping {wait}s...", flush=True)
            time.sleep(wait)
        _call_count    = 0
        _window_start  = time.time()
        print("▶  Resuming.\n", flush=True)


def safe_str(val) -> str:
    s = str(val)
    return "" if s in ("NaT", "None", "nan") else s


def process_session(year: int, event_name: str, session_type: str,
                    tel_rows: list, stint_rows: list,
                    lap_rows: list, rc_rows: list) -> bool:
    label = f"{year} {event_name} {session_type}"
    print(f"  Loading {session_type}...", end=" ", flush=True)
    _rate_check()

    try:
        session = fastf1.get_session(year, event_name, session_type)
        session.load(
            telemetry=(session_type in ("R", "Q")),
            weather=False,
            messages=(session_type == "R"),
        )
        _ = session.laps  # raises DataNotLoadedError if session data unavailable
    except fastf1.exceptions.DataNotLoadedError:
        print("SKIP (data not yet available)")
        return False
    except Exception as e:
        print(f"LOAD ERROR: {e}")
        return False

    # ── Build driver number → code mapping ───────────────────────────────────
    num_to_code = {}
    if not session.results.empty:
        for _, row in session.results.iterrows():
            num  = str(row.get("DriverNumber", ""))
            code = str(row.get("Abbreviation", ""))
            if num and code:
                num_to_code[num] = code

    counts = {"tel": 0, "stints": 0, "laps": 0, "rc": 0,
              "skip_no_lap": 0, "skip_no_fastest": 0,
              "skip_nat": 0, "skip_empty_tel": 0}

    # ── Telemetry summary ─────────────────────────────────────────────────────
    if session_type in ("R", "Q") and not session.laps.empty:
        for drv in session.drivers:
            drv_code = num_to_code.get(str(drv), str(drv))
            try:
                drv_laps = session.laps.pick_driver(drv)
                if drv_laps.empty:
                    counts["skip_no_lap"] += 1; continue

                fast_lap = drv_laps.pick_fastest()
                if fast_lap is None:
                    counts["skip_no_fastest"] += 1; continue

                lap_time = fast_lap["LapTime"]
                if safe_str(lap_time) == "":
                    counts["skip_nat"] += 1; continue

                tel = fast_lap.get_telemetry()
                if tel.empty or "Speed" not in tel.columns:
                    counts["skip_empty_tel"] += 1; continue

                tel_rows.append({
                    "year":            year,
                    "event_name":      event_name,
                    "session_type":    session_type,
                    "driver_code":     drv_code,
                    "driver_number":   drv,
                    "avg_speed":       round(float(tel["Speed"].mean()), 2),
                    "top_speed":       round(float(tel["Speed"].max()), 2),
                    "avg_throttle":    round(float(tel["Throttle"].mean()), 2),
                    "avg_brake":       round(float(tel["Brake"].mean()), 2),
                    "avg_rpm":         round(float(tel["RPM"].mean())),
                    "peak_rpm":        round(float(tel["RPM"].max())),
                    "fastest_lap_time": safe_str(lap_time),
                })
                counts["tel"] += 1
            except Exception as e:
                print(f"\n    [tel skip {drv_code}]: {e}", end="", flush=True)

    # ── Stints ────────────────────────────────────────────────────────────────
    if not session.laps.empty:
        try:
            stints_df = (
                session.laps[["Driver", "Stint", "Compound", "LapNumber"]]
                .drop_duplicates()
            )
            for (drv_id, stint_num), grp in stints_df.groupby(["Driver", "Stint"]):
                compound  = str(grp.iloc[0]["Compound"])
                start_lap = int(grp["LapNumber"].min())
                end_lap   = int(grp["LapNumber"].max())
                stint_rows.append({
                    "year":         year,
                    "event_name":   event_name,
                    "session_type": session_type,
                    "driver_code":  drv_id,
                    "stint_number": int(stint_num),
                    "compound":     compound,
                    "start_lap":    start_lap,
                    "end_lap":      end_lap,
                    "lap_count":    end_lap - start_lap + 1,
                })
                counts["stints"] += 1
        except Exception as e:
            print(f"\n    [stints error]: {e}", end="", flush=True)

    # ── Lap summary ───────────────────────────────────────────────────────────
    if not session.laps.empty:
        for _, lap in session.laps.iterrows():
            try:
                lap_rows.append({
                    "year":          year,
                    "event_name":    event_name,
                    "session_type":  session_type,
                    "driver_code":   str(lap.get("Driver", "")),
                    "lap_number":    lap.get("LapNumber"),
                    "lap_time":      safe_str(lap.get("LapTime")),
                    "sector1_time":  safe_str(lap.get("Sector1Time")),
                    "sector2_time":  safe_str(lap.get("Sector2Time")),
                    "sector3_time":  safe_str(lap.get("Sector3Time")),
                    "is_valid":      bool(lap.get("IsAccurate", False)),
                    "pit_in_lap":    lap.get("PitInTime") is not None and safe_str(lap.get("PitInTime")) != "",
                    "pit_out_lap":   lap.get("PitOutTime") is not None and safe_str(lap.get("PitOutTime")) != "",
                    "compound":      str(lap.get("Compound", "")),
                    "tyre_age":      int(lap.get("TyreLife", 0) or 0),
                })
                counts["laps"] += 1
            except Exception:
                pass

    # ── Race control ──────────────────────────────────────────────────────────
    if session_type == "R":
        try:
            rc = session.race_control_messages
            if rc is not None and len(rc) > 0:
                for _, msg in rc.iterrows():
                    rc_rows.append({
                        "year":         year,
                        "event_name":   event_name,
                        "lap_number":   int(msg.get("Lap", 0) or 0),
                        "flag_type":    str(msg.get("Flag", "")),
                        "sector":       msg.get("Sector"),
                        "message":      str(msg.get("Message", "")),
                    })
                    counts["rc"] += 1
        except Exception:
            pass

    print(
        f"tel={counts['tel']} "
        f"[skip: no_lap={counts['skip_no_lap']} no_fastest={counts['skip_no_fastest']} "
        f"nat={counts['skip_nat']} empty_tel={counts['skip_empty_tel']}]  "
        f"stints={counts['stints']}  laps={counts['laps']}  rc={counts['rc']}",
        flush=True,
    )
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seasons",   nargs="+", type=int, default=[2024, 2025])
    parser.add_argument("--max-races", type=int,  default=None,
                        help="Max races per season (None = all)")
    parser.add_argument("--resume", action="store_true",
                        help="Skip races already in telemetry_summary.csv")
    args = parser.parse_args()

    # Load already-downloaded keys for resume mode
    done_keys: set = set()
    if args.resume:
        tel_path = DATA_DIR / "telemetry_summary.csv"
        if tel_path.exists():
            existing = pd.read_csv(tel_path)
            done_keys = set(zip(existing["year"].astype(str),
                                existing["event_name"],
                                existing["session_type"]))
            print(f"Resume mode: {len(done_keys)} session keys already downloaded")

    tel_rows   = []
    stint_rows = []
    lap_rows   = []
    rc_rows    = []

    for year in args.seasons:
        print(f"\n{'='*55}")
        print(f"SEASON {year}")
        print(f"{'='*55}")

        try:
            schedule = fastf1.get_event_schedule(year, include_testing=False)
        except Exception as e:
            print(f"  Schedule error: {e}")
            continue

        races = list(schedule.iterrows())
        if args.max_races:
            races = races[:args.max_races]

        for _, event in races:
            event_name = event["EventName"]
            print(f"\n{year} {event_name}")
            for session_type in ["R", "Q"]:
                key = (str(year), event_name, session_type)
                if args.resume and key in done_keys:
                    print(f"  ↩  {session_type}: already downloaded — skipping")
                    continue
                process_session(
                    year, event_name, session_type,
                    tel_rows, stint_rows, lap_rows, rc_rows,
                )

    # ── Save to CSV ───────────────────────────────────────────────────────────
    print(f"\n{'='*55}")
    print("Saving CSV files to data/")

    files = {
        "telemetry_summary.csv": tel_rows,
        "stints.csv":            stint_rows,
        "lap_summary.csv":       lap_rows,
        "race_control.csv":      rc_rows,
    }

    for fname, rows in files.items():
        if not rows:
            print(f"  {fname}: 0 new rows — skipped")
            continue
        df   = pd.DataFrame(rows)
        path = DATA_DIR / fname
        # Append if file exists (resume mode), otherwise overwrite
        if args.resume and path.exists():
            df.to_csv(path, mode="a", header=False, index=False)
            print(f"  {fname}: +{len(df):,} rows appended → {path}")
        else:
            df.to_csv(path, index=False)
            print(f"  {fname}: {len(df):,} rows → {path}")

    print("\nDone.")


if __name__ == "__main__":
    main()
