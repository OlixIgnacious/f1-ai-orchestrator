"""
Tests for AlloyDB-backed tools:
  query_f1_db, get_f1_standings, get_circuit_characteristics, get_driver_head_to_head
"""

import pytest
from unittest.mock import patch, MagicMock
from tests.conftest import make_mock_pool, MockToolContext


# ── query_f1_db ───────────────────────────────────────────────────────────────

class TestQueryF1Db:

    def test_select_query_succeeds(self):
        pool, conn, cur = make_mock_pool(
            rows=[("Max Verstappen", 25)],
            colnames=["full_name", "points"]
        )
        with patch("f1_orchestrator.agent._get_pool", return_value=pool):
            from f1_orchestrator.agent import query_f1_db
            result = query_f1_db("SELECT full_name, points FROM f1_drivers LIMIT 1")

        assert "Max Verstappen" in result
        assert "Columns:" in result
        assert "Data:" in result

    def test_drop_table_is_blocked(self):
        from f1_orchestrator.agent import query_f1_db
        result = query_f1_db("DROP TABLE f1_results")
        assert "Only SELECT" in result

    def test_delete_is_blocked(self):
        from f1_orchestrator.agent import query_f1_db
        result = query_f1_db("DELETE FROM f1_results WHERE 1=1")
        assert "Only SELECT" in result

    def test_insert_is_blocked(self):
        from f1_orchestrator.agent import query_f1_db
        result = query_f1_db("INSERT INTO f1_drivers VALUES ('test')")
        assert "Only SELECT" in result

    def test_update_is_blocked(self):
        from f1_orchestrator.agent import query_f1_db
        result = query_f1_db("UPDATE f1_results SET points = 0")
        assert "Only SELECT" in result

    def test_mixed_case_blocked(self):
        from f1_orchestrator.agent import query_f1_db
        result = query_f1_db("SeLeCt * FROM f1_results; DrOp TaBlE f1_results")
        assert "Only SELECT" in result

    def test_db_error_returns_message(self):
        pool = MagicMock()
        conn = MagicMock()
        conn.cursor.return_value.execute.side_effect = Exception("connection refused")
        pool.getconn.return_value = conn
        with patch("f1_orchestrator.agent._get_pool", return_value=pool):
            from f1_orchestrator.agent import query_f1_db
            result = query_f1_db("SELECT 1")
        assert "Database Error" in result

    def test_pool_connection_returned_after_query(self):
        pool, conn, cur = make_mock_pool(rows=[(1,)], colnames=["id"])
        with patch("f1_orchestrator.agent._get_pool", return_value=pool):
            from f1_orchestrator.agent import query_f1_db
            query_f1_db("SELECT 1")
        pool.putconn.assert_called_once_with(conn)

    def test_pool_connection_returned_on_error(self):
        pool, conn, cur = make_mock_pool()
        conn.cursor.return_value.execute.side_effect = Exception("timeout")
        with patch("f1_orchestrator.agent._get_pool", return_value=pool):
            from f1_orchestrator.agent import query_f1_db
            query_f1_db("SELECT 1")
        pool.putconn.assert_called_once_with(conn)


# ── get_f1_standings ──────────────────────────────────────────────────────────

class TestGetF1Standings:

    def test_returns_driver_and_constructor_tables(self):
        pool, conn, cur = make_mock_pool(
            rows=[(1, "Max Verstappen", 100.0, 4)],
            colnames=["pos", "driver", "pts", "wins"]
        )
        with patch("f1_orchestrator.agent._get_pool", return_value=pool), \
             patch("f1_orchestrator.agent._query_raw",
                   return_value="Columns: ['pos','driver','pts','wins']\nData: [(1,'VER',100,4)]"):
            from f1_orchestrator.agent import get_f1_standings
            result = get_f1_standings(2024)

        assert "DRIVER STANDINGS" in result
        assert "CONSTRUCTOR STANDINGS" in result
        assert "2024" in result

    def test_caches_result_in_state(self):
        ctx = MockToolContext()
        with patch("f1_orchestrator.agent._query_raw",
                   return_value="Columns: ['pos']\nData: [(1,)]"):
            from f1_orchestrator.agent import get_f1_standings
            get_f1_standings(2024, tool_context=ctx)

        assert "standings_2024" in ctx.state

    def test_returns_cached_result(self):
        ctx = MockToolContext({"standings_2024": "CACHED STANDINGS"})
        with patch("f1_orchestrator.agent._query_raw") as mock_raw:
            from f1_orchestrator.agent import get_f1_standings
            result = get_f1_standings(2024, tool_context=ctx)

        assert result == "CACHED STANDINGS"
        mock_raw.assert_not_called()

    def test_empty_db_returns_message(self):
        with patch("f1_orchestrator.agent._query_raw",
                   return_value="Columns: ['pos']\nData: []"):
            from f1_orchestrator.agent import get_f1_standings
            result = get_f1_standings(2099)

        assert "No standings data found" in result

    def test_db_error_returns_error_message(self):
        with patch("f1_orchestrator.agent._query_raw",
                   return_value="Database Error: connection refused"):
            from f1_orchestrator.agent import get_f1_standings
            result = get_f1_standings(2024)

        assert "Error" in result

    def test_uses_parameterised_sql_not_fstring(self):
        """Ensures no f-string year interpolation (SQL injection risk)."""
        calls = []
        def capture_raw(sql, params=None):
            calls.append((sql, params))
            return "Columns: ['pos']\nData: []"

        with patch("f1_orchestrator.agent._query_raw", side_effect=capture_raw):
            from f1_orchestrator.agent import get_f1_standings
            get_f1_standings(2024)

        for sql, params in calls:
            assert "2024" not in sql, "year should be in params, not baked into SQL"
            assert params is not None
            assert 2024 in params


