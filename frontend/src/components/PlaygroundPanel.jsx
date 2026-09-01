/**
 * PlaygroundPanel Component
 *
 * Governed strategy testing on historical windows under deterministic
 * stress (ADR-031). Runs the full-chain scope gate: gateway key, actor
 * credentials, and the `playground.run` wildcard grant.
 *
 * @component
 */

import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { playgroundAPI } from '../api';
import { useToast } from './Toast';

const EMPTY_FORM = {
  symbol: 'BTCUSDT',
  timeframe: '5m',
  bars: 240,
  seed: 11,
  flashCrash: false,
  crashMagnitude: -12,
  staleFeed: false,
  staleBars: 20,
  liquidityDrought: false,
  droughtDepth: 0.3,
};

function buildWindowPayload(bars, seed) {
  // Deterministic seeded GBM window built client-side (ADR-031 v1:
  // inline windows; lake-backed windows land with the data-fusion phase).
  let s = seed >>> 0;
  const next = () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 4294967296;
  };
  const gaussian = () =>
    Math.sqrt(-2 * Math.log(next() + 1e-12)) * Math.cos(2 * Math.PI * next());
  const timestamps = [];
  const open = [];
  const high = [];
  const low = [];
  const close = [];
  const volume = [];
  const spread = [];
  let price = 50000;
  const start = Date.UTC(2024, 6, 1, 0, 0, 0);
  for (let i = 0; i < bars; i += 1) {
    timestamps.push(new Date(start + i * 300000).toISOString());
    const o = i === 0 ? price : close[i - 1];
    const drift = 0.0002 + 0.0015 * gaussian();
    const c = o * Math.exp(drift);
    const h = Math.max(o, c) * (1 + Math.abs(gaussian()) * 0.0004);
    const l = Math.min(o, c) * (1 - Math.abs(gaussian()) * 0.0004);
    open.push(o);
    high.push(h);
    low.push(l);
    close.push(c);
    volume.push(10 + next() * 990);
    spread.push(2);
    price = c;
  }
  return { timestamps, open, high, low, close, volume, spread_bps: spread };
}

