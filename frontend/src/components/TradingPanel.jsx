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
import { tradingAPI, modelsAPI, createReconnectingWebSocket } from '../api';
import { useStaleFeed } from '../hooks/useStaleFeed';
import StaleFeedBadge from './StaleFeedBadge';
import { useToast } from './Toast';

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
    error: { color: '#e74c3c', bg: '#f8d7da', label: '❌ ERROR' },
  };

  const config = statusConfig[status] || statusConfig.idle;

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
        aria-label={`${label}: ${value}${suffix}`}
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
    data: tradingStatus = { status: 'idle' },
    isLoading: statusLoading,
    isError: statusIsError,
    error: statusError,
  } = useQuery({
    queryKey: ['trading-status'],
    queryFn: () => tradingAPI.getStatus().then(r => r.data),
    refetchInterval: 3000,
  });

  // Active model query
  const {
    data: activeModelData,
    isError: activeModelIsError,
    error: activeModelError,
  } = useQuery({
    queryKey: ['active-model'],
    queryFn: () => modelsAPI.getActive().then(r => r.data),
  });

  const isRunning = ['starting', 'running', 'stopping'].includes(tradingStatus?.status);
  const currentMode = tradingStatus?.mode || mode;
  const activeModel = activeModelData?.active_model;

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
      queryClient.invalidateQueries({ queryKey: ['trading-status'] });
      setShowConfirmLive(false);
      clearLiveAuthorisation();
    },
    onError: (error) => {
      toast.error(`Failed to start trading: ${error.message}`);
    },
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
    onSuccess: () => {
      toast.error('🚨 EMERGENCY: All processes terminated!');
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
    if (window.confirm('🚨 ARE YOU SURE? This will IMMEDIATELY close ALL positions!')) {
      panicMutation.mutate();
    }
  };

  // ==========================================================================
  // Computed Values
  // ==========================================================================

  // Live feeds can deliver NaN/null/string junk; coerce to finite numbers so
  // the panel degrades to neutral defaults instead of rendering 'NaN'.
  const raw = liveMetrics || {};
  const safeNum = (value) => {
    const n = Number(value);
    return Number.isFinite(n) ? n : 0;
  };
  const metrics = {
    balance: safeNum(raw.balance),
    pnl: safeNum(raw.pnl),
    pnl_pct: safeNum(raw.pnl_pct),
    total_positions: Number.isFinite(Number(raw.total_positions))
      ? Math.trunc(Number(raw.total_positions))
      : 0,
    margin_ratio: safeNum(raw.margin_ratio),
    unrealized_pnl: safeNum(raw.unrealized_pnl),
  };

  const pnlColor = metrics.pnl >= 0 ? '#27ae60' : '#e74c3c';
  const pnlTrend = metrics.pnl >= 0 ? 'up' : 'down';

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

  // A failed status/model fetch must surface as an error with a retry path,
  // not as a silent idle panel that looks like a healthy "not running" state.
  if (statusIsError || activeModelIsError) {
    const fetchError = statusIsError ? statusError : activeModelError;
    const what = statusIsError ? 'trading status' : 'active model';
    return (
      <div className="panel trading-panel">
        <h2>💱 Trading Control</h2>
        <div className="error" role="alert" aria-live="assertive">
          Failed to load the {what}: {fetchError?.message || 'Unknown error'}
        </div>
        <div style={{ textAlign: 'center', marginTop: '12px' }}>
          <button
            type="button"
            className="btn btn-secondary"
            onClick={() =>
              queryClient.invalidateQueries({
                queryKey: statusIsError ? ['trading-status'] : ['active-model'],
              })
            }
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="panel trading-panel">
      <h2>💱 Trading Control</h2>

      {/* Status Overview */}
      <div className="section">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '15px' }}>
          <h3 style={{ margin: 0 }}>Status</h3>
          <StatusBadge status={tradingStatus?.status} />
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
              value={metrics.balance?.toFixed(2) || '0.00'}
              prefix="$"
              color="#2c3e50"
            />
            <MetricCard
              label="P&L"
              value={metrics.pnl?.toFixed(2) || '0.00'}
              prefix={metrics.pnl >= 0 ? '+$' : '-$'}
              trend={pnlTrend}
            />
            <MetricCard
              label="P&L %"
              value={metrics.pnl_pct?.toFixed(2) || '0.00'}
              suffix="%"
              trend={pnlTrend}
            />
            <MetricCard
              label="Open Positions"
              value={metrics.total_positions || 0}
              color="#3498db"
            />
            <MetricCard
              label="Margin Used"
              value={(metrics.margin_ratio * 100)?.toFixed(1) || '0.0'}
              suffix="%"
              color={metrics.margin_ratio > 0.8 ? '#e74c3c' : '#27ae60'}
            />
            <MetricCard
              label="Unrealized P&L"
              value={metrics.unrealized_pnl?.toFixed(2) || '0.00'}
              prefix="$"
              trend={metrics.unrealized_pnl >= 0 ? 'up' : 'down'}
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
              <li>Short selling limited to BTC (0.5 max) & ETH (10.0 max) only</li>
              <li>Maximum leverage: 5x for both Long & Short positions</li>
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
              disabled={startMutation.isPending || !activeModel}
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
            {panicMutation.isPending ? '⏳ Executing...' : '🚨 PANIC - Close All Positions'}
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
              <p>This will IMMEDIATELY terminate ALL running processes:</p>
              <ul>
                <li>Training jobs</li>
                <li>Paper/Live trading</li>
                <li>Data downloads</li>
                <li>Backtests</li>
              </ul>
              <p><strong>This action cannot be undone!</strong></p>
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
          <p><strong>Leverage Limits:</strong></p>
          <ul style={{ margin: '5px 0', paddingLeft: '20px' }}>
            <li>Long positions: Up to 5x leverage</li>
            <li>Short positions: Up to 5x leverage (BTC/ETH only)</li>
          </ul>
          <p><strong>Circuit Breakers:</strong></p>
          <ul style={{ margin: '5px 0', paddingLeft: '20px' }}>
            <li>Max loss: 10% per hour</li>
            <li>Margin floor: 2%</li>
            <li>Liquidation threshold: 3%</li>
          </ul>
          <p><strong>Fees:</strong> 0.075% taker fee, 0.05% daily financing</p>
        </div>
      </div>
    </div>
  );
}
