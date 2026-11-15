// Shared pieces: status language, notes, pending indicators, dates, the admission ticket.

import { h, icon } from "./dom.js";
import { plate } from "./art.js";

// Same wording as the Streamlit UI for the three cluster-level failure states.
export const FAILURE = {
  UNAVAILABLE: "Disconnected: no cluster node answered.",
  NO_QUORUM: "The cluster has no majority right now; nothing was committed.",
  NO_LEADER: "The cluster is electing a leader; try again in a moment.",
  REDIRECT_FAILED: "The leader could not be reached after several redirects.",
  RPC_ERROR: "The gRPC call failed before an answer arrived.",
};

export function note(kind, title, body, actions = []) {
  const ic = kind === "error" ? "alert" : kind === "ok" ? "check" : kind === "offline" ? "offline" : "info";
  return h(
    "div",
    { class: `note note--${kind === "offline" ? "error" : kind}`, role: kind === "error" || kind === "offline" ? "alert" : "status" },
    icon(ic),
    h("p", { class: "note__title" }, title),
    body ? h("p", { class: "note__body" }, body) : null,
    actions.length ? h("div", { class: "note__actions" }, actions) : null,
  );
}

export function button(label, onclick, cls = "btn--small", ic = null) {
  return h("button", { class: `btn ${cls}`, type: "button", onclick }, ic ? icon(ic) : null, label);
}

// For a thrown ApiError (gateway unreachable, timeout, 4xx/5xx).
export function errorNote(err, retry) {
  const offline = err.kind === "network" || err.kind === "timeout";
  const title = offline ? "Gateway not reachable" : err.http === 401 ? "Signed out" : "Request refused";
  return note(offline ? "offline" : "error", title, err.message, retry ? [button("Try again", retry, "btn--small", "refresh")] : []);
}

// For a backend status other than OK that arrived in a 200 body.
export function statusNote(status, message, retry) {
  const known = FAILURE[status];
  const kind = status === "UNAVAILABLE" ? "offline" : "error";
  const body = known ? `${known}${message ? " " + message : ""}` : message || "The cluster returned this status without a message.";
  return note(kind, status.replaceAll("_", " "), body, retry ? [button("Try again", retry, "btn--small", "refresh")] : []);
}

// A pending indicator that counts real elapsed time; stop() must be called when the request ends.
export function pending(text, { timer = false } = {}) {
  const label = h("span", null, text);
  const el = h("span", { class: "pending", role: "status" }, h("span", { class: "ticks", "aria-hidden": "true" }, h("i"), h("i"), h("i")), label);
  let handle = null;
  if (timer) {
    const t0 = performance.now();
    handle = setInterval(() => {
      label.textContent = `${text} ${Math.round((performance.now() - t0) / 1000)} s`;
    }, 1000);
  }
  return { el, stop: () => handle && clearInterval(handle), set: (t) => (label.textContent = t) };
}

export function loadingBlock(text) {
  return h("div", { class: "loading-block" }, pending(text).el);
}

const DTF = {
  weekday: new Intl.DateTimeFormat("en-GB", { weekday: "short", timeZone: "UTC" }),
  weekdayLong: new Intl.DateTimeFormat("en-GB", { weekday: "long", timeZone: "UTC" }),
  day: new Intl.DateTimeFormat("en-GB", { day: "numeric", timeZone: "UTC" }),
  month: new Intl.DateTimeFormat("en-GB", { month: "short", timeZone: "UTC" }),
  monthLong: new Intl.DateTimeFormat("en-GB", { month: "long", timeZone: "UTC" }),
  year: new Intl.DateTimeFormat("en-GB", { year: "numeric", timeZone: "UTC" }),
  time: new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "UTC" }),
  local: new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }),
};

// Dates are shown exactly as seeded, in UTC (the catalogue stores "...Z"); nothing is shifted or hidden.
export function when(iso) {
  const d = new Date(iso);
  if (!iso || Number.isNaN(d.getTime())) return { ok: false, raw: String(iso || "") };
  return {
    ok: true,
    raw: iso,
    ts: d.getTime(),
    weekday: DTF.weekday.format(d),
    weekdayLong: DTF.weekdayLong.format(d),
    day: DTF.day.format(d),
    month: DTF.month.format(d),
    monthLong: DTF.monthLong.format(d),
    year: DTF.year.format(d),
    time: DTF.time.format(d),
    date: `${DTF.weekday.format(d)} ${DTF.day.format(d)} ${DTF.month.format(d)} ${DTF.year.format(d)}`,
    local: DTF.local.format(d),
  };
}

