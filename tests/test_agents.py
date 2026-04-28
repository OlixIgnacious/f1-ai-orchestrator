"""
Tests for agent structure, tool registration, and routing rules.
These are structural tests — they verify the agent definitions are correct
without making real LLM calls.
"""

import pytest
from unittest.mock import patch, MagicMock
import os

os.environ.setdefault("ALLOYDB_PASSWORD",     "test")
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "test-project")


# ── Import guard ──────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def agents():
    from f1_orchestrator import agent as a
    return a


# ── Agent structure ───────────────────────────────────────────────────────────

class TestAgentStructure:

    def test_root_agent_is_orchestrator(self, agents):
        assert agents.root_agent is agents.f1_orchestrator

    def test_orchestrator_name(self, agents):
        assert agents.f1_orchestrator.name == "race_strategist"

    def test_coordinator_is_only_orchestrator_sub_agent(self, agents):
        """ADK parent constraint — orchestrator must only reference coordinator."""
        sub_names = [a.name for a in agents.f1_orchestrator.sub_agents]
        assert sub_names == ["f1_coordinator"]

    def test_coordinator_has_all_four_specialists(self, agents):
        sub_names = {a.name for a in agents.f1_coordinator.sub_agents}
        assert "f1_intel_agent"     in sub_names
        assert "f1_analysis_agent"  in sub_names
        assert "f1_steward_agent"   in sub_names
        assert "f1_event_scheduler" in sub_names

    def test_coordinator_has_exactly_four_sub_agents(self, agents):
        assert len(agents.f1_coordinator.sub_agents) == 4

    def test_all_agents_use_gemini_flash(self, agents):
        for agent in [agents.f1_orchestrator, agents.f1_coordinator,
                      agents.f1_intel_agent, agents.f1_analysis_agent,
                      agents.f1_steward_agent, agents.f1_event_scheduler]:
            assert "gemini" in agent.model.lower(), f"{agent.name} should use Gemini"

    def test_orchestrator_temperature_is_zero(self, agents):
        config = agents.f1_orchestrator.generate_content_config
        assert config.temperature == 0.0

    def test_coordinator_temperature_is_zero(self, agents):
        config = agents.f1_coordinator.generate_content_config
        assert config.temperature == 0.0

    def test_steward_temperature_is_zero(self, agents):
        config = agents.f1_steward_agent.generate_content_config
        assert config.temperature == 0.0

    def test_analysis_temperature_is_nonzero(self, agents):
        """Analysis agent needs some creativity for predictions."""
        config = agents.f1_analysis_agent.generate_content_config
        assert config.temperature > 0.0


# ── Intel agent tool registration ─────────────────────────────────────────────

class TestIntelAgentTools:

    def _tool_names(self, agents):
        return {t.__name__ for t in agents.f1_intel_agent.tools}

    def test_has_query_f1_db(self, agents):
        assert "query_f1_db" in self._tool_names(agents)

    def test_has_fetch_fastf1_live_data(self, agents):
        assert "fetch_fastf1_live_data" in self._tool_names(agents)

    def test_has_get_f1_schedule(self, agents):
        assert "get_f1_schedule" in self._tool_names(agents)

    def test_has_get_f1_standings(self, agents):
        assert "get_f1_standings" in self._tool_names(agents)

    def test_has_get_circuit_characteristics(self, agents):
        assert "get_circuit_characteristics" in self._tool_names(agents)

    def test_has_get_driver_head_to_head(self, agents):
        assert "get_driver_head_to_head" in self._tool_names(agents)

    def test_has_get_temporal_context(self, agents):
        assert "get_temporal_context" in self._tool_names(agents)

    def test_does_not_have_fetch_f1_telemetry(self, agents):
        """fetch_f1_telemetry must NOT be on intel — analysis owns it exclusively."""
        assert "fetch_f1_telemetry" not in self._tool_names(agents)

    def test_does_not_have_fetch_f1_pit_strategy(self, agents):
        """fetch_f1_pit_strategy must NOT be on intel — analysis owns it exclusively."""
        assert "fetch_f1_pit_strategy" not in self._tool_names(agents)

    def test_does_not_have_fetch_f1_technical_details(self, agents):
        """fetch_f1_technical_details must NOT be on intel — analysis owns it exclusively."""
        assert "fetch_f1_technical_details" not in self._tool_names(agents)


# ── Analysis agent tool registration ─────────────────────────────────────────

class TestAnalysisAgentTools:

    def _tool_names(self, agents):
        return {t.__name__ for t in agents.f1_analysis_agent.tools}

    def test_has_fetch_f1_telemetry(self, agents):
        assert "fetch_f1_telemetry" in self._tool_names(agents)

    def test_has_fetch_f1_pit_strategy(self, agents):
        assert "fetch_f1_pit_strategy" in self._tool_names(agents)

    def test_has_fetch_f1_technical_details(self, agents):
        assert "fetch_f1_technical_details" in self._tool_names(agents)

    def test_has_fetch_fastf1_live_data(self, agents):
        assert "fetch_fastf1_live_data" in self._tool_names(agents)

    def test_has_query_f1_db(self, agents):
        assert "query_f1_db" in self._tool_names(agents)

    def test_has_get_temporal_context(self, agents):
        assert "get_temporal_context" in self._tool_names(agents)


# ── Steward agent tool registration ──────────────────────────────────────────

class TestStewardAgentTools:

    def _tool_names(self, agents):
        return {t.__name__ for t in agents.f1_steward_agent.tools}

    def test_has_query_f1_regulations(self, agents):
        assert "query_f1_regulations" in self._tool_names(agents)

    def test_has_query_steward_decisions(self, agents):
        assert "query_steward_decisions" in self._tool_names(agents)

    def test_has_fetch_race_control_messages(self, agents):
        assert "fetch_race_control_messages" in self._tool_names(agents)

    def test_has_query_f1_db(self, agents):
        assert "query_f1_db" in self._tool_names(agents)

    def test_does_not_have_send_calendar_invite(self, agents):
        assert "send_f1_calendar_invite" not in self._tool_names(agents)


