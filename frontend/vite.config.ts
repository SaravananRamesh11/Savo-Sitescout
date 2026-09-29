import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Dev server proxies /api to the FastAPI backend, so the phone/LAN demo needs no CORS setup.
export default defineConfig({
  plugins: [react()],
  server: { host: true, proxy: { '/api': 'http://127.0.0.1:8000' } },
})
