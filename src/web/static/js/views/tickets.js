// Booking history: real records, full copyable IDs, charges/refunds, confirmed cancellation.

import { api, post, ApiError } from "../api.js";
import { h, replace } from "../dom.js";
import { FAILURE, button, clock, errorNote, loadingBlock, note, pending, statusNote, ticket } from "../ui.js";

export async function mount(root, ctx) {
  const stamp = h("span", { class: "stamp-time" }, "");
  const refresh = button("Refresh", () => load(), "btn--small", "refresh");
  const list = h("div", { "aria-live": "polite" });
  const kicker = h("p", { class: "label label--muted" }, "My Tickets");
  let rows = null;
  const open = new Set(); // booking IDs whose cancel confirmation is open
  const outcome = new Map(); // booking ID -> last cancel result note

  root.append(
    h(
      "header",
      { class: "page-head" },
      h("div", null, kicker, h("h1", { class: "display" }, "Your ", h("em", null, "tickets."))),
      h("div", { class: "page-head__tools" }, stamp, refresh),
    ),
    list,
  );

  function item(b) {
    const show = ctx.showById(b.show_id);
    const tools = h("div", { class: "stub-tools" });
    const slot = h("div");
    const li = h("li", null, ticket(b, show, { holder: ctx.state.session.user }), tools, slot);

    function draw() {
      replace(tools);
      replace(slot);
      if (outcome.has(b.booking_id)) slot.append(outcome.get(b.booking_id));
      if (b.status !== "CONFIRMED") {
        tools.append(h("span", { class: "label label--muted" }, b.refunded ? "Cancelled · seats released · refund recorded" : "Cancelled · seats released"));
        return;
      }
      tools.append(
        h("a", { class: "linkbtn", href: `#/show/${encodeURIComponent(b.show_id)}` }, "Open this show"),
        open.has(b.booking_id)
          ? ""
          : button(
              "Cancel booking",
              () => {
                open.add(b.booking_id);
                draw();
                slot.querySelector("button").focus();
              },
              "btn--small btn--danger",
            ),
      );
      if (open.has(b.booking_id)) {
        slot.append(
          h(
            "div",
            { class: "cancelbox", role: "group", "aria-label": `Confirm cancelling booking ${b.booking_id}` },
            h("p", null, h("b", null, `Cancel ${b.seat_id.split(",").join(", ")} for ${show ? show.name : b.show_id}?`), " The seats are released for others at once and the mock charge is refunded. This cannot be undone."),
            h(
              "div",
              null,
              button("Yes, cancel and refund", () => cancel(b, slot), "btn--small btn--primary"),
              button("Keep booking", () => {
                open.delete(b.booking_id);
                draw();
              }),
            ),
          ),
        );
      }
    }
    draw();
    return li;
  }

  async function cancel(b, slot) {
    const p = pending("Cancelling through the Raft leader…", { timer: true });
    replace(slot, p.el);
    let out;
    try {
      out = await post(`/api/bookings/${encodeURIComponent(b.booking_id)}/cancel`, {}, { timeout: 30000 });
    } catch (err) {
      p.stop();
      if (!(err instanceof ApiError)) throw err;
      if (err.http === 401) return;
      outcome.set(b.booking_id, note("warn", "Outcome unknown", `${err.message} The cancellation may or may not have been committed. Retrying reuses the same request ID.`));
      return draw();
    }
    p.stop();
    if (out.status === "OK") {
      open.delete(b.booking_id);
      outcome.set(b.booking_id, note("ok", "Cancelled", `${out.message} Seats released and refund issued. Request ${out.request_id}.`));
      ctx.loadShows({ force: true }).catch(() => {}); // availability changed
      return load();
    }
    if (out.uncertain) {
      outcome.set(b.booking_id, note("warn", `Outcome unknown · ${out.status.replaceAll("_", " ")}`, `${FAILURE[out.status] || out.message} The booking is unchanged as far as this page knows. Confirm again to retry with the same request ID (${out.request_id}).`));
      return draw();
    }
    open.delete(b.booking_id);
    outcome.set(b.booking_id, statusNote(out.status, out.message));
    return load();
  }

  function draw() {
    if (!rows) return;
    const live = rows.filter((r) => r.status === "CONFIRMED").length;
    kicker.textContent = `My Tickets · ${rows.length} ${rows.length === 1 ? "booking" : "bookings"} · ${live} confirmed`;
    if (!rows.length) {
      replace(
        list,
        h(
          "div",
          { class: "empty", style: { "margin-top": "34px" } },
          h("p", { class: "display" }, "No tickets yet."),
          h("p", { class: "lede" }, `The cluster holds no bookings for ${ctx.state.session.user}.`),
          h("a", { class: "btn btn--primary", href: "#/programme" }, "Browse the programme"),
        ),
      );
      return;
    }
    // the cluster returns bookings in commit order; newest first here
    replace(list, h("ul", { class: "stubs" }, rows.slice().reverse().map(item)));
  }

  async function load() {
    refresh.disabled = true;
    if (!rows) replace(list, loadingBlock("Reading your bookings from the cluster"));
    try {
      if (!ctx.state.shows) await ctx.loadShows().catch(() => null); // names/dates for the tickets
      const out = await api("/api/bookings");
      if (!ctx.alive()) return;
      if (out.status === "OK") {
        rows = out.bookings;
        stamp.textContent = `Read confirmed ${clock(out.read_at)}`;
        ctx.setConn("ok");
        draw();
      } else {
        ctx.setConn("down");
        stamp.textContent = rows ? "STALE · live read failed" : "";
        if (rows) {
          draw();
          list.prepend(statusNote(out.status, out.message, load));
        } else replace(list, h("div", { class: "loading-block" }, statusNote(out.status, out.message, load)));
      }
    } catch (err) {
      if (!(err instanceof ApiError)) throw err;
      if (err.http === 401 || !ctx.alive()) return;
      if (rows) {
        draw();
        list.prepend(errorNote(err, load));
      } else replace(list, h("div", { class: "loading-block" }, errorNote(err, load)));
    } finally {
      refresh.disabled = false;
    }
  }

  await load();
  return () => {};
}