export function clock(iso) {
  const d = iso ? new Date(iso) : new Date();
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleTimeString([], { hour12: false });
}

export const amount = (n) => (typeof n === "number" && Number.isFinite(n) ? n.toFixed(2) : "?");

export function availability(show) {
  if (show.seats_status !== "OK") {
    return h("div", { class: "avail avail--none" }, h("span", { class: "avail__text" }, `Availability unknown (${show.seats_status || "?"})`));
  }
  const free = show.seats_available;
  const total = show.seats_total;
  const el = h(
    "div",
    { class: `avail ${free ? "" : "avail--none"}` },
    h("span", { class: "avail__text" }, free ? `${free} of ${total} seats free` : `Fully booked (${total} seats)`),
    h("span", { class: "avail__bar", "aria-hidden": "true" }, h("i", { style: { transform: `scaleX(${total ? free / total : 0})` } })),
  );
  return el;
}

export function toast(message) {
  const host = document.getElementById("toasts");
  const el = h("div", { class: "toast" }, message);
  host.append(el);
  setTimeout(() => el.remove(), 3200);
}

export function copyButton(text, label = "Copy") {
  const span = h("span", null, label);
  const btn = h(
    "button",
    {
      class: "copy",
      type: "button",
      "aria-label": `${label}: ${text}`,
      onclick: async () => {
        try {
          await navigator.clipboard.writeText(text);
          span.textContent = "Copied";
        } catch {
          span.textContent = "Select and copy manually";
        }
        setTimeout(() => (span.textContent = label), 1800);
      },
    },
    icon("copy"),
    span,
  );
  return btn;
}

export function disclosure(summary, body, open = false) {
  return h("details", { class: "disc", open }, h("summary", null, icon("chevron"), summary), h("div", { class: "disc__body" }, body));
}

function pair(dt, dd, cls = "") {
  return h("div", { class: cls }, h("dt", null, dt), dd instanceof Node ? dd : h("dd", null, dd));
}

// The admission ticket. Every value is taken from the booking record / show catalogue as returned.
export function ticket(booking, show, { reveal = false, requestId = "", holder = "" } = {}) {
  const seats = String(booking.seat_id || "").split(",").filter(Boolean);
  const w = when(show && show.start_time);
  const cancelled = booking.status === "CANCELLED";
  const name = show ? show.name : booking.show_id;
  return h(
    "article",
    { class: `ticket ${cancelled ? "ticket--void" : ""} ${reveal ? "ticket--reveal" : ""}`, "aria-label": `Ticket for ${name}, ${booking.status}` },
    h("div", { class: "ticket__art", "aria-hidden": "true" }, plate(booking.show_id)),
    h(
      "div",
      { class: "ticket__main" },
      h(
        "div",
        { class: "ticket__top" },
        h("span", { class: "label" }, `Admit ${seats.length === 1 ? "one" : seats.length} · The Night Programme`),
        h("span", { class: `stamp ${cancelled ? "stamp--void" : "stamp--ok"}` }, booking.status),
      ),
      h("h3", { class: "ticket__name" }, name),
      h(
        "dl",
        { class: "ticket__grid" },
        pair("Date", w.ok ? w.date : w.raw || "Not in catalogue"),
        pair("Time", w.ok ? `${w.time} UTC` : "?"),
        pair("Venue", show ? show.venue : "Not in catalogue"),
        pair(seats.length === 1 ? "Seat" : "Seats", seats.join(" · "), "ticket__seats"),
      ),
    ),
    h(
      "dl",
      { class: "ticket__stub" },
      pair("Booking ID", h("dd", null, booking.booking_id, " ", copyButton(booking.booking_id, "Copy ID")), "wide"),
      pair("Charged", h("dd", { class: "big" }, amount(booking.amount))),
      pair("Charges", h("dd", { class: "big" }, String(booking.charges ?? "?"))),
      pair("Refund", booking.refunded ? "Refunded in full" : cancelled ? "No refund recorded" : "None"),
      pair("Show ID", booking.show_id),
      booking.txn ? pair("Transaction", booking.txn, "wide") : null,
      holder ? pair("Holder", holder) : null,
      requestId ? pair("Request ID", requestId, "wide") : null,
    ),
  );
}
