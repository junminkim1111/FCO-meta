// 테두리를 도는 빛 (border-beam 1.4.1, MIT — static/vendor/border-beam)을 React 없이 붙인다.
// wrapper의 첫 자식 테두리를 따라 돈다 (회전형 md, 무지개색). set(true/false)로 켜고 끄고, setTheme("light"|"dark")로 테마를 바꾼다.
import { beamCss, sizePresets, sizeThemePresets } from "./vendor/border-beam/border-beam.core.js";

export function mountBeam(wrapper, theme = "dark") {
  const id = "chat";
  const radius = parseFloat(getComputedStyle(wrapper.firstElementChild).borderTopLeftRadius) || sizePresets.md.borderRadius;
  const style = document.createElement("style");
  const css = (theme) => {
    const preset = sizeThemePresets.md[theme];
    return beamCss({
      id, size: "md", colorVariant: "colorful", theme,
      borderRadius: radius, borderWidth: sizePresets.md.borderWidth, duration: 1.96, hueRange: 30, staticColors: false,
      strokeOpacity: preset.strokeOpacity, innerOpacity: preset.innerOpacity, bloomOpacity: preset.bloomOpacity,
      innerShadow: preset.innerShadow, brightness: 1.3, saturation: preset.saturation, glowSize: 1,
    });  // prettier-ignore
  };
  style.textContent = css(theme);
  document.head.append(style);
  wrapper.dataset.beam = id;
  const bloom = document.createElement("div");
  bloom.setAttribute("data-beam-bloom", "");
  wrapper.append(bloom);
  wrapper.addEventListener("animationend", (ev) => {
    if (ev.animationName.includes("fade-out")) wrapper.removeAttribute("data-fading");
  });
  const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
  return {
    setTheme(theme) {
      style.textContent = css(theme);
    },
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
