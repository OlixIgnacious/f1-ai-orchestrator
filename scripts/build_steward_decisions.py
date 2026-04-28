"""
Build f1_decisions table from OpenF1 race control messages (2023-2025).
Filters for penalty/investigation/reprimand/DSQ messages, maps to article refs,
generates embeddings, and inserts into AlloyDB.

Usage:
    python scripts/build_steward_decisions.py [--years 2023 2024 2025]
"""

import os
import re
import sys
import time
import argparse
import requests
import psycopg2
from dotenv import load_dotenv

load_dotenv()

PROJECT_ID     = os.getenv("GOOGLE_CLOUD_PROJECT")
VERTEX_LOC     = os.getenv("VERTEX_AI_LOCATION", "us-central1")
OPENF1_BASE    = "https://api.openf1.org/v1"

# Keywords that identify steward-relevant messages
DECISION_KEYWORDS = [
    'PENALTY', 'INVESTIGATION', 'REPRIMAND', 'DISQUALIF',
    'DRIVE THROUGH', 'STOP AND GO', 'TIME PENALTY', 'GRID PENALTY',
    'STEWARDS:', 'UNDER INVESTIGATION', 'NO FURTHER ACTION',
    'NO FURTHER INVESTIGATION',
]

# Map incident keywords → FIA article references (2025 Sporting Regs)
ARTICLE_MAP = {
    'UNSAFE RELEASE':              'B34.13',
    'CAUSING A COLLISION':         'B2 Appendix L Ch.IV Art.2',
    'TRACK LIMITS':                'B33.3',
    'SPEEDING IN PIT LANE':        'B34.7',
    'IGNORING BLUE FLAGS':         'B16.1',
    'WEAVING':                     'B2 Appendix L Ch.IV Art.2',
    'FORCING ANOTHER DRIVER OFF':  'B2 Appendix L Ch.IV Art.2',
    'YELLOW FLAG':                 'B44.1',
    'SAFETY CAR':                  'B57',
    'FALSE START':                 'B36.8',
    'OVERTAKING UNDER RED':        'B57.9',
    'PIT LANE ENTRY':              'B34.4',
    'RECONNAISSANCE LAP':          'B36.1',
    'OVERTAKING':                  'B2 Appendix L Ch.IV',
}


def get_embed_model():
    import vertexai
    from vertexai.language_models import TextEmbeddingModel
    vertexai.init(project=PROJECT_ID, location=VERTEX_LOC)
    return TextEmbeddingModel.from_pretrained("text-embedding-005")


def embed_text(model, text: str) -> list[float]:
    result = model.get_embeddings([text[:3000]])
    return result[0].values


def get_conn():
    return psycopg2.connect(
        host=os.getenv("ALLOYDB_HOST", "127.0.0.1"),
        port=os.getenv("ALLOYDB_PORT", "5432"),
        database=os.getenv("ALLOYDB_DATABASE", "f1db"),
        user=os.getenv("ALLOYDB_USER", "postgres"),
        password=os.getenv("ALLOYDB_PASSWORD")
    )


def fetch_json(url: str, params: dict = None) -> list:
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def parse_message(msg: str) -> dict:
    """
    Parse a race control message into structured fields.
    Examples:
      "CAR 1 (VER) FIVE SECOND TIME PENALTY - UNSAFE RELEASE"
      "CAR 44 (HAM) UNDER INVESTIGATION AFTER THE RACE - CAUSING A COLLISION"
      "FIA STEWARDS: TURN 1 INCIDENT INVOLVING CARS 18 AND 27 - NO FURTHER INVESTIGATION"
    """
    upper = msg.upper()

    # Extract driver code
    driver_match = re.search(r'\(([A-Z]{2,3})\)', msg)
    driver_code  = driver_match.group(1) if driver_match else None

    # Determine penalty type
    penalty = ""
    if "FIVE SECOND" in upper or "5 SECOND" in upper:
        penalty = "5 second time penalty"
    elif "TEN SECOND" in upper or "10 SECOND" in upper:
        penalty = "10 second time penalty"
    elif "DRIVE THROUGH" in upper:
        penalty = "Drive-through penalty"
    elif "STOP AND GO" in upper:
        penalty = "Stop-and-go penalty"
    elif "GRID PENALTY" in upper:
        m = re.search(r'(\d+)\s*GRID', upper)
        penalty = f"{m.group(1)}-place grid penalty" if m else "Grid penalty"
    elif "REPRIMAND" in upper:
        penalty = "Reprimand"
    elif "DISQUALIF" in upper:
        penalty = "Disqualification"
    elif "NO FURTHER INVESTIGATION" in upper or "NO FURTHER ACTION" in upper:
        penalty = "No further action"
    elif "UNDER INVESTIGATION" in upper:
        penalty = "Under investigation"
    elif "WARNING" in upper:
        penalty = "Warning"

    # Determine ruling
    if "NO FURTHER" in upper:
        ruling = "No further action"
    elif "PENALTY" in upper and "UNDER INVESTIGATION" not in upper:
        ruling = "Penalty imposed"
    elif "REPRIMAND" in upper:
        ruling = "Reprimand"
    elif "DISQUALIF" in upper:
        ruling = "Disqualification"
    elif "UNDER INVESTIGATION" in upper:
        ruling = "Referred to stewards"
    else:
        ruling = "Noted"

    # Find incident description (text after " - " separator)
    incident = ""
    if " - " in msg:
        incident = msg.split(" - ", 1)[-1].strip()
    elif "INVOLVING" in upper:
        m = re.search(r'INVOLVING(.+)', msg, re.IGNORECASE)
        incident = m.group(1).strip() if m else msg

    # Map to article reference
    article_ref = ""
    for keyword, article in ARTICLE_MAP.items():
        if keyword in upper:
            article_ref = article
            break

    return {
        "driver_code": driver_code,
        "incident":    incident or msg,
        "ruling":      ruling,
        "penalty":     penalty,
        "article_ref": article_ref,
    }


