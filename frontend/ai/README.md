# FCLM AI frontend

React 19 + Tailwind CSS 4 + Vite. The FastAPI `/ai` route serves the committed build in `fco_meta/web/static/ai/`, so the Python deployment does not require Node.

```sh
npm --prefix frontend/ai ci
npm --prefix frontend/ai run build
```

For development, start the Python server on port 8000, then run `npm --prefix frontend/ai run dev` and open the displayed URL with `/static/ai/`. API requests and shared static assets are proxied to Python.

`src/main.jsx` contains the React page components. Tailwind utilities and page-specific CSS live in `src/styles.css`. The existing `web-ui.js` owns the contents of the empty message, squad, chart, team, and model containers, preserving the current stream protocol, sanitization, commands and session behavior. Do not let React render children inside these containers. This is a hybrid React view, not a full rewrite of the chat engine. There is one mount per page; changing routes performs full navigation.

After editing, rebuild and commit the generated assets alongside the source and lockfile. Do not edit generated assets directly.

The fixture browser tests (`tests/test_ai_web.cjs`) intercept API calls; they never query paid models. Serve `fco_meta/web` with a static server on port 18765 before running them with Playwright installed.

## Motion references

- `src/components/ai-text-loading.tsx` and `ai-loading.tsx`: Kokonut UI registry v2.0.0, MIT (license alongside components). The shadcn command required interactive initialization; the registry source was copied and its `motion`/`lucide-react` imports installed. Labels/colors were adapted to FCLM and real trace events drive the steps.
- `src/motion.jsx`: DesignBookmark Streaming Reply word reveal, stiffness 340 / damping 26, 6px rise and 4px blur, applied after existing Markdown sanitization. Network arrivals determine timing; the demo's fabricated conversation and repeating loop are omitted.
- `beam.js` and `orb.js`: existing Libraries.dev Border Beam and Thinking Orbs engines, activated during requests.
- `spring-input.js`: existing DesignBookmark adaptation; `/ai` disables the typing caret spring while preserving submit/error motion.
- Heading: locally served Noto Sans KR Variable, license in `FONT-LICENSE.txt`.
