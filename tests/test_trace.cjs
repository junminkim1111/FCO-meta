// Run with NODE_PATH pointing to an installed Playwright; no model/API calls.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    const base = process.env.FCO_TEST_URL || 'http://127.0.0.1:8765';
    await page.goto(base);
    await page.evaluate(async () => {
      const { traceBox } = await import('/static/trace.js');
      window.makeTrace = () => {
        const trace = traceBox();
        document.querySelector('#log').append(trace.element);
        return trace;
      };
      window.traceTest = makeTrace();
      traceTest.event({ kind: 'thought', text: '<script>안전한 텍스트</script>' });
      traceTest.event({ kind: 'thought', text: '\n아스널 후보를 확인합니다' });
      traceTest.event({ kind: 'tool', name: 'recommend_squad' });
      traceTest.event({ kind: 'result', name: 'recommend_squad', ok: true });
      traceTest.event({ kind: 'tool', name: 'recommend_squad' });
    });
    assert.equal(await page.locator('.trace li[data-status="done"]').count(), 2);
    assert.equal(await page.locator('.trace li[data-status="running"]').count(), 1);
    assert.equal(await page.locator('.trace script').count(), 0);
    assert.match(await page.locator('.trace-detail').first().textContent(), /안전한 텍스트.*\n아스널/);
    assert.equal(await page.locator('.trace-head .trace-time').textContent(), '0초');
    await page.waitForFunction(() => document.querySelector('.trace-head .trace-time').textContent === '1초');
    assert.equal(await page.locator('.trace-line').first().getAttribute('aria-expanded'), 'false');
    assert.equal(await page.locator('.trace-line').last().getAttribute('aria-expanded'), 'false'); // 도구는 이름 줄만 (눌러야 호출·결과가 보인다)
    assert.equal(await page.locator('.trace-detail').last().textContent(), '1 도구 호출 recommend_squad()');
    await page.screenshot({ path: '/tmp/fclm-trace-running.png', fullPage: true });
    await page.evaluate(() => {
      traceTest.event({ kind: 'result', name: 'recommend_squad', ok: true });
      traceTest.writing();
      traceTest.writing();
      traceTest.event({ kind: 'tool', name: 'check_squad' });
      traceTest.event({ kind: 'result', name: 'check_squad', ok: true });
      traceTest.writing();
      traceTest.finish('done');
    });
    assert.equal(await page.locator('.trace li').count(), 6);
    // 끝난 단계 아이콘: 생각 = 뇌(선 6개), 도구 = 렌치, 답변 = 말풍선
    assert.deepEqual(await page.locator('.trace li').evaluateAll(rows => rows.map(r => r.querySelectorAll('.trace-icon path').length)), [6, 1, 1, 1, 1, 1]);
    assert.match(await page.locator('.trace li').last().locator('path').getAttribute('d'), /^M12 3\.25A8\.75/);
    assert.equal(await page.locator('.trace details').count(), 0);
    assert.match(await page.locator('.trace-head').textContent(), /^\d+\.\d초 동안 작업 · 6단계$/);
    assert.equal(await page.locator('.trace-head').getAttribute('aria-expanded'), 'false');
    await page.locator('.trace-head').focus();
    await page.keyboard.press('Enter');
    assert.equal(await page.locator('.trace-head').getAttribute('aria-expanded'), 'true');
    assert.equal(await page.locator('.trace-label').first().textContent(), '생각함');
    assert.equal(await page.locator('.trace-label').nth(1).textContent(), '스쿼드 구성함');
    assert.equal(await page.locator('.trace li').nth(1).locator('.trace-detail').textContent(), '1 도구 호출 recommend_squad()\n2 도구 결과 recommend_squad()');
    assert.equal(await page.locator('.trace-label').last().textContent(), '답변 작성함');
    assert.equal(await page.locator('.trace-line').first().getAttribute('aria-expanded'), 'false');
    await page.locator('.trace-line').first().focus();
    await page.keyboard.press('Enter');
    assert.equal(await page.locator('.trace-line').first().getAttribute('aria-expanded'), 'true');
    await page.locator('.trace-detail').first().waitFor({ state: 'visible' });
    assert.equal(await page.locator('.trace-detail').first().isVisible(), true);
    await page.locator('.trace-line').nth(1).click();
    assert.equal(await page.locator('.trace-line').nth(1).getAttribute('aria-expanded'), 'true');
    for (const outcome of ['cancelled', 'error']) {
      await page.evaluate(outcome => { window.traceTest = makeTrace(); traceTest.finish(outcome); }, outcome);
      assert.equal(await page.locator('.trace').last().locator('[data-status="running"]').count(), 0);
      assert.match(await page.locator('.trace-detail').last().textContent(), outcome === 'cancelled' ? /중지/ : /오류/);
    }
    await page.evaluate(() => {
      window.traceTest = makeTrace();
      traceTest.event({ kind: 'tool', name: 'check_squad' });
      traceTest.event({ kind: 'result', name: 'check_squad', ok: false });
      traceTest.writing(); traceTest.finish('done');
    });
    assert.match(await page.locator('.trace').last().textContent(), /2 도구 결과 check_squad\(\) \(실패\)/);
    assert.equal(await page.locator('.trace').last().locator('[data-status="error"]').count(), 1);
    // 수치 재검토 → 다음 답 글이 오면 끝. 거절 → '답변 거절됨' (답변 작성 단계는 따로 안 생김)
    await page.evaluate(() => {
      window.traceTest = makeTrace();
      traceTest.writing();
      traceTest.event({ kind: 'recheck' });
      traceTest.writing();
      traceTest.finish('done');
      window.traceTest = makeTrace();
      traceTest.event({ kind: 'blocked' });
      traceTest.writing();
      traceTest.finish('done');
    });
    assert.deepEqual(await page.locator('.trace').nth(-2).locator('.trace-label').allTextContents(), ['생각함', '답변 작성함', '수치 재검토함', '답변 작성함']);
    assert.deepEqual(await page.locator('.trace').last().locator('.trace-label').allTextContents(), ['답변 거절됨']);
    assert.match(await page.locator('.trace').last().locator('li').last().locator('path').first().getAttribute('d'), /^M9\.75 6\.91/);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.evaluate(() => {
      window.traceTest = makeTrace();
      traceTest.event({ kind: 'thought', text: '긴한국어내용'.repeat(100) });
    });
    assert.equal(await page.locator('.trace').last().evaluate(el => el.scrollWidth <= el.clientWidth), true);
    assert.equal(await page.locator('.trace-icon').last().evaluate(el => getComputedStyle(el).animationName), 'none');
    await page.screenshot({ path: '/tmp/fclm-trace-mobile.png', fullPage: true });
    await page.goto(base);
    let requests = 0;
    await page.route('**/api/chat', async route => {
      requests++;
      assert.equal(route.request().postDataJSON().trace, requests === 1);
      const events = [
        { type: 'start', session_id: 'trace-test' },
        { type: 'trace', kind: 'thought', text: '랭커 데이터를 확인합니다' },
        { type: 'trace', kind: 'tool', name: 'recommend_players' },
        { type: 'trace', kind: 'result', name: 'recommend_players', ok: true },
        { type: 'delta', text: '테스트 답변입니다.' },
        { type: 'done', ...(requests === 1 ? {} : { error: 'unavailable' }) },
      ];
      await new Promise(resolve => setTimeout(resolve, 800)); // 기다리는 머리줄을 볼 시간
      await route.fulfill({ contentType: 'application/x-ndjson', body: events.map(e => JSON.stringify(e)).join('\n') + '\n' });
    });
    async function submit(text) {
      await page.locator('#msg').fill(text);
      await page.locator('#send').click();
    }
    await submit('아스널 선수 추천'); // /trace는 기본으로 켬 → 첫 질문에 trace=true
    await page.locator('.trace-head canvas').waitFor();
    assert.equal(await page.locator('.trace-head .shimmer').first().textContent(), '랭커데이터를 살펴보고 있어요...');
    assert.equal(await page.locator('#log .msg.pending').count(), 0);
    await page.waitForFunction(() => document.querySelector('.trace-head')?.textContent.includes('초 동안 작업'));
    assert.equal(await page.locator('.trace-head').getAttribute('aria-expanded'), 'false');
    assert.match(await page.locator('.trace').textContent(), /1 도구 호출 recommend_players\(\)\n2 도구 결과 recommend_players\(\)/);
    await page.waitForFunction(() => !document.querySelector('#send').hasAttribute('data-busy'));
    await submit('/trace'); // 켬 → 끔
    await submit('리버풀 선수 추천'); // 두 번째 질문에 trace=false
    await page.waitForFunction(() => !document.querySelector('#send').hasAttribute('data-busy'));
    assert.equal(requests, 2);
    assert.equal(await page.locator('.trace').count(), 1);
    assert.deepEqual(errors, []);
    console.log('Trace browser checks passed: repeated tools, thoughts, writing/reset, completion, cancellation, errors, visible streaming, mobile, reduced motion.');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
