// 금속 링 (metal-fx 1.0.4 = libraries.dev/metal v1, MIT — static/vendor/metal-fx)을 React 없이 원형 버튼에 붙인다.
// 원본 컴포넌트와 같은 구조를 만든다: 루트 > 캔버스(링) · 안쪽 · 빛 번짐 · 내용(버튼). chromatic 색, 테마는 setMetalTheme.
import {
  buildGlow, createInstance, onFrame, paintGlow, queueGlow, setSharedPreset, setVisible, updateInstance,
} from "./vendor/metal-fx/metal-fx.core.js";

const glows = new Map(); // 인스턴스 → 빛 번짐 핸들 (엔진이 프레임마다 부른다)
const roots = new Set();
let theme = "dark";
onFrame((inst, t) => {
  const glow = glows.get(inst);
  if (glow) paintGlow(glow, inst, t, inst.opacityMul, theme);
});

// "light" | "dark": 링 색(공유 프리셋)과 루트의 data-theme을 함께 바꾼다
export function setMetalTheme(next) {
  theme = next;
  try {
    setSharedPreset("chromatic", theme);
  } catch {} // WebGL 없음
  for (const root of roots) root.dataset.theme = theme;
}

const part = (tag, className, style) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.style.cssText = style;
  return node;
};

// button을 금속 링 루트로 감싸 제자리에 넣고 루트를 돌려준다. WebGL이 안 되면 버튼만 그대로 보인다.
export function mountMetal(button) {
  const root = part("div", "metal-fx-root", "opacity:0;visibility:hidden");
  Object.assign(root.dataset, { variant: "circle", shape: "circle", theme, normalize: "true" });
  roots.add(root);
  root.style.setProperty("--mfx-strength", "1");
  const canvas = part("canvas", "metal-fx-canvas", "position:absolute;inset:0;width:100%;height:100%");
  const glow = part("div", "", "position:absolute;inset:0;pointer-events:none;z-index:3;border-radius:inherit");
  const content = part("div", "metal-fx-content", "");
  button.replaceWith(root);
  content.append(button);
  root.append(canvas, part("div", "metal-fx-inner", "position:absolute;inset:3px"), glow, content);
  const show = () => { root.style.cssText += ";opacity:1;visibility:visible;transition:opacity .15s ease-out"; };
  try {
    setSharedPreset("chromatic", theme);
    const box = root.getBoundingClientRect();
    const w = Math.max(1, Math.round(box.width)), h = Math.max(1, Math.round(box.height)), radius = Math.min(w, h) / 2;
    const inst = createInstance({ hostCanvas: canvas, cssWidth: w, cssHeight: h, cornerRadius: radius, kind: "circle",
                                  paused: matchMedia("(prefers-reduced-motion: reduce)").matches, onFirstCopy: show });  // prettier-ignore
    root.style.setProperty("--mfx-radius", `${radius}px`);
    root.style.borderRadius = `${radius}px`;
    glows.set(inst, buildGlow(glow, { width: w, height: h, cornerRadius: radius, kind: "circle", scale: 1 }));
    queueGlow(inst);
    updateInstance(inst, { opacityMul: 1 });
    new IntersectionObserver((es) => es.forEach((e) => setVisible(inst, e.isIntersecting)), { rootMargin: "64px" }).observe(root);
  } catch {
    show(); // WebGL을 못 쓰는 브라우저: 금속 효과 없이 버튼만
  }
  return root;
}
