import { defineConfig, type ConfigEnv } from 'vite'
import react from '@vitejs/plugin-react'
import cesium from 'vite-plugin-cesium'

function resolveBackendPort(command: ConfigEnv['command']): string {
  if (process.env.VITE_BACKEND_PORT) return process.env.VITE_BACKEND_PORT
  if (process.env.BACKEND_PORT) return process.env.BACKEND_PORT
  return command === 'serve' ? '8001' : '8000'
}

// https://vite.dev/config/
export default defineConfig((env) => {
  const backendTarget = `http://127.0.0.1:${resolveBackendPort(env.command)}`
  const buildOutDir = process.env.VITE_BUILD_OUT_DIR || 'dist'
  const backendProxy = {
    '/api': backendTarget,
    '/data': backendTarget,
    '/tiles': backendTarget,
    '/health': backendTarget,
  }

  return {
    plugins: [react(), cesium()],
    build: {
      outDir: buildOutDir,
    },
    server: {
      host: '0.0.0.0',
      port: 5173,
      strictPort: true,
      allowedHosts: ['portal.droidminingsolutions.com', 'cloud.droidminingsolutions.com'],
      proxy: backendProxy,
    },
    preview: {
      host: '0.0.0.0',
      port: 4173,
      strictPort: true,
      allowedHosts: ['portal.droidminingsolutions.com', 'cloud.droidminingsolutions.com'],
      proxy: backendProxy,
    },
  }
})
