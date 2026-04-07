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

![alt text](image.png)

---

## ☁️ Google Cloud Setup

To host the Orchestrator on Google Cloud, follow these steps:

### 1. Project & APIs
1.  Create or select a project in the [GCP Console](https://console.cloud.google.com).
2.  Enable the following APIs:
    *   **Cloud Run** (for hosting the bot)
    *   **AlloyDB API** (for the database)
    *   **Vertex AI API** (for the Gemini model)
    *   **Google Calendar API** (for scheduling)

### 2. AlloyDB Configuration
1.  Create an **AlloyDB Cluster & Instance**.
2.  In the **Connection** settings, ensure your instance is accessible (either via Public IP or VPC peering).
3.  Create a database named `f1db` and a user with `ALLOYDB_PASSWORD`.

### 3. Service Account Permissions
The identity running your Cloud Run service (the "Service Account") needs the following IAM roles:
*   `AlloyDB Client` (to connect to the database)
*   `Vertex AI User` (to run the Gemini model)
*   `Logs Writer` (for diagnostic logging)

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

## 📅 Calendar Integration Setup

To enable "Direct-Write" capabilities where the bot adds events directly to your calendar:

1.  **Find the Bot's Identity**: Locate the Service Account email in your GCP Console (typically `****************@developer.gserviceaccount.com`).
2.  **Share your Calendar**:
    *   Open [Google Calendar](https://calendar.google.com).
    *   Go to **Settings and sharing** for your primary calendar.
    *   Add the Service Account email under **"Share with specific people."**
    *   Set permissions to **"Make changes to events."**
3.  **The Result**: The bot will now populate your calendar automatically! 

> [!TIP]
> **No Permission? No Problem!**
> If you don't share your calendar, the bot intelligently recognizes the permission failure and provides a **Universal One-Click Link** in the chat instead. Clicking this will open your browser with all the race details pre-filled.

---

## 📈 Data Dictionary
For technical details on the underlying AlloyDB schema, see the [Data Dictionary](./f1_orchestrator/data_dictionary.md).
