import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      '/api': {
        target: process.env.TRADING_GYM_API_TARGET || 'http://localhost:8000',
        changeOrigin: true,
      },
      '/ws': {
        target: (process.env.TRADING_GYM_API_TARGET || 'http://localhost:8000').replace(/^http/, 'ws'),
        ws: true,
      }
    }
  },
  test: {
    environment: 'jsdom',
    setupFiles: './src/setupTests.js',
    globals: true,
    // Node contract tests have their own explicit CI command.
    exclude: ['**/node_modules/**', '**/*.node.test.*'],
    coverage: {
      reporter: ['text', 'lcov'],
    },
  }
})
