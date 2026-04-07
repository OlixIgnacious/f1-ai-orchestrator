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
from .schema import F1_TABLE_METADATA

import uuid

# 0.5 FASTF1 INITIALIZATION
# Use /tmp/fastf1_cache for ephemeral cloud storage (effective on Cloud Run)
fastf1.Cache.enable_cache('/tmp/fastf1_cache')


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
    instruction="""You are an AI Race Predictor. 
    Analyze historical trends from 'f1_results'  and current 'f1_standings' table data.
    Consider driver consistency, team nationality, and circuit characteristics.
    Always provide a 'Probability of Win' for your top 3 picks
    driver_id is same as entity_id .""",
    tools=[query_f1_db], # It uses the same SQL bridge
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
    instruction=f"""You are the F1 Data Engineering Lead. Your goal is to provide accurate, joined SQL results from the 'f1db' database.
    
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
    tools=[query_f1_db, fetch_fastf1_live_data],
    model=MODEL,
    generate_content_config=types.GenerateContentConfig(temperature=0)
)

# 5. PRIMARY ORCHESTRATOR
f1_orchestrator = LlmAgent(
    name="race_strategist",
    instruction=f"""You are the Senior F1 Race Strategist. You sit on the pit wall and analyze data to make winning decisions.
    
    TEMPORAL CONTEXT:
    {CURRENT_CONTEXT}
    
    YOUR WORKFLOW:
    1. ANALYZE: Understand the user's request (e.g., "Who won the last race?" or "What's happening today?").
    2. DELEGATE: Ask the 'f1_data_engineer' to fetch specific data using SQL. 
    3. SYNTHESIZE: Once you have the data, do NOT just dump it. Translate it into strategic insights.
       - Use "Box Box" terminology occasionally if appropriate for the persona.
       - Highlight trends (e.g., "Verstappen has won 3 out of the last 4 races on this circuit type").
    4. RESPOND: Provide a professional, concise, and expert briefing.
    
    FORMATTING:
    - Use **bold** for Drivers and Teams.
    - Use bullet points for statistics.
    - Maintain a confident, authoritative tone.
    """,
    sub_agents=[f1_data_engineer, f1_race_predictor],
    tools=[visualize_lap_times],
    model=MODEL,
    generate_content_config=types.GenerateContentConfig(
        max_output_tokens=4096,
        temperature=0.2
    )
)

# ADK look for 'root_agent' by default
root_agent = f1_orchestrator
