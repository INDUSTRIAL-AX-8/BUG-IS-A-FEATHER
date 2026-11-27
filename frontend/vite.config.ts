import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 폐쇄망: 모든 리소스를 빌드 산출물에 포함한다 (CDN, 외부 폰트 금지).
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: 'dist',
    assetsInlineLimit: 0,
    chunkSizeWarningLimit: 2500,
  },
  server: {
    port: 5173,
    proxy: { '/api': 'http://127.0.0.1:8000' },
  },
})
