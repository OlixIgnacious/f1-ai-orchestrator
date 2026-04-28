"""
Tests for calendar and temporal tools:
  get_temporal_context, send_f1_calendar_invite
"""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock
from tests.conftest import MockToolContext


# ── get_temporal_context ──────────────────────────────────────────────────────

class TestGetTemporalContext:

    def test_writes_current_date_to_state(self):
        ctx = MockToolContext()
        from f1_orchestrator.agent import get_temporal_context
        get_temporal_context(ctx)

        assert "current_date" in ctx.state
        # Should be a valid date string YYYY-MM-DD
        date_str = ctx.state["current_date"]
        datetime.strptime(date_str, "%Y-%m-%d")  # raises if format wrong

    def test_returns_date_in_response(self):
        ctx = MockToolContext()
        from f1_orchestrator.agent import get_temporal_context
        result = get_temporal_context(ctx)

        assert "Today is" in result

    def test_returns_current_season(self):
        ctx = MockToolContext()
        from f1_orchestrator.agent import get_temporal_context
        result = get_temporal_context(ctx)

        current_year = str(datetime.now().year)
        assert current_year in result

    def test_includes_temporal_rules(self):
        ctx = MockToolContext()
        from f1_orchestrator.agent import get_temporal_context
        result = get_temporal_context(ctx)

        assert "occurred" in result.lower() or "upcoming" in result.lower()

    def test_overwrites_stale_date_in_state(self):
        ctx = MockToolContext({"current_date": "2020-01-01"})
        from f1_orchestrator.agent import get_temporal_context
        get_temporal_context(ctx)

        assert ctx.state["current_date"] != "2020-01-01"


# ── send_f1_calendar_invite ───────────────────────────────────────────────────

