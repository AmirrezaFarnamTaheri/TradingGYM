/**
 * Trading Gym Application
 *
 * Main application component that provides:
 * - React Query for data fetching
 * - Toast notifications via ToastProvider
 * - Error boundaries for graceful error handling
 * - Tab-based navigation for different panels
 *
 * @module App
 * @version 4.2.0
 */

import { useState } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import ConfigPanel from './components/ConfigPanel';
import TrainingPanel from './components/TrainingPanel';
import ModelsPanel from './components/ModelsPanel';
import TradingPanel from './components/TradingPanel';
import DataPanel from './components/DataPanel';
import SystemPanel from './components/SystemPanel';
import ConvergencePanel from './components/ConvergencePanel';
import InstitutionalOSPanel from './components/InstitutionalOSPanel';
import ProductionPanel from './components/ProductionPanel';
import ErrorBoundary from './components/ErrorBoundary';
import { ToastProvider } from './components/Toast';
import HelpModal, { useHelp } from './components/HelpModal';
import './App.css';

// =============================================================================
// Query Client Configuration
// =============================================================================

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 2,
      retryDelay: (attemptIndex) => Math.min(1000 * 2 ** attemptIndex, 30000),
      refetchOnWindowFocus: false,
      staleTime: 5000, // Consider data stale after 5 seconds
    },
    mutations: {
      retry: 1,
    },
  },
});

// =============================================================================
// Tab Configuration
// =============================================================================

const tabs = [
  {
    key: 'production',
    label: '🛡️ Production',
    component: ProductionPanel,
    helpTopic: 'trading',
    description: 'Live authority, non-custodial wallet posture, and operation-specific controls',
  },
  {
    key: 'institutional',
    label: '🏛️ Institutional OS',
    component: InstitutionalOSPanel,
    helpTopic: 'general',
    description: 'Twenty bounded intelligence, portfolio, execution, agent, data, risk, governance, and lab planes',
  },
  {
    key: 'convergence',
    label: '🧭 Intelligence',
    component: ConvergencePanel,
    helpTopic: 'general',
    description: 'Safe strategy research, provider tools, and human-governed automation',
  },
  {
    key: 'system',
    label: '🖥️ System',
    component: SystemPanel,
    helpTopic: 'general',
    description: 'System health, processes, and API reference',
  },
  {
    key: 'config',
    label: '⚙️ Config',
    component: ConfigPanel,
    helpTopic: 'config',
    description: 'View and edit system configuration',
  },
  {
    key: 'data',
    label: '📊 Data',
    component: DataPanel,
    helpTopic: 'data',
    description: 'Download data and generate features',
  },
  {
    key: 'training',
    label: '🤖 Training',
    component: TrainingPanel,
    helpTopic: 'training',
    description: 'Train RL agents with live monitoring',
  },
  {
    key: 'models',
    label: '📈 Models',
    component: ModelsPanel,
    helpTopic: 'models',
    description: 'Manage trained models and run backtests',
  },
  {
    key: 'trading',
    label: '💱 Trading',
    component: TradingPanel,
    helpTopic: 'trading',
    description: 'Paper and live trading control',
  },
];

// =============================================================================
// Main App Component
// =============================================================================

function AppContent() {
  const [activeTab, setActiveTab] = useState('institutional');
  const { openHelp, HelpModal: HelpModalComponent } = useHelp();

  const currentTab = tabs.find(tab => tab.key === activeTab);
  const ActiveComponent = currentTab?.component || SystemPanel;

  return (
    <>
      <div className="app">
        {/* Sidebar Navigation */}
        <aside className="sidebar" role="navigation" aria-label="Main navigation">
          <div className="sidebar-header">
            <h1 className="app-title">Trading Gym</h1>
            <div className="app-subtitle">Professional RL Trading Platform</div>
          </div>

          <nav className="sidebar-nav" role="tablist" aria-label="Panel navigation">
            {tabs.map(({ key, label, description }) => (
              <button
                key={key}
                role="tab"
                aria-selected={activeTab === key}
                aria-controls={`panel-${key}`}
                onClick={() => setActiveTab(key)}
                className={`nav-button ${activeTab === key ? 'active' : ''}`}
                title={description}
              >
                {label}
              </button>
            ))}
          </nav>

          <div className="sidebar-footer">
            <div className="footer-info">
              <p className="footer-version">v31.7.0+reconstructed.1</p>
              <p className="footer-copyright">© 2026 Trading Gym</p>
              <p className="footer-note" style={{ fontSize: '10px', marginTop: '5px', opacity: 0.7 }}>
                Evidence-qualified; deployment qualification remains environment-specific
              </p>
            </div>
          </div>
        </aside>

        {/* Main Content Area */}
        <main className="main-content" role="main">
          <header className="content-header">
            <div>
              <h2>{currentTab?.label || 'Trading Gym'}</h2>
              {currentTab?.description && (
                <p style={{ fontSize: '14px', color: '#7f8c8d', margin: '5px 0 0 0' }}>
                  {currentTab.description}
                </p>
              )}
            </div>
            {currentTab && (
              <button
                onClick={() => openHelp(currentTab.helpTopic)}
                className="btn btn-help-main"
                title="Get help for this panel"
                aria-label={`Open help for ${currentTab.label}`}
              >
                ❓ Help
              </button>
            )}
          </header>

          <div
            className="content-wrapper"
            role="tabpanel"
            id={`panel-${currentTab?.key}`}
            aria-labelledby={currentTab?.key}
          >
            <ErrorBoundary>
              <ActiveComponent openHelp={openHelp} />
            </ErrorBoundary>
          </div>
        </main>
      </div>

      {/* Help Modal */}
      <HelpModalComponent />
    </>
  );
}

// =============================================================================
// App with Providers
// =============================================================================

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <ErrorBoundary>
          <AppContent />
        </ErrorBoundary>
      </ToastProvider>
    </QueryClientProvider>
  );
}

export default App;
