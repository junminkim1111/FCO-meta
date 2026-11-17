// Local fixtures only: never contacts model providers.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    for (const width of [390, 768, 1440]) {
      const page = await browser.newPage({ viewport: { width, height: 900 } });
      const errors = []; let requests = 0;
      page.on('pageerror', e => errors.push(e.message));
      await page.route('**/*', async route => {
        const url = new URL(route.request().url());
        if (url.hostname !== '127.0.0.1') return route.abort();
        if (url.pathname === '/api/chat') {
          requests++;
          await new Promise(resolve => setTimeout(resolve, 1200));
          return route.fulfill({ contentType: 'application/x-ndjson', body: [
            { type: 'start', session_id: 'fixture' },
            { type: 'delta', text: '**테스트 답변**입니다.' },
            { type: 'done' },
          ].map(JSON.stringify).join('\n') + '\n' });
        }
        if (url.pathname === '/api/formations') return route.fulfill({ json: { formations: [{ formation: '4-2-3-1', share: 0.5 }] } });
        if (url.pathname === '/api/formation') return route.fulfill({ json: { formation: '4-2-3-1', ranking: null, matchups: { opponents: [] } } });
        if (url.pathname.startsWith('/api/')) return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ formations: [], rows: [] }) });
        return route.continue();
      });
      await page.goto('http://127.0.0.1:18765/static/ai/index.html');
      if (width === 1440) await page.screenshot({ path: '/tmp/fclm-ai-welcome.png', fullPage: true });
      assert.equal(await page.locator('#attach-team, #choose-model, .suggestions, .ai-note, .welcome-context').count(), 0);
      assert.equal(requests, 0);
      const initialWidth = await page.locator('#chat').evaluate(n => n.getBoundingClientRect().width);
      assert.ok(await page.locator('#stats').evaluate(n => n.getBoundingClientRect().top >= innerHeight));
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      await page.locator('#msg').fill('@');
      await page.locator('#team-box').waitFor({ state: 'visible' });
      await page.locator('#team-name').fill('테스트');
      await page.locator('#team-name').press('Escape');
      await page.locator('#msg').fill('/model');
      await page.locator('#chat-form').evaluate(n => n.requestSubmit());
      await page.locator('#model-cancel').click();
      await page.locator('#model-dialog').waitFor({ state: 'hidden' });
      await page.locator('#msg').fill('/trace');
      await page.locator('#chat-form').evaluate(n => n.requestSubmit());
      await page.locator('#msg').fill('가성비 좋은 볼란치 추천해줘');
      await page.locator('#msg').press('Enter');
      await page.locator('.thinking-text output').waitFor();
      assert.equal(await page.locator('#ai-beam').getAttribute('data-active'), '');
      assert.equal(await page.locator('#log canvas').count(), 1);
      if (width === 1440) { await page.waitForTimeout(350); await page.screenshot({ path: '/tmp/fclm-ai-thinking.png', fullPage: true }); }
      await page.waitForFunction(() => document.querySelector('#log').textContent.includes('테스트 답변'));
      assert.equal(await page.locator('#ai-beam').getAttribute('data-active'), null);
      assert.equal(await page.locator('#fdetail .pitch').count(), 0);
      assert.ok(await page.locator('#pitch-detail .pitch').count());
      assert.equal(requests, 1);
      const layout = await page.evaluate(() => {
        const chat = document.querySelector('#chat').getBoundingClientRect();
        const composer = document.querySelector('#ai-beam').getBoundingClientRect();
        const log = document.querySelector('#log').getBoundingClientRect();
        return { width: chat.width, bottom: composer.bottom, logHeight: log.height, viewport: innerHeight, scrollWidth: document.documentElement.scrollWidth };
      });
      if (width === 1440) assert.ok(layout.width > initialWidth * 1.5);
      assert.ok(layout.width >= width - 65);
      assert.ok(layout.bottom >= layout.viewport - 40 && layout.bottom <= layout.viewport);
      assert.ok(layout.logHeight >= layout.viewport * .6);
      assert.ok(layout.scrollWidth <= width);
      assert.equal(await page.locator('#welcome').isVisible(), false);
      assert.ok(await page.locator('.agent-steps').count());
      const alignment = await page.evaluate(() => {
        const trace = document.querySelector('.agent-steps button').getBoundingClientRect();
        const reply = document.querySelector('#log .msg.bot.md').getBoundingClientRect();
        const logo = document.querySelector('.ai-header img').getBoundingClientRect();
        const chat = document.querySelector('#chat').getBoundingClientRect();
        return { traceLeft: trace.left, replyLeft: reply.left, logoLeft: logo.left, chatLeft: chat.left };
      });
      assert.ok(Math.abs(alignment.traceLeft - alignment.replyLeft) <= 4, 'Trace aligns with the answer');
      assert.ok(Math.abs(alignment.logoLeft - alignment.chatLeft) <= 1, 'Header aligns with the conversation');
      assert.deepEqual(errors, []);
      if (width === 1440) { await page.waitForTimeout(450); await page.screenshot({ path: '/tmp/fclm-ai-conversation.png', fullPage: true }); }
      await page.close();
    }
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
