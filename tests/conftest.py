"""
Shared fixtures for all F1 Orchestrator tool tests.
"""

import os
import pytest
import pandas as pd
from unittest.mock import MagicMock, patch

# Set dummy env vars before any agent import
os.environ.setdefault("ALLOYDB_PASSWORD",     "test_password")
os.environ.setdefault("ALLOYDB_HOST",         "127.0.0.1")
os.environ.setdefault("ALLOYDB_PORT",         "5432")
os.environ.setdefault("ALLOYDB_DATABASE",     "f1db")
os.environ.setdefault("ALLOYDB_USER",         "postgres")
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "test-project")
os.environ.setdefault("VERTEX_AI_LOCATION",   "us-central1")
os.environ.setdefault("GCS_REGULATIONS_BUCKET", "test-bucket")
os.environ.setdefault("FASTF1_CACHE_DIR",     "/tmp/fastf1_test_cache")


# ── ToolContext mock ──────────────────────────────────────────────────────────

class MockToolContext:
    """Minimal ToolContext with a real dict for state."""
    def __init__(self, initial: dict = None):
        self.state = initial or {}


@pytest.fixture
def ctx():
    """Fresh ToolContext for each test."""
    return MockToolContext()


@pytest.fixture
def ctx_with_session():
    """ToolContext pre-populated with a cached session."""
    return MockToolContext({
        "session_2024_Bahrain_R": "Results for 2024 Bahrain R:\n[cached data]"
    })


# ── DB pool mock ──────────────────────────────────────────────────────────────

def make_mock_cursor(rows=None, colnames=None):
    """Creates a mock psycopg2 cursor that returns given rows."""
    cur = MagicMock()
    cur.description = [(c,) for c in (colnames or ["col1"])]
    cur.fetchall.return_value = rows or []
    cur.fetchone.return_value = rows[0] if rows else None
    return cur


def make_mock_pool(rows=None, colnames=None):
    """Creates a mock psycopg2 connection pool."""
    cur  = make_mock_cursor(rows, colnames)
    conn = MagicMock()
    conn.cursor.return_value = cur
    pool = MagicMock()
    pool.getconn.return_value = conn
    return pool, conn, cur


@pytest.fixture
def mock_pool():
    pool, conn, cur = make_mock_pool()
    with patch("f1_orchestrator.agent._get_pool", return_value=pool):
        yield pool, conn, cur


@pytest.fixture
def mock_pool_empty():
    pool, conn, cur = make_mock_pool(rows=[], colnames=["id"])
    with patch("f1_orchestrator.agent._get_pool", return_value=pool):
        yield pool, conn, cur


# ── FastF1 mock helpers ───────────────────────────────────────────────────────

