"""
Tests for FastF1-backed tools:
  get_f1_schedule, fetch_fastf1_live_data, fetch_f1_telemetry,
  fetch_f1_pit_strategy, fetch_f1_technical_details, get_session_times
"""

import pytest
import pandas as pd
from unittest.mock import patch, MagicMock, call
from tests.conftest import MockToolContext, make_mock_pool, make_mock_session


# ── get_f1_schedule ───────────────────────────────────────────────────────────

class TestGetF1Schedule:

    def test_returns_schedule_string(self, mock_fastf1_schedule):
        from f1_orchestrator.agent import get_f1_schedule
        result = get_f1_schedule(2026)

        assert "F1 Schedule for 2026" in result
        assert "Bahrain" in result

    def test_caches_result_in_state(self, mock_fastf1_schedule):
        ctx = MockToolContext()
        from f1_orchestrator.agent import get_f1_schedule
        get_f1_schedule(2026, tool_context=ctx)

        assert "schedule_2026" in ctx.state

    def test_cache_hit_skips_fastf1(self):
        ctx = MockToolContext({"schedule_2026": "CACHED SCHEDULE"})
        with patch("fastf1.get_event_schedule") as mock_ff1:
            from f1_orchestrator.agent import get_f1_schedule
            result = get_f1_schedule(2026, tool_context=ctx)

        assert result == "CACHED SCHEDULE"
        mock_ff1.assert_not_called()

    def test_fastf1_error_returns_message(self):
        with patch("fastf1.get_event_schedule", side_effect=Exception("network error")):
            from f1_orchestrator.agent import get_f1_schedule
            result = get_f1_schedule(2026)

        assert "Schedule Error" in result


# ── fetch_fastf1_live_data ────────────────────────────────────────────────────

class TestFetchFastF1LiveData:

    def test_returns_session_results(self, mock_fastf1_session):
        from f1_orchestrator.agent import fetch_fastf1_live_data
        result = fetch_fastf1_live_data(2024, "Bahrain", "R")

        assert "Results for 2024 Bahrain R" in result
        assert "Verstappen" in result

    def test_caches_result_in_state(self, mock_fastf1_session):
        ctx = MockToolContext()
        from f1_orchestrator.agent import fetch_fastf1_live_data
        fetch_fastf1_live_data(2024, "Bahrain", "R", tool_context=ctx)

        assert "session_2024_Bahrain_R" in ctx.state

    def test_cache_hit_skips_fastf1(self):
        ctx = MockToolContext({"session_2024_Bahrain_R": "CACHED SESSION"})
        with patch("fastf1.get_session") as mock_ff1:
            from f1_orchestrator.agent import fetch_fastf1_live_data
            result = fetch_fastf1_live_data(2024, "Bahrain", "R", tool_context=ctx)

        assert result == "CACHED SESSION"
        mock_ff1.assert_not_called()

    def test_fastf1_error_returns_message(self):
        with patch("fastf1.get_session", side_effect=Exception("API down")):
            from f1_orchestrator.agent import fetch_fastf1_live_data
            result = fetch_fastf1_live_data(2024, "Bahrain", "R")

        assert "FastF1 Error" in result

    def test_default_session_type_is_race(self, mock_fastf1_session):
        from f1_orchestrator.agent import fetch_fastf1_live_data
        result = fetch_fastf1_live_data(2024, "Bahrain")

        assert "R" in result  # default session_type = "R"


# ── fetch_f1_telemetry ────────────────────────────────────────────────────────

