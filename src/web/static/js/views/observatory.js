// Cluster Observatory. Every value is from a CLUSTER_STATUS RPC sent to that node alone
// (ui.cluster.observe); nothing is inferred, smoothed or filled in when a node does not answer.

import { api, ApiError } from "../api.js";
import { h, replace, s } from "../dom.js";
import { button, clock, errorNote, loadingBlock, note, statusNote } from "../ui.js";

const BLANK = "–";
const COLS = [
  ["node", "Node", (r) => r.node],
  ["address", "Address", (r) => r.address],
  ["reach", "Reachability", (r) => (r.rpc_status === "OK" ? "REACHABLE" : r.reachable ? r.rpc_status : "UNREACHABLE")],
  ["role", "Role", (r) => r.role],
  ["term", "Term", (r) => r.term, true],
  ["leader", "Leader seen", (r) => r.leader_id],
  ["log", "Log length", (r) => r.log_length, true],
  ["commit", "Commit", (r) => r.commit_index, true],
  ["applied", "Applied", (r) => r.last_applied, true],
  ["lag", "Commit lag", (r) => r.commit_lag, true],
  ["match", "Leader matchIndex", (r) => r.leader_match_index, true],
  ["logd", "Log digest", (r) => r.log_digest],
  ["stated", "State digest", (r) => r.state_digest],
  ["rpc", "RPC ms", (r) => r.rpc_ms, true],
];
const NO_FLASH = new Set(["rpc"]);
const HISTORY = 60;

function text(v) {
  return v === undefined || v === null || v === "" ? BLANK : String(v);
}

