import { defineConfig } from 'vite';
import tailwindcss from '@tailwindcss/vite';
export default defineConfig({
  base: '/static/ai/',
  plugins: [tailwindcss()],
  build: { rollupOptions: { external: ['/static/web-ui.js'] }, outDir: '../../fco_meta/web/static/ai', emptyOutDir: true },
  server: { proxy: {
    '/api': 'http://127.0.0.1:8000',
    '^/static/(?!ai/)': 'http://127.0.0.1:8000',
  } },
});
