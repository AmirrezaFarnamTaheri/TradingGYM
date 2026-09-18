/**
 * SystemPanel Component
 *
 * Displays comprehensive system health information, process status,
 * API endpoints reference, and system configuration details.
 *
 * @component
 * @version 4.2.0
 */

import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { systemAPI, createReconnectingWebSocket } from '../api';
import { useStaleFeed } from '../hooks/useStaleFeed';
import StaleFeedBadge from './StaleFeedBadge';
import { useToast } from './Toast';

// Icons using emoji for simplicity (could be replaced with lucide-react)
const StatusIcon = ({ status }) => {
  const icons = {
    running: '🟢',
    idle: '⚪',
    stopped: '🔴',
    completed: '✅',
    error: '❌',
    unknown: '❓'
  };
  return <span role="img" aria-label={status}>{icons[status] || icons.unknown}</span>;
};

export default function SystemPanel() {
  const [wsStatus, setWsStatus] = useState('disconnected');
  const [liveMetrics, setLiveMetrics] = useState(null);
  // Resource metrics are pushed over the websocket; flag them when the feed
  // goes quiet so a frozen frame is never mistaken for a live one.
  const { stale: metricsStale, markFresh } = useStaleFeed();
  const queryClient = useQueryClient();
  const toast = useToast();

  // Fetch system status
  const { data: statusData, isLoading: statusLoading, error: statusError } = useQuery({
    queryKey: ['system-status'],
    queryFn: () => systemAPI.getStatus().then(r => r.data),
    refetchInterval: 5000, // Poll every 5 seconds
    retry: 2,
  });

  // Fetch metrics via HTTP (fallback)
  const { data: metricsData } = useQuery({
    queryKey: ['system-metrics'],
    queryFn: () => systemAPI.getMetrics().then(r => r.data),
    refetchInterval: 10000,
    retry: 1,
  });

  // Health check
  const { data: healthData } = useQuery({
    queryKey: ['health'],
    queryFn: () => systemAPI.health().then(r => r.data),
    refetchInterval: 30000,
    retry: 1,
  });

  // Prune mutation
  const pruneMutation = useMutation({
    mutationFn: () => systemAPI.prune(),
    onSuccess: () => {
      toast.success('Zombie processes pruned successfully');
      queryClient.invalidateQueries({ queryKey: ['system-status'] });
    },
    onError: (error) => {
      toast.error(`Failed to prune: ${error.message}`);
    }
  });

  // WebSocket connection for real-time metrics
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

  // Compute display metrics (prefer live, fallback to HTTP)
  const metrics = liveMetrics || metricsData || {
    cpu_percent: 0,
    memory_percent: 0,
    disk_percent: 0,
    api_latency_ms: 0,
    active_connections: 0,
  };

  // Process list from status
  const processes = statusData?.processes || {};
  const processCount = Object.keys(processes).length;
  const runningCount = Object.values(processes).filter(p => p.status === 'running').length;

  // Health status
  const isHealthy = healthData?.status === 'healthy';
  const lastUpdate = statusData?.timestamp
    ? new Date(statusData.timestamp * 1000).toLocaleTimeString()
    : 'N/A';

  if (statusLoading) {
    return (
      <div className="panel system-panel">
        <h2>🖥️ System Status</h2>
        <div className="loading" role="status" aria-busy="true">
          Loading system information...
        </div>
      </div>
    );
  }

  if (statusError) {
    return (
      <div className="panel system-panel">
        <h2>🖥️ System Status</h2>
        <div className="error" role="alert">
          Failed to load system status: {statusError.message}
          <button
            onClick={() => queryClient.invalidateQueries({ queryKey: ['system-status'] })}
            className="btn btn-secondary"
            style={{ marginTop: '10px' }}
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="panel system-panel">
      <h2>🖥️ System Status</h2>

      {/* Quick Stats Grid */}
      <div className="section">
        <h3>Health Overview</h3>
        <div className="status-grid" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '15px' }}>
          <div className="status-card" role="region" aria-label="API health status">
            <div className="status-label">API Status</div>
            <div className="status-value" style={{ color: isHealthy ? '#27ae60' : '#e74c3c' }}>
              {isHealthy ? '✅ Healthy' : '❌ Unhealthy'}
            </div>
          </div>

          <div className="status-card" role="region" aria-label="WebSocket connection status">
            <div className="status-label">WebSocket</div>
            <div className="status-value" style={{ color: wsStatus === 'connected' ? '#27ae60' : '#f39c12' }}>
              {wsStatus === 'connected' ? '🔗 Connected' : '⏳ Connecting...'}
            </div>
          </div>

          <div className="status-card" role="region" aria-label="Process count">
            <div className="status-label">Processes</div>
            <div className="status-value">
              {runningCount}/{processCount} Running
            </div>
          </div>

          <div className="status-card" role="region" aria-label="Last updated time">
            <div className="status-label">Last Update</div>
            <div className="status-value">{lastUpdate}</div>
          </div>
        </div>
      </div>

      {/* Resource Metrics */}
      <div className="section">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <h3>System Resources</h3>
          {metricsStale && <StaleFeedBadge testId="system-stale-metrics" />}
        </div>
        <div className="metrics-display">
          <div className="metric-row">
            <span className="metric-label">🔧 CPU Usage</span>
            <span className="metric-value">
              <progress value={metrics.cpu_percent || 0} max="100" style={{ width: '100px', marginRight: '10px' }} />
              {(metrics.cpu_percent || 0).toFixed(1)}%
            </span>
          </div>

          <div className="metric-row">
            <span className="metric-label">💾 Memory Usage</span>
            <span className="metric-value">
              <progress value={metrics.memory_percent || 0} max="100" style={{ width: '100px', marginRight: '10px' }} />
              {(metrics.memory_percent || 0).toFixed(1)}%
            </span>
          </div>

          <div className="metric-row">
            <span className="metric-label">📀 Disk Usage</span>
            <span className="metric-value">
              <progress value={metrics.disk_percent || 0} max="100" style={{ width: '100px', marginRight: '10px' }} />
              {(metrics.disk_percent || 0).toFixed(1)}%
            </span>
          </div>

          <div className="metric-row">
            <span className="metric-label">⚡ API Latency</span>
            <span className="metric-value">{(metrics.api_latency_ms || 0).toFixed(0)} ms</span>
          </div>

          <div className="metric-row">
            <span className="metric-label">🌐 Rate Limits</span>
            <span className="metric-value">50/60 requests per minute</span>
          </div>
        </div>
      </div>

      {/* Process Table */}
      <div className="section">
        <h3>Active Processes</h3>
        {processCount === 0 ? (
          <div className="info-box">
            <p>No active processes. Start training or trading to see processes here.</p>
          </div>
        ) : (
          <div className="processes-table" style={{ overflowX: 'auto' }}>
            <table className="models-table" aria-label="Active processes table">
              <thead>
                <tr>
                  <th>Process</th>
                  <th>Status</th>
                  <th>PID</th>
                  <th>Start Time</th>
                  <th>Details</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(processes).map(([key, proc]) => (
                  <tr key={key}>
                    <td><strong>{key}</strong></td>
                    <td>
                      <StatusIcon status={proc.status} />
                      {' '}{proc.status?.toUpperCase() || 'UNKNOWN'}
                    </td>
                    <td><code>{proc.pid || 'N/A'}</code></td>
                    <td>
                      {proc.start_time
                        ? new Date(proc.start_time * 1000).toLocaleString()
                        : 'N/A'
                      }
                    </td>
                    <td>
                      {proc.config && <span>Config: {proc.config}</span>}
                      {proc.mode && <span>Mode: {proc.mode}</span>}
                      {proc.returncode !== undefined && <span>Exit: {proc.returncode}</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Quick Actions */}
      <div className="section">
        <h3>Quick Actions</h3>
        <div className="button-group">
          <button
            onClick={() => pruneMutation.mutate()}
            disabled={pruneMutation.isPending}
            className="btn btn-warning"
            aria-label="Prune zombie processes"
            aria-busy={pruneMutation.isPending}
          >
            {pruneMutation.isPending ? '⏳ Pruning...' : '✂️ Prune Zombies'}
          </button>

          <button
            onClick={() => {
              queryClient.invalidateQueries({ queryKey: ['system-status'] });
              queryClient.invalidateQueries({ queryKey: ['system-metrics'] });
              queryClient.invalidateQueries({ queryKey: ['health'] });
              toast.info('Refreshing all status...');
            }}
            className="btn btn-secondary"
            aria-label="Refresh all system status"
          >
            🔄 Refresh All
          </button>
        </div>
      </div>

      {/* Environment Info */}
      <div className="section">
        <h3>Environment</h3>
        <div className="system-info">
          <div className="info-row">
            <span className="info-label">Trading Mode</span>
            <span className="info-value" style={{
              color: processes.trading?.mode === 'live' ? '#e74c3c' : '#3498db',
              fontWeight: processes.trading?.mode === 'live' ? 'bold' : 'normal'
            }}>
              {processes.trading?.mode === 'live' ? '🚀 PRODUCTION (Live)' : '🧪 DEVELOPMENT (Paper)'}
            </span>
          </div>

          <div className="info-row">
            <span className="info-label">API Base URL</span>
            <span className="info-value"><code>/api/v1</code></span>
          </div>

          <div className="info-row">
            <span className="info-label">WebSocket URL</span>
            <span className="info-value"><code>ws://localhost:8000/ws</code></span>
          </div>

          <div className="info-row">
            <span className="info-label">Version</span>
            <span className="info-value"><code>4.2.0</code></span>
          </div>
        </div>
      </div>

      {/* API Reference (Collapsible) */}
      <div className="section">
        <h3>API Reference</h3>
        <div className="api-reference">
          <details className="api-section">
            <summary>🏥 Health Endpoints</summary>
            <ul>
              <li><code>GET /health</code> - Liveness probe</li>
              <li><code>GET /ready</code> - Readiness probe</li>
              <li><code>GET /api/v1/status</code> - System status</li>
            </ul>
          </details>

          <details className="api-section">
            <summary>🤖 Training Endpoints</summary>
            <ul>
              <li><code>POST /api/v1/train/start</code> - Start training</li>
              <li><code>POST /api/v1/train/stop</code> - Stop training</li>
              <li><code>GET /api/v1/train/status</code> - Training status</li>
            </ul>
          </details>

          <details className="api-section">
            <summary>💱 Trading Endpoints</summary>
            <ul>
              <li><code>POST /api/v1/trading/start</code> - Start trading</li>
              <li><code>POST /api/v1/trading/stop</code> - Stop trading</li>
              <li><code>GET /api/v1/trading/status</code> - Trading status</li>
              <li><code>POST /api/v1/trading/panic</code> - 🚨 Emergency stop</li>
            </ul>
          </details>

          <details className="api-section">
            <summary>📊 Data Endpoints</summary>
            <ul>
              <li><code>POST /api/v1/data/download</code> - Download OHLCV data</li>
              <li><code>POST /api/v1/data/generate-features</code> - Generate features</li>
            </ul>
          </details>

          <details className="api-section">
            <summary>📈 Model Endpoints</summary>
            <ul>
              <li><code>GET /api/v1/models</code> - List models</li>
              <li><code>POST /api/v1/models/set-active</code> - Set active model</li>
              <li><code>GET /api/v1/models/active</code> - Get active model</li>
              <li><code>POST /api/v1/simulate/backtest</code> - Run backtest</li>
            </ul>
          </details>

          <details className="api-section">
            <summary>🔌 WebSocket Endpoints</summary>
            <ul>
              <li><code>WS /ws/logs</code> - Real-time log streaming</li>
              <li><code>WS /ws/metrics</code> - Real-time metrics streaming</li>
            </ul>
          </details>
        </div>
      </div>

      {/* Quick Links */}
      <div className="section">
        <h3>Documentation Links</h3>
        <div className="quick-links" style={{ display: 'flex', gap: '10px', flexWrap: 'wrap' }}>
          <a
            href="https://github.com/AmirrezaFarnamTaheri/TraderAgentReinforcedGym"
            target="_blank"
            rel="noopener noreferrer"
            className="btn btn-secondary"
          >
            📚 GitHub Repository
          </a>
          <a
            href="/docs/01_Project_Overview.md"
            target="_blank"
            rel="noopener noreferrer"
            className="btn btn-secondary"
          >
            📖 Documentation
          </a>
        </div>
      </div>
    </div>
  );
}
