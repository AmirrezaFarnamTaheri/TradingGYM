import { useState, useEffect, useRef } from 'react';
import { useQuery, useMutation } from '@tanstack/react-query';
import { trainingAPI, createWebSocket } from '../api';
import useStore from '../store';

export default function TrainingPanel() {
  const [configName, setConfigName] = useState('training_t4.yaml');
  const logsEndRef = useRef(null);

  const logs = useStore(state => state.logs);
  const addLog = useStore(state => state.addLog);
  const clearLogs = useStore(state => state.clearLogs);

  // Poll training status
  const { data: statusData } = useQuery({
    queryKey: ['training-status'],
    queryFn: () => trainingAPI.getStatus().then(r => r.data),
    refetchInterval: 2000,
  });

  const status = statusData?.status || 'idle';
  const isRunning = status === 'running';

  // Connect to log WebSocket
  useEffect(() => {
    const ws = createWebSocket('logs');

    ws.onopen = () => {
      console.log('WebSocket connected to logs');
    };

    ws.onmessage = (event) => {
      const log = JSON.parse(event.data);
      addLog(log);
    };

    ws.onerror = (err) => {
      console.error('WebSocket error:', err);
    };

    ws.onclose = () => {
      console.log('WebSocket disconnected');
    };

    return () => {
      ws.close();
    };
  }, [addLog]);

  // Auto-scroll to bottom
  useEffect(() => {
    logsEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [logs]);

  // Start/Stop mutations
  const startMutation = useMutation({
    mutationFn: () => trainingAPI.start(configName),
    onSuccess: () => {
      clearLogs();
    },
    onError: (error) => {
      alert(`Failed to start training: ${error.response?.data?.detail || error.message}`);
    }
  });

  const stopMutation = useMutation({
    mutationFn: () => trainingAPI.stop(),
    onError: (error) => {
      alert(`Failed to stop training: ${error.response?.data?.detail || error.message}`);
    }
  });

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

      <div className="status-section">
        <div className="status-indicator">
          <span>Status:</span>
          <strong style={{ color: getStatusColor(status) }}>
            {status.toUpperCase()}
          </strong>
          {statusData?.pid && (
            <span className="pid-badge">PID: {statusData.pid}</span>
          )}
        </div>

        <div className="config-selector">
          <label htmlFor="training-config">Configuration:</label>
          <select
            id="training-config"
            value={configName}
            onChange={(e) => setConfigName(e.target.value)}
            disabled={isRunning}
            className="config-select"
            aria-label="Select training configuration file (T4 or RTX3050)"
            aria-describedby="config-help"
          >
            <option value="training_t4.yaml">training_t4.yaml (16GB VRAM)</option>
            <option value="training_rtx3050.yaml">training_rtx3050.yaml (4GB VRAM)</option>
          </select>
          <small id="config-help" style={{ display: 'block', marginTop: '5px', color: '#666' }}>
            Select based on your GPU memory capacity
          </small>
        </div>

        <div className="control-buttons">
          <button
            onClick={() => startMutation.mutate()}
            disabled={isRunning || startMutation.isPending}
            className={`btn ${isRunning ? 'btn-disabled' : 'btn-success'}`}
            aria-label={isRunning ? 'Training is running' : 'Start training with selected configuration'}
            aria-busy={startMutation.isPending}
          >
            {startMutation.isPending ? '⏳ Starting...' : '▶️ START TRAINING'}
          </button>

          <button
            onClick={() => stopMutation.mutate()}
            disabled={!isRunning || stopMutation.isPending}
            className={`btn ${!isRunning ? 'btn-disabled' : 'btn-danger'}`}
            aria-label={!isRunning ? 'No training running' : 'Stop the currently running training process'}
            aria-busy={stopMutation.isPending}
          >
            {stopMutation.isPending ? '⏳ Stopping...' : '⏹️ STOP TRAINING'}
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
