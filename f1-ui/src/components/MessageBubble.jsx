import { marked } from 'marked';
import { useMemo, useState } from 'react';

marked.setOptions({ breaks: true, gfm: true });

const AGENT_META = {
  f1_intel_agent:    { label: 'Intel',     color: '#3b82f6' },
  f1_analysis_agent: { label: 'Analysis',  color: '#f59e0b' },
  f1_steward_agent:  { label: 'Steward',   color: '#8b5cf6' },
  f1_event_scheduler:{ label: 'Scheduler', color: '#10b981' },
  f1_coordinator:    { label: 'Coordinator', color: '#6b7280' },
  race_strategist:   { label: 'Pit Wall',  color: '#E8002D' },
};

function getFollowups(agent, originalText) {
  const yearMatch = originalText?.match(/\b(20\d{2})\b/);
  const year = yearMatch ? yearMatch[1] : null;
  const currentYear = new Date().getFullYear();
  const isFuture = year ? parseInt(year) > currentYear : false;

  switch (agent) {
    case 'f1_intel_agent':
      return [
        'Compare telemetry' + (year && !isFuture ? ` for ${year}` : ''),
        year ? `${year} Driver Standings` : 'Current Standings',
        'Head-to-head comparison',
      ];
    case 'f1_analysis_agent':
      return [
        'Compare with another driver',
        'Optimal pit strategy?',
        year ? `${year} Driver Standings` : 'Current Standings',
      ];
    case 'f1_steward_agent':
      return ['Show full ruling', 'Related penalties', 'What does the regulation say?'];
    case 'f1_event_scheduler':
      return ['Add full weekend', 'Tell me about the circuit', 'Show standings'];
    default:
      return [];
  }
}

