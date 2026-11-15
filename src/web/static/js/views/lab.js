// Demo Lab. Scenarios issue real RPCs through the gateway (ui.cluster helpers); every PASS/FAIL
// is computed there from the responses. Stopping or starting a node is always a manual step in
// your own terminal: this page shows the instruction and has no way to run it.

import { api, post, ApiError } from "../api.js";
import { h, icon, replace } from "../dom.js";
import { button, clock, copyButton, errorNote, loadingBlock, note, pending } from "../ui.js";

function checks(list) {
  return h(
    "ul",
    { class: "checks" },
    list.map((c) => {
      const inconclusive = !c.passed && String(c.detail).startsWith("INCONCLUSIVE");
      const cls = c.passed ? "pass" : inconclusive ? "inconclusive" : "fail";
      return h("li", null, h("span", { class: cls }, c.passed ? "PASS" : inconclusive ? "INCONCLUSIVE" : "FAIL"), h("span", null, c.check, h("small", null, c.detail)));
    }),
  );
}

function resultsTable(rows) {
  const cols = ["request_id", "status", "booking_id", "attempts", "latency_ms"];
  return h(
    "div",
    { class: "tablewrap" },
    h(
      "table",
      { class: "tech" },
      h("thead", null, h("tr", null, cols.map((c) => h("th", { scope: "col" }, c.replace("_", " "))))),
      h("tbody", null, rows.map((r) => h("tr", null, cols.map((c) => h("td", null, r[c] === "" ? "–" : String(r[c])))))),
    ),
  );
}

function cmd(text) {
  return h("div", { class: "cmd" }, h("span", null, text), copyButton(text, "Copy"));
}

