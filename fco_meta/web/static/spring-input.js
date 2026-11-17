// Adapted from DesignBookmark Springy Input: https://designbookmark.com/motion-src/springy-input/
export function mountSpringInput(input, { animateTyping = true } = {}) {
  const field = input.parentElement, reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const make = (tag, cls) => { const n = document.createElement(tag); n.className = cls; field.append(n); return n; };
  const view = make('div', 'spring-view');
  const layer = document.createElement('div'); layer.className = 'spring-layer'; view.append(layer);
  const caret = make('span', 'spring-caret'), done = make('span', 'spring-done');
  view.ariaHidden = caret.ariaHidden = done.ariaHidden = 'true';
  done.textContent = '✓ 팀 첨부 완료';
  const S = (k, c) => ({ x: 0, v: 0, t: 0, k, c });
  const C = S(700, 32), W = S(300, 20), shake = S(700, 10);
  let chars = [], spans = [], frame = 0, last = 0, moved = 0, composing = false;
  function sync() {
    const next = Array.from(input.value);
    if (next.join('') === chars.join('')) return;
    let a = 0, b = 0;
    while (a < chars.length && a < next.length && chars[a] === next[a]) a++;
    while (b < chars.length - a && b < next.length - a && chars[chars.length - 1 - b] === next[next.length - 1 - b]) b++;
    spans.splice(a, chars.length - a - b).forEach(s => s.remove());
    const ref = spans[a] || null;
    const added = next.slice(a, next.length - b).map((ch, i) => {
      const s = document.createElement('span'); s.className = 'spring-char'; s.textContent = ch;
      layer.insertBefore(s, ref); return s;
    });
    spans.splice(a, 0, ...added); chars = next; moved = performance.now();
  }
  function loop(now) {
    frame = 0;
    if (!field.getClientRects().length) return;
    sync();
    const dt = Math.min((now - last) / 1000, 1 / 30); last = now;
    const i = Array.from(input.value.slice(0, input.selectionEnd ?? input.value.length)).length;
    C.t = spans[i]?.offsetLeft ?? (spans.length ? spans.at(-1).offsetLeft + spans.at(-1).offsetWidth : 0);
    for (let n = 0; n < 4; n++) for (const s of [C, W, shake]) {
      if (reduced.matches || (!animateTyping && s === C)) { s.x = s.t; s.v = 0; }
      else { s.v += (-s.k * (s.x - s.t) - s.c * s.v) * dt / 4; s.x += s.v * dt / 4; }
    }
    const scroll = input.scrollLeft;
    layer.style.transform = `translateX(${-scroll}px)`;
    const stretch = Math.min(Math.abs(C.v) / 80, 7);
    caret.style.width = `${2 + stretch}px`;
    caret.style.transform = `translateX(${C.x - scroll - (C.v > 0 ? stretch : 0)}px)`;
    caret.style.opacity = document.activeElement === input && !composing && input.selectionStart === input.selectionEnd ? 1 : 0;
    caret.classList.toggle('blink', now - moved > 550 && Math.abs(C.v) < 5);
    field.style.transform = `translateX(${shake.x}px)`;
    field.style.width = W.x > 1 ? `${W.x}px` : '';
    const moving = [C, W, shake].some(s => Math.abs(s.x - s.t) > .01 || Math.abs(s.v) > .1);
    if (moving || (!reduced.matches && document.activeElement === input && now - moved <= 550)) frame = requestAnimationFrame(loop);
  }
  function refresh() {
    sync();
    if (!frame) { last = performance.now(); frame = requestAnimationFrame(loop); }
  }
  input.addEventListener('input', refresh);
  input.addEventListener('focus', refresh);
  input.addEventListener('blur', refresh);
  input.addEventListener('scroll', refresh);
  reduced.addEventListener('change', refresh);
  input.addEventListener('compositionstart', () => { composing = true; field.classList.add('composing'); refresh(); });
  input.addEventListener('compositionend', () => { composing = false; field.classList.remove('composing'); refresh(); });
  for (const event of ['keydown', 'click', 'select']) input.addEventListener(event, () => { moved = performance.now(); refresh(); });
  field.classList.add('spring-input');
  return {
    reset() { field.classList.remove('ok', 'err'); W.x = W.v = W.t = 0; C.x = C.v = C.t = 0; refresh(); },
    loading(value) {
      const button = field.querySelector('button');
      button.setAttribute('aria-busy', String(value));
      button.setAttribute('aria-label', value ? '팀을 불러오는 중' : '팀 불러오기');
    },
    error() { field.classList.add('err'); shake.v += 420; refresh(); },
    async success() {
      W.x = field.clientWidth; W.t = Math.min(W.x, 200); W.v = 0;
      field.classList.add('ok'); input.blur(); refresh();
      await new Promise(resolve => setTimeout(resolve, reduced.matches ? 0 : 650));
    },
  };
}
