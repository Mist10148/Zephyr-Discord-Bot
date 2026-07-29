import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { VitePWA } from 'vite-plugin-pwa'

// navigateFallbackDenylist is load-bearing, not tidiness. Workbox's NavigationRoute
// answers *every* same-origin navigation from the precached index.html, so without
// it a browser hitting /api/v1/auth/login gets the SPA shell and never reaches
// Flask -- and Discord's redirect back to /api/v1/auth/callback is swallowed the
// same way, so signing in silently does nothing once the worker activates.
// Workbox matches the denylist against pathname + search, so these patterns do
// cover the callback's query string.
//
// vite-plugin-pwa merges `workbox` shallowly over its defaults, so navigateFallback
// and cleanupOutdatedCaches survive: offline SPA deep links keep working.
//
// Do NOT add runtimeCaching for /api/v1. There is none today, which is why
// authenticated responses never touch Cache Storage -- and Cache Storage is
// readable by any script on the origin and outlives a sign-out.
export default defineConfig({
  plugins: [react(), tailwindcss(), VitePWA({ registerType: 'autoUpdate', workbox: { navigateFallbackDenylist: [/^\/api\//, /^\/health$/] }, manifest: { name: 'Zephyr Weather', short_name: 'Zephyr', display: 'standalone', scope: '/', start_url: '/', theme_color: '#0b1020', icons: [] } })],
  build: { outDir: '../static', emptyOutDir: true },
  server: { proxy: { '/api': 'http://127.0.0.1:5000' } },
})
