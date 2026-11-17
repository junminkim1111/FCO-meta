// Main page split handle. Run with NODE_PATH pointing to an installed Playwright; no model/API calls.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.goto(process.env.FCO_TEST_URL || 'http://127.0.0.1:8765');
    const width = () => page.locator('.col').evaluate(e => e.getBoundingClientRect().width);
    const half = await width();
    const box = await page.locator('#split').boundingBox();
    const y = box.y + box.height / 2;
    await page.mouse.move(box.x + 8, y); await page.mouse.down();
    await page.mouse.move(box.x - 92, y, { steps: 4 });
    assert.equal(await width(), half - 100, 'drag left widens the chat');
    await page.mouse.move(400, y, { steps: 4 });
    assert.equal(await width(), 536, 'left column stops where the squad pitch still fits');
    await page.mouse.move(0, y, { steps: 4 });
    assert.equal(await width(), 536, 'never folds');
    await page.mouse.move(1400, y, { steps: 4 }); await page.mouse.up();
    assert.equal(await width(), half, 'right stops at half');
    await page.locator('#split').focus();
    await page.keyboard.press('ArrowLeft');
    assert.equal(await width(), half - 40, 'keyboard moves 40px');
    await page.keyboard.press('ArrowRight');
    assert.equal(await width(), half);
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.locator('#split').isVisible(), false, 'no handle on narrow screens');
    assert.deepEqual(errors, []);
    console.log('Split checks passed: drag, minimum, half, keyboard, mobile.');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
