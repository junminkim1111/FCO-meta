// 챗봇 답(마크다운)을 HTML로: marked(MIT)로 변환하고 DOMPurify로 위험한 태그·속성을 걸러 낸다.
import { marked } from "./vendor/marked/marked.esm.js";
const DOMPurify = "setHTML" in Element.prototype ? null : (await import("./vendor/dompurify/purify.es.mjs")).default;

marked.setOptions({ gfm: true, breaks: true }); // 줄바꿈 한 번도 그대로
// 한국어 굵게: 표준 규칙은 "**64.3%**로"처럼 닫는 ** 앞이 문장부호이고 뒤에 글자(조사)가 붙으면 굵게 보지 않는다.
// ** 사이 글만 보고 굵게 만든다 (안쪽 글은 평소처럼 해석).
marked.use({
  extensions: [{
    name: "strongKo",
    level: "inline",
    start: (src) => src.indexOf("**"),
    tokenizer(src) {
      const m = /^\*\*(?=\S)([\s\S]*?\S)\*\*/.exec(src);
      if (m) return { type: "strong", raw: m[0], text: m[1], tokens: this.lexer.inlineTokens(m[1]) };
    },
  }],
});

export function renderMarkdown(node, text) {
  const html = marked.parse(text);
  // 외부 이미지는 네이티브·대체 경로 모두 불러오지 않는다.
  if ("setHTML" in Element.prototype) node.setHTML(html, { sanitizer: { removeElements: ["img"] } });
  else node.innerHTML = DOMPurify.sanitize(html, { FORBID_TAGS: ["img"] });
  node.classList.add("md");
}
