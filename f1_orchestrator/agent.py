import os
import google.auth
import fastf1
import psycopg2
from datetime import datetime, timedelta
from google.adk.agents import LlmAgent
from google.genai import types
from google.adk.tools import ToolContext
from googleapiclient.discovery import build
from .schema import F1_TABLE_METADATA
import base64
import urllib.parse

# 1. SYSTEM INITIALIZATION
CACHE_DIR = '/tmp/fastf1_cache'
os.makedirs(CACHE_DIR, exist_ok=True)
fastf1.Cache.enable_cache(CACHE_DIR)

# 2. TEMPORAL CONTEXT
def get_current_context():
    now = datetime.now()
    today = now.strftime('%Y-%m-%d')
    return f"""Today's Date is {today}.
    TEMPORAL RULES:
    1. HISTORY: Any race scheduled ON or BEFORE {today} has already occurred. Provide actual results.
    2. FUTURE: Only races scheduled AFTER {today} are upcoming. Never simulate results for these — provide predictions instead.
    """

# 3. AUTHENTICATION & CONFIGURATION
credentials, project_id = google.auth.default(
    scopes=[
        'https://www.googleapis.com/auth/calendar',
        'https://www.googleapis.com/auth/gmail.send',
        'https://www.googleapis.com/auth/cloud-platform'
    ]
)
if not credentials.valid:
    credentials.refresh(google.auth.transport.requests.Request())

REGION = os.getenv("REGION")
CLUSTER = os.getenv("ALLOYDB_CLUSTER")
INSTANCE = os.getenv("ALLOYDB_INSTANCE")
MODEL = "gemini-2.5-flash"

# 4. TOOLS

def query_f1_db(sql_query: str):
    """
    Executes a SQL query against the F1 AlloyDB (f1db).
    Use for driver stats, historical race results, standings, and session data for seasons up to early 2026.
    """
    print(f"\n[TOOL: query_f1_db] Executing SQL: {sql_query}")
    try:
        conn = psycopg2.connect(
            host=os.getenv("ALLOYDB_HOST", "127.0.0.1"),
            port=os.getenv("ALLOYDB_PORT", "5433"),
            database=os.getenv("ALLOYDB_DATABASE", "f1db"),
            user=os.getenv("ALLOYDB_USER", "postgres"),
            password=os.getenv("ALLOYDB_PASSWORD")
        )
        cur = conn.cursor()
        cur.execute(sql_query)
        rows = cur.fetchall()
        colnames = [desc[0] for desc in cur.description]
        cur.close()
        conn.close()
        return f"Columns: {colnames}\nData: {rows}"
    except Exception as e:
        return f"Database Error: {str(e)}"

def get_f1_schedule(year: int):
    """
    Fetches the full F1 race schedule (dates, locations, round numbers) for a given year.
    Use to find upcoming races, confirm Grand Prix names, and identify circuit locations.
    - year: Calendar year (e.g. 2025, 2026).
    """
    print(f"\n[TOOL: get_f1_schedule] Fetching F1 Schedule for {year}")
    try:
        schedule = fastf1.get_event_schedule(year)
        overview = schedule[['RoundNumber', 'EventName', 'Location', 'EventDate', 'EventFormat']]
        return f"F1 Schedule for {year}:\n{overview.to_string(index=False)}"
    except Exception as e:
        return f"Schedule Error: {str(e)}"

def fetch_fastf1_live_data(year: int, gp_name: str, session_type: str = "R"):
    """
    Fetches session results from the FastF1 API. Use as a fallback when the local database (f1db)
    returns empty results, especially for 2025+ data.
    - year: e.g. 2024, 2025.
    - gp_name: e.g. 'Bahrain', 'Saudi Arabia'.
    - session_type: 'R' (Race), 'Q' (Qualifying), 'S' (Sprint), 'FP1', 'FP2', 'FP3'.
    """
    print(f"\n[TOOL: fetch_fastf1_live_data] Fetching FastF1 data: {year} {gp_name} {session_type}")
    try:
        session = fastf1.get_session(year, gp_name, session_type)
        session.load(telemetry=False, weather=False, messages=False)
        target_cols = ['ClassifiedPosition', 'FullName', 'TeamName', 'Status', 'Points', 'GridPosition', 'BestLapTime']
        available_cols = [c for c in target_cols if c in session.results.columns]
        results = session.results[available_cols]
        return f"Results for {year} {gp_name} {session_type}:\n{results.to_string(index=False)}"
    except Exception as e:
        return f"FastF1 Error: {str(e)}"

