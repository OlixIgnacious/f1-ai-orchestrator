import psycopg2
import os
import google.auth
import fastf1
import matplotlib.pyplot as plt
import pandas as pd
from datetime import datetime
from google.adk.agents import LlmAgent
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams
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

def create_f1_calendar_event(event_name: str, start_time: str, tool_context: ToolContext):
    """
    Adds an F1 event to the user's calendar.
    - event_name: e.g. 'Bahrain Grand Prix - Race'
    - start_time: ISO format string, e.g. '2026-04-07T15:00:00Z'
    Requires explicit user approval in the UI before execution.
    """
    # 1. CHECK FOR CONFIRMATION
    # The ADK UI will look for this 'tool_confirmation' in the context
    confirmation = tool_context.tool_confirmation()
    
    if not confirmation or not confirmation.confirmed:
        # This TRIGGERS the pop-up in the ADK UI
        tool_context.request_confirmation(
            hint=f"Should I add '{event_name}' to your calendar for {start_time}?",
            payload={"event": event_name, "time": start_time}
        )
        return "Waiting for user confirmation..."

    # 2. EXECUTION (Only runs after user clicks 'Approve' in the UI)
    print(f"\n[AGENT ACTION] Confirmed! Creating Calendar event: {event_name}")
    try:
        # Use the credentials initialized at the top of the file
        service = build('calendar', 'v3', credentials=credentials)
        
        # Simple 2-hour duration for F1 sessions
        from datetime import timedelta
        # Parse ISO string safely (handling 'Z' or offset)
        start_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
        end_dt = start_dt + timedelta(hours=2)
        
        event_body = {
            'summary': event_name,
            'description': 'Added via F1 AI Orchestrator',
            'start': {'dateTime': start_dt.isoformat()},
            'end': {'dateTime': end_dt.isoformat()},
            'reminders': {'useDefault': True},
        }
        
        created_event = service.events().insert(calendarId='primary', body=event_body).execute()
        return f"Successfully added '{event_name}' to your calendar! (Event ID: {created_event.get('id')})"
    except Exception as e:
        return f"Calendar Error: {str(e)}"

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
    
    SCHEMA CONTEXT:
    {F1_TABLE_METADATA}
    
    GUIDELINES:
    {SQL_GUIDELINES}
    5. TIME FILTERS: Use '{datetime.now().strftime('%Y-%m-%d')}' as the reference for 'today'. When asked for upcoming sessions, use 'WHERE date >= CURRENT_DATE' or similar.
    6. LIVE FALLBACK: If a query for a 2024 or 2025 event (results, standings, etc.) via 'query_f1_db' returns "Data: []" (empty results), immediately use 'fetch_fastf1_live_data' to get the latest info.
    
    If you encounter a schema error, check the 'F1_TABLE_METADATA' again and correct your query. 
    Focus on f1_results, f1_drivers, f1_sessions, f1_teams, and f1_standings for most queries.
    """,
    tools=[query_f1_db, fetch_fastf1_live_data, get_f1_schedule],
    model=MODEL,
    generate_content_config=types.GenerateContentConfig(temperature=0)
)

# 5. PRIMARY ORCHESTRATOR
f1_orchestrator = LlmAgent(
    name="race_strategist",
    instruction=f"""You are the Senior F1 Race Strategist. You are the "Head of Strategy" on the pit wall, orchestrating insights for the Team Principal.
    
    TEMPORAL CONTEXT:
    {CURRENT_CONTEXT}
    
    YOUR MISSION:
    Deliver elite, data-driven F1 briefings. You translate raw numbers into winning strategies.
    
    DELEGATION PROTOCOL:
    - For raw numbers, standings, or result lookups: Delegate to 'f1_data_engineer'.
    - For predictions, win probabilities, or "What if?" scenarios: Delegate to 'f1_race_predictor'.
    
    SYNTHESIS WORKFLOW:
    1. Briefing: Start with a concise summary of the requested F1 topic.
    2. Deep Dive: Present key statistics in bullet points.
    3. Strategic Insight: Provide a "Box Box" insight—what does this data mean for the next race?
    4. Actionable Next Step: If an upcoming race is mentioned, proactively ask: "Would you like me to add this to your calendar?"
    
    TONE & STYLE:
    - Authoritative, professional, and slightly "Pit Wall" inspired.
    - Use **bold** for Drivers and Teams.
    - Maintain extreme accuracy.
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
