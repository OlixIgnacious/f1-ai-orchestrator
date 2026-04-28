import os
import logging
import fastf1
import urllib.parse
from datetime import datetime, timedelta
from psycopg2 import pool as pg_pool
from google.adk.agents import LlmAgent
from google.adk.models.google_llm import Gemini
from google.genai import types
from google.adk.tools import ToolContext
from google.adk.agents.callback_context import CallbackContext
from googleapiclient.discovery import build
from dotenv import load_dotenv
from .schema import F1_TABLE_METADATA

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

load_dotenv()


def _log_agent_start(callback_context: CallbackContext):
    """Log when any agent starts processing — shows agent name in Cloud Run logs."""
    logger.info("[AGENT: %s] started processing", callback_context.agent_name)
    return None  # None = continue normally


# ─────────────────────────────────────────────
# 1. SYSTEM INITIALISATION
# ─────────────────────────────────────────────
CACHE_DIR = os.getenv("FASTF1_CACHE_DIR", '/tmp/fastf1_cache')
os.makedirs(CACHE_DIR, exist_ok=True)
fastf1.Cache.enable_cache(CACHE_DIR)

REGION   = os.getenv("REGION")
CLUSTER  = os.getenv("ALLOYDB_CLUSTER")
INSTANCE = os.getenv("ALLOYDB_INSTANCE")
# Retry on 429 with exponential backoff: 3 attempts, 2s → 8s → 32s
_RETRY = types.HttpRetryOptions(
    attempts=3,
    initialDelay=2.0,
    maxDelay=32.0,
    expBase=4.0,
    httpStatusCodes=[429, 503],
)
MODEL = Gemini(model="gemini-2.5-flash", retry_options=_RETRY)

# ─────────────────────────────────────────────
# 2. DATABASE POOL + HELPERS
# ─────────────────────────────────────────────
_pool = None

def _get_pool():
    global _pool
    if _pool is None:
        _pool = pg_pool.ThreadedConnectionPool(
            minconn=2,
            maxconn=10,
            host=os.getenv("ALLOYDB_HOST", "127.0.0.1"),
            port=os.getenv("ALLOYDB_PORT", "5433"),
            database=os.getenv("ALLOYDB_DATABASE", "f1db"),
            user=os.getenv("ALLOYDB_USER", "postgres"),
            password=os.getenv("ALLOYDB_PASSWORD")
        )
    return _pool


BLOCKED_SQL = ['DROP', 'DELETE', 'INSERT', 'UPDATE', 'ALTER', 'TRUNCATE', 'CREATE', 'GRANT']

def _validate_sql(sql: str) -> bool:
    upper = sql.upper()
    return not any(kw in upper for kw in BLOCKED_SQL)


def _query_raw(sql: str, params: list = None) -> str:
    """Parameterised DB query — used internally by tools."""
    conn = _get_pool().getconn()
    try:
        cur = conn.cursor()
        if params:
            cur.execute(sql, params)
        else:
            cur.execute(sql)
        rows = cur.fetchall()
        colnames = [desc[0] for desc in cur.description]
        return f"Columns: {colnames}\nData: {rows}"
    except Exception as e:
        return f"Database Error: {str(e)}"
    finally:
        _get_pool().putconn(conn)


def _to_md_table(raw: str, title: str = "") -> str:
    """Convert _query_raw output (Columns/Data format) to a clean markdown table.
    Preserves 'Data: []' and 'Database Error' signals unchanged for fallback checks."""
    if not raw or "Database Error" in raw:
        return raw
    if "Data: []" in raw:
        return "Data: []"
    try:
        import ast as _ast
        cols_str, data_str = raw.split("\nData: ", 1)
        colnames = _ast.literal_eval(cols_str.replace("Columns: ", "").strip())
        rows     = _ast.literal_eval(data_str.strip())
        if not rows:
            return "Data: []"
        str_rows = [[str(v) if v is not None else "—" for v in row] for row in rows]
        header = "| " + " | ".join(str(c) for c in colnames) + " |"
        sep    = "|" + "|".join(["---"] * len(colnames)) + "|"
        body   = "\n".join("| " + " | ".join(r) + " |" for r in str_rows)
        table  = f"{header}\n{sep}\n{body}"
        return f"{title}\n\n{table}" if title else table
    except Exception:
        return raw  # fallback to raw if parsing fails


_SESSION_TYPE_DB = {
    'R': 'Race', 'Q': 'Qualifying', 'S': 'Sprint',
    'FP1': 'Practice 1', 'FP2': 'Practice 2', 'FP3': 'Practice 3',
}

def _get_session_id(year: int, gp_name: str, session_type: str):
    """Look up the f1_sessions UUID for a given race/session — used for write-backs."""
    db_type = _SESSION_TYPE_DB.get(session_type, session_type)
    conn = _get_pool().getconn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM f1_sessions WHERE season=%s AND race_name ILIKE %s AND session_type=%s LIMIT 1",
            (year, f"%{gp_name}%", db_type)
        )
        row = cur.fetchone()
        return str(row[0]) if row else None
    except Exception:
        return None
    finally:
        _get_pool().putconn(conn)


# ─────────────────────────────────────────────
# 3. SHARED PROMPT CONTEXT
# ─────────────────────────────────────────────
SQL_GUIDELINES = """
SQL Best Practices for F1 Data (verified against live AlloyDB schema):

1. DRIVER_ID IS A 3-LETTER CODE — CRITICAL:
   driver_id in ALL tables (f1_results, f1_stints, f1_telemetry_summary, etc.) = 3-letter code
   e.g. 'VER', 'HAM', 'LEC'. It is TEXT, not a UUID. Join: r.driver_id = d.driver_id (both text codes).
   To filter by driver: WHERE r.driver_id = 'VER'  or  WHERE d.full_name ILIKE '%Verstappen%'

2. SESSION_TYPE VALUES — CRITICAL:
   Only 'Race' and 'Qualifying' exist in f1_sessions. No 'Sprint', 'FP1', 'R', 'Q'.
   CORRECT:   s.session_type = 'Race'
   INCORRECT: s.session_type = 'R'

3. RACE NAME — CRITICAL: ALWAYS use ILIKE with wildcards, NEVER exact match.
   CORRECT:   s.race_name ILIKE '%Bahrain%'
   INCORRECT: s.race_name = 'Bahrain Grand Prix'
   DB stores full multilingual official names e.g. 'FORMULA 1 ARAMCO GRAN PREMIO DE ESPAÑA 2024'.

4. STANDINGS — CRITICAL:
   Table: f1_standings. Columns: standing_type, entity_id, position, points, wins, season, round.
   standing_type = 'driver' (lowercase) or 'constructor' (lowercase).
   entity_id for 'driver'      = driver code e.g. 'VER'
   entity_id for 'constructor' = team_id    e.g. 'mercedes', 'red_bull'
   Join driver standings:      JOIN f1_drivers d ON d.driver_id = fs.entity_id
   Join constructor standings: JOIN f1_teams   t ON t.team_id   = fs.entity_id
   PREFERRED: use get_f1_standings(year) tool — it handles all joins and formatting automatically.

5. "TOP N" AGGREGATES — CRITICAL:
   "Top N penalties", "most common incidents", "most given penalty" = FREQUENCY query.
   CORRECT:   SELECT penalty, COUNT(*) AS times_given FROM f1_decisions
              WHERE year = 2025 GROUP BY penalty ORDER BY times_given DESC LIMIT 5
   INCORRECT: SELECT * FROM f1_decisions WHERE year = 2025 LIMIT 5

6. LIMITS: Apply LIMIT 10 unless the user asks for a full list (standings = no limit).
7. ORDERING: Order results logically (position ASC, points DESC, date DESC).
8. ON ERROR / EMPTY RESULTS: If query_f1_db returns "Database Error" or "Data: []",
   do NOT retry with a different table name. Instead fall back to fetch_fastf1_live_data
   or the appropriate schedule/standings tool.
"""


# ─────────────────────────────────────────────
# 4. TOOLS
# ─────────────────────────────────────────────

def get_temporal_context(tool_context: ToolContext) -> str:
    """
    Returns today's date and the current F1 season year.
    Always call this first to ensure accurate temporal reasoning.
    Any race on or before today has already occurred; races after today are upcoming.
    """
    now = datetime.now()
    date_str = now.strftime('%Y-%m-%d')
    tool_context.state["current_date"] = date_str
    return (
        f"Today is {date_str}. Current F1 season: {now.year}. "
        f"Any race on or before {date_str} has already occurred. "
        f"Only races scheduled after {date_str} are upcoming."
    )


