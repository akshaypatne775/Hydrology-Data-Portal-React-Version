import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import cesium from 'vite-plugin-cesium'

const backendPort = process.env.VITE_BACKEND_PORT || process.env.BACKEND_PORT || '8000'
const backendTarget = `http://127.0.0.1:${backendPort}`
const buildOutDir = process.env.VITE_BUILD_OUT_DIR || 'dist'
const backendProxy = {
  '/api': backendTarget,
  '/data': backendTarget,
  '/tiles': backendTarget,
  '/health': backendTarget,
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), cesium()],
  build: {
    outDir: buildOutDir,
  },
  server: {
    host: '0.0.0.0',
    port: 5173,
    strictPort: true,
    allowedHosts: ['portal.droidminingsolutions.com'],
    proxy: backendProxy,
  },
  preview: {
    host: '0.0.0.0',
    port: 4173,
    strictPort: true,
    allowedHosts: ['portal.droidminingsolutions.com'],
    proxy: backendProxy,
  },
})
