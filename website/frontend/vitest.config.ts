/// <reference types="vitest/config" />
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// Tests live in test/ rather than src/ on purpose: the build (`tsc -b`), the
// lint (`eslint src`) and the PWA bundle all scope themselves to src, so keeping
// specs out of it means adding a test runner costs those pipelines nothing. Vitest
// transforms the specs with esbuild via the react plugin, so no extra tsconfig is
// needed.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./test/setup.ts'],
    include: ['test/**/*.test.{ts,tsx}'],
  },
})
