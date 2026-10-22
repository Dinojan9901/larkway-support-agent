import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

// A real agent turn recorded from the policy engine (no model call), replayed so the
// tool trace, answer, confirmation card and trust panel can be checked and shown.
const chatStream = readFileSync(fileURLToPath(new URL('./fixtures/chat-stream.txt', import.meta.url)), 'utf8')
const shotsDir = fileURLToPath(new URL('../../docs/screenshots/', import.meta.url))

// The demo logins live in the README for reviewers, and only there. Reading them
// from it also proves the README's credentials work.
function readmeLogins() {
  const readme = readFileSync(fileURLToPath(new URL('../../README.md', import.meta.url)), 'utf8')
  const rows = [...readme.matchAll(/^\| (Customer|Ops) \| `([^`]+)` \| `([^`]+)` \|/gm)]
  if (rows.length !== 2) throw new Error('README demo-logins table not found')
  const find = (role: string) => {
    const row = rows.find((r) => r[1] === role)!
    return { username: row[2], password: row[3] }
  }
  return { customer: find('Customer'), ops: find('Ops') }
}
const LOGINS = readmeLogins()

async function expectNoAxeViolations(page: Page, label: string) {
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze()
  const summary = results.violations.map(
    (v) =>
      `${v.id} (${v.impact}): ${v.help} — ` +
      v.nodes.map((n) => `${n.target.join(' ')}${n.any[0]?.message ? ` [${n.any[0].message}]` : ''}`).join('; '),
  )
  expect(summary, `axe violations on ${label}`).toEqual([])
}

async function expectNoHorizontalScroll(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
  expect(overflow).toBeLessThanOrEqual(0)
}

async function signIn(page: Page, who: keyof typeof LOGINS) {
  await page.goto('/')
  await page.getByLabel('Username').fill(LOGINS[who].username)
  await page.getByLabel('Password').fill(LOGINS[who].password)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('tab', { name: 'Chat' })).toBeVisible()
}

const isNarrow = (page: Page) => (page.viewportSize()?.width ?? 1280) < 1024

test.beforeEach(async ({ page }) => {
  await page.route('**/api/chat', (route) =>
    route.fulfill({ status: 200, headers: { 'Content-Type': 'text/event-stream' }, body: chatStream }),
  )
  await page.route('**/api/actions/*/confirm', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ confirmed: 'ACT-E2E', action: { status: 'executed', result: { reference: 'TKT-UPD-1042' } } }),
    }),
  )
})

test('login page is accessible and fits the screen', async ({ page }, info) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible()
  await expectNoAxeViolations(page, 'login')
  await expectNoHorizontalScroll(page)

  await page.getByLabel('Username').fill('customer')
  await page.getByLabel('Password').fill('wrong')
  await page.keyboard.press('Enter')
  await expect(page.getByRole('alert')).toContainText('don’t match')
  await expectNoAxeViolations(page, 'login error')
  await page.screenshot({ path: `${shotsDir}login-${info.project.name}.png` })
})

test('customer chat: streamed answer, confirmation gate and trust panel', async ({ page }, info) => {
  await signIn(page, 'customer')
  await expect(page.getByRole('tab', { name: 'Operations signals' })).toHaveCount(0)
  await expectNoAxeViolations(page, 'customer empty chat')

  const box = page.getByLabel('Message', { exact: true })
  await box.fill('Can I cancel SHP-5001 without a cancellation fee? Explain why.')
  await box.press('Enter')

  const answer = page.getByRole('article', { name: 'Assistant answer' })
  await expect(answer.getByRole('list', { name: 'Tools used' })).toContainText('Calculating cancellation eligibility and fee')
  await expect(answer.getByRole('button', { name: 'Confirm and run' })).toBeVisible()
  await answer.getByRole('button', { name: /confidence/ }).click()
  await expect(answer.getByText('Sources used')).toBeVisible()
  await expectNoAxeViolations(page, 'customer answer')
  await expectNoHorizontalScroll(page)
  await page.screenshot({ path: `${shotsDir}chat-${info.project.name}.png`, fullPage: false })

  await answer.getByRole('button', { name: 'Confirm and run' }).click()
  await expect(answer.getByText('Executed — reference TKT-UPD-1042')).toBeFocused()
})

test('ops console needs the ops role and is accessible', async ({ page }, info) => {
  await signIn(page, 'ops')
  const opsTab = page.getByRole('tab', { name: 'Operations signals' })
  await page.getByRole('tab', { name: 'Chat' }).focus()
  await page.keyboard.press('ArrowRight')
  await expect(opsTab).toBeFocused()
  await expect(opsTab).toHaveAttribute('aria-selected', 'true')

  await expect(page.getByRole('heading', { name: 'What needs attention' })).toBeVisible()
  await expect(page.getByRole('list', { name: 'Summary' })).toContainText('critical')
  await expect(page.getByRole('heading', { name: /CASE-301 \(P1\) BREACHED/ })).toBeVisible()
  await expectNoAxeViolations(page, 'ops console')
  await expectNoHorizontalScroll(page)
  await page.screenshot({ path: `${shotsDir}ops-${info.project.name}.png` })
})

test('sidebar works by keyboard: drawer on small screens, column on desktop', async ({ page }) => {
  await signIn(page, 'customer')

  if (isNarrow(page)) {
    const menu = page.getByRole('button', { name: 'Menu' })
    await expect(menu).toHaveAttribute('aria-expanded', 'false')
    // Closed drawer is inert: Tab must not land inside it.
    await expect(page.getByRole('button', { name: 'Sign out' })).toBeHidden()
    await menu.click()
    await expect(page.getByRole('button', { name: 'Sign out' })).toBeVisible()
    await expectNoAxeViolations(page, 'open drawer')
    await page.keyboard.press('Escape')
    await expect(menu).toBeFocused()
    await expect(menu).toHaveAttribute('aria-expanded', 'false')
    await menu.click()
  }

  await page.getByRole('button', { name: /Show me the orders on my account/ }).click()
  await expect(page.getByLabel('Message', { exact: true })).toHaveValue('Show me the orders on my account.')
  await expect(page.getByLabel('Message', { exact: true })).toBeFocused()
})

test('skip link jumps to the main content', async ({ page }) => {
  await signIn(page, 'customer')
  await page.keyboard.press('Tab')
  const skip = page.getByRole('link', { name: 'Skip to main content' })
  await expect(skip).toBeFocused()
  await skip.press('Enter')
  await expect(page.locator('#main-content')).toBeFocused()
})
