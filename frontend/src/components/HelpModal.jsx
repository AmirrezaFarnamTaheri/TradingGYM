import { useState } from 'react';

/**
 * Help Modal Component
 *
 * Provides comprehensive help documentation for each panel and feature.
 * Can be triggered from any component via the useHelp hook.
 */
export default function HelpModal({ isOpen, onClose, topic = 'general' }) {
  if (!isOpen) return null;

  const helpContent = {
    general: {
      title: 'Trading Gym GUI Help',
      sections: [
        {
          heading: 'Overview',
          content: 'This GUI provides a complete interface for managing your Trading Gym RL trading system. Navigate between panels using the sidebar.'
        },
        {
          heading: 'Panels',
          content: `
            • Config: View and manage system configuration
            • Data: Download historical data and generate features
            • Training: Train RL agents with live log streaming
            • Models: Manage trained models and run backtests
            • Trading: Run paper/live trading with real-time metrics
          `
        },
        {
          heading: 'Real-Time Features',
          content: `
            • Live Logs: Streams training/trading logs via WebSocket
            • Live Metrics: Updates balance, P&L, positions every 500ms
            • Auto-reconnection: WebSocket automatically reconnects on disconnect
          `
        }
      ]
    },

    config: {
      title: 'Configuration Panel Help',
      sections: [
        {
          heading: 'What is this?',
          content: 'The Configuration panel displays your current system configuration loaded from config/app.yaml.'
        },
        {
          heading: 'Features',
          content: `
            • Search: Filter configuration by keyword
            • Download: Export configuration as JSON
            • Auto-refresh: Updates every 10 seconds
          `
        },
        {
          heading: 'Editing Configuration',
          content: `
            You can edit configuration directly in the GUI:
            1. Click "Edit Configuration" button in the Config panel
            2. Modify values in the editor (JSON format)
            3. Click "Save Changes" to apply

            Note: Some changes may require restarting running processes.
            You can also edit config/app.yaml manually and reload.
          `
        },
        {
          heading: 'Configuration Sections',
          content: `
            • trading: Trading parameters, symbols, timeframes
            • model: RL model architecture and hyperparameters
            • execution: Order execution and risk management
            • data: Data pipeline configuration
            • monitoring: Logging and metrics settings
          `
        }
      ]
    },

    data: {
      title: 'Data Management Help',
      sections: [
        {
          heading: 'Download Historical Data',
          content: `
            Downloads OHLCV candlestick data from the exchange.

            Parameters:
            • Symbol: Cryptocurrency to download (BTC, ETH, etc.)
            • Timeframe: Candlestick interval (1m, 5m, 15m, 1h, 4h, 1d)
            • Start/End Date: Date range for historical data

            The download runs in the background. Check Training panel logs for progress.
          `
        },
        {
          heading: 'Generate Features',
          content: `
            Processes raw OHLCV data to create technical indicators and features for training.

            Features Generated:
            • Technical indicators (RSI, MACD, Bollinger Bands, etc.)
            • Multi-timeframe features
            • Volume profiles
            • Price action patterns

            Prerequisites: Raw data must be downloaded first.
          `
        },
        {
          heading: 'Data Pipeline',
          content: `
            Complete workflow:
            1. Download Raw Data → Get OHLCV candlesticks
            2. Generate Features → Create technical indicators
            3. Train Model → Use features to train RL agent
            4. Trade → Deploy trained model
          `
        },
        {
          heading: 'Quick Actions',
          content: `
            Pre-configured downloads for common use cases:
            • BTC 5m (Full Year): Training data for short-term strategies
            • BTC 1h (6 Months): Medium-term strategy development
            • ETH 5m (6 Months): Alternative asset testing
          `
        }
      ]
    },

    training: {
      title: 'Training Panel Help',
      sections: [
        {
          heading: 'Starting Training',
          content: `
            1. Select a configuration file (training_t4.yaml or training_rtx3050.yaml)
            2. Click START TRAINING
            3. Monitor progress in live console
            4. Training runs in background until stopped or completed
          `
        },
        {
          heading: 'Configuration Files',
          content: `
            • training_t4.yaml: Optimized for Kaggle T4 (16GB VRAM)
              - Full batch sizes (256)
              - Large replay buffer
              - Multi-head attention
              - Best for full training runs

            • training_rtx3050.yaml: Optimized for RTX 3050 (4GB VRAM)
              - Gradient checkpointing
              - Small batches with accumulation
              - CPU-offloaded replay buffer
              - Best for inference/evaluation
          `
        },
        {
          heading: 'Live Console',
          content: `
            • Color-coded log levels (INFO, ERROR, WARNING)
            • Auto-scroll to latest logs
            • Shows process source and PID
            • Stores last 200 log entries
            • Clear Logs button to reset view
          `
        },
        {
          heading: 'Status Indicators',
          content: `
            • RUNNING (Green): Training is active
            • IDLE (Gray): No training process
            • COMPLETED (Blue): Training finished
            • PID: Process ID of training job
          `
        },
        {
          heading: 'Stopping Training',
          content: `
            Click STOP TRAINING to gracefully terminate the training process.
            The model checkpoint will be saved at the current episode.
            Check logs to confirm clean shutdown.
          `
        }
      ]
    },

    models: {
      title: 'Model Registry Help',
      sections: [
        {
          heading: 'Model List',
          content: `
            Shows all trained models found in checkpoints/ directory.

            Columns:
            • Version: Model identifier (episode number or timestamp)
            • Has Model: Whether model file exists
            • Modified: Last modification date
            • Status: Active (currently used) or Inactive
          `
        },
        {
          heading: 'Setting Active Model',
          content: `
            The active model is used for trading and backtesting.

            To set active:
            1. Click on a model row to select it
            2. Click "Set Active" button
            3. Model is now ready for trading

            Only models with "Has Model = Yes" can be set as active.
          `
        },
        {
          heading: 'Running Backtests',
          content: `
            Test model performance on historical data before live trading.

            Steps:
            1. Click "Backtest" button on any model
            2. Enter start date (YYYY-MM-DD)
            3. Enter end date (YYYY-MM-DD)
            4. Backtest runs in background
            5. Check Training panel logs for results

            Results include:
            • Total return
            • Sharpe ratio
            • Max drawdown
            • Win rate
            • Trade statistics
          `
        },
        {
          heading: 'Model Selection',
          content: `
            Best Practices:
            • Use recent models for live trading
            • Backtest before deploying to paper/live
            • Keep multiple checkpoints for rollback
            • Compare performance across different training runs
          `
        }
      ]
    },

    trading: {
      title: 'Trading Control Help',
      sections: [
        {
          heading: 'Trading Modes',
          content: `
            • Paper Trading: Simulates trading with virtual money
              - Uses real market data
              - No real funds at risk
              - Perfect for testing strategies
              - Recommended before live trading

            • Live Trading: Real money on real exchange
              - Uses actual account balance
              - Real profit and loss
              - Requires API keys configured
              - Only use after thorough paper testing
          `
        },
        {
          heading: 'Starting Trading',
          content: `
            1. Select mode (Paper or Live)
            2. Optionally specify model version (leave empty for active model)
            3. Click START TRADING
            4. Monitor live metrics in real-time

            Prerequisites:
            • At least one trained model
            • Model set as active (if not specifying version)
            • API keys configured for live trading
          `
        },
        {
          heading: 'Live Metrics',
          content: `
            Real-time updates every 500ms via WebSocket:

            • Balance: Current account balance (USD)
            • P&L: Profit and Loss (Green = profit, Red = loss)
            • Positions: Number of open positions
            • Margin Ratio: Leverage usage (0.0 - 1.0)

            Metrics are color-coded for quick assessment.
          `
        },
        {
          heading: 'Emergency Kill Switch',
          content: `
            Immediately terminates ALL running processes.

            Use when:
            • Unexpected behavior detected
            • Need to stop all operations immediately
            • System becomes unresponsive
            • Risk management breach

            ⚠️ WARNING: This action cannot be undone.
            All processes (training, trading, backtests) will be killed.

            Requires confirmation before execution.
          `
        },
        {
          heading: 'Risk Management',
          content: `
            Built-in safety features:
            • Circuit breakers monitor losses
            • Automatic position limits
            • Margin ratio enforcement
            • Order validation before submission

            Always start with paper trading to validate your strategy.
          `
        }
      ]
    }
  };

  const content = helpContent[topic] || helpContent.general;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content help-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>❓ {content.title}</h2>
          <button onClick={onClose} className="modal-close">✕</button>
        </div>

        <div className="modal-body">
          {content.sections.map((section, idx) => (
            <div key={idx} className="help-section">
              <h3>{section.heading}</h3>
              <pre className="help-content">{section.content.trim()}</pre>
            </div>
          ))}
        </div>

        <div className="modal-footer">
          <button onClick={onClose} className="btn btn-secondary">
            Close
          </button>
        </div>
      </div>
    </div>
  );
}

/**
 * Hook for using help modal
 * Usage: const { openHelp } = useHelp();
 *        openHelp('config');
 */
export function useHelp() {
  const [isOpen, setIsOpen] = useState(false);
  const [topic, setTopic] = useState('general');

  const openHelp = (newTopic = 'general') => {
    setTopic(newTopic);
    setIsOpen(true);
  };

  const closeHelp = () => setIsOpen(false);

  const HelpButton = ({ topicName, label = '❓ Help' }) => (
    <button
      onClick={() => openHelp(topicName)}
      className="btn btn-help"
      title="Open help documentation"
    >
      {label}
    </button>
  );

  return {
    isOpen,
    topic,
    openHelp,
    closeHelp,
    HelpButton,
    HelpModal: () => <HelpModal isOpen={isOpen} onClose={closeHelp} topic={topic} />
  };
}
