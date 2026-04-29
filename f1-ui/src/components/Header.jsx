export default function Header({ connected, onNewSession }) {
  return (
    <header style={{
      background: '#0d0d0d',
      borderBottom: '3px solid #E8002D',
      padding: '0 24px',
      height: '64px',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      flexShrink: 0,
      position: 'sticky',
      top: 0,
      zIndex: 10,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '14px' }}>
        {/* F1 logo mark */}
        <div style={{
          background: '#E8002D',
          color: '#fff',
          fontWeight: '900',
          fontSize: '15px',
          padding: '4px 10px',
          letterSpacing: '-0.5px',
          borderRadius: '3px',
          fontStyle: 'italic',
        }}>F1</div>
        <div>
          <div style={{ fontWeight: '700', fontSize: '16px', letterSpacing: '0.02em', color: '#f0f0f0' }}>
            Virtual Pit Wall
          </div>
          <div style={{ fontSize: '11px', color: '#888', letterSpacing: '0.1em', textTransform: 'uppercase' }}>
            AI Race Strategist
          </div>
        </div>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
        {/* New Session button */}
        <button
          onClick={onNewSession}
          title="Start a new chat session"
          style={{
            background: 'transparent', border: '1px solid #2a2a2a',
            borderRadius: '6px', color: '#888', fontSize: '12px',
            padding: '5px 10px', cursor: 'pointer', transition: 'all 0.15s',
          }}
          onMouseEnter={e => { e.target.style.borderColor = '#E8002D'; e.target.style.color = '#fff'; }}
          onMouseLeave={e => { e.target.style.borderColor = '#2a2a2a'; e.target.style.color = '#888'; }}
        >
          + New Session
        </button>

        {/* Connection status */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '12px', color: '#888' }}>
          <span style={{
            width: '8px', height: '8px', borderRadius: '50%',
            background: connected ? '#22c55e' : '#E8002D',
            display: 'inline-block',
            animation: connected ? 'none' : 'pulse 1.5s infinite',
          }} />
          {connected ? 'Pit Wall Online' : 'Connecting…'}
        </div>
      </div>
    </header>
  );
}
