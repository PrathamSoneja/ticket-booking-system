import { ApiError } from "../api.js";
import { h, icon, replace } from "../dom.js";
import { plate } from "../art.js";
import { availability, button, clock, errorNote, loadingBlock, note, statusNote, when } from "../ui.js";

// filter state survives navigation within the session (plain memory, nothing persisted)
const filter = { q: "", onlyFree: false, sort: "date" };
let entered = false;

function sorted(shows) {
  const list = shows.slice();
  if (filter.sort === "name") list.sort((a, b) => a.name.localeCompare(b.name));
  else if (filter.sort === "free") list.sort((a, b) => (b.seats_available ?? -1) - (a.seats_available ?? -1));
  else list.sort((a, b) => (when(a.start_time).ts || 0) - (when(b.start_time).ts || 0));
  return list;
}

function matches(show) {
  const q = filter.q.trim().toLowerCase();
  if (filter.onlyFree && !(show.seats_available > 0)) return false;
  if (!q) return true;
  const w = when(show.start_time);
  const hay = [show.name, show.venue, show.show_id, w.ok ? `${w.date} ${w.monthLong} ${w.weekdayLong}` : w.raw].join(" ").toLowerCase();
  return q.split(/\s+/).every((bit) => hay.includes(bit));
}

function dateRange(shows) {
  const ts = shows.map((s) => when(s.start_time)).filter((w) => w.ok).sort((a, b) => a.ts - b.ts);
  if (!ts.length) return "";
  const a = ts[0];
  const b = ts[ts.length - 1];
  return a.ts === b.ts ? `${a.day} ${a.month} ${a.year}` : `${a.day} ${a.month} – ${b.day} ${b.month} ${b.year}`;
}

function feature(show, i, labelled) {
  const w = when(show.start_time);
  return h(
    "a",
    { class: entered ? "feature" : "feature enter", href: `#/show/${encodeURIComponent(show.show_id)}`, style: { "--i": String(i) } },
    h("div", { class: "feature__art", "aria-hidden": "true" }, plate(show.show_id)),
    h(
      "div",
      { class: "feature__body" },
      h(
        "div",
        null,
        h("p", { class: "label label--muted" }, labelled ? "No. 01 · First on the bill" : "No. 01"),
        h("h2", { class: "display feature__title" }, show.name),
      ),
      h(
        "dl",
        { class: "feature__meta" },
        h("dt", null, "When"),
        h("dd", { title: w.ok ? `Your local time: ${w.local}` : null }, w.ok ? `${w.weekdayLong} ${w.day} ${w.monthLong} ${w.year}, ${w.time} UTC` : w.raw),
        h("dt", null, "Where"),
        h("dd", null, show.venue),
        h("dt", null, "Show ID"),
        h("dd", { class: "mono" }, show.show_id),
      ),
      h("div", { class: "feature__cta" }, h("span", { class: "btn btn--primary" }, "Choose seats", icon("arrow", "icon--go")), availability(show)),
    ),
  );
}

function row(show, n, i) {
  const w = when(show.start_time);
  return h(
    "a",
    { class: entered ? "row" : "row enter", href: `#/show/${encodeURIComponent(show.show_id)}`, style: { "--i": String(i) } },
    h("span", { class: "row__no", "aria-hidden": "true" }, String(n).padStart(2, "0")),
    h(
      "span",
      { class: "row__date", title: w.ok ? `Your local time: ${w.local}` : null },
      h("span", { class: "row__day" }, w.ok ? w.day : "?"),
      h("span", { class: "row__mon" }, w.ok ? `${w.month} ${w.year}` : w.raw, h("small", null, w.ok ? `${w.weekday} ${w.time} UTC` : "")),
    ),
    h("span", { class: "row__main" }, h("span", { class: "row__title" }, show.name), h("span", { class: "row__venue" }, show.venue)),
    h("span", { class: "row__art", "aria-hidden": "true" }, plate(show.show_id)),
    availability(show),
    icon("arrow"),
  );
}

