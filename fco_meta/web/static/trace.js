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

// 끝난 단계 아이콘 (24×24 선 아이콘): [위치 보정, ...path]
const ICONS = {
  think: ["0 0.13", "M12 4.7A3.5 3.5 0 0 1 18.49 6.76A3 3 0 0 1 20.16 12.07A3.25 3.25 0 0 1 17.99 17.75A3.25 3.25 0 0 1 12 19.23A3.25 3.25 0 0 1 6.01 17.75A3.25 3.25 0 0 1 3.85 12.07A3 3 0 0 1 5.51 6.76A3.5 3.5 0 0 1 12 4.7Z",
          "M12 4.7V19.2", "M4.7 12.07C6.9 12.07 8.5 11 8.75 9.25", "M19.3 12.07C17.1 12.07 15.5 11 15.25 9.25", "M6.8 17.4C8.25 16.75 9 15.75 9 14.25", "M17.2 17.4C15.75 16.75 15 15.75 15 14.25"],
  tool: ["-0.95 0.95", "M18.03 3.64C16.07 2.66 13.7 3.01 12.1 4.52C10.51 6.02 10.03 8.37 10.9 10.38L5.58 15.7C4.83 16.45 4.83 17.66 5.58 18.42C6.33 19.17 7.55 19.17 8.3 18.42L13.62 13.1C15.63 13.97 17.98 13.49 19.48 11.9C20.99 10.3 21.34 7.93 20.36 5.97L17.25 9.08L14.92 6.75L18.03 3.64Z"],
  write: ["-0.05 0", "M12 3.25A8.75 8.75 0 1 1 7.9 19.7L3.5 20.5L4.3 16.1A8.75 8.75 0 0 1 12 3.25Z"],
  recheck: ["0.03 0.35", "M19.05 14.56A7.5 7.5 0 1 1 17.3 6.7L18.96 8.36", "M19.43 3.82L19.43 6.87C19.43 8.23 18.84 8.82 17.48 8.82L14.43 8.82"],
  failed: ["0 0", "M12 2.75A9.25 9.25 0 1 1 12 21.25A9.25 9.25 0 1 1 12 2.75Z", "M9.25 9.25L14.75 14.75", "M14.75 9.25L9.25 14.75"], // 실패한 단계 (close-circle-rounded)
  stopped: ["0 0", "M12 2.75A9.25 9.25 0 1 1 12 21.25A9.25 9.25 0 1 1 12 2.75Z", "M12 11V16.5", "M12 7.75h.01"], // 사용자가 멈춘 단계 (info-circle-rounded)
  blocked: ["0 -0.18", "M9.75 6.91C11.32 4.17 12.68 4.17 14.25 6.91L19.26 15.6C20.83 18.33 20.15 19.5 17 19.5L7 19.5C3.85 19.5 3.17 18.33 4.74 15.6L9.75 6.91Z", "M12 9V13", "M12 16.25h.01"],
};
// 도구 단계 이름: 하는 동안 "… 중...", 끝나면 "…함"
const TOOL_LABELS = {
  resolve_terms: "팀컬러·포지션 이름 확인", list_available_data: "데이터 범위 확인", list_formations: "포메이션 사용률 집계",
  recommend_players: "선수 추천", recommend_squad: "스쿼드 구성", squad_candidates: "자리별 후보 수집", check_squad: "급여·조건 검사",
  diagnose_team: "팀 진단", get_player_detail: "선수 기록 조회", query_squads: "랭커 스쿼드 집계", query_rankers: "랭킹 집계",
  get_formation_overview: "포메이션 전적 조회", get_team_color_overview: "팀컬러 랭커 조회", get_team_color_info: "팀컬러 정보 조회",
  get_meta_trends: "메타 흐름 비교",
};

export function traceBox() {
  const box = node("div", "trace"), list = node("ol", "trace-steps");
  box.setAttribute("aria-label", "모델의 사고과정");
  const header = node("button", "trace-head");
  const whole = disclosure(header, list);
  box.append(header, whole.fold);
  const started = performance.now();
  // 작업 중 머리줄: head에 기다리는 문구(오브)를 넣고, 옆에 1초마다 오르는 시간
  const head = node("span", "trace-wait"), clock = node("span", "trace-time", "0초");
  header.append(head, clock);
  const tick = setInterval(() => { clock.textContent = `${Math.floor((performance.now() - started) / 1000)}초`; }, 1000);
  const steps = [];
  let ended = false, writing = null;
  function status(step, value) {
    step.status = value;
    step.row.dataset.status = value;
    step.icon.replaceChildren();
    if (value !== "running") {
      const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("viewBox", "0 0 24 24");
      const [shift, ...paths] = ICONS[value === "error" ? "failed" : value === "stopped" ? "stopped" : step.kind];
      const g = document.createElementNS(svg.namespaceURI, "g");
      g.setAttribute("transform", `translate(${shift})`);
      for (const d of paths) {
        const path = document.createElementNS(svg.namespaceURI, "path");
        path.setAttribute("d", d);
        path.setAttribute("pathLength", "1");
        g.append(path);
      }
      svg.append(g);
      step.icon.append(svg);
      step.time.textContent = `${((performance.now() - step.started) / 1000).toFixed(1)}초`;
      step.label.textContent = step.done;
      step.open(false);
    }
  }
  // kind = 끝났을 때 아이콘. done = 끝난 이름 ("생각중..." → "생각함")
  function add(label, kind, name = null, done = label.replace(/ ?중\.\.\.$/, "함")) {
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
    const step = { row, icon, label: text, kind, done, time, detail, open, line, name, started: performance.now() };
    steps.push(step);
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
  add("생각중...", "think");
  return {
    element: box,
    head,
    event(ev) {
      if (ended) return;
      if (ev.kind === "thought") {
        let step = steps.at(-1);
        if (step.name || step.status !== "running" || step === writing) {
          closeTransient();
          step = add("생각중...", "think");
        }
        log(step, ev.text, true);
      } else if (ev.kind === "tool") {
        closeTransient();
        const step = add(`${TOOL_LABELS[ev.name] || "도구 호출"} 중...`, "tool", ev.name);
        log(step, `1 도구 호출 ${ev.name}()`);
        step.open(false); // 도구는 이름 줄만 (눌러야 호출·결과가 보인다)
      } else if (ev.kind === "result") {
        const step = steps.find((s) => s.name === ev.name && s.status === "running");
        if (step) {
          status(step, ev.ok ? "done" : "error");
          log(step, `\n2 도구 결과 ${ev.name}()${ev.ok ? "" : " (실패)"}`, true);
        }
      } else if (ev.kind === "recheck") { // 답의 수치를 고쳐 쓰는 중 (다음 답 글이 오면 끝)
        closeTransient();
        add("수치 재검토 중...", "recheck");
      } else if (ev.kind === "blocked") { // 범위 밖·프롬프트 공격: 모델 없이 거절 문구가 답 글 대신
        if (steps.length === 1 && !steps[0].detail.textContent) steps.pop().row.remove(); // 생각한 적 없으니 첫 '생각중...'을 뺀다
        closeTransient();
        writing = add("답변 거절 중...", "blocked", null, "답변 거절됨");
      }
    },
    writing() {
      if (ended || writing?.status === "running") return;
      closeTransient();
      writing = add("답변 작성 중...", "write");
    },
    finish(outcome) {
      if (ended) return;
      ended = true;
      clearInterval(tick);
      box.classList.add("trace-done");
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
