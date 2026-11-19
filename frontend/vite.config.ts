import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'
import { defineConfig } from 'vitest/config'

// PWA (SPEC §10): the service worker precaches the app shell only. /api (cards, previews,
// the WebSocket) always goes to the network: it is live session state on the daemon.
const pwa = VitePWA({
  registerType: 'autoUpdate',
  injectRegister: 'auto',
  includeAssets: ['favicon.svg', 'icon.svg', 'apple-touch-icon.png'],
  manifest: {
    name: 'SwipeSort',
    short_name: 'SwipeSort',
    description: 'Fling each file to where it belongs.',
    start_url: '/',
    scope: '/',
    display: 'standalone',
    orientation: 'portrait',
    background_color: '#fafaf9',
    theme_color: '#059669',
    icons: [
      { src: 'icon-192.png', sizes: '192x192', type: 'image/png' },
      { src: 'icon-512.png', sizes: '512x512', type: 'image/png' },
      { src: 'icon-maskable-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
    ],
  },
  workbox: {
    globPatterns: ['**/*.{js,css,html,svg,png,webmanifest}'],
    navigateFallback: '/index.html',
    navigateFallbackDenylist: [/^\/api(\/|$)/],
    runtimeCaching: [],
    cleanupOutdatedCaches: true,
  },
})

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss(), ...(process.env.VITEST ? [] : [pwa])],
  server: {
    proxy: {
      // xfwd: the daemon sees the real client address (a phone via `vite --host` is not loopback).
      '/api': { target: 'http://127.0.0.1:8765', changeOrigin: true, ws: true, xfwd: true },
    },
  },
  build: { outDir: 'dist', emptyOutDir: true },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    css: false,
  },
})