class TestFetchF1Telemetry:

    def test_alloydb_hit_returns_precomputed(self):
        """When AlloyDB has data, FastF1 should not be called."""
        alloydb_row = [(220.5, 310.0, 75.3, 12.1, 10500.0, 11800.0, "0:01:31.447")]
        db_result   = f"Columns: ['avg_speed','top_speed','avg_throttle','avg_brake','avg_rpm','peak_rpm','fastest_lap_time']\nData: {alloydb_row}"

        with patch("f1_orchestrator.agent._query_raw", return_value=db_result), \
             patch("fastf1.get_session") as mock_ff1:
            from f1_orchestrator.agent import fetch_f1_telemetry
            result = fetch_f1_telemetry(2024, "Bahrain", "R", "VER")

        assert "220.5" in result or "avg_speed" in result
        mock_ff1.assert_not_called()

    def test_fallback_to_fastf1_when_alloydb_empty(self, mock_fastf1_session):
        with patch("f1_orchestrator.agent._query_raw",
                   return_value="Columns: ['avg_speed']\nData: []"):
            from f1_orchestrator.agent import fetch_f1_telemetry
            result = fetch_f1_telemetry(2024, "Bahrain", "R", "VER")

        assert "Telemetry" in result
        assert "VER" in result

    def test_caches_result_in_state(self, mock_fastf1_session):
        ctx = MockToolContext()
        with patch("f1_orchestrator.agent._query_raw",
                   return_value="Columns: ['avg_speed']\nData: []"):
            from f1_orchestrator.agent import fetch_f1_telemetry
            fetch_f1_telemetry(2024, "Bahrain", "R", "VER", tool_context=ctx)

        assert "telemetry_VER_2024_Bahrain_R" in ctx.state

    def test_cache_hit_skips_all_fetches(self):
        ctx = MockToolContext({"telemetry_VER_2024_Bahrain_R": "CACHED TEL"})
        with patch("f1_orchestrator.agent._query_raw") as mock_db, \
             patch("fastf1.get_session") as mock_ff1:
            from f1_orchestrator.agent import fetch_f1_telemetry
            result = fetch_f1_telemetry(2024, "Bahrain", "R", "VER", tool_context=ctx)

        assert result == "CACHED TEL"
        mock_db.assert_not_called()
        mock_ff1.assert_not_called()

    def test_no_laps_returns_error_message(self):
        session = make_mock_session(has_laps=False)
        with patch("f1_orchestrator.agent._query_raw",
                   return_value="Columns: ['avg_speed']\nData: []"), \
             patch("fastf1.get_session", return_value=session):
            session.laps.pick_driver.return_value.empty = True
            from f1_orchestrator.agent import fetch_f1_telemetry
            result = fetch_f1_telemetry(2024, "Bahrain", "R", "VER")

        assert "Error" in result

    def test_writes_back_to_alloydb_after_fastf1_fetch(self, mock_fastf1_session):
        """After a FastF1 fallback, data should be written back to AlloyDB."""
        pool, conn, cur = make_mock_pool(rows=[("test_session_id",)], colnames=["id"])
        with patch("f1_orchestrator.agent._query_raw",
                   return_value="Columns: ['avg_speed']\nData: []"), \
             patch("f1_orchestrator.agent._get_session_id", return_value="test-uuid-123"), \
             patch("f1_orchestrator.agent._get_pool", return_value=pool):
            from f1_orchestrator.agent import fetch_f1_telemetry
            fetch_f1_telemetry(2024, "Bahrain", "R", "VER")

        # Should have attempted an INSERT
        executed_sql = [str(c) for c in cur.execute.call_args_list]
        assert any("INSERT" in sql or "insert" in sql.lower()
                   for sql in executed_sql), "Expected write-back INSERT"

    def test_full_name_resolves_to_abbreviation(self, mock_fastf1_session):
        with patch("f1_orchestrator.agent._query_raw",
                   return_value="Columns: ['avg_speed']\nData: []"):
            from f1_orchestrator.agent import fetch_f1_telemetry
            result = fetch_f1_telemetry(2024, "Bahrain", "R", "Max Verstappen")

        assert "Error" not in result or "VER" in result

    def test_fastf1_error_returns_message(self):
        with patch("f1_orchestrator.agent._query_raw",
                   return_value="Columns: ['avg_speed']\nData: []"), \
             patch("fastf1.get_session", side_effect=Exception("API error")):
            from f1_orchestrator.agent import fetch_f1_telemetry
            result = fetch_f1_telemetry(2024, "Bahrain", "R", "VER")

        assert "Telemetry Error" in result


