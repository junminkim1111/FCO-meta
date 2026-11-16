// Adapted from Kokonut UI AI Loading v2.0.0 (MIT).
// Copyright (c) 2025 kokonutUI — see trace.LICENSE.txt.
const node = (tag, cls, text) => {
  const item = document.createElement(tag);
  if (cls) item.className = cls;
  if (text !== undefined) item.textContent = text;
  return item;
};

function disclosure(button, content) {
  const fold = node("div", "trace-fold");
  content.id = `trace-${crypto.randomUUID()}`;
  fold.append(content);
  button.type = "button";
  button.setAttribute("aria-controls", content.id);
  const open = value => {
    button.setAttribute("aria-expanded", String(value));
    fold.inert = !value;
  };
  button.addEventListener("click", () => open(button.getAttribute("aria-expanded") !== "true"));
  open(true);
  return { fold, open };
}

export function traceBox() {
  const box = node("div", "trace"), list = node("ol", "trace-steps");
  box.setAttribute("aria-label", "모델의 사고과정");
  const header = node("button", "trace-head");
  const whole = disclosure(header, list);
  box.append(header, whole.fold);
  const started = performance.now();
  const steps = [];
  let ended = false, writing = null;
  function status(step, value) {
    step.status = value;
    step.row.dataset.status = value;
    step.icon.replaceChildren();
    if (value === "done") {
      const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("viewBox", "0 0 16 16");
      const path = document.createElementNS(svg.namespaceURI, "path");
      path.setAttribute("d", "M3.5 8.5 6.5 11.5 12.5 4.5");
      path.setAttribute("pathLength", "1");
      svg.append(path);
      step.icon.append(svg);
    } else if (value === "error" || value === "stopped") step.icon.textContent = value === "error" ? "×" : "−";
    if (value !== "running") {
      step.time.textContent = `${((performance.now() - step.started) / 1000).toFixed(1)}초`;
      step.label.textContent = step.text;
      step.open(false);
    }
  }
  function add(label, name = null) {
    const row = node("li"), icon = node("span", "trace-icon"), body = node("div", "trace-body");
    const line = node("button", "trace-line"), text = node("span", "trace-label"), time = node("span", "trace-time");
    const detail = node("div", "trace-detail");
    const { fold, open } = disclosure(line, detail);
    line.disabled = true;
    icon.setAttribute("aria-hidden", "true");
    for (const [i, char] of Array.from(label).entries()) {
      const letter = node("span", "", char);
      letter.style.animationDelay = `${i * .05}s`;
      text.append(letter);
    }
    line.append(text, time);
    body.append(line, fold);
    row.append(icon, body);
    list.append(row);
    const step = { row, icon, label: text, text: label, time, detail, open, line, name, started: performance.now() };
    steps.push(step);
    header.textContent = `${steps.length}단계`;
    status(step, "running");
    return step;
  }
  function log(step, text, append = false) {
    step.detail.textContent = append ? step.detail.textContent + text : text;
    step.line.disabled = !step.detail.textContent;
  }
  function closeTransient() {
    for (const step of steps) if (step.status === "running" && !step.name) status(step, "done");
  }
  add("생각중...");
  return {
    element: box,
    event(ev) {
      if (ended) return;
      if (ev.kind === "thought") {
        let step = steps.at(-1);
        if (step.name || step.status !== "running" || step === writing) {
          closeTransient();
          step = add("생각중...");
        }
        log(step, ev.text, true);
      } else if (ev.kind === "tool") {
        closeTransient();
        const step = add("도구 호출", ev.name);
        log(step, `1 도구 호출 ${ev.name}()`);
      } else if (ev.kind === "result") {
        const step = steps.find((s) => s.name === ev.name && s.status === "running");
        if (step) {
          status(step, ev.ok ? "done" : "error");
          log(step, `\n2 도구 결과 ${ev.name}()${ev.ok ? "" : " (오류)"}`, true);
        }
      }
    },
    writing() {
      if (ended || writing?.status === "running") return;
      closeTransient();
      writing = add("답변 작성 중...");
    },
    finish(outcome) {
      if (ended) return;
      ended = true;
      for (const step of steps) if (step.status === "running") {
        const value = outcome === "done" && !step.name ? "done" : outcome === "cancelled" ? "stopped" : "error";
        status(step, value);
        if (value !== "done") log(step, (step.detail.textContent ? "\n" : "") + (value === "stopped" ? "답변을 중지했습니다." : "처리 중 오류가 발생했습니다."), true);
      }
      const elapsed = ((performance.now() - started) / 1000).toFixed(1);
      header.textContent = outcome === "done" ? `${elapsed}초 동안 작업 · ${steps.length}단계` : `${outcome === "cancelled" ? "작업 중지" : "작업 오류"} · ${steps.length}단계`;
      if (outcome === "done") whole.open(false);
    },
  };
}
