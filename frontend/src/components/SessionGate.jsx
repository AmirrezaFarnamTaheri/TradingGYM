import { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { clearApiKey, setApiKey, systemAPI } from '../api';

/** A gateway key opens this browser session; it never grants actor/live approval. */
export default function SessionGate({ children }) {
  const queryClient = useQueryClient();
  const [connected, setConnected] = useState(false);
  const [key, setKey] = useState('');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');

  function disconnect(message = '') {
    clearApiKey();
    queryClient.clear();
    setConnected(false);
    setKey('');
    setError(message);
  }

  useEffect(() => {
    const expired = () => disconnect('Your session is no longer authorized. Enter a valid gateway key.');
    window.addEventListener('trading-gym:session-expired', expired);
    return () => window.removeEventListener('trading-gym:session-expired', expired);
  }, [queryClient]);

  async function connect(event) {
    event.preventDefault();
    if (pending) return;
    const candidate = key.trim();
    if (!candidate || candidate.length > 4096 || /[\r\n\x00]/.test(candidate)) {
      setError('Enter a valid gateway API key.');
      return;
    }
    setPending(true);
    setError('');
    setApiKey(candidate);
    try {
      // A protected read checks the real header/authentication boundary.
      await systemAPI.getStatus();
      setKey('');
      setConnected(true);
    } catch (failure) {
      clearApiKey();
      setError(failure.status === 503
        ? 'The server is not ready. Check its gateway-key configuration and startup diagnostics.'
        : failure.status === 401 || failure.status === 403
          ? 'The gateway key was rejected. Check the key and try again.'
          : 'Could not reach the API. Check the server address and connection, then retry.');
    } finally {
      setPending(false);
    }
  }

  if (connected) return <>
    <div className="session-toolbar">
      <span role="status">Gateway session connected</span>
      <button type="button" onClick={() => disconnect()}>Disconnect session</button>
    </div>
    {children}
  </>;

  return <main className="session-entry" aria-labelledby="session-heading">
    <h1 id="session-heading">Open Trading Gym</h1>
    <p>Enter the gateway API key configured on your server. It stays in this tab's memory and must be entered again after a reload.</p>
    <p>Connecting does not start a trading session or supply independent operator approval.</p>
    <form onSubmit={connect}>
      <label htmlFor="gateway-key">Gateway API key</label>
      <input id="gateway-key" type="password" autoComplete="off" value={key}
        onChange={(event) => setKey(event.target.value)} disabled={pending}
        aria-describedby={error ? 'session-error' : undefined} required />
      <button type="submit" disabled={pending} aria-busy={pending}>
        {pending ? 'Connecting…' : 'Connect'}
      </button>
      {error && <p id="session-error" role="alert">{error}</p>}
    </form>
  </main>;
}
