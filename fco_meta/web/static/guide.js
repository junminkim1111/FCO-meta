// 가이드 페이지 공통: 실제 화면을 HTML로 다시 그린 모형(.m-window[data-scene])을 화면에 보일 때만 반복 재생한다.
// 움직임 줄이기를 켠 사용자에게는 마지막 장면을 멈춘 채로 보여 준다. 꼬리말도 여기서 붙인다.
const REDUCE = matchMedia("(prefers-reduced-motion: reduce)").matches;

class Stop extends Error {}
const pct = (x) => (x * 100).toFixed(1) + "%";
const html = (s) => { const t = document.createElement("template"); t.innerHTML = s.trim(); return t.content.firstElementChild; };

// 장면 하나를 돌리는 도구 모음. 모든 대기는 멈춤 신호를 확인한다
function tools(win, stage, run) {
  const cursor = win.querySelector(".m-cursor");
  const wait = (ms) => new Promise((ok, no) => setTimeout(() => (run.stop ? no(new Stop()) : ok()), REDUCE ? 0 : ms));
  const t = {
    stage, wait,
    q: (sel) => stage.querySelector(sel),
    put(parent, markup, cls = "m-in") { const n = html(markup); if (cls && !REDUCE) n.classList.add(cls); parent.append(n); return n; },
    async type(el, text, ms = 48) {
      el.closest(".m-input, .m-teambox")?.querySelector(".ph")?.remove();
      if (REDUCE) { el.textContent += text; return; }
      for (const ch of text) { el.textContent += ch; await wait(ms + Math.random() * ms * 0.6); }
    },
    async erase(el, ms = 18) { while (el.textContent) { el.textContent = el.textContent.slice(0, -1); await wait(REDUCE ? 0 : ms); } },
    async point(el, dx = 0.5, dy = 0.5) { // 커서를 요소 위로
      if (REDUCE) return;
      const a = win.getBoundingClientRect(), b = el.getBoundingClientRect();
      cursor.style.opacity = 1;
      cursor.style.transform = `translate(${b.left - a.left + b.width * dx}px, ${b.top - a.top + b.height * dy}px)`;
      await wait(750);
    },
    async click() { if (REDUCE) return; cursor.classList.remove("click"); void cursor.offsetWidth; cursor.classList.add("click"); await wait(260); },
    hideCursor() { cursor.style.opacity = 0; },
  };
  return t;
}

function play(win, scene) {
  const stage = win.querySelector(".m-stage");
  win.append(html(`<svg class="m-cursor" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 2l15 9-6.5 1.6L9.6 19z" fill="#fff" stroke="#000" stroke-width="1.3"/></svg>`));
  let run = null;
  async function loop() {
    const mine = run = { stop: false };
    const t = tools(win, stage, mine);
    try {
      do {
        stage.replaceChildren(); t.hideCursor();
        await scene(t);
        if (REDUCE) return;
        await t.wait(2600);
      } while (!mine.stop);
    } catch (e) { if (!(e instanceof Stop)) console.error(e); }
  }
  new IntersectionObserver(([e]) => {
    if (e.isIntersecting && (!run || run.stop)) loop();
    else if (!e.isIntersecting && run && !REDUCE) run.stop = true;
  }, { threshold: 0.35 }).observe(win);
}

// ---------- 공통 조각 ----------
const chat = (hello = "시세와 게임 데이터는 수집 시각 기준이며 이적시장에 따라 소폭 변동될 수 있습니다. 무엇이든 물어보세요.") => `
  <div class="m-chat"><div class="m-title">FCLM<small>FC Online 랭커 데이터 기반 AI</small></div>
    <div class="m-log"><p class="m-hello">${hello}</p></div>
    <div class="m-form"><div class="m-input"><span class="chipslot"></span><span class="txt"></span><span class="m-caret"></span><span class="ph">무엇이든 물어보세요</span><span class="m-send">↑</span></div></div>
  </div>`;
