import React from 'react';
import { createRoot } from 'react-dom/client';
import { animate } from 'motion';
import AITextLoading from './components/ai-text-loading';
import AILoadingState from './components/ai-loading';

export function installMotion(engine) {
  const roots = new Map();
  new MutationObserver(() => {
    for (const [host, root] of roots) if (!host.isConnected) {
      root.unmount(); roots.delete(host);
    }
  }).observe(document.getElementById('log'), { childList: true });
  const pending = new WeakMap();
  const rendered = new WeakMap();
  engine.configureMotion({
    thinking(bubble) {
      bubble.className = 'msg bot pending';
      const canvas = document.createElement('canvas');
      canvas.ariaHidden = 'true';
      const host = document.createElement('span');
      host.className = 'thinking-text';
      bubble.replaceChildren(canvas, host);
      const root = createRoot(host);
      const show = text => root.render(<AITextLoading texts={[text]} label={text} />);
      show('랭커데이터를 살펴보고 있어요...');
      pending.set(bubble, show);
      rendered.delete(bubble);
      const stop = engine.mountOrb(canvas, 'searching', 20);
      return () => { stop(); pending.delete(bubble); root.unmount(); };
    },
    status(bubble, text) { pending.get(bubble)?.(text); },
    markdown(bubble, text) {
      const before = rendered.get(bubble) || 0;
      engine.renderMarkdown(bubble, text);
      // Keep the sanitized Markdown tree; animate only newly arriving text words.
      const walker = document.createTreeWalker(bubble, NodeFilter.SHOW_TEXT);
      const nodes = []; while (walker.nextNode()) nodes.push(walker.currentNode);
      let offset = 0;
      for (const node of nodes) {
        let wordOffset = offset; offset += node.textContent.length;
        if (offset <= before || node.parentElement.closest('pre, code')) continue;
        const fragment = document.createDocumentFragment();
        for (const word of node.textContent.split(/(\s+)/)) {
          wordOffset += word.length;
          if (!word.trim()) { fragment.append(word); continue; }
          const span = document.createElement('span'); span.textContent = word;
          span.className = 'stream-word'; fragment.append(span);
          if (!matchMedia('(prefers-reduced-motion: reduce)').matches && wordOffset > before) {
            animate(span, { opacity: [0, 1], y: [6, 0], filter: ['blur(4px)', 'blur(0px)'] },
              { type: 'spring', stiffness: 340, damping: 26 });
          }
        }
        node.replaceWith(fragment);
      }
      rendered.set(bubble, offset);
    },
    trace() {
      const original = engine.traceBox();
      const host = document.createElement('div'); host.className = 'agent-steps dark';
      const root = createRoot(host);
      roots.set(host, root);
      const update = () => {
        const steps = [...original.element.querySelectorAll('.trace-steps > li')].map((row, i) => ({
          id: String(i), label: row.querySelector('.trace-label').textContent,
          status: row.dataset.status === 'stopped' ? 'error' : row.dataset.status,
          detail: row.querySelector('.trace-detail').textContent.split('\n').filter(Boolean),
        }));
        root.render(<AILoadingState steps={steps} />);
      };
      update();
      return { element: host, ...Object.fromEntries(['event', 'writing', 'finish'].map(name => [name, (...args) => {
        original[name](...args); update();
      }])) };
    },
  });
}