export async function mount(root, ctx) {
  const health = h("dl", { class: "health", "aria-live": "polite" });
  const alerts = h("div");
  const tbody = h("tbody");
  const groups = h("ul", { class: "checks" });
  const sparks = h("div");
  const analytics = h("div");
  const stamp = h("span", { class: "stamp-time" }, "");
  const auto = h("input", { type: "checkbox" });
  const refresh = button("Refresh", () => tick(true), "btn--small", "refresh");
  const cells = new Map(); // node -> {col -> td}
  const history = new Map(); // node -> [lag|null]
  let timer = null;
  let disposed = false;
  let first = true;

  root.append(
    h(
      "header",
      { class: "page-head" },
      h("div", null, h("p", { class: "label label--muted" }, "System · read-only diagnostics"), h("h1", { class: "display" }, "Cluster ", h("em", null, "Observatory"))),
      h("div", { class: "page-head__tools tools" }, stamp, h("label", { class: "check" }, auto, "Auto-refresh every 2 s"), refresh),
    ),
    health,
    alerts,
    h(
      "section",
      { class: "sys-section" },
      h("h2", null, "Nodes"),
      h("p", null, "One CLUSTER_STATUS RPC per node, sent in parallel with no retries or redirects. A node that does not answer shows UNREACHABLE and blank values, never a remembered or assumed state. Changed values flash once."),
      h(
        "div",
        { class: "tablewrap" },
        h(
          "table",
          { class: "tech tech--stack" },
          h("caption", { class: "sr" }, "Per-node Raft status"),
          h("thead", null, h("tr", null, COLS.map(([, label, , num]) => h("th", { scope: "col", class: num ? "num" : null }, label)))),
          tbody,
        ),
      ),
      h("p", { class: "stamp-time", style: { "margin-top": "10px" } }, "Commit lag = leader commit index − node commit index, as sampled. Leader matchIndex is the leader's own record of each follower's replicated index."),
    ),
    h("section", { class: "sys-section" }, h("h2", null, "Consistency"), h("p", null, "Reachable nodes grouped by the commit index they report. Digests are only comparable inside one group."), groups),
    h("section", { class: "sys-section" }, h("h2", null, "Commit lag, sampled"), h("p", null, `The last ${HISTORY} samples taken while this page was open. Gaps are samples where the node did not answer or no leader was observed.`), sparks),
    h("section", { class: "sys-section" }, h("h2", null, "Booking analytics"), h("p", null, "The BOOKING_ANALYTICS read, as served by the leader."), analytics),
  );
  replace(health, h("div", null, loadingBlock("Probing every node")));

  function stat(label, value, small, state) {
    return h("div", { dataset: { state: state || "" } }, h("dt", null, label), h("dd", null, value, small ? h("small", null, small) : null));
  }

  function paint(data) {
    const snap = data.snapshot;
    const rep = data.consistency;
    const quorum = data.nodes_ok >= data.majority;
    const g = rep.groups;
    let conv = "Not converged";
    let convNote = "Reachable nodes report different commit indexes, or a node is unreachable.";
    let convState = "bad";
    if (rep.converged && g.length === 1 && g[0].log_digests_equal && g[0].state_digests_equal) {
      conv = "Converged";
      convNote = `All ${data.nodes_total} nodes at commit ${g[0].commit_index} with equal log and state digests.`;
      convState = "good";
    } else if (rep.converged) {
      convNote = "All nodes report one commit index, but digests differ or a node is still applying.";
    }
    replace(
      health,
      stat("Leader", snap.leader || "None observed", snap.leader ? `term ${snap.term}` : "No reachable node reports itself leader.", snap.leader ? "good" : "bad"),
      stat("Nodes answering", `${data.nodes_ok} / ${data.nodes_total}`, quorum ? `Majority is ${data.majority}: a quorum is reachable from the gateway.` : `Majority is ${data.majority}: no quorum is reachable from the gateway.`, quorum ? "good" : "bad"),
      stat("Replication", conv, convNote, convState),
      stat("Sampled", clock(snap.sampled_at), snap.sampled_at),
    );
    const multi = Object.entries(rep.multiple_leaders_same_term || {});
    replace(alerts, multi.length ? note("error", "Multiple leaders in one term", multi.map(([t, n]) => `term ${t}: ${n.join(", ")}`).join("; ")) : null);
    if (alerts.firstChild) alerts.firstChild.style.marginTop = "18px";

    for (const r of snap.rows) {
      let row = cells.get(r.node);
      if (!row) {
        row = {};
        const tr = h("tr");
        for (const [key, label, , num] of COLS) {
          row[key] = h("td", { class: num ? "num" : null, dataset: { label } }, BLANK);
          tr.append(row[key]);
        }
        tbody.append(tr);
        cells.set(r.node, row);
      }
      for (const [key, , get] of COLS) {
        const td = row[key];
        const next = text(get(r));
        if (td.dataset.v === next) continue;
        td.dataset.v = next;
        if (key === "role") {
          const kind = r.rpc_status !== "OK" ? "down" : r.role === "leader" ? "leader" : r.role === "follower" ? "follower" : "other";
          replace(td, h("span", { class: "role", dataset: { role: kind } }, next));
        } else if (key === "reach") {
          replace(td, h("span", { class: next === "REACHABLE" ? "pass" : "fail" }, next));
        } else td.textContent = next;
        if (!first && !NO_FLASH.has(key)) {
          td.classList.remove("is-changed");
          void td.offsetWidth;
          td.classList.add("is-changed");
        }
      }
      const hist = history.get(r.node) || [];
      hist.push(typeof r.commit_lag === "number" ? r.commit_lag : null);
      history.set(r.node, hist.slice(-HISTORY));
    }
    first = false;

    replace(
      groups,
      g.length
        ? g.map((x) =>
            h(
              "li",
              null,
              h("span", null, `commit ${x.commit_index}`),
              h(
                "span",
                null,
                x.nodes.join(", "),
                h(
                  "small",
                  null,
                  h("span", { class: x.log_digests_equal ? "pass" : "fail" }, x.log_digests_equal ? "log digests equal" : "LOG DIGESTS DIFFER"),
                  " · ",
                  h("span", { class: x.state_digests_equal ? "pass" : "inconclusive" }, x.state_digests_equal ? "state digests equal" : "state digests differ or still applying"),
                ),
              ),
            ),
          )
        : h("li", null, h("span", { class: "fail" }, "no data"), h("span", null, "No node answered CLUSTER_STATUS.")),
    );

    const peak = Math.max(1, ...[...history.values()].flat().filter((v) => v !== null));
    replace(
      sparks,
      [...history.entries()].map(([node, hist]) => {
        let d = "";
        let pen = false;
        hist.forEach((v, i) => {
          if (v === null) {
            pen = false;
            return;
          }
          const x = hist.length > 1 ? (i / (HISTORY - 1)) * 100 : 0;
          const y = 28 - (v / peak) * 24;
          d += `${pen ? "L" : "M"}${x.toFixed(2)} ${y.toFixed(2)}`;
          if (!pen) d += `h0.01`;
          pen = true;
        });
        const last = hist[hist.length - 1];
        return h(
          "div",
          { class: "spark" },
          h("span", null, node),
          s("svg", { viewBox: "0 0 100 32", preserveAspectRatio: "none", role: "img", "aria-label": `Commit lag samples for ${node}; latest ${last === null ? "not available" : last}` }, s("line", { x1: 0, y1: 28, x2: 100, y2: 28 }), s("path", { d })),
          h("span", { class: "num" }, last === null ? "no sample" : `lag ${last}`),
        );
      }),
      h("p", { class: "stamp-time", style: { "margin-top": "8px" } }, `Vertical scale: 0 to ${peak} entries.`),
    );
  }

  async function loadAnalytics() {
    try {
      const out = await api("/api/analytics");
      if (disposed) return;
      if (out.status !== "OK") return replace(analytics, statusNote(out.status, out.message, loadAnalytics));
      replace(
        analytics,
        h(
          "div",
          { class: "tablewrap" },
          h(
            "table",
            { class: "tech" },
            h("caption", { class: "sr" }, `Booking analytics, read confirmed ${clock(out.read_at)}`),
            h("thead", null, h("tr", null, h("th", { scope: "col" }, "Show"), h("th", { scope: "col" }, "Title"), h("th", { scope: "col", class: "num" }, "Bookings"), h("th", { scope: "col", class: "num" }, "Cancelled"), h("th", { scope: "col", class: "num" }, "Cancellation rate"))),
            h(
              "tbody",
              null,
              out.rows.map((r) => {
                const show = ctx.showById(r.show_id);
                return h("tr", null, h("td", null, r.show_id), h("td", null, show ? show.name : BLANK), h("td", { class: "num" }, r.booking_count), h("td", { class: "num" }, r.cancelled_count), h("td", { class: "num" }, r.booking_count ? `${(r.cancellation_rate * 100).toFixed(1)} %` : BLANK));
              }),
            ),
          ),
        ),
      );
    } catch (err) {
      if (err instanceof ApiError && err.http !== 401 && !disposed) replace(analytics, errorNote(err, loadAnalytics));
    }
  }

  async function tick(manual) {
    clearTimeout(timer);
    if (disposed) return;
    refresh.disabled = true;
    try {
      const data = await api("/api/cluster", { timeout: 10000 });
      if (disposed) return;
      ctx.setConn(data.nodes_ok ? "ok" : "down");
      stamp.textContent = `Sampled ${clock(data.snapshot.sampled_at)}`;
      paint(data);
      if (manual) loadAnalytics();
    } catch (err) {
      if (!(err instanceof ApiError)) throw err;
      if (err.http === 401 || disposed) return;
      stamp.textContent = "Sample failed";
      replace(alerts, errorNote(err, () => tick(true)));
      alerts.firstChild.style.marginTop = "18px";
    } finally {
      refresh.disabled = false;
      if (auto.checked && !disposed) timer = setTimeout(() => tick(false), 2000);
    }
  }
  auto.addEventListener("change", () => (auto.checked ? tick(false) : clearTimeout(timer)));

  ctx.loadShows().catch(() => {}).then(loadAnalytics);
  await tick(false);
  return () => {
    disposed = true;
    clearTimeout(timer);
  };
}
