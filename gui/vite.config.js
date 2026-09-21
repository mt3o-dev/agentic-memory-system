import { defineConfig } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'

export default defineConfig({
  plugins: [svelte()],
  base: './',
  server: {
    proxy: {
      // dev server proxies API calls to the Python GUI server
      '/api': 'http://127.0.0.1:8765',
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    include: ['src/**/*.test.js'],
    // The Svelte compiler has a browser build and a server build; component tests need
    // the browser one or lifecycle and effects never run.
    alias: process.env.VITEST ? { 'svelte/internal/server': 'svelte/internal/client' } : {},
  },
  resolve: {
    conditions: process.env.VITEST ? ['browser'] : undefined,
  },
})
