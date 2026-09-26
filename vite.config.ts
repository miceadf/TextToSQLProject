import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api/adaptive': {
        target: process.env.VITE_ADAPTIVE_PROTOTYPE_TARGET || 'http://127.0.0.1:8123',
        changeOrigin: true,
      },
      '/api/v1': {
        target: process.env.VITE_BACKEND_TARGET || 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
