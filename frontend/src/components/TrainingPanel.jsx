import { useState, useEffect, useRef } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { trainingAPI, createWebSocket } from '../api';
import useStore from '../store';
import { parseTrainingLogEvent } from '../logTelemetry';

// Must match TrainingStartRequest.model_version in the FastAPI schema.
const MODEL_VERSION_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$/;
const PROCESS_STATES = new Set(['idle', 'starting', 'running', 'stopping', 'stopped', 'completed', 'failed', 'error']);

export default function TrainingPanel() {
  const queryClient = useQueryClient();
  const [configName, setConfigName] = useState('training_local.yaml');
  const [modelVersion, setModelVersion] = useState('');
  const validModelVersion = MODEL_VERSION_PATTERN.test(modelVersion);
  const logsEndRef = useRef(null);

  const logs = useStore(state => state.logs);
  const addLog = useStore(state => state.addLog);
  const clearLogs = useStore(state => state.clearLogs);

  // Poll training status
  const { data: statusData, isError: statusError, isLoading: statusLoading, refetch } = useQuery({
    queryKey: ['training-status'],
    queryFn: async () => {
      const { data } = await trainingAPI.getStatus();
      if (!PROCESS_STATES.has(data?.status)) {
        throw new Error('Invalid training process status');
      }
      return data;
    },
    refetchInterval: 2000,
    retry: false,
  });

  const status = !statusError && statusData ? statusData.status : 'unknown';
  const isBusy = ['starting', 'running', 'stopping'].includes(status);

  // Connect to log WebSocket
  useEffect(() => {
    const ws = createWebSocket('logs');

    ws.onmessage = (event) => {
      // Keep bounded/redacted display fields; never persist raw WebSocket
      // credentials, opaque metadata or arbitrary CSS severity names.
      const log = parseTrainingLogEvent(event.data);
      if (log) addLog(log);
    };

    ws.onerror = (err) => {
      console.error('WebSocket error:', err);
    };

    return () => {
      ws.close();
    };
  }, [addLog]);

  // Auto-scroll to bottom
  useEffect(() => {
    logsEndRef.current?.scrollIntoView?.({ behavior: 'smooth' });
  }, [logs]);

  // Start/Stop mutations
  const startMutation = useMutation({
    mutationFn: ({ config, version }) => trainingAPI.start(config, version),
    onSuccess: () => clearLogs(),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ['training-status'] }),
  });

  const stopMutation = useMutation({
    mutationFn: () => trainingAPI.stop(),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ['training-status'] }),
  });
  const startDisabled = status === 'unknown' || isBusy || startMutation.isPending || !validModelVersion;

  const getStatusColor = (status) => {
    switch (status) {
      case 'running': return '#27ae60';
      case 'completed': return '#2980b9';
      case 'idle': return '#95a5a6';
      default: return '#e67e22';
    }
  };

  return (
    <div className="panel training-panel">
      <h2>🤖 Training Lab</h2>
      {statusLoading && <p role="status">Checking authoritative training status…</p>}
      {statusError && (
        <div role="alert" className="error">
          Training status unavailable. Starting is blocked until status is verified.
          <button type="button" className="btn btn-secondary" onClick={() => refetch()}>
            Retry status
          </button>
        </div>
      )}
      {startMutation.isError && (
        <p role="alert" className="error">Training could not start. Verify the selected profile, candidate version, and worker availability before retrying.</p>
      )}
      {stopMutation.isError && (
        <p role="alert" className="error">Training stop could not be confirmed. Refresh status before assuming the process has stopped.</p>
      )}

      <div className="status-section">
        <div className="status-indicator">
          <span>Status:</span>
          <strong style={{ color: getStatusColor(status) }}>
            {status.toUpperCase()}
          </strong>
          {status !== 'unknown' && statusData?.pid && (
            <span className="pid-badge">PID: {statusData.pid}</span>
          )}
        </div>

        <div className="config-selector">
          <label htmlFor="training-config">Configuration:</label>
          <select
            id="training-config"
            value={configName}
            onChange={(e) => setConfigName(e.target.value)}
            disabled={isBusy || startMutation.isPending}
            className="config-select"
            aria-label="Select training configuration file (Local or RTX3050)"
            aria-describedby="config-help"
          >
            <option value="training_local.yaml">training_local.yaml (local/default)</option>
            <option value="training_rtx3050.yaml">training_rtx3050.yaml (4GB VRAM)</option>
          </select>
          <small id="config-help" style={{ display: 'block', marginTop: '5px', color: '#666' }}>
            Select based on your GPU memory capacity
          </small>
        </div>

        <div className="config-selector">
          <label htmlFor="training-model-version">Model registry version:</label>
          <input
            id="training-model-version"
            className="config-select"
            type="text"
            value={modelVersion}
            onChange={(e) => setModelVersion(e.target.value)}
            maxLength={120}
            pattern="[A-Za-z0-9][A-Za-z0-9._-]{0,119}"
            aria-describedby="training-model-version-help"
            aria-invalid={modelVersion.length > 0 && !validModelVersion}
            disabled={isBusy || startMutation.isPending}
            placeholder="e.g. btc-ppo-v2"
            autoComplete="off"
            required
          />
          <small id="training-model-version-help">
            Required registry identifier (letters, digits, periods, underscores,
            hyphens; maximum 120 characters).
          </small>
        </div>

        <div className="control-buttons">
          <button
            onClick={() => startMutation.mutate({
              config: configName,
              version: modelVersion,
            })}
            disabled={startDisabled}
            className={`btn ${startDisabled ? 'btn-disabled' : 'btn-success'}`}
            aria-label={isBusy ? 'Training process is busy' : status === 'unknown' ? 'Training status is unknown' : 'Start training with selected configuration'}
            aria-busy={startMutation.isPending}
          >
            {startMutation.isPending ? '⏳ Starting...' : '▶️ START TRAINING'}
          </button>

          <button
            onClick={() => stopMutation.mutate()}
            disabled={!isBusy || stopMutation.isPending}
            className={`btn ${!isBusy ? 'btn-disabled' : 'btn-danger'}`}
            aria-label={!isBusy ? 'No training running' : status === 'stopping' ? 'Retry stopping the training process' : 'Stop the currently running training process'}
            aria-busy={stopMutation.isPending}
          >
            {stopMutation.isPending ? '⏳ Stopping...' : status === 'stopping' ? 'Retry stop request' : '⏹️ STOP TRAINING'}
          </button>

          <button
            onClick={clearLogs}
            className="btn btn-secondary"
            aria-label="Clear all log entries from the console"
          >
            🗑️ Clear Logs
          </button>
        </div>
      </div>

      <div className="logs-section">
        <div className="logs-header">
          <h3 id="logs-title">Live Console</h3>
          <span className="log-count" aria-live="polite" aria-atomic="true">
            {logs.length} logs
          </span>
        </div>

        <div
          className="logs-console"
          role="log"
          aria-labelledby="logs-title"
          aria-live="polite"
          aria-atomic="false"
        >
          {logs.length === 0 && (
            <div className="logs-empty">
              Waiting for logs... Start training to see output.
            </div>
          )}

          {logs.map((log, i) => (
            <div
              key={i}
              className={`log-entry log-${log.level.toLowerCase()}`}
              role="status"
              aria-label={`${log.level}: ${log.message} from ${log.source}`}
            >
              <span className="log-source">[{log.source}]</span>
              <span className="log-message">{log.message}</span>
            </div>
          ))}

          <div ref={logsEndRef} />
        </div>
      </div>
    </div>
  );
}
