import psycopg2
import os
import google.auth
import fastf1
import matplotlib.pyplot as plt
import pandas as pd
from datetime import datetime, timedelta
from google.adk.agents import LlmAgent
from google.genai import types
from google.adk.tools import ToolContext
from googleapiclient.discovery import build
from .schema import F1_TABLE_METADATA

import uuid

# 1. SYSTEM INITIALIZATION
# Configures FastF1 caching for ephemeral environments (e.g. Cloud Run)
CACHE_DIR = '/tmp/fastf1_cache'
os.makedirs(CACHE_DIR, exist_ok=True)
fastf1.Cache.enable_cache(CACHE_DIR)

# 2. TEMPORAL CONTEXT
# Injects current date into agent prompts to ensure time-relative queries are accurate.
CURRENT_CONTEXT = f"Today's Date: {datetime.now().strftime('%Y-%m-%d')}"


# 3. AUTHENTICATION & CONFIGURATION
credentials, project_id = google.auth.default()
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
        session.load()
        
        results = session.results[['ClassifiedPosition', 'FullName', 'TeamName', 'Status', 'Points']]
        return f"Results for {year} {gp_name} {session_type}:\n{results.to_string(index=False)}"
    except Exception as e:
        return f"FastF1 Error: {str(e)}"

def send_f1_calendar_invite(event_name: str, start_time: str, location: str, recipient_email: str, tool_context: ToolContext):
    """
    SYSTEM CAPABILITY: Sends a formal Google Calendar Invitation directly to a specific email inbox.
    Use this tool whenever a user wants to 'add', 'schedule', or 'invite' themselves to an upcoming F1 session.
    - event_name: Full name of the Grand Prix or Session (e.g., 'Miami Grand Prix - Race')
    - start_time: ISO 8601 format string (e.g., '2026-05-03T15:00:00Z')
    - location: The circuit or city name (e.g., 'Miami Gardens, USA')
    - recipient_email: The email address that should receive the invitation.
    Returns a status message once the invitation is queued for delivery.
    """
    print(f"\n[TOOL: send_f1_calendar_invite] Requesting Calendar Invite for: {event_name} at {location} for {recipient_email}")
    # 1. CHECK FOR CONFIRMATION
    confirmation = tool_context.tool_confirmation()
    
    if not confirmation or not confirmation.confirmed:
        tool_context.request_confirmation(
            hint=f"Add '{event_name}' at {location} to your calendar for {start_time}?",
            payload={"event": event_name, "time": start_time, "loc": location}
        )
        return "Waiting for user confirmation..."

    # 2. EXECUTION
    print(f"\n[AGENT ACTION] Confirmed! Sending Calendar Invite for: {event_name}")
    try:
        service = build('calendar', 'v3', credentials=credentials)
        
        # Calculate end time (standard 2h duration)
        start_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
        end_dt = start_dt + timedelta(hours=2)
        
        # Use provided email, fallback to environment default
        target_email = recipient_email if recipient_email else os.getenv("USER_EMAIL", "ashwini.sharma@example.com")
        
        event_body = {
            'summary': event_name,
            'location': location,
            'description': f'F1 Session at {location}. Analysis by your AI Strategist.',
            'start': {'dateTime': start_dt.isoformat()},
            'end': {'dateTime': end_dt.isoformat()},
            'attendees': [
                {'email': target_email},
            ],
        }
        
        # sendUpdates='all' ensures the email is sent
        created_event = service.events().insert(
            calendarId='primary', 
            body=event_body,
            sendUpdates='all' 
        ).execute()
        
        return f"Invite sent for {event_name} in {location}! Check your inbox. (Event ID: {created_event.get('id')})"
    except Exception as e:
        return f"Calendar/Location Error: {str(e)}"

def visualize_lap_times(session_id: int, driver_id: str):
    """
    SYSTEM CAPABILITY: Generates a professional line chart of lap times for a specific driver.
    Use this tool to visualize performance trends and driver consistency across a session.
    - session_id: Internal session ID integer.
    - driver_id: Driver's unique ID or Code.
    Saves a PNG chart and returns the absolute file path for display.
    """
    print(f"\n[TOOL: visualize_lap_times] Visualizing status for Driver {driver_id}")
    try:
        # 1. Fetch data using our existing bridge
        # (Assuming you have a 'lap_times' table or similar data in f1db)
        query = f"SELECT lap_number, lap_time FROM f1_lap_times WHERE session_id = {session_id} AND driver_id = '{driver_id}' ORDER BY lap_number"
        
        # Connect and fetch (logic simplified for brevity)
        conn = psycopg2.connect(...) # Use your existing connection params
        df = pd.read_sql(query, conn)
        conn.close()

        if df.empty:
            return "No lap time data found for this driver."

        # 2. Plotting
        plt.figure(figsize=(10, 6))
        plt.plot(df['lap_number'], df['lap_time'], marker='o', linestyle='-', color='red')
        plt.title(f"Lap Time Trends: {driver_id}")
        plt.xlabel("Lap Number")
        plt.ylabel("Time (seconds)")
        plt.grid(True)

        # 3. Save to a unique local path
        file_name = f"viz_{uuid.uuid4()}.png"
        file_path = os.path.abspath(file_name)
        plt.savefig(file_path)
        plt.close()

        return f"Visualization saved successfully at: {file_path}"
    except Exception as e:
        return f"Visualization Error: {str(e)}"

