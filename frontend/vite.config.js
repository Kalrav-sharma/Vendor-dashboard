import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

const root = fileURLToPath(new URL('.', import.meta.url))

// Multi-page build: each migrated page gets its own HTML entry. Built
// output is copied manually into ../docs/ (see scripts/build-and-copy.sh)
// rather than deploying dist/ directly, since docs/ also holds pages not
// migrated yet (vendor.html, admin.html) and the Uniware sync workflow's
// own heartbeat file -- none of that should be touched by this build.
//
// assetsDir is set to something other than Vite's default ("assets") so
// this build's hashed JS/CSS files never land in the same folder as the
// legacy docs/assets/ (app-common.js, styles.css, supabase-client.js),
// which are still used by the not-yet-migrated pages.
export default defineConfig({
  // GitHub Pages serves this site from /Vendor-dashboard/, not the domain
  // root -- without this, Vite's default root-absolute asset paths
  // (/vite-assets/...) would resolve to the wrong URL on the live site.
  base: '/Vendor-dashboard/',
  plugins: [vue()],
  build: {
    assetsDir: 'vite-assets',
    rollupOptions: {
      input: {
        index: resolve(root, 'index.html'),
        login: resolve(root, 'login.html'),
        'reset-password': resolve(root, 'reset-password.html'),
        vendor: resolve(root, 'vendor.html'),
        admin: resolve(root, 'admin.html'),
      },
    },
  },
})
