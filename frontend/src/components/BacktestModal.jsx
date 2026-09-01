import { useState } from 'react';

/**
 * BacktestModal Component
 *
 * Provides a proper modal dialog for backtest configuration
 * instead of browser prompt() dialogs
 */
export default function BacktestModal({ modelVersion, onClose, onSubmit, isLoading }) {
  const [startDate, setStartDate] = useState(() => {
    // Default to 6 months ago
    const d = new Date();
    d.setMonth(d.getMonth() - 6);
    return d.toISOString().split('T')[0];
  });

  const [endDate, setEndDate] = useState(() => {
     // Default to today
     return new Date().toISOString().split('T')[0];
  });

  const [error, setError] = useState('');

  const handleSubmit = (e) => {
    e.preventDefault();
    setError('');

    // Validation
    if (!startDate) {
      setError('Start date is required');
      return;
    }
    if (!endDate) {
      setError('End date is required');
      return;
    }

    // Validate date format
    const startPattern = /^\d{4}-\d{2}-\d{2}$/;
    const endPattern = /^\d{4}-\d{2}-\d{2}$/;

    if (!startPattern.test(startDate)) {
      setError('Start date must be in YYYY-MM-DD format');
      return;
    }
    if (!endPattern.test(endDate)) {
      setError('End date must be in YYYY-MM-DD format');
      return;
    }

    // Parse using UTC to avoid timezone issues
    const toUTCDate = (value) => {
      const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
      if (!match) return null;
      const [, year, month, day] = match;
      const timestamp = Date.UTC(Number(year), Number(month) - 1, Number(day));
      return Number.isFinite(timestamp) ? new Date(timestamp) : null;
    };

    const startDateObj = toUTCDate(startDate);
    const endDateObj = toUTCDate(endDate);

    if (!startDateObj || !endDateObj) {
      setError('Invalid date value(s). Please use YYYY-MM-DD.');
      return;
    }

    if (startDateObj.getTime() >= endDateObj.getTime()) {
      setError('Start date must be strictly before end date');
      return;
    }

    const toYMD = (dateObj) => dateObj.toISOString().slice(0, 10);

    // Submit with validated and normalized dates
    onSubmit({ startDate: toYMD(startDateObj), endDate: toYMD(endDateObj) });
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal-content backtest-modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-labelledby="backtest-title"
      >
        <div className="modal-header">
          <h3 id="backtest-title">Run Backtest: {modelVersion}</h3>
          <button
            onClick={onClose}
            className="modal-close"
            aria-label="Close backtest dialog"
            disabled={isLoading}
          >
            ✕
          </button>
        </div>

        <form onSubmit={handleSubmit} className="backtest-form">
          <div className="form-group">
            <label htmlFor="start-date">Start Date:</label>
            <input
              id="start-date"
              type="date"
              value={startDate}
              onChange={(e) => setStartDate(e.target.value)}
              disabled={isLoading}
              className="text-input"
              aria-label="Backtest start date"
              aria-describedby="start-help"
            />
            <small id="start-help" style={{ color: '#666' }}>
              Beginning of backtest period
            </small>
          </div>

          <div className="form-group">
            <label htmlFor="end-date">End Date:</label>
            <input
              id="end-date"
              type="date"
              value={endDate}
              onChange={(e) => setEndDate(e.target.value)}
              disabled={isLoading}
              className="text-input"
              aria-label="Backtest end date"
              aria-describedby="end-help"
            />
            <small id="end-help" style={{ color: '#666' }}>
              End of backtest period
            </small>
          </div>

          {error && (
            <div
              className="error-message"
              role="alert"
              aria-live="polite"
              style={{
                background: '#fce4e4',
                border: '1px solid #f5a5a5',
                padding: '10px',
                borderRadius: '4px',
                color: '#c1121f',
                marginBottom: '15px',
                fontSize: '13px'
              }}
            >
              {error}
            </div>
          )}

          <div className="modal-buttons">
            <button
              type="submit"
              disabled={isLoading}
              className="btn btn-primary"
              aria-label="Submit backtest with selected dates"
              aria-busy={isLoading}
            >
              {isLoading ? '⏳ Running...' : '▶️ Run Backtest'}
            </button>

            <button
              type="button"
              onClick={onClose}
              disabled={isLoading}
              className="btn btn-secondary"
              aria-label="Cancel backtest and close dialog"
            >
              Cancel
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