export default function MessageBubble({ message, onRetry, onSuggestion }) {
  const { role, text, streaming, thinking, steps, agent, originalText } = message;
  const isUser = role === 'user';
  const [copied, setCopied] = useState(false);
  const [hovered, setHovered] = useState(false);

  const html = useMemo(() => {
    if (isUser || !text) return null;
    // Gemini sometimes escapes newlines as literal \n — unescape before parsing
    const clean = text.replace(/\\n/g, '\n');
    return marked.parse(clean);
  }, [text, isUser]);

  function copy() {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }

  const isError = text?.startsWith('⚠️');
  const agentMeta = AGENT_META[agent];
  const followups = !streaming && !isUser && !isError && agent ? getFollowups(agent, originalText) : null;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: isUser ? 'flex-end' : 'flex-start', padding: '4px 0', animation: 'slideUp 0.2s ease' }}>
      <div style={{ display: 'flex', justifyContent: isUser ? 'flex-end' : 'flex-start', width: '100%' }}>
        {/* Avatar */}
        {!isUser && (
          <div style={{ width: '32px', height: '32px', borderRadius: '50%', background: '#E8002D', color: '#fff', fontWeight: '900', fontSize: '12px', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0, marginRight: '10px', alignSelf: 'flex-end', fontStyle: 'italic' }}>
            F1
          </div>
        )}

        <div
          style={{ maxWidth: 'min(85%, 720px)', position: 'relative' }}
          onMouseEnter={() => setHovered(true)}
          onMouseLeave={() => setHovered(false)}
        >
          {/* Agent badge */}
          {!isUser && agentMeta && !streaming && (
            <div style={{ marginBottom: '5px', display: 'flex', alignItems: 'center', gap: '6px' }}>
              <span style={{ fontSize: '11px', fontWeight: '600', color: agentMeta.color, background: `${agentMeta.color}20`, padding: '2px 8px', borderRadius: '10px', letterSpacing: '0.05em', textTransform: 'uppercase' }}>
                {agentMeta.label}
              </span>
            </div>
          )}

          {/* Bubble */}
          <div style={{ background: isUser ? '#1e1e1e' : '#140008', borderRadius: isUser ? '16px 16px 4px 16px' : '16px 16px 16px 4px', borderLeft: isUser ? 'none' : `3px solid ${isError ? '#ef4444' : '#E8002D'}`, padding: '12px 16px', position: 'relative' }}>

            {isUser ? (
              <p style={{ color: '#f0f0f0', margin: 0, wordBreak: 'break-word' }}>{text}</p>
            ) : thinking ? (
              <div>
                {/* Live steps */}
                {steps && steps.length > 0 && (
                  <div style={{ marginBottom: '10px', display: 'flex', flexDirection: 'column', gap: '4px' }}>
                    {steps.map((s, i) => (
                      <div key={i} style={{ fontSize: '12px', color: i === steps.length - 1 ? '#E8002D' : '#555', display: 'flex', alignItems: 'center', gap: '6px', transition: 'color 0.3s' }}>
                        {i === steps.length - 1 && <span style={{ width: '6px', height: '6px', borderRadius: '50%', background: '#E8002D', display: 'inline-block', animation: 'pulse 0.8s infinite', flexShrink: 0 }} />}
                        {i < steps.length - 1 && <span style={{ width: '6px', height: '6px', borderRadius: '50%', background: '#333', display: 'inline-block', flexShrink: 0 }} />}
                        {s}
                      </div>
                    ))}
                  </div>
                )}
                {/* Dots */}
                <div style={{ display: 'flex', alignItems: 'center', gap: '10px', color: '#666', fontSize: '13px' }}>
                  <div style={{ display: 'flex', gap: '4px' }}>
                    {[0, 150, 300].map(d => (
                      <span key={d} style={{ width: '7px', height: '7px', borderRadius: '50%', background: '#E8002D', display: 'inline-block', animation: `pulse 1s ease-in-out ${d}ms infinite` }} />
                    ))}
                  </div>
                  Pit wall is thinking…
                </div>
              </div>
            ) : (
              <div className="md-content" dangerouslySetInnerHTML={{ __html: html }} style={{ color: '#e0e0e0', wordBreak: 'break-word' }} />
            )}

            {/* Streaming cursor */}
            {streaming && !thinking && (
              <span style={{ display: 'inline-block', width: '7px', height: '7px', borderRadius: '50%', background: '#E8002D', marginLeft: '4px', verticalAlign: 'middle', animation: 'pulse 0.8s ease-in-out infinite' }} />
            )}
          </div>

          {/* Copy button — hover only, non-user, non-thinking */}
          {!isUser && !thinking && !streaming && text && hovered && (
            <button onClick={copy} style={{ position: 'absolute', top: agentMeta ? '26px' : '2px', right: '-36px', background: '#1e1e1e', border: '1px solid #2a2a2a', borderRadius: '6px', color: copied ? '#22c55e' : '#888', fontSize: '12px', padding: '4px 7px', cursor: 'pointer', whiteSpace: 'nowrap' }}>
              {copied ? '✓' : '⎘'}
            </button>
          )}

          {/* Retry button on errors */}
          {isError && onRetry && (
            <button onClick={() => onRetry(originalText)} style={{ marginTop: '8px', background: 'transparent', border: '1px solid #E8002D', borderRadius: '6px', color: '#E8002D', fontSize: '12px', padding: '5px 12px', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '6px' }}>
              ↩ Retry
            </button>
          )}
        </div>
      </div>

      {/* Follow-up suggestions */}
      {followups && (
        <div style={{ marginLeft: '42px', marginTop: '8px', display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
          {followups.map(s => (
            <button key={s} onClick={() => onSuggestion(s)} style={{ background: '#1a1a1a', border: '1px solid #2a2a2a', borderRadius: '14px', color: '#999', fontSize: '12px', padding: '5px 12px', cursor: 'pointer', transition: 'all 0.15s' }}
              onMouseEnter={e => { e.target.style.borderColor = '#E8002D'; e.target.style.color = '#fff'; }}
              onMouseLeave={e => { e.target.style.borderColor = '#2a2a2a'; e.target.style.color = '#999'; }}
            >
              {s}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
