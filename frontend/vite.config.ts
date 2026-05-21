import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import cesium from 'vite-plugin-cesium'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), cesium()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    strictPort: true,
    allowedHosts: ['portal.droidminingsolutions.com'],
    proxy: {
      '/api': 'http://localhost:8000',
      '/data': 'http://localhost:8000',
      '/tiles': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
    },
  },
})
