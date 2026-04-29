import { useState, useRef } from 'react';

export default function InputBar({ onSend, disabled, loading }) {
  const [text, setText] = useState('');
  const textareaRef = useRef(null);

  function handleSend() {
    const trimmed = text.trim();
    if (!trimmed || disabled) return;
    onSend(trimmed);
    setText('');
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
    }
  }

  function handleKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  }

  function handleInput(e) {
    setText(e.target.value);
    const ta = textareaRef.current;
    if (ta) {
      ta.style.height = 'auto';
      ta.style.height = Math.min(ta.scrollHeight, 140) + 'px';
    }
  }

  return (
    <div style={{
      padding: '16px 24px 20px',
      background: '#0d0d0d',
      borderTop: '1px solid #1e1e1e',
      flexShrink: 0,
    }}>
      <div style={{
        display: 'flex', alignItems: 'flex-end', gap: '12px',
        background: '#1a1a1a', borderRadius: '14px',
        border: '1px solid #2a2a2a',
        padding: '10px 14px',
        transition: 'border-color 0.15s',
      }}
        onFocusCapture={e => e.currentTarget.style.borderColor = '#E8002D'}
        onBlurCapture={e => e.currentTarget.style.borderColor = '#2a2a2a'}
      >
        <textarea
          ref={textareaRef}
          value={text}
          onChange={handleInput}
          onKeyDown={handleKeyDown}
          disabled={disabled}
          placeholder={loading ? 'Pit wall is responding…' : 'Ask the pit wall anything about F1…'}
          rows={1}
          style={{
            flex: 1, background: 'transparent', border: 'none', outline: 'none',
            color: '#f0f0f0', fontSize: '14px', lineHeight: '1.5', resize: 'none',
            fontFamily: 'inherit', maxHeight: '140px', overflowY: 'auto',
          }}
        />

        <button
          onClick={handleSend}
          disabled={disabled || !text.trim()}
          style={{
            width: '36px', height: '36px', borderRadius: '8px', border: 'none',
            background: disabled || !text.trim() ? '#2a2a2a' : '#E8002D',
            color: '#fff', cursor: disabled || !text.trim() ? 'not-allowed' : 'pointer',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            flexShrink: 0, transition: 'background 0.15s',
            fontSize: '16px',
          }}
        >
          {loading ? (
            <span style={{ animation: 'pulse 0.8s infinite', display: 'block', width: '8px', height: '8px', borderRadius: '50%', background: '#fff' }} />
          ) : '↑'}
        </button>
      </div>
      <div style={{ textAlign: 'center', fontSize: '11px', color: '#444', marginTop: '8px' }}>
        Enter to send · Shift+Enter for new line
      </div>
    </div>
  );
}
