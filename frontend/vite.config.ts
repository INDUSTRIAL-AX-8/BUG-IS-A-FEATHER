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
    // 백엔드가 다른 포트면 BIF_API_URL=http://127.0.0.1:8010 npm run dev
    proxy: { '/api': process.env.BIF_API_URL ?? 'http://127.0.0.1:8000' },
  },
})
