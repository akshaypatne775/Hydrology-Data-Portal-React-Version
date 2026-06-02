import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

/** Production: skip copying `public/ortho_data` (huge); dev server still serves it. Deploy tiles separately if needed. */
const PUBLIC_ROOT_FILES = [
  'favicon.svg',
  'pwa-192x192.png',
  'pwa-512x512.png',
  'icons.svg',
  'shapes.json',
  'dashboard.html',
  'index.html',
]

function copyPublicRootFilesForBuild() {
  return {
    name: 'copy-public-root-no-ortho',
    apply: 'build',
    closeBundle() {
      const srcRoot = path.resolve(__dirname, 'public')
      const destRoot = path.resolve(__dirname, 'dist')
      if (!fs.existsSync(destRoot)) return
      for (const name of PUBLIC_ROOT_FILES) {
        const from = path.join(srcRoot, name)
        if (fs.existsSync(from)) {
          fs.copyFileSync(from, path.join(destRoot, name))
        }
      }
    },
  }
}

// https://vite.dev/config/
export default defineConfig(({ command }) => ({
  // `ortho_data` is only copied in dev; keeps `vite build` fast and avoids Windows dist cleanup issues.
  publicDir: command === 'serve' ? 'public' : false,
  plugins: [
    react(),
    copyPublicRootFilesForBuild(),
    VitePWA({
      registerType: 'autoUpdate',
      includeAssets: ['favicon.svg', 'pwa-192x192.png', 'pwa-512x512.png'],
      manifest: {
        name: 'Data Collect Portal — Field',
        short_name: 'Field Survey',
        description: 'Field survey data collection (works offline after install).',
        theme_color: '#0f5162',
        background_color: '#f0f4f5',
        display: 'standalone',
        orientation: 'portrait-primary',
        scope: '/',
        start_url: '/field',
        icons: [
          {
            src: 'pwa-192x192.png',
            sizes: '192x192',
            type: 'image/png',
          },
          {
            src: 'pwa-512x512.png',
            sizes: '512x512',
            type: 'image/png',
          },
          {
            src: 'pwa-512x512.png',
            sizes: '512x512',
            type: 'image/png',
            purpose: 'maskable',
          },
        ],
      },
      workbox: {
        navigateFallback: '/index.html',
        globPatterns: ['**/*.{js,css,html,ico}'],
        runtimeCaching: [
          {
            urlPattern: /^https:\/\/fonts\.googleapis\.com\/.*/i,
            handler: 'CacheFirst',
            options: {
              cacheName: 'google-fonts-stylesheets',
              expiration: { maxEntries: 10, maxAgeSeconds: 60 * 60 * 24 * 365 },
            },
          },
          {
            urlPattern: /^https:\/\/fonts\.gstatic\.com\/.*/i,
            handler: 'CacheFirst',
            options: {
              cacheName: 'google-fonts-webfonts',
              expiration: { maxEntries: 10, maxAgeSeconds: 60 * 60 * 24 * 365 },
            },
          },
        ],
      },
    }),
  ],
  server: {
    watch: {
      ignored: ['**/public/ortho_data/**'],
    },
  },
}))