def get_f1_standings(year: int):
    """
    Fetches Drivers' and Constructors' championship standings for a given year.
    Returns two clean tables: Driver Standings and Constructor Standings.
    - year: The F1 season year (e.g., 2024, 2025, 2026).
    """
    print(f"\n[TOOL: get_f1_standings] Fetching Standings for {year}")
    try:
        latest_round_sql = f"SELECT max(round) FROM f1_standings WHERE season = {year}"

        driver_sql = f"""
        SELECT
            fs.position        AS pos,
            d.full_name        AS driver,
            fs.points          AS pts,
            fs.wins            AS wins
        FROM f1_standings fs
        JOIN f1_drivers d ON d.code = fs.entity_id
        WHERE fs.season = {year}
          AND fs.standing_type = 'driver'
          AND fs.round = ({latest_round_sql})
        ORDER BY fs.position ASC
        """

        constructor_sql = f"""
        SELECT
            fs.position        AS pos,
            t.name             AS constructor,
            fs.points          AS pts,
            fs.wins            AS wins
        FROM f1_standings fs
        JOIN f1_teams t ON t.team_id = fs.entity_id
        WHERE fs.season = {year}
          AND fs.standing_type = 'constructor'
          AND fs.round = ({latest_round_sql})
        ORDER BY fs.position ASC
        """

        driver_res = query_f1_db(driver_sql)
        constructor_res = query_f1_db(constructor_sql)

        if "Database Error" in driver_res and "Database Error" in constructor_res:
            return f"Standings Error: {driver_res}"

        if "Data: []" in driver_res and "Data: []" in constructor_res:
            return f"No standings data found for {year} in the database yet."

        return (
            f"=== {year} DRIVER STANDINGS ===\n{driver_res}\n\n"
            f"=== {year} CONSTRUCTOR STANDINGS ===\n{constructor_res}"
        )
    except Exception as e:
        return f"Standings Error: {str(e)}"

def fetch_f1_telemetry(year: int, gp_name: str, session_type: str, driver_id: str):
    """
    Fetches car telemetry (Speed, Throttle, Brake, Gear, RPM) for a specific driver's fastest lap.
    Use for performance analysis, cornering efficiency, and technical comparisons.
    - driver_id: 3-letter abbreviation (e.g. 'VER', 'HAM') or full name.
    """
    print(f"\n[TOOL: fetch_f1_telemetry] Analyzing car data for {driver_id} at {gp_name} {year}")
    try:
        session = fastf1.get_session(year, gp_name, session_type)
        session.load(telemetry=True, weather=False, messages=False)

        target_driver = driver_id
        if len(driver_id) > 3:
            match = session.results[session.results['FullName'].str.contains(driver_id, case=False, na=False)]
            if not match.empty:
                target_driver = match.iloc[0]['Abbreviation']

        laps = session.laps.pick_driver(target_driver)
        if laps.empty:
            return f"Telemetry Error: No laps recorded for driver '{target_driver}' in this session."

        fastest_lap = laps.pick_fastest()
        if fastest_lap is None:
            return f"Telemetry Error: Could not identify a fastest lap for driver '{target_driver}'."

        telemetry = fastest_lap.get_telemetry().iloc[::10, :]
        return f"Telemetry Summary (Fastest Lap) for {target_driver}:\n{telemetry[['Speed', 'Throttle', 'Brake', 'nGear', 'RPM']].describe().to_string()}"
    except Exception as e:
        return f"Telemetry Error: {str(e)}"

