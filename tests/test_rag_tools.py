"""
Tests for RAG and race control tools:
  query_f1_regulations, query_steward_decisions, fetch_race_control_messages
"""

import pytest
import pandas as pd
from unittest.mock import patch, MagicMock
from tests.conftest import MockToolContext, make_mock_pool


# ── query_f1_regulations ──────────────────────────────────────────────────────

class TestQueryF1Regulations:

    def test_stub_when_no_data_ingested(self):
        """Returns graceful stub message when f1_regulations table is empty."""
        with patch("f1_orchestrator.agent._rag_available", return_value=False):
            from f1_orchestrator.agent import query_f1_regulations
            result = query_f1_regulations("What is the penalty for unsafe release?")

        assert "not yet ingested" in result or "Phase 4" in result or "model" in result.lower()

    def test_live_search_when_data_available(self):
        """Uses pgvector search when table has data."""
        db_result = ("Columns: ['article_number','article_title','content','year','reg_type','distance']\n"
                     "Data: [('34.13','Unsafe Release','A car released in an unsafe manner...',2025,'Sporting',0.12)]")
        with patch("f1_orchestrator.agent._rag_available", return_value=True), \
             patch("f1_orchestrator.agent._get_embedding", return_value=[0.0] * 768), \
             patch("f1_orchestrator.agent._query_raw", return_value=db_result):
            from f1_orchestrator.agent import query_f1_regulations
            result = query_f1_regulations("What is the penalty for unsafe release?")

        assert "34.13" in result or "Unsafe Release" in result

    def test_caches_result_in_state(self):
        ctx = MockToolContext()
        db_result = "Columns: ['article_number']\nData: [('39',)]"
        with patch("f1_orchestrator.agent._rag_available", return_value=True), \
             patch("f1_orchestrator.agent._get_embedding", return_value=[0.0] * 768), \
             patch("f1_orchestrator.agent._query_raw", return_value=db_result):
            from f1_orchestrator.agent import query_f1_regulations
            query_f1_regulations("safety car rules", tool_context=ctx)

        assert any("reg_" in k for k in ctx.state)

    def test_cache_hit_skips_search(self):
        question = "safety car rules"
        import hashlib
        cache_key = f"reg_{hash(question)}_None_None"
        ctx = MockToolContext({cache_key: "CACHED REGS"})

        with patch("f1_orchestrator.agent._rag_available", return_value=True), \
             patch("f1_orchestrator.agent._get_embedding") as mock_embed, \
             patch("f1_orchestrator.agent._query_raw") as mock_db:
            from f1_orchestrator.agent import query_f1_regulations
            result = query_f1_regulations(question, tool_context=ctx)

        assert result == "CACHED REGS"
        mock_embed.assert_not_called()
        mock_db.assert_not_called()

    def test_year_filter_passed_to_query(self):
        calls = []
        def capture_raw(sql, params=None):
            calls.append(params)
            return "Columns: ['article_number']\nData: []"

        with patch("f1_orchestrator.agent._rag_available", return_value=True), \
             patch("f1_orchestrator.agent._get_embedding", return_value=[0.0] * 768), \
             patch("f1_orchestrator.agent._query_raw", side_effect=capture_raw):
            from f1_orchestrator.agent import query_f1_regulations
            query_f1_regulations("tyre rules", year_filter=2025)

        flat = [p for params in calls if params for p in params]
        assert 2025 in flat, "year_filter=2025 should be passed as a query parameter"

    def test_reg_type_filter_passed_to_query(self):
        calls = []
        def capture_raw(sql, params=None):
            calls.append(params)
            return "Columns: ['article_number']\nData: []"

        with patch("f1_orchestrator.agent._rag_available", return_value=True), \
             patch("f1_orchestrator.agent._get_embedding", return_value=[0.0] * 768), \
             patch("f1_orchestrator.agent._query_raw", side_effect=capture_raw):
            from f1_orchestrator.agent import query_f1_regulations
            query_f1_regulations("cost cap", reg_type="Financial")

        flat = [p for params in calls if params for p in params]
        assert "Financial" in flat, "reg_type should be passed as a query parameter"

    def test_embedding_error_returns_message(self):
        with patch("f1_orchestrator.agent._rag_available", return_value=True), \
             patch("f1_orchestrator.agent._get_embedding",
                   side_effect=Exception("Vertex AI unavailable")):
            from f1_orchestrator.agent import query_f1_regulations
            result = query_f1_regulations("DRS rules")

        assert "error" in result.lower() or "Error" in result


# ── query_steward_decisions ───────────────────────────────────────────────────

