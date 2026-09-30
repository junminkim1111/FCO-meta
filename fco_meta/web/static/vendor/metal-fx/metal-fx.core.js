// metal-fx 1.0.4 (MIT) dist/index.es.js 에서 React 컴포넌트를 뺀 엔진 부분 — 원본 1–2행(import)과 1387행 이후 제거, 끝의 export만 추가
function xt(e) {
  let t = e.replace("#", "");
  return t.length === 3 && (t = t[0] + t[0] + t[1] + t[1] + t[2] + t[2]), [parseInt(t.slice(0, 2), 16) / 255, parseInt(t.slice(2, 4), 16) / 255, parseInt(t.slice(4, 6), 16) / 255];
}
function vt(e, t, n) {
  e /= 255, t /= 255, n /= 255;
  const o = Math.max(e, t, n), r = Math.min(e, t, n), a = o - r;
  let s = 0;
  const d = o === 0 ? 0 : a / o;
  return a !== 0 && (o === e ? s = ((t - n) / a + 6) % 6 : o === t ? s = (n - e) / a + 2 : s = (e - t) / a + 4, s /= 6), [s, d, o];
}
function bt(e, t, n) {
  const o = Math.floor(e * 6), r = e * 6 - o, a = n * (1 - t), s = n * (1 - r * t), d = n * (1 - (1 - r) * t);
  let l = 0, c = 0, u = 0;
  switch (o % 6) {
    case 0:
      l = n, c = d, u = a;
      break;
    case 1:
      l = s, c = n, u = a;
      break;
    case 2:
      l = a, c = n, u = d;
      break;
    case 3:
      l = a, c = s, u = n;
      break;
    case 4:
      l = d, c = a, u = n;
      break;
    case 5:
      l = n, c = a, u = s;
      break;
  }
  return [Math.round(l * 255), Math.round(c * 255), Math.round(u * 255)];
}
const wt = 66, yt = 66, _t = 1500, Mt = 1, Te = 16, Rt = 16, At = 8, St = 96, Tt = 2, Ct = {
  name: "chromatic",
  modes: {
    dark: {
      colors: ["#000000", "#aae8ff", "#c5fe9e", "#f7888d", "#0d0d0d", "#fffdc3", "#007cff"],
      alphas: [1, 1, 1, 1, 1, 1, 1],
      direction: 80,
      speed: 1.2,
      intensity: 2,
      scale: 1.6,
      softness: 0.18,
      distortion: 0.3,
      complexity: 0.68,
      shape: 1,
      blur: 1,
      vignette: 0.26,
      vigOpacity: 0.6,
      shaderOpacity: 1
    },
    light: {
      colors: ["#ffffff", "#ffffff", "#ffffff", "#ffb3b3", "#adadad", "#f5ff70", "#007cff"],
      alphas: [1, 1, 1, 1, 1, 1, 1],
      direction: 80,
      speed: 1.2,
      intensity: 2,
      scale: 2.5,
      softness: 0.18,
      distortion: 0.3,
      complexity: 0.68,
      shape: 1,
      blur: 1,
      vignette: 0.24,
      vigOpacity: 0.16,
      shaderOpacity: 1
    }
  }
}, kt = {
  name: "silver",
  modes: {
    dark: {
      colors: ["#000000", "#dedede", "#747270", "#e5e5e5", "#0d0d0d", "#ffffff", "#e6e6e6"],
      alphas: [1, 1, 1, 1, 1, 1, 1],
      direction: 80,
      speed: 1.2,
      intensity: 2,
      scale: 2.5,
      softness: 0.18,
      distortion: 0.3,
      complexity: 0.68,
      shape: 1,
      blur: 1,
      vignette: 0.26,
      vigOpacity: 0.6,
      shaderOpacity: 0.88
    },
    light: {
      colors: ["#f6f6f6", "#ffffff", "#ffffff", "#f7f7f7", "#c9c9c9", "#d0d0d0", "#d1d1d1"],
      alphas: [1, 1, 1, 1, 1, 1, 1],
      direction: 80,
      speed: 1.2,
      intensity: 2,
      scale: 2.5,
      softness: 0.18,
      distortion: 0.3,
      complexity: 0.68,
      shape: 1,
      blur: 1,
      vignette: 0.2,
      vigOpacity: 0.26,
      shaderOpacity: 1
    }
  }
}, Et = {
  name: "gold",
  modes: {
    dark: {
      colors: ["#000000", "#ffffff", "#ffffff", "#f7d488", "#0d0d0d", "#fffdc3", "#ffffff"],
      alphas: [1, 1, 1, 1, 1, 1, 1],
      direction: 80,
      speed: 1,
      intensity: 2,
      scale: 2.5,
      softness: 0.18,
      distortion: 0.3,
      complexity: 0.68,
      shape: 1,
      blur: 1,
      vignette: 0.26,
      vigOpacity: 0.6,
      shaderOpacity: 0.92
    },
    light: {
      colors: ["#fff8e1", "#fffbe0", "#ffffff", "#fff6d6", "#d2c7a7", "#dcd2bc", "#f9f7e5"],
      alphas: [1, 1, 1, 1, 1, 1, 1],
      direction: 80,
      speed: 1.2,
      intensity: 2,
      scale: 2.5,
      softness: 0.18,
      distortion: 0.3,
      complexity: 0.68,
      shape: 1,
      blur: 1,
      vignette: 0.22,
      vigOpacity: 0.24,
      shaderOpacity: 1
    }
  }
}, je = {
  chromatic: Ct,
  silver: kt,
  gold: Et
}, Lt = (
  /* glsl */
  `
  attribute vec2 a_position;
  void main() { gl_Position = vec4(a_position, 0.0, 1.0); }
`
), It = (
  /* glsl */
  `
  precision highp float;

  uniform vec2 u_resolution;
  uniform float u_time;
  uniform vec3 u_color1, u_color2, u_color3, u_color4, u_color5, u_color6, u_color7;
  uniform float u_alpha1, u_alpha2, u_alpha3, u_alpha4, u_alpha5, u_alpha6, u_alpha7;
  uniform float u_intensity, u_scale, u_direction;
  uniform float u_softness, u_distortion, u_complexity, u_shape;
  uniform float u_vignette, u_vigOpacity, u_blur, u_shaderOpacity;

  vec3 mod289(vec3 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
  vec2 mod289v2(vec2 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
  vec3 permute(vec3 x) { return mod289((x * 34.0 + 1.0) * x); }

  float snoise(vec2 v) {
    const vec4 C = vec4(0.211324865405187, 0.366025403784439,
                        -0.577350269189626, 0.024390243902439);
    vec2 i = floor(v + dot(v, C.yy));
    vec2 x0 = v - i + dot(i, C.xx);
    vec2 i1 = (x0.x > x0.y) ? vec2(1.0, 0.0) : vec2(0.0, 1.0);
    vec4 x12 = x0.xyxy + C.xxzz;
    x12.xy -= i1;
    i = mod289v2(i);
    vec3 p = permute(permute(i.y + vec3(0.0, i1.y, 1.0)) + i.x + vec3(0.0, i1.x, 1.0));
    vec3 m = max(0.5 - vec3(dot(x0, x0), dot(x12.xy, x12.xy), dot(x12.zw, x12.zw)), 0.0);
    m = m * m; m = m * m;
    vec3 x_ = 2.0 * fract(p * C.www) - 1.0;
    vec3 h = abs(x_) - 0.5;
    vec3 ox = floor(x_ + 0.5);
    vec3 a0 = x_ - ox;
    m *= 1.79284291400159 - 0.85373472095314 * (a0 * a0 + h * h);
    vec3 g;
    g.x = a0.x * x0.x + h.x * x0.y;
    g.yz = a0.yz * x12.xz + h.yz * x12.yw;
    return 130.0 * dot(m, g);
  }

  float fbm(vec2 p, float oct) {
    float val = 0.0, amp = 0.5;
    int n = int(oct);
    for (int i = 0; i < 7; i++) {
      if (i >= n) break;
      val += amp * snoise(p);
      p *= 2.0;
      amp *= 0.5;
    }
    return val;
  }

  float nfbm(vec2 p) { return fbm(p, 3.0 + u_complexity * 4.0); }

  /* 5-stop palette used by effect 1 (Plasma) — direct port of \`palette\` from
   * the canonical engine. Stops at t = 0, 0.25, 0.5, 0.75, 1.0. */
  vec3 palette(float t) {
    t = clamp(t, 0.0, 1.0);
    t = t * t * (3.0 - 2.0 * t);
    float k = 64.0;
    float w1 = u_alpha1 * exp(-k * t * t);
    float w2 = u_alpha2 * exp(-k * (t - 0.25) * (t - 0.25));
    float w3 = u_alpha3 * exp(-k * (t - 0.5)  * (t - 0.5));
    float w4 = u_alpha4 * exp(-k * (t - 0.75) * (t - 0.75));
    float w5 = u_alpha5 * exp(-k * (t - 1.0)  * (t - 1.0));
    float total = w1 + w2 + w3 + w4 + w5 + 0.0001;
    return (u_color1 * w1 + u_color2 * w2 + u_color3 * w3 +
            u_color4 * w4 + u_color5 * w5) / total;
  }

  /* Per-pixel alpha that re-introduces transparency when the user dials any
   * palette stop's alpha below 1. Same \`paletteAlpha\` from the canonical
   * engine. With every preset shipping all-1 alphas, this returns ~1 for every
   * pixel — but mirroring it keeps custom-preset behaviour identical. */
  float paletteAlpha(float t) {
    t = clamp(t, 0.0, 1.0);
    t = t * t * (3.0 - 2.0 * t);
    float k = 64.0;
    float w1 = u_alpha1 * exp(-k * t * t);
    float w2 = u_alpha2 * exp(-k * (t - 0.25) * (t - 0.25));
    float w3 = u_alpha3 * exp(-k * (t - 0.5)  * (t - 0.5));
    float w4 = u_alpha4 * exp(-k * (t - 0.75) * (t - 0.75));
    float w5 = u_alpha5 * exp(-k * (t - 1.0)  * (t - 1.0));
    float totalW = w1 + w2 + w3 + w4 + w5 + 0.0001;
    float rawW = exp(-k * t * t)
               + exp(-k * (t - 0.25) * (t - 0.25))
               + exp(-k * (t - 0.5)  * (t - 0.5))
               + exp(-k * (t - 0.75) * (t - 0.75))
               + exp(-k * (t - 1.0)  * (t - 1.0))
               + 0.0001;
    return totalW / rawW;
  }

  vec2 warp(vec2 p, float t) {
    float str = u_distortion * 2.0;
    return vec2(
      nfbm(p + vec2(t * 0.1, 0.0)),
      nfbm(p + vec2(0.0, t * 0.12) + 5.0)
    ) * str;
  }

  /* Plasma: four sine bands warped by an FBM field, mapped through the
   * 5-stop palette. Identical to effect 1 in the canonical engine. */
  vec3 computeEffect(vec2 uv, float aspect, float t, float dist, float cpx) {
    vec2 p = (uv - 0.5) * u_scale;
    p.x *= aspect;
    p += vec2(cos(u_direction), sin(u_direction)) * t * 0.15;

    float freq = 3.0 + cpx * 8.0;
    float val = 0.0;
    val += sin(p.x * freq + t);
    val += sin(p.y * freq + t * 1.3);
    val += sin((p.x + p.y) * freq * 0.7 + t * 0.7);
    val += sin(length(p) * freq * 0.8 - t * 1.5);
    vec2 w = warp(p, t);
    val += (w.x + w.y) * dist;
    val = val * 0.2 * u_intensity + 0.5;

    return palette(clamp(val, 0.0, 1.0));
  }

  void main() {
    vec2 uv = gl_FragCoord.xy / u_resolution;
    float aspect = u_resolution.x / u_resolution.y;
    float t = u_time;          // JS already multiplied u_time by preset.speed.
    float dist = u_distortion;
    float cpx = u_complexity;

    /* 5-tap cross blur (center + cardinal offsets). The chromatic/silver/gold
     * presets all ship with blur=1 so this path is always active. 5 taps
     * instead of the canonical engine's 9 saves ~44% fragment work; the
     * perceptual difference is nil because the output is already soft from
     * the plasma's low spatial frequency and CSS blur on reflections. */
    vec3 col;
    if (u_blur < 0.01) {
      col = computeEffect(uv, aspect, t, dist, cpx);
    } else {
      float r = u_blur * 0.02;
      col  = computeEffect(uv,                  aspect, t, dist, cpx) * 0.4;
      col += computeEffect(uv + vec2( r, 0.0),  aspect, t, dist, cpx) * 0.15;
      col += computeEffect(uv + vec2(-r, 0.0),  aspect, t, dist, cpx) * 0.15;
      col += computeEffect(uv + vec2(0.0,  r),  aspect, t, dist, cpx) * 0.15;
      col += computeEffect(uv + vec2(0.0, -r),  aspect, t, dist, cpx) * 0.15;
    }

    /* Gamma punch — adds the contrast pop that defines the chromatic
     * highlights. From the canonical engine: \`col = pow(col, vec3(1.3))\`. */
    col = pow(col, vec3(1.3));

    /* Vignette — soft edge darkening so corners read as recessed. The 40-px
     * scale at the bottom of the formula is hard-coded in the canonical
     * engine; we keep it for visual parity. */
    float edgeDist = min(min(uv.x, 1.0 - uv.x), min(uv.y, 1.0 - uv.y));
    float vigPx = 40.0 / min(u_resolution.x, u_resolution.y);
    float vigRange = vigPx * (1.0 + u_vignette * 3.0);
    float vig = edgeDist * edgeDist / (vigRange * vigRange);
    vig = smoothstep(0.0, 1.0, vig);
    col *= mix(1.0, vig, u_vignette * u_vigOpacity);

    /* Per-pixel alpha. With all-1 alphas the formula collapses to ~1 but the
     * computation matches the canonical engine so custom presets behave the
     * same. */
    float colorAlpha = (u_alpha1 + u_alpha2 + u_alpha3 + u_alpha4 + u_alpha5) / 5.0;
    if (colorAlpha < 0.999) {
      vec3 c1d = col - u_color1, c2d = col - u_color2, c3d = col - u_color3,
           c4d = col - u_color4, c5d = col - u_color5;
      float prox1 = exp(-8.0 * dot(c1d, c1d));
      float prox2 = exp(-8.0 * dot(c2d, c2d));
      float prox3 = exp(-8.0 * dot(c3d, c3d));
      float prox4 = exp(-8.0 * dot(c4d, c4d));
      float prox5 = exp(-8.0 * dot(c5d, c5d));
      float pTotal = prox1 + prox2 + prox3 + prox4 + prox5 + 0.0001;
      colorAlpha = (prox1 * u_alpha1 + prox2 * u_alpha2 + prox3 * u_alpha3 +
                    prox4 * u_alpha4 + prox5 * u_alpha5) / pTotal;
    }
    float alpha = colorAlpha;

    /* Touch the unused-at-effect-1 uniforms so GL drivers that complain about
     * declared-but-unread uniforms (some Mali / Adreno builds do) keep them
     * live. The contribution is provably zero. */
    alpha += 0.0 * (u_softness + u_shape +
                    u_alpha6 + u_alpha7 +
                    u_color6.x + u_color7.x);

    gl_FragColor = vec4(col, alpha * u_shaderOpacity);
  }
`
);
function Ce(e, t, n) {
  const o = e.createShader(t);
  if (!o) throw new Error("metal-fx: gl.createShader returned null");
  if (e.shaderSource(o, n), e.compileShader(o), !e.getShaderParameter(o, e.COMPILE_STATUS)) {
    const r = e.getShaderInfoLog(o);
    throw e.deleteShader(o), new Error(`metal-fx: shader compile failed: ${r ?? "(no info log)"}`);
  }
  return o;
}
function Ot(e, t, n) {
  const o = e.createProgram();
  if (!o) throw new Error("metal-fx: gl.createProgram returned null");
  if (e.attachShader(o, t), e.attachShader(o, n), e.linkProgram(o), !e.getProgramParameter(o, e.LINK_STATUS)) {
    const r = e.getProgramInfoLog(o);
    throw e.deleteProgram(o), new Error(`metal-fx: program link failed: ${r ?? "(no info log)"}`);
  }
  return o;
}
const Ke = 140, Qe = 40, Je = 1.6, Ze = 1.3;
let i = null, le = null;
function Ft(e) {
  le = e;
}
const Pt = [
  "u_resolution",
  "u_time",
  "u_color1",
  "u_color2",
  "u_color3",
  "u_color4",
  "u_color5",
  "u_color6",
  "u_color7",
  "u_alpha1",
  "u_alpha2",
  "u_alpha3",
  "u_alpha4",
  "u_alpha5",
  "u_alpha6",
  "u_alpha7",
  "u_intensity",
  "u_scale",
  "u_direction",
  "u_softness",
  "u_distortion",
  "u_complexity",
  "u_shape",
  "u_vignette",
  "u_vigOpacity",
  "u_blur",
  "u_shaderOpacity"
];
function ke(e) {
  e.enable(e.BLEND), e.blendFunc(e.SRC_ALPHA, e.ONE_MINUS_SRC_ALPHA);
  const t = Ce(e, e.VERTEX_SHADER, Lt), n = Ce(e, e.FRAGMENT_SHADER, It), o = Ot(e, t, n);
  e.useProgram(o);
  const r = e.createBuffer();
  if (!r) throw new Error("metal-fx: gl.createBuffer returned null");
  e.bindBuffer(e.ARRAY_BUFFER, r), e.bufferData(e.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, -1, 1, 1, -1, 1, 1]), e.STATIC_DRAW);
  const a = e.getAttribLocation(o, "a_position");
  e.enableVertexAttribArray(a), e.vertexAttribPointer(a, 2, e.FLOAT, !1, 0, 0);
  const s = {};
  for (const d of Pt) s[d] = e.getUniformLocation(o, d);
  return { program: o, buffer: r, uniforms: s };
}
function et() {
  if (i) return i;
  const e = Math.min(Tt, typeof window < "u" && window.devicePixelRatio || 1), t = Math.round(St * e), n = typeof OffscreenCanvas < "u";
  let o, r;
  if (n)
    o = new OffscreenCanvas(t, t), r = o.getContext("webgl", {
      alpha: !0,
      premultipliedAlpha: !1,
      antialias: !1
    });
  else {
    const u = document.createElement("canvas");
    u.width = t, u.height = t, r = u.getContext("webgl", {
      alpha: !0,
      premultipliedAlpha: !1,
      antialias: !1,
      preserveDrawingBuffer: !0
    }) ?? u.getContext("experimental-webgl"), o = u;
  }
  if (!r) throw new Error("metal-fx: WebGL not supported");
  const { program: a, buffer: s, uniforms: d } = ke(r), l = (u) => {
    u.preventDefault(), i && (i.contextLost = !0);
  }, c = () => {
    if (!i) return;
    const u = ke(i.gl);
    i.program = u.program, i.buffer = u.buffer, i.uniforms = u.uniforms, i.presetDirty = !0, i.contextLost = !1, le == null || le();
  };
  return o.addEventListener("webglcontextlost", l, !1), o.addEventListener("webglcontextrestored", c, !1), i = {
    glCanvas: o,
    gl: r,
    program: a,
    buffer: s,
    uniforms: d,
    preset: je.chromatic.modes.dark,
    presetDirty: !0,
    contextLost: !1,
    useOffscreen: n,
    frameBitmap: null,
    startMs: performance.now(),
    pausedMs: 0,
    pausedAtMs: null,
    rafId: 0,
    dpr: e,
    instances: /* @__PURE__ */ new Set(),
    frameCount: 0,
    glowQueue: [],
    glowIdx: 0,
    glowSkip: 0,
    glowPixels: new Uint8Array(t * t * 4),
    glowPixelsW: t,
    glowPixelsH: t
  }, i;
}
function $t() {
  var r;
  if (!i) return;
  const { gl: e, program: t, buffer: n, frameBitmap: o } = i;
  try {
    o == null || o.close(), e.deleteBuffer(n), e.deleteProgram(t), (r = e.getExtension("WEBGL_lose_context")) == null || r.loseContext();
  } catch {
  }
  i = null;
}
let Ee = 0;
function fe() {
  if (!i) return;
  const e = performance.now();
  if (e - Ee < _t) return;
  Ee = e;
  const { gl: t, glCanvas: n } = i, o = n.width, r = n.height;
  (i.glowPixelsW !== o || i.glowPixelsH !== r) && (i.glowPixelsW = o, i.glowPixelsH = r, i.glowPixels = new Uint8Array(o * r * 4)), t.readPixels(0, 0, o, r, t.RGBA, t.UNSIGNED_BYTE, i.glowPixels);
}
const Q = { bx: 0, by: 0 };
function _e(e, t, n) {
  if (!i)
    return Q.bx = 0, Q.by = 0, Q;
  const { glCanvas: o } = i, r = o.width, a = o.height, s = e.dpr, d = e.cssWidth * s, l = e.cssHeight * s, c = Ke * s, u = Qe * s;
  let x = d * (r / c) / e.shaderScale, f = l * (a / u) / e.shaderScale;
  x > r && (x = r), f > a && (f = a);
  const p = (r - x) / 2, h = (a - f) / 2, m = p + t / e.cssWidth * x, S = h + n / e.cssHeight * f;
  return Q.bx = Math.round(m), Q.by = Math.round(a - 1 - S), Q;
}
const z = { r: 0, g: 0, b: 0, lum: 0, count: 0 };
function tt(e, t, n, o, r, a) {
  const s = Math.max(1, a | 0), d = Math.max(0, o - s), l = Math.min(t, o + s + 1), c = Math.max(0, r - s), u = Math.min(n, r + s + 1);
  z.r = 0, z.g = 0, z.b = 0, z.lum = 0, z.count = 0;
  for (let x = c; x < u; x++) {
    const f = x * t;
    for (let p = d; p < l; p++) {
      const h = (f + p) * 4;
      z.r += e[h], z.g += e[h + 1], z.b += e[h + 2], z.lum += (0.2126 * e[h] + 0.7152 * e[h + 1] + 0.0722 * e[h + 2]) / 255, z.count++;
    }
  }
  return z;
}
const A = { r: 255, g: 255, b: 255 };
function Le(e, t, n, o) {
  if (!i) return 0;
  fe();
  const r = _e(e, t, n), a = tt(i.glowPixels, i.glowPixelsW, i.glowPixelsH, r.bx, r.by, o);
  return a.count > 0 ? a.lum / a.count : 0;
}
function Ht(e, t, n, o) {
  if (!i)
    return A.r = 255, A.g = 255, A.b = 255, A;
  fe();
  const r = _e(e, t, n), a = tt(i.glowPixels, i.glowPixelsW, i.glowPixelsH, r.bx, r.by, o);
  return a.count === 0 ? (A.r = 255, A.g = 255, A.b = 255, A) : (A.r = a.r / a.count, A.g = a.g / a.count, A.b = a.b / a.count, A);
}
function Nt(e, t, n, o) {
  if (!i)
    return A.r = 255, A.g = 255, A.b = 255, A;
  fe();
  const r = _e(e, t, n), { glowPixels: a, glowPixelsW: s, glowPixelsH: d } = i, l = Math.max(1, o | 0), c = Math.max(0, r.bx - l), u = Math.min(s, r.bx + l + 1), x = Math.max(0, r.by - l), f = Math.min(d, r.by + l + 1);
  let p = -1;
  A.r = 255, A.g = 255, A.b = 255;
  for (let h = x; h < f; h++) {
    const m = h * s;
    for (let S = c; S < u; S++) {
      const w = (m + S) * 4, T = a[w], F = a[w + 1], P = a[w + 2], $ = Math.max(T, F, P), k = Math.min(T, F, P), D = ($ > 0 ? ($ - k) / $ : 0) * (0.35 + 0.65 * ($ / 255));
      D > p && (p = D, A.r = T, A.g = F, A.b = P);
    }
  }
  return A;
}
Ft(() => {
  i && i.instances.size > 0 && i.pausedAtMs === null && te();
});
typeof document < "u" && document.addEventListener("visibilitychange", () => {
  !i || i.pausedAtMs !== null || i.contextLost || (document.hidden ? Me() : i.instances.size > 0 && te());
});
function Wt(e) {
  const t = et(), n = e.hostCanvas.getContext("2d", { alpha: !0 });
  if (!n) throw new Error("metal-fx: canvas 2D context unavailable");
  const o = e.scale ?? 1, r = {
    canvas: e.hostCanvas,
    ctx: n,
    cssWidth: e.cssWidth,
    cssHeight: e.cssHeight,
    cornerRadius: e.cornerRadius,
    kind: e.kind,
    ringCssPx: e.ringCssPx ?? (e.kind === "circle" ? 2 : 1) * o,
    shaderScale: e.shaderScale ?? (e.kind === "circle" ? Ze : Je) * o,
    opacityMul: e.opacityMul ?? 1,
    visible: !0,
    paused: e.paused ?? !1,
    everCopied: !1,
    dpr: typeof window < "u" && window.devicePixelRatio || 1,
    scale: o,
    onAfterFrame: e.onAfterFrame,
    onFirstCopy: e.onFirstCopy
  };
  return nt(r), t.instances.add(r), t.rafId === 0 && t.pausedAtMs === null && te(), r;
}
function Gt(e) {
  if (!i) return;
  i.instances.delete(e);
  const t = i.glowQueue.indexOf(e);
  t !== -1 && i.glowQueue.splice(t, 1), i.instances.size === 0 && (Me(), $t());
}
function zt(e) {
  i && (i.glowQueue.includes(e) || i.glowQueue.push(e));
}
function Dt(e) {
  if (!i) return;
  const t = i.glowQueue.indexOf(e);
  t !== -1 && i.glowQueue.splice(t, 1);
}
function oe(e, t) {
  let n = !1;
  t.cssWidth !== void 0 && t.cssWidth !== e.cssWidth && (e.cssWidth = t.cssWidth, n = !0), t.cssHeight !== void 0 && t.cssHeight !== e.cssHeight && (e.cssHeight = t.cssHeight, n = !0), t.cornerRadius !== void 0 && (e.cornerRadius = t.cornerRadius), t.scale !== void 0 && (e.scale = t.scale), t.kind !== void 0 && t.kind !== e.kind && (e.kind = t.kind, t.shaderScale === void 0 && (e.shaderScale = (t.kind === "circle" ? Ze : Je) * e.scale), t.ringCssPx === void 0 && (e.ringCssPx = (t.kind === "circle" ? 2 : 1) * e.scale)), t.shaderScale !== void 0 && (e.shaderScale = t.shaderScale), t.ringCssPx !== void 0 && (e.ringCssPx = t.ringCssPx), t.opacityMul !== void 0 && (e.opacityMul = t.opacityMul), t.paused !== void 0 && t.paused !== e.paused && (e.paused = t.paused, !t.paused && i && i.rafId === 0 && i.pausedAtMs === null && !i.contextLost && te()), n && nt(e);
}
function Bt(e, t) {
  e.visible = t, t && i && i.rafId === 0 && i.pausedAtMs === null && !i.contextLost && te();
}
function Ut(e, t) {
  const n = et();
  n.preset = je[e].modes[t], n.presetDirty = !0;
}
function lo() {
  !i || i.pausedAtMs !== null || (i.pausedAtMs = performance.now(), Me());
}
function co() {
  !i || i.pausedAtMs === null || (i.pausedMs += performance.now() - i.pausedAtMs, i.pausedAtMs = null, i.instances.size > 0 && te());
}
let we = null;
function Xt(e) {
  we = e;
}
function nt(e) {
  e.dpr = typeof window < "u" && window.devicePixelRatio || 1;
  const t = Math.max(1, Math.round(e.cssWidth * e.dpr)), n = Math.max(1, Math.round(e.cssHeight * e.dpr));
  e.canvas.width !== t && (e.canvas.width = t), e.canvas.height !== n && (e.canvas.height = n);
}
function qt(e) {
  const { ctx: t, dpr: n, canvas: o } = e, r = e.ringCssPx * n, a = o.width, s = o.height, d = Math.max(0, (e.cornerRadius - e.ringCssPx) * n);
  t.save(), t.globalCompositeOperation = "destination-out", t.fillStyle = "#000", t.beginPath(), t.roundRect(r, r, a - 2 * r, s - 2 * r, d), t.fill(), t.restore();
}
function Vt(e) {
  var p;
  if (!i) return;
  const t = i.frameBitmap ?? i.glCanvas, n = e.dpr, o = e.canvas.width, r = e.canvas.height;
  if (o < 1 || r < 1) return;
  const a = i.glCanvas.width, s = i.glCanvas.height, d = Ke * n, l = Qe * n;
  let c = o * (a / d) / e.shaderScale, u = r * (s / l) / e.shaderScale;
  c > a && (c = a), u > s && (u = s);
  const x = Math.max(0, (a - c) / 2), f = Math.max(0, (s - u) / 2);
  if (e.ctx.clearRect(0, 0, o, r), e.opacityMul < 1 && (e.ctx.globalAlpha = e.opacityMul), e.ctx.drawImage(t, x, f, c, u, 0, 0, o, r), e.opacityMul < 1 && (e.ctx.globalAlpha = 1), qt(e), e.onFirstCopy) {
    const h = e.onFirstCopy;
    e.onFirstCopy = void 0, h();
  }
  (p = e.onAfterFrame) == null || p.call(e);
}
function Yt() {
  if (!i) return;
  const { gl: e, uniforms: t, preset: n, glCanvas: o } = i;
  t.u_resolution && e.uniform2f(t.u_resolution, o.width, o.height);
  for (let r = 0; r < 7; r++) {
    const a = t[`u_color${r + 1}`];
    if (a) {
      const [d, l, c] = xt(n.colors[r]);
      e.uniform3f(a, d, l, c);
    }
    const s = t[`u_alpha${r + 1}`];
    s && e.uniform1f(s, n.alphas[r]);
  }
  t.u_intensity && e.uniform1f(t.u_intensity, n.intensity), t.u_scale && e.uniform1f(t.u_scale, n.scale), t.u_direction && e.uniform1f(t.u_direction, n.direction * Math.PI / 180), t.u_softness && e.uniform1f(t.u_softness, n.softness), t.u_distortion && e.uniform1f(t.u_distortion, n.distortion), t.u_complexity && e.uniform1f(t.u_complexity, n.complexity), t.u_shape && e.uniform1f(t.u_shape, n.shape), t.u_vignette && e.uniform1f(t.u_vignette, n.vignette), t.u_vigOpacity && e.uniform1f(t.u_vigOpacity, n.vigOpacity), t.u_blur && e.uniform1f(t.u_blur, n.blur), t.u_shaderOpacity && e.uniform1f(t.u_shaderOpacity, n.shaderOpacity), i.presetDirty = !1;
}
function jt(e) {
  if (!i) return;
  const { gl: t, uniforms: n, preset: o, glCanvas: r } = i, a = (e - i.startMs - i.pausedMs) / 1e3 * o.speed;
  t.viewport(0, 0, r.width, r.height), t.clearColor(0, 0, 0, 0), t.clear(t.COLOR_BUFFER_BIT), i.presetDirty && Yt(), n.u_time && t.uniform1f(n.u_time, a), t.drawArrays(t.TRIANGLES, 0, 6), i.frameCount++;
}
let Ie = 0;
function ot(e) {
  var n;
  if (!i) return;
  if (i.contextLost) {
    i.rafId = 0;
    return;
  }
  let t = !1;
  for (const o of i.instances)
    if (o.visible && (!o.paused || !o.everCopied)) {
      t = !0;
      break;
    }
  if (!t) {
    i.rafId = 0;
    return;
  }
  if (i.rafId = requestAnimationFrame(ot), !(e - Ie < wt)) {
    Ie = e, jt(e), i.useOffscreen && (i.glowQueue.length > 0 && fe(), (n = i.frameBitmap) == null || n.close(), i.frameBitmap = i.glCanvas.transferToImageBitmap());
    for (const o of i.instances)
      o.visible && (o.paused && o.everCopied || (Vt(o), o.everCopied = !0));
    if (we && i.glowQueue.length > 0 && ++i.glowSkip % Mt === 0) {
      const o = i.glowQueue;
      i.glowIdx >= o.length && (i.glowIdx = 0);
      const r = o[i.glowIdx];
      r.visible && !r.paused && we(r, e), i.glowIdx++;
    }
  }
}
function te() {
  !i || i.rafId !== 0 || (i.rafId = requestAnimationFrame(ot));
}
function Me() {
  i && (i.rafId !== 0 && cancelAnimationFrame(i.rafId), i.rafId = 0);
}
const ce = {
  linear: (e) => e,
  smoothstep: (e) => e * e * (3 - 2 * e)
};
function J(e, t, n, o = ce.linear) {
  return { from: e, to: t, dur: n, ease: o, startMs: -1, val: e, done: !1 };
}
function Z(e, t) {
  e.startMs = t, e.val = e.from, e.done = !1;
}
function Oe(e, t) {
  if (e.done || e.startMs < 0) return e.val;
  const n = Math.min(1, (t - e.startMs) / e.dur);
  return e.val = e.from + (e.to - e.from) * e.ease(n), n >= 1 && (e.done = !0), e.val;
}
const Kt = 1.5, ae = 1 / 3, Qt = 4 * ae, Jt = 2 * ae, Zt = 2 * ae, en = 1.35 * ae, tn = 13 * ae;
function Re(e, t, n) {
  const o = Math.max(0, Math.min(n, Math.min(e, t) / 2));
  return 2 * Math.max(0, e - 2 * o) + 2 * Math.max(0, t - 2 * o) + 2 * Math.PI * o;
}
function Ae(e, t, n, o) {
  return o === "circle" ? 2 * Math.PI * Math.max(0, Math.min(n, Math.min(e, t) / 2)) : Re(e, t, n);
}
function re(e, t, n, o, r, a, s, d) {
  const l = d || { x: 0, y: 0 }, c = Math.max(0, Math.min(o, Math.min(t, n) / 2));
  if (s === "circle") {
    const w = 2 * Math.PI * c;
    if (w <= 1e-4)
      return l.x = t * 0.5, l.y = n * 0.5, l;
    e = (e % w + w) % w;
    const T = -Math.PI / 2 + e / w * Math.PI * 2, F = Math.max(0, c - r + a);
    return l.x = t * 0.5 + F * Math.cos(T), l.y = n * 0.5 + F * Math.sin(T), l;
  }
  const u = Math.max(0, t - 2 * c), x = Math.max(0, n - 2 * c), f = Math.PI * c / 2, p = 2 * (u + x) + 4 * f;
  e = (e % p + p) % p;
  const h = Math.max(0, c - r + a);
  let m = e;
  if (m < u)
    return l.x = c + m, l.y = r - a, l;
  if (m -= u, m < f) {
    const w = -Math.PI / 2 + (f > 0 ? m / f : 0) * (Math.PI / 2);
    return l.x = t - c + h * Math.cos(w), l.y = c + h * Math.sin(w), l;
  }
  if (m -= f, m < x)
    return l.x = t - r + a, l.y = c + m, l;
  if (m -= x, m < f) {
    const w = (f > 0 ? m / f : 0) * (Math.PI / 2);
    return l.x = t - c + h * Math.cos(w), l.y = n - c + h * Math.sin(w), l;
  }
  if (m -= f, m < u)
    return l.x = t - c - m, l.y = n - r + a, l;
  if (m -= u, m < f) {
    const w = Math.PI / 2 + (f > 0 ? m / f : 0) * (Math.PI / 2);
    return l.x = c + h * Math.cos(w), l.y = n - c + h * Math.sin(w), l;
  }
  if (m -= f, m < x)
    return l.x = r - a, l.y = n - c - m, l;
  m -= x;
  const S = Math.PI + (f > 0 ? m / f : 0) * (Math.PI / 2);
  return l.x = c + h * Math.cos(S), l.y = c + h * Math.sin(S), l;
}
function Fe(e, t) {
  const n = e * 2 / t;
  let o = "";
  for (let r = 0; r <= t; r++) {
    const a = -e + r * n;
    o += (r === 0 ? "M " : "L ") + a.toFixed(3) + " 0 ";
  }
  return o;
}
const pe = { x: 0, y: 0 }, me = { x: 0, y: 0 };
function nn(e, t, n, o, r, a) {
  return re(e - 0.1, t, n, o, r, 0, a, pe), re(e + 0.1, t, n, o, r, 0, a, me), Math.atan2(me.y - pe.y, me.x - pe.x);
}
function Pe(e, t, n) {
  const o = Math.max(0, Math.min(1, (n - e) / (t - e)));
  return o * o * (3 - 2 * o);
}
function on(e) {
  const t = Ae(e.width, e.height, e.cornerRadius, e.kind), n = Kt * (e.scale ?? 1), o = [];
  for (let r = 0; r < Te; r++) {
    const a = r / Te * t, s = re(a, e.width, e.height, e.cornerRadius, n, 0, e.kind);
    o.push({ x: s.x, y: s.y, arc: a });
  }
  return o;
}
function rn(e, t) {
  const { width: n, height: o, cornerRadius: r } = e, a = e.scale ?? 1, s = e.kind === "circle" ? 2 : 1, d = Math.max(0, r - s), l = (-200 * a).toFixed(0), c = l, u = (540 * a).toFixed(0), x = (440 * a).toFixed(0), f = `x="${l}" y="${c}" width="${u}" height="${x}"`, p = `${f} filterUnits="userSpaceOnUse" color-interpolation-filters="sRGB"`, h = (S) => (S * a).toFixed(3), m = (S) => (S * a).toFixed(3);
  return [
    "<defs>",
    `<filter id="${t}_bXl" ${p}><feGaussianBlur stdDeviation="${m(8.4)}"/></filter>`,
    `<filter id="${t}_bLg" ${p}><feGaussianBlur stdDeviation="${m(4.8)}"/></filter>`,
    `<filter id="${t}_bMd" ${p}><feGaussianBlur stdDeviation="${m(2.1)}"/></filter>`,
    `<filter id="${t}_bSm" ${p}><feGaussianBlur stdDeviation="${m(0.9)}"/></filter>`,
    `<filter id="${t}_ebO" ${p}><feGaussianBlur stdDeviation="${m(Zt)}"/></filter>`,
    `<filter id="${t}_ebC" ${p}><feGaussianBlur stdDeviation="${m(en)}"/></filter>`,
    `<radialGradient id="${t}_fg" cx="0.5" cy="0.5" r="0.5"><stop offset="0" stop-color="white"/><stop offset="0.30" stop-color="white"/><stop offset="0.65" stop-color="#404040"/><stop offset="1" stop-color="black"/></radialGradient>`,
    `<mask id="${t}_fm" maskUnits="userSpaceOnUse" ${f}><rect ${f} fill="black"/><circle id="${t}_fc" cx="0" cy="0" r="${(tn * a).toFixed(3)}" fill="url(#${t}_fg)"/></mask>`,
    `<mask id="${t}_rm" maskUnits="userSpaceOnUse" ${f}><rect ${f} fill="#808080"/><rect x="0" y="0" width="${n}" height="${o}" rx="${r}" ry="${r}" fill="white"/><rect x="${s}" y="${s}" width="${n - s * 2}" height="${o - s * 2}" rx="${d}" ry="${d}" fill="black"/></mask>`,
    "</defs>",
    // Safari clips mask to the masked element's bbox; our horizontal strokes
    // have zero height, so the mask becomes a sliver. These spacer rects
    // inflate the bbox to the full filter region.
    `<g id="${t}_h" mask="url(#${t}_rm)" opacity="0">`,
    `<rect ${f} fill="none" pointer-events="none"/>`,
    `<g id="${t}_hI" stroke="white">`,
    `<path id="${t}_pXl" stroke-width="${h(26.4)}" stroke-linecap="round" stroke-linejoin="round" fill="none" opacity="0.385" filter="url(#${t}_bXl)"/>`,
    `<path id="${t}_pLg" stroke-width="${h(15.6)}" stroke-linecap="round" stroke-linejoin="round" fill="none" opacity="0.595" filter="url(#${t}_bLg)"/>`,
    `<path id="${t}_pMd" stroke-width="${h(7.2)}" stroke-linecap="round" stroke-linejoin="round" fill="none" opacity="0.70" filter="url(#${t}_bMd)"/>`,
    `<path id="${t}_pSm" stroke-width="${h(3)}" stroke-linecap="round" stroke-linejoin="round" fill="none" opacity="0.70" filter="url(#${t}_bSm)"/>`,
    "</g></g>",
    `<g id="${t}_e" mask="url(#${t}_rm)" opacity="0">`,
    `<rect ${f} fill="none" pointer-events="none"/>`,
    `<g mask="url(#${t}_fm)">`,
    `<g id="${t}_eI" stroke="white">`,
    `<path id="${t}_eO" stroke-width="${h(Qt)}" stroke-linecap="round" stroke-linejoin="round" fill="none" opacity="0.85" filter="url(#${t}_ebO)"/>`,
    `<path id="${t}_eC" stroke-width="${h(Jt)}" stroke-linecap="round" stroke-linejoin="round" fill="none" opacity="1.0" filter="url(#${t}_ebC)"/>`,
    "</g></g></g>"
  ].join("");
}
const an = 875e-5, $e = 0.08, He = 0.32, sn = 0.05, ln = 3e3, Ne = 0.85, se = 0.34, ge = 1500, cn = 15, dn = 75e-4, fn = 120, un = 1.5, hn = 7.8, pn = 9.13952, mn = 1, gn = 1 / 3, xn = 0.8, vn = 3.51, We = 2e3, xe = 400, bn = 2.625, wn = 1.008, yn = 0.31, rt = 140, at = 40, it = 20;
let _n = 0;
const q = { x: 0, y: 0 };
function Ge(e, t) {
  const n = `mfxg_${++_n}`, o = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  o.setAttribute("class", "metal-fx-glow-svg"), o.setAttribute("preserveAspectRatio", "none"), o.setAttribute("viewBox", `0 0 ${t.width} ${t.height}`), o.innerHTML = rn(t, n), e.appendChild(o);
  const r = (w) => o.querySelector(`#${n}_${w}`), a = r("h"), s = r("hI"), d = r("e"), l = r("eI"), c = r("fc"), u = Ae(t.width, t.height, t.cornerRadius, t.kind) / Re(rt, at, it), x = Math.max(1, hn * u), f = Math.max(0.6, pn * gn * u), p = Fe(x, Rt), h = Fe(f, At), m = [r("pXl"), r("pLg"), r("pMd"), r("pSm")], S = [r("eO"), r("eC")];
  for (const w of m) w.setAttribute("d", p);
  for (const w of S) w.setAttribute("d", h);
  return s.style.transformOrigin = "0 0", l.style.transformOrigin = "0 0", s.style.willChange = "transform", l.style.willChange = "transform", s.style.transition = "transform 100ms linear", l.style.transition = "transform 100ms linear", a.style.willChange = "opacity", d.style.willChange = "opacity", a.style.transition = "opacity 100ms linear", d.style.transition = "opacity 100ms linear", c.style.willChange = "transform", {
    svg: o,
    haloGroup: a,
    haloInner: s,
    extraGroup: d,
    extraInner: l,
    fadeCircle: c,
    width: t.width,
    height: t.height,
    cornerRadius: t.cornerRadius,
    kind: t.kind,
    scale: t.scale ?? 1,
    perim: on(t),
    currentIdx: 0,
    appearedAt: 0,
    glowOpacity: 0,
    relocTween: null,
    relocNextIdx: -1,
    wanderS: 0,
    wanderTargetS: 0,
    wanderFrames: 0,
    tintFrom: { r: 255, g: 255, b: 255 },
    tintTarget: { r: 255, g: 255, b: 255 },
    tintTween: null,
    tintHoldUntil: 0,
    lastHaloStroke: "",
    lastExtraStroke: ""
  };
}
function Mn(e, t, n, o, r = "dark") {
  var b;
  const { width: a, height: s, cornerRadius: d, perim: l } = e;
  if (l.length === 0) return;
  const c = 2;
  let u = -1, x = e.currentIdx, f = 0;
  for (let g = 0; g < l.length; g++) {
    const v = l[g], _ = Le(t, v.x, v.y, c);
    _ > u && (u = _, x = g), g === e.currentIdx && (f = _);
  }
  const p = e.appearedAt > 0 && n - e.appearedAt < ln, h = se + (Ne - se) * Pe($e, He, f), m = !p && u - f > sn;
  if (!e.relocTween || e.relocTween.done)
    if (e.appearedAt === 0)
      e.currentIdx = x, e.appearedAt = n, e.wanderS = 0, e.wanderTargetS = 0, e.wanderFrames = 0, e.relocTween = J(0, h, ge, ce.smoothstep), Z(e.relocTween, n);
    else if ((b = e.relocTween) != null && b.done && e.relocTween.to === 0) {
      e.currentIdx = e.relocNextIdx, e.appearedAt = n, e.wanderS = 0, e.wanderTargetS = 0, e.wanderFrames = 0;
      const g = l[e.currentIdx], v = Le(t, g.x, g.y, c), _ = se + (Ne - se) * Pe($e, He, v);
      e.relocTween = J(0, _, ge, ce.smoothstep), Z(e.relocTween, n);
    } else m ? (e.relocNextIdx = x, e.relocTween = J(e.glowOpacity, 0, ge, ce.smoothstep), Z(e.relocTween, n)) : e.glowOpacity += (h - e.glowOpacity) * an;
  e.relocTween && (e.glowOpacity = Oe(e.relocTween, n)), e.glowOpacity = Math.max(0, Math.min(1, e.glowOpacity));
  const S = Ae(a, s, d, e.kind) / Re(rt, at, it), w = cn * S;
  e.wanderFrames++ >= fn && (e.wanderTargetS = (Math.random() * 2 - 1) * w, e.wanderFrames = 0), e.wanderS += (e.wanderTargetS - e.wanderS) * dn;
  const T = l[e.currentIdx].arc + e.wanderS, F = un * e.scale;
  re(T, a, s, d, F, 0, e.kind, q);
  const P = q.x, $ = q.y, k = nn(T, a, s, d, F, e.kind), N = `translate(${P.toFixed(3)}px,${$.toFixed(3)}px) rotate(${k.toFixed(4)}rad)`;
  e.haloInner.style.transform = N;
  const D = mn * S * e.scale;
  re(T, a, s, d, F, D, e.kind, q), e.extraInner.style.transform = `translate(${q.x.toFixed(3)}px,${q.y.toFixed(3)}px) rotate(${k.toFixed(4)}rad)`, e.fadeCircle.style.transform = `translate(${q.x.toFixed(3)}px,${q.y.toFixed(3)}px)`;
  const H = r === "light", y = H ? Nt(t, P, $, c) : Ht(t, P, $, c);
  e.tintTween ? e.tintTween.done && (H ? (e.tintFrom = {
    r: e.tintFrom.r + (e.tintTarget.r - e.tintFrom.r) * e.tintTween.val,
    g: e.tintFrom.g + (e.tintTarget.g - e.tintFrom.g) * e.tintTween.val,
    b: e.tintFrom.b + (e.tintTarget.b - e.tintFrom.b) * e.tintTween.val
  }, e.tintTarget = { ...y }, e.tintTween = J(0, 1, xe), Z(e.tintTween, n)) : n >= e.tintHoldUntil && (e.tintFrom = { ...e.tintTarget }, e.tintTarget = { ...y }, e.tintTween = J(0, 1, xe), Z(e.tintTween, n), e.tintHoldUntil = n + We)) : (e.tintFrom = { ...y }, e.tintTarget = { ...y }, e.tintTween = J(0, 1, xe), Z(e.tintTween, n), e.tintHoldUntil = H ? 0 : n + We), Oe(e.tintTween, n);
  const M = e.tintTween.val;
  let E, L, W;
  if (H)
    E = Math.round(e.tintFrom.r + (e.tintTarget.r - e.tintFrom.r) * M), L = Math.round(e.tintFrom.g + (e.tintTarget.g - e.tintFrom.g) * M), W = Math.round(e.tintFrom.b + (e.tintTarget.b - e.tintFrom.b) * M);
  else {
    const g = e.tintFrom.r + (e.tintTarget.r - e.tintFrom.r) * M, v = e.tintFrom.g + (e.tintTarget.g - e.tintFrom.g) * M, _ = e.tintFrom.b + (e.tintTarget.b - e.tintFrom.b) * M, C = Math.max(g, v, _) || 1;
    E = Math.round(255 * (g / C)), L = Math.round(255 * (v / C)), W = Math.round(255 * (_ / C));
  }
  const G = `rgb(${E},${L},${W})`;
  if (G !== e.lastHaloStroke && (e.lastHaloStroke = G, e.haloInner.style.stroke = G), H) {
    const g = vt(E, L, W), [v, _, C] = bt(g[0], Math.min(1, g[1] * bn), Math.max(yn, g[2] * wn)), I = `rgb(${v},${_},${C})`;
    I !== e.lastExtraStroke && (e.lastExtraStroke = I, e.extraInner.style.stroke = I);
  } else e.lastExtraStroke !== "#ffffff" && (e.lastExtraStroke = "#ffffff", e.extraInner.style.stroke = "#ffffff");
  const B = Math.max(0, Math.min(1, o));
  e.haloGroup.style.opacity = (e.glowOpacity * xn * B).toFixed(3), e.extraGroup.style.opacity = Math.min(1, e.glowOpacity * vn * B).toFixed(3);
}
const ye = 12, ze = 32, De = 1, Be = 0.55, Rn = 1, An = 1, Sn = 0.85, Tn = 0, Cn = 1.3, Ue = 3.6, kn = 0.7, En = 1, Ln = 0.52, In = 1, On = 0.044, Fn = 235, Pn = 2.535, $n = 0.7, Hn = 0.5, Nn = /* @__PURE__ */ new Set(["INPUT", "TEXTAREA", "SELECT", "OPTION"]);
function Wn(e, t) {
  const n = Math.max(e.left - t.right, t.left - e.right, 0), o = Math.max(e.top - t.bottom, t.top - e.bottom, 0);
  return Math.sqrt(n * n + o * o);
}
function Gn(e, t, n, o) {
  return !(Math.min(e.bottom, t.bottom) - Math.max(e.top, t.top) < n || Math.max(
    e.left - t.right,
    t.left - e.right,
    0
  ) > o);
}
function zn(e, t, n, o) {
  return Math.min(e.right, t.right) - Math.max(e.left, t.left) < n ? !1 : Math.max(
    e.top - t.bottom,
    t.top - e.bottom,
    0
  ) <= o;
}
function j(e, t, n, o, r, a) {
  const s = Math.max(0, Math.min(a, o * 0.5, r * 0.5)), d = e.roundRect;
  if (typeof d == "function") {
    d.call(e, t, n, o, r, s);
    return;
  }
  e.moveTo(t + s, n), e.lineTo(t + o - s, n), e.quadraticCurveTo(t + o, n, t + o, n + s), e.lineTo(t + o, n + r - s), e.quadraticCurveTo(t + o, n + r, t + o - s, n + r), e.lineTo(t + s, n + r), e.quadraticCurveTo(t, n + r, t, n + r - s), e.lineTo(t, n + s), e.quadraticCurveTo(t, n, t + s, n);
}
function st(e, t, n, o, r) {
  if (!r.flipX && !r.flipY) {
    e.drawImage(t, 0, 0, n, o, r.x, r.y, r.w, r.h);
    return;
  }
  e.save(), r.flipX && (e.translate(r.x + r.w, 0), e.scale(-1, 1)), r.flipY && (e.translate(0, r.y + r.h), e.scale(1, -1)), e.drawImage(
    t,
    0,
    0,
    n,
    o,
    r.flipX ? 0 : r.x,
    r.flipY ? 0 : r.y,
    r.w,
    r.h
  ), e.restore();
}
const Dn = 4;
function Bn(e, t, n, o, r, a, s) {
  if (o <= 2 * s || r <= 2 * s) {
    e.beginPath(), j(e, t, n, o, r, a), e.clip();
    return;
  }
  e.beginPath(), j(e, t, n, o, r, a), j(e, t + s, n + s, o - 2 * s, r - 2 * s, Math.max(0, a - s)), e.clip("evenodd");
}
function Un(e, t, n, o, r, a, s, d, l, c, u) {
  const x = Math.max(1, Math.round((ye + Dn * 3) * u));
  let f = Math.max(0, s), p = !0;
  for (let h = 0; h < 3 && f > 1e-4; h++) {
    const m = Math.min(1, f);
    e.save(), Bn(e, c.x, c.y, c.w, c.h, c.r, x), e.globalCompositeOperation = p ? "source-over" : "lighter", p = !1, e.globalAlpha = m, st(e, t, n, o, l), e.globalAlpha = 1, e.globalCompositeOperation = "destination-in", e.fillStyle = d, e.fillRect(0, 0, r, a), e.restore(), f -= m;
  }
}
function lt(e, t, n, o, r, a, s) {
  const d = s | 0;
  if (d < 1 || o <= 2 * d || r <= 2 * d) {
    e.beginPath(), j(e, t, n, o, r, a), e.clip();
    return;
  }
  e.beginPath(), j(e, t, n, o, r, a), j(e, t + d, n + d, o - 2 * d, r - 2 * d, Math.max(0, a - d)), e.clip("evenodd");
}
function Xn(e, t, n, o, r, a, s, d, l, c, u, x) {
  let f = d * u, p = !0;
  for (let h = 0; h < 3 && f > 1e-4; h++) {
    const m = Math.min(1, f);
    e.save(), lt(e, s.x, s.y, s.w, s.h, s.r, l), e.globalCompositeOperation = p ? "source-over" : "lighter", p = !1, e.globalAlpha = m, st(e, t, n, o, x), e.globalAlpha = 1, e.globalCompositeOperation = "destination-in", e.fillStyle = c, e.fillRect(0, 0, r, a), e.restore(), f -= m;
  }
}
function qn(e, t, n, o, r, a, s, d) {
  const l = e.createLinearGradient(o, r, a, s);
  l.addColorStop(0, `rgba(255,255,255,${d.toFixed(3)})`), l.addColorStop(0.5, `rgba(255,255,255,${(d * 0.45).toFixed(3)})`), l.addColorStop(1, "rgba(255,255,255,0)"), e.save(), lt(e, t.x, t.y, t.w, t.h, t.r, n), e.globalCompositeOperation = "lighter", e.lineWidth = n * 2, e.strokeStyle = l, e.beginPath(), j(e, t.x, t.y, t.w, t.h, t.r), e.stroke(), e.restore();
}
function ct(e) {
  const t = getComputedStyle(e), n = [
    parseFloat(t.borderTopLeftRadius) || 0,
    parseFloat(t.borderTopRightRadius) || 0,
    parseFloat(t.borderBottomRightRadius) || 0,
    parseFloat(t.borderBottomLeftRadius) || 0
  ].filter((o) => o > 0);
  return n.length ? Math.min.apply(null, n) : 0;
}
function dt(e) {
  const t = getComputedStyle(e), n = Math.max(
    parseFloat(t.borderTopWidth) || 0,
    parseFloat(t.borderRightWidth) || 0,
    parseFloat(t.borderBottomWidth) || 0,
    parseFloat(t.borderLeftWidth) || 0
  );
  let o = 0, r = 0;
  const a = t.boxShadow;
  if (a && a !== "none") {
    const c = a.replace(/rgba?\([^)]*\)/g, (f) => f.replace(/,/g, "\0")).split(/,\s*/);
    let u = 1 / 0, x = 1 / 0;
    for (const f of c) {
      const p = f.match(/-?\d+(?:\.\d+)?px/g);
      if (!p || p.length < 4) continue;
      const h = parseFloat(p[3]);
      h > 0 && (/\binset\b/.test(f) ? h < u && (u = h) : h < x && (x = h));
    }
    Number.isFinite(u) && (o = u), Number.isFinite(x) && (r = x);
  }
  const s = Math.max(n, r);
  return { width: Math.max(n, o, r) || 1, outerCssPx: s };
}
function Xe(e) {
  e.cornerRadius = ct(e.el);
  const t = dt(e.el);
  e.hairlineWidth = t.width, e.hairlineOuterCssPx = t.outerCssPx;
}
function Vn(e) {
  typeof ResizeObserver < "u" && (e.resizeObserver = new ResizeObserver(() => Xe(e)), e.resizeObserver.observe(e.el)), typeof MutationObserver < "u" && (e.mutationObserver = new MutationObserver(() => Xe(e)), e.mutationObserver.observe(e.el, {
    attributes: !0,
    attributeFilter: ["style", "class"]
  }));
}
function Yn(e) {
  var t, n;
  (t = e.resizeObserver) == null || t.disconnect(), e.resizeObserver = null, (n = e.mutationObserver) == null || n.disconnect(), e.mutationObserver = null;
}
const ee = /* @__PURE__ */ new Set();
function jn(e, t, n) {
  if (typeof document > "u" || Nn.has(e.tagName)) return null;
  for (const p of ee)
    if (p.el === e) return p;
  const o = document.createElement("div");
  o.setAttribute("data-metal-fx-reflection", ""), o.setAttribute("aria-hidden", "true");
  const r = document.createElement("canvas");
  r.className = "metal-fx-reflection-canvas";
  const a = r.getContext("2d", { alpha: !0 });
  if (!a) return null;
  const s = document.createElement("canvas");
  s.className = "metal-fx-reflection-stroke-canvas";
  const d = s.getContext("2d", { alpha: !0 });
  if (!d) return null;
  o.appendChild(r), o.appendChild(s);
  const l = getComputedStyle(e);
  let c = !1;
  l.position === "static" && (e.style.position = "relative", c = !0);
  let u = !1;
  l.isolation !== "isolate" && (e.style.isolation = "isolate", u = !0), e.setAttribute("data-metal-fx-reflect-host", ""), e.insertBefore(o, e.firstChild);
  const x = dt(e), f = {
    el: e,
    anchor: t,
    anchorEl: n,
    wrap: o,
    canvas: r,
    ctx: a,
    strokeCanvas: s,
    strokeCtx: d,
    cornerRadius: ct(e),
    hairlineWidth: x.width,
    hairlineOuterCssPx: x.outerCssPx,
    appliedPositionRelative: c,
    appliedIsolation: u,
    resizeObserver: null,
    mutationObserver: null
  };
  return Vn(f), ee.add(f), f;
}
function Kn(e) {
  for (const t of ee)
    if (t.el === e) {
      Yn(t), t.canvas.width = 0, t.canvas.height = 0, t.strokeCanvas.width = 0, t.strokeCanvas.height = 0, t.wrap.parentNode === t.el && t.el.removeChild(t.wrap), t.el.removeAttribute("data-metal-fx-reflect-host"), t.appliedPositionRelative && (t.el.style.position = ""), t.appliedIsolation && (t.el.style.isolation = ""), ee.delete(t);
      return;
    }
}
function Qn() {
  if (ee.size === 0) return;
  const e = typeof window < "u" && window.devicePixelRatio || 1, t = /* @__PURE__ */ new Map();
  for (const n of ee) {
    const o = n.el.getBoundingClientRect();
    let r = t.get(n.anchorEl);
    if (r || (r = n.anchorEl.getBoundingClientRect(), t.set(n.anchorEl, r)), o.width < 1 || o.height < 1 || r.width < 1 || r.height < 1) continue;
    if (!Gn(r, o, De, ze) && !zn(r, o, De, ze)) {
      n.canvas.width !== 1 && (n.canvas.width = 1, n.canvas.height = 1), n.strokeCanvas.width !== 1 && (n.strokeCanvas.width = 1, n.strokeCanvas.height = 1);
      continue;
    }
    const a = n.anchor.canvas, s = a.width | 0, d = a.height | 0;
    if (s < 4 || d < 4) continue;
    const l = (r.left + r.right) * 0.5, c = (r.top + r.bottom) * 0.5, u = (o.left + o.right) * 0.5, x = (o.top + o.bottom) * 0.5, f = l - u, p = c - x, h = Math.max(r.left - o.right, o.left - r.right, 0), m = Math.max(r.top - o.bottom, o.top - r.bottom, 0), S = h >= m, w = Wn(r, o);
    let T = 1 - Math.min(1, w / ye);
    T = T * T * (3 - 2 * T);
    const F = Be + (Rn - Be) * T, P = Math.min(
      Ue,
      F * Cn * kn
    ), $ = n.anchor.scale ?? 1, k = Math.max(En * $, n.hairlineWidth), N = Math.max(1, Math.round(k * e)), D = Math.max(
      1,
      Math.round(Math.max(In * $, n.hairlineWidth) * e)
    ), H = n.hairlineOuterCssPx;
    n.wrap.style.inset = `${-H}px`, n.wrap.style.borderRadius = `${Math.max(0, n.cornerRadius)}px`;
    const y = Math.max(1, Math.round((o.width + H * 2) * e)), M = Math.max(1, Math.round((o.height + H * 2) * e));
    n.canvas.width !== y && (n.canvas.width = y), n.canvas.height !== M && (n.canvas.height = M), n.strokeCanvas.width !== y && (n.strokeCanvas.width = y), n.strokeCanvas.height !== M && (n.strokeCanvas.height = M);
    const E = n.ctx;
    E.setTransform(1, 0, 0, 1, 0, 0), E.clearRect(0, 0, y, M);
    const L = n.strokeCtx;
    L.setTransform(1, 0, 0, 1, 0, 0), L.clearRect(0, 0, y, M);
    const W = Math.min(ye * e, Math.max(y, M));
    let G, B, b, g;
    S ? (G = f > 0 ? y : 0, b = f > 0 ? y - W : W, B = M * 0.5, g = M * 0.5) : (B = p > 0 ? M : 0, g = p > 0 ? M - W : W, G = y * 0.5, b = y * 0.5);
    const v = E.createLinearGradient(G, B, b, g);
    v.addColorStop(0, `rgba(0,0,0,${An})`), v.addColorStop(0.5, `rgba(0,0,0,${Sn})`), v.addColorStop(1, `rgba(0,0,0,${Tn})`);
    const _ = s / e, C = Math.max(1, Math.round(Fn * Math.max(0.1, _ / 140) * e));
    let I, K, U, R, O = !1, V = !1;
    if (S) {
      const ne = Math.max(r.top, o.top), he = Math.min(r.bottom, o.bottom);
      O = !0, I = f > 0 ? y - C : 0, K = Math.round((ne - o.top + H) * e), U = C, R = Math.max(1, Math.round((he - ne) * e));
    } else {
      const ne = Math.max(r.left, o.left), he = Math.min(r.right, o.right);
      V = !0, I = Math.round((ne - o.left + H) * e), K = p > 0 ? M - C : 0, U = Math.max(1, Math.round((he - ne) * e)), R = C;
    }
    const Se = { x: I, y: K, w: U, h: R, flipX: O, flipY: V }, ue = { x: 0, y: 0, w: y, h: M, r: Math.max(0, n.cornerRadius * e) }, ft = Math.min(
      Ue,
      P * Pn * $n * Hn
    );
    Un(E, a, s, d, y, M, ft, v, Se, ue, e), Xn(
      L,
      a,
      s,
      d,
      y,
      M,
      ue,
      P,
      N,
      v,
      Ln,
      Se
    ), qn(
      L,
      ue,
      D,
      G,
      B,
      b,
      g,
      Math.min(0.85, On * P)
    ), E.globalCompositeOperation = "source-over", L.globalCompositeOperation = "source-over";
  }
}
let ve = !1, qe = 0;
function Jn() {
  ve || (ve = !0, !(typeof requestAnimationFrame > "u") && requestAnimationFrame((e) => {
    ve = !1, !(e - qe < yt) && (qe = e, Qn());
  }));
}
const Ve = "metal-fx-styles", Zn = (
  /* css */
  `
.metal-fx-root {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  isolation: isolate;
  overflow: visible;
  background: #272727;
  color: #f8f8f8;
}
.metal-fx-root[data-theme='light'] {
  background: #ffffff;
  color: #1d1d1d;
}

.metal-fx-root::before {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: inherit;
  pointer-events: none;
  z-index: 2;
  box-shadow: inset 0 0 50px 0 rgba(255, 255, 255, 0.02);
}
.metal-fx-root[data-theme='light']::before {
  box-shadow: inset 0 0 50px 0 rgba(0, 0, 0, 0.02);
}

.metal-fx-root::after {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: inherit;
  pointer-events: none;
  z-index: 4;
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.1);
}
.metal-fx-root[data-theme='light']::after {
  box-shadow: inset 0 0 0 1px rgba(0, 0, 0, 0.06);
}
/* Circle variant gets a thicker outer rim than the button variant. */
.metal-fx-root[data-variant='circle']::after {
  box-shadow: inset 0 0 0 2px rgba(255, 255, 255, 0.1);
}
.metal-fx-root[data-theme='light'][data-variant='circle']::after {
  box-shadow: inset 0 0 0 2px rgba(0, 0, 0, 0.06);
}

.metal-fx-canvas {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  display: block;
  z-index: 0;
  pointer-events: none;
  border-radius: inherit;
}

/* The inner spacer — defines the inset geometry where the metal ring meets
   the interior (3 px for Button, 1-2 px for Circle) and carries the Circle dark
   hairline ('box-shadow: inset' rules below). Intentionally transparent so
   the wrapper's background propagates through to the punched shader centre,
   giving consumers a single surface tone to override. See "Single-surface
   background" in the file header for the rationale. */
.metal-fx-inner {
  position: absolute;
  inset: 3px;
  border-radius: inherit;
  z-index: 1;
  pointer-events: none;
}

.metal-fx-root[data-variant='button'][data-shape='pill'] .metal-fx-inner {
  border-radius: calc(var(--mfx-radius, 20px) - 3px);
}
.metal-fx-root[data-variant='button'][data-shape='circle'] .metal-fx-inner {
  border-radius: calc(var(--mfx-radius, 16px) - 3px);
}
.metal-fx-root[data-variant='circle'][data-shape='pill'] .metal-fx-inner {
  inset: 0;
  border-radius: var(--mfx-radius, 20px);
  box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.45);
}
.metal-fx-root[data-variant='circle'][data-shape='circle'] .metal-fx-inner {
  inset: 0;
  border-radius: var(--mfx-radius, 16px);
  box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.45);
}
/* Circle-variant hairline alpha — light mode.
   Source-of-truth: index.html L2261-2267. The 0.45-alpha black inset that
   reads as a single-pixel frame against the dark interior is too heavy
   on a #ffffff inner: it ends up looking like a hard 2-px black ring
   against the iridescent shader. Suppressed entirely (alpha 0) — the
   shader's own iridescent rim already defines the silhouette in light
   mode, so an extra dark hairline only competes with it. The rule is
   kept (rather than deleted) as a tunable hook in case a future variant
   wants to re-introduce a soft edge. NOTE: we keep the dark-mode inset
   and border-radius values because — unlike index.html — our renderer
   does NOT overscan the canvas in light mode, so there is no 1-px gap
   between inner element and shader to compensate for. */
.metal-fx-root[data-theme='light'][data-variant='circle'][data-shape='pill'] .metal-fx-inner,
.metal-fx-root[data-theme='light'][data-variant='circle'][data-shape='circle'] .metal-fx-inner {
  box-shadow: 0 0 0 1px rgba(0, 0, 0, 0);
}

/* ─── Combined glow SVG (z=3) ──────────────────────────────────────────────
   Single SVG per instance that holds BOTH the wide-halo group
   (#mfx_haloTravel) and the catch-light group (#mfx_extraTravel), exactly
   mirroring canonical's _buildGlowSvgInner (index.html L8078). One
   mix-blend-mode: screen lifts the combined composite onto the shader
   ring; per-frame opacity attributes on each inner group still drive the
   independent fade-in / fade-out cycles for the halo and the catch-light.

   Why a single SVG: the circle variant anchors halo + catch-light at the same
   perimeter point, so they overlap in the bright zone. Two separately-
   screened SVGs would double-screen the overlap (A + B + C - AB - AC -
   BC + ABC instead of A + B + C - AB - AC once both groups composite
   in source-over inside one SVG and then screen against the host once).
   That overlap looked muted versus canonical specifically on the circle
   variant where both layers travel together.

   Source-of-truth opacity: #btnGlowSvg drops to 0.7 in dark and 0.2746 in
   light (index.html L632/L643). */
.metal-fx-glow-svg {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  overflow: visible;
  z-index: 3;
  pointer-events: none;
  opacity: 0.7;
}
.metal-fx-root[data-theme='light'] .metal-fx-glow-svg {
  /* Light-mode 1-px overscan mirrors .btn-glow-svg in metal.html so the
     halo stays glued to the visible silhouette (the shader ring there sits
     1 px outside the host's padding box). */
  inset: -1px;
  width: calc(100% + 2px);
  height: calc(100% + 2px);
  mix-blend-mode: multiply;
  /* Source-of-truth: html[data-theme="light"] #btnGlowSvg { opacity: 0.2746 }
     → −35 % from 0.4225 from the original 0.7 dark-mode opacity. */
  opacity: 0.2746;
  filter: saturate(5.355) brightness(0.78);
}
/* Circle light-mode small variants (e.g. 36×36 send button): the geometrically
   shrunk halo loses density when multiplied against #ffffff. Mirror the
   canonical override at index.html L2316 — bump saturation + drop brightness
   so the small glow holds together visually. */
.metal-fx-root[data-variant='circle'][data-shape='circle'][data-theme='light'] .metal-fx-glow-svg {
  filter: saturate(7.5) brightness(0.6);
}

/* The wrapped child — hoisted into z=5 so it sits above every overlay, with
   normalized chrome so consumer button styles don't fight the metal frame. */
.metal-fx-content {
  position: relative;
  z-index: 5;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  pointer-events: none;
}
.metal-fx-content > * {
  pointer-events: auto;
}
.metal-fx-root[data-normalize='true'] .metal-fx-content > * {
  background: transparent !important;
  border: 0 !important;
  outline: 0 !important;
  box-shadow: none !important;
  /* Sizing: we deliberately DO NOT force \`width: 100%; height: 100%\` on the
     child here. That used to be the contract ("the wrapper is the visible
     button surface; the child stretches to fill it"), but it created a cyclic
     percentage dependency: the wrapper is \`inline-flex\` with no intrinsic
     size, .metal-fx-content is \`width/height: 100%\` of the wrapper, and the
     child was \`100%\` of .metal-fx-content. With nothing breaking the cycle,
     icon-only / class-sized children collapsed.

     The new contract: the child sizes itself (intrinsic content, CSS class,
     or inline style — all work), and the wrapper's \`inline-flex\` wraps it
     tightly. Consumers who want a metal frame BIGGER than the child (e.g.
     padding around an icon) size <MetalFx style={{ width, height }}> AND
     explicitly set width/height on the child to fill (or accept that the
     child renders at its intrinsic size, centered).

     Typography is intentionally NOT touched. We used to apply
     \`color: inherit; font: inherit;\` here to "match" the wrapper, but
     \`font: inherit\` is a shorthand that overrides font-family, font-size,
     font-weight, AND line-height on the child — which (a) shrank the
     button height (line-height changes propagate through the flex
     content box) and (b) scaled em-based icons / font-icons inside the
     child to whatever the wrapper inherited. The wrapper now stays out
     of the child's typography entirely; consumers who want typographic
     normalization can apply it themselves on the child element. */
}

[data-metal-fx-reflection] {
  position: absolute;
  inset: 0;
  pointer-events: none;
  border-radius: inherit;
  overflow: hidden;
  z-index: 0;
  isolation: isolate;
}
.metal-fx-reflection-canvas {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  display: block;
  filter: blur(4px) saturate(1.2) brightness(1.58);
}
.metal-fx-reflection-stroke-canvas {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  display: block;
  filter: saturate(1.35) brightness(1.75);
}
/* Hosts that participate as reflection targets need positioning + isolation
   so the wrap composites only against the host (not the parent stack). The
   wrap injects these inline as well, but stating them here keeps reflections
   working on hosts that already have other inline styles applied. */
[data-metal-fx-reflect-host] {
  isolation: isolate;
}
`
);
let be = !1;
function eo() {
  if (be || typeof document > "u") return;
  if (document.getElementById(Ve)) {
    be = !0;
    return;
  }
  const e = document.createElement("style");
  e.id = Ve, e.textContent = Zn, document.head.appendChild(e), be = !0;
}
eo();
export { Wt as createInstance, Gt as destroyInstance, oe as updateInstance, Ut as setSharedPreset, Ge as buildGlow, Mn as paintGlow, Xt as onFrame, zt as queueGlow, Dt as unqueueGlow, Bt as setVisible };