def fetch_f1_pit_strategy(year: int, gp_name: str):
    """
    Fetches pit stop stints (lap number, tyre compound, stint number) for all drivers in a race.
    Use for strategy analysis, undercut/overcut reviews, and tyre degradation assessment.
    """
    print(f"\n[TOOL: fetch_f1_pit_strategy] Analyzing pit stops for {gp_name} {year}")
    try:
        session = fastf1.get_session(year, gp_name, 'R')
        session.load(telemetry=False, weather=False, messages=False)
        stints = session.laps[['Driver', 'Stint', 'Compound', 'LapNumber']].drop_duplicates()
        return f"Pit/Stint Strategy for {gp_name} {year}:\n{stints.to_string(index=False)}"
    except Exception as e:
        return f"Pit Strategy Error: {str(e)}"

def fetch_f1_technical_details(year: int, gp_name: str, session_type: str):
    """
    Fetches track weather conditions and recent team radio transcripts for a session.
    Use to assess weather impact on strategy and driver-engineer communications.
    """
    print(f"\n[TOOL: fetch_f1_technical_details] Fetching Weather & Radio for {gp_name} {year}")
    try:
        session = fastf1.get_session(year, gp_name, session_type)
        session.load(telemetry=False, weather=True, messages=True)
        weather = session.weather_data.iloc[-1:].to_string(index=False)
        messages = "No radio messages available for this session."
        if hasattr(session, 'messages') and len(session.messages) > 0:
            messages = session.messages[['Time', 'Driver', 'Message']].tail(5).to_string(index=False)
        return f"Track Weather:\n{weather}\n\nRecent Radio Transcripts:\n{messages}"
    except Exception as e:
        return f"Technical Data Error: {str(e)}"

def send_f1_calendar_invite(event_name: str, start_time: str, location: str, recipient_email: str, tool_context: ToolContext):
    """
    Sends a Google Calendar invitation to a user's email for an upcoming F1 session.
    Use whenever the user wants to 'add', 'schedule', or 'set a reminder' for a race.
    - event_name: Full name of the session (e.g., 'Miami Grand Prix - Race')
    - start_time: ISO 8601 format (e.g., '2026-05-03T15:00:00Z')
    - location: Circuit or city name (e.g., 'Miami Gardens, USA')
    - recipient_email: The user's email address.
    """
    print(f"\n[TOOL: send_f1_calendar_invite] Sending invite: {event_name} → {recipient_email}")
    try:
        target_email = recipient_email or os.getenv("USER_EMAIL", "user@example.com")
        service = build('calendar', 'v3', credentials=credentials)
        start_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
        end_dt = start_dt + timedelta(hours=2)

        event_body = {
            'summary': event_name,
            'location': location,
            'description': f'F1 Session at {location}. Analysis by your AI Strategist.',
            'start': {'dateTime': start_dt.isoformat()},
            'end': {'dateTime': end_dt.isoformat()},
        }
        created_event = service.events().insert(calendarId=target_email, body=event_body).execute()
        return f"Invite added to {target_email}! (Event ID: {created_event.get('id')})"

    except Exception as calendar_err:
        print(f"[calendar] Direct write failed: {calendar_err}. Generating fallback link.")
        try:
            start_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
            end_dt = start_dt + timedelta(hours=2)
            fmt = "%Y%m%dT%H%M%SZ"
            params = {
                'action': 'TEMPLATE',
                'text': event_name,
                'dates': f"{start_dt.strftime(fmt)}/{end_dt.strftime(fmt)}",
                'details': f"F1 Session at {location}. Generated by your AI Strategist.",
                'location': location
            }
            template_url = f"https://www.google.com/calendar/render?{urllib.parse.urlencode(params)}"
            return f"Couldn't write directly to your calendar (permissions issue), but here's a **[One-Click Link]({template_url})** to add it yourself!"
        except Exception as link_err:
            return f"Calendar Error: {str(calendar_err)}"


# 5. SQL GUIDELINES (shared context)
SQL_GUIDELINES = """
SQL Best Practices for F1 Data:
1. JOINS: Always join 'f1_results' with 'f1_drivers' (on driver_id) and 'f1_teams' (on team_id) to return names.
2. SESSIONS: Join 'f1_sessions' to filter by race_name, season, or session_type (Race, Qualifying, etc.).
3. LIMITS: Always apply LIMIT 10 unless the user asks for a full list.
4. ORDERING: Order results logically (e.g., position ASC, points DESC, date DESC).
5. EMPTY RESULTS: If a query returns Data: [], immediately fallback to fetch_fastf1_live_data.
"""


