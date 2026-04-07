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
# Configures FastF1 caching for ephemeral environments (e.g. Cloud Run)
CACHE_DIR = '/tmp/fastf1_cache'
os.makedirs(CACHE_DIR, exist_ok=True)
fastf1.Cache.enable_cache(CACHE_DIR)

# 2. TEMPORAL CONTEXT
# Dynamic context injected into agent prompts to ensure time-relative accuracy.
def get_current_context():
    now = datetime.now()
    today = now.strftime('%Y-%m-%d')
    return f"""Today's Date is {today}. 
    TEMPORAL RULES:
    1. HISTORY: Any race scheduled ON or BEFORE {today} has already occurred. You MUST provide results and standings for these.
    2. FUTURE: Only races scheduled AFTER {today} are in the future. Do not simulate results for these.
    """


# 3. AUTHENTICATION & CONFIGURATION
# Added specific scope for Google Calendar and Gmail API access
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

# 4. SYSTEM TOOLS (CORE CAPABILITIES)

def query_f1_db(sql_query: str):
    """
    SYSTEM CAPABILITY: Executes a SQL query against the F1 AlloyDB (f1db).
    Use this for driver stats, historical race results, standings, and telemetry lookups for Seasons < 2025.
    """
    print(f"\n[TOOL: query_f1_db] Executing SQL: {sql_query}")
    try:
        # Use environment variables, defaulting to your local proxy settings (127.0.0.1:5433).
        db_host = os.getenv("ALLOYDB_HOST", "127.0.0.1")
        db_port = os.getenv("ALLOYDB_PORT", "5433")
        db_name = os.getenv("ALLOYDB_DATABASE", "f1db")
        db_user = os.getenv("ALLOYDB_USER", "postgres")
        db_pass = os.getenv("ALLOYDB_PASSWORD")

        # Connect via the Proxy (local or Cloud Run sidecar)
        conn = psycopg2.connect(
            host=db_host,
            port=db_port,
            database=db_name,
            user=db_user,
            password=db_pass
        )
        cur = conn.cursor()
        cur.execute(sql_query)
        rows = cur.fetchall()
        
        # Get column names for better AI readability
        colnames = [desc[0] for desc in cur.description]
        cur.close()
        conn.close()
        
        return f"Columns: {colnames}\nData: {rows}"
    except Exception as e:
        return f"Database Error: {str(e)}"

def get_f1_schedule(year: int):
    """
    SYSTEM CAPABILITY: Fetches the full F1 race schedule, dates, and locations for a specific year.
    Use this tool to discover upcoming races, identify Grand Prix names, and confirm circuit names.
    - year: The calendar year to look up (e.g. 2024, 2025, 2026).
    """
    print(f"\n[TOOL: get_f1_schedule] Fetching F1 Schedule for {year}")
    try:
        schedule = fastf1.get_event_schedule(year)
        # Select relevant columns for a concise overview
        overview = schedule[['RoundNumber', 'EventName', 'Location', 'EventDate', 'EventFormat']]
        return f"F1 Schedule for {year}:\n{overview.to_string(index=False)}"
    except Exception as e:
        return f"Schedule Error: {str(e)}"

def fetch_fastf1_live_data(year: int, gp_name: str, session_type: str = "R"):
    """
    SYSTEM CAPABILITY: Fetches real-time or recent F1 session data using the live FastF1 API.
    Use this tool as a fallback if the local database (f1db) is missing 2025+ data.
    - year: e.g. 2024, 2025.
    - gp_name: e.g. 'Bahrain', 'Saudi Arabia'.
    - session_type: 'R' (Race), 'Q' (Qualifying), 'S' (Sprint), 'FP1', 'FP2', 'FP3'.
    """
    print(f"\n[TOOL: fetch_fastf1_live_data] Fetching FastF1 data: {year} {gp_name} {session_type}")
    try:
        session = fastf1.get_session(year, gp_name, session_type)
        session.load(telemetry=False, weather=False, messages=False)
        
        # Select more comprehensive columns to cover Grid, Points, and Fastest Lap
        results = session.results[['ClassifiedPosition', 'FullName', 'TeamName', 'Status', 'Points', 'GridPosition', 'LapTime']]
        return f"Results for {year} {gp_name} {session_type}:\n{results.to_string(index=False)}"
    except Exception as e:
        return f"FastF1 Error: {str(e)}"

def fetch_f1_telemetry(year: int, gp_name: str, session_type: str, driver_id: str):
    """
    SYSTEM CAPABILITY: Fetches technical car telemetry (Speed, Throttle, Brake, Gear) for a driver.
    Use this for performance analysis, cornering efficiency, and technical comparisons.
    """
    print(f"\n[TOOL: fetch_f1_telemetry] Analyzing car data for {driver_id} at {gp_name} {year}")
    try:
        session = fastf1.get_session(year, gp_name, session_type)
        session.load(telemetry=True, weather=False, messages=False)
        laps = session.laps.pick_driver(driver_id)
        fastest_lap = laps.pick_fastest()
        telemetry = fastest_lap.get_telemetry().iloc[::10, :] # Downsample for AI readability
        return f"Telemetry Summary (Fastest Lap) for {driver_id}:\n{telemetry[['Speed', 'Throttle', 'Brake', 'nGear', 'RPM']].describe().to_string()}"
    except Exception as e:
        return f"Telemetry Error: {str(e)}"