export async function mount(root, ctx) {
  const kicker = h("p", { class: "label label--muted" }, "Programme");
  const stamp = h("p", null, "Reading the programme from the cluster…");
  const results = h("div", { "aria-live": "polite" });
  const search = h("input", { id: "p-q", type: "search", placeholder: "Title, venue, date or show ID", value: filter.q, autocomplete: "off" });
  const sort = h(
    "select",
    { id: "p-sort" },
    [["date", "Date"], ["name", "Title"], ["free", "Most seats free"]].map(([v, t]) => h("option", { value: v, selected: filter.sort === v }, t)),
  );
  const free = h("input", { type: "checkbox", checked: filter.onlyFree });
  const refresh = button("Refresh", () => load(true), "btn--small", "refresh");
  let data = ctx.state.shows;

  search.addEventListener("input", () => {
    filter.q = search.value;
    draw();
  });
  sort.addEventListener("change", () => {
    filter.sort = sort.value;
    draw();
  });
  free.addEventListener("change", () => {
    filter.onlyFree = free.checked;
    draw();
  });

  root.append(
    h(
      "header",
      { class: "prog-head" },
      h("div", { class: "prog-head__title" }, kicker, h("h1", { class: "display" }, "The programme,", h("em", null, "night by night."))),
      h("div", { class: "prog-head__side" }, stamp, h("div", null, refresh)),
    ),
    h(
      "div",
      { class: "filters", role: "search" },
      h("div", { class: "field" }, h("label", { for: "p-q" }, "Search"), search),
      h("div", { class: "field" }, h("label", { for: "p-sort" }, "Order"), sort),
      h("label", { class: "check" }, free, "Only shows with free seats"),
    ),
    results,
  );

  function draw() {
    if (!data) return;
    const all = data.shows;
    kicker.textContent = `Programme · ${all.length} ${all.length === 1 ? "show" : "shows"}${dateRange(all) ? " · " + dateRange(all) : ""}`;
    const list = sorted(all.filter(matches));
    if (!all.length) {
      replace(results, h("div", { class: "empty" }, h("p", { class: "display" }, "The programme is empty."), h("p", { class: "lede" }, "The cluster returned no shows. The catalogue is seeded by the app nodes on first start.")));
      return;
    }
    if (!list.length) {
      replace(
        results,
        h(
          "div",
          { class: "empty" },
          h("p", { class: "display" }, "Nothing on the bill matches."),
          h("p", { class: "lede" }, "No show fits the current search and filter."),
          button("Clear search and filter", () => {
            filter.q = "";
            filter.onlyFree = false;
            search.value = "";
            free.checked = false;
            draw();
            search.focus();
          }),
        ),
      );
      return;
    }
    const plain = !filter.q.trim() && !filter.onlyFree;
    const lead = plain ? list[0] : null;
    const rest = lead ? list.slice(1) : list;
    replace(
      results,
      lead ? feature(lead, 1, filter.sort === "date") : null,
      rest.length
        ? h(
            "section",
            { "aria-label": "Shows" },
            h(
              "div",
              { class: "list-head" },
              h("h2", { class: "label" }, lead ? "Also in the programme" : `${list.length} of ${all.length} shows`),
              h("span", { class: "label label--muted" }, "Dates as listed, UTC"),
            ),
            h("div", { class: "rows" }, rest.map((s, i) => row(s, i + (lead ? 2 : 1), i + 2))),
          )
        : null,
    );
    entered = true;
  }

  async function load(force) {
    refresh.disabled = true;
    if (!data) replace(results, loadingBlock("Reading shows and seat maps through the Raft leader"));
    try {
      const out = await ctx.loadShows({ force });
      if (!ctx.alive()) return;
      if (out.status === "OK") {
        data = out;
        stamp.textContent = `Every listing and seat count is a linearizable read from the cluster, last confirmed at ${clock(out.read_at)}.`;
        draw();
      } else if (data) {
        stamp.textContent = `STALE: the live read failed (${out.status}). Showing the programme last confirmed at ${clock(data.read_at)}.`;
        draw();
        results.prepend(statusNote(out.status, out.message, () => load(true)));
      } else {
        stamp.textContent = "The programme could not be read.";
        replace(results, statusNote(out.status, out.message, () => load(true)));
      }
    } catch (err) {
      if (!(err instanceof ApiError)) throw err;
      if (err.http === 401 || !ctx.alive()) return;
      ctx.setConn("down");
      if (data) {
        stamp.textContent = `STALE: showing the programme last confirmed at ${clock(data.read_at)}.`;
        draw();
        results.prepend(errorNote(err, () => load(true)));
      } else {
        stamp.textContent = "The programme could not be read.";
        replace(results, errorNote(err, () => load(true)));
      }
    } finally {
      refresh.disabled = false;
    }
  }

  if (data) draw();
  await load(true);
  return () => {};
}