class TestQueryStewardDecisions:

    def test_stub_when_no_data_ingested(self):
        with patch("f1_orchestrator.agent._rag_available", return_value=False):
            from f1_orchestrator.agent import query_steward_decisions
            result = query_steward_decisions("unsafe release penalty precedent")

        assert "not yet ingested" in result or "Phase 4" in result or "model" in result.lower()

    def test_live_search_when_data_available(self):
        db_result = ("Columns: ['race','year','driver_id','incident','ruling','penalty','article_ref','distance']\n"
                     "Data: [('Bahrain Grand Prix',2024,'VER','UNSAFE RELEASE','Penalty imposed','5 second time penalty','B34.13',0.08)]")
        with patch("f1_orchestrator.agent._rag_available", return_value=True), \
             patch("f1_orchestrator.agent._get_embedding", return_value=[0.0] * 768), \
             patch("f1_orchestrator.agent._query_raw", return_value=db_result):
            from f1_orchestrator.agent import query_steward_decisions
            result = query_steward_decisions("unsafe release penalty")

        assert "Bahrain" in result or "VER" in result or "5 second" in result

    def test_caches_result_in_state(self):
        ctx = MockToolContext()
        db_result = "Columns: ['race']\nData: [('Monaco GP',)]"
        with patch("f1_orchestrator.agent._rag_available", return_value=True), \
             patch("f1_orchestrator.agent._get_embedding", return_value=[0.0] * 768), \
             patch("f1_orchestrator.agent._query_raw", return_value=db_result):
            from f1_orchestrator.agent import query_steward_decisions
            query_steward_decisions("causing a collision", tool_context=ctx)

        assert any("decision_" in k for k in ctx.state)

    def test_year_filter_applied(self):
        calls = []
        def capture(sql, params=None):
            calls.append(params)
            return "Columns: ['race']\nData: []"

        with patch("f1_orchestrator.agent._rag_available", return_value=True), \
             patch("f1_orchestrator.agent._get_embedding", return_value=[0.0] * 768), \
             patch("f1_orchestrator.agent._query_raw", side_effect=capture):
            from f1_orchestrator.agent import query_steward_decisions
            query_steward_decisions("weaving under braking", year_filter=2024)

        flat = [p for params in calls if params for p in params]
        assert 2024 in flat


# ── fetch_race_control_messages ───────────────────────────────────────────────

class TestFetchRaceControlMessages:

    def test_alloydb_hit_skips_fastf1(self):
        db_result = ("Columns: ['lap_number','flag_type','sector','message','driver_id']\n"
                     "Data: [(4,'YELLOW',1,'YELLOW FLAG',None),(30,'GREEN',None,'CLEAR',None)]")
        with patch("f1_orchestrator.agent._query_raw", return_value=db_result), \
             patch("fastf1.get_session") as mock_ff1:
            from f1_orchestrator.agent import fetch_race_control_messages
            result = fetch_race_control_messages(2024, "Bahrain")

        assert "YELLOW" in result
        mock_ff1.assert_not_called()

    def test_fallback_to_fastf1_when_alloydb_empty(self):
        session = MagicMock()
        session.race_control_messages = pd.DataFrame({
            "Time":    ["00:05:00"],
            "Lap":     [5],
            "Flag":    ["YELLOW"],
            "Scope":   ["Track"],
            "Sector":  [None],
            "Message": ["YELLOW FLAG SECTOR 1"],
        })
        with patch("f1_orchestrator.agent._query_raw",
                   return_value="Columns: ['lap_number']\nData: []"), \
             patch("fastf1.get_session", return_value=session):
            from f1_orchestrator.agent import fetch_race_control_messages
            result = fetch_race_control_messages(2024, "Bahrain")

        assert "Race Control Messages" in result or "YELLOW" in result

    def test_caches_result_in_state(self):
        ctx = MockToolContext()
        db_result = "Columns: ['message']\nData: [('YELLOW FLAG',)]"
        with patch("f1_orchestrator.agent._query_raw", return_value=db_result):
            from f1_orchestrator.agent import fetch_race_control_messages
            fetch_race_control_messages(2024, "Bahrain", tool_context=ctx)

        assert "race_control_2024_Bahrain" in ctx.state

    def test_cache_hit_skips_everything(self):
        ctx = MockToolContext({"race_control_2024_Bahrain": "CACHED RC"})
        with patch("f1_orchestrator.agent._query_raw") as mock_db, \
             patch("fastf1.get_session") as mock_ff1:
            from f1_orchestrator.agent import fetch_race_control_messages
            result = fetch_race_control_messages(2024, "Bahrain", tool_context=ctx)

        assert result == "CACHED RC"
        mock_db.assert_not_called()
        mock_ff1.assert_not_called()

    def test_empty_fastf1_returns_not_found_message(self):
        session = MagicMock()
        session.race_control_messages = pd.DataFrame()  # empty
        with patch("f1_orchestrator.agent._query_raw",
                   return_value="Columns: ['message']\nData: []"), \
             patch("fastf1.get_session", return_value=session):
            from f1_orchestrator.agent import fetch_race_control_messages
            result = fetch_race_control_messages(2024, "Bahrain")

        assert "not found" in result.lower() or "No race control" in result

    def test_fastf1_error_returns_error_message(self):
        with patch("f1_orchestrator.agent._query_raw",
                   return_value="Columns: ['message']\nData: []"), \
             patch("fastf1.get_session", side_effect=Exception("API down")):
            from f1_orchestrator.agent import fetch_race_control_messages
            result = fetch_race_control_messages(2024, "Bahrain")

        assert "Race Control Error" in result
