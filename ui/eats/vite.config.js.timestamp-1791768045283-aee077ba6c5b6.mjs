// vite.config.js
import { defineConfig } from "file:///sessions/rcw-01nozdqhqwro86gbzrbxqnto/mnt/project/adar-rag/ui/eats/node_modules/vite/dist/node/index.js";
import react from "file:///sessions/rcw-01nozdqhqwro86gbzrbxqnto/mnt/project/adar-rag/ui/eats/node_modules/@vitejs/plugin-react/dist/index.js";
var vite_config_default = defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: true }
    }
  },
  build: {
    outDir: "dist",
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
          react: ["react", "react-dom"]
        }
      }
    }
  }
});
export {
  vite_config_default as default
};
//# sourceMappingURL=data:application/json;base64,ewogICJ2ZXJzaW9uIjogMywKICAic291cmNlcyI6IFsidml0ZS5jb25maWcuanMiXSwKICAic291cmNlc0NvbnRlbnQiOiBbImNvbnN0IF9fdml0ZV9pbmplY3RlZF9vcmlnaW5hbF9kaXJuYW1lID0gXCIvc2Vzc2lvbnMvcmN3LTAxbm96ZHFocXdybzg2Z2J6cmJ4cW50by9tbnQvcHJvamVjdC9hZGFyLXJhZy91aS9lYXRzXCI7Y29uc3QgX192aXRlX2luamVjdGVkX29yaWdpbmFsX2ZpbGVuYW1lID0gXCIvc2Vzc2lvbnMvcmN3LTAxbm96ZHFocXdybzg2Z2J6cmJ4cW50by9tbnQvcHJvamVjdC9hZGFyLXJhZy91aS9lYXRzL3ZpdGUuY29uZmlnLmpzXCI7Y29uc3QgX192aXRlX2luamVjdGVkX29yaWdpbmFsX2ltcG9ydF9tZXRhX3VybCA9IFwiZmlsZTovLy9zZXNzaW9ucy9yY3ctMDFub3pkcWhxd3JvODZnYnpyYnhxbnRvL21udC9wcm9qZWN0L2FkYXItcmFnL3VpL2VhdHMvdml0ZS5jb25maWcuanNcIjtpbXBvcnQgeyBkZWZpbmVDb25maWcgfSBmcm9tICd2aXRlJztcbmltcG9ydCByZWFjdCBmcm9tICdAdml0ZWpzL3BsdWdpbi1yZWFjdCc7XG5cbi8vIE1pcnJvcnMgYWRhci1yYWcvZnJvbnRlbmQvdml0ZS5jb25maWcuanMgKHRoZSBEb2NJbnRlbCB3ZWIgYXBwKSAtLVxuLy8gc2FtZSBkZXYtcHJveHktdG8tbG9jYWwtYmFja2VuZCBwYXR0ZXJuLCBzYW1lIGJ1aWxkIG91dHB1dCBzaGFwZSwgc29cbi8vIHRoaXMgYXBwIGRlcGxveXMgdG8gRmlyZWJhc2UgSG9zdGluZyB0aGUgc2FtZSB3YXkuXG5leHBvcnQgZGVmYXVsdCBkZWZpbmVDb25maWcoe1xuICBwbHVnaW5zOiBbcmVhY3QoKV0sXG5cbiAgc2VydmVyOiB7XG4gICAgcG9ydDogNTE3NCxcbiAgICBwcm94eToge1xuICAgICAgJy9hcGknOiB7IHRhcmdldDogJ2h0dHA6Ly9sb2NhbGhvc3Q6ODAwMCcsIGNoYW5nZU9yaWdpbjogdHJ1ZSB9LFxuICAgIH0sXG4gIH0sXG5cbiAgYnVpbGQ6IHtcbiAgICBvdXREaXI6ICdkaXN0JyxcbiAgICAvLyBlbXB0eU91dERpciBsZWZ0IGZhbHNlOiB0aGlzIHNoZWxsIGNhbid0IGRlbGV0ZSBmaWxlcyBpbiB0aGVcbiAgICAvLyBjb25uZWN0ZWQgZm9sZGVyIChubyBkZWxldGUgcGVybWlzc2lvbiBncmFudGVkKSwgYW5kIFZpdGUncyBkZWZhdWx0XG4gICAgLy8gY2xlYW4tYmVmb3JlLWJ1aWxkIG5lZWRzIHRvIHJtIHRoZSBvbGQgZGlzdC8gZmlyc3QuIExlYXZpbmcgaXQgb2ZmXG4gICAgLy8gbWVhbnMgc3RhbGUgY29udGVudC1oYXNoZWQgY2h1bmtzIGZyb20gZWFybGllciBidWlsZHMgYWNjdW11bGF0ZSBpblxuICAgIC8vIGRpc3QvYXNzZXRzLyBpbnN0ZWFkIG9mIGJlaW5nIHJlbW92ZWQgLS0gaGFybWxlc3MgZm9yIGEgc3RhdGljXG4gICAgLy8gZGVwbG95IChpbmRleC5odG1sIGFsd2F5cyBwb2ludHMgYXQgdGhlIGN1cnJlbnQgYnVpbGQncyBoYXNoZXMpLFxuICAgIC8vIGp1c3QgYSBsaXR0bGUgZXh0cmEgc3RvcmFnZS4gUmUtZW5hYmxlIG9uY2UgZGVsZXRlIGlzIGFsbG93ZWQuXG4gICAgZW1wdHlPdXREaXI6IGZhbHNlLFxuICAgIHJvbGx1cE9wdGlvbnM6IHtcbiAgICAgIG91dHB1dDoge1xuICAgICAgICBtYW51YWxDaHVua3M6IHtcbiAgICAgICAgICByZWFjdDogWydyZWFjdCcsICdyZWFjdC1kb20nXSxcbiAgICAgICAgfSxcbiAgICAgIH0sXG4gICAgfSxcbiAgfSxcbn0pO1xuIl0sCiAgIm1hcHBpbmdzIjogIjtBQUEyWCxTQUFTLG9CQUFvQjtBQUN4WixPQUFPLFdBQVc7QUFLbEIsSUFBTyxzQkFBUSxhQUFhO0FBQUEsRUFDMUIsU0FBUyxDQUFDLE1BQU0sQ0FBQztBQUFBLEVBRWpCLFFBQVE7QUFBQSxJQUNOLE1BQU07QUFBQSxJQUNOLE9BQU87QUFBQSxNQUNMLFFBQVEsRUFBRSxRQUFRLHlCQUF5QixjQUFjLEtBQUs7QUFBQSxJQUNoRTtBQUFBLEVBQ0Y7QUFBQSxFQUVBLE9BQU87QUFBQSxJQUNMLFFBQVE7QUFBQTtBQUFBO0FBQUE7QUFBQTtBQUFBO0FBQUE7QUFBQTtBQUFBLElBUVIsYUFBYTtBQUFBLElBQ2IsZUFBZTtBQUFBLE1BQ2IsUUFBUTtBQUFBLFFBQ04sY0FBYztBQUFBLFVBQ1osT0FBTyxDQUFDLFNBQVMsV0FBVztBQUFBLFFBQzlCO0FBQUEsTUFDRjtBQUFBLElBQ0Y7QUFBQSxFQUNGO0FBQ0YsQ0FBQzsiLAogICJuYW1lcyI6IFtdCn0K
