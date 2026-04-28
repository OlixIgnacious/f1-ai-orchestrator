import os
import fastf1
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import Response
from google.adk.cli.fast_api import get_fast_api_app

load_dotenv()

# 1. APP SETUP
app: FastAPI = get_fast_api_app(agents_dir=".", web=True, auto_create_session=True)

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