def query_f1_db(sql_query: str, tool_context: ToolContext = None) -> str:  # noqa: ARG001
    """
    F1_TABLE_METADATA is the source of truth for the current AlloyDB schema. Always refer to it when writing SQL queries.
    DO NOT assume any schema details that are not explicitly listed in F1_TABLE_METADATA. If you need a specific data point, check if it's in the metadata and which table/column it belongs to before writing your query.
    Executes a SELECT query against the F1 AlloyDB (f1db).
    Use for driver stats, historical race results, standings, circuits, and session data.
    Only SELECT queries are permitted — write operations will be rejected.
    SQL_GUIDELINES provides best practices and common pitfalls to avoid when writing queries against the F1 database.
    DATABASE: f1db (AlloyDB / PostgreSQL)
    This schema is the AUTHORITATIVE source of truth. Do not invent table or column names.
    
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    DATA AVAILABILITY RULE
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    PAST races (date ≤ today) → query AlloyDB tables below.
    FUTURE races (date > today) → NOT in the database.
      Use get_f1_schedule(year) to find upcoming race name/date/location.
      Use get_f1_standings(year) + query_f1_db on PAST races for prediction context.
    
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    VALID TABLES — only these exist. Any other name will fail.
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    f1_drivers        — driver_id (TEXT, PK = 3-letter code e.g. 'VER'), code (TEXT, same as driver_id),
                        full_name, given_name, family_name, nationality, number, updated_at
    
    f1_teams          — team_id (TEXT, PK e.g. 'mercedes', 'red_bull', 'ferrari'),
                        name, nationality, updated_at
    
    f1_circuits       — circuit_id (TEXT, PK), name, locality, country, lat, lng, updated_at
    
    f1_sessions       — id (UUID, PK), season (INT), round (INT), session_type (TEXT), race_name (TEXT),
                        circuit_id (TEXT → f1_circuits), date (DATE)
                        session_type values: 'Race' | 'Qualifying'  ← only these two
    
    f1_results        — id (UUID, PK), session_id (UUID → f1_sessions), driver_id (TEXT → f1_drivers),
                        team_id (TEXT → f1_teams), grid (INT), position (INT), classified (BOOL),
                        status (TEXT), points (FLOAT), fastest_lap (BOOL)
    
    f1_standings      — id (UUID), season (INT), round (INT), standing_type (TEXT), entity_id (TEXT),
                        position (INT), points (FLOAT), wins (INT)
                        standing_type values: 'driver' | 'constructor'  ← lowercase, only these two
                        entity_id for 'driver'      = driver code e.g. 'VER', 'HAM'
                        entity_id for 'constructor' = team_id  e.g. 'mercedes', 'red_bull'
    
    f1_telemetry_summary — id (UUID), session_id (UUID → f1_sessions), driver_id (TEXT → f1_drivers),
                           avg_speed, top_speed, avg_throttle, avg_brake, avg_rpm, peak_rpm (all FLOAT),
                           fastest_lap_time (INTERVAL), created_at
    
    f1_stints         — id (UUID), session_id (UUID → f1_sessions), driver_id (TEXT → f1_drivers),
                        stint_number (INT), compound (TEXT), start_lap (INT), end_lap (INT), lap_count (INT)
    
    f1_lap_summary    — id (UUID), session_id (UUID → f1_sessions), driver_id (TEXT → f1_drivers),
                        lap_number (INT), lap_time (INTERVAL), sector1_time, sector2_time, sector3_time (INTERVAL),
                        is_valid (BOOL), pit_in_lap (BOOL), pit_out_lap (BOOL), compound (TEXT), tyre_age (INT)
    
    f1_race_control   — id (UUID), session_id (UUID → f1_sessions), lap_number (INT),
                        timestamp (TIMESTAMP), flag_type (TEXT), sector (INT), message (TEXT),
                        driver_id (TEXT)
    
    f1_regulations    — id (UUID), year (INT), reg_type (TEXT), article_number (TEXT), article_title (TEXT),
                        content (TEXT), embedding (VECTOR), source_url, created_at
    
    f1_decisions      — id (UUID), race (TEXT), year (INT), driver_id (TEXT), team_id (TEXT),
                        incident (TEXT), ruling (TEXT), penalty (TEXT), article_ref (TEXT),
                        content (TEXT), embedding (VECTOR), created_at


    """
    print(f"\n[TOOL: query_f1_db] {sql_query[:120]}")
    if not _validate_sql(sql_query):
        return "SQL Error: Only SELECT queries are permitted."
    raw = _query_raw(sql_query)
    # Preserve "Data: []" signal for fallback logic; format everything else
    if "Data: []" in raw or "Database Error" in raw:
        return raw
    return _to_md_table(raw)


def get_f1_schedule(year: int, tool_context: ToolContext = None) -> str:
    """
    Fetches the full F1 race schedule for a given year.
    Use to find upcoming races, confirm Grand Prix names, and identify circuit locations.
    - year: Calendar year e.g. 2025, 2026.
    """
    cache_key = f"schedule_{year}"
    if tool_context and cache_key in tool_context.state:
        print(f"\n[TOOL: get_f1_schedule] Cache hit: {cache_key}")
        return tool_context.state[cache_key]

    print(f"\n[TOOL: get_f1_schedule] Fetching schedule for {year}")
    try:
        schedule = fastf1.get_event_schedule(year)
        overview = schedule[['RoundNumber', 'EventName', 'Location', 'EventDate', 'EventFormat']]
        result = f"F1 Schedule for {year}:\n{overview.to_string(index=False)}"
        if tool_context:
            tool_context.state[cache_key] = result
        return result
    except Exception as e:
        return f"Schedule Error: {str(e)}"


def fetch_fastf1_live_data(year: int, gp_name: str, session_type: str = "R",
                            tool_context: ToolContext = None) -> str:
    """
    Fetches session results from the FastF1 API.
    Use as a fallback when AlloyDB returns empty results, especially for 2025+ data.
    - year: e.g. 2024, 2025.
    - gp_name: e.g. 'Bahrain', 'Saudi Arabia'.
    - session_type: 'R' (Race), 'Q' (Qualifying), 'S' (Sprint), 'FP1', 'FP2', 'FP3'.
    """
    cache_key = f"session_{year}_{gp_name}_{session_type}"
    if tool_context and cache_key in tool_context.state:
        print(f"\n[TOOL: fetch_fastf1_live_data] Cache hit: {cache_key}")
        return tool_context.state[cache_key]

    print(f"\n[TOOL: fetch_fastf1_live_data] Fetching: {year} {gp_name} {session_type}")
    try:
        session = fastf1.get_session(year, gp_name, session_type)
        session.load(telemetry=False, weather=False, messages=False)
        target_cols = ['ClassifiedPosition', 'FullName', 'TeamName', 'Status',
                       'Points', 'GridPosition', 'BestLapTime']
        available_cols = [c for c in target_cols if c in session.results.columns]
        df = session.results[available_cols].fillna("—")
        header  = "| " + " | ".join(available_cols) + " |"
        sep     = "|" + "|".join(["---"] * len(available_cols)) + "|"
        rows_md = "\n".join(
            "| " + " | ".join(str(v) for v in row) + " |"
            for row in df.itertuples(index=False)
        )
        result = f"## Results — {year} {gp_name} ({session_type})\n\n{header}\n{sep}\n{rows_md}"
        if tool_context:
            tool_context.state[cache_key] = result
        return result
    except Exception as e:
        return f"FastF1 Error: {str(e)}"


def get_f1_standings(year: int, tool_context: ToolContext = None) -> str:
    """
    Fetches Drivers' and Constructors' championship standings for a given year.
    Returns two tables: Driver Standings and Constructor Standings.
    - year: The F1 season year e.g. 2024, 2025, 2026.
    """
    cache_key = f"standings_{year}"
    if tool_context and cache_key in tool_context.state:
        print(f"\n[TOOL: get_f1_standings] Cache hit: {cache_key}")
        return tool_context.state[cache_key]

    print(f"\n[TOOL: get_f1_standings] Fetching standings for {year}")
    try:
        driver_sql = """
            SELECT fs.position AS pos, d.full_name AS driver,
                   fs.points AS pts, fs.wins AS wins
            FROM f1_standings fs
            JOIN f1_drivers d ON d.code = fs.entity_id
            WHERE fs.season = %s
              AND fs.standing_type = 'driver'
              AND fs.round = (SELECT max(round) FROM f1_standings WHERE season = %s)
            ORDER BY fs.position ASC
        """
        constructor_sql = """
            SELECT fs.position AS pos, t.name AS constructor,
                   fs.points AS pts, fs.wins AS wins
            FROM f1_standings fs
            JOIN f1_teams t ON t.team_id = fs.entity_id
            WHERE fs.season = %s
              AND fs.standing_type = 'constructor'
              AND fs.round = (SELECT max(round) FROM f1_standings WHERE season = %s)
            ORDER BY fs.position ASC
        """
        driver_res      = _query_raw(driver_sql, [year, year])
        constructor_res = _query_raw(constructor_sql, [year, year])

        if "Database Error" in driver_res and "Database Error" in constructor_res:
            return f"Standings Error: {driver_res}"
        if "Data: []" in driver_res and "Data: []" in constructor_res:
            return f"No standings data found for {year} in the database yet."

        driver_table      = _to_md_table(driver_res,      f"## {year} Driver Championship")
        constructor_table = _to_md_table(constructor_res, f"## {year} Constructor Championship")
        result = f"{driver_table}\n\n{constructor_table}"
        if tool_context:
            tool_context.state[cache_key] = result
        return result
    except Exception as e:
        return f"Standings Error: {str(e)}"


