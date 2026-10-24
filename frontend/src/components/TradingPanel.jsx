/**
 * TradingPanel Component
 *
 * Comprehensive trading control panel that provides:
 * - Paper and Live trading modes
 * - Real-time metrics via WebSocket
 * - Emergency controls (Panic button, Kill all)
 * - Position monitoring
 * - Risk configuration display
 *
 * @component
 * @version 11.0.0
 */

import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { tradingAPI, modelsAPI, configAPI, createReconnectingWebSocket } from '../api';
import { useStaleFeed } from '../hooks/useStaleFeed';
import StaleFeedBadge from './StaleFeedBadge';
import { useToast } from './Toast';

const PROCESS_STATES = new Set(['idle', 'starting', 'running', 'stopping', 'stopped', 'completed', 'failed', 'error']);
const finiteNumber = (value) => typeof value === 'number' && Number.isFinite(value) ? value : null;
const formattedNumber = (value, suffix = '', scale = 1) => finiteNumber(value) === null
  ? 'Unavailable' : `${(value * scale).toLocaleString(undefined, { maximumFractionDigits: 4 })}${suffix}`;

// =============================================================================
// Status Badge Component
// =============================================================================

function StatusBadge({ status }) {
  const statusConfig = {
    starting: { color: '#795500', bg: '#fff3cd', label: 'STARTING' },
    stopping: { color: '#795500', bg: '#fff3cd', label: 'STOP REQUESTED' },
    running: { color: '#27ae60', bg: '#d4edda', label: '🟢 ACTIVE' },
    idle: { color: '#7f8c8d', bg: '#f8f9fa', label: '⚪ IDLE' },
    stopped: { color: '#e74c3c', bg: '#f8d7da', label: '🔴 STOPPED' },
    completed: { color: '#23629a', bg: '#e7f0fa', label: 'COMPLETED' },
    failed: { color: '#a42828', bg: '#f8d7da', label: 'FAILED' },
    error: { color: '#e74c3c', bg: '#f8d7da', label: '❌ ERROR' },
    unknown: { color: '#795500', bg: '#fff3cd', label: 'STATUS UNAVAILABLE' },
  };

  const config = statusConfig[status] || statusConfig.unknown;

  return (
    <span
      className="badge"
      style={{
        backgroundColor: config.bg,
        color: config.color,
        padding: '6px 12px',
        borderRadius: '16px',
        fontWeight: '600',
        fontSize: '12px',
      }}
    >
      {config.label}
    </span>
  );
}

// =============================================================================
// Metric Card Component
// =============================================================================

function MetricCard({ label, value, prefix = '', suffix = '', trend, color }) {
  const trendColors = {
    up: '#27ae60',
    down: '#e74c3c',
    neutral: '#7f8c8d',
  };

  return (
    <div className="metric-card">
      <div className="metric-label">{label}</div>
      <div
        className="metric-value"
        style={{ color: color || trendColors[trend] || '#2c3e50' }}
        aria-label={`${label}: ${prefix}${value}${suffix}`}
      >
        {prefix}{typeof value === 'number' ? value.toLocaleString() : value}{suffix}
      </div>
    </div>
  );
}

// =============================================================================
// Main TradingPanel Component
// =============================================================================

