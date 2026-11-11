/* 스쿼드 저장하기 (FCLM 북마클릿): FCLM의 "스쿼드메이커 열기"로 연 넥슨 스쿼드메이커 페이지에서 누르면 작은 창이 뜨고,
   주소 #fclm= 뒤의 스쿼드를 스쿼드메이커의 저장 요청(savesquadinfo) 그대로 방문자 본인 보관함에 저장한다.
   로그인이 안 돼 있으면 스쿼드를 이 사이트(fconline.nexon.com) 저장공간에 맡겨 두고, 로그인 뒤 다시 누르면 이어서 저장한다.
   넥슨 말고 다른 곳으로는 아무것도 보내지 않는다. index.html·send.html이 이 파일을 javascript: 링크로 만든다 (줄 주석 금지). */
(() => {
  const KEY = "fclm-squad";
  const old = document.getElementById("fclm-save");
  if (old) old.remove();
  const host = document.createElement("div");
  host.id = "fclm-save";
  host.style.cssText = "position:fixed;top:16px;right:16px;z-index:2147483647";
  const root = host.attachShadow({ mode: "open" });
  root.innerHTML = `<style>
    .box{width:300px;padding:16px;border-radius:14px;background:#16171d;color:#ededef;border:1px solid rgba(255,255,255,.14);
         box-shadow:0 12px 32px rgba(0,0,0,.5);font:14px/1.5 -apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Malgun Gothic",sans-serif}
    .head{display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;font-weight:700;color:#a5b4fc}
    .from{color:#8e8e98;font-weight:400;font-size:12px} .x{background:none;border:0;color:#8e8e98;font-size:16px;cursor:pointer}
    .msg{margin:0 0 12px;color:#ededef} .sub{color:#8e8e98;font-size:12px}
    .btn{display:block;width:100%;margin-top:8px;padding:10px;border-radius:10px;border:1px solid rgba(255,255,255,.16);
         background:rgba(255,255,255,.06);color:#ededef;font:inherit;font-weight:600;cursor:pointer;text-align:center;text-decoration:none}
    .btn.main{background:#a5b4fc;color:#0b0b0d;border-color:#a5b4fc} .btn:disabled{opacity:.5;cursor:default}
    .id{display:flex;gap:8px;align-items:center;margin-top:8px;padding:8px 10px;border-radius:10px;background:rgba(0,0,0,.35);font:600 15px ui-monospace,monospace}
    .id button{margin-left:auto;padding:4px 10px;border-radius:8px;border:1px solid rgba(255,255,255,.16);background:none;color:#ededef;cursor:pointer}
    .err{color:#ff8a8a}
  </style><div class="box"><div class="head"><span>스쿼드 저장하기 <span class="from">FCLM</span></span><button class="x" title="닫기">✕</button></div><div class="body"></div></div>`;
  document.body.append(host);
  const body = root.querySelector(".body");
  root.querySelector(".x").onclick = () => host.remove();
  const show = (html) => { body.innerHTML = html; };
  const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

  const m = location.hash.match(/fclm=([^&]+)/);
  let raw = m ? m[1] : null;
  if (!raw) { try { raw = localStorage.getItem(KEY); } catch (e) { raw = null; } }
  if (location.hostname !== "fconline.nexon.com" || !raw) {
    show(`<p class="msg">FCLM 답에서 <b>스쿼드메이커로 보내기</b> → <b>스쿼드메이커 열기</b>로 연 넥슨 페이지에서 눌러 주세요.</p>
          <a class="btn main" href="https://fconline.nexon.com/squadmaker">스쿼드메이커로 이동</a>`);
    return;
  }
  let d;
  try { d = JSON.parse(decodeURIComponent(raw)); } catch (e) { show(`<p class="msg err">스쿼드 정보를 읽지 못했어요.</p>`); return; }

  const save = async (share) => {
    body.querySelectorAll("button").forEach((b) => { b.disabled = true; });
    const none = [-2, -2, -2];
    const empty = { role: "", position: "", position2: "", position3: "", state: 0, x: 0, y: 0, buildUp: 0, strategy: [],
                    participation: [], traits: none, player_traits: [], trait: "", trainer: none };
    const players = d.p.map(([role, x, y, spid, buildUp]) => ({ ...empty, role, state: 1, x, y, buildUp, thumb_custom: "", pid: spid % 1000000, spid }));
    while (players.length < 18) players.push({ ...empty });
    const squad = { squadName: d.t, contents: "스쿼드메이커 소개 입니다.", mode: "1", formation: d.f, coach: 0, adap: 5, preset: 0,
                    backcolor: null, ovr: { fw: 0, mf: 0, df: 0, total: 0 }, players, coachinfo: null, totalPay: "0", totalPrice: "0",
                    totalTeamColor: null, teamSt: { defensivestyle: 1, defwidth: 6, defdepth: 6, offensive_style_buildup: 1,
                    offensive_style_chancecreation: 1, offwidth: 6, offplayerinbox: 6, offcornerkick: 3, offfreekick: 3, teammentality: 3 } };
    const res = await fetch("/squadmakerapi/SquadMakerProc", {
      method: "POST",
      headers: { "X-Requested-With": "XMLHttpRequest", "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ strMethod: "savesquadinfo", strSquadJson: JSON.stringify(squad), n1Feed: share ? "1" : "0" }),
    }).then((r) => r.json()).catch(() => null);
    const msg = (res && res.ResultMsg) || "연결하지 못했어요";
    if (!res || res.ResultCode !== 1) {
      if (/로그인/.test(msg)) {
        try { localStorage.setItem(KEY, raw); } catch (e) { }
        const back = encodeURIComponent("https://fconline.nexon.com/squadmaker");
        show(`<p class="msg">넥슨에 로그인돼 있지 않아요.</p><p class="sub">로그인한 뒤 이 북마크(<b>스쿼드 저장하기</b>)를 다시 누르면 이어서 저장돼요.</p>
              <a class="btn main" href="https://nxlogin.nexon.com/common/login.aspx?redirect=${back}">넥슨 로그인</a>`);
      } else {
        show(`<p class="msg err">저장하지 못했어요: ${esc(msg)}</p>`);
      }
      return;
    }
    try { localStorage.removeItem(KEY); } catch (e) { }
    history.replaceState(null, "", location.pathname);
    const id = res.ResultData && res.ResultData.strFeedId;
    show(`<p class="msg">저장했어요.</p><p class="sub">스쿼드메이커의 <b>내 스쿼드 → 스쿼드 보관함</b>에서 불러올 수 있어요.</p>` +
         (id ? `<div class="id">${esc(id)}<button>복사</button></div><p class="sub">피드 ID: 스쿼드 피드에서 누구나 이 ID로 볼 수 있어요.</p>` : ""));
    const copy = body.querySelector(".id button");
    if (copy) copy.onclick = () => navigator.clipboard.writeText(id).then(() => { copy.textContent = "복사됨"; });
  };

  show(`<p class="msg"><b>${esc(d.t)}</b> 스쿼드를 내 스쿼드메이커에 저장할까요?</p>
        <button class="btn main" data-share="0">보관함에 저장</button>
        <button class="btn" data-share="1">피드에도 공개하고 ID 받기</button>`);
  body.querySelectorAll("button").forEach((b) => { b.onclick = () => save(b.dataset.share === "1"); });
})();