def fetch_f1_telemetry(year: int, gp_name: str, session_type: str, driver_id: str,
                        tool_context: ToolContext = None) -> str:
    """
    Fetches car telemetry (Speed, Throttle, Brake, Gear, RPM) for a driver's fastest lap.
    Queries AlloyDB pre-aggregated stats first; falls back to FastF1 live fetch and
    writes the result back to AlloyDB for future calls.
    Use for performance analysis, cornering efficiency, and driver comparisons.
    - driver_id: 3-letter code e.g. 'VER', 'HAM', or full name.
    """
    cache_key = f"telemetry_{driver_id}_{year}_{gp_name}_{session_type}"
    if tool_context and cache_key in tool_context.state:
        print(f"\n[TOOL: fetch_f1_telemetry] Cache hit: {cache_key}")
        return tool_context.state[cache_key]

    print(f"\n[TOOL: fetch_f1_telemetry] {driver_id} at {gp_name} {year}")

    # ── AlloyDB first ──────────────────────────────────────────────────────────
    db_type = _SESSION_TYPE_DB.get(session_type, session_type)
    db_result = _query_raw("""
        SELECT ts.avg_speed, ts.top_speed, ts.avg_throttle, ts.avg_brake,
               ts.avg_rpm, ts.peak_rpm, ts.fastest_lap_time
        FROM f1_telemetry_summary ts
        JOIN f1_sessions s ON s.id = ts.session_id
        JOIN f1_drivers  d ON d.driver_id = ts.driver_id
        WHERE s.season = %s AND s.race_name ILIKE %s
          AND s.session_type = %s AND d.code = %s
        LIMIT 1
    """, [year, f"%{gp_name}%", db_type, driver_id.upper()])

    if "Data: []" not in db_result and "Database Error" not in db_result:
        # Parse the tuple row into a readable summary for the analysis agent
        try:
            import ast
            data_str  = db_result.split("Data: ")[1]
            row       = ast.literal_eval(data_str)[0]
            avg_s, top_s, avg_t, avg_b, avg_r, pk_r, lap_t = row
            result = (
                f"Telemetry Summary (Pre-aggregated) for {driver_id.upper()} "
                f"at {gp_name} {year} {session_type}:\n"
                f"  avg_speed={round(float(avg_s),1)} km/h  top_speed={round(float(top_s),1)} km/h\n"
                f"  avg_throttle={round(float(avg_t),1)}%  avg_brake={round(float(avg_b),1)}%\n"
                f"  avg_rpm={round(float(avg_r))}  peak_rpm={round(float(pk_r))}\n"
                f"  fastest_lap_time={lap_t}"
            )
            if tool_context:
                tool_context.state[cache_key] = result
            return result
        except Exception:
            pass  # malformed row — fall through to FastF1

    # ── FastF1 fallback + write-back ───────────────────────────────────────────
    try:
        session = fastf1.get_session(year, gp_name, session_type)
        session.load(telemetry=True, weather=False, messages=False)

        target = driver_id
        if len(driver_id) > 3:
            match = session.results[
                session.results['FullName'].str.contains(driver_id, case=False, na=False)
            ]
            if not match.empty:
                target = match.iloc[0]['Abbreviation']

        laps = session.laps.pick_driver(target)
        if laps.empty:
            return f"Telemetry Error: No laps recorded for driver '{target}'."
        fastest = laps.pick_fastest()
        if fastest is None:
            return f"Telemetry Error: Could not identify fastest lap for '{target}'."

        tel = fastest.get_telemetry().iloc[::10, :]
        stats = tel[['Speed', 'Throttle', 'Brake', 'RPM']].agg(['mean', 'max'])
        avg_speed    = round(float(stats.loc['mean', 'Speed']),    1)
        top_speed    = round(float(stats.loc['max',  'Speed']),    1)
        avg_throttle = round(float(stats.loc['mean', 'Throttle']), 1)
        avg_brake    = round(float(stats.loc['mean', 'Brake']),    1)
        avg_rpm      = round(float(stats.loc['mean', 'RPM']))
        peak_rpm     = round(float(stats.loc['max',  'RPM']))
        lap_time_str = str(fastest['LapTime'])

        result = (
            f"Telemetry Summary (Fastest Lap) for {target} "
            f"at {gp_name} {year} {session_type}:\n"
            f"  avg_speed={avg_speed} km/h  top_speed={top_speed} km/h\n"
            f"  avg_throttle={avg_throttle}%  avg_brake={avg_brake}%\n"
            f"  avg_rpm={avg_rpm}  peak_rpm={peak_rpm}\n"
            f"  fastest_lap_time={lap_time_str}"
        )

        # Write back to AlloyDB so subsequent calls hit the DB path
        session_id = _get_session_id(year, gp_name, session_type)
        if session_id:
            wb_conn = _get_pool().getconn()
            try:
                cur = wb_conn.cursor()
                cur.execute("""
                    INSERT INTO f1_telemetry_summary
                    (session_id, driver_id, avg_speed, top_speed, avg_throttle,
                     avg_brake, avg_rpm, peak_rpm, fastest_lap_time)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (session_id, driver_id) DO NOTHING
                """, (session_id, target, avg_speed, top_speed, avg_throttle,
                      avg_brake, avg_rpm, peak_rpm, lap_time_str))
                wb_conn.commit()
                print(f"\n[TOOL: fetch_f1_telemetry] Wrote back to AlloyDB: {target}")
            except Exception as e:
                wb_conn.rollback()
                print(f"\n[TOOL: fetch_f1_telemetry] Write-back skipped: {e}")
            finally:
                _get_pool().putconn(wb_conn)

        if tool_context:
            tool_context.state[cache_key] = result
        return result
    except Exception as e:
        return f"Telemetry Error: {str(e)}"


def fetch_f1_pit_strategy(year: int, gp_name: str, tool_context: ToolContext = None) -> str:
    """
    Fetches pit stop stints (stint number, tyre compound, lap range) for all drivers in a race.
    Queries AlloyDB pre-aggregated stints first; falls back to FastF1 live fetch and
    writes results back to AlloyDB for future calls.
    Use for strategy analysis, undercut/overcut reviews, and tyre degradation assessment.
    """
    cache_key = f"stints_{year}_{gp_name}"
    if tool_context and cache_key in tool_context.state:
        print(f"\n[TOOL: fetch_f1_pit_strategy] Cache hit: {cache_key}")
        return tool_context.state[cache_key]

    print(f"\n[TOOL: fetch_f1_pit_strategy] {gp_name} {year}")

    # ── AlloyDB first ──────────────────────────────────────────────────────────
    db_result = _query_raw("""
        SELECT d.code AS driver, st.stint_number, st.compound,
               st.start_lap, st.end_lap, st.lap_count
        FROM f1_stints st
        JOIN f1_sessions s ON s.id = st.session_id
        JOIN f1_drivers  d ON d.driver_id = st.driver_id
        WHERE s.season = %s AND s.race_name ILIKE %s AND s.session_type = 'Race'
        ORDER BY d.code, st.stint_number
    """, [year, f"%{gp_name}%"])

    if "Data: []" not in db_result and "Database Error" not in db_result:
        result = _to_md_table(db_result, f"## Pit Strategy — {gp_name} {year}")
        if tool_context:
            tool_context.state[cache_key] = result
        return result

    # ── FastF1 fallback + write-back ───────────────────────────────────────────
    try:
        session = fastf1.get_session(year, gp_name, 'R')
        session.load(telemetry=False, weather=False, messages=False)
        stints_df = session.laps[['Driver', 'Stint', 'Compound', 'LapNumber']].drop_duplicates()
        # Summarise per-lap data into one row per stint
        summary_rows = []
        for (drv, stint_num), grp in stints_df.groupby(['Driver', 'Stint']):
            summary_rows.append({
                'Driver': drv, 'Stint': int(stint_num),
                'Compound': grp.iloc[0]['Compound'],
                'Start Lap': int(grp['LapNumber'].min()),
                'End Lap':   int(grp['LapNumber'].max()),
                'Lap Count': int(grp['LapNumber'].max() - grp['LapNumber'].min() + 1),
            })
        import pandas as _pd
        summary = _pd.DataFrame(summary_rows).sort_values(['Driver', 'Stint'])
        header  = "| Driver | Stint | Compound | Start Lap | End Lap | Lap Count |"
        sep     = "|--------|-------|----------|-----------|---------|-----------|"
        rows_md = "\n".join(
            f"| {r['Driver']} | {r['Stint']} | {r['Compound']} | {r['Start Lap']} | {r['End Lap']} | {r['Lap Count']} |"
            for _, r in summary.iterrows()
        )
        result = f"## Pit Strategy — {gp_name} {year}\n\n{header}\n{sep}\n{rows_md}"

        # Write each stint back to AlloyDB
        session_id = _get_session_id(year, gp_name, 'R')
        if session_id:
            wb_conn = _get_pool().getconn()
            try:
                cur     = wb_conn.cursor()
                written = 0
                for (drv, stint_num), grp in stints_df.groupby(['Driver', 'Stint']):
                    compound  = grp.iloc[0]['Compound']
                    start_lap = int(grp['LapNumber'].min())
                    end_lap   = int(grp['LapNumber'].max())
                    cur.execute("""
                        INSERT INTO f1_stints
                        (session_id, driver_id, stint_number, compound,
                         start_lap, end_lap, lap_count)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (session_id, driver_id, stint_number) DO NOTHING
                    """, (session_id, drv, int(stint_num), compound,
                          start_lap, end_lap, end_lap - start_lap + 1))
                    written += 1
                wb_conn.commit()
                print(f"\n[TOOL: fetch_f1_pit_strategy] Wrote back {written} stint rows to AlloyDB")
            except Exception as e:
                wb_conn.rollback()
                print(f"\n[TOOL: fetch_f1_pit_strategy] Write-back skipped: {e}")
            finally:
                _get_pool().putconn(wb_conn)

        if tool_context:
            tool_context.state[cache_key] = result
        return result
    except Exception as e:
        return f"Pit Strategy Error: {str(e)}"


