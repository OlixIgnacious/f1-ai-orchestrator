import { useState, useEffect, useCallback, useRef } from 'react';
import { createSession, streamMessage } from './api';
import Header from './components/Header';
import MessageList from './components/MessageList';
import InputBar from './components/InputBar';
import './styles/theme.css';

const API_URL = import.meta.env.VITE_API_URL ?? '';
const USER_ID  = import.meta.env.VITE_USER_ID  || 'user';

export default function App() {
  const [sessionId, setSessionId] = useState(null);
  const [messages,  setMessages]  = useState([]);
  const [loading,   setLoading]   = useState(false);
  const [connected, setConnected] = useState(false);

  const initDone = useRef(false);
  useEffect(() => {
    if (initDone.current) return;
    initDone.current = true;

    const stored = localStorage.getItem('f1_session_id');
    const resume = stored
      ? fetch(`${API_URL}/apps/f1_orchestrator/users/${USER_ID}/sessions/${stored}`)
          .then(r => r.ok ? stored : null).catch(() => null)
      : Promise.resolve(null);

    resume.then(existingId => {
      if (existingId) { setSessionId(existingId); setConnected(true); }
      else return createSession().then(id => {
        localStorage.setItem('f1_session_id', id);
        setSessionId(id); setConnected(true);
      });
    }).catch(() => setConnected(false));
  }, []);

  const sendMessage = useCallback(async (text) => {
    if (!sessionId || loading) return;

    const userId      = Date.now();
    const assistantId = userId + 1;

    setMessages(prev => [
      ...prev,
      { id: userId,      role: 'user',      text },
      { id: assistantId, role: 'assistant', text: '', streaming: true,
        thinking: true, steps: [], agent: null, originalText: text },
    ]);
    setLoading(true);

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 5 * 60 * 1000);

    try {
      let received = false;
      let finalAgent = null;

      for await (const event of streamMessage(sessionId, text, controller.signal)) {
        if (event.type === 'step') {
          setMessages(prev => prev.map(m =>
            m.id === assistantId
              ? { ...m, steps: [...(m.steps || []).slice(-4), event.label] }
              : m
          ));
        } else if (event.type === 'text') {
          if (!received) {
            received = true;
            setMessages(prev => prev.map(m =>
              m.id === assistantId ? { ...m, thinking: false } : m
            ));
          }
          finalAgent = event.author;
          setMessages(prev => prev.map(m =>
            m.id === assistantId ? { ...m, text: m.text + event.text } : m
          ));
        }
      }

      if (!received) {
        setMessages(prev => prev.map(m =>
          m.id === assistantId
            ? { ...m, text: '⚠️ No response received — server may have restarted. Please retry.' }
            : m
        ));
      } else {
        setMessages(prev => prev.map(m =>
          m.id === assistantId ? { ...m, agent: finalAgent } : m
        ));
      }
    } catch (err) {
      setMessages(prev => prev.map(m =>
        m.id === assistantId
          ? { ...m, text: `⚠️ ${err.name === 'AbortError' ? 'Request timed out (3 min).' : `Connection error: ${err.message}.`} Please retry.` }
          : m
      ));
    } finally {
      clearTimeout(timeout);
      setMessages(prev => prev.map(m =>
        m.id === assistantId ? { ...m, streaming: false, thinking: false } : m
      ));
      setLoading(false);
    }
  }, [sessionId, loading]);

  function newSession() {
    localStorage.removeItem('f1_session_id');
    initDone.current = false;
    setSessionId(null); setMessages([]); setConnected(false); setLoading(false);
    createSession().then(id => {
      localStorage.setItem('f1_session_id', id);
      setSessionId(id); setConnected(true);
    }).catch(() => setConnected(false));
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', overflow: 'hidden' }}>
      <Header connected={connected} onNewSession={newSession} />
      <MessageList messages={messages} onSuggestion={sendMessage} onRetry={sendMessage} />
      <InputBar onSend={sendMessage} disabled={!connected || loading} loading={loading} />
    </div>
  );
}
