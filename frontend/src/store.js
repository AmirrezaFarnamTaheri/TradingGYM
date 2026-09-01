import { create } from 'zustand';

const useStore = create((set) => ({
  // Active model
  activeModel: null,
  setActiveModel: (model) => set({ activeModel: model }),

  // Training status
  trainingStatus: 'idle',
  setTrainingStatus: (status) => set({ trainingStatus: status }),

  // Trading status
  tradingStatus: 'idle',
  tradingMode: null,
  setTradingStatus: (status, mode) => set({ tradingStatus: status, tradingMode: mode }),

  // Metrics
  metrics: {
    balance: 0,
    pnl: 0,
    total_positions: 0,
    positions: []
  },
  setMetrics: (metrics) => set({ metrics }),

  // Logs
  logs: [],
  addLog: (log) => set((state) => ({
    logs: [...state.logs.slice(-199), log] // Keep last 200 logs
  })),
  clearLogs: () => set({ logs: [] }),
}));

export default useStore;