def fetch_f1_technical_details(year: int, gp_name: str, session_type: str,
                                tool_context: ToolContext = None) -> str:
    """
    Fetches track weather conditions and recent team radio transcripts for a session.
    Use to assess weather impact on strategy and driver-engineer communications.
    """
    cache_key = f"technical_{year}_{gp_name}_{session_type}"
    if tool_context and cache_key in tool_context.state:
        print(f"\n[TOOL: fetch_f1_technical_details] Cache hit: {cache_key}")
        return tool_context.state[cache_key]

    print(f"\n[TOOL: fetch_f1_technical_details] {gp_name} {year}")
    try:
        session = fastf1.get_session(year, gp_name, session_type)
        session.load(telemetry=False, weather=True, messages=True)
        weather  = session.weather_data.iloc[-1:].to_string(index=False)
        messages = "No radio messages available."
        if hasattr(session, 'messages') and len(session.messages) > 0:
            messages = session.messages[['Time', 'Driver', 'Message']].tail(5).to_string(index=False)
        result = f"Track Weather:\n{weather}\n\nRecent Radio Transcripts:\n{messages}"
        if tool_context:
            tool_context.state[cache_key] = result
        return result
    except Exception as e:
        return f"Technical Data Error: {str(e)}"


def send_f1_calendar_invite(event_name: str, start_time: str, location: str,
                             recipient_email: str, duration_hours: int = 2,
                             tool_context: ToolContext = None) -> str:  # noqa: ARG001
    """
    Sends a Google Calendar invitation for an F1 session.
    Use whenever the user wants to 'add', 'schedule', or 'set a reminder' for a race.
    - event_name: Full session name e.g. 'Monaco Grand Prix — Race'
    - start_time: ISO 8601 format e.g. '2026-05-24T15:00:00Z'
    - location: Circuit or city e.g. 'Circuit de Monaco, Monte Carlo'
    - recipient_email: The user's email address.
    - duration_hours: 2 for races, 1 for qualifying / practice / sprint.
    """
    print(f"\n[TOOL: send_f1_calendar_invite] {event_name} → {recipient_email}")

    # Validate email before attempting API call
    target_email = recipient_email or os.getenv("USER_EMAIL", "")
    if not target_email or "@" not in target_email or "." not in target_email.split("@")[-1]:
        try:
            start_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
            end_dt   = start_dt + timedelta(hours=duration_hours)
            fmt      = "%Y%m%dT%H%M%SZ"
            params   = {
                'action':   'TEMPLATE',
                'text':     event_name,
                'dates':    f"{start_dt.strftime(fmt)}/{end_dt.strftime(fmt)}",
                'details':  f"F1 Session at {location}. Generated by your AI Race Strategist.",
                'location': location
            }
            url = f"https://www.google.com/calendar/render?{urllib.parse.urlencode(params)}"
            return (
                f"⚠️ '{target_email}' doesn't look like a valid email address. "
                f"Use this link to add it manually: **[Add to Calendar]({url})**"
            )
        except Exception:
            return f"⚠️ Invalid email address '{target_email}'. Please provide a valid email e.g. user@gmail.com"

    try:
        import google.auth
        import google.auth.transport.requests
        # Fresh credentials per call — avoids thread-safety issues with shared state
        # across concurrent Cloud Run requests
        creds, _ = google.auth.default(
            scopes=['https://www.googleapis.com/auth/calendar']
        )
        if not creds.valid or getattr(creds, 'expired', False):
            creds.refresh(google.auth.transport.requests.Request())

        service  = build('calendar', 'v3', credentials=creds)
        start_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
        end_dt   = start_dt + timedelta(hours=duration_hours)

        event_body = {
            'summary':     event_name,
            'location':    location,
            'description': f'F1 Session at {location}. Scheduled by your AI Race Strategist.',
            'start': {'dateTime': start_dt.isoformat()},
            'end':   {'dateTime': end_dt.isoformat()},
            'reminders': {
                'useDefault': False,
                'overrides': [
                    {'method': 'popup', 'minutes': 60},    # 1 hour before
                    {'method': 'popup', 'minutes': 1440},  # 1 day before
                ]
            }
        }
        created = service.events().insert(calendarId=target_email, body=event_body).execute()
        return f"✅ Added to {target_email}: {event_name} (Event ID: {created.get('id')})"

    except Exception as calendar_err:
        try:
            start_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
            end_dt   = start_dt + timedelta(hours=duration_hours)
            fmt      = "%Y%m%dT%H%M%SZ"
            params   = {
                'action':   'TEMPLATE',
                'text':     event_name,
                'dates':    f"{start_dt.strftime(fmt)}/{end_dt.strftime(fmt)}",
                'details':  f"F1 Session at {location}. Generated by your AI Race Strategist.",
                'location': location
            }
            url = f"https://www.google.com/calendar/render?{urllib.parse.urlencode(params)}"
            return f"⚠️ Direct calendar write failed. **[One-Click Add Link]({url})**"
        except Exception:
            return f"Calendar Error: {str(calendar_err)}"


# ── Phase 2 new tools ─────────────────────────────────────────────────────────

def get_circuit_characteristics(circuit_id: str, tool_context: ToolContext = None) -> str:
    """
    Returns circuit metadata and race history for a given track.
    Use for circuit deep-dives, track layout context, and how many times a venue has hosted F1.
    - circuit_id: lowercase circuit ID or name e.g. 'monaco', 'spa', 'bahrain', 'Silverstone'.
    """
    cache_key = f"circuit_{circuit_id.lower()}"
    if tool_context and cache_key in tool_context.state:
        print(f"\n[TOOL: get_circuit_characteristics] Cache hit: {cache_key}")
        return tool_context.state[cache_key]

    print(f"\n[TOOL: get_circuit_characteristics] {circuit_id}")
    sql = """
        SELECT c.circuit_id, c.name, c.locality, c.country, c.lat, c.lng,
               COUNT(DISTINCT s.id)  AS times_hosted,
               MIN(s.season)         AS first_season,
               MAX(s.season)         AS last_season
        FROM f1_circuits c
        LEFT JOIN f1_sessions s
               ON s.circuit_id = c.circuit_id AND s.session_type = 'Race'
        WHERE c.circuit_id ILIKE %s
           OR c.name       ILIKE %s
           OR c.locality   ILIKE %s
        GROUP BY c.circuit_id, c.name, c.locality, c.country, c.lat, c.lng
        LIMIT 1
    """
    pattern = f"%{circuit_id}%"
    result  = _query_raw(sql, [pattern, pattern, pattern])

    if "Data: []" in result:
        result = f"No circuit found matching '{circuit_id}'. Try a different spelling or circuit ID."
    else:
        result = _to_md_table(result, f"## Circuit: {circuit_id.title()}")

    if tool_context:
        tool_context.state[cache_key] = result
    return result


def get_driver_head_to_head(driver1_code: str, driver2_code: str,
                             season: int = None, tool_context: ToolContext = None) -> str:
    """
    Head-to-head comparison: wins, podiums, poles, points, starts.
    When season is omitted, covers all seasons in the database (NOT full all-time career).
    - driver1_code: 3-letter code e.g. 'VER'
    - driver2_code: 3-letter code e.g. 'HAM'
    - season: optional e.g. 2024. If omitted, returns all seasons present in the database.
    """
    scope     = str(season) if season else "all_db"
    cache_key = f"head_to_head_{driver1_code}_{driver2_code}_{scope}"
    if tool_context and cache_key in tool_context.state:
        print(f"\n[TOOL: get_driver_head_to_head] Cache hit: {cache_key}")
        return tool_context.state[cache_key]

    print(f"\n[TOOL: get_driver_head_to_head] {driver1_code} vs {driver2_code} ({scope})")

    season_clause = "AND s.season = %(season)s" if season else ""
    sql = f"""
        SELECT
            d.code,
            d.full_name,
            MIN(s.season)                               AS first_season,
            MAX(s.season)                               AS last_season,
            COUNT(CASE WHEN r.position = 1  THEN 1 END) AS wins,
            COUNT(CASE WHEN r.position <= 3 THEN 1 END) AS podiums,
            COUNT(CASE WHEN r.grid = 1      THEN 1 END) AS poles,
            COALESCE(SUM(r.points), 0)                  AS total_points,
            COUNT(CASE WHEN r.classified    THEN 1 END) AS finishes,
            COUNT(r.id)                                 AS starts
        FROM f1_results r
        JOIN f1_drivers d  ON d.driver_id = r.driver_id
        JOIN f1_sessions s ON s.id = r.session_id AND s.session_type = 'Race'
        WHERE d.code IN (%(d1)s, %(d2)s)
        {season_clause}
        GROUP BY d.code, d.full_name
        ORDER BY total_points DESC
    """
    params = {"d1": driver1_code, "d2": driver2_code}
    if season:
        params["season"] = season

    conn = _get_pool().getconn()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall()

        if not rows:
            result = f"No data found for {driver1_code} or {driver2_code} in the database."
        else:
            # Determine scope label from actual data
            all_first = [r[2] for r in rows if r[2]]
            all_last  = [r[3] for r in rows if r[3]]
            if season:
                scope_label = f"{season} season"
                disclaimer  = ""
            elif all_first and all_last:
                db_from = min(all_first)
                db_to   = max(all_last)
                scope_label = f"{db_from}–{db_to} (database coverage)"
                disclaimer  = (
                    f"\n> **Note:** Figures cover {db_from}–{db_to} only "
                    f"(seasons available in this database). "
                    f"All-time career totals will differ for drivers active before {db_from}."
                )
            else:
                scope_label = "database coverage"
                disclaimer  = "\n> **Note:** Figures cover seasons in this database only."

            lines = [f"## Head-to-Head: {driver1_code} vs {driver2_code} ({scope_label})",
                     "",
                     "| Driver | Wins | Podiums | Poles | Points | Finishes | Starts |",
                     "|--------|------|---------|-------|--------|----------|--------|"]
            for r in rows:
                # r: code, full_name, first_season, last_season, wins, podiums, poles, points, finishes, starts
                lines.append(
                    f"| {r[1]} | {r[4]} | {r[5]} | {r[6]} | {int(r[7])} | {r[8]} | {r[9]} |"
                )
            if disclaimer:
                lines.append(disclaimer)
            result = "\n".join(lines)

    except Exception as e:
        result = f"Database Error: {str(e)}"
    finally:
        _get_pool().putconn(conn)

    if tool_context:
        tool_context.state[cache_key] = result
    return result


