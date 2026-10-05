import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

const root = fileURLToPath(new URL('.', import.meta.url))

// Must match SUPABASE_URL in src/supabaseClient.js.
const SUPABASE_HOST = 'jfxfzulufaxrmopnvpqa.supabase.co'

const CONTENT_SECURITY_POLICY = [
  "default-src 'self'",
  "script-src 'self'",
  "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
  "font-src 'self' data: https://fonts.gstatic.com",
  "img-src 'self' data: blob:",
  `connect-src 'self' https://${SUPABASE_HOST} wss://${SUPABASE_HOST}`,
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
].join('; ')

// GitHub Pages can't send security headers, so the built pages carry them as
// <meta> tags instead. Build-only, so the dev server's HMR socket isn't blocked.
function securityMetaTags() {
  const tags =
    `\n<meta http-equiv="Content-Security-Policy" content="${CONTENT_SECURITY_POLICY}">` +
    '\n<meta name="referrer" content="strict-origin-when-cross-origin">'
  const anchor = /<meta name="viewport"[^>]*>/
  return {
    name: 'security-meta-tags',
    apply: 'build',
    transformIndexHtml(html, ctx) {
      if (!anchor.test(html)) throw new Error(`security-meta-tags: no viewport meta in ${ctx.filename}`)
      return html.replace(anchor, (m) => m + tags)
    },
  }
}

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
  // (A native-vendors.com custom domain was tried 2026-09-16 and reverted
  // the same day -- Urban Company's Palo Alto security policy sinkholes
  // brand-new domains, blocking it on managed laptops. Revisit once IT
  // allow-lists the domain, or drop it if a subdomain of an
  // already-trusted urbancompany.com becomes an option instead.)
  base: '/Vendor-dashboard/',
  plugins: [vue(), securityMetaTags()],
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