# 6. SPECIALIST AGENTS

# SPECIALIST A: INTEL (ALL FETCHES)
# Owns every tool that makes a network or DB call.
# Returns structured raw data only — never interprets, never predicts.
f1_intel_agent = LlmAgent(
    name="f1_intel_agent",
    instruction=f"""
    {get_current_context()}

    You are the F1 Intelligence Officer. Your ONLY job is to fetch and return raw F1 data.
    You do NOT analyze, interpret, or predict — you retrieve and structure data clearly.

    TOOL USAGE POLICY:
    1. RESULTS & STANDINGS (up to early 2026): Use 'query_f1_db' first.
       - If it returns Data: [], IMMEDIATELY fallback to 'fetch_fastf1_live_data'.
       - If session awards (fastest_lap, pole) are all False in SQL, fallback to 'fetch_fastf1_live_data'.
    2. LIVE / 2025+ DATA: Use 'fetch_fastf1_live_data' directly.
    3. TELEMETRY: Use 'fetch_f1_telemetry' for raw Speed, Throttle, Brake, Gear, RPM numbers.
    4. PIT STINTS: Use 'fetch_f1_pit_strategy' for raw stint, compound, and lap number data.
    5. WEATHER & RADIO: Use 'fetch_f1_technical_details' for track conditions and team radio.
    6. SCHEDULE: Use 'get_f1_schedule' for calendars, dates, and circuit names.
    7. STANDINGS: Use 'get_f1_standings' for championship positions and points.

    OUTPUT FORMAT:
    - Return data in clearly labelled sections with headers (e.g. "## Telemetry — VER").
    - Never return raw Python tuples or column arrays. Convert to readable tables or lists.
    - For driver/team names: use full names, not codes (e.g. "Max Verstappen", not "VER").
    - If a source returns no data, say so explicitly and note which fallback you tried.
    - Never summarise or draw conclusions — that is f1_analysis_agent's job.

    SQL GUIDELINES:
    {SQL_GUIDELINES}

    SCHEMA CONTEXT:
    {F1_TABLE_METADATA}
    """,
    tools=[
        query_f1_db,
        fetch_fastf1_live_data,
        get_f1_schedule,
        get_f1_standings,
        fetch_f1_telemetry,
        fetch_f1_pit_strategy,
        fetch_f1_technical_details,
    ],
    model=MODEL,
    generate_content_config=types.GenerateContentConfig(temperature=0)
)

# SPECIALIST B: ANALYSIS & PREDICTION
# Self-sufficient for all performance analysis — fetches its own telemetry/pit/live data
# and reasons over it in one shot. Does NOT handle standings, results, or schedule lookups.
f1_analysis_agent = LlmAgent(
    name="f1_analysis_agent",
    instruction=f"""
    {get_current_context()}

    You are the F1 Performance Analyst & Race Strategist. You fetch the data you need
    and produce a complete analysis in one response — never ask the user for data.

    TOOL USAGE:
    1. TELEMETRY: Use 'fetch_f1_telemetry' for Speed, Throttle, Brake, Gear, RPM.
       Always fetch for every driver being compared — never skip one side of a comparison.
    2. PIT STRATEGY: Use 'fetch_f1_pit_strategy' for stint lengths, tyre compounds, pit laps.
       Fetch this proactively for any race analysis — do not wait to be asked.
    3. WEATHER & RADIO: Use 'fetch_f1_technical_details' for track conditions and engineer comms.
    4. RACE RESULTS (context): Use 'fetch_fastf1_live_data' for session finishing order.
    5. HISTORICAL CONTEXT: Use 'query_f1_db' to pull career stats or past race results
       that support a prediction or comparison.

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
    | Stint | Driver      | Laps      | Compound | Note              |
    |-------|-------------|-----------|----------|-------------------|
    | 1     | [Driver A]  | L1 – L00  | SOFT     | ...               |
    | 1     | [Driver B]  | L1 – L00  | MEDIUM   | ...               |

    > **Decisive moment:** [one sentence on the key strategic call]

    ### Verdict
    **Strategic edge:** [Driver] — [one sentence reason]
    **Performance edge:** [Driver] — [one sentence reason]
    **Race outcome factor:** [what ultimately separated them]

    ---

    FORMATTING RULES:
    - Always use the table structures above — never use bullet lists for numbers.
    - Round telemetry values: speeds to 1 decimal, throttle/brake to 1 decimal, RPM to nearest integer.
    - Use full names in headers (e.g. "Max Verstappen"), abbreviations only inside tables (VER/HAM).
    - For predictions, use a numbered podium list, not a table.
    - Never output raw stat blocks (describe() output) — always convert to the table format above.
    - Keep prose sections (Decisive moment, Verdict) to 1–2 sentences each.
    """,
    tools=[
        fetch_f1_telemetry,
        fetch_f1_pit_strategy,
        fetch_f1_technical_details,
        fetch_fastf1_live_data,
        query_f1_db,
    ],
    model=MODEL,
    generate_content_config=types.GenerateContentConfig(temperature=0.4)
)