def get_session_times(year: int, gp_name: str, tool_context: ToolContext = None) -> str:
    """
    Fetches all session start times for a Grand Prix weekend.
    Returns FP1, FP2, FP3, Sprint Qualifying, Sprint, Qualifying, and Race times.
    Use when the user wants to add multiple sessions or the full race weekend to calendar.
    Note: future event times may not yet be available in FastF1 — fall back to get_f1_schedule.
    - year: e.g. 2026
    - gp_name: e.g. 'Monaco', 'British'
    """
    cache_key = f"session_times_{year}_{gp_name}"
    if tool_context and cache_key in tool_context.state:
        print(f"\n[TOOL: get_session_times] Cache hit: {cache_key}")
        return tool_context.state[cache_key]

    print(f"\n[TOOL: get_session_times] {year} {gp_name}")
    try:
        event    = fastf1.get_event(year, gp_name)
        sessions = {}
        for name in ['Practice 1', 'Practice 2', 'Practice 3',
                     'Sprint Qualifying', 'Sprint', 'Qualifying', 'Race']:
            try:
                s = event.get_session(name)
                if s.date is not None:
                    sessions[name] = s.date.isoformat()
            except Exception:
                pass  # session doesn't exist for this event format

        if not sessions:
            return (f"Session times for {year} {gp_name} are not yet available in FastF1. "
                    f"Use get_f1_schedule to find the event date and create an invite for Race day.")

        result = f"Session times for {year} {gp_name} GP:\n"
        for name, time in sessions.items():
            result += f"  {name}: {time}\n"
        result += "\nDuration guide: Race=2h, Qualifying=1h, any Practice=1h, Sprint=1h, Sprint Qualifying=1h"

        if tool_context:
            tool_context.state[cache_key] = result
        return result
    except Exception as e:
        return f"Session Times Error: {str(e)}"


def get_calendar_options(year: int, gp_name: str,
                          location: str = "",
                          recipient_email: str = "",
                          tool_context: ToolContext = None) -> str:
    """
    Returns two calendar options for a GP weekend:
      1. A single .ics download link containing ALL sessions in one file.
      2. Individual one-click Google Calendar links per session.
    Always call this instead of calling send_f1_calendar_invite multiple times
    for full-weekend requests. Let the user choose which option they prefer.
    - year: e.g. 2026
    - gp_name: e.g. 'Miami', 'Monaco'
    - location: circuit/city e.g. 'Miami Gardens, USA'
    - recipient_email: user's email (used if they choose direct write)
    """
    print(f"\n[TOOL: get_calendar_options] {year} {gp_name}")

    # ── Fetch session times ───────────────────────────────────────────────────
    session_times = {}
    try:
        event = fastf1.get_event(year, gp_name)
        if not location:
            location = f"{event.get('Location', gp_name)}, {event.get('Country', '')}"
        for name in ['Practice 1', 'Practice 2', 'Practice 3',
                     'Sprint Qualifying', 'Sprint', 'Qualifying', 'Race']:
            try:
                s = event.get_session(name)
                if s.date is not None:
                    session_times[name] = s.date.isoformat()
            except Exception:
                pass
    except Exception as e:
        return f"Calendar Options Error: {str(e)}"

    if not session_times:
        return (f"Session times for {year} {gp_name} are not yet available. "
                f"Try get_f1_schedule for the race date and use send_f1_calendar_invite.")

    durations = {
        'Race': 2, 'Qualifying': 1, 'Sprint': 1,
        'Sprint Qualifying': 1, 'Practice 1': 1, 'Practice 2': 1, 'Practice 3': 1,
    }

    # ── Option 1: single .ics link ────────────────────────────────────────────
    service_url = os.getenv("SERVICE_URL",
                            "https://f1-orchestrator-521055768390.us-central1.run.app")
    ics_url = f"{service_url}/ics/{year}/{gp_name.replace(' ', '%20')}"

    # ── Option 2: individual Google Calendar links ────────────────────────────
    table_rows = []
    for name, iso_time in session_times.items():
        try:
            start_dt = datetime.fromisoformat(iso_time.replace('Z', '+00:00'))
            end_dt   = start_dt + timedelta(hours=durations.get(name, 1))
            fmt      = "%Y%m%dT%H%M%SZ"
            params   = {
                'action':   'TEMPLATE',
                'text':     f"{gp_name} Grand Prix — {name}",
                'dates':    f"{start_dt.strftime(fmt)}/{end_dt.strftime(fmt)}",
                'details':  'F1 session scheduled by your AI Race Strategist.',
                'location': location,
            }
            link     = f"https://www.google.com/calendar/render?{urllib.parse.urlencode(params)}"
            date_str = start_dt.strftime("%a %d %b, %H:%M UTC")
            table_rows.append(f"| {name} | {date_str} | [Add to Calendar]({link}) |")
        except Exception:
            pass

    table = (
        "| Session | Date & Time | |\n"
        "|---------|------------|---|\n"
        + "\n".join(table_rows)
    )

    result = f"""## {gp_name} Grand Prix {year} — Add to Calendar

### Option A — All sessions in one file
Import all {len(session_times)} sessions at once into Google Calendar, Apple Calendar, or Outlook:
**[Download {gp_name} GP Calendar (.ics)]({ics_url})**

---

### Option B — Add sessions individually

{table}

Which option would you prefer?"""

    if tool_context:
        tool_context.state[f"calendar_options_{year}_{gp_name}"] = result
    return result


def fetch_race_control_messages(year: int, gp_name: str,
                                 tool_context: ToolContext = None) -> str:
    """
    Fetches race control messages: flags, safety car, VSC, incidents, penalties.
    Use for steward incident validation, yellow flag checks, safety car timing.
    - year: e.g. 2024
    - gp_name: e.g. 'Bahrain', 'Monaco'
    """
    cache_key = f"race_control_{year}_{gp_name}"
    if tool_context and cache_key in tool_context.state:
        print(f"\n[TOOL: fetch_race_control_messages] Cache hit: {cache_key}")
        return tool_context.state[cache_key]

    print(f"\n[TOOL: fetch_race_control_messages] {year} {gp_name}")

    # AlloyDB first (populated after Phase 3 backfill)
    sql = """
        SELECT rc.lap_number, rc.flag_type, rc.sector, rc.message, rc.driver_id
        FROM f1_race_control rc
        JOIN f1_sessions s ON s.id = rc.session_id
        WHERE s.season = %s
          AND s.race_name ILIKE %s
          AND s.session_type = 'Race'
        ORDER BY rc.lap_number, rc.timestamp
    """
    raw = _query_raw(sql, [year, f"%{gp_name}%"])

    if "Data: []" in raw or "Database Error" in raw:
        # FastF1 live fallback
        try:
            session = fastf1.get_session(year, gp_name, 'R')
            session.load(telemetry=False, weather=False, messages=True)
            if len(session.race_control_messages) > 0:
                df   = session.race_control_messages
                cols = [c for c in ['Lap', 'Flag', 'Scope', 'Sector', 'Message']
                        if c in df.columns]
                df   = df[cols].fillna("—")
                header = "| " + " | ".join(cols) + " |"
                sep    = "|" + "|".join(["---"] * len(cols)) + "|"
                rows_md = "\n".join(
                    "| " + " | ".join(str(v) for v in row) + " |"
                    for row in df.itertuples(index=False)
                )
                result = f"## Race Control — {year} {gp_name}\n\n{header}\n{sep}\n{rows_md}"
            else:
                result = f"No race control messages found for {year} {gp_name}."
        except Exception as e:
            result = f"Race Control Error: {str(e)}"
    else:
        result = _to_md_table(raw, f"## Race Control — {year} {gp_name}")

    if tool_context:
        tool_context.state[cache_key] = result
        tool_context.state["_sw_race_ctrl"] = result
    return result


def _get_embedding(text: str) -> list[float]:
    """Generate a 768-dim embedding via Vertex AI text-embedding-005."""
    import vertexai
    from vertexai.language_models import TextEmbeddingModel
    vertexai.init(
        project=os.getenv("GOOGLE_CLOUD_PROJECT"),
        location=os.getenv("VERTEX_AI_LOCATION", "us-central1")
    )
    model  = TextEmbeddingModel.from_pretrained("text-embedding-005")
    result = model.get_embeddings([text[:3000]])
    return result[0].values


def _rag_available(table: str) -> bool:
    """Return True if the RAG table has at least one embedded row."""
    result = _query_raw(f"SELECT COUNT(*) FROM {table} WHERE embedding IS NOT NULL")
    try:
        import ast
        count = ast.literal_eval(result.split("Data: ")[1])[0][0]
        return count > 0
    except Exception:
        return False


