/**
 * Trading Gym API Client
 *
 * This module provides a comprehensive API client for communicating with the Trading Gym backend.
 * All endpoints are protected by API key authentication (X-API-Key header).
 *
 * @module api
 * @see /api/v1 (versioned route prefix)
 */

import axios from 'axios';

// =============================================================================
// Configuration
// =============================================================================

const API_BASE_URL = import.meta.env.VITE_API_URL || '/api/v1';
const DEFAULT_WS_SCHEME = window.location.protocol === 'https:' ? 'wss' : 'ws';
const WS_BASE_URL = import.meta.env.VITE_WS_URL || `${DEFAULT_WS_SCHEME}://${window.location.host}`;
const API_TIMEOUT = 30000; // 30 seconds

// =============================================================================
// Axios Instance
// =============================================================================

/**
 * Configured axios instance for API communication
 * @type {import('axios').AxiosInstance}
 */
const api = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
  timeout: API_TIMEOUT,
});

export const exampleRunsAPI = {
  list: () => api.get('/runs/example'),
  start: (idempotencyKey) => api.post('/runs/example', { idempotency_key: idempotencyKey }),
  get: (runId) => api.get(`/runs/example/${encodeURIComponent(runId)}`),
  artifact: (runId) => api.get(`/runs/example/${encodeURIComponent(runId)}/artifact`),
  cancel: (runId) => api.post(`/runs/example/${encodeURIComponent(runId)}/cancel`),
};

// =============================================================================
// Interceptors
// =============================================================================

/**
 * Request interceptor to inject the session-only API key.
 */
// Session API key: held in memory only (ADR-031 / security review M-4).
// Nothing is written to localStorage — any legacy value is purged on load —
// so an XSS cannot exfiltrate a persisted gateway credential; a page reload
// requires re-entering the key.
let sessionApiKey = '';
try {
  localStorage.removeItem('trading_gym_api_key');
} catch {
  // storage unavailable (private mode): session key still works
}

api.interceptors.request.use(
  (config) => {
    const apiKey = sessionApiKey;

    if (apiKey) {
      config.headers['X-API-Key'] = apiKey;
    }

    // Do not log request bodies or headers: they may contain approval keys,
    // live-execution consent or other authority-bearing secrets.
    if (import.meta.env.DEV) {
      console.debug(`[API] ${config.method?.toUpperCase()} ${config.url}`);
    }

    return config;
  },
  (error) => Promise.reject(error)
);

/**
 * Response interceptor for global error handling and response unwrapping
 */
api.interceptors.response.use(
  (response) => {
    // Unwrap data from response
    return response;
  },
  (error) => {
    const status = error.response?.status;
    const detail = error.response?.data?.detail || error.message || 'Unknown API Error';

    // Only expire the credential actually rejected. A late response from a
    // previous session must not disconnect a newly authenticated session, and
    // an approval/actor permission failure is not a rejected gateway key.
    const requestHeaders = error.config?.headers;
    const attemptedKey = requestHeaders?.get?.('X-API-Key') || requestHeaders?.['X-API-Key'];
    const gatewayRejected = status === 401 || (status === 403 && detail === 'Invalid API key');
    if (gatewayRejected && sessionApiKey && attemptedKey === sessionApiKey) {
      clearApiKey();
      window.dispatchEvent(new Event('trading-gym:session-expired'));
    }

    // Handle specific error codes
    if (status === 401) {
      console.error('[API] Authentication failed. Please check your API key.');
    } else if (status === 403) {
      console.error('[API] Access forbidden:', detail);
    } else if (status === 404) {
      console.warn('[API] Resource not found:', error.config?.url);
    } else if (status === 409) {
      console.warn('[API] Conflict:', detail);
    } else if (status === 503) {
      console.error('[API] Service unavailable:', detail);
    } else if (status >= 500) {
      console.error('[API] Server error:', detail);
    }

    // Create enhanced error with additional context
    const enhancedError = new Error(detail);
    enhancedError.status = status;
    enhancedError.response = error.response;
    enhancedError.originalError = error;

    return Promise.reject(enhancedError);
  }
);

