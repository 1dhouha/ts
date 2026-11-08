import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';
import {fileURLToPath} from 'node:url';
import {resolve} from 'node:path';

const root = fileURLToPath(new URL('.', import.meta.url));

export default defineConfig({
  root,
  plugins: [react()],
  base: '/3d/assets/',
  publicDir: false,
  build: {
    outDir: resolve(root, '../../src/city_dashboard/web/3d/assets'),
    emptyOutDir: true,
    cssCodeSplit: false,
    sourcemap: false,
    rollupOptions: {
      input: resolve(root, 'src/main.jsx'),
      output: {
        codeSplitting: false,
        entryFileNames: 'city3d-viewer.js',
        assetFileNames: 'city3d.[ext]'
      }
    }
  }
});
