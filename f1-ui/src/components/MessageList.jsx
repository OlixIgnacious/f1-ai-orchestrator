import { useEffect, useRef } from 'react';
import MessageBubble from './MessageBubble';

const SUGGESTIONS = [
  'Who won the 2024 Monaco GP?',
  'Top 5 penalties in 2025 season',
  'Analyse Norris vs Piastri telemetry at Monza 2024',
  'Add next race to my calendar',
  'Was the pit release at 2024 Bahrain safe?',
  'Tell me about the Spa-Francorchamps circuit',
];

export default function MessageList({ messages, onSuggestion, onRetry }) {
  const bottomRef = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  if (messages.length === 0) {
    return (
      <div style={{
        flex: 1, display: 'flex', flexDirection: 'column',
        alignItems: 'center', justifyContent: 'center',
        padding: '40px 24px', gap: '32px',
      }}>
        {/* Hero */}
        <div style={{ textAlign: 'center' }}>
          <div style={{
            fontSize: '48px', fontWeight: '900', fontStyle: 'italic',
            color: '#E8002D', letterSpacing: '-2px', lineHeight: 1,
          }}>F1</div>
          <div style={{ fontSize: '22px', fontWeight: '700', color: '#f0f0f0', marginTop: '8px' }}>
            Virtual Pit Wall
          </div>
          <div style={{ fontSize: '13px', color: '#666', marginTop: '6px', letterSpacing: '0.08em', textTransform: 'uppercase' }}>
            Powered by Gemini 2.5 Flash · AlloyDB · FastF1
          </div>
        </div>

        {/* Suggestion chips */}
        <div style={{
          display: 'flex', flexWrap: 'wrap', gap: '10px',
          justifyContent: 'center', maxWidth: '640px',
        }}>
          {SUGGESTIONS.map((s) => (
            <button
              key={s}
              onClick={() => onSuggestion(s)}
              style={{
                background: '#1e1e1e', border: '1px solid #2a2a2a',
                borderRadius: '20px', color: '#ccc', fontSize: '13px',
                padding: '8px 16px', cursor: 'pointer', transition: 'all 0.15s',
              }}
              onMouseEnter={e => { e.target.style.borderColor = '#E8002D'; e.target.style.color = '#fff'; }}
              onMouseLeave={e => { e.target.style.borderColor = '#2a2a2a'; e.target.style.color = '#ccc'; }}
            >
              {s}
            </button>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div style={{
      flex: 1, overflowY: 'auto', padding: '20px 24px',
      display: 'flex', flexDirection: 'column', gap: '8px',
    }}>
      {messages.map((msg) => (
        <MessageBubble key={msg.id} message={msg} onRetry={onRetry} onSuggestion={onSuggestion} />
      ))}
      <div ref={bottomRef} />
    </div>
  );
}