// =============================================================================
// Health & System APIs
// =============================================================================

/**
 * System health and status endpoints
 */
export const systemAPI = {
  /**
   * Check if the API is healthy (liveness probe)
   * @returns {Promise<{status: string, timestamp: number}>}
   */
  health: () => api.get('/health'),

  /**
   * Check if the API is ready to receive traffic (readiness probe)
   * @returns {Promise<{status: string, timestamp: number}>}
   */
  ready: () => api.get('/ready'),

  /**
   * Get overall system status including running processes
   * @returns {Promise<{processes: Object, timestamp: number}>}
   */
  getStatus: () => api.get('/status'),

  /**
   * Manually trigger zombie process pruning
   * @returns {Promise<{status: string, message: string}>}
   */
  prune: () => api.post('/system/prune'),

  /**
   * Get current metrics from memory-mapped file
   * @returns {Promise<Object>}
   */
  getMetrics: () => api.get('/metrics'),
};

// =============================================================================
// Configuration APIs
// =============================================================================

/**
 * Configuration management endpoints
 */
export const configAPI = {
  /**
   * Get the entire configuration object
   * @returns {Promise<Object>}
   */
  getConfig: () => api.get('/config'),

  /**
   * Update configuration with a partial update (deep merge)
   * @param {Object} updates - Configuration updates to apply
   * @returns {Promise<{status: string, config: Object}>}
   */
  updateConfig: (updates) => api.post('/config', updates),

  /**
   * Get a specific section of the configuration
   * @param {string} section - Section name (e.g., 'env', 'training', 'data')
   * @returns {Promise<Object>}
   */
  getSection: (section) => api.get(`/config/${section}`),
};

// =============================================================================
// Training APIs
// =============================================================================

/**
 * Training control endpoints
 */
export const trainingAPI = {
  /**
   * Start a training run with specified configuration
   * @param {string} configName - An existing training YAML profile
   * @param {string} modelVersion - Required safe registry identifier
   * @returns {Promise<{status: string, pid: number, config: string, model_version: string}>}
   */
  start: (configName, modelVersion) =>
    api.post('/train/start', {
      config_name: configName,
      model_version: modelVersion,
    }),

  /**
   * Stop the currently running training process gracefully
   * @returns {Promise<{status: string}>}
   */
  stop: () => api.post('/train/stop'),

  /**
   * Get the current training status
   * @returns {Promise<{status: string, pid?: number, config?: string, start_time?: number}>}
   */
  getStatus: () => api.get('/train/status'),
};

// =============================================================================
// Data Management APIs
// =============================================================================

/**
 * Data download and feature generation endpoints
 */
export const dataAPI = {
  /**
   * Download historical OHLCV data from the exchange
   * @param {string} symbol - Trading pair symbol (e.g., 'BTC')
   * @param {string} timeframe - Candle timeframe (e.g., '5m', '1h')
   * @param {string} startDate - Start date in YYYY-MM-DD format
   * @param {string} endDate - End date in YYYY-MM-DD format
   * @returns {Promise<{status: string, symbol: string, timeframe: string, job_key: string, pid: number}>}
   */
  download: (symbol, timeframe, startDate, endDate) =>
    api.post('/data/download', {
      symbol,
      timeframe,
      start_date: startDate,
      end_date: endDate,
    }),

  /**
   * Generate features from raw OHLCV data
   * @returns {Promise<{status: string, pid: number}>}
   */
  generateFeatures: () => api.post('/data/generate-features'),
};

// =============================================================================
// Model Management APIs
// =============================================================================

/**
 * Model registry and management endpoints
 */
