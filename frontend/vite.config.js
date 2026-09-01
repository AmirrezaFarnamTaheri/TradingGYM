import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/ws': {
        target: 'ws://localhost:8000',
        ws: true,
      }
    }
  },
  test: {
    environment: 'jsdom',
    setupFiles: './src/setupTests.js',
    globals: true,
    // The wallet node test runs under plain `node`, not vitest.
    exclude: ['**/node_modules/**', 'src/wallets/trustWallet.node.test.js'],
    coverage: {
      reporter: ['text', 'lcov'],
    },
  }
})
