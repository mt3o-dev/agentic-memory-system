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
})