export const modelsAPI = {
  /**
   * List all trained models in the checkpoints directory
   * @returns {Promise<{models: Array<{version: string, path: string, timestamp: number, has_model: boolean}>}>}
   */
  list: () => api.get('/models'),

  /**
   * Set a model as the active model for trading
   * @param {string} version - Model version/directory name
   * @returns {Promise<{status: string, version: string}>}
   */
  setActive: (version, authorisation) => {
    const query = new URLSearchParams({
      version,
      proposal_id: authorisation.proposalId,
      expected_revision: String(authorisation.expectedRevision),
    });
    return api.post(`/models/set-active?${query.toString()}`, null, {
      headers: {
        'X-Actor': authorisation.actor,
        'X-Actor-Key': authorisation.actorKey,
        'X-Approval-Key': authorisation.approvalKey,
      },
    });
  },

  /**
   * Get the currently active model
   * @returns {Promise<{active_model: string|null}>}
   */
  getActive: () => api.get('/models/active'),

  /**
   * Run a backtest for a specific model
   * @param {string} modelVersion - Model version to backtest
   * @param {string} startDate - Backtest start date (YYYY-MM-DD)
   * @param {string} endDate - Backtest end date (YYYY-MM-DD)
   * @returns {Promise<{status: string, job_key: string, pid: number}>}
   */
  runBacktest: (modelVersion, startDate, endDate) =>
    api.post('/simulate/backtest', {
      model_version: modelVersion,
      start_date: startDate,
      end_date: endDate,
    }),
};

// =============================================================================
// Production Authority (read-only)
// =============================================================================

/**
 * Show independently persisted authority and emergency-stop evidence.
 * This client intentionally exposes no production authorization mutation.
 * Changes to funded authority remain four-eyes and server-governed.
 */
export const productionAPI = {
  getAuthority: () => api.get('/production/authority'),
  getEmergencyState: () => api.get('/production/emergency'),
  getReconciliation: () => api.get('/production/reconciliation'),
};

// =============================================================================
// Trading APIs
// =============================================================================

/**
 * Trading control and emergency endpoints
 */
export const tradingAPI = {
  /**
   * Start paper or live trading
   * @param {'paper'|'live'} mode - Trading mode
   * @param {string|null} modelVersion - Model version to use (null for active model)
   * @returns {Promise<{status: string, mode: string, pid: number}>}
   */
  start: (mode = 'paper', modelVersion = null, authorisation = {}) =>
    api.post('/trading/start', {
      mode,
      model_version: modelVersion,
      ...(mode === 'live' ? {
        proposal_id: authorisation.proposalId,
        expected_revision: authorisation.expectedRevision,
      } : {}),
    }, mode === 'live' ? {
      headers: {
        'X-Actor': authorisation.actor || 'gui-operator',
        'X-Actor-Key': authorisation.actorKey || '',
        'X-Approval-Key': authorisation.approvalKey || '',
      },
    } : undefined),

  /**
   * Stop trading gracefully
   * @returns {Promise<{status: string}>}
   */
  stop: () => api.post('/trading/stop'),

  /**
   * Get trading status
   * @returns {Promise<{status: string, mode?: string, pid?: number, start_time?: number}>}
   */
  getStatus: () => api.get('/trading/status'),

  /**
   * 🚨 PANIC: Immediately flatten all positions and halt trading
   * @returns {Promise<{status: string, message: string, timestamp: number}>}
   */
  panic: (actor = 'gui-operator') => api.post('/trading/panic', null, {
    headers: { 'X-Actor': actor },
  }),

  /**
   * 🚨 EMERGENCY: Kill all processes (requires confirmation)
   * @param {string} confirmation - Must be 'CONFIRM_KILL'
   * @returns {Promise<{status: string, timestamp: number}>}
   */
  emergencyKillAll: (confirmation = 'CONFIRM_KILL', emergencyKey = '', actor = 'gui-operator') =>
    api.post('/emergency/kill-all', { confirmation }, {
      headers: {
        'X-Actor': actor,
        'X-Emergency-Key': emergencyKey,
      },
    }),
};

// =============================================================================
// WebSocket Factory
// =============================================================================

/**
 * Create a WebSocket connection to the specified endpoint
 * @param {'logs'|'metrics'} endpoint - WebSocket endpoint name
 * @returns {WebSocket}
 */
