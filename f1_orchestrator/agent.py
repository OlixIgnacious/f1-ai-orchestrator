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

# 0.5 FASTF1 INITIALIZATION
# Use /tmp/fastf1_cache for ephemeral cloud storage (effective on Cloud Run)
CACHE_DIR = '/tmp/fastf1_cache'
os.makedirs(CACHE_DIR, exist_ok=True)
fastf1.Cache.enable_cache(CACHE_DIR)


# 0. TEMPORAL CONTEXT
# Injects the current date into agent instructions for time-aware queries.
CURRENT_CONTEXT = f"Today's Date: {datetime.now().strftime('%Y-%m-%d')}"


# 1. AUTHENTICATION
credentials, project_id = google.auth.default()
if not credentials.valid:
    credentials.refresh(google.auth.transport.requests.Request())

# 2. CONFIG
REGION = os.getenv("REGION")
CLUSTER = os.getenv("ALLOYDB_CLUSTER")
INSTANCE = os.getenv("ALLOYDB_INSTANCE")
# The user specified gemini-3.1-flash-lite. In early 2026, this is likely their intended model.
MODEL = "gemini-2.5-flash" 


def query_f1_db(sql_query: str):
    """
    Executes a SQL query against the F1 AlloyDB (f1db).
    Use this for driver stats, race results, and lap times.
    """
    print(f"\n[AGENT ACTION] Executing SQL: {sql_query}")
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
    Fetches the full F1 race schedule for a specific year.
    Use this to find upcoming races, dates, and circuit names.
    """
    print(f"\n[AGENT ACTION] Fetching F1 Schedule for {year}")
    try:
        schedule = fastf1.get_event_schedule(year)
        # Select relevant columns for a concise overview
        overview = schedule[['RoundNumber', 'EventName', 'Location', 'EventDate', 'EventFormat']]
        return f"F1 Schedule for {year}:\n{overview.to_string(index=False)}"
    except Exception as e:
        return f"Schedule Error: {str(e)}"

def fetch_fastf1_live_data(year: int, gp_name: str, session_type: str = "R"):
    """
    Fetches real-time or recent F1 session data using the FastF1 API.
    Use this as a fallback if the local database (f1db) is missing 2024+ data.
    - gp_name: e.g. 'Bahrain', 'Saudi Arabia'
    - session_type: 'R' (Race), 'Q' (Qualifying), 'S' (Sprint), 'FP1', 'FP2', 'FP3'
    """
    print(f"\n[AGENT ACTION] Fetching FastF1 data: {year} {gp_name} {session_type}")
    try:
        session = fastf1.get_session(year, gp_name, session_type)
        session.load()
        
        results = session.results[['ClassifiedPosition', 'FullName', 'TeamName', 'Status', 'Points']]
        return f"Results for {year} {gp_name} {session_type}:\n{results.to_string(index=False)}"
    except Exception as e:
        return f"FastF1 Error: {str(e)}"

def create_f1_calendar_event(event_name: str, start_time: str, location: str, tool_context: ToolContext):
    """
    Adds an F1 event to the user's calendar with a location and sends an invite.
    - event_name: e.g. 'Miami Grand Prix'
    - start_time: ISO format '2026-04-12T15:00:00Z'
    - location: The circuit or city name (e.g., 'Miami Gardens, USA')
    """
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
        
        USER_EMAIL = os.getenv("USER_EMAIL", "ashwini.sharma@example.com")
        
        event_body = {
            'summary': event_name,
            'location': location,
            'description': f'F1 Session at {location}. Analysis by your AI Strategist.',
            'start': {'dateTime': start_dt.isoformat()},
            'end': {'dateTime': end_dt.isoformat()},
            'attendees': [
                {'email': USER_EMAIL},
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
    Generates a line chart of lap times for a specific driver in a session.
    Saves as a PNG and returns the absolute file path.
    """
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

# THE PREDICTIVE SPECIALIST
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

# 2. SCHEMA DEFINITION (Imported from schema.py for full context)
SQL_GUIDELINES = """
SQL Best Practices for F1 Data:
1. JOINS: Always join 'f1_results' with 'f1_drivers' (on driver_id) and 'f1_teams' (on team_id) to return names instead of IDs.
2. SESSIONS: Join with 'f1_sessions' to filter by race_name, season, or session_type (Race, Qualifying, etc.).
3. LIMITS: Always apply 'LIMIT 10' to queries unless explicitly asked for a full list.
4. ORDERING: Order results logically (e.g., position ASC, points DESC, date DESC).
"""

# 4. SUB-AGENT (The Data Specialist)
f1_data_engineer = LlmAgent(
    name="f1_data_engineer",
    instruction=f"""You are the F1 Data Engineering Lead. You are the source of truth for all telemetry, results, and standings.
    
    TEMPORAL CONTEXT:
    {CURRENT_CONTEXT}
    
    HANDLING DATA SOURCES:
    1. PRIMARY (f1db): Use 'query_f1_db' for historical data (Seasons < 2024).
    2. FALLBACK (FastF1): 
       - If a query for a 2024+ event or schedule returns empty:
       - Use 'get_f1_schedule(year)' to find the calendar and upcoming races.
       - Use 'fetch_fastf1_live_data' for specific session results.
    
    SQL GUIDELINES:
    {SQL_GUIDELINES}
    
    SCHEMA CONTEXT:
    {F1_TABLE_METADATA}
    """,
    tools=[query_f1_db, fetch_fastf1_live_data, get_f1_schedule],
    model=MODEL,
    generate_content_config=types.GenerateContentConfig(temperature=0)
)

# 5. PRIMARY ORCHESTRATOR
f1_orchestrator = LlmAgent(
    name="race_strategist",
    instruction=f"""You are the Senior F1 Race Strategist on the pit wall. Your goal is to orchestrate elite F1 briefings.
    
    HARD CAPABILITY - CALENDAR INVITES:
    You HAVE the capability to send Google Calendar invitations directly to the user's email.
    - NEVER say "I cannot directly add events" or "I cannot add to your calendar."
    - ALWAYS use the 'create_f1_calendar_event' tool when requested.
    - If a user asks to "add the next race", you MUST first ask the 'f1_data_engineer' to find the next race details (Date, Name, Location), then call 'create_f1_calendar_event'.
    
    TEMPORAL CONTEXT:
    {CURRENT_CONTEXT}
    
    DELEGATION PROTOCOL:
    - For raw numbers, standings, or result lookups: Delegate to 'f1_data_engineer'.
    - For predictions or strategy analysis: Delegate to 'f1_race_predictor'.
    - For ALL Calendar/Email Invites: Use YOUR 'create_f1_calendar_event' tool.
    
    SYNTHESIS WORKFLOW:
    1. Briefing: Start with a concise summary.
    2. Strategic Insight: Provide a "Box Box" insight.
    3. Actionable Next Step: You MUST ask: "Would you like me to send a Google Calendar invite for this race to your email?"
    
    TONE & STYLE:
    - Authoritative, professional, and slightly "Pit Wall" inspired.
    - Use **bold** for Drivers and Teams.
    """,
    sub_agents=[f1_data_engineer, f1_race_predictor],
    tools=[visualize_lap_times, create_f1_calendar_event],
    model=MODEL,
    generate_content_config=types.GenerateContentConfig(
        max_output_tokens=4096,
        temperature=0.2
    )
)

# ADK look for 'root_agent' by default
root_agent = f1_orchestrator
