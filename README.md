# 🏎️ F1 AI Orchestrator: Virtual Pit Wall

A high-performance, multi-agent AI system designed to provide professional-grade F1 race strategy, historical analysis, and live telemetry orchestration. Powered by **Google GenAI ADK**, **AlloyDB**, and **FastF1**.

---

## 🌟 Key Features

### 🎙️ Multi-Agent Swarm Intelligence
The system uses a specialized team of AI agents to handle every aspect of the race weekend:
*   **Senior Race Strategist**: The head of the pit wall. Orchestrates complex data lookups, handles calendar scheduling, and performs head-to-head driver comparisons.
*   **Data Engineering Lead**: The source of truth. Bridges the gap between your **2020–2026 AlloyDB** and live **FastF1** technical data.
*   **Predictive Analyst**: Performance modeling specialist. Factors in cornering telemetry, throttle usage, and weather impacts to predict podiums and strategy shifts.

### 📊 Deep Telemetry & Technical Insights
Beyond scores and standings, the Orchestrator provides:
*   **Telemetry Swarm**: Real-time speed, throttle, brake, and gear analysis.
*   **Pit Intelligence**: Lap-by-lap stint reviews and pit stop duration analysis.
*   **Live Context**: Track weather conditions and driver-to-engineer radio transcripts.

### 📅 Seamless Calendar Orchestration
*   **Direct-Write Invitations**: Automatically adds F1 sessions to your Google Calendar.
*   **One-Click Fallback**: Provides a universal Google Calendar template link if permissions are restricted.

---

## 🛠️ Tech Stack

*   **Framework**: [Google GenAI ADK](https://github.com/google/generative-ai-adk) + FastAPI.
*   **Database**: Google Cloud AlloyDB (PostgreSQL-compatible) for high-speed 2020–2026 historical data.
*   **F1 Data**: [FastF1](https://github.com/theOehrly/Fast-F1) for live telemetry and technical modeling.
*   **Compute**: Google Cloud Run (Containerized Deployment).

---

## 🚀 Getting Started

### 1. Environment Configuration
Ensure your `.env` file contains the following:
```bash
# GCP & AlloyDB
PROJECT_ID=your-project-id
REGION=us-central1
ALLOYDB_HOST=your-ip
ALLOYDB_PASSWORD=your-password

# User Context
USER_EMAIL=your-email@gmail.com
```

### 2. Local Setup
```bash
pip install -r requirements.txt
python main.py
```

### 3. Deploying to Cloud Run
```bash
gcloud run deploy f1-orchestrator --source . --region us-central1
```

---

## 🏎️ Usage Examples

*   **"Analyze the 2025 championships results for Red Bull."**
*   **"Compare the telemetry of Verstappen and Hamilton from the latest race."**
*   **"Summarize the pit strategy for the Japanese Grand Prix."**
*   **"Add the next 2026 Grand Prix to my calendar."**

---

## 📈 Data Dictionary
For technical details on the underlying AlloyDB schema, see the [Data Dictionary](./f1_orchestrator/data_dictionary.md).
