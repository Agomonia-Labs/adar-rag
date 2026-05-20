import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],

  server: {
    port: 5173,
    // Local dev: proxy /api/* to local FastAPI
    proxy: {
      '/api': { target: 'http://localhost:8000', changeOrigin: true },
    },
  },

  build: {
    outDir: 'dist',
    emptyOutDir: true,
    // Optimise bundle
    rollupOptions: {
      output: {
        manualChunks: {
          react: ['react', 'react-dom'],
        },
      },
    },
  },
});