class TestSendF1CalendarInvite:

    def _make_creds(self, valid=True, expired=False):
        creds = MagicMock()
        creds.valid   = valid
        creds.expired = expired
        return creds

    def _make_service(self, event_id="abc123"):
        service = MagicMock()
        service.events.return_value.insert.return_value.execute.return_value = {"id": event_id}
        return service

    def test_successful_invite_returns_confirmation(self):
        creds   = self._make_creds()
        service = self._make_service("event_xyz")
        with patch("google.auth.default", return_value=(creds, "project")), \
             patch("f1_orchestrator.agent.build", return_value=service):
            from f1_orchestrator.agent import send_f1_calendar_invite
            result = send_f1_calendar_invite(
                event_name="Monaco Grand Prix — Race",
                start_time="2026-05-24T15:00:00Z",
                location="Circuit de Monaco",
                recipient_email="test@example.com"
            )

        assert "event_xyz" in result or "Added" in result or "✅" in result

    def test_refreshes_expired_credentials(self):
        creds = self._make_creds(valid=False, expired=True)
        with patch("google.auth.default", return_value=(creds, "project")), \
             patch("google.auth.transport.requests.Request"), \
             patch("f1_orchestrator.agent.build", return_value=self._make_service()):
            from f1_orchestrator.agent import send_f1_calendar_invite
            send_f1_calendar_invite(
                "Monaco GP", "2026-05-24T15:00:00Z",
                "Monaco", "test@example.com"
            )

        creds.refresh.assert_called_once()

    def test_valid_credentials_not_refreshed(self):
        creds = self._make_creds(valid=True, expired=False)
        with patch("google.auth.default", return_value=(creds, "project")), \
             patch("f1_orchestrator.agent.build", return_value=self._make_service()):
            from f1_orchestrator.agent import send_f1_calendar_invite
            send_f1_calendar_invite(
                "Monaco GP", "2026-05-24T15:00:00Z",
                "Monaco", "test@example.com"
            )

        creds.refresh.assert_not_called()

    def test_calendar_write_failure_returns_fallback_link(self):
        creds = self._make_creds()
        with patch("google.auth.default", return_value=(creds, "project")), \
             patch("f1_orchestrator.agent.build", side_effect=Exception("permission denied")):
            from f1_orchestrator.agent import send_f1_calendar_invite
            result = send_f1_calendar_invite(
                "Monaco Grand Prix — Race",
                "2026-05-24T15:00:00Z",
                "Circuit de Monaco",
                "test@example.com"
            )

        assert "calendar.google.com" in result or "calendar/render" in result or "One-Click" in result

    def test_fallback_link_contains_event_details(self):
        creds = self._make_creds()
        with patch("google.auth.default", return_value=(creds, "project")), \
             patch("f1_orchestrator.agent.build", side_effect=Exception("403")):
            from f1_orchestrator.agent import send_f1_calendar_invite
            result = send_f1_calendar_invite(
                "Monaco Grand Prix — Race",
                "2026-05-24T15:00:00Z",
                "Circuit de Monaco",
                "test@example.com"
            )

        assert "Monaco" in result

    def test_default_duration_is_2_hours(self):
        creds   = self._make_creds()
        service = self._make_service()
        with patch("google.auth.default", return_value=(creds, "project")), \
             patch("f1_orchestrator.agent.build", return_value=service):
            from f1_orchestrator.agent import send_f1_calendar_invite
            send_f1_calendar_invite(
                "Race", "2026-05-24T15:00:00Z", "Monaco", "test@example.com"
            )

        call_kwargs = service.events.return_value.insert.call_args
        event_body  = call_kwargs[1].get("body") or call_kwargs[0][1]
        start = event_body["start"]["dateTime"]
        end   = event_body["end"]["dateTime"]

        start_dt = datetime.fromisoformat(start)
        end_dt   = datetime.fromisoformat(end)
        assert (end_dt - start_dt) == timedelta(hours=2)

    def test_custom_duration_is_applied(self):
        creds   = self._make_creds()
        service = self._make_service()
        with patch("google.auth.default", return_value=(creds, "project")), \
             patch("f1_orchestrator.agent.build", return_value=service):
            from f1_orchestrator.agent import send_f1_calendar_invite
            send_f1_calendar_invite(
                "Qualifying", "2026-05-23T15:00:00Z",
                "Monaco", "test@example.com", duration_hours=1
            )

        call_kwargs = service.events.return_value.insert.call_args
        event_body  = call_kwargs[1].get("body") or call_kwargs[0][1]
        start_dt = datetime.fromisoformat(event_body["start"]["dateTime"])
        end_dt   = datetime.fromisoformat(event_body["end"]["dateTime"])
        assert (end_dt - start_dt) == timedelta(hours=1)

    def test_reminders_set_to_1h_and_1day(self):
        creds   = self._make_creds()
        service = self._make_service()
        with patch("google.auth.default", return_value=(creds, "project")), \
             patch("f1_orchestrator.agent.build", return_value=service):
            from f1_orchestrator.agent import send_f1_calendar_invite
            send_f1_calendar_invite(
                "Race", "2026-05-24T15:00:00Z", "Monaco", "test@example.com"
            )

        call_kwargs = service.events.return_value.insert.call_args
        event_body  = call_kwargs[1].get("body") or call_kwargs[0][1]
        reminders   = event_body.get("reminders", {})
        overrides   = reminders.get("overrides", [])
        minutes     = {r["minutes"] for r in overrides}

        assert 60   in minutes, "Expected 1-hour reminder (60 min)"
        assert 1440 in minutes, "Expected 1-day reminder (1440 min)"

    def test_uses_recipient_email_as_calendar_id(self):
        creds   = self._make_creds()
        service = self._make_service()
        with patch("google.auth.default", return_value=(creds, "project")), \
             patch("f1_orchestrator.agent.build", return_value=service):
            from f1_orchestrator.agent import send_f1_calendar_invite
            send_f1_calendar_invite(
                "Race", "2026-05-24T15:00:00Z",
                "Monaco", "fan@formula1.com"
            )

        insert_call = service.events.return_value.insert.call_args
        calendar_id = insert_call[1].get("calendarId") or insert_call[0][0]
        assert calendar_id == "fan@formula1.com"

    def test_credentials_built_fresh_per_call(self):
        """Ensures google.auth.default() is called each invocation — no shared state."""
        creds = self._make_creds()
        with patch("google.auth.default", return_value=(creds, "project")) as mock_auth, \
             patch("f1_orchestrator.agent.build", return_value=self._make_service()):
            from f1_orchestrator.agent import send_f1_calendar_invite
            send_f1_calendar_invite("Race", "2026-05-24T15:00:00Z", "Monaco", "a@b.com")
            send_f1_calendar_invite("Race", "2026-05-24T15:00:00Z", "Monaco", "a@b.com")

        assert mock_auth.call_count == 2, "Credentials should be fetched fresh on each call"
