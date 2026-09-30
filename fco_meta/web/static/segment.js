// 고무 세그먼트 버튼 — React Bits "Rubber Segment"(https://reactbits.dev/micro/rubber-segment)를 React·motion 없이 옮긴 것.
// Copyright (c) 2026 David Haz. MIT + Commons Clause: 앱·웹사이트의 일부로 쓰는 것은 허용, 컴포넌트 자체를 팔거나
// 따로 재배포하는 것은 금지. 누르면 엄지가 목표 쪽으로 늘어났다 도착하며 살짝 눌리고, 끌거나 튕겨서 옮길 수도 있다.

const EASE_OUT = (t) => 1 - (1 - t) ** 5; // cubic-bezier(0.23, 1, 0.32, 1)에 가까운 감속
const SPRING_UI = { duration: 0.3, bounce: 0 };
const SPRING_MOMENTUM = { duration: 0.4, bounce: 0.2 };
const SPRING_RELAX = { duration: 0.16, bounce: 0 };
const DILATE = 0.19, HANDOFF = 0.15, FLICK = 110, MAX_VELOCITY = 2000, DEADZONE = 4, SLOP = 10, RUBBER = 0.55;
const STRETCH = 1, SQUASH = 3, GLIDE = 75, RADIUS = 10, INSET = 3;

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
const rubber = (over, dim) => (over * dim * RUBBER) / (dim + RUBBER * Math.abs(over));
const project = (v, glide) => {
  const d = 1 - 0.1 * Math.pow(0.05, glide / 100);
  return ((v / 1000) * d) / (1 - d);
};
const velocityOf = (hist, now) => {
  const recent = hist.filter(([t]) => now - t <= 100);
  if (recent.length < 2) return 0;
  const [t0, x0] = recent[0], [t1, x1] = recent[recent.length - 1];
  return t1 - t0 >= 8 ? ((x1 - x0) / (t1 - t0)) * 1000 : 0;
};
const nearestSlot = (slots, x) => {
  let best = 0;
  for (let i = 1; i < slots.length; i++)
    if (Math.abs((slots[i].l + slots[i].r) / 2 - x) < Math.abs((slots[best].l + slots[best].r) / 2 - x)) best = i;
  return best;
};

// 움직이는 값 하나 (motion의 useMotionValue + animate 대신): 트윈 또는 스프링, 속도 추적
class Value {
  constructor(v, onChange) { this.v = v; this.onChange = onChange; this.raf = 0; this.hist = []; }
  get() { return this.v; }
  set(v) {
    this.v = v;
    const now = performance.now();
    this.hist.push([now, v]);
    if (this.hist.length > 4) this.hist.shift();
    this.onChange();
  }
  jump(v) { this.stop(); this.hist = []; this.set(v); }
  stop() { cancelAnimationFrame(this.raf); this.raf = 0; this.done?.(); this.done = null; }
  getVelocity() {
    const h = this.hist;
    if (h.length < 2 || performance.now() - h[h.length - 1][0] > 50) return 0;
    const [t0, x0] = h[0], [t1, x1] = h[h.length - 1];
    return t1 > t0 ? ((x1 - x0) / (t1 - t0)) * 1000 : 0;
  }
  // tween: {duration, ease}, spring: {duration, bounce, velocity} (px/s). 끝나면 resolve.
  animate(to, opts) {
    this.stop();
    const from = this.v, start = performance.now(), dur = opts.duration;
    let pos;
    if (opts.ease) {
      pos = (t) => (t >= dur ? null : from + (to - from) * opts.ease(t / dur));
    } else {
      const zeta = 1 - (opts.bounce || 0), wn = 4.6 / (Math.max(zeta, 0.3) * dur), a = from - to, v0 = opts.velocity || 0;
      if (zeta >= 1) {
        pos = (t) => (t > dur * 2 ? null : to + (a + (v0 + wn * a) * t) * Math.exp(-wn * t));
      } else {
        const wd = wn * Math.sqrt(1 - zeta * zeta), b = (v0 + zeta * wn * a) / wd;
        pos = (t) => (t > dur * 2.5 ? null : to + Math.exp(-zeta * wn * t) * (a * Math.cos(wd * t) + b * Math.sin(wd * t)));
      }
    }
    return new Promise((resolve) => {
      this.done = resolve;
      const step = () => {
        const t = (performance.now() - start) / 1000;
        let x = pos(t);
        if (x !== null && !opts.ease && t >= dur && Math.abs(x - to) < 0.3) x = null; // 스프링이 거의 멈췄으면 끝
        if (x === null) { this.raf = 0; this.set(to); this.done = null; resolve(); return; }
        this.set(x);
        this.raf = requestAnimationFrame(step);
      };
      this.raf = requestAnimationFrame(step);
    });
  }
}

