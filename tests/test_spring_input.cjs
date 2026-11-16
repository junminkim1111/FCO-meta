const { chromium } = require('playwright');
const assert = require('node:assert/strict');
(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
    const errors = []; page.on('pageerror', e => errors.push(e.message));
    await page.goto('http://127.0.0.1:8000/');
    let nickname, fail = false;
    await page.route('**/api/team', async route => {
      await new Promise(resolve => setTimeout(resolve, 300));
      nickname = route.request().postDataJSON().nickname;
      return route.fulfill({ status: fail ? 400 : 200, contentType: 'application/json', body: JSON.stringify(fail ? { detail: '구단주를 찾지 못했어요.' } : { nickname, mention: nickname.startsWith('#') ? nickname : '@' + nickname, label: '테스트 팀', session_id: 'mock' }) });
    });
    for (const mark of ['@', '#']) {
      await page.locator('#msg').fill(mark);
      await page.locator('#team-name').waitFor({ state: 'visible' });
      assert.equal(await page.locator('#team-name').getAttribute('placeholder'), mark === '@' ? '구단주명' : '스쿼드 피드 ID');
      assert.equal(await page.locator('.spring-label').count(), 0);
      assert.equal(await page.locator('.spring-input').evaluate(el => parseFloat(getComputedStyle(el).height)), 38);
      await page.locator('#team-name').fill(mark === '@' ? '아스널😀' : '12345');
      await page.waitForFunction(() => document.querySelector('.spring-layer').textContent.includes(document.querySelector('#team-name').value));
      assert.equal(await page.locator('.spring-char').first().evaluate(el => getComputedStyle(el).animationName), 'none');
      await page.locator('#team-name').press('Enter');
      await page.locator('#team-ok[aria-busy="true"]').waitFor();
      assert.equal(await page.locator('#team-ok').isDisabled(), true);
      assert.equal(await page.locator('#team-ok').evaluate(button => {
        const spinner = getComputedStyle(button, '::after');
        const x = parseFloat(spinner.left) + parseFloat(spinner.marginLeft) + parseFloat(spinner.width) / 2;
        const y = parseFloat(spinner.top) + parseFloat(spinner.marginTop) + parseFloat(spinner.height) / 2;
        return Math.abs(x - button.clientWidth / 2) < .5 && Math.abs(y - button.clientHeight / 2) < .5;
      }), true);
      await page.waitForFunction(() => document.querySelector('.spring-input').classList.contains('ok'));
      assert.equal(await page.locator('.spring-done').textContent(), '✓ 팀 첨부 완료');
      await page.waitForFunction(() => document.querySelector('#team-box').hidden);
      assert.equal(nickname, mark === '@' ? '아스널😀' : '#12345');
      assert.match(await page.locator('#team-chip-name').textContent(), mark === '@' ? /아스널/ : /12345/);
    }
    await page.locator('#msg').fill('@');
    await page.locator('#team-name').evaluate(input => {
      input.dispatchEvent(new CompositionEvent('compositionstart', { bubbles: true }));
      input.value = '한'; input.dispatchEvent(new InputEvent('input', { bubbles: true, isComposing: true }));
    });
    assert.equal(await page.locator('.spring-input').evaluate(n => n.classList.contains('composing')), true);
    await page.locator('#team-name').evaluate(input => input.dispatchEvent(new CompositionEvent('compositionend', { bubbles: true })));
    await page.locator('#team-name').fill('아주긴구단주이름테스트12345678901234');
    await page.locator('#team-name').press('Home');
    await page.locator('#team-name').press('Delete');
    fail = true;
    await page.locator('#team-name').press('Enter');
    await page.locator('#team-hint.err').waitFor();
    assert.equal(await page.locator('#team-box').isVisible(), true);
    await page.waitForFunction(() => Math.abs(new DOMMatrix(getComputedStyle(document.querySelector('.spring-input')).transform).m41) < .1);
    assert.equal(await page.locator('.spring-input').evaluate(el => el.getBoundingClientRect().right <= innerWidth), true);
    await page.screenshot({ path: '/tmp/fclm-spring-input.png' });
    await page.locator('#team-name').press('Escape');
    await page.locator('#team-box').waitFor({ state: 'hidden' });
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.locator('#msg').fill('#');
    await page.locator('#team-name').fill('123');
    assert.equal(await page.locator('.spring-char').last().evaluate(el => getComputedStyle(el).animationName), 'none');
    assert.equal(await page.locator('#team-ok').getAttribute('aria-busy'), 'false');
    assert.deepEqual(errors, []);
    console.log('Spring input passed: @/#, Unicode, IME, editing, success/error, Escape, mobile, reduced motion; mocked API only.');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
