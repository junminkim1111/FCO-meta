// 테두리를 도는 빛 (border-beam 1.4.1, MIT — static/vendor/border-beam)을 React 없이 붙인다.
// wrapper의 첫 자식 테두리를 따라 돈다. 회전형(md·sm·line)만. set(true/false)로 켜고 끈다.
import { beamCss, sizePresets, sizeThemePresets } from "./vendor/border-beam/border-beam.core.js";

let count = 0;

export function mountBeam(wrapper, { size = "md", colorVariant = "colorful", theme = "dark", strength = 1 } = {}) {
  const id = "beam-" + ++count;
  const preset = sizeThemePresets[size][theme];
  const radius = parseFloat(getComputedStyle(wrapper.firstElementChild).borderTopLeftRadius) || sizePresets[size].borderRadius;
  const style = document.createElement("style");
  style.textContent = beamCss({
    id, size, colorVariant, theme,
    borderRadius: radius, borderWidth: sizePresets[size].borderWidth,
    duration: size === "line" ? 3.1 : 1.96, hueRange: size === "line" ? 13 : 30, staticColors: colorVariant === "mono",
    strokeOpacity: preset.strokeOpacity, innerOpacity: preset.innerOpacity, bloomOpacity: preset.bloomOpacity,
    innerShadow: preset.innerShadow, brightness: preset.brightness ?? 1.3, saturation: preset.saturation, glowSize: 1,
  });  // prettier-ignore
  document.head.append(style);
  wrapper.dataset.beam = id;
  wrapper.style.setProperty("--beam-strength", strength);
  const bloom = document.createElement("div");
  bloom.setAttribute("data-beam-bloom", "");
  wrapper.append(bloom);
  wrapper.addEventListener("animationend", (ev) => {
    if (ev.animationName.includes("fade-out")) wrapper.removeAttribute("data-fading");
  });
  const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
  return {
    set(on) {
      if (on && !still) {
        wrapper.removeAttribute("data-fading");
        wrapper.setAttribute("data-active", "");
      } else if (wrapper.hasAttribute("data-active")) { // 부드럽게 사라진다
        wrapper.removeAttribute("data-active");
        wrapper.setAttribute("data-fading", "");
      }
    },
  };
}