def query_f1_regulations(question: str, year_filter: int = None,
                          reg_type: str = None, tool_context: ToolContext = None) -> str:
    """
    Semantic search over FIA F1 regulations (10 years of Sporting, Technical, Financial regs).
    Use for: penalty rules, technical legality, safety car procedures, tyre rules,
    DRS zones, cost cap, 'is X legal', 'what does the rulebook say about Y'.
    - question: natural language question about F1 rules
    - year_filter: specific season e.g. 2025. None = search all years.
    - reg_type: 'Sporting', 'Technical', 'Financial', 'General', or 'Operational'. None = search all.
      Note: 2026 regs use section names — General (A), Sporting (B), Technical (C),
      Financial (D), Operational (F). 2025 and earlier use Sporting/Technical/Financial.
    """
    cache_key = f"reg_{hash(question)}_{year_filter}_{reg_type}"
    if tool_context and cache_key in tool_context.state:
        print(f"\n[TOOL: query_f1_regulations] Cache hit")
        return tool_context.state[cache_key]

    print(f"\n[TOOL: query_f1_regulations] {question[:80]}")

    if not _rag_available("f1_regulations"):
        return (
            "FIA regulations RAG not yet ingested (Phase 4 pending). "
            "Answering from model training knowledge — treat as indicative, not authoritative."
        )

    try:
        import re as _re

        # ── Path 1: direct article number lookup ─────────────────────────────
        # Detects patterns like "34.14", "Article 34.14", "B34.13", "39"
        article_match = _re.search(r'\b([A-Z]?\d+(?:\.\d+)+|\b[A-Z]\d+\.\d+)\b', question)
        if article_match:
            art_num = article_match.group(1)
            where_parts = ["article_number ILIKE %s"]
            params_direct = [f"%{art_num}%"]
            if year_filter:
                where_parts.append("year = %s")
                params_direct.append(year_filter)
            if reg_type:
                where_parts.append("reg_type = %s")
                params_direct.append(reg_type)
            where_sql = f"WHERE {' AND '.join(where_parts)}"
            direct_result = _query_raw(
                f"SELECT article_number, article_title, content, year, reg_type "
                f"FROM f1_regulations {where_sql} ORDER BY year DESC LIMIT 3",
                params_direct
            )
            if "Data: []" not in direct_result and "Database Error" not in direct_result:
                print(f"\n[TOOL: query_f1_regulations] Direct article match: {art_num}")
                if tool_context:
                    tool_context.state[cache_key] = direct_result
                    tool_context.state["_sw_regulation"] = direct_result
                return direct_result
            # No exact match — fall through to semantic search

        # ── Path 2: semantic (vector) search ─────────────────────────────────
        embedding   = _get_embedding(question)
        where_parts = []
        params      = [embedding]

        if year_filter:
            where_parts.append("year = %s")
            params.append(year_filter)
        if reg_type:
            where_parts.append("reg_type = %s")
            params.append(reg_type)

        where_sql = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
        sql = f"""
            SELECT article_number, article_title, content, year, reg_type,
                   embedding <=> %s::vector AS distance
            FROM f1_regulations
            {where_sql}
            ORDER BY distance ASC
            LIMIT 5
        """
        result = _query_raw(sql, [embedding] + params[1:])
        if tool_context:
            tool_context.state[cache_key] = result
            tool_context.state["_sw_regulation"] = result
        return result
    except Exception as e:
        return f"Regulations search error: {str(e)}"


def query_steward_decisions(question: str, year_filter: int = None,
                             tool_context: ToolContext = None) -> str:
    """
    Semantic search over historical FIA steward decisions and penalty precedents.
    Use for: 'has this been penalised before', 'what penalty did X get for Y',
    incident ruling history, penalty precedent.
    - question: describe the incident or penalty type
    - year_filter: specific season. None = all years.
    """
    cache_key = f"decision_{hash(question)}_{year_filter}"
    if tool_context and cache_key in tool_context.state:
        print(f"\n[TOOL: query_steward_decisions] Cache hit")
        return tool_context.state[cache_key]

    print(f"\n[TOOL: query_steward_decisions] {question[:80]}")

    if not _rag_available("f1_decisions"):
        return (
            "Steward decisions RAG not yet ingested (Phase 4 pending). "
            "Answering from model training knowledge — treat as indicative, not authoritative."
        )

    try:
        embedding   = _get_embedding(question)
        params      = [embedding]
        year_clause = ""
        if year_filter:
            year_clause = "WHERE year = %s"
            params.append(year_filter)

        sql = f"""
            SELECT race, year, driver_id, incident, ruling, penalty,
                   COALESCE(NULLIF(TRIM(article_ref), ''), '—') AS article_ref,
                   embedding <=> %s::vector AS distance
            FROM f1_decisions
            {year_clause}
            ORDER BY distance ASC
            LIMIT 5
        """
        result = _query_raw(sql, [embedding] + ([year_filter] if year_filter else []))
        if tool_context:
            tool_context.state[cache_key] = result
            tool_context.state["_sw_decisions"] = result
        return result
    except Exception as e:
        return f"Steward decisions search error: {str(e)}"


def get_full_ruling(
    incident_description: str,
    race_control_summary: str,
    article_number: str,
    article_content: str,
    precedent_summary: str,
    finding: str,
    reason: str,
    likely_penalty: str,
    tool_context: ToolContext = None,
) -> str:
    """
    Formats a complete FIA-style steward ruling document from structured data.
    Call this ONLY when the user explicitly asks for the 'full ruling', 'complete ruling',
    'all details', or 'show me more'. Do NOT call during the initial fast verdict.

    Pass summaries you already gathered from the other steward tools:
    - incident_description: e.g. "VER unsafe pit release, 2024 Bahrain, Lap 34"
    - race_control_summary: key flags/messages from fetch_race_control_messages
    - article_number: e.g. "34.14"
    - article_content: the relevant article text from query_f1_regulations
    - precedent_summary: formatted string from query_steward_decisions rows
    - finding: "VIOLATION" / "NO VIOLATION" / "INCONCLUSIVE"
    - reason: one-sentence explanation citing the article and observed facts
    - likely_penalty: e.g. "5-second time penalty" or "None"
    """
    print(f"\n[TOOL: get_full_ruling] Formatting ruling for: {incident_description[:60]}")

    # Format raw DB results into readable text if they're still in Columns/Data format
    def _clean(raw: str) -> str:
        if not raw or raw in (None, "None", ""):
            return "_Not available._"
        if "Columns:" in raw and "Data:" in raw:
            formatted = _to_md_table(raw)
            return formatted if formatted != raw else raw
        return raw

    try:
        lines = [
            f"## Steward Ruling: {incident_description}",
            "",
            "---",
            "",
            "### Race Control at Time of Incident",
            _clean(race_control_summary),
            "",
            "---",
            "",
            "### Applicable Regulation",
            f"**Article {article_number}**" if article_number else "**Article: Not identified**",
            "",
            _clean(article_content),
            "",
            "---",
            "",
            "### Precedent",
            _clean(precedent_summary),
            "",
            "---",
            "",
            "### Verdict",
            f"**Finding:** {finding or 'INCONCLUSIVE'}",
            f"**Reason:** {reason or 'Not specified.'}",
            f"**Likely Penalty:** {likely_penalty or 'Under review.'}",
            "",
            "---",
            "_FIA Steward ruling generated by F1 AI Orchestrator. "
            "Verify against official FIA documents for binding interpretation._",
        ]

        ruling_text = "\n".join(lines)
        if tool_context:
            tool_context.state["_last_full_ruling"] = ruling_text
        return ruling_text

    except Exception as e:
        return (
            f"## Steward Ruling: {incident_description}\n\n"
            f"**Error generating full ruling:** {str(e)}\n\n"
            f"**Finding:** {finding or 'See fast verdict above.'}\n"
            f"**Likely Penalty:** {likely_penalty or 'See fast verdict above.'}"
        )


# ─────────────────────────────────────────────
# 5. SPECIALIST AGENTS
# ─────────────────────────────────────────────

# SPECIALIST A: INTEL
f1_intel_agent = LlmAgent(
    name="f1_intel_agent",
    description="Fetches raw F1 data: race results, standings, schedule, circuits, head-to-head stats. Cannot predict or analyse.",
    instruction=f"""
    You are the F1 Intelligence Officer. Your ONLY job is to fetch and return raw F1 data.

    SCHEMA IS THE SOURCE OF TRUTH: Always check F1_TABLE_METADATA below before writing SQL.
    Only query tables listed there. Any other table name WILL fail with a database error.

    TOOL USAGE POLICY:
    1. FUTURE RACES / SCHEDULE / NEXT RACE (date > today):
       → Call get_f1_schedule(year) IMMEDIATELY. This is the ONLY source for upcoming races.
       → Do NOT call query_f1_db — future races are not in the database.
       → Do NOT try tables like 'races', 'schedule', 'grand_prix' — they do not exist.
    2. PAST RACE RESULTS (date ≤ today): Use query_f1_db first.
       → If it returns Data: [] OR "Database Error", immediately fallback to fetch_fastf1_live_data.
       → ALWAYS use ILIKE with wildcards: race_name ILIKE '%Bahrain%' NOT race_name = 'Bahrain Grand Prix'.
    3. LIVE / 2025+ PAST DATA: Use fetch_fastf1_live_data directly.
    4. STANDINGS: Use get_f1_standings for championship positions and points.
    5. CIRCUIT INFO: Use get_circuit_characteristics for track metadata and history.
    6. HEAD-TO-HEAD: Use get_driver_head_to_head for career or season comparisons.

    OUTPUT FORMAT:
    - Use ## section headers for every result set (e.g. "## Race Results — Bahrain 2024").
    - ALWAYS convert data to a markdown table. NEVER return raw Python tuples, column arrays,
      or unformatted data strings (e.g. "Columns: [...] Data: [...]" is FORBIDDEN).
      Convert every tuple like ('VER', 399.0, 9) into a proper table row.
    - Use full names (e.g. "Max Verstappen", not "VER") in the Name/Driver column.
    - If a source returns no data, say so explicitly and note which fallback was tried.
    - Return ONLY the data you were asked to fetch. Do NOT add disclaimers, explanations
      of your limitations, or commentary about what other agents will do next.
      If your task asks for something outside your tools (e.g. predictions), silently skip
      that part and return only the data you can fetch.
    - NEVER mention tool names, table names, or internal implementation details in output.
      Do NOT write "From query_f1_db tool:", "From f1_decisions table:", "Using fetch_fastf1_live_data:", etc.
      Use clean section headers instead (e.g. "## 2025 Penalty Results").
    - HEAD-TO-HEAD: the tool already returns a formatted table — pass it through unchanged.
      Always append: "Figures cover database seasons only (2020–2025), not all-time career."

    SQL GUIDELINES:
    {SQL_GUIDELINES}

    SCHEMA CONTEXT:
    {F1_TABLE_METADATA}
    """,
    tools=[
        get_temporal_context,
        query_f1_db,
        fetch_fastf1_live_data,
        get_f1_schedule,
        get_f1_standings,
        get_circuit_characteristics,
        get_driver_head_to_head,
    ],
    before_agent_callback=_log_agent_start,
    model=MODEL,
    generate_content_config=types.GenerateContentConfig(temperature=0)
)