export async function mount(root, ctx) {
  let disposed = false;
  let view = null; // last /api/lab payload
  let busy = false;
  const runners = [];

  const head = h(
    "header",
    { class: "page-head" },
    h("div", null, h("p", { class: "label label--muted" }, "System · fault and concurrency demonstrations"), h("h1", { class: "display" }, "Demo ", h("em", null, "Lab"))),
    h("div", { class: "page-head__tools" }, h("p", { class: "stamp-time" }, "Real RPCs · evidence-based checks · manual process control")),
  );
  const bodyEl = h("div");
  root.append(head, bodyEl);
  replace(bodyEl, loadingBlock("Observing the cluster"));

  let shows = [];
  try {
    const data = await ctx.loadShows();
    shows = (data && data.shows) || [];
  } catch (err) {
    if (err instanceof ApiError && err.http === 401) return null;
  }

  // ---- shared: a show + free-seat picker fed by real reads
  function target(id, preferShow) {
    const showSel = h("select", { id: `${id}-show` }, shows.map((s) => h("option", { value: s.show_id, selected: s.show_id === preferShow }, `${s.name} (${s.show_id})`)));
    const seatSel = h("select", { id: `${id}-seat` }, h("option", { value: "" }, "Reading seats…"));
    async function fill() {
      if (!showSel.value) return replace(seatSel, h("option", { value: "" }, "No shows loaded"));
      replace(seatSel, h("option", { value: "" }, "Reading seats…"));
      try {
        const out = await api(`/api/shows/${encodeURIComponent(showSel.value)}/seats`);
        const free = out.status === "OK" ? out.seats.filter((s) => s.status === "AVAILABLE") : [];
        replace(seatSel, free.length ? free.map((s) => h("option", { value: s.seat_id }, s.seat_id)) : h("option", { value: "" }, out.status === "OK" ? "No free seats" : `Unreadable (${out.status})`));
        if (free.length) seatSel.selectedIndex = free.length - 1; // back of the house: least likely to be wanted
      } catch {
        replace(seatSel, h("option", { value: "" }, "Unreadable"));
      }
    }
    showSel.addEventListener("change", fill);
    fill();
    return {
      fields: [h("div", { class: "field" }, h("label", { for: `${id}-show` }, "Show"), showSel), h("div", { class: "field" }, h("label", { for: `${id}-seat` }, "Free seat"), seatSel)],
      value: () => ({ show_id: showSel.value, seat_id: seatSel.value }),
      refill: fill,
    };
  }

  // ---- shared: run one scenario with an honest elapsed-time indicator
  async function run(out, label, path, payload, timeout, after) {
    if (busy) return;
    busy = true;
    for (const b of runners) b.disabled = true;
    const p = pending(label, { timer: true });
    replace(out, p.el);
    try {
      const data = await post(path, payload, { timeout });
      if (disposed) return;
      view = { ...view, ...data };
      after(data);
      drawTimeline();
    } catch (err) {
      if (!(err instanceof ApiError)) throw err;
      if (err.http !== 401 && !disposed) replace(out, errorNote(err));
    } finally {
      p.stop();
      busy = false;
      for (const b of runners) b.disabled = false;
    }
  }

  function runner(label, fn) {
    const b = h("button", { class: "btn btn--primary", type: "button", onclick: fn }, label);
    runners.push(b);
    return b;
  }

  function scenario(no, title, ...children) {
    return h("section", { class: "scenario", "aria-labelledby": `sc-${no}` }, h("span", { class: "scenario__no", "aria-hidden": "true" }, no), h("h2", { id: `sc-${no}` }, title), h("div", { class: "scenario__body" }, children));
  }

  // ---- 1 + 2: race and same-request retry
  function concurrency(no, title, blurb, id, path, def, noun, preferShow) {
    const t = target(id, preferShow);
    const n = h("input", { id: `${id}-n`, type: "number", min: 2, max: 20, value: def, inputmode: "numeric" });
    const out = h("div", { "aria-live": "polite" });
    const go = runner(`Run ${noun}`, () => {
      const v = t.value();
      if (!v.seat_id) return replace(out, note("warn", "Pick a free seat", "The scenario needs a seat that is currently AVAILABLE."));
      run(out, `Running ${n.value} concurrent requests…`, path, { ...v, n: Number(n.value) }, 60000, (data) => {
        replace(out, resultsTable(data.results), checks(data.checks));
        out.firstChild.style.marginBottom = "12px";
        t.refill();
      });
    });
    return scenario(
      no,
      title,
      h("p", null, blurb),
      h("div", { class: "scenario__form" }, t.fields, h("div", { class: "field" }, h("label", { for: `${id}-n` }, noun === "race" ? "Concurrent clients" : "Concurrent copies"), n), go),
      out,
    );
  }

  // ---- 3: guided failover
  const leaderLine = h("p");
  const stopCmd = h("div");
  const watchOut = h("div", { "aria-live": "polite" });
  const catchStep = h("li", { hidden: true });
  const catchOut = h("div", { "aria-live": "polite" });

  function drawFailover() {
    if (view.leader) {
      replace(leaderLine, "Current leader: ", h("b", { class: "mono" }, view.leader), ` (term ${view.term}), observed ${clock(view.sampled_at)}. Stop it yourself, in the terminal that runs it:`);
      replace(stopCmd, cmd(view.hints[view.leader].stop));
    } else {
      replace(leaderLine, h("span", { class: "fail" }, "No leader is currently observed."), " Start a majority of nodes, then re-observe.");
      replace(stopCmd);
    }
    const old = view.failover && view.failover.old;
    catchStep.hidden = !old;
    if (old) {
      replace(
        catchStep,
        h(
          "div",
          null,
          h("p", null, "Restart the old leader ", h("b", { class: "mono" }, old), " yourself, then check that it catches up:"),
          cmd(view.hints[old].start),
          h("div", null, runner("Check old-leader catch-up", () => run(catchOut, "Waiting for the old leader to reach the leader's commit index (up to 30 s)…", "/api/lab/failover/catchup", {}, 45000, (d) => replace(catchOut, checks(d.checks))))),
          catchOut,
        ),
      );
    }
  }

  // ---- 4: no quorum
  const nqTarget = target("nq", shows[5] && shows[5].show_id);
  const nqPending = h("div");
  const nqOut = h("div", { "aria-live": "polite" });

  function drawNoQuorum() {
    const p = view.nq_pending;
    replace(
      nqPending,
      p
        ? note("warn", "Pending request (retried unchanged)", `${p.request_id} for ${p.show_id}/${p.seat_id} · ${p.attempts} attempt(s)${p.refused_without_quorum ? " · refused once with a majority unreachable" : ""}. The next attempt reuses this request ID, show and seat.`, [
            button("Abandon this request", async () => {
              try {
                view = { ...view, ...(await post("/api/lab/noquorum/reset", {})) };
                drawNoQuorum();
              } catch (err) {
                if (err instanceof ApiError && err.http !== 401) replace(nqOut, errorNote(err));
              }
            }),
          ])
        : null,
    );
  }

  // ---- timeline + export
  const timeline = h("div");
  function drawTimeline() {
    const ev = view.timeline || [];
    replace(
      timeline,
      ev.length
        ? h(
            "div",
            { class: "tablewrap" },
            h(
              "table",
              { class: "tech" },
              h("thead", null, h("tr", null, ["t (ms)", "UTC", "Kind", "Node", "Correlation", "Detail"].map((c, i) => h("th", { scope: "col", class: i === 0 ? "num" : null }, c)))),
              h("tbody", null, ev.map((e) => h("tr", null, h("td", { class: "num" }, e.t_ms), h("td", null, e.ts.slice(11, 23)), h("td", null, e.kind), h("td", null, e.node || "–"), h("td", null, e.correlation || "–"), h("td", { class: "wrap" }, e.detail)))),
            ),
          )
        : h("p", { class: "stamp-time" }, "No events yet. Run a scenario and its requests, responses and checks are recorded here."),
    );
    if (view.failover) drawFailover();
    drawNoQuorum();
  }

  async function observe() {
    try {
      view = { ...(view || {}), ...(await api("/api/lab", { timeout: 10000 })) };
      if (disposed) return false;
      ctx.setConn(view.leader ? "ok" : "down");
      return true;
    } catch (err) {
      if (!(err instanceof ApiError)) throw err;
      if (err.http !== 401 && !disposed) replace(bodyEl, h("div", { class: "loading-block" }, errorNote(err, () => ctx.navigate("/system/lab"))));
      return false;
    }
  }

  if (!(await observe())) return null;

  replace(
    bodyEl,
    note("warn", "These scenarios make real bookings", `Seats won in a race or retry are booked and mock-charged to ${ctx.state.session.user}. They appear under My Tickets and can be cancelled there.`),
    concurrency("1", "Concurrent seat race", "Several clients, each with its own request ID, try to book the same seat at the same instant. Exactly one may win; the rest must be refused as ALREADY_BOOKED.", "race", "/api/lab/race", 8, "race", shows[7] && shows[7].show_id),
    concurrency("2", "Same-request retry", "One request ID is submitted several times at once, as a client would after timeouts. All copies must resolve to one booking and one charge.", "retry", "/api/lab/retry", 5, "retry", shows[6] && shows[6].show_id),
    scenario(
      "3",
      "Guided leader failure and recovery",
      h("p", null, "The page watches real node status every 200 ms while you stop the leader by hand. Failover time is measured from the first sample without the old leader to the first sample showing a different leader in a higher term."),
      h(
        "ol",
        { class: "steps" },
        h("li", null, h("div", null, leaderLine, stopCmd, h("div", null, button("Re-observe leader", async () => (await observe()) && drawFailover(), "btn--small", "refresh")))),
        h(
          "li",
          null,
          h(
            "div",
            null,
            h("p", null, "Start watching, then run the stop instruction within 30 seconds."),
            h(
              "div",
              null,
              runner("Watch failover", () =>
                run(watchOut, "Polling node status every 200 ms (30 s window)…", "/api/lab/failover/watch", {}, 45000, (d) => {
                  replace(watchOut, d.checks ? checks(d.checks) : note("warn", d.status.replaceAll("_", " "), d.message));
                  observe().then((ok) => ok && drawFailover());
                }),
              ),
            ),
            watchOut,
          ),
        ),
        catchStep,
      ),
    ),
    scenario(
      "4",
      "No-quorum scenario",
      h("p", null, "Stop two of the three nodes, attempt a booking, restart one node, then attempt again: the same request ID is reused. A refusal only counts as evidence when the gateway also saw fewer than a majority of nodes answering."),
      h("div", { class: "checks" }, Object.entries(view.hints).map(([node, x]) => h("li", null, h("span", null, node), h("span", null, x.stop, h("small", null, `afterwards: ${x.start}`))))),
      h("div", { class: "scenario__form" }, nqTarget.fields, h("div"), runner("Attempt booking now", () => {
        const v = nqTarget.value();
        if (!view.nq_pending && !v.seat_id) return replace(nqOut, note("warn", "Pick a free seat", "Choose the seat while the cluster is still healthy, then stop the nodes."));
        run(nqOut, "Attempting the booking (up to 4 s of retries)…", "/api/lab/noquorum", v, 30000, (d) => {
          const r = d.result;
          replace(nqOut, h("p", { class: "mono" }, `${r.status}${r.booking_id ? " · " + r.booking_id : ""} · request ${r.request_id} · ${r.attempts} RPC attempt(s) · ${r.latency_ms} ms · ${d.nodes_answered}/${d.nodes_total} nodes answered beforehand`), checks(d.checks));
          drawNoQuorum();
        });
      })),
      nqPending,
      nqOut,
    ),
    h(
      "section",
      { class: "sys-section" },
      h("h2", null, "Timeline"),
      h("p", null, "Timestamped, correlated events for this browser session. The exports contain every check recorded above plus this table."),
      h(
        "div",
        { class: "tools", style: { "margin-bottom": "14px" } },
        h("a", { class: "btn btn--small", href: "/api/lab/export.md", download: "demo-lab-summary.md" }, icon("download"), "Summary (Markdown)"),
        h("a", { class: "btn btn--small", href: "/api/lab/export.json", download: "demo-lab-summary.json" }, icon("download"), "Summary (JSON)"),
        button("Clear timeline", async () => {
          try {
            view = { ...view, ...(await post("/api/lab/clear", {})) };
            drawTimeline();
          } catch (err) {
            if (err instanceof ApiError && err.http !== 401) replace(timeline, errorNote(err));
          }
        }),
      ),
      timeline,
    ),
  );
  bodyEl.firstChild.style.marginTop = "26px";
  drawFailover();
  drawTimeline();
  return () => {
    disposed = true;
  };
}
