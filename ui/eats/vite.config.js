import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Mirrors adar-rag/frontend/vite.config.js (the DocIntel web app) --
// same dev-proxy-to-local-backend pattern, same build output shape, so
// this app deploys to Firebase Hosting the same way.
export default defineConfig({
  plugins: [react()],

  server: {
    port: 5174,
    proxy: {
      '/api': { target: 'http://localhost:8000', changeOrigin: true },
    },
  },

  build: {
    outDir: 'dist',
    // emptyOutDir left false: this shell can't delete files in the
    // connected folder (no delete permission granted), and Vite's default
    // clean-before-build needs to rm the old dist/ first. Leaving it off
    // means stale content-hashed chunks from earlier builds accumulate in
    // dist/assets/ instead of being removed -- harmless for a static
    // deploy (index.html always points at the current build's hashes),
    // just a little extra storage. Re-enable once delete is allowed.
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
