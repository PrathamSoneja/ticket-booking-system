// Shell: session bootstrap, masthead, hash router. Each view exports mount(root, ctx) and may
// return a dispose function (timers, observers) that runs before the next view mounts.

import { api, post, setCsrf, onSignedOut, ApiError } from "./api.js";
import { h, replace } from "./dom.js";
import { errorNote, loadingBlock, toast } from "./ui.js";
import * as auth from "./views/auth.js";
import * as programme from "./views/programme.js";
import * as show from "./views/show.js";
import * as tickets from "./views/tickets.js";
import * as help from "./views/help.js";
import * as observatory from "./views/observatory.js";
import * as lab from "./views/lab.js";

const main = document.getElementById("main");
const masthead = document.getElementById("masthead");
const colophon = document.getElementById("colophon");

const state = {
  session: null, // /api/session payload
  notice: "", // one-off message for the sign-in page (e.g. session expired)
  shows: null, // last good /api/shows payload
  conn: "unknown",
};

const ROUTES = [
  { rx: /^\/?$/, view: programme, nav: "programme", title: "Programme" },
  { rx: /^\/programme$/, view: programme, nav: "programme", title: "Programme" },
  { rx: /^\/show\/([^/]+)$/, view: show, nav: "programme", title: "Seats" },
  { rx: /^\/tickets$/, view: tickets, nav: "tickets", title: "My Tickets" },
  { rx: /^\/help$/, view: help, nav: "help", title: "Help" },
  { rx: /^\/system\/observatory$/, view: observatory, nav: "observatory", title: "Cluster Observatory", theme: "ink" },
  { rx: /^\/system\/lab$/, view: lab, nav: "lab", title: "Demo Lab", theme: "ink" },
];

let dispose = null;
let mountSeq = 0;

const ctx = {
  state,
  navigate(path) {
    if (location.hash === `#${path}`) route();
    else location.hash = path;
  },
  // Show catalogue with live seat counts. Returns the last good payload when a refresh fails.
  async loadShows({ force = false } = {}) {
    if (state.shows && !force) return state.shows;
    const data = await api("/api/shows");
    setConn(data.status === "OK" ? "ok" : "down");
    if (data.status === "OK") state.shows = data;
    return data;
  },
  showById(id) {
    return state.shows ? state.shows.shows.find((s) => s.show_id === id) : null;
  },
  setConn,
  async refreshSession() {
    state.session = await api("/api/session");
    return state.session;
  },
};

function setConn(value) {
  state.conn = value;
  renderColophon();
}

function link(href, label, key, current) {
  return h("a", { href, "aria-current": key === current ? "page" : null }, label);
}

function renderMasthead(current) {
  const s = state.session;
  const signedIn = s && s.authenticated;
  replace(
    masthead,
    h(
      "div",
      { class: "masthead__bar" },
      h("a", { class: "wordmark", href: "#/programme", "aria-label": "The Night Programme, home" }, "The Night Programme", h("i", null, ".")),
      signedIn
        ? h(
            "nav",
            { class: "nav", "aria-label": "Booking" },
            link("#/programme", "Programme", "programme", current),
            link("#/tickets", "My Tickets", "tickets", current),
            link("#/help", "Help", "help", current),
          )
        : null,
      signedIn
        ? h(
            "div",
            { class: "masthead__aside" },
            h(
              "nav",
              { class: "sysnav", "aria-label": "System" },
              h("span", { class: "label" }, "System"),
              link("#/system/observatory", "Observatory", "observatory", current),
              link("#/system/lab", "Demo Lab", "lab", current),
            ),
            h("div", { class: "who" }, h("b", null, s.user), h("button", { class: "linkbtn", type: "button", onclick: signOut }, "Sign out")),
          )
        : null,
    ),
  );
}

function renderColophon() {
  const s = state.session;
  const nodes = s ? Object.entries(s.nodes).map(([k, v]) => `${k} ${v}`).join("  ·  ") : "";
  const label = { ok: "Cluster answering", down: "Cluster not answering", unknown: "Cluster not yet contacted" }[state.conn];
  replace(
    colophon,
    h("span", { class: "conn", dataset: { state: state.conn } }, label),
    h("span", null, "browser → local gateway → gRPC"),
    h("span", null, nodes),
  );
}

async function signOut() {
  try {
    const out = await post("/api/logout", {});
    if (!out.revoked_on_cluster) toast("Signed out here. The cluster could not confirm revoking the session; it will expire on its own.");
  } catch (err) {
    toast(err.message);
    return;
  }
  state.shows = null;
  state.notice = "";
  await boot();
}

onSignedOut((reason) => {
  if (!state.session || !state.session.authenticated) return;
  state.session.authenticated = false;
  state.shows = null;
  state.notice = reason === "expired" ? "Your session expired on the cluster. Sign in again to continue; nothing you had not confirmed was booked." : "";
  // pick up the rotated CSRF token, then show the sign-in page (the hash keeps the destination)
  boot();
});

function route() {
  const seq = ++mountSeq;
  if (dispose) {
    try {
      dispose();
    } catch {
      /* a view's cleanup must never block navigation */
    }
    dispose = null;
  }
  const s = state.session;
  if (!s || !s.authenticated) {
    document.body.dataset.theme = "paper";
    document.title = "Sign in · The Night Programme";
    renderMasthead(null);
    auth.mount(replace(main), { ...ctx, onSignedIn: boot });
    return;
  }
  const path = location.hash.replace(/^#/, "") || "/programme";
  let found = null;
  let match = null;
  for (const r of ROUTES) {
    match = r.rx.exec(path);
    if (match) {
      found = r;
      break;
    }
  }
  if (!found) {
    ctx.navigate("/programme");
    return;
  }
  document.body.dataset.theme = found.theme || "paper";
  document.title = `${found.title} · The Night Programme`;
  renderMasthead(found.nav);
  replace(main);
  window.scrollTo(0, 0);
  Promise.resolve(found.view.mount(main, { ...ctx, params: match.slice(1).map(decodeURIComponent), alive: () => seq === mountSeq }))
    .then((d) => {
      if (seq === mountSeq) dispose = d || null;
      else if (d) d();
    })
    .catch((err) => {
      if (seq !== mountSeq || (err instanceof ApiError && err.http === 401)) return;
      replace(main, h("div", { class: "loading-block" }, errorNote(err instanceof ApiError ? err : new ApiError("http", "This page failed to render. See the browser console."), route)));
      if (!(err instanceof ApiError)) console.error(err);
    });
}

async function boot() {
  replace(main, loadingBlock("Contacting the local gateway"));
  try {
    state.session = await api("/api/session");
    setCsrf(state.session.csrf);
  } catch (err) {
    renderMasthead(null);
    replace(main, h("div", { class: "loading-block" }, errorNote(err, boot)));
    return;
  }
  renderColophon();
  route();
}

window.addEventListener("hashchange", route);
boot();
