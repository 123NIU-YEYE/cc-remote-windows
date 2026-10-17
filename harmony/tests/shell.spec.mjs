import { expect, test } from '../../web/node_modules/@playwright/test/index.mjs';
import { readFileSync } from 'node:fs';

const adapter = readFileSync(new URL('../entry/src/main/resources/rawfile/shell.js', import.meta.url), 'utf8');
async function install(page, origin = 'http://127.0.0.1:4177') {
  await page.evaluate(`${adapter}(${JSON.stringify(origin)});`);
}

test('native menu integration preserves the original sidebar rendering', async ({ page }) => {
  await page.goto('/tests/history-browser.html?mobile-sidebar=1');
  await expect(page.locator('.thread')).toBeVisible();
  const appearance = () => page.locator('.pane').evaluate(node => {
    const style = getComputedStyle(node);
    return [style.filter, style.transform, style.borderRadius, style.boxShadow, style.transition];
  });
  const before = await appearance();
  await install(page);
  expect(await appearance()).toEqual(before);
  await expect(page.locator('.pane')).toHaveCSS('filter', 'blur(0px)');
});

test('settings uses the existing menu, survives page switches and does not duplicate', async ({ page }) => {
  await page.goto('/tests/history-browser.html?header-menu=1&engine=codex');
  await expect(page.getByRole('button', { name: '更多设置' })).toBeVisible();
  await install(page);
  await install(page);
  await expect(page.locator('#cc-remote-harmony-settings-fallback')).toBeHidden();
  for (let cycle = 0; cycle < 2; cycle++) {
    await page.getByRole('button', { name: '更多设置' }).click();
    await expect(page.getByRole('link', { name: /客户端设置/ })).toHaveCount(1);
    await expect(page.getByRole('link', { name: /客户端设置/ })).toHaveAttribute('href', 'cc-remote-app://settings');
    await page.getByRole('button', { name: /^通知/ }).click();
    await expect(page.getByRole('link', { name: /客户端设置/ })).toHaveCount(0);
    await page.getByRole('button', { name: '返回设置' }).click();
    await expect(page.getByRole('link', { name: /客户端设置/ })).toHaveCount(1);
    await page.keyboard.press('Escape');
  }
});

test('client settings remains reachable at login', async ({ page }) => {
  await page.goto('/tests/history-browser.html?header-menu=1');
  await expect(page.getByRole('button', { name: '更多设置' })).toBeVisible();
  await page.evaluate(() => document.querySelector('#root').replaceChildren());
  await install(page);
  await expect(page.getByRole('link', { name: '客户端设置', exact: true })).toBeVisible();
});

test('other origins and iframe documents receive no native settings link', async ({ page }) => {
  await page.goto('/tests/history-browser.html?header-menu=1');
  await install(page, 'https://other.example');
  await expect(page.locator('#cc-remote-harmony-style')).toHaveCount(0);
  await page.evaluate(() => {
    const frame = document.createElement('iframe');
    frame.src = '/tests/history-browser.html?header-menu=1';
    document.body.append(frame);
  });
  await expect.poll(() => page.frames().length).toBe(2);
  const frame = page.frames()[1];
  await frame.waitForURL(/history-browser\.html/);
  await frame.waitForLoadState();
  await install(frame);
  await expect(frame.locator('#cc-remote-harmony-style')).toHaveCount(0);
});
