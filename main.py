import os
from dotenv import load_dotenv
from fastapi import FastAPI
from google.adk.cli.fast_api import get_fast_api_app

load_dotenv()

# 1. APP SETUP
# 'agents_dir="."' tells ADK to look for agent folders in the current directory.
# 'auto_create_session=True' tells ADK to automatically create a session if it doesn't exist.
app: FastAPI = get_fast_api_app(agents_dir=".", web=True, auto_create_session=True)

@app.get("/health")
def health():
    return {"status": "ready"}

if __name__ == "__main__":
    import uvicorn
    # Use the PORT environment variable provided by Cloud Run, defaulting to 8080.
    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)