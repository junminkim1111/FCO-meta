// Thinking orb (thinking-orbs 0.3.2, MIT — static/vendor/thinking-orbs) drawn on a canvas without React.
import { MODE_FRAMES, paintFrame, resolvePreset } from "./vendor/thinking-orbs/engine.es.js";

// size: 20 · 32 · 64 (라이브러리가 손으로 맞춘 크기). 멈추는 함수를 돌려준다.
export function mountOrb(canvas, state = "connecting", size = 20) {
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  canvas.width = canvas.height = Math.round(size * dpr);
  canvas.style.width = canvas.style.height = size + "px";
  const ctx = canvas.getContext("2d");
  const { mode, speed, opts } = resolvePreset(state, size);
  const draw = (t) => {
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, size, size);
    paintFrame(ctx, MODE_FRAMES[mode](size, t, opts), true);
  };
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) {
    draw(0.6);
    return () => {};
  }
  let raf = 0;
  const loop = () => {
    draw((performance.now() / 1000) * speed);
    raf = requestAnimationFrame(loop);
  };
  loop();
  return () => cancelAnimationFrame(raf);
}
