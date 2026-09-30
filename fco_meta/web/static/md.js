// 챗봇 답(마크다운)을 HTML로: marked(MIT)로 변환하고 DOMPurify로 위험한 태그·속성을 걸러 낸다.
import { marked } from "./vendor/marked/marked.esm.js";
import DOMPurify from "./vendor/dompurify/purify.es.mjs";

marked.setOptions({ gfm: true, breaks: true }); // 줄바꿈 한 번도 그대로

export function renderMarkdown(node, text) {
  node.innerHTML = DOMPurify.sanitize(marked.parse(text), { FORBID_TAGS: ["img"] }); // 외부 이미지는 불러오지 않는다
  node.classList.add("md");
  for (const table of node.querySelectorAll("table")) { // 넓은 표는 말풍선 안에서 가로로 스크롤
    const wrap = document.createElement("div");
    wrap.className = "table-wrap";
    table.replaceWith(wrap);
    wrap.append(table);
  }
  for (const a of node.querySelectorAll("a[href]")) { a.target = "_blank"; a.rel = "noopener noreferrer"; }
}