const ARSENAL = [ // 2026-11-11 데이터로 받은 실제 답 (아스널 50억)
  ["ST", "티에리 앙리", "UC", 8, 34, "14억 2,000만", "53.7%"], ["ST", "이언 라이트", "SPT", 8, 33, "8억 100만", "25.0%"],
  ["LM", "부카요 사카", "25 UCL", 8, 28, "8억 1,300만", "19.7%"], ["RM", "가브리엘 마르티넬리", "25DP", 9, 28, "900만", "11.2%"],
  ["DM", "데클런 라이스", "25 UCL", 8, 28, "8억 9,200만", "72.3%"], ["DM", "수비멘디", "PTG", 9, 28, "4,580만", "47.3%"],
  ["LB", "애슐리 콜", "EVO-CU", 10, 27, "6,790만", "46.3%"], ["CB", "가브리엘 마갈량이스", "25 UCL", 8, 29, "3억 100만", "61.7%"],
];
async function ask(t, text, chip = null) {
  const input = t.q(".m-input"), txt = input.querySelector(".txt");
  await t.type(txt, text);
  await t.wait(350);
  const log = t.q(".m-log");
  t.put(log, `<div class="m-me">${chip ? `<span class="at">${chip}</span> ` : ""}${text}</div>`);
  txt.textContent = ""; input.querySelector(".chipslot").replaceChildren();
  const dots = t.put(log, `<div class="m-dots"><i></i><i></i><i></i></div>`);
  await t.wait(1300);
  dots.remove();
  return log;
}
async function attach(t, mark, value, chip) { // @·#: 칸을 열고 값을 넣어 팀을 붙인다
  const form = t.q(".m-form"), txt = t.q(".m-input .txt");
  await t.type(txt, mark); await t.wait(250); txt.textContent = "";
  t.q(".m-input .chipslot").append(html(`<span class="m-chip">${mark}</span>`));
  const box = t.put(form, `<div class="m-teambox"><span class="val"><span class="ph">${mark === "#" ? "스쿼드 피드 ID" : "구단주명"}</span><span class="v"></span></span><span class="go">→</span></div>`, "m-pop");
  await t.wait(500);
  await t.type(box.querySelector(".v"), value, 60);
  await t.point(box.querySelector(".go")); await t.click();
  const hint = t.put(box, `<span class="hint">팀을 불러오는 중…</span>`);
  await t.wait(900);
  box.remove(); hint.remove(); t.hideCursor();
  t.q(".m-input .chipslot").replaceChildren(html(`<span class="m-chip m-in">${chip}</span>`));
  await t.wait(500);
}

// ---------- 장면 ----------
const FALLBACK = { // API를 못 쓰면 (2026-11-11 기준 값)
  formations: [["4-2-2-2", .388], ["4-2-2-1-1", .233], ["4-2-3-1", .207], ["4-1-4-1", .052], ["4-1-2-3", .031]],
  teams: [["레알 마드리드", .0772], ["FC 바르셀로나", .0658], ["바이에른 뮌헨", .0627], ["첼시", .0543], ["리버풀", .0516]],
};
const cache = {};
async function json(url) { if (!(url in cache)) cache[url] = fetch(url).then((r) => (r.ok ? r.json() : null)).catch(() => null); return cache[url]; }
async function statRows() {
  const f = await json("/api/formations"), c = await json("/api/teamcolors");
  return {
    formations: f ? f.formations.slice(0, 5).map((x) => [x.formation, x.share]) : FALLBACK.formations,
    teams: c ? c.rows.slice(0, 5).map((x) => [x.team_color, x.share]) : FALLBACK.teams,
  };
}
async function detail(kind, name) {
  const d = await json(`/api/${kind}?name=${encodeURIComponent(name)}`);
  return d && d.ranking ? d : null;
}

