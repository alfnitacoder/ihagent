import React, { useEffect, useState } from 'react';
import {
  enableMicrophone,
  getController,
  getSnapshot,
  setAgentMessage,
  setAgentState,
  subscribe,
} from './controller';
import { STATE_LABELS, STATES } from './states';
import { bridgeOrigin, connectBridge, sendBridgeMessage } from './bridge';
import './robot.css';

function webglAvailable() {
  try {
    const canvas = document.createElement('canvas');
    return !!(canvas.getContext('webgl2') || canvas.getContext('webgl'));
  } catch (err) {
    return false;
  }
}

function CssRobot() {
  return (
    <div className="ih-fallback" aria-hidden="true">
      <div className="ih-fallback-body" />
    </div>
  );
}

class SceneBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { failed: false };
  }

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    if (this.state.failed) return <CssRobot />;
    return this.props.children;
  }
}

export default function FloatingRobot({
  corner = 'bottom-right',
  size,
  demo = true,
  bridgeUrl,
  showBubble = true,
}) {
  const [snap, setSnap] = useState(getSnapshot);
  const [open, setOpen] = useState(false);
  const [minimized, setMinimized] = useState(false);
  const [theme, setTheme] = useState('auto');
  const [connected, setConnected] = useState(false);
  const [chat, setChat] = useState([]);
  const [draft, setDraft] = useState('');
  const [note, setNote] = useState('');
  const [Scene, setScene] = useState(null);
  const [failed, setFailed] = useState(false);
  const [paused, setPaused] = useState(false);
  const origin = bridgeOrigin(bridgeUrl);

  useEffect(() => subscribe(setSnap), []);

  useEffect(() => {
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)');
    const hidden = () => setPaused(document.hidden || reduce.matches);
    const apply = () => {
      getController().reducedMotion = reduce.matches;
      hidden();
    };
    apply();
    reduce.addEventListener('change', apply);
    document.addEventListener('visibilitychange', hidden);
    return () => {
      reduce.removeEventListener('change', apply);
      document.removeEventListener('visibilitychange', hidden);
    };
  }, []);

  useEffect(() => {
    const move = (ev) => {
      const c = getController();
      c.pointerX = (ev.clientX / window.innerWidth) * 2 - 1;
      c.pointerY = -((ev.clientY / window.innerHeight) * 2 - 1);
    };
    window.addEventListener('pointermove', move, { passive: true });
    return () => window.removeEventListener('pointermove', move);
  }, []);

  useEffect(() => {
    if (!webglAvailable()) {
      setFailed(true);
      return undefined;
    }
    let alive = true;
    import('./RobotScene')
      .then((mod) => {
        if (alive) setScene(() => mod.RobotScene);
      })
      .catch(() => {
        if (alive) setFailed(true);
      });
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    window.IHAgentRobot = {
      setAgentState,
      setAgentMessage,
      getSnapshot,
      STATES,
    };
    return () => {
      if (window.IHAgentRobot && window.IHAgentRobot.setAgentState === setAgentState) {
        delete window.IHAgentRobot;
      }
      const stop = getController().stopAudio;
      if (typeof stop === 'function') stop();
    };
  }, []);

  useEffect(() => {
    return connectBridge(origin, (data) => {
      if (!data || typeof data !== 'object') return;
      if (data.type === 'message') {
        setAgentMessage(data.message || '');
        if (!data.message) return;
        setChat((prev) => {
          const last = prev[prev.length - 1];
          if (last && last.role === 'agent' && !last.final) {
            return prev.slice(0, -1).concat({ role: 'agent', text: data.message, final: !!data.final });
          }
          return prev.concat({ role: 'agent', text: data.message, final: !!data.final });
        });
        return;
      }
      if (data.type !== 'state' || !data.state) return;
      if (data.state === 'listening' && !data.mic) return;
      if (data.state === 'speaking' && !data.audio) return;
      setAgentState(data.state, { message: data.message || '' });
    }, ({ connected: on }) => setConnected(!!on));
  }, [origin]);

  async function onSend(ev) {
    ev.preventDefault();
    const text = draft.trim();
    if (!text) return;
    setDraft('');
    setChat((prev) => prev.concat({ role: 'you', text, final: true }));
    if (!connected) {
      setNote('IHAgent bridge is not connected, so this was not delivered. Microphone level and speech playback are not simulated.');
      return;
    }
    try {
      const result = await sendBridgeMessage(origin, text);
      if (!result.ok) setNote(result.reason);
      else setNote('');
    } catch (err) {
      setNote(err && err.message ? err.message : 'Could not reach the bridge.');
    }
  }

  async function onMic() {
    const result = await enableMicrophone();
    if (!result.ok) setNote(result.reason);
    else setNote('Microphone is live. The robot uses its real level, and only while this tab has permission.');
  }

  const style = size ? { '--ih-size': `${size}px` } : undefined;

  return (
    <div
      className="ih-robot"
      data-corner={corner}
      data-theme={theme}
      data-state={snap.state}
      style={style}
    >
      {demo && !minimized && (
        <div className="ih-demo" role="toolbar" aria-label="Robot states">
          {STATES.map((name) => (
            <button
              key={name}
              type="button"
              aria-pressed={snap.state === name}
              onClick={() => setAgentState(name)}
            >
              {STATE_LABELS[name]}
            </button>
          ))}
        </div>
      )}

      <div className="ih-stage">
        {showBubble && snap.message && !minimized ? (
          <div className="ih-bubble" role="status">{snap.message}</div>
        ) : null}

        {minimized ? (
          <button type="button" className="ih-mini" onClick={() => setMinimized(false)} aria-label="Expand robot">
            <span />
          </button>
        ) : (
          <div className="ih-canvas-wrap">
            {failed || !Scene ? (
              <CssRobot />
            ) : (
              <SceneBoundary>
                <Scene paused={paused} />
              </SceneBoundary>
            )}
            <button
              type="button"
              className="ih-hit"
              aria-expanded={open}
              aria-label="Open IHAgent chat"
              onClick={() => setOpen((value) => !value)}
            />
          </div>
        )}

        {!minimized && (
          <div className="ih-toolbar">
            <span className="ih-pill" aria-live="polite">{STATE_LABELS[snap.state] || snap.state}</span>
            <span className="ih-link" data-on={connected ? 'true' : 'false'}>
              <i />
              {connected ? 'agent' : 'offline'}
            </span>
            <button type="button" className="ih-text-btn" onClick={() => setMinimized(true)}>
              Minimize
            </button>
          </div>
        )}

        {open && !minimized && (
          <section className="ih-chat" aria-label="IHAgent chat">
            <header>
              <h2>IHAgent</h2>
              <button type="button" className="ih-text-btn" onClick={() => setOpen(false)}>Close</button>
            </header>
            <ul className="ih-log">
              {chat.length === 0 && (
                <li className="ih-note">Messages go to a running ihagent only when the local bridge is connected.</li>
              )}
              {chat.map((line, index) => (
                <li key={`${line.role}-${index}`}>
                  <span className="who">{line.role === 'you' ? 'You' : 'Agent'}</span>
                  {line.text}
                </li>
              ))}
            </ul>
            {note ? <p className="ih-note">{note}</p> : null}
            <form onSubmit={onSend}>
              <input
                value={draft}
                onChange={(ev) => setDraft(ev.target.value)}
                placeholder="Message ihagent"
                aria-label="Message ihagent"
              />
              <button type="submit">Send</button>
            </form>
            <div className="ih-chat-actions">
              <button type="button" onClick={onMic}>Use microphone</button>
              <button
                type="button"
                onClick={() => setTheme((value) => (value === 'auto' ? 'light' : value === 'light' ? 'dark' : 'auto'))}
              >
                Theme: {theme}
              </button>
            </div>
          </section>
        )}
      </div>
    </div>
  );
}

export { setAgentState, STATES };