# Phase 5: AgentEngineSandboxCodeExecutor — None-safe fallback if not configured
_code_executor = None
_AGENT_ENGINE = os.getenv(
    "AGENT_ENGINE_RESOURCE",
    # Reasoning Engine created 2026-04-26 in project f1-command-center-dev
    "projects/521055768390/locations/us-central1/reasoningEngines/6007806850714566656"
)
try:
    from google.adk.code_executors.agent_engine_sandbox_code_executor import (
        AgentEngineSandboxCodeExecutor,
    )
    _code_executor = AgentEngineSandboxCodeExecutor(
        agent_engine_resource_name=_AGENT_ENGINE
    )
    print(f"[Phase 5] AgentEngineSandboxCodeExecutor loaded ✓")
except Exception as _e:
    print(f"[Phase 5] Sandbox unavailable — Monte Carlo will use estimates: {_e}")


# SPECIALIST B: ANALYSIS & PREDICTION
f1_analysis_agent = LlmAgent(
    name="f1_analysis_agent",
    description="Analyses F1 telemetry, pit strategy, and produces race predictions and winner forecasts. Use for any prediction or performance analysis.",
    instruction=f"""
    You are the F1 Performance Analyst & Race Strategist. You fetch the data you need
    and produce a complete analysis in one response — never ask the user for data.

    TOOL USAGE:
    1. FUTURE RACE PREDICTIONS ("predict the winner of the next race", "who will win X"):
       The future race has NOT happened — do NOT call fetch_fastf1_live_data to fetch ITS results.
       DO: call get_f1_standings(year) for current championship form.
       DO: call query_f1_db to get PAST results at that circuit for circuit-specific form.
       DO: call fetch_fastf1_live_data for PAST races at that circuit if DB returns empty.
       Base the prediction on current standings + past circuit performance.

    2. TELEMETRY (past races only): Use fetch_f1_telemetry for Speed, Throttle, Brake, Gear, RPM.
       Always fetch for every driver being compared — never skip one side.
    3. PIT STRATEGY (past races only): Use fetch_f1_pit_strategy for stint lengths, tyre compounds, pit laps.
       Fetch proactively for any race analysis — do not wait to be asked.
    4. WEATHER & RADIO: Use fetch_f1_technical_details for track conditions and engineer comms.
    5. RACE RESULTS (context): Use fetch_fastf1_live_data for session finishing order — past races only.
    6. HISTORICAL CONTEXT: Use query_f1_db to pull career stats or past results.

    MONTE CARLO PIT STRATEGY SIMULATION:
    When asked for optimal pit window, stop count, or strategy predictions:
    1. Fetch stint data with fetch_f1_pit_strategy (tyre compounds + lap counts)
    2. Write a Python simulation with these parameters:
       - iterations = 1000
       - tyre_deg_rates = {{"SOFT": 0.08, "MEDIUM": 0.05, "HARD": 0.03}}  # s/lap
       - pit_loss_time  = 22  # seconds (adjust per circuit if known)
       - sc_prob_per_lap = 0.05
       - race_laps = actual lap count for that circuit
    3. Simulate each iteration: vary deg rate ±20%, sc deployment, pit timing
    4. Report: optimal pit lap, 1-stop vs 2-stop delta, confidence interval
    If code_executor is unavailable, produce a reasoned estimate from the stint
    data directly and clearly label it as an estimate, not a simulation.

    OUTPUT FORMAT — follow this exact structure:

    ---
    ## 🏎 [Driver A] vs [Driver B] — [Year] [GP Name]

    ### Fastest Lap Telemetry
    | Metric       | [Driver A] | [Driver B] | Edge        |
    |--------------|------------|------------|-------------|
    | Avg Speed    | 000.0 km/h | 000.0 km/h | [who & why] |
    | Top Speed    | 000.0 km/h | 000.0 km/h | [who & why] |
    | Avg Throttle | 00.0 %     | 00.0 %     | [who & why] |
    | Avg RPM      | 00000      | 00000      | [who & why] |
    | Peak RPM     | 00000      | 00000      | [who & why] |

    ### Pit Stop Strategy
    | Stint | Driver     | Laps     | Compound | Note |
    |-------|------------|----------|----------|------|
    | 1     | [Driver A] | L1 – L00 | SOFT     | ...  |

    > **Decisive moment:** [one sentence on the key strategic call]

    ### Verdict
    **Strategic edge:** [Driver] — [one sentence reason]
    **Performance edge:** [Driver] — [one sentence reason]
    **Race outcome factor:** [what ultimately separated them]
    ---

    ### Monte Carlo Simulation (when applicable)
    | Scenario       | Optimal Pit Lap | Delta vs Alt | Confidence |
    |----------------|-----------------|--------------|------------|
    | 1-stop MEDIUM  | Lap XX          | –Xs          | XX%        |
    | 2-stop S→M→H   | Lap XX / Lap XX | –Xs          | XX%        |
    **Recommended:** [strategy] — [one sentence reason]

    FORMATTING RULES:
    - Always use the table structures above — never use bullet lists for numbers.
    - Round speeds to 1 decimal, throttle/brake to 1 decimal, RPM to nearest integer.
    - Use full names in headers, abbreviations inside tables (VER/HAM).
    - For predictions, use a numbered podium list, not a table.
    - Never output raw stat blocks — always convert to the table format above.
    - Keep prose sections to 1–2 sentences each.
    """,
    tools=[
        get_temporal_context,
        fetch_f1_telemetry,
        fetch_f1_pit_strategy,
        fetch_f1_technical_details,
        fetch_fastf1_live_data,
        query_f1_db,
        get_f1_standings,
    ],
    before_agent_callback=_log_agent_start,
    code_executor=_code_executor,  # None if sandbox not configured — agent still works
    model=MODEL,
    generate_content_config=types.GenerateContentConfig(temperature=0.4)
)


# SPECIALIST C: STEWARD
f1_steward_agent = LlmAgent(
    name="f1_steward_agent",
    description="Validates racing incidents against FIA regulations and historical steward decisions. Use for rules questions, penalty lookups, and incident verdicts.",
    instruction=f"""
    You are the FIA Steward Panel AI. Validate racing incidents against FIA regulations
    and historical precedent. Optimise for speed: return a concise verdict by default.

    ── WORKFLOW ──────────────────────────────────────────────────────────────────
    1. fetch_race_control_messages(year, gp_name) — flag status, sector, lap number
    2. query_f1_regulations(incident_description) — find the applicable FIA article
    3. query_steward_decisions(incident_description) — find historical precedent
    4. Output the FAST VERDICT below (DEFAULT — always do this first)

    ── DEFAULT OUTPUT (fast verdict — always return this) ───────────────────────
    Return EXACTLY these 4 lines and nothing else:
    **Finding:** VIOLATION / NO VIOLATION / INCONCLUSIVE
    **Article:** [article_number] — [one-line article summary]
    **Precedent:** [race, year, driver] — [ruling], [penalty]
    **Likely Penalty:** [penalty or "None"]

    If data is unavailable, use 'from model knowledge' as the finding qualifier.

    ── ON-DEMAND FULL RULING ────────────────────────────────────────────────────
    Call get_full_ruling() ONLY if the user EXPLICITLY asks for:
    "full ruling", "complete ruling", "all details", "show me more", or "explain".
    Pass the data you already collected as parameters.
    Return the tool result DIRECTLY to the user — do NOT reformat or add preamble.

    ── RULES ────────────────────────────────────────────────────────────────────
    - Always cite a specific FIA article number (or say "model knowledge" if not found)
    - Always include at least one precedent (or "No precedent found")
    - Never guess on penalty amounts — use precedent or say "under investigation"
    - Never output Markdown tables or headers during the fast verdict phase

    SQL GUIDELINES:
    {SQL_GUIDELINES}

    SCHEMA CONTEXT:
    {F1_TABLE_METADATA}
    """,
    tools=[
        query_f1_regulations,
        query_steward_decisions,
        query_f1_db,
        fetch_race_control_messages,
        get_full_ruling,
    ],
    before_agent_callback=_log_agent_start,
    model=MODEL,
    generate_content_config=types.GenerateContentConfig(temperature=0)
)