function bars(list) { // 분포 막대 목록: fill(데이터)로 다시 그리며 자란다 (막대는 그 목록의 1위 기준, 실제 화면과 같게)
  return (data) => {
    const top = Math.max(...data.map((r) => r[1]));
    list.replaceChildren(...data.map(([n, s], i) => html(`<div class="m-row${i ? "" : " on"}"><em>${n}</em><span class="t"><i style="transform:scaleX(0)"></i></span><small>${pct(s)}</small></div>`)));
    requestAnimationFrame(() => list.querySelectorAll(".t i").forEach((b, i) => setTimeout(() => (b.style.transform = `scaleX(${data[i][1] / top})`), REDUCE ? 0 : i * 90)));
  };
}
const TABS = `<div class="m-tabs"><b class="on">포메이션 분포</b><b>팀컬러 분포</b></div>`;
async function switchTab(t, tabs, to) {
  await t.point(tabs[to]); await t.click();
  tabs[1 - to].classList.remove("on"); tabs[to].classList.add("on");
}

const SCENES = {
  async hub(t) {
    t.stage.innerHTML = chat();
    const txt = t.q(".m-input .txt");
    await t.wait(400);
    await t.type(txt, "레알 5억 미만 공격수 추천해줘"); await t.wait(1100); await t.erase(txt);
    await t.wait(300);
    t.q(".m-input .chipslot").append(html(`<span class="m-chip m-in">@구단주명</span>`));
    await t.type(txt, "내 팀 약점이 뭐야?"); await t.wait(1100); await t.erase(txt); t.q(".m-input .chipslot").replaceChildren();
    await t.wait(300);
    await attach(t, "#", "KQ221UD8K7R53LN", "#KQ221UD8K7R53LN");
    await t.type(txt, "이렇게 팀 맞추려고 하는데 어때?");
    await t.wait(1400);
  },

  async screen(t) {
    const rows = await statRows();
    t.stage.innerHTML = `<div class="m-split"><div class="m-col">
        <div class="m-panel">${TABS}<div class="rows"></div></div>
        <div class="m-panel det"></div></div>${chat()}</div>`;
    const list = t.q(".rows"), det = t.q(".det"), tabs = t.stage.querySelectorAll(".m-tabs b"), fill = bars(list);
    const show = async (kind, name) => {
      const d = await detail(kind, name), r = d && d.ranking;
      det.replaceChildren(html(`<div class="m-in"><div class="m-h">${name}<small>${r ? `사용률 ${r.usage_rank}위 · ${pct(r.share)}` : ""}</small></div>
        <div class="m-stats"><div><small>시즌 승률</small><b>${r ? pct(r.season_win_rate) : "-"}</b></div><div><small>평균 ELO</small><b>${r ? Math.round(r.avg_elo) : "-"}</b></div>
        <div><small>평균 구단가치</small><b>${r ? r.avg_squad_value || "-" : "-"}</b></div><div><small>최고 순위</small><b>${r ? r.best_rank + "위" : "-"}</b></div></div></div>`));
    };
    fill(rows.formations);
    await show("formation", rows.formations[0][0]);
    await t.wait(1200);
    const second = list.children[1];
    await t.point(second, 0.3); await t.click();
    list.querySelector(".on").classList.remove("on"); second.classList.add("on");
    await show("formation", rows.formations[1][0]);
    await t.wait(1500);
    await switchTab(t, tabs, 1);
    fill(rows.teams);
    await show("teamcolor", rows.teams[0][0]);
    t.hideCursor();
    await t.wait(1600);
  },

  async bars(t) { // 왼쪽 분포 칸만 크게: 막대가 자라고, 위 버튼으로 포메이션 ↔ 팀컬러
    const rows = await statRows();
    t.stage.innerHTML = `<div class="m-panel m-big">${TABS}<div class="rows"></div></div>`;
    const tabs = t.stage.querySelectorAll(".m-tabs b"), fill = bars(t.q(".rows"));
    fill(rows.formations);
    await t.wait(1800);
    await switchTab(t, tabs, 1); fill(rows.teams);
    await t.wait(1800);
    await switchTab(t, tabs, 0); fill(rows.formations);
    t.hideCursor();
    await t.wait(1400);
  },

  async detail(t) { // 상세를 내려 상대 포메이션별 전적까지, 막대에 커서를 올리면 경기 수·승무패
    const name = (await statRows()).formations[0][0], d = await detail("formation", name), r = d && d.ranking;
    const opp = d ? d.matchups.opponents.slice(0, 6) : [];
    const spots = [[38, 12], [62, 12], [30, 34], [70, 34], [40, 56], [60, 56], [12, 66], [36, 76], [64, 76], [88, 66], [50, 92]];
    t.stage.innerHTML = `<div class="m-panel m-scrollbox"><div class="m-scroll">
      <div class="m-h">${name}<small>${r ? `사용률 ${r.usage_rank}위 · ${pct(r.share)}` : ""}</small></div>
      <div class="m-stats"><div><small>시즌 승률</small><b>${r ? pct(r.season_win_rate) : "-"}</b></div><div><small>평균 ELO</small><b>${r ? Math.round(r.avg_elo) : "-"}</b></div>
        <div><small>평균 구단가치</small><b>${r ? r.avg_squad_value || "-" : "-"}</b></div><div><small>최고 순위</small><b>${r ? r.best_rank + "위" : "-"}</b></div></div>
      <p class="m-sub">스쿼드</p><div class="m-pitch">${spots.map(([x, y]) => `<div class="m-pl" style="left:${x}%;top:${y}%"><i></i></div>`).join("")}</div>
      <p class="m-sub mu-head">상대 포메이션별 전적</p>
      <p class="m-legend"><span><i style="background:var(--win)"></i>승</span><span><i style="background:var(--draw)"></i>무</span><span><i style="background:var(--loss)"></i>패</span></p>
      <div class="m-mu">${opp.map((o) => `<span>vs ${o.opponent}</span><span class="stack"><i style="flex:${o.wins};background:var(--win)"></i><i style="flex:${o.draws};background:var(--draw)"></i><i style="flex:${o.losses};background:var(--loss)"></i></span><small>${pct(o.win_rate)}</small>`).join("")}</div>
    </div></div>`;
    const scroll = t.q(".m-scroll"), box = t.q(".m-scrollbox");
    await t.wait(1400);
    scroll.style.transform = `translateY(${-Math.max(0, t.q(".mu-head").offsetTop - 14)}px)`;
    await t.wait(1300);
    const stacks = t.stage.querySelectorAll(".m-mu .stack");
    const tip = t.put(box, `<div class="m-tipbox" hidden></div>`, null);
    for (const i of [0, 2]) {
      if (!stacks[i]) break;
      await t.point(stacks[i], 0.45);
      const o = opp[i], a = box.getBoundingClientRect(), b = stacks[i].getBoundingClientRect();
      tip.textContent = `vs ${o.opponent}\n${o.games.toLocaleString()}경기 ${o.wins}승 ${o.draws}무 ${o.losses}패 · 승률 ${pct(o.win_rate)}`;
      tip.hidden = false; // 칸 밖으로 나가지 않게 오른쪽 끝에서 멈춘다
      tip.style.left = `${Math.min(b.left - a.left + b.width * 0.45 + 14, a.width - tip.offsetWidth - 8)}px`; tip.style.top = `${b.bottom - a.top + 6}px`;
      await t.wait(1700);
      tip.hidden = true;
    }
    t.hideCursor();
    await t.wait(600);
  },

  async ask(t) {
    t.stage.innerHTML = chat();
    await t.wait(400);
    const log = await ask(t, "아스널 50억으로 스쿼드 짜줘");
    const bot = t.put(log, `<div class="m-bot"></div>`);
    t.put(bot, `<p>아스널 랭커들이 가장 많이 쓰는 <b>4-2-2-2</b> 기반 50억 BP 스쿼드입니다. (아스널 랭커 188명, 2026-11-11 05:00 기준)</p>`);
    await t.wait(400);
    t.put(bot, `<p><b>총 급여 315 / 315 · 총액 49억 4,420만 (남은 금액 5,580만)</b></p>`);
    await t.wait(400);
    const body = t.put(bot, `<table class="m-table"><thead><tr><th>자리</th><th>선수</th><th>시즌</th><th>강화</th><th>급여</th><th>시세</th><th>사용률</th></tr></thead><tbody></tbody></table>`).querySelector("tbody");
    for (const r of ARSENAL) { t.put(body, `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`); await t.wait(170); }
    await t.wait(300);
    t.put(bot, `<p><span class="m-pill">스쿼드메이커로 보내기 ↗</span></p>`);
    await t.wait(1400);
  },

  async examples(t) {
    t.stage.innerHTML = chat();
    const txt = t.q(".m-input .txt");
    for (const q of ["라브 달린 뮌헨 윙어 추천해줘", "라이스랑 수비멘디 비교해줘", "UC 마네 대신 급여를 1 줄일 수 있는 윙어", "요즘 사용률이 가장 많이 오른 팀컬러는?"]) {
      await t.type(txt, q, 40); await t.wait(1000); if (REDUCE) return; await t.erase(txt); await t.wait(200);
    }
  },

  async team(t) {
    t.stage.innerHTML = chat();
    await t.wait(400);
    await attach(t, "@", "구단주명", "@구단주명");
    const log = await ask(t, "내 팀 약점이 뭐야?", "@구단주명");
    const bot = t.put(log, `<div class="m-bot"></div>`);
    t.put(bot, `<p>불러온 팀(최근 경기 선발 11명, 팀컬러 기준)을 랭커들의 같은 자리 선수와 비교했어요.</p>`);
    await t.wait(450);
    t.put(bot, `<p>· 사용률이 낮은 자리부터 업그레이드 후보<br>· 랭커 평균보다 급여를 많이 쓰는 자리<br>· 같은 팀컬러 랭커들이 많이 쓰는 대안 선수</p>`);
    await t.wait(1500);
  },

  async feed(t) {
    t.stage.innerHTML = chat();
    await t.wait(400);
    await attach(t, "#", "KQ221UD8K7R53LN", "#KQ221UD8K7R53LN");
    const log = await ask(t, "이 스쿼드에서 급여를 줄이려면?", "#KQ221UD8K7R53LN");
    const bot = t.put(log, `<div class="m-bot"></div>`);
    t.put(bot, `<p>피드의 선발 11명을 기준으로, 급여를 줄일 수 있는 자리와 랭커들이 쓰는 대안을 찾았어요.</p>`);
    await t.wait(1500);
  },

  async commands(t) {
    t.stage.innerHTML = chat();
    const form = t.q(".m-form"), txt = t.q(".m-input .txt");
    await t.wait(400);
    await t.type(txt, "/");
    const items = [["/model", "다음 질문을 답할 모델 (auto·gemini·flash·lite·deep)"], ["/compare", "다음 질문을 세 AI 모델이 이름을 가린 채 답하고 하나를 고름"],
                   ["/trace", "모델 선택·생각·도구 결과를 답 위에 보여 줌"], ["/cancel", "켜 둔 명령어를 모두 끄고 기본 모델로"], ["/help", "명령어와 사용법"]];
    let menu = t.put(form, `<ul class="m-menu">${items.map(([c, d]) => `<li>${c}<span>${d}</span></li>`).join("")}</ul>`, "m-pop");
    const select = (i) => menu.querySelectorAll("li").forEach((li, j) => li.classList.toggle("on", i === j));
    await t.wait(500);
    for (const i of [0, 1, 2, 1, 0]) { select(i); await t.wait(650); }
    menu.remove(); txt.textContent = "/model ";
    const models = [["auto", "기본: 질문에 맞춰 고름"], ["gemini", "자동, 실패해도 Gemini 안에서"], ["flash", "늘 3.5 Flash"], ["lite", "늘 Flash-Lite (빠르고 가볍게)"],
                    ["deep", "DeepSeek 생각 끔 (최대 5분)"], ["deep --r", "DeepSeek 생각 켬 (더 깊게)"]];
    menu = t.put(form, `<ul class="m-menu">${models.map(([c, d]) => `<li>${c}<span>${d}</span></li>`).join("")}</ul>`, "m-pop");
    for (const i of [0, 1, 2, 3, 4]) { select(i); await t.wait(i === 4 ? 900 : 380); }
    menu.remove(); txt.textContent = "/model deep";
    await t.wait(500);
    txt.textContent = "";
    t.put(t.q(".m-log"), `<p class="m-note">/model deep 적용: 다음 질문 하나만 DeepSeek V4.1 Flash(생각 끔).</p>`);
    await t.wait(1500);
  },

  async send(t) {
    t.stage.innerHTML = chat();
    const log = t.q(".m-log");
    const bot = t.put(log, `<div class="m-bot"><p><b>총 급여 315 / 315 · 총액 49억 4,420만</b></p>
      <table class="m-table"><tbody>${ARSENAL.slice(0, 4).map((r) => `<tr>${r.slice(0, 3).map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`, null);
    const btn = t.put(bot, `<p><span class="m-pill">스쿼드메이커로 보내기 ↗</span></p>`);
    await t.wait(700);
    await t.point(btn.querySelector(".m-pill")); await t.click();
    t.hideCursor();
    const tab = t.put(t.stage, `<div class="m-tab2"><div class="m-h">아스널 4-2-2-2<small>총 급여 315 · 총액 49억 4,420만</small></div><div class="m-pitch"></div>
      <div class="m-steps"><span>① 버튼을 북마크바로</span><span>② 스쿼드메이커 열기</span><span>③ 북마크 누르면 저장</span></div></div>`);
    const pitch = tab.querySelector(".m-pitch");
    const spots = [["앙리", 38, 14], ["라이트", 62, 14], ["사카", 15, 36], ["마르티넬리", 85, 36], ["라이스", 38, 56], ["수비멘디", 62, 56],
                   ["콜", 12, 72], ["마갈량이스", 38, 78], ["살리바", 62, 78], ["팀버", 88, 72], ["라야", 50, 92]];
    await t.wait(400);
    for (const [n, x, y] of spots) { t.put(pitch, `<div class="m-pl" style="left:${x}%;top:${y}%"><i></i>${n}</div>`, "m-drop"); await t.wait(110); }
    for (const s of tab.querySelectorAll(".m-steps span")) { await t.wait(600); s.classList.add("on"); }
    await t.wait(1400);
  },
};

// 좁은 화면의 가로 목차: 지금 페이지 칸이 보이게
const here = document.querySelector('.g-side [aria-current="page"]');
if (here && matchMedia("(max-width: 860px)").matches) here.parentElement.scrollLeft = here.offsetLeft - 16;
// ---------- 꼬리말 ----------
document.body.append(html(`<footer class="foot">
  <p>© 2026 FCLM. All rights reserved.</p>
  <p>FCLM은 FC Online 유저를 위한 비공식 랭커 데이터 통계 서비스입니다. FC Online 관련 데이터는 NEXON Open API(Data based on NEXON Open API)와
    FC온라인 데이터센터의 공개 정보를 기반으로 제공됩니다. FCLM은 NEXON 및 EA SPORTS와 공식적으로 관련이 없습니다.
    게임 관련 상표, 이미지, 선수 정보 및 저작권은 EA SPORTS 및 NEXON에 있습니다.</p>
  <p>질문은 AI 모델(Google Gemini·DeepSeek)로 처리되며 서비스 개선을 위해 기록됩니다.</p>
  <p class="contact">버그 / 문의사항
    <a href="https://open.kakao.com/o/s3axuDWi" target="_blank" rel="noopener noreferrer" aria-label="카카오톡 오픈채팅으로 문의하기" title="카카오톡 오픈채팅">
      <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><rect width="24" height="24" rx="6" fill="#FEE500"/>
        <path fill="#191919" d="M12 5.5c-3.9 0-7 2.4-7 5.4 0 1.9 1.3 3.6 3.2 4.6l-.7 2.5c-.1.3.2.5.4.3l3-2c.4 0 .7.1 1.1.1 3.9 0 7-2.4 7-5.5S15.9 5.5 12 5.5z"/></svg></a></p>
</footer>`));

document.querySelectorAll(".m-window[data-scene]").forEach((w) => SCENES[w.dataset.scene] && play(w, SCENES[w.dataset.scene]));
