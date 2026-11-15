// FAQ conversation. The gateway runs each question on its own thread and gRPC client, so a slow
// or dead LLM never blocks booking. Answers are labelled by what the backend actually returned.

import { api, post, ApiError } from "../api.js";
import { h, icon, replace } from "../dom.js";
import { FAILURE, button, disclosure, errorNote, note, pending } from "../ui.js";

const SUGGESTIONS = [
  "How do I cancel a booking?",
  "Will I get a refund if I cancel?",
  "Can I change my seats after booking?",
  "How early can I book tickets?",
  "Which seats are available for this show?",
];
const POLL_MS = 1200;

function answer(entry, retry) {
  const sup = entry.support || {};
  const secs = entry.elapsed_ms != null ? `${(entry.elapsed_ms / 1000).toFixed(1)} s` : "";
  let kind = "error";
  let tag = entry.status;
  let tagIcon = "alert";
  let text = entry.text;
  const extra = [];
  if (entry.status === "OK") {
    kind = "generated";
    tag = "LLM-generated answer";
    tagIcon = "spark";
  } else if (entry.status === "LLM_UNAVAILABLE") {
    kind = "fallback";
    tag = "Verbatim FAQ text · not AI-generated";
    tagIcon = "quote";
    extra.push(button("Ask the model again", retry, "btn--small", "refresh"));
  } else if (entry.status === "OUT_OF_DOMAIN") {
    kind = "scope";
    tag = "Outside what this desk answers";
    tagIcon = "info";
  } else {
    tag = `No answer · ${String(entry.status).replaceAll("_", " ")}`;
    text = FAILURE[entry.status] ? `${FAILURE[entry.status]} ${entry.text || ""}` : entry.text;
    extra.push(button("Retry question", retry, "btn--small", "refresh"));
  }
  const hasSupport = sup.correlation_id || (sup.sources && sup.sources.length) || sup.faq_context || sup.live_context;
  return h(
    "div",
    { class: "turn__a", dataset: { kind } },
    h("span", { class: "turn__tag" }, icon(tagIcon), tag),
    h("p", { class: "turn__text" }, text || "(empty reply)"),
    extra.length ? h("div", { class: "note__actions" }, extra) : null,
    h("span", { class: "turn__meta" }, [secs, sup.correlation_id ? `correlation ${sup.correlation_id}` : ""].filter(Boolean).join(" · ")),
    hasSupport
      ? disclosure(
          "Sources and correlation",
          h(
            "div",
            { class: "sources" },
            h("p", null, h("b", null, "Correlation ID "), h("span", { class: "mono" }, sup.correlation_id || "none")),
            h("p", null, h("b", null, "FAQ sources "), h("span", { class: "mono" }, sup.sources && sup.sources.length ? sup.sources.join(", ") : "none matched")),
            sup.faq_context ? h("div", null, h("p", { class: "label label--muted" }, "FAQ text given to the model"), h("pre", null, sup.faq_context)) : null,
            sup.live_context ? h("div", null, h("p", { class: "label label--muted" }, "Live cluster context"), h("pre", null, sup.live_context)) : null,
          ),
        )
      : null,
  );
}