function EquityChart({ curve }) {
  if (!curve || curve.length < 2) return null;
  const values = curve.map((point) => point.equity);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const width = 560;
  const height = 140;
  const points = curve
    .map((point, index) => {
      const x = (index / (curve.length - 1)) * width;
      const y = height - ((point.equity - min) / span) * height;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');
  return (
    <div className="equity-chart" data-testid="equity-chart">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label="Equity curve"
        style={{ width: '100%', height: 'auto' }}
      >
        <polyline
          points={points}
          fill="none"
          stroke="var(--accent, #4a7dff)"
          strokeWidth="1.5"
        />
      </svg>
      <div className="chart-caption">
        Final equity: <strong>{values[values.length - 1].toFixed(2)}</strong>
      </div>
    </div>
  );
}

export default function PlaygroundPanel() {
  const [form, setForm] = useState(EMPTY_FORM);
  const [actor, setActor] = useState('agent-axiom1');
  const [actorKey, setActorKey] = useState('');
  const toast = useToast();

  const runMutation = useMutation({
    mutationFn: (payload) => playgroundAPI.run(payload, actor, actorKey),
    onSuccess: () => toast.success('Playground run complete'),
    onError: (error) => toast.error(error.message || 'Run failed'),
  });

  const update = (field) => (event) => {
    const value =
      event.target.type === 'checkbox' ? event.target.checked : event.target.value;
    setForm((current) => ({ ...current, [field]: value }));
  };

  const handleSubmit = (event) => {
    event.preventDefault();
    const overlays = [];
    if (form.flashCrash) {
      overlays.push({
        type: 'gap_injection',
        params: {
          kind: 'flash_crash',
          magnitude_pct: Number(form.crashMagnitude),
          recovery_bars: 10,
          start: Math.floor(Number(form.bars) * 0.4),
        },
      });
    }
    if (form.staleFeed) {
      overlays.push({
        type: 'stale_feed',
        params: {
          duration_bars: Number(form.staleBars),
          start: Math.floor(Number(form.bars) * 0.25),
        },
      });
    }
    if (form.liquidityDrought) {
      overlays.push({
        type: 'liquidity_drought',
        params: {
          depth_multiplier: Number(form.droughtDepth),
          start: Math.floor(Number(form.bars) * 0.5),
        },
      });
    }
    runMutation.mutate({
      symbol: form.symbol,
      timeframe: form.timeframe,
      window: buildWindowPayload(Number(form.bars), Number(form.seed)),
      strategy: { preset: 'sma-cross', name: 'sma-cross' },
      overlays,
      initial_capital: 10000,
      fee_bps: 10,
      position_fraction: 1,
    });
  };

  const metrics = runMutation.data?.data?.metrics;
  const curve = runMutation.data?.data?.equity_curve;

  return (
    <section className="panel playground-panel" aria-label="Playground">
      <h2>Playground</h2>
      <p className="panel-hint">
        Deterministic strategy testing under stress. Requires the{' '}
        <code>playground.run</code> wildcard grant (ADR-028/031).
      </p>
      <form onSubmit={handleSubmit} className="playground-form">
        <label>
          Symbol
          <input value={form.symbol} onChange={update('symbol')} aria-label="Symbol" />
        </label>
        <label>
          Timeframe
          <select value={form.timeframe} onChange={update('timeframe')} aria-label="Timeframe">
            <option value="1m">1m</option>
            <option value="5m">5m</option>
            <option value="15m">15m</option>
            <option value="1h">1h</option>
          </select>
        </label>
        <label>
          Bars
          <input
            type="number"
            min="60"
            max="5000"
            value={form.bars}
            onChange={update('bars')}
            aria-label="Bars"
          />
        </label>
        <label>
          Seed
          <input
            type="number"
            value={form.seed}
            onChange={update('seed')}
            aria-label="Seed"
          />
        </label>

        <fieldset className="overlay-group">
          <legend>Scenario overlays</legend>
          <label>
            <input
              type="checkbox"
              checked={form.flashCrash}
              onChange={update('flashCrash')}
            />{' '}
            Flash crash
          </label>
          {form.flashCrash && (
            <label>
              Magnitude %
              <input
                type="number"
                step="0.5"
                value={form.crashMagnitude}
                onChange={update('crashMagnitude')}
                aria-label="Crash magnitude percent"
              />
            </label>
          )}
          <label>
            <input
              type="checkbox"
              checked={form.staleFeed}
              onChange={update('staleFeed')}
            />{' '}
            Stale feed
          </label>
          {form.staleFeed && (
            <label>
              Frozen bars
              <input
                type="number"
                min="1"
                value={form.staleBars}
                onChange={update('staleBars')}
                aria-label="Frozen bars"
              />
            </label>
          )}
          <label>
            <input
              type="checkbox"
              checked={form.liquidityDrought}
              onChange={update('liquidityDrought')}
            />{' '}
            Liquidity drought
          </label>
          {form.liquidityDrought && (
            <label>
              Depth multiplier
              <input
                type="number"
                step="0.05"
                min="0.05"
                max="1"
                value={form.droughtDepth}
                onChange={update('droughtDepth')}
                aria-label="Depth multiplier"
              />
            </label>
          )}
        </fieldset>

        <fieldset className="credential-group">
          <legend>Actor credentials</legend>
          <label>
            Actor
            <input
              value={actor}
              onChange={(event) => setActor(event.target.value)}
              aria-label="Actor principal"
            />
          </label>
          <input
            type="password"
            value={actorKey}
            onChange={(event) => setActorKey(event.target.value)}
            placeholder="X-Actor-Key"
            aria-label="Actor key"
          />
        </fieldset>

        <button type="submit" disabled={runMutation.isPending || !actorKey}>
          {runMutation.isPending ? 'Running…' : 'Run simulation'}
        </button>
      </form>

      {metrics && (
        <div className="run-results" data-testid="run-results">
          <h3>Results</h3>
          <dl className="metrics-grid">
            <div>
              <dt>Return</dt>
              <dd data-testid="metric-return">
                {metrics.total_return_pct.toFixed(2)}%
              </dd>
            </div>
            <div>
              <dt>Max drawdown</dt>
              <dd>{(metrics.max_drawdown * 100).toFixed(2)}%</dd>
            </div>
            <div>
              <dt>Trades</dt>
              <dd>{metrics.n_trades}</dd>
            </div>
            <div>
              <dt>Win rate</dt>
              <dd>{(metrics.win_rate * 100).toFixed(1)}%</dd>
            </div>
            <div>
              <dt>Exposure</dt>
              <dd>{(metrics.exposure * 100).toFixed(1)}%</dd>
            </div>
          </dl>
          <EquityChart curve={curve} />
        </div>
      )}
    </section>
  );
}