def fetch_driver_map(session_key: int) -> dict:
    """Returns {driver_number: {code, team}} for a session."""
    drivers = fetch_json(f"{OPENF1_BASE}/drivers", {"session_key": session_key})
    return {
        str(d.get("driver_number", "")): {
            "code": d.get("name_acronym", ""),
            "team": d.get("team_name", ""),
        }
        for d in drivers
    }


def process_session(session: dict, embed_model, conn) -> int:
    session_key = session["session_key"]
    year        = session["year"]
    race        = session.get("location", "") + " Grand Prix"

    try:
        messages   = fetch_json(f"{OPENF1_BASE}/race_control", {"session_key": session_key})
        driver_map = fetch_driver_map(session_key)
    except Exception as e:
        print(f"    ⚠ fetch error: {e}")
        return 0

    cur     = conn.cursor()
    inserted = 0

    for msg_data in messages:
        msg = msg_data.get("message", "")
        if not msg:
            continue
        upper = msg.upper()
        if not any(kw in upper for kw in DECISION_KEYWORDS):
            continue

        parsed = parse_message(msg)

        # Resolve driver info from number if code not in message
        drv_num  = str(msg_data.get("driver_number") or "")
        drv_info = driver_map.get(drv_num, {})
        code     = parsed["driver_code"] or drv_info.get("code", "")
        team     = drv_info.get("team", "")

        # Build full content string for embedding
        content = (
            f"Race: {race} {year}\n"
            f"Driver: {code} ({team})\n"
            f"Incident: {parsed['incident']}\n"
            f"Ruling: {parsed['ruling']}\n"
            f"Penalty: {parsed['penalty']}\n"
            f"Article: {parsed['article_ref']}\n"
            f"Full message: {msg}"
        )

        try:
            embedding = embed_text(embed_model, content)
            cur.execute("""
                INSERT INTO f1_decisions
                (race, year, driver_id, team_id, incident, ruling,
                 penalty, article_ref, content, embedding)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT DO NOTHING
            """, (
                race, year, code, team,
                parsed["incident"], parsed["ruling"],
                parsed["penalty"], parsed["article_ref"],
                content, embedding
            ))
            inserted += 1
        except Exception as e:
            print(f"      ⚠ insert error: {e}")

    conn.commit()
    cur.close()
    return inserted


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", nargs="+", type=int,
                        default=[2023, 2024, 2025])
    args = parser.parse_args()

    print(f"Building steward decisions — years: {args.years}")
    embed_model = get_embed_model()
    conn        = get_conn()
    grand_total = 0

    for year in args.years:
        print(f"\n── {year} ───────────────────────────────────────")
        sessions = fetch_json(f"{OPENF1_BASE}/sessions",
                              {"year": year, "session_name": "Race"})
        print(f"  {len(sessions)} race sessions found")

        for session in sessions:
            race = session.get("location", "Unknown") + " GP"
            print(f"  {race}...", end=" ", flush=True)
            n = process_session(session, embed_model, conn)
            print(f"{n} decisions")
            grand_total += n
            time.sleep(0.3)  # be polite to OpenF1 API

    conn.close()
    print(f"\nDone — {grand_total} steward decisions inserted into f1_decisions")
    print("Next: run 'python scripts/run_migrations.py --indexes' to create ScaNN index")


if __name__ == "__main__":
    main()