export async function mount(root, ctx) {
  const log = h("div", { class: "chat__log", "aria-live": "polite" });
  const status = h("div");
  const input = h("input", { id: "faq-q", maxlength: 500, autocomplete: "off", placeholder: "Bookings, seats, payments, cancellations, refunds…" });
  const send = h("button", { class: "btn btn--primary", type: "submit" }, "Ask", icon("arrow", "icon--go"));
  const showSel = h("select", { id: "faq-show" }, h("option", { value: "" }, "No show (general question)"));
  const rendered = new Map(); // entry id -> {state, el, ticker}
  let timer = null;
  let disposed = false;

  ctx
    .loadShows()
    .then((data) => {
      for (const s of (data && data.shows) || []) showSel.append(h("option", { value: s.show_id }, `${s.name} (${s.show_id})`));
    })
    .catch(() => {});

  async function ask(query, showId) {
    const q = (query ?? input.value).trim();
    if (!q) return input.focus();
    send.disabled = true;
    replace(status);
    try {
      await post("/api/faq", { query: q, show_id: showId ?? showSel.value });
      if (query === undefined) input.value = "";
      await sync();
    } catch (err) {
      if (!(err instanceof ApiError)) throw err;
      if (err.http === 401) return;
      replace(status, err.data && err.data.status === "FAQ_BUSY" ? note("warn", "One at a time", err.message) : errorNote(err));
    } finally {
      send.disabled = false;
    }
  }

  function paint(entries) {
    if (!entries.length) {
      rendered.clear();
      replace(log, h("div", { class: "empty" }, h("p", { class: "display" }, "Ask the desk."), h("p", { class: "lede" }, "Answers come from the FAQ service: an LLM that is given the matching FAQ text, or that text itself when the model cannot be reached.")));
      return false;
    }
    if (!rendered.size) replace(log);
    let waiting = false;
    for (const e of entries) {
      const seen = rendered.get(e.id);
      if (seen && seen.state === e.state) {
        continue; // unchanged: leave the node alone so open disclosures and scroll stay put
      }
      if (seen && seen.ticker) seen.ticker.stop();
      let reply;
      let ticker = null;
      if (e.state === "pending") {
        waiting = true;
        ticker = pending("Waiting for the FAQ service…", { timer: true });
        reply = h("div", { class: "turn__a" }, ticker.el, h("span", { class: "turn__meta" }, `A cold Ollama model can take about two minutes to load; the gateway waits up to ${Math.round(ctx.state.session.faq_timeout_s)} s per attempt. Booking keeps working meanwhile.`));
      } else {
        reply = answer(e, () => ask(e.query, e.show_id));
      }
      const el = h("div", { class: "turn" }, h("p", { class: "turn__q" }, h("span", { class: "sr" }, "You asked: "), e.query), reply);
      if (seen) seen.el.replaceWith(el);
      else log.append(el);
      rendered.set(e.id, { state: e.state, el, ticker });
      if (!seen || seen.state === "pending") el.scrollIntoView({ block: "nearest", behavior: "auto" });
    }
    for (const [id, r] of rendered) {
      if (!entries.some((e) => e.id === id)) {
        r.el.remove();
        rendered.delete(id);
      }
    }
    return waiting || entries.some((e) => e.state === "pending");
  }

  async function sync() {
    clearTimeout(timer);
    if (disposed) return;
    try {
      const out = await api("/api/faq");
      if (disposed) return;
      if (paint(out.entries)) timer = setTimeout(sync, POLL_MS);
    } catch (err) {
      if (!(err instanceof ApiError)) throw err;
      if (err.http === 401 || disposed) return;
      replace(status, errorNote(err, sync));
    }
  }

  root.append(
    h(
      "header",
      { class: "page-head" },
      h("div", null, h("p", { class: "label label--muted" }, "Help · FAQ desk"), h("h1", { class: "display" }, "Ask about ", h("em", null, "your booking."))),
      h(
        "div",
        { class: "page-head__tools" },
        button("Clear conversation", async () => {
          try {
            await post("/api/faq/clear", {});
            await sync();
          } catch (err) {
            if (err instanceof ApiError && err.http !== 401) replace(status, errorNote(err));
          }
        }),
      ),
    ),
    h(
      "div",
      { class: "help" },
      h(
        "section",
        { class: "chat", "aria-label": "Conversation" },
        log,
        status,
        h(
          "form",
          {
            class: "composer",
            onsubmit: (ev) => {
              ev.preventDefault();
              ask();
            },
          },
          h("div", { class: "field" }, h("label", { for: "faq-q" }, "Your question"), input),
          send,
        ),
      ),
      h(
        "aside",
        { class: "help__side" },
        h("div", { class: "field" }, h("label", { for: "faq-show" }, "Show for live seat context"), showSel, h("p", { class: "hint" }, "If set, seat questions also receive that show's committed seat map.")),
        h("div", null, h("h2", { class: "label" }, "Things people ask"), h("div", { class: "suggest" }, SUGGESTIONS.map((q) => h("button", { type: "button", onclick: () => ask(q) }, q)))),
        h("p", null, "Green rule: text written by the local model (llama3.2:1b via Ollama). Brass rule: the FAQ entry quoted verbatim because the model was unreachable. The model's text is never applied to bookings."),
      ),
    ),
  );

  await sync();
  return () => {
    disposed = true;
    clearTimeout(timer);
    for (const r of rendered.values()) if (r.ticker) r.ticker.stop();
  };
}