export function createWebSocket(endpoint) {
  const url = `${WS_BASE_URL}/ws/${endpoint}`;
  const apiKey = getApiKey() || '';
  const protocols = ['trading-gym.v1'];
  if (apiKey) {
    const bytes = new TextEncoder().encode(apiKey);
    let binary = '';
    bytes.forEach((byte) => { binary += String.fromCharCode(byte); });
    const token = btoa(binary)
      .replace(/\+/g, '-')
      .replace(/\//g, '_')
      .replace(/=+$/g, '');
    protocols.push(`auth.${token}`);
  }

  if (import.meta.env.DEV) {
    console.debug(`[WS] Connecting to ${url}`);
  }

  const ws = new WebSocket(url, protocols);

  ws.addEventListener('open', () => {
    console.log(`[WS] Connected to ${endpoint}`);
  });

  ws.addEventListener('close', (event) => {
    console.log(`[WS] Disconnected from ${endpoint}`, event.code, event.reason);
  });

  ws.addEventListener('error', (error) => {
    console.error(`[WS] Error on ${endpoint}:`, error);
  });

  return ws;
}

/**
 * Create a reconnecting WebSocket connection with exponential backoff
 * @param {'logs'|'metrics'} endpoint - WebSocket endpoint name
 * @param {Object} options - Configuration options
 * @param {number} options.maxRetries - Maximum reconnection attempts (default: 10)
 * @param {number} options.baseDelay - Base delay in ms (default: 1000)
 * @param {number} options.maxDelay - Maximum delay in ms (default: 30000)
 * @param {function} options.onMessage - Message handler callback
 * @param {function} options.onConnect - Connection established callback
 * @param {function} options.onDisconnect - Disconnection callback
 * @returns {{connect: function, disconnect: function, send: function}}
 */
export function createReconnectingWebSocket(endpoint, options = {}) {
  const {
    maxRetries = 10,
    baseDelay = 1000,
    maxDelay = 30000,
    onMessage = () => {},
    onConnect = () => {},
    onDisconnect = () => {},
  } = options;

  let ws = null;
  let retryCount = 0;
  let reconnectTimeout = null;
  let isManualClose = false;

  const connect = () => {
    if (ws?.readyState === WebSocket.OPEN || ws?.readyState === WebSocket.CONNECTING) {
      return;
    }

    isManualClose = false;
    ws = createWebSocket(endpoint);

    ws.addEventListener('open', () => {
      retryCount = 0;
      onConnect();
    });

    ws.addEventListener('message', (event) => {
      try {
        const data = JSON.parse(event.data);
        onMessage(data);
      } catch (e) {
        console.warn('[WS] Failed to parse message:', e);
      }
    });

    ws.addEventListener('close', () => {
      onDisconnect();

      if (!isManualClose && retryCount < maxRetries) {
        const delay = Math.min(baseDelay * Math.pow(2, retryCount), maxDelay);
        retryCount++;
        console.log(`[WS] Reconnecting in ${delay}ms (attempt ${retryCount}/${maxRetries})`);
        reconnectTimeout = setTimeout(connect, delay);
      }
    });
  };

  const disconnect = () => {
    isManualClose = true;
    if (reconnectTimeout) {
      clearTimeout(reconnectTimeout);
      reconnectTimeout = null;
    }
    if (ws) {
      ws.close();
      ws = null;
    }
  };

  const send = (data) => {
    if (ws?.readyState === WebSocket.OPEN) {
      ws.send(typeof data === 'string' ? data : JSON.stringify(data));
    }
  };

  return { connect, disconnect, send };
}

// =============================================================================
// Utility Functions
// =============================================================================

/**
 * Set the API key for this session (memory only, never persisted)
 * @param {string} apiKey - The API key to hold for the current session
 */
export function setApiKey(apiKey) {
  sessionApiKey = apiKey || '';
}

/**
 * Get the currently held session API key
 * @returns {string|null}
 */
export function getApiKey() {
  return sessionApiKey || null;
}

/**
 * Remove the session API key
 */
export function clearApiKey() {
  sessionApiKey = '';
}

/**
 * Check if an API key is configured
 * @returns {boolean}
 */
export function hasApiKey() {
  return Boolean(getApiKey());
}

// =============================================================================
// Default Export
// =============================================================================

export { api };
export default api;

// =============================================================================
// AXIOM-1: Wildcard Grants (authz) and Playground APIs
// =============================================================================

function actorHeaders(actor, actorKey) {
  return { 'X-Actor': actor, 'X-Actor-Key': actorKey };
}

/**
 * Wildcard capability grant endpoints (ADR-028)
 */
export const authzAPI = {
  check: (scope, principal) =>
    api.post('/authz/check', { scope, ...(principal ? { principal } : {}) }),
  listGrants: () => api.get('/authz/grants'),
  health: () => api.get('/authz/health'),
  issue: (payload, actor, actorKey) =>
    api.post('/authz/grants', payload, {
      headers: actorHeaders(actor, actorKey),
    }),
  revoke: (grantId, actor, actorKey) =>
    api.post('/authz/grants/' + grantId + '/revoke', null, {
      headers: actorHeaders(actor, actorKey),
    }),
};

/**
 * Governed playground endpoints (ADR-031)
 */
export const playgroundAPI = {
  run: (payload, actor, actorKey) =>
    api.post('/playground/runs', payload, {
      headers: actorHeaders(actor, actorKey),
      timeout: 120000,
    }),
};

/**
 * Agent memory, strategy grammar, and tool nominations (ADR-034).
 *
 * Every call is separately scope-gated server-side (`forge.memory.read`,
 * `forge.memory.write`, `forge.strategy.read`, `forge.strategy.propose`,
 * `forge.nomination.read`, `forge.nomination.create`); resolving a
 * nomination additionally requires the operator role. Nothing here applies
 * a revision or reaches a venue.
 */
export const forgeAPI = {
  kernel: () => api.get('/forge/kernel'),
  memoryAdvice: (tags, actor, actorKey) =>
    api.get('/forge/memory/advice', {
      params: { tags },
      paramsSerializer: {
        // FastAPI reads repeated `tags=` query parameters, not `tags[]=`
        indexes: null,
      },
      headers: actorHeaders(actor, actorKey),
    }),
  recordExperience: (payload, actor, actorKey) =>
    api.post('/forge/memory/records', payload, {
      headers: actorHeaders(actor, actorKey),
    }),
  templates: (actor, actorKey, detail = false) =>
    api.get('/forge/strategies/templates', {
      params: { detail },
      headers: actorHeaders(actor, actorKey),
    }),
  strategyVariants: (payload, actor, actorKey) =>
    api.post('/forge/strategies/variants', payload, {
      headers: actorHeaders(actor, actorKey),
    }),
  nominations: (actor, actorKey) =>
    api.get('/forge/nominations', {
      headers: actorHeaders(actor, actorKey),
    }),
  nominate: (payload, actor, actorKey) =>
    api.post('/forge/nominations', payload, {
      headers: actorHeaders(actor, actorKey),
    }),
  resolveNomination: (toolName, payload, actor, actorKey) =>
    api.post(
      '/forge/nominations/' + encodeURIComponent(toolName) + '/resolve',
      payload,
      { headers: actorHeaders(actor, actorKey) }
    ),
};

// =============================================================================
// Converged Strategy, Research, Governance, and Agent APIs
// =============================================================================

export const convergenceAPI = {
  capabilities: () => api.get('/convergence/capabilities'),
  templates: () => api.get('/convergence/strategies/templates'),
  strategies: () => api.get('/convergence/strategies'),
  validateStrategy: (payload) => api.post('/convergence/strategies/validate', payload),
  saveStrategy: (payload) => api.post('/convergence/strategies', payload),
  instruments: (activeOnly = false) => api.get(`/convergence/market/instruments?active_only=${activeOnly}`),
  resolveInstrument: (identifier, venue = '') => api.get(
    `/convergence/market/instruments/resolve/${encodeURIComponent(identifier)}${venue ? `?venue=${encodeURIComponent(venue)}` : ''}`
  ),
  datasets: () => api.get('/convergence/market/datasets'),
  backtestPolicy: (payload) => api.post('/convergence/market/backtest-policy', payload),
  planBacktest: (payload, actor = 'gui-operator') => api.post(
    '/convergence/workflows/backtest', payload, { headers: { 'X-Actor': actor } }
  ),
  sizePosition: (payload) => api.post('/convergence/risk/size', payload),
  assessPortfolio: (snapshot, limits) => api.post('/convergence/risk/portfolio', { snapshot, limits }),
  researchSources: () => api.get('/convergence/research/sources'),
  createDossier: (payload) => api.post('/convergence/research/dossier', payload),
  assessResearch: (payload) => api.post('/convergence/research/quality', payload),
  reflect: (payload) => api.post('/convergence/research/reflect', payload),
  workspace: () => api.get('/convergence/research/workspace'),
  providers: () => api.get('/convergence/providers'),
  saveProvider: (config, expectedRevision = null) => api.post('/convergence/providers', {
    config,
    expected_revision: expectedRevision,
  }),
  providerStatus: (providerId) => api.get(`/convergence/providers/${encodeURIComponent(providerId)}/status`),
  experiments: (state = '') => api.get(`/convergence/experiments${state ? `?state=${encodeURIComponent(state)}` : ''}`),
  auditTemporalLeakage: (payload) => api.post('/convergence/experiments/temporal-leakage/audit', payload),
  accountingAccounts: () => api.get('/convergence/accounting/accounts'),
  planCorporateAction: (payload) => api.post('/convergence/accounting/corporate-actions/plan', payload),
  accountingAccount: (accountId) => api.get(`/convergence/accounting/accounts/${encodeURIComponent(accountId)}`),
  verifyAccounting: (accountId) => api.get(`/convergence/accounting/accounts/${encodeURIComponent(accountId)}/verify`),
  executionScenarios: (payload) => api.post('/convergence/execution/scenarios', payload),
  runManifests: (experimentId) => api.get(`/convergence/reproducibility/experiments/${encodeURIComponent(experimentId)}/manifests`),
  driftBaselines: () => api.get('/convergence/monitoring/drift/baselines'),
  snapshots: () => api.get('/convergence/operations/snapshots'),
  verifySnapshot: (snapshotId) => api.get(`/convergence/operations/snapshots/${encodeURIComponent(snapshotId)}/verify`),
  supplyChain: () => api.get('/convergence/operations/supply-chain'),
  evaluateClock: (payload) => api.post('/convergence/operations/clock/evaluate', payload),
  evaluateRecoveryObjectives: (payload) => api.post('/convergence/operations/recovery-objectives/evaluate', payload),
  qualifyCandidate: (payload) => api.post('/convergence/assurance/qualify', payload),
  tools: () => api.get('/convergence/agent/tools'),
  invokeTool: (payload) => api.post('/convergence/agent/tools/invoke', payload),
  triggers: (state = '') => api.get(`/convergence/governance/triggers${state ? `?state=${encodeURIComponent(state)}` : ''}`),
  proposals: (state = '') => api.get(`/convergence/governance/proposals${state ? `?state=${encodeURIComponent(state)}` : ''}`),
  createProposal: (payload, actor = 'gui-operator', actorKey = '') => api.post(
    '/convergence/governance/proposals',
    payload,
    { headers: { 'X-Actor': actor, ...(actorKey ? { 'X-Actor-Key': actorKey } : {}) } }
  ),
  decideProposal: (proposalId, payload, approvalKey = '', actorKey = '') => api.post(
    `/convergence/governance/proposals/${encodeURIComponent(proposalId)}/decision`,
    payload,
    { headers: {
      ...(approvalKey ? { 'X-Approval-Key': approvalKey } : {}),
      ...(actorKey ? { 'X-Actor-Key': actorKey } : {}),
    } }
  ),
  identityHealth: () => api.get('/convergence/governance/identity/health'),
  deploymentHealth: () => api.get('/convergence/governance/deployment/health'),
  evaluateDataLifecycle: (payload) => api.post('/convergence/governance/data-lifecycle/evaluate', payload),
  evaluateCredentialRotation: (payload) => api.post('/convergence/governance/credentials/rotation/evaluate', payload),
  verifyAudit: () => api.get('/convergence/governance/audit/verify'),
  reconcileAudit: () => api.post('/convergence/governance/audit/reconcile'),
};

// =============================================================================
// Institutional Trading Intelligence OS APIs (v11)
// =============================================================================

/**
 * Institutional analysis and final donor-absorption assurance planes.
 * These endpoints never submit orders, move funds, promote models, deploy
 * changes, install marketplace packages, or execute chaos experiments.
 */
export const institutionalAPI = {
  evaluate: (path, payload) => api.post(`/institutional/${path}`, payload),
  artifacts: (domain = '', limit = 100) => api.get(
    `/institutional/artifacts?limit=${encodeURIComponent(limit)}${domain ? `&domain=${encodeURIComponent(domain)}` : ''}`
  ),
  artifact: (artifactId, revision = null) => api.get(
    `/institutional/artifacts/${encodeURIComponent(artifactId)}${revision ? `?revision=${encodeURIComponent(revision)}` : ''}`
  ),
  saveArtifact: (payload) => api.post('/institutional/artifacts', payload),
  reconcileArtifactAudit: () => api.post('/institutional/artifacts/audit/reconcile'),
  sourceMining: (payload) => api.post('/institutional/source-mining/evaluate', payload),
  marketRegime: (payload) => api.post('/institutional/market-intelligence/regime', payload),
  alphaFactory: (payload) => api.post('/institutional/alpha-factory/evaluate', payload),
  signalGraph: (payload) => api.post('/institutional/signal-graph/analyze', payload),
  portfolioConstruction: (payload) => api.post('/institutional/portfolio/construct', payload),
  portfolioTwin: (payload) => api.post('/institutional/portfolio/digital-twin', payload),
  microstructure: (payload) => api.post('/institutional/execution/microstructure', payload),
  routing: (payload) => api.post('/institutional/execution/routing', payload),
  executionQuality: (payload) => api.post('/institutional/execution/quality', payload),
  workforce: (payload) => api.post('/institutional/agents/workforce', payload),
  memory: (payload) => api.post('/institutional/agents/memory/query', payload),
  modelLifecycle: (payload) => api.post('/institutional/models/lifecycle', payload),
  explainability: (payload) => api.post('/institutional/models/explain', payload),
  causalResearch: (payload) => api.post('/institutional/research/causal', payload),
  dataPlatform: (payload) => api.post('/institutional/data-platform/evaluate', payload),
  enterpriseRisk: (payload) => api.post('/institutional/risk/enterprise', payload),
  policyCompliance: (payload) => api.post('/institutional/governance/policy-compliance', payload),
  distributedAssurance: (payload) => api.post('/institutional/distributed/assure', payload),
  productReview: (payload) => api.post('/institutional/product/evaluate', payload),
  scientificLab: (payload) => api.post('/institutional/scientific-lab/evaluate', payload),
  completeConvergence: (payload) => api.post('/institutional/assurance/complete-convergence', payload),
  behaviouralBenchmarks: (payload) => api.post('/institutional/absorption/behavioural-benchmarks', payload),
  featureStore: (payload) => api.post('/institutional/absorption/feature-store', payload),
  marketReplay: (payload) => api.post('/institutional/absorption/market-replay', payload),
  financingInventory: (payload) => api.post('/institutional/absorption/financing-inventory', payload),
  performanceAttribution: (payload) => api.post('/institutional/absorption/performance-attribution', payload),
  knowledgeImpact: (payload) => api.post('/institutional/absorption/knowledge-impact', payload),
  workflowAssurance: (payload) => api.post('/institutional/absorption/workflow-assurance', payload),
  dynamicMutationCoverage: (payload) => api.post('/institutional/absorption/dynamic-mutation-coverage', payload),
  absorptionSaturation: (payload) => api.post('/institutional/absorption/saturation', payload),
};
