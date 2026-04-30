import os
import fastf1
from datetime import timedelta, timezone
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from google.adk.cli.fast_api import get_fast_api_app



load_dotenv()

# 1. APP SETUP
# Persistent Session Storage (AlloyDB)
user = os.getenv("ALLOYDB_USER", "postgres")
password = os.getenv("ALLOYDB_PASSWORD")
host = os.getenv("ALLOYDB_HOST", "127.0.0.1")
port = os.getenv("ALLOYDB_PORT", "5433")
db = os.getenv("ALLOYDB_DATABASE", "f1db")

# Construct async connection string for DatabaseSessionService
async_db_url = f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{db}"

app: FastAPI = get_fast_api_app(
    agents_dir=".",
    web=True,
    auto_create_session=True,
    session_service_uri=async_db_url,
    allow_origins=["regex:http://localhost:[0-9]+"],
)

# Serve the React UI at /ui/ — only mounted when the build exists (production)
_ui_dist = os.path.join(os.path.dirname(__file__), "f1-ui", "dist")
if os.path.exists(_ui_dist):
    app.mount("/ui", StaticFiles(directory=_ui_dist, html=True), name="ui")

@app.get("/health")
def health():
    return {"status": "ready"}


# 2. ICS CALENDAR ENDPOINT
# Returns a single .ics file containing all sessions for a GP weekend.
# Example: GET /ics/2026/Miami  →  Miami_Grand_Prix_2026.ics
@app.api_route("/ics/{year}/{gp_name}", methods=["GET", "HEAD"])
def get_ics(year: int, gp_name: str):

    SESSION_DURATIONS = {
        "Race": 2, "Qualifying": 1, "Sprint": 1,
        "Sprint Qualifying": 1, "Practice 1": 1, "Practice 2": 1, "Practice 3": 1,
    }

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//F1 AI Orchestrator//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]

    found = 0
    try:
        event = fastf1.get_event(year, gp_name)
        location = f"{event.get('Location', gp_name)}, {event.get('Country', '')}"

        for session_name in ["Practice 1", "Practice 2", "Practice 3",
                             "Sprint Qualifying", "Sprint", "Qualifying", "Race"]:
            try:
                s = event.get_session(session_name)
                if s.date is None:
                    continue
                start_dt = s.date
                if start_dt.tzinfo is None:
                    start_dt = start_dt.replace(tzinfo=timezone.utc)
                end_dt   = start_dt + timedelta(hours=SESSION_DURATIONS.get(session_name, 2))
                fmt      = "%Y%m%dT%H%M%SZ"

                lines += [
                    "BEGIN:VEVENT",
                    f"UID:{year}-{gp_name.replace(' ','-')}-{session_name.replace(' ','-')}@f1-orchestrator",
                    f"DTSTART:{start_dt.strftime(fmt)}",
                    f"DTEND:{end_dt.strftime(fmt)}",
                    f"SUMMARY:{event.get('EventName', gp_name)} — {session_name}",
                    f"LOCATION:{location}",
                    "DESCRIPTION:F1 session scheduled by your AI Race Strategist.",
                    "BEGIN:VALARM",
                    "TRIGGER:-PT60M",
                    "ACTION:DISPLAY",
                    "DESCRIPTION:Race starts in 1 hour",
                    "END:VALARM",
                    "BEGIN:VALARM",
                    "TRIGGER:-P1D",
                    "ACTION:DISPLAY",
                    "DESCRIPTION:Race tomorrow",
                    "END:VALARM",
                    "END:VEVENT",
                ]
                found += 1
            except Exception:
                pass
    except Exception as e:
        return Response(content=f"Error fetching {gp_name} {year}: {e}",
                        media_type="text/plain", status_code=404)

    lines.append("END:VCALENDAR")

    if found == 0:
        return Response(content=f"No sessions found for {gp_name} {year}",
                        media_type="text/plain", status_code=404)

    ics_content  = "\r\n".join(lines)
    filename     = f"{gp_name.replace(' ','_')}_{year}.ics"
    return Response(
        content=ics_content,
        media_type="text/calendar",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

if __name__ == "__main__":
    import uvicorn
    # Use the PORT environment variable provided by Cloud Run, defaulting to 8080.
    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)