export default function TradingPanel() {
  const toast = useToast();
  const queryClient = useQueryClient();

  // Local state
  const [mode, setMode] = useState('paper');
  const [liveMetrics, setLiveMetrics] = useState(null);
  const [wsStatus, setWsStatus] = useState('disconnected');
  const [showConfirmLive, setShowConfirmLive] = useState(false);
  const [showEmergencyConfirm, setShowEmergencyConfirm] = useState(false);
  const [liveAuthorisation, setLiveAuthorisation] = useState({
    proposalId: '',
    expectedRevision: '',
    actor: '',
    actorKey: '',
    approvalKey: '',
  });
  const [emergencyKey, setEmergencyKey] = useState('');

  // Live metrics arrive over the websocket; if the feed stalls, the last frame
  // would otherwise keep rendering as if it were current.
  const { stale: metricsStale, markFresh } = useStaleFeed();

  const clearLiveAuthorisation = () => setLiveAuthorisation({
    proposalId: '',
    expectedRevision: '',
    actor: '',
    actorKey: '',
    approvalKey: '',
  });

  // ==========================================================================
  // Queries
  // ==========================================================================

  // Trading status query
  const {
    data: tradingStatus,
    isLoading: statusLoading,
    isError: statusIsError,
    error: statusError,
  } = useQuery({
    queryKey: ['trading-status'],
    queryFn: async () => {
      const { data } = await tradingAPI.getStatus();
      if (!PROCESS_STATES.has(data?.status)) throw new Error('Invalid trading process status');
      return data;
    },
    refetchInterval: 3000,
    retry: false,
  });

  // Active model query
  const {
    data: activeModelData,
    isError: activeModelIsError,
    error: activeModelError,
  } = useQuery({
    queryKey: ['active-model'],
    queryFn: () => modelsAPI.getActive().then(r => r.data),
    retry: false,
  });

  const { data: configuredRisk, isError: riskIsError, isLoading: riskLoading, refetch: refreshRisk } = useQuery({
    queryKey: ['config', 'env'],
    queryFn: () => configAPI.getSection('env').then(r => r.data),
    retry: false,
  });
  const statusUnknown = statusIsError || !tradingStatus;
  const isRunning = !statusUnknown && ['starting', 'running', 'stopping'].includes(tradingStatus?.status);
  const currentMode = tradingStatus?.mode || mode;
  const activeModel = activeModelData?.active_model;
  const risk = riskIsError ? null : configuredRisk;

  // ==========================================================================
  // WebSocket for Real-time Metrics
  // ==========================================================================

  useEffect(() => {
    const ws = createReconnectingWebSocket('metrics', {
      onConnect: () => setWsStatus('connected'),
      onDisconnect: () => setWsStatus('disconnected'),
      onMessage: (data) => {
        markFresh();
        setLiveMetrics(data);
      },
    });

    ws.connect();

    return () => ws.disconnect();
  }, [markFresh]);

  // ==========================================================================
  // Mutations
  // ==========================================================================

  const startMutation = useMutation({
    mutationFn: ({ mode, modelVersion, authorisation }) =>
      mode === 'live'
        ? tradingAPI.start(mode, modelVersion, authorisation)
        : tradingAPI.start(mode, modelVersion),
    onSuccess: (data, variables) => {
      toast.success(`${variables.mode.toUpperCase()} worker started. Check session status for readiness.`);
      setShowConfirmLive(false);
      clearLiveAuthorisation();
    },
    onError: (error) => {
      toast.error(`Failed to start trading: ${error.message}`);
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: ['trading-status'] }),
  });

  const stopMutation = useMutation({
    mutationFn: () => tradingAPI.stop(),
    onSuccess: (response) => {
      toast.info(response.data?.status === 'stopped'
        ? 'Trading worker stopped'
        : 'Stop requested; worker exit is not yet confirmed');
      queryClient.invalidateQueries({ queryKey: ['trading-status'] });
    },
    onError: (error) => {
      toast.error(`Failed to stop trading: ${error.message}`);
    },
  });

  const panicMutation = useMutation({
    mutationFn: () => tradingAPI.panic(),
    onSuccess: () => {
      toast.error('Panic request recorded. Check reconciliation for the position outcome.');
      queryClient.invalidateQueries({ queryKey: ['trading-status'] });
    },
    onError: (error) => {
      toast.error(`Panic failed: ${error.message}`);
    },
  });

  const emergencyKillMutation = useMutation({
    mutationFn: ({ key }) => tradingAPI.emergencyKillAll('CONFIRM_KILL', key),
    onSuccess: (response) => {
      const outcome = response.data || {};
      const positions = outcome.flatten_confirmed === true
        ? 'The server confirmed positions were flattened.'
        : 'Position closure is unconfirmed; reconcile the account.';
      const signal = outcome.panic_status === 'degraded'
        ? ' The panic signal could not be saved.' : '';
      toast.warning(`Emergency stop processed. Check process status. ${positions}${signal}`);
      queryClient.invalidateQueries({ queryKey: ['trading-status'] });
      queryClient.invalidateQueries({ queryKey: ['system-status'] });
      setShowEmergencyConfirm(false);
      setEmergencyKey('');
    },
    onError: (error) => {
      toast.error(`Emergency kill failed: ${error.message}`);
    },
  });

  // ==========================================================================
  // Handlers
  // ==========================================================================

  const handleStart = () => {
    if (mode === 'live') {
      setShowConfirmLive(true);
    } else {
      startMutation.mutate({ mode, modelVersion: activeModel });
    }
  };

  const handleConfirmLive = () => {
    startMutation.mutate({
      mode: 'live',
      modelVersion: activeModel,
      authorisation: {
        ...liveAuthorisation,
        expectedRevision: Number(liveAuthorisation.expectedRevision),
      },
    });
  };

  const handlePanic = () => {
    if (window.confirm('Request a panic stop? The worker will attempt to close positions. Confirm the outcome in account reconciliation.')) {
      panicMutation.mutate();
    }
  };

  // ==========================================================================
  // Computed Values
  // ==========================================================================

  // Missing or invalid telemetry is unavailable, never an invented zero balance.
  const raw = liveMetrics || {};
  const metrics = {
    balance: finiteNumber(raw.balance),
    pnl: finiteNumber(raw.pnl),
    pnl_pct: finiteNumber(raw.pnl_pct),
    total_positions: Number.isInteger(raw.total_positions) && raw.total_positions >= 0 ? raw.total_positions : null,
    margin_ratio: finiteNumber(raw.margin_ratio),
    unrealized_pnl: finiteNumber(raw.unrealized_pnl),
  };
  const pnlTrend = metrics.pnl === null ? 'neutral' : metrics.pnl >= 0 ? 'up' : 'down';
  const riskRows = [
    ['Maximum long leverage', formattedNumber(risk?.leverage?.max_leverage, '×')],
    ['Maximum short leverage', risk?.shorts?.enabled === false ? 'Short selling disabled' : formattedNumber(risk?.leverage?.max_short_leverage, '×')],
    ['Shortable assets', risk?.shorts?.enabled === false ? 'None' : Array.isArray(risk?.shorts?.allowed_symbols) ? risk.shorts.allowed_symbols.join(', ') || 'None' : 'Unavailable'],
    ['Loss limit', `${formattedNumber(risk?.breaker?.max_loss_pct, '%')} / ${formattedNumber(risk?.breaker?.max_loss_window_minutes, ' minutes')}`],
    ['Margin floor', formattedNumber(risk?.breaker?.margin_ratio_floor, '%', 100)],
    ['Liquidation threshold', formattedNumber(risk?.leverage?.liquidation_margin_ratio, '%', 100)],
    ['Taker fee', formattedNumber(risk?.costs?.taker_fee_bps, '%', .01)],
    ['Daily financing rate', formattedNumber(risk?.leverage?.financing?.daily_pct, '%')],
  ];

  // ==========================================================================
  // Render
  // ==========================================================================

  if (statusLoading) {
    return (
      <div className="panel trading-panel">
        <h2>💱 Trading Control</h2>
        <div className="loading">Loading trading status...</div>
      </div>
    );
  }

  return (
    <div className="panel trading-panel">
      <h2>💱 Trading Control</h2>
      {(statusIsError || activeModelIsError) && <div className="error" role="alert">
        <p>Failed to load the {statusIsError ? 'trading status' : 'active model'}: {(statusIsError ? statusError : activeModelError)?.message || 'Unknown error'}.</p>
        <p>Starting is blocked until readiness is confirmed. Emergency stop controls remain available.</p>
        <button type="button" className="btn btn-secondary" onClick={() => {
          queryClient.invalidateQueries({ queryKey: ['trading-status'] });
          queryClient.invalidateQueries({ queryKey: ['active-model'] });
        }}>Retry</button>
      </div>}

      {/* Status Overview */}
      <div className="section">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '15px' }}>
          <h3 style={{ margin: 0 }}>Status</h3>
          <StatusBadge status={statusUnknown ? 'unknown' : tradingStatus.status} />
        </div>

        <div className="info-box">
          <p><strong>Mode:</strong> {currentMode?.toUpperCase() || 'Not Running'}</p>
          <p><strong>Active Model:</strong> {activeModel || 'None selected'}</p>
          <p><strong>WebSocket:</strong> {wsStatus === 'connected' ? '🟢 Connected' : '🔴 Disconnected'}</p>
          {tradingStatus?.pid && <p><strong>PID:</strong> {tradingStatus.pid}</p>}
        </div>
      </div>

      {/* Live Metrics */}
      {isRunning && (
        <div className="section">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '15px' }}>
            <h3 style={{ margin: 0 }}>Live Metrics</h3>
            {metricsStale && <StaleFeedBadge testId="trading-stale-metrics" />}
          </div>
          <div className="metrics-grid">
            <MetricCard
              label="Balance"
              value={metrics.balance?.toFixed(2) ?? 'Unavailable'}
              prefix={metrics.balance === null ? '' : '$'}
              color="#2c3e50"
            />
            <MetricCard
              label="P&L"
              value={metrics.pnl === null ? 'Unavailable' : Math.abs(metrics.pnl).toFixed(2)}
              prefix={metrics.pnl === null ? '' : metrics.pnl >= 0 ? '+$' : '-$'}
              trend={pnlTrend}
            />
            <MetricCard
              label="P&L %"
              value={metrics.pnl_pct?.toFixed(2) ?? 'Unavailable'}
              suffix={metrics.pnl_pct === null ? '' : '%'}
              trend={pnlTrend}
            />
            <MetricCard
              label="Open Positions"
              value={metrics.total_positions ?? 'Unavailable'}
              color="#3498db"
            />
            <MetricCard
              label="Margin ratio"
              value={metrics.margin_ratio === null ? 'Unavailable' : (metrics.margin_ratio * 100).toFixed(1)}
              suffix={metrics.margin_ratio === null ? '' : '%'}
            />
            <MetricCard
              label="Unrealized P&L"
              value={metrics.unrealized_pnl?.toFixed(2) ?? 'Unavailable'}
              prefix={metrics.unrealized_pnl === null ? '' : '$'}
              trend={metrics.unrealized_pnl === null ? 'neutral' : metrics.unrealized_pnl >= 0 ? 'up' : 'down'}
            />
          </div>
        </div>
      )}

      {/* Mode Selection */}
      <div className="section">
        <h3>Trading Mode</h3>
        <div className="mode-buttons" style={{ display: 'flex', gap: '15px', marginBottom: '15px' }}>
          <button
            onClick={() => setMode('paper')}
            disabled={isRunning}
            className={`btn ${mode === 'paper' && !isRunning ? 'btn-primary' : 'btn-secondary'}`}
            style={{ flex: 1 }}
          >
            🧪 Paper Trading
          </button>
          <button
            onClick={() => setMode('live')}
            disabled={isRunning}
            className={`btn ${mode === 'live' && !isRunning ? 'btn-danger' : 'btn-secondary'}`}
            style={{ flex: 1 }}
          >
            🚀 Live Trading
          </button>
        </div>

        {mode === 'live' && !isRunning && (
          <div className="info-box" style={{ backgroundColor: '#fff3cd', borderLeftColor: '#f39c12' }}>
            <p><strong>⚠️ LIVE TRADING WARNING:</strong></p>
            <ul style={{ margin: '10px 0', paddingLeft: '20px' }}>
              <li>Real money will be used</li>
              <li>Losses are real and cannot be undone</li>
              <li>The selected model must be admitted and active</li>
              <li>A separately approved critical <code>trading.start_live</code> proposal is required</li>
              <li>The executor principal and approval factor are verified independently</li>
              <li>Review the current configured limits below before requesting a start</li>
            </ul>
          </div>
        )}
      </div>

      {/* Trading Controls */}
      <div className="section">
        <h3>Controls</h3>
        <div className="control-buttons">
          {!isRunning ? (
            <button
              onClick={handleStart}
              disabled={startMutation.isPending || statusUnknown || activeModelIsError || !activeModel}
              className="btn btn-success btn-large"
              style={{ flex: 2 }}
            >
              {startMutation.isPending ? '⏳ Starting...' : `▶️ START ${mode.toUpperCase()} TRADING`}
            </button>
          ) : (
            <button
              onClick={() => stopMutation.mutate()}
              disabled={stopMutation.isPending}
              className="btn btn-warning btn-large"
              style={{ flex: 2 }}
            >
              {stopMutation.isPending ? '⏳ Stopping...' : tradingStatus?.status === 'stopping' ? 'Retry stop request' : '⏹️ STOP TRADING'}
            </button>
          )}
        </div>

        {!activeModel && (
          <p style={{ color: '#e74c3c', marginTop: '10px', fontSize: '14px' }}>
            ⚠️ No active model selected. Please select a model in the Models panel first.
          </p>
        )}
      </div>

      {/* Live Confirmation Modal */}
      {showConfirmLive && (
        <div className="live-confirmation" role="dialog" aria-modal="true" aria-label="Governed live trading authorisation">
          <div className="confirmation-warning">
            <strong>⚠️ CONFIRM LIVE TRADING</strong>
            <p>You are requesting a governed LIVE start with real funds and model <strong>{activeModel}</strong>.</p>
            <p>The API will consume a previously approved critical proposal and bind a one-use runtime authorisation to the spawned process.</p>
            <ul>
              <li>Ensure you have reviewed your strategy</li>
              <li>Confirm your risk management settings</li>
              <li>Confirm the proposal payload names this exact model and operation</li>
              <li>Use an executor principal distinct from the proposer and approver</li>
            </ul>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '10px', margin: '15px 0' }}>
            <label>Proposal ID
              <input
                value={liveAuthorisation.proposalId}
                onChange={event => setLiveAuthorisation({ ...liveAuthorisation, proposalId: event.target.value })}
                autoComplete="off"
              />
            </label>
            <label>Expected revision
              <input
                type="number"
                min="1"
                value={liveAuthorisation.expectedRevision}
                onChange={event => setLiveAuthorisation({ ...liveAuthorisation, expectedRevision: event.target.value })}
              />
            </label>
            <label>Executor principal
              <input
                value={liveAuthorisation.actor}
                onChange={event => setLiveAuthorisation({ ...liveAuthorisation, actor: event.target.value })}
                autoComplete="username"
              />
            </label>
            <label>Principal key
              <input
                type="password"
                value={liveAuthorisation.actorKey}
                onChange={event => setLiveAuthorisation({ ...liveAuthorisation, actorKey: event.target.value })}
                autoComplete="off"
              />
            </label>
            <label>Approval factor
              <input
                type="password"
                value={liveAuthorisation.approvalKey}
                onChange={event => setLiveAuthorisation({ ...liveAuthorisation, approvalKey: event.target.value })}
                autoComplete="off"
              />
            </label>
          </div>
          <div className="confirmation-buttons">
            <button
              onClick={handleConfirmLive}
              disabled={
                startMutation.isPending
                || statusUnknown
                || activeModelIsError
                || isRunning
                || !activeModel
                || !liveAuthorisation.proposalId
                || !liveAuthorisation.expectedRevision
                || !liveAuthorisation.actor
                || !liveAuthorisation.actorKey
                || !liveAuthorisation.approvalKey
              }
              className="btn btn-danger"
            >
              {startMutation.isPending ? '⏳ Starting...' : '🚀 CONFIRM - START LIVE'}
            </button>
            <button
              onClick={() => {
                setShowConfirmLive(false);
                clearLiveAuthorisation();
              }}
              className="btn btn-secondary"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Emergency Controls */}
      <div className="section emergency-section">
        <h3 style={{ color: '#e74c3c' }}>🚨 Emergency Controls</h3>

        <div className="control-buttons" style={{ marginTop: '15px' }}>
          <button
            onClick={handlePanic}
            disabled={!isRunning || panicMutation.isPending}
            className="btn btn-danger"
            style={{ flex: 1 }}
          >
            {panicMutation.isPending ? 'Sending request…' : 'Request panic stop'}
          </button>

          <button
            onClick={() => setShowEmergencyConfirm(true)}
            disabled={emergencyKillMutation.isPending}
            className="btn btn-danger"
            style={{ flex: 1, backgroundColor: '#c0392b' }}
          >
            ☠️ KILL ALL PROCESSES
          </button>
        </div>

        {showEmergencyConfirm && (
          <div className="emergency-confirm" style={{ marginTop: '15px' }}>
            <div className="confirmation-warning" style={{ backgroundColor: '#f8d7da', padding: '15px', borderRadius: '8px' }}>
              <strong style={{ color: '#c0392b' }}>⚠️ EMERGENCY KILL CONFIRMATION</strong>
              <p>Request a stop for all managed processes:</p>
              <ul>
                <li>Training jobs</li>
                <li>Paper/Live trading</li>
                <li>Data downloads</li>
                <li>Backtests</li>
              </ul>
              <p>The server first attempts a graceful trading stop, then terminates managed workers. Check process status and reconcile positions afterward.</p>
              <label style={{ display: 'block', marginTop: '12px' }}>
                Independent emergency key
                <input
                  type="password"
                  value={emergencyKey}
                  onChange={event => setEmergencyKey(event.target.value)}
                  autoComplete="off"
                  style={{ display: 'block', width: '100%', marginTop: '6px' }}
                />
              </label>
            </div>
            <div className="confirmation-buttons" style={{ marginTop: '15px' }}>
              <button
                onClick={() => emergencyKillMutation.mutate({ key: emergencyKey })}
                disabled={emergencyKillMutation.isPending || !emergencyKey}
                className="btn btn-danger"
              >
                {emergencyKillMutation.isPending ? '⏳ Killing...' : '☠️ CONFIRM KILL ALL'}
              </button>
              <button
                onClick={() => {
                  setShowEmergencyConfirm(false);
                  setEmergencyKey('');
                }}
                className="btn btn-secondary"
              >
                Cancel
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Risk Configuration Display */}
      <div className="section">
        <h3>Risk Configuration</h3>
        <div className="info-box">
          <p>Current server configuration. A running worker keeps the settings admitted when it started.</p>
          {riskLoading && <p role="status">Loading configured limits…</p>}
          {riskIsError && <p role="alert">Configured limits are unavailable. <button type="button" className="btn" onClick={() => refreshRisk()}>Retry limits</button></p>}
          {!riskLoading && !riskIsError && <table className="models-table">
            <caption>Configured risk and cost limits</caption>
            <tbody>{riskRows.map(([label, value]) => <tr key={label}>
              <th scope="row">{label}</th><td>{value}</td>
            </tr>)}</tbody>
          </table>}
        </div>
      </div>
    </div>
  );
}