# SPECIALIST C: EVENT SCHEDULING
# Owns schedule lookups and calendar invite delivery only.
# Does NOT retrieve results, standings, or performance data.
f1_event_scheduler = LlmAgent(
    name="f1_event_scheduler",
    instruction=f"""
    {get_current_context()}

    You are the F1 Event Scheduler. Help users discover upcoming races and add them to
    their Google Calendar.

    WORKFLOW:
    1. DISCOVER: Use 'get_f1_schedule' to find the event the user wants.
       - For "next race" requests, fetch the 2026 schedule and find the first event AFTER today.
       - Confirm the exact event name, ISO 8601 date, and location before proceeding.
    2. SCHEDULE: Use 'send_f1_calendar_invite' with the confirmed event details.
       Always use the exact ISO 8601 start_time from the schedule output.

    RULES:
    - If the user has not provided their email, ask before sending.
    - Include the session type in event_name if specified (e.g. 'Miami Grand Prix — Qualifying').
    - If the calendar write fails, the tool returns a One-Click Link — present it prominently.
    - Do NOT answer questions about race results, standings, or driver performance.
    """,
    tools=[get_f1_schedule, send_f1_calendar_invite],
    model=MODEL,
    generate_content_config=types.GenerateContentConfig(temperature=0)
)


# 7. ORCHESTRATION LAYER
f1_orchestrator = LlmAgent(
    name="race_strategist",
    instruction=f"""
    {get_current_context()}

    You are the Senior F1 Race Strategist — the Pit Wall Director. You coordinate three
    specialists. Route each request to exactly one agent — never chain or loop between agents.

    ═══════════════════════════════════════════════════════
    AGENT ROSTER
    ═══════════════════════════════════════════════════════
    • f1_intel_agent    — DB lookups, standings, results, schedule, historical stats
    • f1_analysis_agent — telemetry, pit strategy, head-to-head comparisons, predictions
                          (self-sufficient: fetches its own data and produces analysis)
    • f1_event_scheduler — schedule lookup + Google Calendar invites

    ═══════════════════════════════════════════════════════
    ROUTING — one agent per request, no exceptions
    ═══════════════════════════════════════════════════════

    → f1_intel_agent:
      Race results, finishing positions, lap times, race winners, driver/constructor
      standings, championship points, historical stats (wins, poles, DNFs), session data.

    → f1_analysis_agent:
      Telemetry analysis, car performance, pit stop strategy, tyre compounds,
      head-to-head driver comparisons, race predictions, "who will win" questions,
      strategy recommendations, track condition impact.

    → f1_event_scheduler:
      Adding a race to calendar, scheduling reminders, any calendar invite request.

    ═══════════════════════════════════════════════════════
    OUTPUT RULES
    ═══════════════════════════════════════════════════════
    • Never show raw tuples, column arrays, or Python data structures to the user.
    • Present the final answer as clean markdown — tables for standings, prose for analysis.
    • Use full driver and team names throughout (e.g. "Lewis Hamilton", not "HAM").
    • If a sub-agent returns an error, report it clearly — do not retry silently.
    """,
    sub_agents=[f1_intel_agent, f1_analysis_agent, f1_event_scheduler],
    tools=[],
    model=MODEL,
    generate_content_config=types.GenerateContentConfig(temperature=0)
)

# 8. ENTRY POINT
root_agent = f1_orchestrator