# ── fetch_f1_pit_strategy ─────────────────────────────────────────────────────

class TestFetchF1PitStrategy:

    def test_alloydb_hit_skips_fastf1(self):
        db_result = ("Columns: ['driver','stint_number','compound','start_lap','end_lap','lap_count']\n"
                     "Data: [('VER',1,'SOFT',1,20,20),('VER',2,'MEDIUM',21,57,37)]")
        with patch("f1_orchestrator.agent._query_raw", return_value=db_result), \
             patch("fastf1.get_session") as mock_ff1:
            from f1_orchestrator.agent import fetch_f1_pit_strategy
            result = fetch_f1_pit_strategy(2024, "Bahrain")

        assert "AlloyDB" in result
        mock_ff1.assert_not_called()

    def test_fallback_to_fastf1_when_alloydb_empty(self, mock_fastf1_session):
        with patch("f1_orchestrator.agent._query_raw",
                   return_value="Columns: ['driver']\nData: []"):
            from f1_orchestrator.agent import fetch_f1_pit_strategy
            result = fetch_f1_pit_strategy(2024, "Bahrain")

        assert "Pit/Stint Strategy" in result

    def test_caches_result_in_state(self, mock_fastf1_session):
        ctx = MockToolContext()
        with patch("f1_orchestrator.agent._query_raw",
                   return_value="Columns: ['driver']\nData: []"):
            from f1_orchestrator.agent import fetch_f1_pit_strategy
            fetch_f1_pit_strategy(2024, "Bahrain", tool_context=ctx)

        assert "stints_2024_Bahrain" in ctx.state

    def test_cache_hit_skips_all(self):
        ctx = MockToolContext({"stints_2024_Bahrain": "CACHED STINTS"})
        with patch("f1_orchestrator.agent._query_raw") as mock_db, \
             patch("fastf1.get_session") as mock_ff1:
            from f1_orchestrator.agent import fetch_f1_pit_strategy
            result = fetch_f1_pit_strategy(2024, "Bahrain", tool_context=ctx)

        assert result == "CACHED STINTS"
        mock_db.assert_not_called()
        mock_ff1.assert_not_called()

    def test_writes_back_stints_after_fastf1_fetch(self, mock_fastf1_session):
        pool, conn, cur = make_mock_pool(rows=[("test-uuid",)], colnames=["id"])
        with patch("f1_orchestrator.agent._query_raw",
                   return_value="Columns: ['driver']\nData: []"), \
             patch("f1_orchestrator.agent._get_session_id", return_value="test-uuid"), \
             patch("f1_orchestrator.agent._get_pool", return_value=pool):
            from f1_orchestrator.agent import fetch_f1_pit_strategy
            fetch_f1_pit_strategy(2024, "Bahrain")

        executed = [str(c) for c in cur.execute.call_args_list]
        assert any("INSERT" in sql or "insert" in sql.lower() for sql in executed)

    def test_fastf1_error_returns_message(self):
        with patch("f1_orchestrator.agent._query_raw",
                   return_value="Columns: ['driver']\nData: []"), \
             patch("fastf1.get_session", side_effect=Exception("network")):
            from f1_orchestrator.agent import fetch_f1_pit_strategy
            result = fetch_f1_pit_strategy(2024, "Bahrain")

        assert "Pit Strategy Error" in result


# ── fetch_f1_technical_details ────────────────────────────────────────────────

