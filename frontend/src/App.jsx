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
 * @see ../package.json (authoritative frontend release version)
 */

import { lazy, Suspense, useState } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import appPackage from '../package.json';
// Shell-critical providers stay eager: they must render before any panel loads.
import ErrorBoundary from './components/ErrorBoundary';
import { ToastProvider } from './components/Toast';
import HelpModal, { useHelp } from './components/HelpModal';
import { CardSkeleton } from './components/LoadingSkeleton';
// Panels are code-split: only the active tab's chunk is downloaded.
const ConfigPanel = lazy(() => import('./components/ConfigPanel'));
const TrainingPanel = lazy(() => import('./components/TrainingPanel'));
const ModelsPanel = lazy(() => import('./components/ModelsPanel'));
const TradingPanel = lazy(() => import('./components/TradingPanel'));
const DataPanel = lazy(() => import('./components/DataPanel'));
const SystemPanel = lazy(() => import('./components/SystemPanel'));
const ConvergencePanel = lazy(() => import('./components/ConvergencePanel'));
const InstitutionalOSPanel = lazy(() => import('./components/InstitutionalOSPanel'));
const ProductionPanel = lazy(() => import('./components/ProductionPanel'));
const AuthzPanel = lazy(() => import('./components/AuthzPanel'));
const PlaygroundPanel = lazy(() => import('./components/PlaygroundPanel'));
const ForgePanel = lazy(() => import('./components/ForgePanel'));
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
      // Network timeouts do not prove that a trade or approval was rejected.
      // Automatic retries can duplicate non-idempotent POST operations.
      retry: false,
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
    key: 'forge',
    label: '🧠 Forge & Memory',
    component: ForgePanel,
    helpTopic: 'general',
    description:
      'Agent memory digests, grammar-bounded strategy development, and the X-tier tool queue (ADR-034)',
  },
  {
    key: 'playground',
    label: '🎛️ Playground',
    component: PlaygroundPanel,
    helpTopic: 'general',
    description:
      'Deterministic strategy simulation under scenario stress, scope-gated (ADR-031)',
  },
  {
    key: 'authz',
    label: '🔑 Grants',
    component: AuthzPanel,
    helpTopic: 'general',
    description:
      'Wildcard capability grants: inspect, check a scope, revoke (ADR-028)',
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

  function handleTabKeyDown(event, currentKey) {
    const currentIndex = tabs.findIndex(tab => tab.key === currentKey);
    const last = tabs.length - 1;
    let nextIndex;
    if (event.key === 'ArrowDown' || event.key === 'ArrowRight') {
      nextIndex = (currentIndex + 1) % tabs.length;
    } else if (event.key === 'ArrowUp' || event.key === 'ArrowLeft') {
      nextIndex = (currentIndex + last) % tabs.length;
    } else if (event.key === 'Home') {
      nextIndex = 0;
    } else if (event.key === 'End') {
      nextIndex = last;
    } else {
      return;
    }
    event.preventDefault();
    const nextKey = tabs[nextIndex].key;
    setActiveTab(nextKey);
    event.currentTarget.parentElement?.querySelector(`#tab-${nextKey}`)?.focus();
  }

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
                id={`tab-${key}`}
                role="tab"
                aria-selected={activeTab === key}
                aria-controls={activeTab === key ? `panel-${key}` : undefined}
                tabIndex={activeTab === key ? 0 : -1}
                onClick={() => setActiveTab(key)}
                onKeyDown={(event) => handleTabKeyDown(event, key)}
                className={`nav-button ${activeTab === key ? 'active' : ''}`}
                title={description}
              >
                {label}
              </button>
            ))}
          </nav>

          <div className="sidebar-footer">
            <div className="footer-info">
              <p className="footer-version">v{appPackage.version}</p>
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
            aria-labelledby={`tab-${currentTab?.key}`}
          >
            <ErrorBoundary>
              <Suspense fallback={<CardSkeleton />}>
                <ActiveComponent openHelp={openHelp} />
              </Suspense>
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