# ── get_circuit_characteristics ───────────────────────────────────────────────

class TestGetCircuitCharacteristics:

    def test_returns_circuit_data(self):
        with patch("f1_orchestrator.agent._query_raw",
                   return_value="Columns: ['circuit_id','name','locality','country']\n"
                                "Data: [('monaco','Circuit de Monaco','Monte Carlo','MCO')]"):
            from f1_orchestrator.agent import get_circuit_characteristics
            result = get_circuit_characteristics("monaco")

        assert "Data:" in result
        assert "monaco" in result.lower()

    def test_not_found_returns_helpful_message(self):
        with patch("f1_orchestrator.agent._query_raw",
                   return_value="Columns: ['circuit_id']\nData: []"):
            from f1_orchestrator.agent import get_circuit_characteristics
            result = get_circuit_characteristics("nonexistent_circuit_xyz")

        assert "No circuit found" in result

    def test_caches_result_in_state(self):
        ctx = MockToolContext()
        with patch("f1_orchestrator.agent._query_raw",
                   return_value="Columns: ['circuit_id']\nData: [('spa',)]"):
            from f1_orchestrator.agent import get_circuit_characteristics
            get_circuit_characteristics("spa", tool_context=ctx)

        assert "circuit_spa" in ctx.state

    def test_cache_hit_skips_db(self):
        ctx = MockToolContext({"circuit_monaco": "CACHED CIRCUIT"})
        with patch("f1_orchestrator.agent._query_raw") as mock_raw:
            from f1_orchestrator.agent import get_circuit_characteristics
            result = get_circuit_characteristics("monaco", tool_context=ctx)

        assert result == "CACHED CIRCUIT"
        mock_raw.assert_not_called()

    def test_case_insensitive_lookup(self):
        calls = []
        def capture(sql, params=None):
            calls.append(params)
            return "Columns: ['circuit_id']\nData: [('monaco',)]"

        with patch("f1_orchestrator.agent._query_raw", side_effect=capture):
            from f1_orchestrator.agent import get_circuit_characteristics
            get_circuit_characteristics("Monaco")

        flat_params = [p for params in calls if params for p in params]
        assert any("Monaco" in str(p) or "monaco" in str(p).lower() for p in flat_params)


# ── get_driver_head_to_head ───────────────────────────────────────────────────

class TestGetDriverHeadToHead:

    def _make_h2h_pool(self, rows=None):
        pool, conn, cur = make_mock_pool(
            rows=rows or [
                ("VER", "Max Verstappen", 5, 8, 3, 125.0, 10, 12),
                ("HAM", "Lewis Hamilton", 3, 7, 2, 100.0, 9, 12),
            ],
            colnames=["code", "full_name", "wins", "podiums",
                      "poles", "total_points", "finishes", "starts"]
        )
        return pool, conn, cur

    def test_career_comparison(self):
        pool, conn, cur = self._make_h2h_pool()
        with patch("f1_orchestrator.agent._get_pool", return_value=pool):
            from f1_orchestrator.agent import get_driver_head_to_head
            result = get_driver_head_to_head("VER", "HAM")

        assert "VER" in result
        assert "HAM" in result
        assert "career" in result.lower()

    def test_season_scoped_comparison(self):
        pool, conn, cur = self._make_h2h_pool()
        with patch("f1_orchestrator.agent._get_pool", return_value=pool):
            from f1_orchestrator.agent import get_driver_head_to_head
            result = get_driver_head_to_head("VER", "HAM", season=2024)

        assert "2024" in result

    def test_caches_career_result(self):
        ctx = MockToolContext()
        pool, conn, cur = self._make_h2h_pool()
        with patch("f1_orchestrator.agent._get_pool", return_value=pool):
            from f1_orchestrator.agent import get_driver_head_to_head
            get_driver_head_to_head("VER", "HAM", tool_context=ctx)

        assert "head_to_head_VER_HAM_career" in ctx.state

    def test_caches_season_result(self):
        ctx = MockToolContext()
        pool, conn, cur = self._make_h2h_pool()
        with patch("f1_orchestrator.agent._get_pool", return_value=pool):
            from f1_orchestrator.agent import get_driver_head_to_head
            get_driver_head_to_head("VER", "HAM", season=2024, tool_context=ctx)

        assert "head_to_head_VER_HAM_2024" in ctx.state

    def test_cache_hit_skips_db(self):
        ctx = MockToolContext({"head_to_head_VER_HAM_career": "CACHED H2H"})
        pool, conn, cur = self._make_h2h_pool()
        with patch("f1_orchestrator.agent._get_pool", return_value=pool):
            from f1_orchestrator.agent import get_driver_head_to_head
            result = get_driver_head_to_head("VER", "HAM", tool_context=ctx)

        assert result == "CACHED H2H"
        conn.cursor.assert_not_called()

    def test_db_error_returns_message(self):
        pool = MagicMock()
        conn = MagicMock()
        conn.cursor.return_value.execute.side_effect = Exception("timeout")
        pool.getconn.return_value = conn
        with patch("f1_orchestrator.agent._get_pool", return_value=pool):
            from f1_orchestrator.agent import get_driver_head_to_head
            result = get_driver_head_to_head("VER", "HAM")

        assert "Database Error" in result
