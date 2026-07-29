import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { VitePWA } from 'vite-plugin-pwa'

export default defineConfig({
  plugins: [react(), tailwindcss(), VitePWA({ registerType: 'autoUpdate', manifest: { name: 'Zephyr Weather', short_name: 'Zephyr', display: 'standalone', start_url: '/', theme_color: '#0b1020', icons: [] } })],
  build: { outDir: '../static', emptyOutDir: true },
  server: { proxy: { '/api': 'http://127.0.0.1:5000' } },
})