/**
 * container 안에 세그먼트를 그린다. items: [{value, label}], onChange(value, index).
 * 돌려주는 객체의 set(value)로 바깥에서 선택을 바꿀 수 있다 (애니메이션 없이).
 */
export function mountSegment(container, items, { value = items[0].value, onChange, ariaLabel = "보기 전환" } = {}) {
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const thumbRadius = Math.max(0, RADIUS - INSET);
  const track = document.createElement("div");
  track.className = "rubber-segment";
  track.setAttribute("role", "radiogroup");
  track.setAttribute("aria-label", ariaLabel);
  track.dataset.equal = "";
  track.dataset.draggable = "";
  const buttons = items.map((item) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "rubber-segment__item";
    b.setAttribute("role", "radio");
    b.textContent = item.label;
    return b;
  });
  const thumb = document.createElement("div");
  thumb.className = "rubber-segment__thumb";
  thumb.setAttribute("aria-hidden", "true");
  for (const item of items) {
    const s = document.createElement("span");
    s.className = "rubber-segment__item rubber-segment__copy";
    s.textContent = item.label;
    thumb.append(s);
  }
  track.append(...buttons, thumb);
  container.replaceChildren(track);

  let index = Math.max(0, items.findIndex((i) => i.value === value));
  let committed = index, handoff = 0, drag = null, gen = 0, slots = [], box = null, innerW = 0, frame = 0;
  const paint = () => {
    if (frame) return;
    frame = requestAnimationFrame(() => {
      frame = 0;
      thumb.style.clipPath =
        `inset(0 ${Math.max(0, innerW - edgeR.get())}px 0 ${Math.max(0, edgeL.get())}px round ${thumbRadius}px)`;
    });
  };
  const edgeL = new Value(0, paint), edgeR = new Value(0, paint);

  const sync = () => buttons.forEach((b, i) => {
    b.setAttribute("aria-checked", String(i === index));
    b.tabIndex = i === index ? 0 : -1;
  });
  const jumpTo = (i) => {
    const s = slots[i];
    if (!s) return;
    clearTimeout(handoff);
    gen += 1;
    edgeL.jump(s.l);
    edgeR.jump(s.r);
  };
  const measure = () => {
    const rect = track.getBoundingClientRect();
    box = rect;
    slots = buttons.map((b) => {
      const r = b.getBoundingClientRect();
      return { l: r.left - rect.left - INSET, r: r.right - rect.left - INSET };
    });
    innerW = rect.width - INSET * 2;
    jumpTo(committed);
  };
  const commit = (i) => {
    committed = i;
    if (i === index) return;
    index = i;
    sync();
    onChange?.(items[i].value, i);
  };
  const land = (to, v, flick, withSquash) => {
    const b = slots[to];
    if (!b) return;
    const g = ++gen;
    const dir = Math.sign((b.l + b.r) / 2 - (edgeL.get() + edgeR.get()) / 2) || 1;
    const [lead, leadTo, trail, trailTo] = dir > 0 ? [edgeR, b.r, edgeL, b.l] : [edgeL, b.l, edgeR, b.r];
    const vel = (mv) => clamp(v === null ? mv.getVelocity() : v, -MAX_VELOCITY, MAX_VELOCITY);
    const trailVelocity = vel(trail);
    lead.animate(leadTo, { ...(flick ? SPRING_MOMENTUM : SPRING_UI), velocity: vel(lead) });
    if (!withSquash) { trail.animate(trailTo, { ...SPRING_UI, velocity: trailVelocity }); return; }
    trail.animate(trailTo + dir * SQUASH, { ...SPRING_UI, velocity: trailVelocity }).then(() => {
      if (gen === g) trail.animate(trailTo, SPRING_RELAX);
    });
  };
  const travel = (from, to) => {
    const a = slots[from], b = slots[to];
    if (!a || !b) return;
    clearTimeout(handoff);
    gen += 1;
    if (reduce) { edgeL.jump(b.l); edgeR.jump(b.r); return; }
    const tween = { duration: DILATE, ease: EASE_OUT };
    edgeL.animate(b.l + (Math.min(a.l, b.l) - b.l) * STRETCH, tween);
    edgeR.animate(b.r + (Math.max(a.r, b.r) - b.r) * STRETCH, tween);
    handoff = setTimeout(() => land(to, null, false, true), HANDOFF * 1000);
  };
  const localX = (e) => e.clientX - (box ? box.left : 0) - INSET;

  buttons.forEach((b, i) => {
    b.addEventListener("pointerdown", (e) => {
      if (drag || e.button !== 0) return;
      box = track.getBoundingClientRect();
      try { b.setPointerCapture(e.pointerId); } catch { /* 무시 */ }
      const x = localX(e);
      const onThumb = x >= edgeL.get() && x <= edgeR.get();
      drag = { id: e.pointerId, x0: x, slot: i, onThumb, live: false, offset: 0, w: 0, hist: [[e.timeStamp, x]] };
      if (onThumb) { clearTimeout(handoff); gen += 1; edgeL.stop(); edgeR.stop(); }
      else if (!reduce) b.dataset.pressed = "";
    });
    b.addEventListener("keydown", (e) => {
      const last = items.length - 1;
      const next = { ArrowRight: index + 1, ArrowDown: index + 1, ArrowLeft: index - 1, ArrowUp: index - 1, Home: 0, End: last }[e.key];
      if (next === undefined) return;
      e.preventDefault();
      const to = clamp(next, 0, last);
      if (to === index) return;
      commit(to);
      jumpTo(to);
      buttons[to].focus();
    });
  });
  track.addEventListener("pointermove", (e) => {
    const d = drag;
    if (!d || e.pointerId !== d.id || !d.onThumb) return;
    const x = localX(e);
    d.hist.push([e.timeStamp, x]);
    if (d.hist.length > 8) d.hist.shift();
    if (!d.live) {
      if (Math.abs(x - d.x0) < DEADZONE) return;
      d.live = true;
      d.offset = x - edgeL.get();
      d.w = edgeR.get() - edgeL.get();
      track.dataset.held = "";
    }
    const l = x - d.offset, maxL = innerW - d.w;
    if (reduce) { const c = clamp(l, 0, maxL); edgeL.set(c); edgeR.set(c + d.w); }
    else if (l < 0) { edgeL.set(0); edgeR.set(d.w - rubber(-l, d.w)); }
    else if (l > maxL) { edgeR.set(innerW); edgeL.set(maxL + rubber(l - maxL, d.w)); }
    else { edgeL.set(l); edgeR.set(l + d.w); }
  });
  const release = () => {
    const d = drag;
    drag = null;
    delete track.dataset.held;
    delete buttons[d.slot].dataset.pressed;
    return d;
  };
  track.addEventListener("pointerup", (e) => {
    const d = drag;
    if (!d || e.pointerId !== d.id) return;
    release();
    const x = localX(e);
    if (!d.live) {
      if (Math.abs(x - d.x0) <= SLOP && d.slot !== committed) {
        const from = committed;
        commit(d.slot);
        travel(from, d.slot);
      }
      return;
    }
    const v = velocityOf(d.hist, e.timeStamp);
    const flick = Math.abs(v) > FLICK;
    let to = nearestSlot(slots, (edgeL.get() + edgeR.get()) / 2 + project(v, GLIDE));
    if (flick && to === committed) to = clamp(to + Math.sign(v), 0, items.length - 1);
    commit(to);
    if (reduce) jumpTo(to);
    else land(to, v, flick, flick);
  });
  const cancel = (e) => {
    const d = drag;
    if (!d || e.pointerId !== d.id) return;
    release();
    if (!d.live) return;
    if (reduce) jumpTo(committed);
    else land(committed, null, false, false);
  };
  track.addEventListener("pointercancel", cancel);
  track.addEventListener("lostpointercapture", cancel);

  sync();
  measure();
  new ResizeObserver(measure).observe(track);
  document.fonts?.ready.then(measure);
  return { set(v) { const i = items.findIndex((it) => it.value === v); if (i >= 0) { commit(i); jumpTo(i); } } };
}