def make_mock_session(drivers=None, has_laps=True, has_telemetry=True):
    """Builds a minimal mock FastF1 session object."""
    session = MagicMock()

    # Results DataFrame
    results_data = {
        "ClassifiedPosition": ["1", "2", "3"],
        "FullName":            ["Max Verstappen", "Lewis Hamilton", "Charles Leclerc"],
        "TeamName":            ["Red Bull Racing", "Mercedes", "Ferrari"],
        "Status":              ["Finished", "Finished", "Finished"],
        "Points":              [25.0, 18.0, 15.0],
        "GridPosition":        [1, 3, 2],
        "BestLapTime":         ["1:31.447", "1:31.902", "1:32.011"],
        "Abbreviation":        ["VER", "HAM", "LEC"],
    }
    session.results = pd.DataFrame(results_data)
    session.drivers = drivers or ["VER", "HAM", "LEC"]

    if has_laps:
        laps_data = {
            "Driver":    ["VER", "VER", "HAM", "HAM"],
            "Stint":     [1, 1, 1, 2],
            "Compound":  ["SOFT", "SOFT", "MEDIUM", "HARD"],
            "LapNumber": [1, 2, 1, 30],
            "LapTime":   [pd.Timedelta("0:01:31.447"), pd.Timedelta("0:01:31.502"),
                          pd.Timedelta("0:01:31.902"), pd.Timedelta("0:01:32.100")],
            "IsAccurate": [True, True, True, True],
            "PitInTime":  [None, None, None, None],
            "PitOutTime": [None, None, None, None],
            "TyreLife":   [1, 2, 1, 15],
        }
        session.laps = MagicMock()
        session.laps.empty = False

        mock_laps_df = pd.DataFrame(laps_data)
        session.laps.__getitem__ = mock_laps_df.__getitem__
        session.laps.drop_duplicates.return_value = mock_laps_df
        session.laps.iterrows.return_value = mock_laps_df.iterrows()

        import numpy as np

        # Telemetry DataFrame returned by fast_lap.get_telemetry()
        tel_df = pd.DataFrame({
            "Speed":    np.random.uniform(100, 310, 50),
            "Throttle": np.random.uniform(50, 100, 50),
            "Brake":    np.random.uniform(0, 50, 50),
            "nGear":    np.random.randint(1, 8, 50),
            "RPM":      np.random.uniform(8000, 12000, 50),
        })
        tel_df_sampled = tel_df.iloc[::10, :]

        fast_lap = MagicMock()
        fast_lap.get_telemetry.return_value = tel_df_sampled
        # 1-arg side_effect — MagicMock's __getitem__ receives only the key, not self
        fast_lap.__getitem__ = MagicMock(
            side_effect=lambda k: pd.Timedelta("0:01:31.447") if k == "LapTime" else None
        )

        mock_driver_laps = MagicMock()
        mock_driver_laps.empty = False
        mock_driver_laps.pick_fastest.return_value = fast_lap
        session.laps.pick_driver.return_value = mock_driver_laps

        # Allow session.laps[['Driver', 'Stint', 'Compound', 'LapNumber']] to work
        stints_subset = mock_laps_df[['Driver', 'Stint', 'Compound', 'LapNumber']]
        session.laps.__getitem__ = MagicMock(return_value=stints_subset)
    else:
        session.laps = MagicMock()
        session.laps.empty = True

    # Weather + messages
    session.weather_data = pd.DataFrame({
        "AirTemp": [28.0], "TrackTemp": [42.0], "Humidity": [55.0],
        "Pressure": [1013.0], "WindSpeed": [2.1], "Rainfall": [False]
    })
    session.messages = pd.DataFrame({
        "Time":    ["00:01:23"],
        "Driver":  ["VER"],
        "Message": ["Push, push!"]
    })

    rc_data = {
        "Time":    ["00:05:00", "00:30:00"],
        "Lap":     [5, 30],
        "Flag":    ["YELLOW", "GREEN"],
        "Scope":   ["Track", "Track"],
        "Sector":  [None, None],
        "Message": ["YELLOW FLAG", "CAR 1 (VER) FIVE SECOND TIME PENALTY - UNSAFE RELEASE"],
    }
    session.race_control_messages = pd.DataFrame(rc_data)

    return session


@pytest.fixture
def mock_fastf1_session():
    session = make_mock_session()
    with patch("fastf1.get_session", return_value=session):
        yield session


@pytest.fixture
def mock_fastf1_schedule():
    schedule = pd.DataFrame({
        "RoundNumber": [1, 2, 3],
        "EventName":   ["Bahrain Grand Prix", "Saudi Arabian Grand Prix", "Australian Grand Prix"],
        "Location":    ["Sakhir", "Jeddah", "Melbourne"],
        "EventDate":   ["2026-03-16", "2026-03-23", "2026-04-13"],
        "EventFormat": ["conventional", "conventional", "conventional"],
    })
    with patch("fastf1.get_event_schedule", return_value=schedule):
        yield schedule


# ── Embedding mock ────────────────────────────────────────────────────────────

@pytest.fixture
def mock_embedding():
    """Mock Vertex AI embedding — returns 768-dim zero vector."""
    embed_result = MagicMock()
    embed_result.values = [0.0] * 768
    model = MagicMock()
    model.get_embeddings.return_value = [embed_result]
    with patch("f1_orchestrator.agent._get_embedding", return_value=[0.0] * 768):
        yield model


# ── Calendar mock ─────────────────────────────────────────────────────────────

@pytest.fixture
def mock_calendar():
    """Mock Google Calendar API service."""
    creds = MagicMock()
    creds.valid   = True
    creds.expired = False

    event_result = {"id": "test_event_id_123"}
    service      = MagicMock()
    service.events.return_value.insert.return_value.execute.return_value = event_result

    with patch("google.auth.default", return_value=(creds, "test-project")), \
         patch("f1_orchestrator.agent.build", return_value=service):
        yield service, creds
