
import { mountSpringInput } from "/static/spring-input.js";
import { traceBox } from "/static/trace.js";
import { mountOrb } from "/static/orb.js"; // 생각 중 오브
import { renderMarkdown } from "/static/md.js"; // 답의 마크다운 표시
// 답 끝의 "다음 질문: …" 줄은 답에서 빼고 입력창에 흐리게 채운다 (Tab으로 그대로 보낸다) → [본문, 다음 질문 또는 null]
const NEXT_Q = /(?:\n|[ \t])[ \t>*_#-]*다음 질문\s*[:：][ \t*_]*([^\n]*?)[ \t*_]*$/;
const CHOICES = /(?:^|\n)[ \t>*_#-]*선택지\s*[:：][ \t*_]*([^\n]*?)[ \t*_]*(?=\n|$)/;
const splitChoices = (text) => { const m = text.match(CHOICES); if (!m) return [text, null]; const c = m[1].split("|").map((x) => x.replace(/^[ "“'*_]+|[ "”'*_]+$/g, "")).filter(Boolean); return [text.slice(0, m.index) + text.slice(m.index + m[0].length), c.length ? c : null]; };
const splitNext = (text) => { const m = text.match(NEXT_Q); return m ? [text.slice(0, m.index), m[1].replace(/^["“]|["”]$/g, "").trim() || null] : [text, null]; };
import { mountBeam } from "/static/beam.js"; // 답을 기다리는 동안 입력창 테두리를 도는 빛
const $ = (id) => document.getElementById(id);
const beam = mountBeam($("msg-beam"));

const el = (tag, props = {}, ...children) => {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") node.className = v; else if (k === "text") node.textContent = v; else node[k] = v;
  }
  for (const c of children) if (c != null) node.append(c);
  return node;
};
const pct = (x) => (x * 100).toFixed(1) + "%";
const season = (s) => (s || "?").split(" (")[0];

async function failure(res) {
  const d = (await res.json().catch(() => ({}))).detail;
  const err = new Error(typeof d === "string" ? d : Array.isArray(d) ? d.map((e) => e.msg).join(", ") : `오류 (${res.status})`);
  err.status = res.status;
  return err;
}
async function api(path, options) {
  const res = await fetch(path, options);
  if (!res.ok) throw await failure(res);
  return res.json();
}

// --- tooltip ---
const tip = $("tip");
function showTip(ev, text) {
  tip.textContent = text;
  tip.hidden = false;
  const r = tip.getBoundingClientRect();
  const x = Math.min(ev.clientX + 12, window.innerWidth - r.width - 8);
  const y = ev.clientY - r.height - 10 < 8 ? ev.clientY + 16 : ev.clientY - r.height - 10;
  tip.style.left = Math.max(8, x) + "px";
  tip.style.top = y + "px";
}
const hideTip = () => { tip.hidden = true; };
const hover = (node, text) => {
  node.tabIndex = 0;
  node.setAttribute("aria-describedby", "tip");
  const show = () => {
    const r = node.getBoundingClientRect();
    showTip({ clientX: r.left, clientY: r.bottom + 24 }, text);
  };
  node.addEventListener("focus", show);
  node.addEventListener("blur", hideTip);
  node.addEventListener("click", show);
  node.addEventListener("keydown", (ev) => { if (ev.key === "Escape") hideTip(); });
  node.addEventListener("mousemove", (ev) => showTip(ev, text));
  node.addEventListener("mouseleave", hideTip);
  return node;
};

// --- 분포: 포메이션 / 팀컬러 (위 전환 버튼) ---
const MAX_BARS = 5;  // 더보기를 누르기 전에 보이는 순위
const MODES = {
  formation: {
    list: "/api/formations", rows: (d) => d.formations || [], key: (r) => r.formation,
    more: 15,  // 더보기를 누르면 이 순위까지
    detail: (name) => "/api/formation?" + new URLSearchParams({ name }),
  },
  team: {
    list: "/api/teamcolors", rows: (d) => d.rows || [], key: (r) => r.team_color, icon: (r) => r.icon,
    more: 20,
    detail: (name) => "/api/teamcolor?" + new URLSearchParams({ name }),
  },
};
let mode = "formation";
// 국가 팀컬러는 "브라질(국가)"로 오지만 화면에는 이름만 (조회할 때는 클럽과 구분되도록 원래 이름을 쓴다)
const plain = (name) => name.replace(/\(국가\)$/, "");
let selected = null;

function markSelected() {
  for (const b of $("fchart").querySelectorAll(".row")) b.classList.toggle("sel", b.dataset.key === selected);
}

// 팀컬러 엠블럼·국기 (넥슨 CDN). 모르거나 못 불러오면 같은 크기 빈 자리로 둬서 이름 줄을 맞춘다
function logo(path) {
  if (!path) return el("span", { class: "logo" });
  const img = el("img", { class: "logo", src: `${IMG}/${path}`, alt: "", loading: "lazy" });
  img.addEventListener("error", () => { img.style.visibility = "hidden"; });
  return img;
}

async function loadChart() {
  const m = MODES[mode], want = mode, box = $("fchart");
  box.ariaBusy = "true";
  try {
    const d = await api(m.list);
    if (mode !== want) return;  // 그사이 전환했으면 버린다
    const all = m.rows(d);
    if (!all.length) {
      box.replaceChildren(el("p", { class: "stat", text: d.note || "랭킹 스냅샷이 없습니다." }));
      return;
    }
    const shown = all.slice(0, m.more);
    box.classList.remove("open");
    const max = shown[0].share || 1;
    const rows = [];
    for (const [i, r] of shown.entries()) {
      const key = m.key(r);
      const row = el("button", { type: "button", class: i < MAX_BARS ? "row" : "row extra", ariaLabel: `${plain(key)}, ${pct(r.share)} — 누르면 상세` },
        el("span", { class: "f" }, m.icon ? logo(m.icon(r)) : null, plain(key)),
        el("span", { class: "track" }, el("span", { style: `width:${(r.share / max) * 100}%` })),
        el("span", { class: "v", text: pct(r.share) }));
      row.dataset.key = key;
      row.addEventListener("click", () => showDetail(key));
      rows.push(row);
    }
    if (shown.length > MAX_BARS) {
      const more = el("button", { type: "button", class: "more", text: "더보기" });
      more.addEventListener("click", () => { more.textContent = box.classList.toggle("open") ? "접기" : "더보기"; });
      rows.push(more);
    }
    box.replaceChildren(...rows);
    showDetail(m.key(shown[0]));
  } catch (e) {
    box.replaceChildren(el("p", { class: "error", text: e.message }));
  } finally {
    if (mode === want) box.ariaBusy = "false";
  }
}

// 보기 전환 토글 (분포·스쿼드 같은 모양): 누르면 그 쪽이 밝아지고 onChange
function mountPills(container, items, { value = items[0].value, onChange, ariaLabel = "보기 전환" } = {}) {
  const seg = el("div", { class: "seg", role: "radiogroup", ariaLabel });
  const set = (v) => buttons.forEach((b, i) => { b.classList.toggle("on", items[i].value === v); b.setAttribute("aria-checked", String(items[i].value === v)); b.tabIndex = items[i].value === v ? 0 : -1; });
  const buttons = items.map((item, i) => {
    const b = el("button", { type: "button", text: item.label, role: "radio" });
    b.addEventListener("click", () => { if (b.classList.contains("on")) return; set(item.value); onChange?.(item.value); });
    b.addEventListener("keydown", (ev) => {
      const delta = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[ev.key];
      if (!delta && ev.key !== "Home" && ev.key !== "End") return;
      ev.preventDefault();
      const next = ev.key === "Home" ? 0 : ev.key === "End" ? buttons.length - 1 : (i + delta + buttons.length) % buttons.length;
      buttons[next].focus(); buttons[next].click();
    });
    return b;
  });
  seg.append(...buttons);
  container.replaceChildren(seg);
  set(value);
}

// --- formation detail ---
// 선수 사진: 카드 사진 → 선수 기본 사진 → 이름 첫 글자 (넥슨 이미지 CDN을 못 쓰면 글자로 표시)
const IMG = "https://fco.dn.nexoncdn.co.kr/live/externalAssets/common";
function photo(p) {
  const box = el("div", { class: "ph", ariaHidden: "true" });
  const sources = [`${IMG}/playersAction/p${p.sp_id}.png`, `${IMG}/players/p${p.pid}.png`];
  const next = () => {
    const src = sources.shift();
    if (!src) { box.replaceChildren((p.name || "?").slice(0, 1)); return; }
    const img = el("img", { src, alt: "", loading: "lazy" });
    img.onerror = next;
    box.replaceChildren(img);
  };
  next();
  const g = p.grade, tier = g >= 11 ? "platinum" : g >= 8 ? "gold" : g >= 5 ? "silver" : g >= 2 ? "bronze" : "";
  return el("div", { class: "pw" }, box, g ? el("span", { class: "gr " + tier, text: String(g) }) : null);
}

// spposition 코드 → 경기장 위치 [x%, y%] (위쪽이 공격 방향, 오른쪽이 R 포지션)
const POS = {
  0: [50, 91], 1: [50, 80], 2: [88, 57], 3: [85, 73], 4: [66, 78], 5: [50, 79], 6: [34, 78], 7: [15, 73], 8: [12, 57],
  9: [64, 62], 10: [50, 63], 11: [36, 62], 12: [86, 44], 13: [68, 48], 14: [50, 49], 15: [32, 48], 16: [14, 44],
  17: [70, 32], 18: [50, 33], 19: [30, 32], 20: [66, 20], 21: [50, 21], 22: [34, 20],
  23: [84, 17], 24: [63, 10], 25: [50, 10], 26: [37, 10], 27: [16, 17],
};
const POS_NAME = ["GK", "SW", "RWB", "RB", "RCB", "CB", "LCB", "LB", "LWB", "RDM", "CDM", "LDM", "RM", "RCM", "CM", "LCM", "LM",
  "RAM", "CAM", "LAM", "RF", "CF", "LF", "RW", "RS", "ST", "LS", "LW"];

function pitch(players, emptyText, detailOf) {
  const box = el("div", { class: "pitch" }, el("div", { class: "mid" }), el("div", { class: "circle" }),
    el("div", { class: "box top" }), el("div", { class: "box bottom" }));
  if (!players || !players.length) {
    box.append(el("div", { class: "empty-pitch", text: emptyText }));
    return box;
  }
  for (const p of players) {
    const [x, y] = POS[p.position] || [50, 50];
    const short = (p.name || "?").trim().split(" ").pop(); // DB 이름 끝에 공백이 붙은 선수가 있다 ("A. 우에다 ")
    const icon = p.season_img ? el("img", { src: p.season_img, alt: "", loading: "lazy", onerror() { this.remove(); } }) : null;
    const node = el("div", { class: "pp", style: `left:${x}%;top:${y}%` }, photo(p), el("div", { class: "nm" }, icon, short));
    hover(node, `${POS_NAME[p.position] || ""} ${p.name || "?"}\n${season(p.season)}${p.grade ? ` · ${p.grade}강` : ""}${detailOf(p)}`);
    box.append(node);
  }
  return box;
}

function tile(k, val, sub) {
  return el("div", { class: "tile" }, el("div", { class: "k", text: k }), el("div", { class: "val", text: val }),
            sub ? el("div", { class: "sub", text: sub }) : null);
}

// text의 숫자만 위로 굴러가 멈춘다. from이 있으면 그 숫자에서 출발 (같은 자리 숫자). 오른쪽 자리일수록 오래 구른다
function roll(text, from = "") {
  const box = el("span", { class: "roll", ariaLabel: text });
  const digits = [...text].filter((c) => /\d/.test(c)).length;
  const old = [...from].filter((c) => /\d/.test(c)).reverse();
  let k = digits;
  for (const c of text) {
    if (!/\d/.test(c)) { box.append(el("span", { text: c, ariaHidden: "true" })); continue; }
    k--;
    const strip = el("span", {}, ...Array.from({ length: 20 }, (_, i) => el("span", { text: String(i % 10) })));
    const start = Number(old[k] ?? 0), end = 10 + Number(c);
    strip.style.transform = `translateY(${-start * 1.2}em)`;
    strip.style.setProperty("--t", `${0.7 + (digits - k) * 0.12}s`);
    requestAnimationFrame(() => requestAnimationFrame(() => (strip.style.transform = `translateY(${-end * 1.2}em)`)));
    box.append(el("span", { class: "digit", ariaHidden: "true" }, strip));
    if (k === 0) strip.addEventListener("transitionend", () => box.replaceWith(text), { once: true }); // 다 멈추면 보통 글자로
  }
  return box;
}

function renderPitchArea(d) {
  const area = el("div", { style: "margin-top:24px" }); // 위 타일과 띄운다 (전에는 "스쿼드" 제목이 띄웠다)
  const top = d.top_ranker_squad, best = d.best_eleven;
  const what = d.formation ? "이 포메이션을 쓰는" : "이 팀컬러";
  const views = {
    top: () => {
      if (!top) return [el("p", { class: "caption", text: `스쿼드 수집 랭커 중 ${what} 랭커가 아직 없습니다.` }),
                        pitch([], "매일 수집으로 스쿼드가 쌓이면 표시됩니다", () => "")];
      // 포메이션 보기는 팀컬러를, 팀컬러 보기는 그 경기 배치를 함께 보여 준다
      const side = d.formation ? top.team_color || "팀컬러 미상" : top.played_formation || "배치 미상";
      // 구단가치 = 선발 11명 최신 시세 합. 시세를 모르는 선수가 있으면 그만큼 빠져 있어 "이상"
      const value = top.squad_value ? ` · 구단가치 ${top.squad_value}${top.unpriced ? " 이상" : ""}` : "";
      const out = [el("p", { class: "caption", text: `${top.rank}위 랭커 · ${side}${value} · ELO ${Math.round(top.elo)}` })];
      if (d.formation && !top.formation_match)
        out.push(el("div", { class: "notice", text: `이 경기 실제 배치는 ${top.played_formation || "알 수 없음"}입니다 (랭킹 표시는 ${d.formation}).` }));
      out.push(pitch(top.players, "", (p) => (p.rating != null ? ` · 평점 ${p.rating}` : "")));
      return out;
    },
    best: () => {
      if (!best) return [el("p", { class: "caption", text: d.formation ? "이 포메이션으로 실제 경기한 스쿼드가 아직 없습니다." : "스쿼드 수집 랭커 중 이 팀컬러 랭커가 아직 없습니다." }),
                         pitch([], "매일 수집으로 스쿼드가 쌓이면 표시됩니다", () => "")];
      // 최상위 랭커 스쿼드의 캡션 줄만큼 띄워 버튼과 경기장 사이 간격을 맞춘다
      return [el("p", { class: "caption", text: "\u00a0" }), pitch(best.players, "", () => "")];
    },
  };
  const body = el("div"), seg = el("div");
  const show = (key) => body.replaceChildren(...views[key]());
  const first = top ? "top" : "best";
  mountPills(seg, [{ value: "top", label: "최상위 랭커 스쿼드" }, { value: "best", label: "랭커 베스트 11" }],
             { value: first, onChange: show, ariaLabel: "스쿼드 보기" }); // 분포 전환과 같은 알약 토글
  area.append(seg, body);
  show(first);
  return area;
}

function renderMatchups(m) {
  if (!m.opponents.length)
    return [el("p", { class: "stat", text: "아직 이 포메이션의 경기 기록이 없습니다. 매일 약 300경기씩 쌓입니다." })];
  const rows = [];
  for (const o of m.opponents.slice(0, 8)) {
    const stack = el("div", { class: "stack", role: "img", ariaLabel: `${o.wins}승 ${o.draws}무 ${o.losses}패` });
    for (const [n, color] of [[o.wins, "var(--win)"], [o.draws, "var(--draw)"], [o.losses, "var(--loss)"]])
      if (n) stack.append(el("span", { style: `flex:${n};background:${color}` }));
    rows.push(el("span", { class: "f", text: "vs " + o.opponent }),
              hover(stack, `vs ${o.opponent}\n${o.games}경기 ${o.wins}승 ${o.draws}무 ${o.losses}패 · 승률 ${pct(o.win_rate)}`),
              el("span", { class: "v", text: pct(o.win_rate) }));
  }
  return [
    el("div", { class: "legend" }, el("span", {}, el("i", { style: "background:var(--win)" }), "승"),
       el("span", {}, el("i", { style: "background:var(--draw)" }), "무"), el("span", {}, el("i", { style: "background:var(--loss)" }), "패")),
    el("div", { class: "mu" }, ...rows),
  ];
}

async function showDetail(name) {
  showSquad(null); // 위에서 항목을 고르면 열려 있던 스쿼드는 접힌다
  selected = name;
  markSelected();
  const want = mode, isForm = mode === "formation";
  const box = $("fdetail");
  if (box.childElementCount) box.ariaBusy = "true"; // 이미 보이는 내용은 흐리게만 두고 그 자리를 지킨다
  else box.replaceChildren(el("p", { class: "stat", text: `${plain(name)} 불러오는 중…` }));
  try {
    const d = await api(MODES[mode].detail(name));
    if (selected !== name || mode !== want) return;  // 그사이 다른 막대를 눌렀거나 전환했으면 버린다
    const r = d.ranking;
    const out = [el("div", { class: "fd-head" }, el("h2", { text: isForm ? name : plain(d.team_color) }),
      el("span", { class: "stat", text: r ? `사용률 ${r.usage_rank}위 · ${pct(r.share)}` : (d.note || "") }))];
    if (r) {
      const avg = isForm ? d.all_formations_win_rate : d.all_rankers_win_rate;
      const diff = avg != null ? (r.season_win_rate - avg) * 100 : null;
      out.push(el("div", { class: "tiles" },
        tile("시즌 승률", pct(r.season_win_rate), diff != null ? `전체 평균 대비 ${diff >= 0 ? "+" : ""}${diff.toFixed(1)}%p` : ""),
        tile("평균 ELO", String(Math.round(r.avg_elo)), ""),
        tile("평균 구단가치", r.avg_squad_value || "-", ""),
        tile("최고 순위", `${r.best_rank}위`, "")));
    }
    out.push(renderPitchArea(d));
    if (isForm) out.push(el("h3", { text: "상대 포메이션별 전적" }), ...renderMatchups(d.matchups));
    if (r) {
      // 포메이션 보기: 그 포메이션을 쓰는 팀컬러 / 팀컬러 보기: 그 팀컬러가 쓰는 포메이션
      const [title, list] = isForm ? ["많이 쓰는 팀컬러", d.team_colors.map((t) => [t.team_color, t])]
                                   : ["많이 쓰는 포메이션", d.formations.map((f) => [f.formation, f])];
      const top5 = el("div", { class: "rows" }, ...list.flatMap(([label, t]) =>
        [el("span", { text: label }), el("span", { class: "v", text: pct(t.share) })]));
      const maxBand = Math.max(...d.rank_bands.map((b) => b.share_of_band), 0.0001);
      const bands = el("div", { class: "band" }, ...d.rank_bands.flatMap((b) => [
        el("span", { text: b.rank_band }),
        hover(el("div", { class: "track" }, el("span", { style: `width:${(b.share_of_band / maxBand) * 100}%` })),
              `${b.rank_band}\n구간의 ${pct(b.share_of_band)}`),
        el("span", { class: "v", text: pct(b.share_of_band) })]));
      out.push(el("div", { class: "two" },
        el("div", {}, el("h3", { text: title }), top5),
        el("div", {}, el("h3", { text: "순위 구간별 사용 비율" }), bands)));
    }
    box.replaceChildren(...out);
  } catch (e) {
    box.replaceChildren(el("p", { class: "error", text: e.message }));
  } finally {
    if (selected === name && mode === want) box.ariaBusy = "false";
  }
}

// --- chat ---
let sessionId = null;
const say = (who, text) => {
  const node = el("div", { class: "msg " + who, text });
  $("log").append(node);
  $("log").scrollTop = $("log").scrollHeight;
  return node;
};
// 질문을 채팅창 맨 위로 (LLM 서비스처럼): #log 아래 여백을 늘려 올릴 자리를 만들고, 답이 길어지는 만큼 여백을 줄인다
let pinned = null;
const PIN_GAP = 4; // 맨 위로 올린 질문과 채팅창 위 가장자리 사이
function fillPin() {
  const log = $("log");
  if (!pinned || !log.lastElementChild) return 0;
  const room = log.clientHeight - PIN_GAP - (log.lastElementChild.getBoundingClientRect().bottom - pinned.getBoundingClientRect().top); // 맨 아래로 내려도 질문이 위에서 PIN_GAP 아래에 오게
  log.style.paddingBottom = `${Math.max(12, room)}px`; // 12 = 원래 아래 여백
  return room;
}
// trace 상자가 접히는 등 질문 아래 높이가 줄면 여백이 모자라 질문이 아래로 밀린다: 다시 맞추고 질문을 맨 위로 되돌린다 (사용자가 직접 스크롤하기 전, 답이 아직 화면보다 짧을 때만)
let pinFree = false, pinReady = false; // pinReady: 올라가는 애니메이션이 끝난 뒤부터 되돌린다 (도중에 끼어들면 툭 튄다)
const holdPin = new ResizeObserver(() => {
  const log = $("log");
  if (!pinned || pinFree || !pinReady || fillPin() < 12) return;
  log.scrollTop += pinned.getBoundingClientRect().top - log.getBoundingClientRect().top - PIN_GAP;
});
$("log").addEventListener("wheel", () => { pinFree = true; }, { passive: true });
$("log").addEventListener("touchmove", () => { pinFree = true; }, { passive: true });
function pinQuestion(q, ...below) {
  const log = $("log");
  pinned = q;
  pinFree = false;
  pinReady = false;
  setTimeout(() => { pinReady = true; }, 700);
  holdPin.disconnect();
  below.forEach((e) => e && holdPin.observe(e));
  fillPin();
  log.scrollTo({ top: log.scrollTop + q.getBoundingClientRect().top - log.getBoundingClientRect().top - PIN_GAP,
                 behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
}
const THINKING = "랭커데이터를 살펴보고 있어요...";
const RECHECK = "보다 정확한 답을 드리기 위해 수치를 재검토 중이에요. 잠시만 기다려 주세요.";
// 오래 걸리면 도구별 문구 대신: 1분이 넘으면 둘 중 하나(질문마다 무작위), 1분 30초가 넘으면 마지막 문구
const LONG_WAIT = ["열심히 찾아보는 중이에요. 조금만 기다려 주세요...", "확실한 답을 드리기 위해 노력하고 있어요. 조금만 기다려 주세요..."];
const ALMOST = "거의 다 찾아봤어요. 잠시만 기다려 주세요...";
// 모델이 도구를 부르면 기다리는 문구를 그 도구에 맞게 바꾼다 (a = 도구 인자)
const tc = (a) => (a.team_color ? a.team_color + " " : "");
const TOOL_STATUS = {
  resolve_terms: () => "팀컬러·포지션 이름을 확인하고 있어요...",
  list_available_data: () => "수집된 데이터 범위를 확인하고 있어요...",
  list_formations: (a) => `${tc(a)}포메이션 사용률을 세고 있어요...`,
  recommend_players: (a) => `${tc(a)}랭커들이 쓰는 선수를 추리고 있어요...`,
  recommend_squad: (a) => `${tc(a)}예산에 맞춰 스쿼드를 짜고 있어요...`,
  squad_candidates: (a) => `${tc(a)}자리별 후보와 기록을 모으고 있어요...`,
  check_squad: () => "고른 선수들의 급여·조건을 검사하고 있어요...",
  diagnose_team: (a) => `${tc(a)}랭커들과 팀을 자리별로 비교하고 있어요...`,
  get_player_detail: (a) => `${a.player || a.name ? (a.player || a.name) + " " : ""}선수 기록을 살펴보고 있어요...`,
  query_squads: () => "랭커 스쿼드를 조건대로 집계하고 있어요...",
  query_rankers: () => "상위 10,000명 랭킹을 집계하고 있어요...",
  get_formation_overview: (a) => `${a.formation || "포메이션"} 전적을 살펴보고 있어요...`,
  get_team_color_overview: (a) => `${a.team_color || "팀컬러"} 랭커들을 살펴보고 있어요...`,
  get_team_color_info: (a) => `${a.team_color || a.player || a.effect || ""} 팀컬러 정보를 찾고 있어요...`,
  get_meta_trends: () => "요즘 메타 흐름을 비교하고 있어요...",
  check_numbers: () => RECHECK, // 답에 조회 결과에 없는 수치가 있어 고쳐 쓰는 중
};
// 명령어 상태 (새로고침하면 처음으로): model = 모델 선택창에서 고른 것 (새로고침 전까지), /trace = 사고과정 표시 (한 번 더 치면 끔)
let model = null, traceOn = true; // { key: "gpt" }, null이면 기본 (GPT-6 Luna). /trace는 기본으로 켬 (한 번 더 치면 끔, 새로고침하면 다시 켬)
// 선택창의 모델: key → [서버 mode, 이름, 로고(static/vendor/lobehub-icons/)]. 둘 다 생각 켬 (2026-11-20 DeepSeek Eco도 켬으로: OpenGateway 할인가라 켬의 추가 비용이 작다)
const MODELS = { gpt: ["luna_r", "GPT-6 Luna", "openai"], deepseek: ["deep_r", "DeepSeek V4.1 Flash Eco", "deepseek"] }; // 선택창 순서
const DEFAULT_MODEL = "gpt"; // 서버 mode auto가 답하는 모델 (선택창에 '기본' 표시)
const modelText = (m) => `model: ${MODELS[m.key][1]}`;
const modeOf = (m) => (m ? MODELS[m.key][0] : "auto"); // 서버에 보낼 mode
// 클로즈드 베타: 서버가 켜져 있다고 알리면 키(localStorage)를 X-Beta-Key로 붙이고, 키가 없으면 질문·팀 불러오기 때 입력창을 띄운다
let beta = false, betaClosed = false, betaKey = "";
try { betaKey = localStorage.getItem("fclm-beta-key") || ""; } catch {}
const setBetaKey = (v) => { betaKey = v; try { v ? localStorage.setItem("fclm-beta-key", v) : localStorage.removeItem("fclm-beta-key"); } catch {} };
const needKey = () => beta && !betaClosed && !betaKey;
const headers = () => ({ "Content-Type": "application/json", ...(betaKey && { "X-Beta-Key": betaKey }) });
fetch("/api/visit", { method: "POST", keepalive: true, headers: headers(), body: JSON.stringify({ path: location.pathname, ref: document.referrer }) }).catch(() => {}); // 방문 통계 (IP·UA는 서버가 해시로만)
function askKey(note = "") {
  const dialog = $("beta-dialog");
  $("beta-note").textContent = note; $("beta-key").value = "";
  if (!dialog.open) dialog.showModal();
  $("beta-key").focus();
}
// 서버가 키를 거절했다 → 저장된 키를 지우고 다시 묻는다
const rejectedKey = (note) => { setBetaKey(""); askKey(note); };
$("beta-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const value = $("beta-key").value.trim();
  if (!value) return;
  $("beta-ok").disabled = true;
  try {
    const res = await fetch("/api/beta/check", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ key: value }) });
    if (res.ok) { setBetaKey(value); $("beta-dialog").close(); $("msg").focus(); return; }
    $("beta-note").textContent = (await res.json().catch(() => ({}))).detail || "키를 확인하지 못했어요.";
  } catch { $("beta-note").textContent = "연결하지 못했어요."; }
  finally { $("beta-ok").disabled = false; }
});
$("beta-cancel").addEventListener("click", () => $("beta-dialog").close());
$("beta-change").addEventListener("click", (ev) => { ev.preventDefault(); askKey(); });
fetch("/api/beta").then((r) => r.json()).then((b) => { beta = b.beta; betaClosed = b.closed; $("beta-change-wrap").hidden = !beta; }).catch(() => {});
// 답을 기다리는 동안 보내기 버튼은 정지 버튼이 되고, 누르면 그 요청을 취소한다
let inflight = null;
function setBusy(ctrl) {
  inflight = ctrl;
  setHint(ctrl ? "" : DEFAULT_HINT); // 답을 만드는 동안엔 비우고, 끝나면 기본 안내 (send가 답의 다음 질문으로 덮어쓴다). 무작위 예시는 대화 전 첫 화면만
  beam.set(!!ctrl);
  $("send").type = ctrl ? "button" : "submit";
  $("send").toggleAttribute("data-busy", !!ctrl); // 위쪽 화살표 ↔ 정지 사각형
  $("send").setAttribute("aria-label", ctrl ? "답변 정지" : "보내기");
}
$("send").addEventListener("click", (ev) => {
  if (!inflight) return;
  ev.preventDefault(); // 취소하면 버튼이 곧바로 '보내기'로 돌아오므로, 이 클릭이 폼 제출까지 되지 않게 막는다
  inflight.abort();
});
// 반짝이는 문구. 새로 만들어도 빛이 지나가는 위치가 처음으로 튀지 않게 같은 시계에 맞춘다
const shimmer = (text, cls = "") => {
  const node = el("span", { class: ("shimmer " + cls).trim(), text });
  node.style.animationDelay = `${-(performance.now() % 2200)}ms, 0s`;
  return node;
};
// 답을 기다리는 말풍선: 오브 + "랭커데이터를 살펴보고 있어요...". 멈추는 함수를 돌려준다
function thinking(bubble) {
  bubble.className = "msg bot pending";
  bubble.title = "답을 만드는 동안 왼쪽 포메이션을 둘러보세요";
  return orbStatus(bubble);
}
function orbStatus(box) {
  const canvas = el("canvas", { ariaHidden: "true" });
  box.replaceChildren(canvas, el("span", { class: "status" }, shimmer(THINKING)));
  return mountOrb(canvas, "connecting", 20);
}
// 서버는 답을 몇 단어씩 묶어 보낸다(/api/chat, 한 줄에 JSON 이벤트 하나). 받은 글은 프레임마다 몇 글자씩 풀어 그린다
async function send(text) {
  if (inflight) return;
  $("msg").rows = 1; // 질문을 보내면 입력창은 한 줄로
  const ctrl = new AbortController();
  setBusy(ctrl);
  document.querySelector("#log .quiz:not(.leaving)")?.remove(); // 카드에서 보낸 것이면 카드가 스스로 접히며 사라진다
  document.querySelector("#log .msg.intro")?.remove(); // 시세 안내 문구는 첫 질문 전까지만
  const question = mention(say("me", text));
  const used = model, mode = modeOf(used); // 답 도중에 모델을 바꿔도 이 답의 표시는 그대로
  const trace = traceOn ? traceBox() : null;
  if (trace) $("log").append(trace.element);
  const bubble = el("div");
  $("log").append(bubble);
  pinQuestion(question, trace?.element, bubble);
  // /trace를 켜면 오브와 기다리는 문구는 말풍선 대신 trace 머리줄에 (답이 다 써질 때까지)
  const head = trace?.head, stopHead = head && orbStatus(head);
  const wait = () => head ? (bubble.className = "", bubble.replaceChildren(), () => {}) : thinking(bubble);
  let traceOutcome = "error";
  const finishTrace = () => { stopHead?.(); trace?.finish(ctrl.signal.aborted ? "cancelled" : traceOutcome); };
  let stopOrb = wait(), answer = "", shown = 0, frame = 0, answerId = null, squad = false, built = null, mine = null;
  const draw = () => {
    shown = Math.min(answer.length, shown + Math.max(2, Math.ceil((answer.length - shown) / 12))); // 밀리면 빨리 따라잡는다
    const log = $("log"), stick = log.scrollHeight - log.scrollTop - log.clientHeight < 40; // 위로 올려 읽는 중이면 끌어내리지 않는다
    renderMarkdown(bubble, splitNext(splitChoices(answer.slice(0, shown))[0])[0]);
    // 답이 아직 화면보다 짧으면(여백이 남으면) 질문 고정은 holdPin이 맡는다 — 여기서 맨 아래로 내리면 둘이 몇 px씩 서로 당겨 화면이 떨린다
    const fits = fillPin() > 12;
    if (!stick) $("jump").hidden = false;
    else if (!fits) log.scrollTop = log.scrollHeight;
    frame = shown < answer.length ? requestAnimationFrame(draw) : 0;
    if (!frame && !inflight) { $("jump").hidden = true; finishTrace(); } // 답이 다 써지면 '…'를 지운다
  };
  // 기다리는 문구: 도구에 맞는 문구, 1분·1분 30초가 넘으면 오래 걸린다는 안내 (수치 재검토 안내는 그대로)
  const started = performance.now(), longWait = LONG_WAIT[Math.floor(Math.random() * LONG_WAIT.length)];
  let waitLine = THINKING;
  const status = (line) => {
    waitLine = line;
    const waited = (performance.now() - started) / 1000;
    const text = line === RECHECK ? line : waited >= 90 ? ALMOST : waited >= 60 ? longWait : line;
    const box = (head || bubble).querySelector(".status"), node = box?.lastElementChild; // 답이 나오기 시작했으면 없다
    if (!node || node.textContent === text) return;
    node.className = "shimmer out"; // 이전 문구는 흐려지며 사라지고 새 문구가 떠오른다
    node.ariaHidden = "true";
    node.addEventListener("animationend", (e) => e.animationName === "status-out" && node.remove());
    box.append(shimmer(text, "fresh"));
  };
  const timers = [60, 90].map((sec) => setTimeout(() => status(waitLine), sec * 1000));
  const grow = (piece) => {
    if (stopOrb) { stopOrb(); stopOrb = null; bubble.className = "msg bot arrive"; bubble.removeAttribute("title"); bubble.replaceChildren(); }
    answer += piece;
    if (!frame) frame = requestAnimationFrame(draw);
  };
  try {
    const res = await fetch("/api/chat", { method: "POST", headers: headers(), signal: ctrl.signal,
                                           body: JSON.stringify({ message: text, session_id: sessionId, mode, trace: !!trace }) });
    if (!res.ok) throw await failure(res);
    const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
    let rest = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      const lines = (rest + value).split("\n");
      rest = lines.pop();
      for (const line of lines) {
        const ev = JSON.parse(line);
        if (trace && ev.type === "trace") trace.event(ev); // 서버가 보낸 도구 호출·결과 확인 줄만
        // 왼쪽 스쿼드 칸: 스쿼드 도구가 돌기 시작하면 빈 경기장부터 열고, 짤 때마다 선수를 채운다
        // (사용자가 닫았거나 다른 스쿼드를 보는 중이면 그대로 둔다)
        const ours = !mine || shownSquad === mine;
        if (ev.type === "tool" && SQUAD_STEPS.includes(ev.name) && ours) {
          if (!mine) {
            // 이미 짠 스쿼드가 열려 있으면 그걸 고쳐 짠다 (바뀐 선수만 교체). 아니면 빈 경기장부터
            if (shownSquad?.players.length) mine = shownSquad, squadStatus(TOOL_STATUS[ev.name](ev.args || {}));
            else showSquad(mine = { players: [] }, { status: TOOL_STATUS[ev.name](ev.args || {}) });
            // 왼쪽에서 어딜 보고 있었든 스쿼드 칸을 화면 가운데로 (모바일은 칸이 챗봇 아래라 답을 읽던 자리를 지킨다)
            if (matchMedia("(min-width: 861px)").matches)
              $("squad-panel").scrollIntoView({ block: $("squad-panel").offsetHeight > innerHeight - 32 ? "start" : "center", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
          }
          else squadStatus(TOOL_STATUS[ev.name](ev.args || {}));
        }
        if (ev.type === "squad") {
          if (ours) showSquad(ev, { status: "스쿼드를 다듬고 있어요...", prev: mine });
          mine = built = ev;
        }
        if (ev.type === "delta" && !answer && mine && shownSquad === mine) squadStatus("설명을 쓰고 있어요...", false);
        if (ev.type === "start") sessionId = ev.session_id ?? sessionId;
        else if (ev.type === "done") { answerId = ev.id || null; squad = !!ev.squad; traceOutcome = ev.error ? "error" : "done"; } // 끝까지 받은 답에만 온다 (좋아요·아쉬워요가 이 id로 간다)
        else if (ev.type === "delta") { trace?.writing(); grow(ev.text); }
        else if (ev.type === "tool" && stopOrb) status(TOOL_STATUS[ev.name]?.(ev.args || {}) || THINKING); // 기다리는 중이면 문구를 그 도구에 맞게
        else if (ev.type === "reset" && !stopOrb) { // 도구를 부르기 전에 쓴 글 → 지우고 다시 기다린다
          cancelAnimationFrame(frame);
          answer = "", shown = 0, frame = 0;
          stopOrb = wait();
          status(THINKING); // 1분이 넘었으면 오래 걸린다는 안내로
        }
      }
    }
  } catch (e) {
    traceOutcome = "error";
    // 질문 수 제한(429)은 안내 문구 그대로, 그 밖의 실패는 "오류:"를 붙여 보여 준다
    if (!ctrl.signal.aborted) grow((answer ? "\n\n" : "") + ([401, 403, 429].includes(e.status) ? "" : "오류: ") + e.message);
    if (e.status === 401 && beta) { if (!$("msg").value) $("msg").value = text; rejectedKey(e.message); } // 거절당한 질문 글은 입력창에 되돌려 둔다
  }
  timers.forEach(clearTimeout);
  setBusy(null);
  if (!frame) $("jump").hidden = true;
  if (mine && shownSquad === mine) built || mine.players.length ? squadStatus(null) : showSquad(null); // 다 짰거나 원래 있던 스쿼드면 진행 문구만 떼고, 빈 칸이면 닫는다
  if (ctrl.signal.aborted && !answer) {
    stopOrb();
    bubble.removeAttribute("title");
    bubble.className = "msg bot cancelled";
    bubble.textContent = "답변을 취소했습니다.";
    finishTrace();
    return;
  }
  if (ctrl.signal.aborted) grow("\n\n*(여기서 멈췄습니다)*");
  else if (!answer) grow("답을 받지 못했습니다.");
  const [body, choices] = splitChoices(answer);
  const next = !ctrl.signal.aborted && splitNext(body)[1];
  if (next) setHint(next, true); // 다음 질문이 없으면 기본 안내 그대로 (대화와 상관없는 예시를 띄우지 않는다)
  if (used) bubble.after(el("div", { class: "applied", text: modelText(used) }));
  if (answerId && !ctrl.signal.aborted && Math.random() < RATE_CHANCE) bubble.after(rateRow(answerId));
  if (answerId && squad && !ctrl.signal.aborted) bubble.after(feedRow(answerId, built));
  if (choices && !ctrl.signal.aborted) bubble.after(choiceRow(choices));
  if (!frame) finishTrace();
}
// 되묻는 답 아래 선택지 카드(Questionnaire): 라디오 목록 + 맨 끝 "기타" 인라인 입력 + 보내기. 1~n 단축키는 카드 안에 포커스가 있을 때만.
// 보내면 접히며 사라지고, 다음 질문을 입력창으로 보내도 사라진다
function choiceRow(choices) {
  const form = el("form", { class: "quiz", noValidate: true });
  const radios = [], rows = [];
  const err = el("div", { role: "alert" });
  const add = (label, extra) => {
    const radio = el("input", { type: "radio", name: "quiz" });
    const row = el("label", { class: "q-row" }, radio, el("span", { class: "q-dot", ariaHidden: "true" }),
                   el("kbd", { text: String(rows.length + 1) }), extra);
    radios.push(radio); rows.push(row);
    return radio;
  };
  choices.forEach((c) => {
    const [label, desc] = c.split(/\s+[—–]\s+/, 2);
    const radio = add(label, el("span", { class: "q-text" }, el("span", { class: "q-label", text: label }), desc ? el("span", { class: "q-desc", text: desc }) : null));
    radio.value = label;
  });
  const other = el("input", { type: "text", class: "q-input", placeholder: "직접 입력…", autocomplete: "off", ariaLabel: "기타(직접 작성)" });
  const otherRadio = add("", other);
  other.addEventListener("input", () => { otherRadio.checked = true; });
  other.addEventListener("focus", () => { otherRadio.checked = true; });
  const submit = el("button", { type: "submit", class: "q-submit", text: "보내기" });
  form.append(el("div", { class: "q-in" },
    el("fieldset", {}, el("legend", { text: "선택해 주세요" }), ...rows),
    el("div", { class: "q-err" }, el("div", {}, err)),
    el("div", { class: "q-actions" }, submit)));
  const fail = (msg) => { err.textContent = ""; void err.offsetWidth; err.textContent = msg; form.classList.add("has-err"); };
  const clear = () => { form.classList.remove("has-err"); };
  form.addEventListener("change", clear);
  other.addEventListener("input", clear);
  const go = () => {
    if (inflight) return;
    const i = radios.findIndex((r) => r.checked);
    const text = i < 0 ? "" : i === radios.length - 1 ? other.value.trim() : radios[i].value;
    if (!text) return fail(i < 0 ? "선택지를 하나 골라 주세요." : "내용을 입력해 주세요.");
    form.classList.add("leaving");
    setTimeout(() => form.remove(), 250);
    send(text);
  };
  form.addEventListener("submit", (ev) => { ev.preventDefault(); go(); });
  form.addEventListener("keydown", (ev) => {
    if (ev.isComposing || ev.altKey) return;
    if (ev.key === "Enter" && ev.target.type !== "submit") { ev.preventDefault(); go(); return; }
    if (ev.target === other || ev.ctrlKey || ev.metaKey || !/^[1-9]$/.test(ev.key)) return;
    const r = radios[+ev.key - 1];
    if (!r) return;
    ev.preventDefault();
    r.checked = true; r.focus(); clear();
    if (r === otherRadio) other.focus();
  });
  return form;
}
// 스쿼드 답 아래 "스쿼드메이커로 내보내기": 새 탭의 안내 페이지(/send)에서 넥슨 스쿼드메이커 내 보관함에 저장하는 법을 보여 준다
function feedRow(id, sq) {
  const row = el("div", { class: "rate feed" },
                 el("a", { href: "/send?id=" + encodeURIComponent(id), target: "_blank", rel: "noopener", text: "스쿼드메이커로 내보내기 ↗" }));
  if (sq) {
    const b = el("button", { type: "button", class: "squad-toggle" });
    b.squad = sq;
    b.addEventListener("click", () => showSquad(shownSquad === sq ? null : sq));
    row.append(b);
    syncSquadButtons(b);
  }
  return row;
}
// 왼쪽 스쿼드 칸: 챗봇이 짠 스쿼드를 랭커 스쿼드와 같은 경기장에 그린다. null이면 닫고 상세 칸을 다시 보인다
let shownSquad = null;
const SQUAD_STEPS = ["squad_candidates", "recommend_squad", "check_squad"];
// prev: 바로 앞에 그린 스쿼드. 처음이면 골키퍼부터 한 명씩, 고쳐 짠 거면 바뀐 선수만 새로 들어온다
// 모바일(한 줄로 쌓인 화면): 스쿼드 칸은 챗봇 바로 아래 따로 뜨고(상세 칸을 가리지 않는다), 화면 밖이면 "아래에서 확인" 말풍선
const NARROW = matchMedia("(max-width: 860px)");
const squadHint = el("button", { type: "button", class: "squad-hint", text: "아래에서 스쿼드를 확인해보세요! ↓", hidden: true });
document.body.append(squadHint);
squadHint.addEventListener("click", () => { squadHint.hidden = true; $("squad-panel").scrollIntoView({ behavior: "smooth", block: "start" }); });
let hintY = 0;
addEventListener("scroll", () => { if (!squadHint.hidden && scrollY > hintY + 24) squadHint.hidden = true; }, { passive: true }); // 아래로 내리면 사라진다
function showSquadHint() {
  if (!NARROW.matches || $("squad-panel").getBoundingClientRect().top < innerHeight * .6) return; // 칸이 화면 위쪽까지 올라와 있으면(이미 보이면) 띄우지 않는다
  hintY = scrollY; squadHint.hidden = false;
}
function showSquad(sq, { status = null, prev = null } = {}) {
  const panel = $("squad-panel"), opening = sq && !shownSquad;
  shownSquad = sq;
  panel.hidden = !sq;
  $("fdetail").hidden = !!sq && !NARROW.matches;
  if (!sq) squadHint.hidden = true;
  else if (opening) requestAnimationFrame(showSquadHint);
  syncSquadButtons(...document.querySelectorAll(".squad-toggle"));
  if (!sq) return;
  const close = el("button", { type: "button", text: "✕", ariaLabel: "스쿼드 닫기", title: "닫기" });
  close.addEventListener("click", () => showSquad(null));
  const players = sq.players.map((p) => ({ ...p, position: p.code, name: p.player }));
  const field = pitch(players, "", () => "");
  field.append(el("div", { class: "sq-loader", role: "status" }));
  // 경기장 왼쪽 위: 줄별 OVR 평균 (spposition 20~ 공격, 9~19 미드필드, 1~8 수비. 골키퍼는 뺀다)
  const lines = [["FW", (c) => c >= 20], ["MF", (c) => c >= 9 && c < 20], ["DF", (c) => c >= 1 && c < 9]].map(([k, inLine]) => {
    const ovrs = players.filter((p) => inLine(p.code) && p.ovr != null).map((p) => p.ovr);
    return ovrs.length ? el("div", { class: k.toLowerCase() }, el("b", { text: k }), el("strong", { text: String(Math.round(ovrs.reduce((a, b) => a + b) / ovrs.length)) })) : null;
  }).filter(Boolean);
  if (lines.length) field.append(el("div", { class: "ovr-box", ariaLabel: "줄별 평균 OVR" }, ...lines));
  if (status) {
    const before = new Map((prev?.players || []).map((p) => [p.code, p]));
    const subs = [];
    let n = 0;
    field.querySelectorAll(".pp").forEach((node, i) => {
      const old = before.get(players[i].code);
      if (old?.sp_id === players[i].sp_id) return; // 그대로인 선수는 가만히
      if (old) return subs.push([old, sq.players[i], node]); // 같은 자리에 다른 선수 → 교체
      node.classList.add("enter");
      node.style.animationDelay = `${n++ * (before.size ? 60 : 110)}ms`;
    });
    if (subs.length) substitute(field, subs);
  }
  const tiles = el("div", { class: "tiles" }, tile("팀컬러", sq.team_color ? plain(sq.team_color) : sq.players.length ? "없음" : "-", ""),
                  tile("포메이션", sq.formation || "-", ""), tile("총 금액", sq.price || "-", ""),
                  tile("총 급여", sq.salary != null ? String(sq.salary) : "-", ""));
  // 짜는 중에 값이 바뀌면 금액·급여 숫자를 굴린다 (다시 열 때나 움직임 줄이기 설정이면 그냥 보인다)
  if (status && !matchMedia("(prefers-reduced-motion: reduce)").matches) {
    [[2, sq.price, prev?.price], [3, sq.salary, prev?.salary]].forEach(([i, now, was]) => {
      if (now == null || String(now) === String(was ?? "")) return;
      tiles.children[i].querySelector(".val").replaceChildren(roll(String(now), String(was ?? "")));
    });
  }
  panel.replaceChildren(el("div", { class: "fd-head" }, el("h2", { text: "추천 스쿼드" }), close),
                        tiles, field);
  squadStatus(status, !sq.players.length); // 선수가 올라왔으면 로고는 내린다
}
// 중계 화면 선수 교체처럼: 나가는 선수(▼ 빨강)는 흐려지며 내려가고, 들어오는 선수(▲ 초록)가 그 자리로 들어온다
function substitute(field, subs) {
  const moving = !matchMedia("(prefers-reduced-motion: reduce)").matches;
  const ghosts = moving ? pitch(subs.map(([o]) => ({ ...o, position: o.code, name: o.player })), "", () => "").querySelectorAll(".pp") : [];
  subs.forEach(([, , node], i) => {
    const ghost = ghosts[i];
    if (ghost) {
      ghost.classList.add("sub-out");
      ghost.style.animationDelay = `${i * 300}ms`;
      ghost.addEventListener("animationend", () => ghost.remove(), { once: true });
      field.append(ghost);
    }
    node.classList.add("sub-in");
    node.style.animationDelay = `${i * 300 + 450}ms`;
    setTimeout(() => node.classList.remove("sub-in"), 4500);
  });
}
// 짜는 중 표시 (진행 문구는 화면에 쓰지 않고 스크린리더에만). null이면 뗀다.
// working: 도구가 도는 중이라 경기장에 로고 로딩도 띄운다 (false = 스쿼드는 다 짰고 설명을 쓰는 중: 테두리 빛만)
function squadStatus(text, working = true) {
  const box = $("squad-panel").querySelector(".sq-loader");
  if (box) box.ariaLabel = text || "";
  $("squad-panel").classList.toggle("building", !!text); // 짜는 동안 칸 테두리·바탕을 강조색으로
  $("squad-panel").classList.toggle("working", !!text && working);
}
function syncSquadButtons(...buttons) {
  for (const b of buttons) b.textContent = shownSquad === b.squad ? "스쿼드 접기" : "스쿼드 열기";
}
// 답 아래 좋아요·아쉬워요: 매번이 아니라 RATE_CHANCE 확률로만 묻는다 (관리자 페이지에서 그 질문 옆에 보인다)
const RATE_CHANCE = 0.3;
const THUMB = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7 10v12"/><path d="M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2a3.13 3.13 0 0 1 3 3.88Z"/></svg>';
function rateRow(id) {
  const row = el("div", { class: "rate" }, el("span", { text: "이 답이 도움이 됐나요?" }));
  for (const [vote, label] of [["up", "도움이 됐어요"], ["down", "아쉬워요"]]) {
    const b = el("button", { type: "button", class: vote, innerHTML: THUMB, ariaLabel: label, title: label });
    b.addEventListener("click", () => {
      fetch("/api/feedback", { method: "POST", headers: headers(), body: JSON.stringify({ id, vote }) }).catch(() => {});
      row.replaceChildren(el("span", { text: vote === "up" ? "고마워요" : "알려 주셔서 고마워요. 더 나아질게요." }));
      setTimeout(() => { row.classList.add("gone"); row.addEventListener("transitionend", () => row.remove(), { once: true }); }, 2000);
    });
    row.append(b);
  }
  return row;
}
// "/"로 시작하면 쓸 수 있는 명령어를 입력창 위에 펼친다 (Claude Code처럼). 친 만큼 좁혀지고, 고르면 입력창에 채운다
const COMMANDS = [
  ["/model", "답할 모델 고르기 (새로고침 전까지)"],
  ["/trace", "모델의 사고과정 표시 (기본 켬, 한 번 더 치면 끔)"],
  ["/help", "명령어 안내 (새 탭)"],
];
let cmdShown = [], cmdIndex = 0;
function closeCmdMenu() {
  $("cmd-menu").hidden = true;
  $("msg").removeAttribute("aria-activedescendant");
}

function updateCmdMenu() {
  const v = $("msg").value;
  cmdShown = /^\/[^\n]*$/.test(v) ? COMMANDS.filter(([c]) => c.startsWith(v.trimEnd()) || v.startsWith(c + " ")) : [];
  cmdIndex = Math.min(cmdIndex, Math.max(cmdShown.length - 1, 0));
  $("cmd-menu").hidden = !cmdShown.length;
  if (cmdShown.length) $("msg").setAttribute("aria-activedescendant", `cmd-${cmdIndex}`);
  else closeCmdMenu();
  $("cmd-menu").replaceChildren(...cmdShown.map(([c, desc], i) => {
    const li = el("li", { id: `cmd-${i}`, role: "option", ariaSelected: String(i === cmdIndex) }, el("b", { text: c }), el("span", { text: desc }));
    li.addEventListener("pointerdown", (ev) => { ev.preventDefault(); pickCmd(c); }); // 입력창 포커스를 잃지 않게
    return li;
  }));
}
// 고르면 입력창에 채우기만 한다 (실행은 Enter로). /model은 바로 모델 선택창
function pickCmd(c) {
  $("msg").value = c + " "; cmdShown = []; closeCmdMenu();
  $("msg").focus(); $("msg").setSelectionRange($("msg").value.length, $("msg").value.length);
  if (c === "/model") { $("msg").value = ""; pickModel(); } // 모델 선택창
}
function cmdKeys(ev) {
  if ($("cmd-menu").hidden || ev.isComposing) return false;
  if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
    const started = $("cmd-menu").classList.contains("kb"); // 처음 누른 ↓는 첫 줄부터
    cmdIndex = started ? (cmdIndex + (ev.key === "ArrowDown" ? 1 : cmdShown.length - 1)) % cmdShown.length
                       : ev.key === "ArrowDown" ? 0 : cmdShown.length - 1;
    $("cmd-menu").classList.add("kb"); updateCmdMenu();
  } else if (ev.key === "Enter" && !ev.shiftKey) {
    const c = cmdShown[cmdIndex][0], v = $("msg").value.trim();
    if (v === c || v.startsWith(c + " ")) { closeCmdMenu(); return false; } // 다 친 명령어는 그대로 실행
    pickCmd(c);
  } else if (ev.key === "Tab") pickCmd(cmdShown[cmdIndex][0]);
  else if (ev.key === "Escape") closeCmdMenu();
  else return false;
  ev.preventDefault();
  return true;
}
$("msg").addEventListener("input", () => { cmdIndex = 0; $("cmd-menu").classList.remove("kb"); updateCmdMenu(); });
$("msg").addEventListener("blur", () => { closeCmdMenu(); });
$("msg").addEventListener("keydown", (ev) => { // Enter는 보내기, Shift+Enter는 줄바꿈 (한글 조합 중인 Enter는 무시)
  if (cmdKeys(ev)) return; // 명령어 목록이 열려 있으면 ↑↓·Enter·Tab·Esc는 목록이
  if (ev.key === "Enter" && !ev.shiftKey && !ev.isComposing) {
    ev.preventDefault();
    $("chat-form").requestSubmit();
  } else if (ev.key === "Tab" && !ev.shiftKey && !$("msg").value && tabFill) {
    ev.preventDefault(); // 빈 입력창에서 Tab = 흐리게 보이는 예시 질문을 그대로 채운다 (Shift+Tab은 원래대로 이동)
    $("msg").value = tabFill;
    $("msg").dispatchEvent(new Event("input"));
  }
});
// 입력창 안내: 20% 가이드, 10% 홍보(static/hints.txt의 '# 가이드'·'# 홍보' 아래 줄), 70% 예시 질문(static/examples.txt) 중 하나.
// 칸이 비어 있으면 예시 질문으로. Tab으로 채우는 건 예시 질문만
const DEFAULT_HINT = $("msg").placeholder; // 예시를 못 불러왔을 때 ("무엇이든 물어보세요")
let examples = [], hints = { 가이드: [], 홍보: [] }, tabFill = "";
const pick = (list) => list[Math.floor(Math.random() * list.length)];
function setHint(text, fill = false) { $("msg").placeholder = text; tabFill = fill ? text : ""; }
function randomHint() {
  const r = Math.random();
  const own = r < 0.2 ? hints.가이드 : r < 0.3 ? hints.홍보 : [];
  if (own.length) return setHint(pick(own));
  if (!examples.length) return setHint(DEFAULT_HINT);
  setHint(pick(examples), true);
}
// @닉네임: 빈 입력창에 @를 치면 위에 작은 칸이 뜨고, 그 유저의 최근 경기 선발 11명을 불러와 다음 질문에 붙인다 (멘션처럼 그 질문 한 번)
// #피드ID: 같은 칸으로 스쿼드메이커 피드의 선발 11명을 붙인다
let team = null; // 붙인 팀 { nickname, mention, label }
let teamMark = "@";
const teamInput = mountSpringInput($("team-name"));
function openTeamBox(mark = "@") {
  teamMark = mark;
  $("team-box").hidden = false; $("team-hint").textContent = ""; $("team-hint").className = "hint";
  $("team-name").value = ""; $("team-name").focus();
  $("team-name").placeholder = mark === "#" ? "스쿼드 피드 ID" : "구단주명";
  $("team-name").setAttribute("aria-label", mark === "#" ? "팀을 불러올 스쿼드 피드 ID" : "팀을 불러올 구단주명");
  teamInput.reset();
  showTeam(mark); // 닉네임을 넣는 동안 입력창에는 같은 모양의 @(#)만
}
function closeTeamBox() { if (!$("team-box").hidden) { $("team-box").hidden = true; showTeam(); $("msg").focus(); } }
// 입력창 첫 줄을 표시만큼 들여 써서 "@닉네임 질문…"처럼 보이게. pending = 닉네임을 넣는 중의 "@" (✕ 없음)
function showTeam(pending = null) {
  const text = pending || (team ? team.mention : "");
  $("team-chip").hidden = !text;
  $("team-chip-name").textContent = text;
  $("team-off").hidden = !!pending;
  $("team-chip").title = !pending && team ? team.label : "";
  $("msg").style.textIndent = text ? `${$("team-chip").offsetWidth + 6}px` : "";
}
async function loadTeam() {
  if ($("team-ok").disabled) return;
  if (needKey()) { askKey(); return; }
  const value = $("team-name").value.trim().replace(/^[@#]/, "");
  if (!value) { teamInput.error(); $("team-name").focus(); return; }
  const nickname = teamMark === "#" ? "#" + value : value;
  teamInput.loading(true);
  $("team-ok").disabled = true; $("team-hint").className = "hint"; $("team-hint").textContent = "팀을 불러오는 중…";
  try {
    const res = await fetch("/api/team", { method: "POST", headers: headers(), body: JSON.stringify({ nickname, session_id: sessionId }) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok && res.status === 401 && beta) rejectedKey(data.detail);
    if (!res.ok) { $("team-hint").className = "hint err"; $("team-hint").textContent = data.detail || "팀을 불러오지 못했어요."; teamInput.error(); return; }
    sessionId = data.session_id;
    team = { nickname: data.nickname, mention: data.mention, label: data.label + (data.old ? " (30일 넘은 경기)" : "") };
    await teamInput.success();
    closeTeamBox();
  } catch {
    $("team-hint").className = "hint err"; $("team-hint").textContent = "연결하지 못했어요."; teamInput.error();
  } finally { teamInput.loading(false); $("team-ok").disabled = false; }
}
// 질문 말풍선 앞에 파란 @닉네임. 팀은 그 질문이 가져가므로 입력창에서는 뗀다
function mention(bubble) {
  if (team) {
    bubble.prepend(el("span", { class: "mention", text: `${team.mention} ` }));
    team = null; showTeam();
  }
  return bubble;
}
function dropTeam() {
  if (!team) return;
  if (sessionId) fetch("/api/team?session_id=" + encodeURIComponent(sessionId), { method: "DELETE", headers: headers() }).catch(() => {});
  team = null; showTeam(); $("msg").focus();
}
$("msg").addEventListener("input", () => { const v = $("msg").value; if (v === "@" || v === "#") { $("msg").value = ""; openTeamBox(v); } });
$("msg").addEventListener("keydown", (ev) => { // 빈 입력창에서 지우기 = 팀 떼기
  if (ev.key === "Backspace" && team && !$("msg").value) dropTeam();
});
$("team-ok").addEventListener("click", loadTeam);
document.addEventListener("pointerdown", (ev) => { // 칸 밖을 누르면 닫힌다
  if (!$("team-box").hidden && !$("team-box").contains(ev.target)) closeTeamBox();
});
$("team-off").addEventListener("click", dropTeam);
$("team-name").addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" && !ev.isComposing) { ev.preventDefault(); loadTeam(); }
  if (ev.key === "Escape") closeTeamBox();
});
// 채팅에 "/admin": 질문으로 보내지 않고 관리자 비밀번호를 받아, 맞으면 답변 기록 페이지로 간다
function adminPrompt() {
  const input = el("input", { type: "password", placeholder: "관리자 비밀번호", autocomplete: "current-password", required: true });
  const note = el("div", { class: "admin-note", role: "alert" });
  const form = el("form", { class: "msg bot" }, el("div", { text: "관리자 비밀번호를 입력하세요." }),
                  el("div", { class: "admin-row" }, input, el("button", { type: "submit", text: "확인" })), note);
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const res = await fetch("/api/admin/logs?days=1", { headers: { "X-Admin-Key": input.value } }).catch(() => null);
    if (res && res.ok) {
      try { localStorage.setItem("fclm-admin", input.value); } catch {} // 기록 페이지가 다시 묻지 않도록 (이 기기에서 기억)
      form.replaceWith(el("div", { class: "msg bot note done", text: "/admin 적용: 답변 기록 페이지로 이동합니다." }));
      location.href = "/admin";
      return;
    }
    // 3회부터 남은 기회 안내, 5회 틀리면 이 IP는 막힌다 (서버가 알려 준 문구 그대로)
    const detail = res && res.status !== 404 ? (await res.json().catch(() => ({}))).detail : null;
    note.textContent = !res ? "연결하지 못했습니다." : res.status === 404 ? "관리자 페이지가 꺼져 있습니다." : detail || "비밀번호가 맞지 않습니다.";
    if (res && res.status === 403) for (const c of form.querySelectorAll("input, button")) c.disabled = true;
    else input.select();
  });
  $("log").append(form);
  $("log").scrollTop = $("log").scrollHeight;
  input.focus();
}
const note = (text) => say("bot note done", text); // 명령어가 적용됐다는 안내
$("model-cancel").addEventListener("click", () => $("model-dialog").close("no")); // 제출 버튼이 아니어야 창 안의 Enter가 '적용'이 된다
// /model이나 명령어 목록에서 /model을 고르면 뜨는 선택창. 지금 설정이 골라져 있다
async function pickModel() {
  const dialog = $("model-dialog"), now = model || { key: DEFAULT_MODEL }; // 고르지 않았으면 기본 GPT-6 Luna
  $("model-opts").replaceChildren(...Object.entries(MODELS).map(([key, [, name, logo]]) => el("label", { class: "opt" },
    el("input", { type: "radio", name: "model", value: key, checked: key === now.key }),
    el("img", { src: `/static/vendor/lobehub-icons/${logo}.svg`, alt: "", width: 20, height: 20 }), el("b", { text: name }),
    key === DEFAULT_MODEL ? el("span", { class: "tag", text: "기본" }) : null)));
  dialog.returnValue = "";
  dialog.showModal();
  dialog.querySelector("input[name=model]:checked").focus(); // 처음 포커스는 고른 줄에 (아니면 맨 위 줄에 가서 두 줄이 강조돼 보인다)
  await new Promise((done) => dialog.addEventListener("close", done, { once: true }));
  $("msg").focus();
  if (dialog.returnValue !== "yes") return;
  const picked = { key: dialog.querySelector("input[name=model]:checked").value };
  model = picked.key === DEFAULT_MODEL ? null : picked; // 기본과 같으면 기본으로 (첫 질문 답 캐시도 쓴다)
  note(modelText(picked));
}
// "/"로 시작하면 모델에 보내지 않는다: 아는 명령어면 실행, 아니면 invalid command.
function command(text) {
  const [name, ...flags] = text.split(/\s+/);
  const cmd = name.toLowerCase();
  const only = (allowed) => new Set(flags).size === flags.length && flags.every((f) => allowed.includes(f));
  if (cmd === "/admin" && !flags.length) return adminPrompt();
  if (cmd === "/trace" && !flags.length) {
    traceOn = !traceOn; // 기본으로 켬. 한 번 더 치면 끈다 (새로고침하면 다시 켬)
    return note(traceOn ? "/trace 적용: 모델의 사고과정을 표시합니다." : "/trace 해제: 모델의 사고과정을 더 이상 표시하지 않습니다.");
  }
  if (cmd === "/model" && !flags.length) return pickModel(); // 모델 선택창
  if (cmd === "/help" && !flags.length) { // 가이드의 명령어 페이지를 새 탭에 (Enter로 보낸 직후라 팝업 차단에 걸리지 않는다)
    const tab = window.open("/guide/commands", "_blank");
    if (tab) { tab.opener = null; return note("/help: 명령어 안내를 새 탭에서 열었습니다."); }
    note("/help: 새 탭이 막혀 열지 못했습니다. ").append(el("a", { href: "/guide/commands", target: "_blank", rel: "noopener", text: "명령어 안내 열기" }));
    return;
  }
  $("log").append(el("div", { class: "msg bot note" }, el("em", { text: "invalid command." })));
  $("log").scrollTop = $("log").scrollHeight;
}

// '…': 답이 써지는 중에 위로 올려 읽으면 뜬다. 누르면 맨 아래로, 맨 아래 가까이 내려오면 저절로 사라진다
$("jump").addEventListener("click", () => {
  const log = $("log");
  log.scrollTo({ top: log.scrollHeight, behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
});
$("log").addEventListener("scroll", () => {
  const log = $("log");
  if (log.scrollHeight - log.scrollTop - log.clientHeight < 40) $("jump").hidden = true;
}, { passive: true });

$("chat-form").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const text = $("msg").value.trim();
  if (!text || inflight) return; // 답을 기다리는 중에는 입력한 글을 그대로 둔다
  $("msg").value = "";
  if (text.startsWith("/")) return command(text);
  send(text);
});
const lines = (url) => fetch(url).then((r) => r.text()).then((t) => t.split("\n").map((q) => q.trim()).filter(Boolean));
Promise.all([lines("/static/examples.txt"), lines("/static/hints.txt").catch(() => [])]).then(([ex, hs]) => {
  examples = ex;
  let section = null;
  for (const line of hs) {
    if (line.startsWith("#")) section = hints[line.slice(1).trim()]; // '# 가이드' / '# 홍보'
    else section?.push(line);
  }
  if (!inflight) randomHint();
}).catch(() => {});
say("bot intro", "시세와 게임 데이터는 수집 시각 기준이며 이적시장에 따라 소폭 변동될 수 있습니다.");

mountPills($("dist-mode"), [{ value: "formation", label: "포메이션 분포" }, { value: "team", label: "팀컬러 분포" }], {
  ariaLabel: "분포 보기 전환",
  onChange: (v) => { mode = v; selected = null; loadChart(); },
});
loadChart();

// 두 칸 사이 손잡이 (메인, 넓은 화면): 왼쪽으로 끌면 채팅창이 넓어진다. 왼쪽 칸은 스쿼드 경기장(최대 494px + 칸 여백)까지만 줄고,
// 오른쪽으로는 반반까지. 키보드: ←→ 40px. 새로고침하면 반반으로
const split = $("split");
if (split) {
  const main = document.querySelector("main"), MIN = 536, STEP = 40;
  let left = null; // null = 반반 (기본)
  const half = () => (main.clientWidth - 32 - 16) / 2; // main 좌우 여백 16px씩, 두 칸 사이 16px
  const set = (px) => {
    const h = half();
    left = Math.max(MIN, px) >= h ? null : Math.max(MIN, px); // 화면이 좁아 최소 폭이 반을 넘으면 반반 그대로
    if (left === null) main.style.removeProperty("--left"); else main.style.setProperty("--left", `${left}px`);
    split.setAttribute("aria-valuenow", String(Math.round(((left ?? h) / (2 * h)) * 100)));
  };
  split.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    split.setPointerCapture(e.pointerId);
    split.classList.add("dragging");
    document.body.classList.add("resizing");
  });
  split.addEventListener("pointermove", (e) => {
    if (split.hasPointerCapture(e.pointerId)) set(e.clientX - main.getBoundingClientRect().left - 16 - 8); // 손잡이 가운데 = 왼쪽 칸 끝 + 8px
  });
  const end = () => { split.classList.remove("dragging"); document.body.classList.remove("resizing"); };
  split.addEventListener("lostpointercapture", end);
  split.addEventListener("keydown", (e) => {
    const now = left ?? half();
    if (e.key === "ArrowLeft") set(now - STEP);
    else if (e.key === "ArrowRight") set(now + STEP);
    else return;
    e.preventDefault();
  });
  addEventListener("resize", () => left && set(left)); // 창이 줄어 반을 넘으면 반반으로
}