class TestFetchF1TechnicalDetails:

    def test_returns_weather_and_radio(self, mock_fastf1_session):
        from f1_orchestrator.agent import fetch_f1_technical_details
        result = fetch_f1_technical_details(2024, "Bahrain", "R")

        assert "Track Weather" in result
        assert "Radio" in result

    def test_caches_result_in_state(self, mock_fastf1_session):
        ctx = MockToolContext()
        from f1_orchestrator.agent import fetch_f1_technical_details
        fetch_f1_technical_details(2024, "Bahrain", "R", tool_context=ctx)

        assert "technical_2024_Bahrain_R" in ctx.state

    def test_cache_hit_skips_fastf1(self):
        ctx = MockToolContext({"technical_2024_Bahrain_R": "CACHED TECH"})
        with patch("fastf1.get_session") as mock_ff1:
            from f1_orchestrator.agent import fetch_f1_technical_details
            result = fetch_f1_technical_details(2024, "Bahrain", "R", tool_context=ctx)

        assert result == "CACHED TECH"
        mock_ff1.assert_not_called()

    def test_fastf1_error_returns_message(self):
        with patch("fastf1.get_session", side_effect=Exception("timeout")):
            from f1_orchestrator.agent import fetch_f1_technical_details
            result = fetch_f1_technical_details(2024, "Bahrain", "R")

        assert "Technical Data Error" in result


# ── get_session_times ─────────────────────────────────────────────────────────

class TestGetSessionTimes:

    def _make_mock_event(self, include_sprint=False):
        event = MagicMock()
        sessions = {
            "Practice 1": MagicMock(date=MagicMock(isoformat=lambda: "2026-05-22T10:30:00")),
            "Practice 2": MagicMock(date=MagicMock(isoformat=lambda: "2026-05-22T14:00:00")),
            "Practice 3": MagicMock(date=MagicMock(isoformat=lambda: "2026-05-23T11:30:00")),
            "Qualifying":  MagicMock(date=MagicMock(isoformat=lambda: "2026-05-23T15:00:00")),
            "Race":        MagicMock(date=MagicMock(isoformat=lambda: "2026-05-24T15:00:00")),
        }
        if include_sprint:
            sessions["Sprint"] = MagicMock(date=MagicMock(isoformat=lambda: "2026-05-23T10:00:00"))

        def get_session(name):
            if name not in sessions:
                raise KeyError(name)
            return sessions[name]

        event.get_session.side_effect = get_session
        return event

    def test_returns_all_sessions_for_gp(self):
        event = self._make_mock_event()
        with patch("fastf1.get_event", return_value=event):
            from f1_orchestrator.agent import get_session_times
            result = get_session_times(2026, "Monaco")

        assert "Race" in result
        assert "Qualifying" in result
        assert "Practice 1" in result

    def test_includes_duration_guide(self):
        event = self._make_mock_event()
        with patch("fastf1.get_event", return_value=event):
            from f1_orchestrator.agent import get_session_times
            result = get_session_times(2026, "Monaco")

        assert "Duration guide" in result
        assert "Race=2h" in result

    def test_caches_result_in_state(self):
        event = self._make_mock_event()
        ctx   = MockToolContext()
        with patch("fastf1.get_event", return_value=event):
            from f1_orchestrator.agent import get_session_times
            get_session_times(2026, "Monaco", tool_context=ctx)

        assert "session_times_2026_Monaco" in ctx.state

    def test_cache_hit_skips_fastf1(self):
        ctx = MockToolContext({"session_times_2026_Monaco": "CACHED TIMES"})
        with patch("fastf1.get_event") as mock_event:
            from f1_orchestrator.agent import get_session_times
            result = get_session_times(2026, "Monaco", tool_context=ctx)

        assert result == "CACHED TIMES"
        mock_event.assert_not_called()

    def test_no_session_times_returns_fallback_message(self):
        event = MagicMock()
        event.get_session.side_effect = Exception("not available")
        with patch("fastf1.get_event", return_value=event):
            from f1_orchestrator.agent import get_session_times
            result = get_session_times(2026, "Monaco")

        assert "not yet available" in result or "get_f1_schedule" in result

    def test_fastf1_event_error_returns_message(self):
        with patch("fastf1.get_event", side_effect=Exception("unknown GP")):
            from f1_orchestrator.agent import get_session_times
            result = get_session_times(2026, "NonExistentGP")

        assert "Session Times Error" in result
