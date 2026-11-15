// Seat selection, server quote, simulated payment, confirmation ticket.

import { api, post, ApiError } from "../api.js";
import { h, icon, replace } from "../dom.js";
import { plate } from "../art.js";
import { FAILURE, amount, button, clock, errorNote, loadingBlock, note, pending, statusNote, ticket, when } from "../ui.js";

const POLL_MS = 10000;

export async function mount(root, ctx) {
  const showId = ctx.params[0];
  const max = ctx.state.session.max_seats || 5;
  root.append(h("a", { class: "crumb", href: "#/programme" }, icon("arrow"), "Programme"));
  const body = h("div");
  root.append(body);
  replace(body, loadingBlock("Reading the show and its committed seat map"));

  let show = ctx.showById(showId);
  if (!show) {
    try {
      const out = await ctx.loadShows({ force: true });
      if (out.status !== "OK") {
        replace(body, statusNote(out.status, out.message, () => ctx.navigate(`/show/${showId}`)));
        return null;
      }
      show = ctx.showById(showId);
    } catch (err) {
      if (!(err instanceof ApiError)) throw err;
      if (err.http !== 401) replace(body, errorNote(err, () => ctx.navigate(`/show/${showId}`)));
      return null;
    }
  }
  if (!ctx.alive()) return null;
  if (!show) {
    replace(
      body,
      h("div", { class: "empty" }, h("p", { class: "display" }, "No such show."), h("p", { class: "lede" }, `The cluster's catalogue has no show with the ID “${showId}”.`), h("a", { class: "btn", href: "#/programme" }, "Back to the programme")),
    );
    return null;
  }

  // ---------------------------------------------------------------- state
  let seats = null; // [{seat_id, status}] as last confirmed by the cluster
  let readAt = "";
  const picked = [];
  let quote = null;
  let quoteState = "idle"; // idle | pending | ok | error
  let quoteError = "";
  let quoteSeq = 0;
  let payment = "approve";
  let phase = "select"; // select | booking | uncertain
  let unresolved = null;
  let pollTimer = null;
  let observer = null;
  let disposed = false;

  try {
    const sess = await ctx.refreshSession();
    const p = sess.pending_booking;
    if (p && p.show_id === showId) {
      picked.push(...p.seat_ids);
      payment = p.payment;
      phase = "uncertain";
      unresolved = { status: p.last_status || "UNKNOWN", request_id: p.request_id, attempts: p.attempts, message: "" };
    }
  } catch (err) {
    if (err instanceof ApiError && err.http === 401) return null;
  }

  // ---------------------------------------------------------------- skeleton (built once; pieces are patched)
  const w = when(show.start_time);
  const freeFact = h("dd", null, "…");
  const readStamp = h("span", { class: "hall__read" }, "");
  const staleSlot = h("div");
  const grid = h("div", { class: "seats", role: "group", "aria-label": `Seats for ${show.name}. Arrow keys move, Space or Enter selects.` });
  const hallMsg = h("p", { class: "hall__msg", role: "status", "aria-live": "polite" });
  const refreshBtn = button("Refresh seats", () => loadSeats(true), "btn--small", "refresh");
  const pickedBox = h("div", { class: "picked" });
  const quoteBox = h("div", { class: "quote", "aria-live": "polite" });
  const actionBox = h("div", { class: "summary__action" });
  const resultBox = h("div", { "aria-live": "assertive" });
  const payInputs = {};
  const payField = h(
    "fieldset",
    { class: "pay" },
    h("legend", null, "Simulated payment"),
    [
      ["approve", "Approve", "The mock issuer accepts the charge."],
      ["decline", "Decline", "The mock issuer refuses; nothing is booked."],
    ].map(([value, title, text]) => {
      const input = h("input", { type: "radio", name: "pay", value, checked: payment === value, onchange: () => (payment = value) });
      payInputs[value] = input;
      return h("label", null, input, h("b", null, title), h("span", null, text));
    }),
    h("p", { class: "pay__disclaimer" }, "A simulation only: no card details are asked for, stored or sent."),
  );
  const summary = h("aside", { class: "summary", id: "summary", "aria-label": "Your booking" }, h("h2", null, "Your selection"), pickedBox, quoteBox, payField, actionBox, resultBox);
  const minibar = h("div", { class: "minibar", hidden: true });

  replace(
    body,
    h(
      "header",
      { class: "show-head enter" },
      h(
        "div",
        null,
        h("p", { class: "label label--muted" }, w.ok ? `${w.weekdayLong} ${w.day} ${w.monthLong} ${w.year}` : "Date as listed"),
        h("h1", { class: "display" }, show.name),
        h(
          "dl",
          { class: "facts" },
          h("div", null, h("dt", null, "Time"), h("dd", { title: w.ok ? `Your local time: ${w.local}` : null }, w.ok ? `${w.time} UTC` : w.raw)),
          h("div", null, h("dt", null, "Venue"), h("dd", null, show.venue)),
          h("div", null, h("dt", null, "Show ID"), h("dd", { class: "mono" }, show.show_id)),
          h("div", null, h("dt", null, "Free"), freeFact),
        ),
      ),
      h("div", { class: "show-head__art", "aria-hidden": "true" }, plate(show.show_id)),
    ),
    h(
      "div",
      { class: "booking" },
      h(
        "section",
        { class: "hall enter", style: { "--i": "1" }, "aria-label": "Seat map" },
        h("div", { class: "hall__bar" }, h("h2", { class: "label" }, `Choose up to ${max} seats`), readStamp, refreshBtn),
        staleSlot,
        h("div", { class: "stage", "aria-hidden": "true" }, h("i"), h("span", null, "Stage")),
        grid,
        hallMsg,
        h(
          "ul",
          { class: "legend", "aria-label": "Legend" },
          h("li", null, h("span", { class: "seat", "aria-hidden": "true" }, "A1"), "Available: solid outline"),
          h("li", null, h("span", { class: "seat", "aria-pressed": "true", "aria-hidden": "true" }, "A1"), "Selected: filled, ticked"),
          h("li", null, h("span", { class: "seat", "aria-disabled": "true", "aria-hidden": "true" }, "A1"), "Booked: dashed, struck through"),
        ),
      ),
      summary,
    ),
    minibar,
  );

  // ---------------------------------------------------------------- seat grid
  const buttons = new Map();

  function seatLabel(seat, selected) {
    return `Seat ${seat.seat_id}, ${seat.status !== "AVAILABLE" ? "booked" : selected ? "selected" : "available"}`;
  }

  function paintSeat(seat) {
    const btn = buttons.get(seat.seat_id);
    const selected = picked.includes(seat.seat_id);
    const booked = seat.status !== "AVAILABLE";
    btn.setAttribute("aria-pressed", String(selected && !booked));
    btn.setAttribute("aria-disabled", String(booked));
    btn.setAttribute("aria-label", seatLabel(seat, selected));
  }

  function patchSeats(next, announce) {
    const before = new Map((seats || []).map((s) => [s.seat_id, s.status]));
    const lost = [];
    if (!seats || next.length !== seats.length || next.some((s) => !buttons.has(s.seat_id))) {
      buttons.clear();
      replace(
        grid,
        next.map((seat, i) => {
          const btn = h("button", { class: "seat", type: "button", tabindex: i === 0 ? "0" : "-1", dataset: { seat: seat.seat_id } }, seat.seat_id);
          buttons.set(seat.seat_id, btn);
          return btn;
        }),
      );
    }
    seats = next;
    for (const seat of seats) {
      const booked = seat.status !== "AVAILABLE";
      if (booked && phase === "select" && picked.includes(seat.seat_id)) {
        picked.splice(picked.indexOf(seat.seat_id), 1);
        lost.push(seat.seat_id);
      }
      paintSeat(seat);
      if (announce && before.has(seat.seat_id) && before.get(seat.seat_id) !== seat.status) {
        const btn = buttons.get(seat.seat_id);
        btn.classList.remove("is-changed");
        void btn.offsetWidth;
        btn.classList.add("is-changed");
      }
    }
    const free = seats.filter((s) => s.status === "AVAILABLE").length;
    freeFact.textContent = `${free} of ${seats.length} seats`;
    if (lost.length) {
      hallMsg.textContent = `${lost.join(", ")} ${lost.length === 1 ? "was" : "were"} just booked by someone else and removed from your selection.`;
      drawPicked();
      fetchQuote();
    }
  }

  function toggle(id) {
    const seat = seats.find((s) => s.seat_id === id);
    if (!seat) return;
    if (phase !== "select") {
      hallMsg.textContent = phase === "booking" ? "A booking is in flight; the selection is locked until it resolves." : "Resolve the unanswered request first: retry it or start over.";
      return;
    }
    if (seat.status !== "AVAILABLE") {
      hallMsg.textContent = `Seat ${id} is already booked.`;
      return;
    }
    const at = picked.indexOf(id);
    if (at >= 0) picked.splice(at, 1);
    else if (picked.length >= max) {
      hallMsg.textContent = `One booking holds at most ${max} seats. Remove one to add ${id}.`;
      return;
    } else picked.push(id);
    hallMsg.textContent = "";
    replace(resultBox);
    paintSeat(seat);
    drawPicked();
    fetchQuote();
  }

  grid.addEventListener("click", (ev) => {
    const btn = ev.target.closest(".seat");
    if (btn) toggle(btn.dataset.seat);
  });

  grid.addEventListener("keydown", (ev) => {
    const btn = ev.target.closest(".seat");
    if (!btn) return;
    const all = [...buttons.values()];
    const i = all.indexOf(btn);
    const cols = getComputedStyle(grid).gridTemplateColumns.split(" ").length;
    let j = i;
    if (ev.key === "ArrowRight") j = Math.min(all.length - 1, i + 1);
    else if (ev.key === "ArrowLeft") j = Math.max(0, i - 1);
    else if (ev.key === "ArrowDown") j = i + cols < all.length ? i + cols : i;
    else if (ev.key === "ArrowUp") j = i - cols >= 0 ? i - cols : i;
    else if (ev.key === "Home") j = i - (i % cols);
    else if (ev.key === "End") j = Math.min(all.length - 1, i - (i % cols) + cols - 1);
    else return;
    ev.preventDefault();
    btn.tabIndex = -1;
    all[j].tabIndex = 0;
    all[j].focus();
  });

  // ---------------------------------------------------------------- summary pieces
  function drawPicked() {
    if (!picked.length) {
      replace(pickedBox, h("p", null, "No seats selected yet. Pick from the map."));
    } else {
      replace(
        pickedBox,
        picked.map((id) =>
          h("button", { type: "button", "aria-label": `Remove seat ${id}`, disabled: phase !== "select", onclick: () => toggle(id) }, id, icon("close")),
        ),
      );
    }
    drawMinibar();
  }

  function drawQuote() {
    if (quoteState === "idle") {
      replace(quoteBox, h("small", null, "The price is whatever the server quotes for your seats; this page never computes one."));
    } else if (quoteState === "pending") {
      replace(quoteBox, pending("Fetching the server quote").el);
    } else if (quoteState === "ok") {
      replace(
        quoteBox,
        h("div", { class: "quote__line" }, h("span", null, `${quote.seat_ids.length} × ${amount(quote.unit_price)}`), h("span", { class: "label label--muted" }, "Server quote")),
        h("div", { class: "quote__line" }, h("span", { class: "label" }, "Total"), h("span", { class: "quote__total" }, amount(quote.total))),
        h("small", null, "Figures are the server's QUOTE read; the backend names no currency."),
      );
    } else {
      replace(quoteBox, h("small", null, `No quote: ${quoteError}`), button("Retry quote", fetchQuote, "btn--small", "refresh"));
    }
    drawAction();
    drawMinibar();
  }

  async function fetchQuote() {
    const seq = ++quoteSeq;
    quote = null;
    if (!picked.length) {
      quoteState = "idle";
      return drawQuote();
    }
    quoteState = "pending";
    drawQuote();
    try {
      const out = await api(`/api/quote?show_id=${encodeURIComponent(showId)}&seats=${encodeURIComponent(picked.join(","))}`);
      if (seq !== quoteSeq || disposed) return;
      if (out.status === "OK") {
        quote = out.quote;
        quoteState = "ok";
      } else {
        quoteState = "error";
        quoteError = `${out.status}. ${FAILURE[out.status] || out.message}`;
      }
    } catch (err) {
      if (seq !== quoteSeq || disposed || !(err instanceof ApiError) || err.http === 401) return;
      quoteState = "error";
      quoteError = err.message;
    }
    drawQuote();
  }

  let flight = null;

  function drawAction() {
    for (const input of Object.values(payInputs)) input.disabled = phase !== "select";
    if (phase === "booking") return; // the in-flight indicator owns this box
    if (phase === "uncertain") {
      const u = unresolved;
      const known = FAILURE[u.status];
      replace(
        actionBox,
        note(
          "warn",
          `Outcome unknown · ${u.status.replaceAll("_", " ")}`,
          `${known || u.message || "No final answer arrived."} Nothing is confirmed yet. Retrying sends the same request ID, so the cluster applies it at most once and cannot charge twice.`,
          [
            button("Retry same request", book, "btn--primary btn--small", "refresh"),
            h("a", { class: "btn btn--small", href: "#/tickets" }, "Check My Tickets"),
            button("Start over", startOver),
          ],
        ),
        u.request_id ? h("p", { class: "mono", style: { "margin-top": "10px" } }, `request ${u.request_id} · attempt ${u.attempts}`) : null,
      );
      return;
    }
    const ready = quoteState === "ok" && picked.length > 0;
    replace(
      actionBox,
      h(
        "button",
        { class: "btn btn--primary btn--block", type: "button", disabled: !ready, onclick: book },
        payment === "decline" ? "Submit (simulated decline)" : "Pay and book",
        icon("arrow", "icon--go"),
      ),
    );
  }
  payField.addEventListener("change", drawAction);

  function drawMinibar() {
    if (!picked.length || phase === "booking") {
      minibar.hidden = true;
      return;
    }
    const total = quoteState === "ok" ? `total ${amount(quote.total)}` : quoteState === "pending" ? "quote pending" : phase === "uncertain" ? "request unresolved" : "no quote";
    replace(
      minibar,
      h("div", null, h("b", null, `${picked.length} ${picked.length === 1 ? "seat" : "seats"} · ${picked.join(" ")}`), h("span", null, total)),
      h("button", { class: "btn btn--small", type: "button", onclick: () => summary.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" }) }, "Review"),
    );
    minibar.hidden = summaryVisible;
  }

  let summaryVisible = false;
  if ("IntersectionObserver" in window) {
    observer = new IntersectionObserver(
      ([entry]) => {
        summaryVisible = entry.isIntersecting;
        drawMinibar();
      },
      { threshold: 0.35 },
    );
    observer.observe(summary);
  }

  // ---------------------------------------------------------------- booking
  async function book() {
    if (phase === "booking" || !picked.length) return;
    phase = "booking";
    replace(resultBox);
    hallMsg.textContent = "";
    drawPicked();
    drawAction();
    flight = pending("Proposing the booking to the Raft leader…", { timer: true });
    replace(actionBox, flight.el);
    let out;
    try {
      out = await post("/api/bookings", { show_id: showId, seat_ids: picked, payment }, { timeout: 30000 });
    } catch (err) {
      flight.stop();
      if (!(err instanceof ApiError)) throw err;
      if (err.http === 401 || disposed) return;
      if (err.kind === "http") {
        phase = "select";
        replace(resultBox, errorNote(err));
      } else {
        // no answer from the gateway: the request may or may not have been committed
        phase = "uncertain";
        unresolved = { status: "GATEWAY_SILENT", message: `${err.message} The gateway keeps the request ID for this exact selection while it is running.`, request_id: "", attempts: (unresolved && unresolved.attempts) || 1 };
      }
      drawPicked();
      drawAction();
      return;
    }
    flight.stop();
    if (disposed) return;
    if (out.status === "OK") return confirmed(out);
    if (out.uncertain) {
      phase = "uncertain";
      unresolved = out;
      drawPicked();
      drawAction();
      return;
    }
    phase = "select";
    unresolved = null;
    if (out.status === "PAYMENT_FAILED") {
      replace(resultBox, note("error", "Payment declined", `${out.message} No seats were booked and nothing was charged. Request ${out.request_id}.`));
    } else if (out.status === "ALREADY_BOOKED") {
      replace(resultBox, note("error", "Seat conflict", "Someone else's booking for one of these seats was committed first. Nothing was booked or charged; the seat map has been refreshed."));
    } else {
      replace(resultBox, statusNote(out.status, out.message));
    }
    drawPicked();
    drawAction();
    await loadSeats(true);
  }

  async function startOver() {
    try {
      await post("/api/bookings/pending/discard", {});
    } catch (err) {
      if (err instanceof ApiError && err.http === 401) return;
    }
    phase = "select";
    unresolved = null;
    picked.length = 0;
    replace(resultBox, note("warn", "Earlier request abandoned", "If the cluster did commit it after all, it will appear under My Tickets, where you can cancel it."));
    drawPicked();
    await loadSeats(true);
    fetchQuote();
  }

  function confirmed(out) {
    stop();
    ctx.loadShows({ force: true }).catch(() => {});
    const booking = out.booking || { booking_id: out.booking_id, show_id: showId, seat_id: picked.join(","), status: "CONFIRMED" };
    const head = h("h1", { class: "display", tabindex: "-1" }, "Booking confirmed.");
    replace(
      body,
      h(
        "section",
        { class: "confirm" },
        h(
          "div",
          { class: "confirm__head" },
          h("p", { class: "label label--muted" }, `Committed by a Raft majority${out.attempts > 1 ? ` · attempt ${out.attempts}, same request ID` : ""}`),
          head,
        ),
        ticket(booking, show, { reveal: true, requestId: out.request_id, holder: ctx.state.session.user }),
        out.booking ? null : note("warn", "Charge details not read back", "The booking is committed, but its charge record could not be read just now. My Tickets shows it once the cluster answers."),
        h(
          "div",
          { class: "confirm__actions" },
          h("a", { class: "btn btn--primary", href: "#/tickets" }, "View my tickets"),
          button("Book more seats for this show", () => ctx.navigate(`/show/${showId}`), ""),
          h("a", { class: "btn", href: "#/programme" }, "Back to the programme"),
        ),
      ),
    );
    window.scrollTo(0, 0);
    head.focus({ preventScroll: true });
  }

  // ---------------------------------------------------------------- live seat map
  async function loadSeats(manual) {
    if (disposed) return;
    if (manual) refreshBtn.disabled = true;
    try {
      const out = await api(`/api/shows/${encodeURIComponent(showId)}/seats`);
      if (disposed) return;
      if (out.status === "OK") {
        ctx.setConn("ok");
        patchSeats(out.seats, seats !== null);
        readAt = out.read_at;
        readStamp.textContent = `Linearizable read confirmed ${clock(readAt)}`;
        replace(staleSlot);
      } else {
        ctx.setConn("down");
        failed(`${out.status}. ${FAILURE[out.status] || out.message}`);
      }
    } catch (err) {
      if (!(err instanceof ApiError)) throw err;
      if (err.http === 401 || disposed) return;
      ctx.setConn("down");
      failed(err.message);
    } finally {
      refreshBtn.disabled = false;
    }
  }

  function failed(reason) {
    if (seats) {
      readStamp.textContent = `STALE · last confirmed ${clock(readAt)}`;
      replace(staleSlot, note("warn", "Stale seat map", `The live read failed (${reason}) Showing the map last confirmed at ${clock(readAt)}; a booking may fail.`));
      staleSlot.firstChild.style.marginBottom = "18px";
    } else {
      replace(grid);
      replace(staleSlot, note("offline", "Seat map unavailable", reason, [button("Try again", () => loadSeats(true), "btn--small", "refresh")]));
    }
  }

  function stop() {
    disposed = true;
    clearInterval(pollTimer);
    if (flight) flight.stop();
    if (observer) observer.disconnect();
  }

  drawPicked();
  drawQuote();
  await loadSeats(false);
  if (picked.length) {
    for (const seat of seats || []) paintSeat(seat);
    if (phase === "select") fetchQuote();
  }
  pollTimer = setInterval(() => {
    if (document.visibilityState === "visible" && phase === "select") loadSeats(false);
  }, POLL_MS);
  return stop;
}
