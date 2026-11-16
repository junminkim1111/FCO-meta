// Static server: python3 -m http.server 18765 --directory fco_meta/web
// Uses fixtures only; no backend or paid APIs.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const source = fs.readFileSync('fco_meta/web/static/index.html', 'utf8');
const tooltip = source.split('// --- tooltip ---')[1].split('// --- 분포:')[0];
(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    for (const fallback of [false, true]) {
      const page = await browser.newPage({ viewport: { width: 390, height: 844 }, hasTouch: true });
      const errors = [];
      page.on('pageerror', e => errors.push(e.message));
      await page.route('**/*', route => {
        const url = new URL(route.request().url());
        if (url.hostname !== '127.0.0.1') return route.abort();
        if (url.pathname.startsWith('/api/')) return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ formations: [], rows: [] }) });
        return route.continue();
      });
      await page.addInitScript(fallback => {
        if (fallback) {
          delete Element.prototype.setHTML;
          delete HTMLDialogElement.prototype.closedBy;
        }
        window.framesRequested = 0;
        const raf = window.requestAnimationFrame.bind(window);
        window.requestAnimationFrame = fn => { window.framesRequested++; return raf(fn); };
      }, fallback);
      const base = process.env.FCO_TEST_URL || 'http://127.0.0.1:18765';
      await page.goto(base + '/static/index.html');
      if (!fallback) assert.equal(await page.evaluate(() => 'setHTML' in Element.prototype), true);
      const msg = page.locator('#msg');
      await msg.fill('/');
      assert.equal(await msg.getAttribute('aria-controls'), 'cmd-menu');
      assert.equal(await msg.getAttribute('aria-activedescendant'), 'cmd-0');
      await msg.press('ArrowDown'); await msg.press('ArrowDown');
      assert.equal(await msg.getAttribute('aria-activedescendant'), 'cmd-1');
      assert.equal(await page.locator('#cmd-1').getAttribute('aria-selected'), 'true');
      await msg.press('Escape');
      assert.equal(await msg.getAttribute('aria-activedescendant'), null);
      await msg.fill('/tr'); await msg.press('Tab');
      assert.equal(await msg.inputValue(), '/trace ');
      await msg.fill('/'); await msg.blur();
      assert.equal(await msg.getAttribute('aria-activedescendant'), null);
      const radios = page.locator('#dist-mode [role=radio]');
      await radios.first().focus(); await page.keyboard.press('ArrowLeft');
      assert.equal(await radios.last().getAttribute('aria-checked'), 'true');
      assert.equal(await radios.first().getAttribute('tabindex'), '-1');
      await page.keyboard.press('Home');
      assert.equal(await radios.first().getAttribute('aria-checked'), 'true');
      await page.keyboard.press('End');
      assert.equal(await radios.last().getAttribute('aria-checked'), 'true');
      await msg.focus();
      assert.notEqual(await msg.evaluate(n => getComputedStyle(n).outlineStyle), 'none');
      await page.evaluate(source => {
        const hover = new Function('$', source + '\nreturn hover;')(id => document.getElementById(id));
        const node = document.createElement('div'); node.id = 'tooltip-test'; node.textContent = '선수';
        document.body.append(hover(node, '선수 전체 이름 · 시즌 · 강화'));
      }, tooltip);
      const target = page.locator('#tooltip-test');
      await target.focus();
      assert.equal(await page.locator('#tip').isVisible(), true);
      assert.equal(await target.getAttribute('aria-describedby'), 'tip');
      await target.press('Escape');
      assert.equal(await page.locator('#tip').isVisible(), false);
      await page.touchscreen.tap(...await target.evaluate(n => { const r = n.getBoundingClientRect(); return [r.x + 2, r.y + 2]; }));
      assert.equal(await page.locator('#tip').isVisible(), true);
      await msg.focus();
      assert.equal(await page.locator('#tip').isVisible(), false);
      await msg.fill('@');
      await page.locator('#team-name').fill('한😀');
      await page.waitForFunction(() => document.querySelector('.spring-layer').textContent === '한😀');
      await page.locator('#team-name').press('Home');
      await page.waitForTimeout(1500);
      let frames = await page.evaluate(() => framesRequested);
      await page.waitForTimeout(150);
      assert.equal(await page.evaluate(() => framesRequested), frames, 'settled input stops rendering');
      await page.emulateMedia({ reducedMotion: 'reduce' });
      await page.locator('#team-name').fill('한글');
      await page.waitForTimeout(100);
      frames = await page.evaluate(() => framesRequested);
      await page.waitForTimeout(150);
      assert.equal(await page.evaluate(() => framesRequested), frames, 'reduced motion stops rendering');
      await page.locator('#team-name').blur();
      await page.waitForTimeout(100);
      frames = await page.evaluate(() => framesRequested);
      await page.waitForTimeout(150);
      assert.equal(await page.evaluate(() => framesRequested), frames, 'unfocused input stops rendering');
      await page.locator('#team-name').press('Escape');
      await page.evaluate(async () => {
        const { renderMarkdown } = await import('/static/md.js');
        const node = document.createElement('div'); node.id = 'markdown-test'; document.body.append(node);
        renderMarkdown(node, '**64.3%**로\n\n| 선수 | 시즌 |\n|---|---|\n| 앙리 | UC |\n\n<img src="https://example.com/x" onerror="window.xss=1"><script>window.xss=1</script><a href="javascript:window.xss=1">unsafe</a>');
      });
      assert.equal(await page.locator('#markdown-test strong').textContent(), '64.3%');
      assert.equal(await page.locator('#markdown-test table').count(), 1);
      assert.equal(await page.locator('#markdown-test img, #markdown-test script, #markdown-test [onerror], #markdown-test [href^="javascript:"]').count(), 0);
      assert.equal(await page.evaluate(() => window.xss), undefined);
      assert.deepEqual(errors, []);
      // Native and fallback backdrop dismissal, without loading any logs.
      await page.route('**/api/admin/**', route => route.fulfill({ status: 401, contentType: 'application/json', body: '{}' }));
      await page.goto(base + '/static/admin.html');
      await page.evaluate(fallback => {
        const drawer = document.querySelector('#drawer');
        if (fallback) drawer.removeAttribute('closedby'); // Exercise only the fallback listeners.
        drawer.showModal();
      }, fallback);
      await page.locator('#drawer').click({ position: { x: 5, y: 100 } });
      assert.equal(await page.locator('#drawer').evaluate(n => n.open), true);
      await page.setViewportSize({ width: 1200, height: 844 });
      await page.mouse.move(1000, 400); await page.mouse.down();
      await page.mouse.move(10, 400); await page.mouse.up();
      assert.equal(await page.locator('#drawer').evaluate(n => n.open), true, 'selection drag does not dismiss');
      await page.mouse.click(10, 400);
      await page.waitForFunction(() => !document.querySelector('#drawer').open);
      assert.deepEqual(errors, []);
      await page.close();
      console.log(`Modern web passed (${fallback ? 'fallback' : 'native'}): autocomplete, radios, focus, idle/reduced-motion rendering, Markdown/XSS, dialog.`);
    }
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