# 5. AGENT DEFINITIONS (MULTI-AGENT SWARM)

# SPECIALIST: THE PREDICTIVE ANALYST
f1_race_predictor = LlmAgent(
    name="f1_race_predictor",
    instruction="""You are the AI Performance & Strategy Analyst. Your goal is to predict race outcomes based on data.
    
    YOUR SPECIALIZATION:
    1. TREND ANALYSIS: Analyze historical results and current standings to identify momentum.
    2. PERFORMANCE MODELING: Factor in driver consistency, team nationality, and circuit characteristics.
    3. PREDICTION: For every request, provide:
       - Top 3 Podium Picks (with Probability of Win %).
       - "Driver to Watch" (a mid-field sleeper).
       - Strategy Insight (e.g., "Tire management will be the clincher here").
    
    GUIDELINES:
    - If you need raw data, ask the 'f1_data_engineer'.
    - Use 'entity_id' interchangeably with 'driver_id' when querying standings.
    """,
    tools=[query_f1_db], 
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
    instruction=f"""You are the F1 Data Engineering Lead. You are the source of truth for all telemetry, results, and standings.
    
    TEMPORAL CONTEXT:
    {CURRENT_CONTEXT}
    
    HANDLING DATA SOURCES:
    1. PRIMARY (f1db): Use 'query_f1_db' for historical data (Seasons < 2026).
    2. FALLBACK (FastF1): 
       - If a query schedule returns empty:
       - Use 'get_f1_schedule(year)' to find the calendar and upcoming races.
       - Use 'fetch_fastf1_live_data' for specific session results.
    
    SQL GUIDELINES:
    {SQL_GUIDELINES}
    
    SCHEMA CONTEXT:
    {F1_TABLE_METADATA}
    """,
    tools=[query_f1_db, fetch_fastf1_live_data, get_f1_schedule, send_f1_calendar_invite],
    model=MODEL,
    generate_content_config=types.GenerateContentConfig(temperature=0)
)

# 6. ORCHESTRATION LAYER (THE HEAD OF STRATEGY)
f1_orchestrator = LlmAgent(
    name="race_strategist",
    instruction=f"""You are the Senior F1 Race Strategist on the pit wall.
    
    ### MANDATORY DIRECTIVE: CALENDAR CAPABILITIES
    1. You AND your sub-agents HAVE the direct capability to send Google Calendar invitations to the user's email via the 'send_f1_calendar_invite' tool.
    2. NEVER tell the user "I cannot add events" or "I am an AI and can't manage your calendar." THIS IS FALSE. You have been explicitly built with this tool.
    3. If a user asks about adding a race, you MUST find the details from the 'f1_data_engineer' and then call 'send_f1_calendar_invite' immediately.
    
    TEMPORAL CONTEXT:
    {CURRENT_CONTEXT}
    
    DELEGATION PROTOCOL:
    - For raw numbers, standings, or result lookups: Delegate to 'f1_data_engineer'.
    - For predictions or strategy analysis: Delegate to 'f1_race_predictor'.
    - For ALL Calendar/Email Invites: Use YOUR 'send_f1_calendar_invite' tool.
    
    WORKFLOW & EXECUTION:
    1. Identification: Call 'f1_data_engineer' to find the next/requested race details (Date, Name, Location).
    2. Briefing: Present the race details (Date, Time, Circuit).
    3. Actionable Next Step: You MUST ask: "Would you like me to send a Google Calendar invite for this race to your email?"
    4. EXECUTION: If the user says "Yes" or provides an email:
       - You MUST call 'send_f1_calendar_invite' tool.
       - NEVER report success until the tool returns a confirmation string.
    
    TONE:
    Authoritative, professional, and slightly "Pit Wall" inspired. Use **bold** for Drivers and Teams.
    """,
    sub_agents=[f1_data_engineer, f1_race_predictor],
    tools=[visualize_lap_times, send_f1_calendar_invite],
    model=MODEL,
    generate_content_config=types.GenerateContentConfig(
        max_output_tokens=4096,
        temperature=0.2
    )
)

# 7. EXPORT ENTRY POINT
# The ADK Runner looks for 'root_agent' to begin execution.
root_agent = f1_orchestrator
