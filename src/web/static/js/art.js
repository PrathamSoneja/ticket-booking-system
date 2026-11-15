// Original abstract plates, drawn from the show ID. No posters, no photographs: the catalogue has
// no images, so each show gets a deterministic geometric composition in the house palette.

import { s } from "./dom.js";

const C = { paper: "#F2EBDD", sand: "#DFD4BC", ink: "#202622", ox: "#792F3E", moss: "#53654A", brass: "#B9944D" };

function seeded(text) {
  let a = 2166136261;
  for (let i = 0; i < text.length; i++) a = Math.imul(a ^ text.charCodeAt(i), 16777619);
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const W = 600;
const H = 400;

function moonrise(r) {
  const cx = 140 + r() * 320;
  const cy = 130 + r() * 70;
  const rad = 70 + r() * 50;
  const out = [s("rect", { width: W, height: H, fill: C.ink }), s("circle", { cx, cy, r: rad, fill: C.brass })];
  let y = 230 + r() * 30;
  const bands = [C.moss, C.paper, C.ox, C.moss];
  for (let i = 0; y < H; i++) {
    const t = 10 + r() * 34;
    out.push(s("rect", { x: 0, y, width: W, height: t, fill: bands[i % bands.length] }));
    y += t + 6 + r() * 16;
  }
  out.push(s("circle", { cx, cy, r: rad + 22, fill: "none", stroke: C.paper, "stroke-width": 1.5 }));
  return out;
}

function curtain(r) {
  const out = [s("rect", { width: W, height: H, fill: C.ox })];
  let x = 0;
  while (x < W) {
    const w = 8 + r() * 46;
    if (r() > 0.42) out.push(s("rect", { x, y: 0, width: w, height: H, fill: r() > 0.5 ? "#5E2230" : C.ink }));
    x += w + 6 + r() * 22;
  }
  const cx = 120 + r() * 360;
  out.push(s("path", { d: `M${cx - 120} ${H}a120 120 0 0 1 240 0z`, fill: C.paper }));
  out.push(s("path", { d: `M${cx - 62} ${H}a62 62 0 0 1 124 0z`, fill: C.brass }));
  return out;
}

function rings(r) {
  const cx = 150 + r() * 300;
  const cy = 120 + r() * 160;
  const out = [s("rect", { width: W, height: H, fill: C.sand })];
  const n = 5 + Math.floor(r() * 4);
  for (let i = n; i >= 1; i--) {
    out.push(s("circle", { cx, cy, r: i * 38, fill: "none", stroke: C.ink, "stroke-width": i % 3 === 0 ? 9 : 2 }));
  }
  out.push(s("circle", { cx, cy, r: 22, fill: C.ox }));
  out.push(s("rect", { x: 0, y: H - 46 - r() * 30, width: W, height: 14, fill: C.moss }));
  return out;
}

function steps(r) {
  const out = [s("rect", { width: W, height: H, fill: C.moss })];
  const fills = [C.paper, C.brass, C.ink, C.ox];
  const n = 4;
  const size = W / n;
  const flip = r() > 0.5;
  for (let i = 0; i < n; i++) {
    const x = i * size;
    const top = 40 + (flip ? n - 1 - i : i) * 60 + r() * 24;
    out.push(s("path", { d: `M${x} ${H}V${top + size}a${size} ${size} 0 0 1 ${size} ${-size}V${H}z`, fill: fills[(i + Math.floor(r() * 4)) % 4] }));
  }
  out.push(s("rect", { x: 0, y: 28 + r() * 20, width: W, height: 2, fill: C.paper }));
  return out;
}

const PLATES = [moonrise, curtain, rings, steps];

export function plate(seed, label) {
  const r = seeded(String(seed));
  const pick = PLATES[Math.floor(r() * PLATES.length)];
  return s(
    "svg",
    { viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: "xMidYMid slice", role: "img", "aria-label": label || "Abstract plate" },
    pick(r),
  );
}
