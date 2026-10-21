import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Mirrors ui/eats/vite.config.js -- same dev-proxy-to-local-backend
// pattern, same build output shape, so this app deploys to Firebase
// Hosting the same way (a second/third frontend on the same backend).
export default defineConfig({
  plugins: [react()],

  server: {
    port: 5175,
    proxy: {
      '/api': { target: 'http://localhost:8000', changeOrigin: true },
    },
  },

  build: {
    outDir: 'dist',
    // emptyOutDir left false -- see ui/eats/vite.config.js for why
    // (this shell can't delete files in the connected folder until
    // deletion is explicitly granted).
    emptyOutDir: false,
    rollupOptions: {
      output: {
        manualChunks: {
          react: ['react', 'react-dom'],
        },
      },
    },
  },
});