# SPECIALIST D: EVENT SCHEDULER
f1_event_scheduler = LlmAgent(
    name="f1_event_scheduler",
    description="Adds F1 sessions to Google Calendar. Use only for calendar invites and scheduling. Always call last after any analysis or data tasks.",
    instruction="""
    You are the F1 Event Scheduler. Help users discover F1 sessions and add them to
    their Google Calendar.

    WORKFLOW:
    1. SINGLE SESSION ("add the Monaco race", "next race"):
       → use get_f1_schedule to confirm date and location.
       → use send_f1_calendar_invite to add it directly.
       → if direct write fails, show the one-click fallback link.

    2. FULL WEEKEND ("add the whole Monaco weekend", "add all sessions"):
       → use get_calendar_options to get BOTH options at once.
       → present the response to the user exactly as returned — do not reformat it.
       → wait for the user to choose Option A (single .ics) or Option B (individual links).
       → if they choose Option B and provide their email, call send_f1_calendar_invite
          for each session individually.

    3. "NEXT RACE" → use get_f1_schedule for current year, find first event AFTER today.

    4. EMAIL — if the user hasn't provided their email and direct write is needed, ask first.

    RULES:
    - Never simulate or guess session times — always fetch first.
    - Focus ONLY on the calendar task. Do not comment on race results or predictions.
    - For full-weekend requests, ALWAYS use get_calendar_options — never call
      send_f1_calendar_invite five times in a row.
    - After calling any tool, return the tool result DIRECTLY to the user word-for-word.
      Do NOT add preamble, do NOT reformat. If it starts with ✅ or ⚠️, show it as-is.
      This avoids an extra LLM hop that can time out.
    """,
    tools=[
        get_f1_schedule,
        get_session_times,
        get_calendar_options,
        send_f1_calendar_invite,
    ],
    before_agent_callback=_log_agent_start,
    model=MODEL,
    generate_content_config=types.GenerateContentConfig(temperature=0)
)


# ─────────────────────────────────────────────
# 6. COORDINATOR (TIER 2)
# ─────────────────────────────────────────────
f1_coordinator = LlmAgent(
    name="f1_coordinator",
    instruction="""
    You are the F1 Race Engineer. Your job: call the right agents and combine their answers.

    STEP 0 — ALWAYS FIRST: call get_temporal_context to get today's date.
    Include "Today is [date]." at the start of EVERY task you send to a specialist agent.

    AGENT ROLES — know what each specialist does:
    - f1_intel_agent:      fetches raw data — results, standings, schedule, circuits, head-to-head
    - f1_analysis_agent:   predicts and analyses — winner predictions, telemetry, pit strategy
    - f1_steward_agent:    FIA rules, penalty lookups, incident verdicts
    - f1_event_scheduler:  Google Calendar invites ONLY — always call this LAST

    ROUTING CHECKLIST — work through this for every query:
    [ ] Does the query need DATA (results, standings, schedule, next race)?
        → transfer to f1_intel_agent with ONLY the data task

    [ ] Does the query need PREDICTION or ANALYSIS (winner, telemetry, strategy)?
        → transfer to f1_analysis_agent with ONLY that task
        → do this even if intel already answered part of the query

    [ ] Does the query ask about RULES or PENALTIES?
        → transfer to f1_steward_agent with ONLY the rules task

    [ ] Does the query ask to ADD TO CALENDAR or SET A REMINDER?
        → transfer to f1_event_scheduler LAST with ONLY the scheduling task

    Complete EVERY checked item before responding. Don't skip any.
    ORDERING: intel first → analysis/steward second → scheduler last.
    MAX 3 agent transfers per query (not counting get_temporal_context).

    ── WORKED EXAMPLES — match the incoming query to the closest example ──────────

    EXAMPLE 1 — "Who won the 2024 Monaco GP?":
      call get_temporal_context → "Today is [date]"
      [DATA ✓] → transfer f1_intel_agent:
          "Today is [date]. Who won the 2024 Monaco GP? Use query_f1_db."
      Return intel's answer directly.

    EXAMPLE 2 — "Top 5 most common penalties in 2025":
      call get_temporal_context → "Today is [date]"
      [DATA ✓] → transfer f1_intel_agent:
          "Today is [date]. What are the top 5 most common penalties in 2025?
           Use query_f1_db with a GROUP BY query on f1_decisions table."
      Return intel's answer directly.

    EXAMPLE 3 — "Analyse Norris vs Piastri telemetry at Monza 2024":
      call get_temporal_context → "Today is [date]"
      [ANALYSIS ✓] → transfer f1_analysis_agent:
          "Today is [date]. Compare Norris (NOR) vs Piastri (PIA) telemetry at Monza 2024 Race.
           Fetch telemetry for both drivers and compare speed, throttle, brake, and pit strategy."
      Return analysis answer directly.

    EXAMPLE 4 — "Was Verstappen's move on Hamilton legal? What's the standings impact?":
      call get_temporal_context → "Today is [date]"
      [RULES ✓] → transfer f1_steward_agent:
          "Today is [date]. Was Verstappen's move on Hamilton at [race] legal? Give a verdict."
      [DATA ✓] → transfer f1_intel_agent:
          "Today is [date]. What are the current [year] driver championship standings?"
      Combine: steward verdict first, then standings table.

    EXAMPLE 5 — "Add next race to calendar and predict the winner":
      call get_temporal_context → "Today is [date]"
      [DATA ✓] → transfer f1_intel_agent:
          "Today is [date]. The next race is in the future — NOT in the database.
           Use get_f1_schedule([year]) ONLY to find the next race after [date].
           Return: race name, date, circuit. Do NOT use query_f1_db for schedule."
      [ANALYSIS ✓] → transfer f1_analysis_agent:
          "Today is [date]. Predict the winner of [race_name] at [circuit].
           The race has NOT happened — do NOT fetch its results.
           Use get_f1_standings([year]) for current form +
           query_f1_db for PAST results at [circuit] (FastF1 fallback if DB is empty)."
      [CALENDAR ✓] → transfer f1_event_scheduler:
          "Add [race_name] on [date] at [location] to the user's calendar."
      Combine all three into ONE final response.

    EXAMPLE 6 — "Add the British GP weekend to my calendar":
      call get_temporal_context → "Today is [date]"
      [CALENDAR ✓] → transfer f1_event_scheduler:
          "Today is [date]. Add all sessions for the [year] British GP to the user's calendar."
      Return scheduler's answer directly.

    OUTPUT RULES:
    - Show the FULL content from every agent — never summarise or skip.
    - The prediction must be shown in full, not just mentioned.
    - The calendar result (confirmation, fallback link, or question) must be shown.
    - If any agent asks the user a question (e.g. "what is your email?"), include
      that question word-for-word so the user can answer it.
    - Do NOT write "All tasks completed" — show the actual results.
    - No agent names or routing details in the output.

    FORMATTING RULES — always follow these:
    - Use ## section headers to separate each domain (e.g. ## Race Results, ## Championship Standings, ## Calendar).
    - Race results and standings: always use a markdown table, never a bullet list.
      | Pos | Driver | Team | Points |
      |-----|--------|------|--------|
    - Predictions: numbered podium list (1. / 2. / 3.) followed by a brief reasoning paragraph.
    - Steward verdicts: bold the 4-line format (Finding / Article / Precedent / Likely Penalty).
    - Calendar: pass through the tool result unchanged — do not reformat.
    - Keep prose to 1–2 sentences per section. Numbers go in tables, not sentences.
    - Never output raw Python tuples, column arrays, or unformatted data strings.
    """,
    tools=[get_temporal_context],
    sub_agents=[
        f1_intel_agent,
        f1_analysis_agent,
        f1_steward_agent,
        f1_event_scheduler,
    ],
    before_agent_callback=_log_agent_start,
    model=MODEL,
    generate_content_config=types.GenerateContentConfig(temperature=0.3)
)


# ─────────────────────────────────────────────
# 7. ORCHESTRATOR (TIER 1)
# ─────────────────────────────────────────────
# ADK constraint: an agent instance can only have one parent.
# Specialists are children of f1_coordinator, so the orchestrator routes
# exclusively through the coordinator. The coordinator handles both simple
# (single specialist) and complex (multi-specialist) dispatch.
f1_orchestrator = LlmAgent(
    name="race_strategist",
    instruction="""
    You are the Senior F1 Race Strategist — the Pit Wall Director.
    Your ONLY job is to classify the query and route it to f1_coordinator.
    You do NOT answer questions directly. You do NOT call tools.

    Classify the query with one of these labels, then transfer to f1_coordinator:
    - [SIMPLE: intel]     — results, standings, circuits, head-to-head
    - [SIMPLE: analysis]  — telemetry, pit strategy, predictions
    - [SIMPLE: steward]   — regulations, penalties, incident verdicts
    - [SIMPLE: scheduler] — calendar invites, race schedule
    - [COMPLEX]           — multiple domains, needs multi-agent sequencing

    Never answer directly. Never explain the classification to the user.
    """,
    sub_agents=[f1_coordinator],
    before_agent_callback=_log_agent_start,
    model=MODEL,
    generate_content_config=types.GenerateContentConfig(temperature=0)
)


# ─────────────────────────────────────────────
# 8. ENTRY POINT
# ─────────────────────────────────────────────
root_agent = f1_orchestrator
