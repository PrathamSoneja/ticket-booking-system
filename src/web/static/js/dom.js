// Tiny DOM builder. Text always goes through text nodes, so server data is never parsed as HTML.

const SVG_NS = "http://www.w3.org/2000/svg";

function apply(el, attrs) {
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") el.setAttribute("class", value);
    else if (key === "dataset") Object.assign(el.dataset, value);
    else if (key === "style") for (const [p, v] of Object.entries(value)) el.style.setProperty(p, v);
    else if (key.startsWith("on") && typeof value === "function") el.addEventListener(key.slice(2), value);
    else if (key === "value" || key === "checked" || key === "selected" || key === "disabled") el[key] = value;
    else el.setAttribute(key, value === true ? "" : String(value));
  }
}

function append(el, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

export function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  apply(el, attrs);
  append(el, children);
  return el;
}

export function s(tag, attrs, ...children) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value !== null && value !== undefined && value !== false) el.setAttribute(key, String(value));
  }
  append(el, children);
  return el;
}

export function clear(el) {
  while (el.firstChild) el.firstChild.remove();
  return el;
}

export function replace(el, ...children) {
  clear(el);
  append(el, children);
  return el;
}

// One consistent icon family: 20px grid, 1.5px square-capped strokes.
const ICONS = {
  arrow: ["M3 10h13", "M11 5l5 5-5 5"],
  chevron: ["M7 4l6 6-6 6"],
  close: ["M5 5l10 10", "M15 5L5 15"],
  copy: ["M7 7h9v9H7z", "M4 13V4h9"],
  check: ["M4 10.5l4 4 8-9"],
  refresh: ["M16 10a6 6 0 1 1-2-4.5", "M16 3v4h-4"],
  alert: ["M10 3l8 14H2z", "M10 8v4", "M10 14.5v.5"],
  offline: ["M3 3l14 14", "M5 9a7 7 0 0 1 3-1.6", "M12 7.4A7 7 0 0 1 15 9", "M8 12.5a3 3 0 0 1 4 0", "M10 15.5v.5"],
  info: ["M3 3h14v14H3z", "M10 9v5", "M10 6v.5"],
  spark: ["M10 3v4", "M10 13v4", "M3 10h4", "M13 10h4", "M5.5 5.5l2 2", "M12.5 12.5l2 2"],
  quote: ["M4 5h12v8H9l-4 3v-3H4z"],
  ticket: ["M3 6h14v3a1.5 1.5 0 0 0 0 3v3H3v-3a1.5 1.5 0 0 0 0-3z", "M12 6v9"],
  download: ["M10 3v10", "M6 9l4 4 4-4", "M4 16h12"],
  search: ["M9 3a6 6 0 1 0 0 12A6 6 0 0 0 9 3z", "M13.5 13.5L17 17"],
};

export function icon(name, cls = "") {
  return s(
    "svg",
    { class: `icon ${cls}`.trim(), viewBox: "0 0 20 20", "aria-hidden": "true", focusable: "false" },
    (ICONS[name] || []).map((d) => s("path", { d })),
  );
}
