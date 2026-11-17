import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import '@fontsource-variable/noto-sans-kr';
import './styles.css';
import { installMotion } from './motion.jsx';



// Existing renderers own the contents of log, squad, stats and dialog containers.
// Keep these nodes mounted: their listeners and session live for the page lifetime.
function App() {
  const [engine, setEngine] = useState(null);
  const [error, setError] = useState('');
  useEffect(() => {
    import(/* @vite-ignore */ '/static/web-ui.js')
      .then(engine => { installMotion(engine); setEngine(engine); })
      .catch(() => setError('화면을 불러오지 못했습니다. 새로고침해 주세요.'));
  }, []);
  return <>
    <Header />
    <main><div className="side"><div className="contents" inert={!engine}><Chat /></div>
    {error && <p role="alert" className="text-red-300 p-4">{error}</p>}</div><Stats /></main>
    <Footer /><div id="tip" role="tooltip" hidden />
  </>;
}

function Header() { return (<>

<header className="ai-header flex items-center justify-between mx-auto"><a href="/ai" aria-label="FCLM AI 홈"><img src="/static/fclm-logo.png" alt="FCLM" width="151" height="32" /></a><nav aria-label="주 메뉴"><a href="#stats">데이터 보기</a><a href="/guide">가이드</a><a href="/">기존 화면</a></nav></header>

</>); }

function Welcome() { return (<>
<div className="welcome text-center mb-9" id="welcome"><h1>다음 스쿼드의 시작,<br />FCLM과 함께.</h1><p>선수 선택부터 포메이션 비교까지.<br className="mobile-break" /> 랭커 데이터에서 답을 찾아보세요.</p></div>
</>); }

function Chat() { return (<>
  <section id="chat" aria-labelledby="chat-title">
    <h2 id="chat-title" className="conversation-title">FCLM과 대화</h2>
    <Welcome />
    <div id="log" aria-live="polite"></div><section id="squad-panel" aria-live="polite" hidden></section>
    <dialog id="model-dialog" aria-labelledby="model-title"><form method="dialog"><h3 id="model-title">답할 모델</h3>
      <fieldset id="model-opts" aria-labelledby="model-title"></fieldset>
      <div className="row"><button type="button" id="model-cancel">취소</button><button value="yes" className="yes">적용</button></div></form></dialog>
    <div id="ai-beam"><form id="chat-form">
      <div id="team-box" hidden><div className="field"><input id="team-name" maxLength="30" autoComplete="off" placeholder="구단주명" aria-label="팀을 불러올 구단주명" />
        <button type="button" id="team-ok" aria-label="팀 불러오기"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6"/></svg></button></div>
        <span className="hint" id="team-hint" role="alert"></span></div>
      <ul id="cmd-menu" role="listbox" aria-label="명령어" hidden></ul>
      <button type="button" id="jump" aria-label="맨 아래로" hidden>…</button>
      <div id="msg-beam"><span id="team-chip" hidden><span id="team-chip-name"></span><button type="button" id="team-off" aria-label="팀 떼기">✕</button></span><textarea id="msg" aria-label="질문" aria-controls="cmd-menu" aria-haspopup="listbox" aria-autocomplete="list" rows="3" maxLength="500" placeholder="무엇이든 물어보세요"></textarea><button type="submit" id="send" aria-label="보내기"><svg className="i-send" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 19V5M5.5 11.5 12 5l6.5 6.5"/></svg><svg className="i-stop" viewBox="0 0 24 24" aria-hidden="true"><rect x="7" y="7" width="10" height="10" rx="2" fill="currentColor"/></svg></button></div>
    </form></div>
  </section>

</>); }

function Stats() { return (<>
<div className="col" id="stats"><div className="stats-heading"><div><h2>랭커 데이터 살펴보기</h2><p>지금 랭커들은 어떤 팀을 쓰고 있을까요?</p></div></div><div className="stats-grid grid grid-cols-1 sm:grid-cols-2 gap-6 items-start"><section aria-label="랭커 분포"><div className="dist-head"><div id="dist-mode"></div></div><div id="fchart" className="fchart"></div><div id="fdetail" aria-live="polite"></div></section><section id="pitch-detail" aria-label="랭커 스쿼드" aria-live="polite"></section></div></div>
</>); }

function Footer() { return (<>


<footer className="foot">
  <p>© 2026 FCLM. All rights reserved.</p>
  <p>FCLM은 FC Online 유저를 위한 비공식 랭커 데이터 통계 서비스입니다.<br />
    FC Online 관련 데이터는 NEXON Open API(Data based on NEXON Open API)와 FC온라인 데이터센터의 공개 정보를 기반으로 제공됩니다.<br />
    FCLM은 NEXON 및 EA SPORTS와 공식적으로 관련이 없습니다.<br />
    게임 관련 상표, 이미지, 선수 정보 및 저작권은 EA SPORTS 및 NEXON에 있습니다.</p>
  <p>질문은 AI 모델(Google Gemini·DeepSeek·OpenAI)로 처리되며 서비스 개선을 위해 기록됩니다. FCLM은 실수할 수 있으니 중요한 정보는 확인하세요.</p>
  <p className="contact"><a href="/guide" style={{color:"inherit"}}>사용 가이드</a><span aria-hidden="true">·</span>버그 / 문의사항
    <a href="https://open.kakao.com/o/s3axuDWi" target="_blank" rel="noopener noreferrer" aria-label="카카오톡 오픈채팅으로 문의하기" title="카카오톡 오픈채팅">
      <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><rect width="24" height="24" rx="6" fill="#FEE500"/>
        <path fill="#191919" d="M12 5.5c-3.9 0-7 2.4-7 5.4 0 1.9 1.3 3.6 3.2 4.6l-.7 2.5c-.1.3.2.5.4.3l3-2c.4 0 .7.1 1.1.1 3.9 0 7-2.4 7-5.5S15.9 5.5 12 5.5z"/></svg>
    </a>
    <a href="mailto:fclmdev@gmail.com" style={{color:"inherit"}} title="이메일로 문의하기">
      <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="m4 7 8 6 8-6"/></svg>fclmdev@gmail.com
    </a>
  </p>
</footer>



</>); }

createRoot(document.getElementById("root")).render(<App />);