# ── Scheduler agent tool registration ────────────────────────────────────────

class TestSchedulerAgentTools:

    def _tool_names(self, agents):
        return {t.__name__ for t in agents.f1_event_scheduler.tools}

    def test_has_get_f1_schedule(self, agents):
        assert "get_f1_schedule" in self._tool_names(agents)

    def test_has_get_session_times(self, agents):
        assert "get_session_times" in self._tool_names(agents)

    def test_has_send_f1_calendar_invite(self, agents):
        assert "send_f1_calendar_invite" in self._tool_names(agents)

    def test_does_not_have_telemetry_tools(self, agents):
        tool_names = self._tool_names(agents)
        assert "fetch_f1_telemetry"       not in tool_names
        assert "fetch_f1_pit_strategy"    not in tool_names
        assert "query_f1_regulations"     not in tool_names
        assert "query_steward_decisions"  not in tool_names


# ── Tool deduplication across agents ─────────────────────────────────────────

class TestNoToolDuplication:

    def test_telemetry_only_on_analysis(self, agents):
        intel_tools    = {t.__name__ for t in agents.f1_intel_agent.tools}
        analysis_tools = {t.__name__ for t in agents.f1_analysis_agent.tools}
        steward_tools  = {t.__name__ for t in agents.f1_steward_agent.tools}
        sched_tools    = {t.__name__ for t in agents.f1_event_scheduler.tools}

        duplicated = (intel_tools | steward_tools | sched_tools) & {
            "fetch_f1_telemetry", "fetch_f1_pit_strategy", "fetch_f1_technical_details"
        }
        assert not duplicated, f"Telemetry tools duplicated on: {duplicated}"

    def test_calendar_tool_only_on_scheduler(self, agents):
        for agent in [agents.f1_intel_agent, agents.f1_analysis_agent,
                      agents.f1_steward_agent]:
            tool_names = {t.__name__ for t in agent.tools}
            assert "send_f1_calendar_invite" not in tool_names, \
                f"send_f1_calendar_invite should not be on {agent.name}"

    def test_rag_tools_only_on_steward(self, agents):
        rag_tools = {"query_f1_regulations", "query_steward_decisions"}
        for agent in [agents.f1_intel_agent, agents.f1_analysis_agent,
                      agents.f1_event_scheduler]:
            tool_names = {t.__name__ for t in agent.tools}
            overlap = rag_tools & tool_names
            assert not overlap, f"RAG tools {overlap} should not be on {agent.name}"


# ── SQL injection protection ──────────────────────────────────────────────────

class TestSQLInjectionProtection:

    @pytest.mark.parametrize("malicious_sql", [
        "DROP TABLE f1_results",
        "DELETE FROM f1_drivers WHERE 1=1",
        "INSERT INTO f1_results VALUES ('hack')",
        "UPDATE f1_standings SET points=999",
        "ALTER TABLE f1_sessions ADD COLUMN hack TEXT",
        "TRUNCATE TABLE f1_results",
        "CREATE TABLE evil (id TEXT)",
        "GRANT ALL ON f1_results TO hacker",
        "SELECT * FROM f1_results; DROP TABLE f1_results",
        "select * from f1_results; delete from f1_drivers",
    ])
    def test_blocked_sql(self, malicious_sql):
        from f1_orchestrator.agent import query_f1_db
        result = query_f1_db(malicious_sql)
        assert "Only SELECT" in result, f"Should have blocked: {malicious_sql}"

    @pytest.mark.parametrize("safe_sql", [
        "SELECT * FROM f1_results LIMIT 10",
        "SELECT full_name FROM f1_drivers WHERE code = 'VER'",
        "SELECT COUNT(*) FROM f1_sessions WHERE season = 2024",
        "SELECT r.position, d.full_name FROM f1_results r JOIN f1_drivers d ON d.driver_id = r.driver_id",
    ])
    def test_safe_select_passes_validation(self, safe_sql):
        from f1_orchestrator.agent import _validate_sql
        assert _validate_sql(safe_sql), f"Should have allowed: {safe_sql}"


# ── Connection pool ───────────────────────────────────────────────────────────

class TestConnectionPool:

    def test_pool_is_reused_across_calls(self):
        from f1_orchestrator import agent
        # Reset pool to force re-initialisation
        agent._pool = None
        pool_mock = MagicMock()
        conn_mock = MagicMock()
        pool_mock.getconn.return_value = conn_mock

        with patch("f1_orchestrator.agent.pg_pool") as mock_pg:
            mock_pg.ThreadedConnectionPool.return_value = pool_mock
            p1 = agent._get_pool()
            p2 = agent._get_pool()

        assert p1 is p2, "Pool should be a singleton"
        assert mock_pg.ThreadedConnectionPool.call_count == 1

    def test_pool_uses_env_vars(self):
        from f1_orchestrator import agent
        agent._pool = None
        with patch("f1_orchestrator.agent.pg_pool") as mock_pg:
            mock_pg.ThreadedConnectionPool.return_value = MagicMock()
            agent._get_pool()

        call_kwargs = mock_pg.ThreadedConnectionPool.call_args[1]
        assert call_kwargs["host"]     == os.getenv("ALLOYDB_HOST")
        assert call_kwargs["database"] == os.getenv("ALLOYDB_DATABASE", "f1db")
        assert call_kwargs["user"]     == os.getenv("ALLOYDB_USER", "postgres")
