// Empty string = same origin (production, served from Cloud Run)
// Set VITE_API_URL in .env.local to point at Cloud Run for local dev
const API_URL = import.meta.env.VITE_API_URL ?? '';
const USER_ID  = import.meta.env.VITE_USER_ID  || 'user';
const APP_NAME = import.meta.env.VITE_APP_NAME || 'f1_orchestrator';

const AGENT_LABELS = {
  race_strategist:   'Pit Wall Director',
  f1_coordinator:    'Race Engineer',
  f1_intel_agent:    'Intelligence Officer',
  f1_analysis_agent: 'Performance Analyst',
  f1_steward_agent:  'FIA Steward Panel',
  f1_event_scheduler:'Event Scheduler',
};

const TOOL_LABELS = {
  query_f1_db:               'Querying AlloyDB',
  fetch_fastf1_live_data:    'Fetching FastF1 data',
  get_f1_schedule:           'Loading F1 schedule',
  get_f1_standings:          'Fetching standings',
  fetch_f1_telemetry:        'Analysing telemetry',
  fetch_f1_pit_strategy:     'Loading pit strategy',
  fetch_f1_technical_details:'Loading weather & radio',
  query_f1_regulations:      'Searching FIA regulations',
  query_steward_decisions:   'Checking steward precedents',
  get_circuit_characteristics:'Loading circuit data',
  get_driver_head_to_head:   'Comparing drivers',
  get_temporal_context:      null, // silent — internal only
  get_full_ruling:           'Formatting full ruling',
  get_session_times:         'Fetching session times',
  send_f1_calendar_invite:   'Adding to calendar',
  get_calendar_options:      'Building calendar options',
  fetch_race_control_messages:'Fetching race control messages',
};

export async function createSession() {
  const res = await fetch(`${API_URL}/apps/${APP_NAME}/users/${USER_ID}/sessions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  });
  if (!res.ok) throw new Error(`Session creation failed: ${res.status}`);
  const data = await res.json();
  return data.id;
}

// Yields: { type: 'step', label } | { type: 'text', text, author }
export async function* streamMessage(sessionId, text, signal) {
  const res = await fetch(`${API_URL}/run_sse`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    signal,
    body: JSON.stringify({
      app_name: APP_NAME,
      user_id: USER_ID,
      session_id: sessionId,
      streaming: true,
      new_message: { role: 'user', parts: [{ text }] },
    }),
  });

  if (!res.ok) throw new Error(`Request failed: ${res.status}`);

  const reader  = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer    = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop();

    for (const line of lines) {
      if (!line.startsWith('data: ')) continue;
      const raw = line.slice(6).trim();
      if (!raw || raw === '[DONE]') continue;
      try {
        const event = JSON.parse(raw);
        if (event.partial === false) continue;

        // Agent transfer step
        if (event.actions?.transferToAgent) {
          const label = AGENT_LABELS[event.actions.transferToAgent];
          if (label) yield { type: 'step', label: `→ ${label}` };
        }

        const parts = event?.content?.parts ?? [];
        for (const part of parts) {
          // Tool call step
          if (part.functionCall && part.functionCall.name !== 'transfer_to_agent') {
            const label = TOOL_LABELS[part.functionCall.name];
            if (label) yield { type: 'step', label };
          }
          // Text chunk
          if (typeof part.text === 'string' && part.text) {
            yield { type: 'text', text: part.text, author: event.author };
          }
        }
      } catch {
        // malformed chunk — skip
      }
    }
  }
}