def fetch_f1_pit_strategy(year: int, gp_name: str):
    """
    SYSTEM CAPABILITY: Fetches pit stop durations and lap numbers for all drivers in a session.
    Use this for strategy analysis, undercut/overcut reviews, and pit crew performance.
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
    SYSTEM CAPABILITY: Fetches track conditions (Weather) and latest team radio transcripts.
    Use this to understand weather impacts and driver-engineer communications.
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

def get_f1_standings(year: int):
    """
    SYSTEM CAPABILITY: Fetches the Drivers' and Constructors' standings for a specific year.
    Use this to identify champions, current leaders, and team performance trends.
    - year: The F1 season year (e.g., 2024, 2025, 2026).
    """
    print(f"\n[TOOL: get_f1_standings] Fetching Standings for {year}")
    try:
        # 1. Primary: Use AlloyDB (f1db)
        # Simplified query to find the latest standing for the year
        sql = f"""
        SELECT position, points, wins, standing_type, entity_id 
        FROM f1_standings 
        WHERE season = {year} 
        AND round = (SELECT max(round) FROM f1_standings WHERE season = {year})
        ORDER BY position ASC
        """
        db_res = query_f1_db(sql)
        if "Data: []" not in db_res and "Database Error" not in db_res:
             return f"F1 Standings for {year} (via f1db):\n{db_res}"
             
        # 2. Fallback: FastF1 (Ergast) Standings (Logic placeholder)
        return f"Standings data for {year} is not available in the database yet. Please use 'get_f1_schedule' to manually check race winners."
    except Exception as e:
        return f"Standings Error: {str(e)}"

def send_f1_calendar_invite(event_name: str, start_time: str, location: str, recipient_email: str, tool_context: ToolContext):
    """
    SYSTEM CAPABILITY: Sends a formal Google Calendar Invitation directly to a specific email inbox.
    Use this tool whenever a user wants to 'add', 'schedule', or 'invite' themselves to an upcoming F1 session.
    - event_name: Full name of the Grand Prix or Session (e.g., 'Miami Grand Prix - Race')
    - start_time: ISO 8601 format string (e.g., '2026-05-03T15:00:00Z')
    - location: The circuit or city name (e.g., 'Miami Gardens, USA')
    - recipient_email: The email address that should receive the invitation.
    Returns a status message once the invitation is queued for delivery.
    - recipient_email: The email address that should receive the invitation.
    Returns a status message once the invitation is queued for delivery.
    """
    print(f"\n[TOOL: send_f1_calendar_invite] Sending Calendar Invite for: {event_name} at {location} for {recipient_email}")
    
    # EXECUTION
    try:
        import sys
        # Use provided email, fallback to environment default
        target_email = recipient_email if recipient_email else os.getenv("USER_EMAIL", "ashwini.sharma@example.com")
        
        print(f"DEBUG: Initializing Google Calendar Service for {target_email}...", flush=True)
        service = build('calendar', 'v3', credentials=credentials)
        
        # Calculate end time (standard 2h duration)
        start_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
        end_dt = start_dt + timedelta(hours=2)
        
        event_body = {
            'summary': event_name,
            'location': location,
            'description': f'F1 Session at {location}. Analysis by your AI Strategist.',
            'start': {'dateTime': start_dt.isoformat()},
            'end': {'dateTime': end_dt.isoformat()},
        }
        
        print(f"DEBUG: Attempting direct write to calendar: {target_email}...", flush=True)
        # Attempt direct write (target_email as calendarId)
        created_event = service.events().insert(
            calendarId=target_email, 
            body=event_body
        ).execute()
        
        print(f"DEBUG: Event created successfully! ID: {created_event.get('id')}", flush=True)
        return f"Invite added directly to your calendar ({target_email})! (Event ID: {created_event.get('id')})"

    except Exception as calendar_err:
        print(f"DEBUG: Calendar Direct Write Failed: {str(calendar_err)}", flush=True)
        print(f"DEBUG: Generating One-Click Template Link as fallback...", flush=True)
        
        # FALLBACK: ONE-CLICK GOOGLE CALENDAR LINK
        # Pre-fill a URL that allows the user to add it manually in 1 click
        try:
            from datetime import datetime
            start_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
            end_dt = start_dt + timedelta(hours=2)
            
            # Format dates for Google: YYYYMMDDTHHMMSSZ
            fmt = "%Y%m%dT%H%M%SZ"
            g_start = start_dt.strftime(fmt)
            g_end = end_dt.strftime(fmt)
            
            # Build URL parameters
            params = {
                'action': 'TEMPLATE',
                'text': event_name,
                'dates': f"{g_start}/{g_end}",
                'details': f"F1 Session at {location}. Generated by your AI Strategist.",
                'location': location
            }
            template_url = f"https://www.google.com/calendar/render?{urllib.parse.urlencode(params)}"
            
            print(f"DEBUG: Template Link generated successfully!", flush=True)
            return f"I couldn't write directly to your calendar (permissions), but you can add it manually with this **[One-Click Link]({template_url})**!"
            
        except Exception as link_err:
            print(f"DEBUG: Link Template also failed: {str(link_err)}", flush=True)
            return f"Calendar Error. Please verify your calendar is shared with the bot. Details: {str(calendar_err)}"


# 5. AGENT DEFINITIONS (MULTI-AGENT SWARM)

# SPECIALIST: THE PREDICTIVE ANALYST
f1_race_predictor = LlmAgent(
    name="f1_race_predictor",
    instruction=f"""
    {get_current_context()}
    
    You are the AI Performance & Strategy Analyst. Your goal is to predict race outcomes and analyze historical performance.
    
    YOUR SPECIALIZATION:
    1. TREND ANALYSIS: Analyze historical results and technical telemetry to identify momentum.
    2. PERFORMANCE MODELING: Factor in cornering speed, throttle usage, and energy management from telemetry tools.
    3. STRATEGY: Review pit stop data to identify undercut/overcut opportunities.
    4. PREDICTION: For upcoming races, provide:
       - Top 3 Podium Picks (with Probability of Win %).
       - "Driver to Watch" (a mid-field sleeper).
       - Strategy Insight based on weather and pit data.
    """,
    tools=[query_f1_db, fetch_f1_telemetry, fetch_f1_pit_strategy], 
    model=MODEL
)

# GUIDELINES FOR DATA ENGINEERING
SQL_GUIDELINES = """
SQL Best Practices for F1 Data:
1. JOINS: Always join 'f1_results' with 'f1_drivers' (on driver_id) and 'f1_teams' (on team_id) to return names instead of IDs.
2. SESSIONS: Join with 'f1_sessions' to filter by race_name, season, or session_type (Race, Qualifying, etc.).
3. LIMITS: Always apply 'LIMIT 10' to queries unless explicitly asked for a full list.
4. ORDERING: Order results logically (e.g., position ASC, points DESC, date DESC).
"""

# SPECIALIST: THE DATA ENGINEER
f1_data_engineer = LlmAgent(
    name="f1_data_engineer",
    instruction=f"""
    {get_current_context()}
    
    You are the F1 Data Engineering Lead. You are the source of truth for all telemetry, results, and standings.
    
    DATA SOURCE POLICY:
    1. PRIMARY (f1db): Use 'query_f1_db' for core standings, results, and sessions (2020 through March 2026). 
       - CRITICAL: If 'query_f1_db' returns an empty list (`Data: []`), you MUST fallback to 'fetch_fastf1_live_data' immediately.
    2. SESSION AWARDS (Fastest Lap/Pole): If SQL results are inconclusive (all False), you MUST fallback to 'fetch_fastf1_live_data' to confirm official awards.
    3. TELEMETRY/TECHNICAL: Use 'fetch_f1_telemetry', 'fetch_f1_pit_strategy', and 'fetch_f1_technical_details' for deep car/track data.
    4. FALLBACK: Use 'get_f1_schedule' or 'get_f1_standings' for any years not fully covered in SQL.
    
    SQL GUIDELINES:
    {SQL_GUIDELINES}
    
    SCHEMA CONTEXT:
    {F1_TABLE_METADATA}
    """,
    tools=[query_f1_db, fetch_fastf1_live_data, get_f1_schedule, get_f1_standings, fetch_f1_telemetry, fetch_f1_pit_strategy, fetch_f1_technical_details, send_f1_calendar_invite],
    model=MODEL,
    generate_content_config=types.GenerateContentConfig(temperature=0)
)

# 6. ORCHESTRATION LAYER (THE HEAD OF STRATEGY)
f1_orchestrator = LlmAgent(
    name="race_strategist",
    instruction=f"""
    {get_current_context()}
    
    You are the Senior F1 Race Strategist on the pit wall.
    
    ### MANDATORY DIRECTIVES
    1. CALENDAR: Use 'send_f1_calendar_invite' for ANY race adding request.
    2. ANALYSIS: Perform "Head-to-Head" driver comparisons using both SQL results and Telemetry data.
    3. STRATEGY: Synthesize pit stop durations and weather data into your final race predictions.
    
    ### DELEGATION PROTOCOL:
    - Raw Standings/Results/Technical Data: Delegate to 'f1_data_engineer'.
    - Deep Strategy Trends/Predictions: Delegate to 'f1_race_predictor'.
    - Championship Scenarios: You coordinate the analysis between Engineer and Analyst.
    """,
    sub_agents=[f1_data_engineer, f1_race_predictor],
    tools=[get_f1_standings, fetch_f1_telemetry, send_f1_calendar_invite],
    model=MODEL,
    generate_content_config=types.GenerateContentConfig(temperature=0)
)

# 7. EXPORT ENTRY POINT
# The ADK Runner looks for 'root_agent' to begin execution.
root_agent = f1_orchestrator
