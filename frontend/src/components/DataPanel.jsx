import { useState, useEffect } from 'react';
import { useMutation } from '@tanstack/react-query';
import { dataAPI } from '../api';
import { unwrapJobResponse } from '../jobResponse';
import { useToast } from './Toast';

export default function DataPanel() {
  const [symbol, setSymbol] = useState('BTC');
  const [timeframe, setTimeframe] = useState('5m');

  // Dynamic Default Dates:
  // End Date: Today
  // Start Date: 6 months ago
  const getToday = () => new Date().toISOString().split('T')[0];
  const getSixMonthsAgo = () => {
    const d = new Date();
    d.setMonth(d.getMonth() - 6);
    return d.toISOString().split('T')[0];
  };

  const [startDate, setStartDate] = useState(getSixMonthsAgo());
  const [endDate, setEndDate] = useState(getToday());

  const toast = useToast();

  const downloadMutation = useMutation({
    mutationFn: (params) => dataAPI.download(
      params.symbol, params.timeframe, params.startDate, params.endDate
    ).then((response) => unwrapJobResponse(response)),
    onSuccess: (data) => {
      toast.success(`Data download started! Job: ${data.job_key}, PID: ${data.pid}. Check Training panel logs for progress.`);
    },
    onError: (error) => {
      toast.error(`Failed to start download: ${error.response?.data?.detail || error.message}`);
    }
  });

  const featuresMutation = useMutation({
    mutationFn: () => dataAPI.generateFeatures().then((response) => unwrapJobResponse(response, false)),
    onSuccess: (data) => {
      toast.success(`Feature generation started! PID: ${data.pid}. Check Training panel logs for progress.`);
    },
    onError: (error) => {
      toast.error(`Failed to generate features: ${error.response?.data?.detail || error.message}`);
    }
  });

  // Presets hand their own values straight to the mutation.  The mutationFn
  // closes over render-time state, so calling mutate() with no arguments right
  // after setState submits the *previous* selection -- the setTimeout that
  // papered over this was both racy and still stale.
  const runPreset = (preset) => {
    const endDate = getToday();
    const start = new Date();
    if (preset.years) {
      start.setFullYear(start.getFullYear() - preset.years);
    } else {
      start.setMonth(start.getMonth() - (preset.months || 0));
    }
    const startDate = start.toISOString().split('T')[0];

    setSymbol(preset.symbol);
    setTimeframe(preset.timeframe);
    setStartDate(startDate);
    setEndDate(endDate);
    downloadMutation.mutate({
      symbol: preset.symbol, timeframe: preset.timeframe, startDate, endDate,
    });
  };

  return (
    <div className="panel">
      <h2>📊 Data Management</h2>

      {/* Download Data Section */}
      <div className="section">
        <h3>Download Historical Data</h3>

        <div className="form-grid">
          <div className="form-group">
            <label htmlFor="data-symbol">Symbol:</label>
            <select
              id="data-symbol"
              value={symbol}
              onChange={(e) => setSymbol(e.target.value)}
              className="select-input"
              aria-label="Select cryptocurrency symbol for download (BTC, ETH, BNB, ADA, SOL, DOT)"
              aria-describedby="symbol-help"
            >
              <option value="BTC">BTC (Bitcoin)</option>
              <option value="ETH">ETH (Ethereum)</option>
              <option value="BNB">BNB (Binance Coin)</option>
              <option value="ADA">ADA (Cardano)</option>
              <option value="SOL">SOL (Solana)</option>
              <option value="DOT">DOT (Polkadot)</option>
            </select>
            <small id="symbol-help" style={{ color: '#666' }}>Which cryptocurrency to download data for</small>
          </div>

          <div className="form-group">
            <label htmlFor="data-timeframe">Timeframe:</label>
            <select
              id="data-timeframe"
              value={timeframe}
              onChange={(e) => setTimeframe(e.target.value)}
              className="select-input"
              aria-label="Select timeframe for candlestick data (1m, 5m, 15m, 1h, 4h, 1d)"
              aria-describedby="timeframe-help"
            >
              <option value="1m">1 minute</option>
              <option value="5m">5 minutes</option>
              <option value="15m">15 minutes</option>
              <option value="1h">1 hour</option>
              <option value="4h">4 hours</option>
              <option value="1d">1 day</option>
            </select>
            <small id="timeframe-help" style={{ color: '#666' }}>Candle size for OHLCV data</small>
          </div>

          <div className="form-group">
            <label htmlFor="data-start-date">Start Date:</label>
            <input
              id="data-start-date"
              type="date"
              value={startDate}
              onChange={(e) => setStartDate(e.target.value)}
              className="text-input"
              aria-label="Download data starting from this date"
              aria-describedby="start-date-help"
            />
            <small id="start-date-help" style={{ color: '#666' }}>Beginning of data range</small>
          </div>

          <div className="form-group">
            <label htmlFor="data-end-date">End Date:</label>
            <input
              id="data-end-date"
              type="date"
              value={endDate}
              onChange={(e) => setEndDate(e.target.value)}
              className="text-input"
              aria-label="Download data ending on this date"
              aria-describedby="end-date-help"
            />
            <small id="end-date-help" style={{ color: '#666' }}>End of data range</small>
          </div>
        </div>

        <div className="info-box" role="note" aria-label="Download process information">
          <p>
            <strong>Note:</strong> This will download historical OHLCV data from the configured data source.
            The download will run in the background. Check the Training panel for logs.
          </p>
        </div>

        <button
          onClick={() => downloadMutation.mutate({ symbol, timeframe, startDate, endDate })}
          disabled={downloadMutation.isPending}
          className="btn btn-primary btn-large"
          aria-label={`Download data for ${symbol} at ${timeframe} timeframe from ${startDate} to ${endDate}`}
          aria-busy={downloadMutation.isPending}
        >
          {downloadMutation.isPending ? '⏳ Starting Download...' : '📥 Download Data'}
        </button>
      </div>

      {/* Generate Features Section */}
      <div className="section">
        <h3>Generate Features</h3>

        <div className="info-box" role="note" aria-label="Feature generation process information">
          <p>
            <strong>Feature Generation</strong> processes raw OHLCV data and creates technical indicators,
            multi-timeframe features, and other derived data used for training.
          </p>
          <p>
            Make sure you have downloaded raw data first before generating features.
          </p>
        </div>

        <button
          onClick={() => featuresMutation.mutate()}
          disabled={featuresMutation.isPending}
          className="btn btn-success btn-large"
          aria-label="Generate technical indicators and features from downloaded data"
          aria-busy={featuresMutation.isPending}
        >
          {featuresMutation.isPending ? '⏳ Generating...' : '⚙️ Generate Features'}
        </button>
      </div>

      {/* Data Pipeline Info */}
      <div className="section">
        <h3>Data Pipeline</h3>

        <ol className="pipeline-steps" role="list" aria-label="Data processing pipeline steps">
          <li className="pipeline-step" role="listitem">
            <div className="step-number" aria-label="Step 1 of 4">1</div>
            <div className="step-content">
              <h4>Download Raw Data</h4>
              <p>Download OHLCV candlestick data from exchange</p>
            </div>
          </li>

          <li className="pipeline-arrow" role="presentation">↓</li>

          <li className="pipeline-step" role="listitem">
            <div className="step-number" aria-label="Step 2 of 4">2</div>
            <div className="step-content">
              <h4>Generate Features</h4>
              <p>Process data to create technical indicators and features</p>
            </div>
          </li>

          <li className="pipeline-arrow" role="presentation">↓</li>

          <li className="pipeline-step" role="listitem">
            <div className="step-number" aria-label="Step 3 of 4">3</div>
            <div className="step-content">
              <h4>Train Model</h4>
              <p>Use processed features to train RL agent</p>
            </div>
          </li>

          <li className="pipeline-arrow" role="presentation">↓</li>

          <li className="pipeline-step" role="listitem">
            <div className="step-number" aria-label="Step 4 of 4">4</div>
            <div className="step-content">
              <h4>Trade</h4>
              <p>Deploy trained model for paper/live trading</p>
            </div>
          </li>
        </ol>
      </div>

      {/* Quick Actions */}
      <div className="section">
        <h3>Quick Actions</h3>

        <div className="quick-actions" role="group" aria-label="Preset download configurations">
          <button
            onClick={() => runPreset({ symbol: 'BTC', timeframe: '5m', years: 1 })}
            disabled={downloadMutation.isPending}
            className="btn btn-secondary"
            aria-label="Download Bitcoin data at 5 minute intervals for past year"
            aria-busy={downloadMutation.isPending}
          >
            📦 Download BTC 5m (1 Year)
          </button>

          <button
            onClick={() => runPreset({ symbol: 'BTC', timeframe: '1h', months: 6 })}
            disabled={downloadMutation.isPending}
            className="btn btn-secondary"
            aria-label="Download Bitcoin data at 1 hour intervals for 6 months"
            aria-busy={downloadMutation.isPending}
          >
            📦 Download BTC 1h (6 Months)
          </button>

          <button
            onClick={() => runPreset({ symbol: 'ETH', timeframe: '5m', months: 6 })}
            disabled={downloadMutation.isPending}
            className="btn btn-secondary"
            aria-label="Download Ethereum data at 5 minute intervals for 6 months"
            aria-busy={downloadMutation.isPending}
          >
            📦 Download ETH 5m (6 Months)
          </button>
        </div>
      </div>
    </div>
  );
}
