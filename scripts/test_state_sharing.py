"""
Smoke test: verifies that ToolContext.state written by one sub-agent is visible
to a subsequent sub-agent within the same ADK session.

Run this BEFORE building Phase 3+ on top of the state-caching architecture.
A 'Cache hit' log line confirms state sharing is working correctly.

Usage:
    cd /path/to/f1-ai-orchestrator
    python scripts/test_state_sharing.py
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv
load_dotenv()

from google.adk.runners import InMemoryRunner
from google.genai import types
from f1_orchestrator.agent import root_agent


async def main():
    runner  = InMemoryRunner(agent=root_agent, app_name="state_test")
    session = await runner.session_service.create_session(
        app_name="state_test", user_id="test_user"
    )
    print(f"Session created: {session.id}\n")

    # ── Turn 1: intel query — should write schedule/results to state ──────────
    print("=" * 60)
    print("TURN 1: Intel query — expect schedule written to state")
    print("=" * 60)
    result1 = []
    async for event in runner.run_async(
        user_id="test_user",
        session_id=session.id,
        new_message=types.Content(
            role="user",
            parts=[types.Part(text="Who won the 2024 Bahrain Grand Prix?")]
        )
    ):
        if hasattr(event, 'content') and event.content:
            for part in event.content.parts:
                if hasattr(part, 'text') and part.text:
                    result1.append(part.text)
    print("Response:", "".join(result1)[:300])

    # ── Turn 2: analysis query — should hit the FastF1 session cache ─────────
    print("\n" + "=" * 60)
    print("TURN 2: Analysis query — watch for '[Cache hit]' in logs")
    print("=" * 60)
    result2 = []
    async for event in runner.run_async(
        user_id="test_user",
        session_id=session.id,
        new_message=types.Content(
            role="user",
            parts=[types.Part(text="Analyse Verstappen's telemetry at the 2024 Bahrain GP")]
        )
    ):
        if hasattr(event, 'content') and event.content:
            for part in event.content.parts:
                if hasattr(part, 'text') and part.text:
                    result2.append(part.text)
    print("Response:", "".join(result2)[:300])

    # ── Inspect session state ─────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("SESSION STATE KEYS (after both turns):")
    print("=" * 60)
    try:
        final_session = await runner.session_service.get_session(
            app_name="state_test", user_id="test_user", session_id=session.id
        )
        for k in final_session.state:
            preview = str(final_session.state[k])[:80]
            print(f"  {k}: {preview}...")
    except Exception as e:
        print(f"  Could not inspect state: {e}")

    print("\n✅ PASS if '[TOOL: ...] Cache hit' appeared in logs above.")
    print("❌ FAIL if every tool call fetched fresh — state is not propagating.")
    print("   In that case, check ADK version and InvocationContext propagation.")


if __name__ == "__main__":
    asyncio.run(main())
