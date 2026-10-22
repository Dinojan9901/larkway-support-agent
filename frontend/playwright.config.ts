import { defineConfig } from '@playwright/test'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

// Real-browser checks: axe accessibility scans, layout at three widths, keyboard
// paths and README screenshots. Needs the built frontend (`npm run build`) and the
// backend; Playwright starts uvicorn itself unless one is already on the port.
//   LARKWAY_PYTHON=../.venv/Scripts/python.exe npm run check:browser
const port = Number(process.env.E2E_PORT ?? 8765)
const here = path.dirname(fileURLToPath(import.meta.url))
// Resolved and quoted: on Windows the command runs through cmd.exe, which can't
// execute a relative path like ../.venv/Scripts/python.exe.
const python = process.env.LARKWAY_PYTHON ? `"${path.resolve(here, process.env.LARKWAY_PYTHON)}"` : 'python'

export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  fullyParallel: false,
  workers: 1,
  reporter: [['list']],
  use: { baseURL: `http://127.0.0.1:${port}`, trace: 'retain-on-failure' },
  projects: [
    { name: 'mobile-375', use: { viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true } },
    { name: 'tablet-768', use: { viewport: { width: 768, height: 1024 } } },
    { name: 'desktop-1280', use: { viewport: { width: 1280, height: 800 } } },
  ],
  webServer: {
    command: `${python} -m uvicorn app.main:app --host 127.0.0.1 --port ${port}`,
    cwd: '..',
    url: `http://127.0.0.1:${port}/api/health`,
    reuseExistingServer: true,
    env: { LARKWAY_JWT_SECRET: process.env.LARKWAY_JWT_SECRET ?? 'e2e-only-secret-not-for-production-use-0000' },
  },
})
