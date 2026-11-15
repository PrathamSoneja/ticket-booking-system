import { post, ApiError } from "../api.js";
import { h, replace } from "../dom.js";
import { plate } from "../art.js";
import { FAILURE, note, pending } from "../ui.js";

export function mount(root, ctx) {
  let mode = "login";
  let busy = false;
  const status = h("div", { "aria-live": "polite" });
  if (ctx.state.notice) replace(status, note("warn", "Signed out", ctx.state.notice));

  const user = h("input", { id: "f-user", name: "username", autocomplete: "username", autocapitalize: "none", spellcheck: "false", required: true, maxlength: 64 });
  const pass = h("input", { id: "f-pass", name: "password", type: "password", autocomplete: "current-password", required: true, maxlength: 256 });
  const userErr = h("p", { class: "err", id: "f-user-err" });
  const passErr = h("p", { class: "err", id: "f-pass-err" });
  const userHint = h("p", { class: "hint" });
  const passHint = h("p", { class: "hint" });
  const userField = h("div", { class: "field" }, h("label", { for: "f-user" }, "Username"), user, userHint, userErr);
  const passField = h("div", { class: "field" }, h("label", { for: "f-pass" }, "Password"), pass, passHint, passErr);
  const submit = h("button", { class: "btn btn--primary btn--block", type: "submit" }, "Sign in");
  const tabIn = h("button", { type: "button", role: "tab", onclick: () => setMode("login") }, "Sign in");
  const tabUp = h("button", { type: "button", role: "tab", onclick: () => setMode("signup") }, "Create account");

  function setMode(next) {
    mode = next;
    tabIn.setAttribute("aria-selected", String(mode === "login"));
    tabUp.setAttribute("aria-selected", String(mode === "signup"));
    submit.textContent = mode === "login" ? "Sign in" : "Create account";
    pass.autocomplete = mode === "login" ? "current-password" : "new-password";
    // the rules are the server's (ticket_booking/auth.py); it stays the judge, these are hints
    userHint.textContent = mode === "signup" ? "3 to 20 characters: letters, numbers, underscore." : "";
    passHint.textContent = mode === "signup" ? "At least 8 characters, with letters and numbers." : "";
    invalid(userField, userErr, "");
    invalid(passField, passErr, "");
  }

  function invalid(field, slot, message) {
    slot.textContent = message;
    if (message) field.dataset.invalid = "";
    else delete field.dataset.invalid;
    field.querySelector("input").setAttribute("aria-invalid", message ? "true" : "false");
  }

  async function onSubmit(ev) {
    ev.preventDefault();
    if (busy) return;
    invalid(userField, userErr, user.value.trim() ? "" : "Enter your username.");
    invalid(passField, passErr, pass.value ? "" : "Enter your password.");
    if (!user.value.trim()) return user.focus();
    if (!pass.value) return pass.focus();
    busy = true;
    submit.disabled = true;
    const p = pending(mode === "login" ? "Asking the cluster to open a session…" : "Registering through the Raft leader…", { timer: true });
    replace(status, p.el);
    try {
      const out = await post(`/api/${mode}`, { username: user.value.trim(), password: pass.value }, { timeout: 30000 });
      if (out.status === "OK") {
        ctx.state.notice = "";
        pass.value = "";
        await ctx.onSignedIn();
        return;
      }
      pass.value = "";
      if (out.status === "AUTH_FAILED") {
        invalid(passField, passErr, out.message);
        replace(status);
        pass.focus();
      } else if (out.status === "WEAK_PASSWORD") {
        invalid(passField, passErr, out.message);
        replace(status);
        pass.focus();
      } else if (out.status === "USERNAME_TAKEN" || out.status === "INVALID_REQUEST") {
        invalid(userField, userErr, out.message);
        replace(status);
        user.focus();
      } else {
        const extra = out.uncertain ? " The account may or may not have been created: try signing in with it before registering again." : "";
        replace(status, note(out.status === "UNAVAILABLE" ? "offline" : "error", out.status.replaceAll("_", " "), (FAILURE[out.status] || out.message) + extra));
      }
    } catch (err) {
      if (err instanceof ApiError) replace(status, note(err.kind === "http" ? "error" : "offline", err.kind === "http" ? "Request refused" : "Gateway not reachable", err.message));
      else throw err;
    } finally {
      p.stop();
      busy = false;
      submit.disabled = false;
    }
  }

  const demos = (ctx.state.session.demo_accounts || []).map((acct) =>
    h(
      "div",
      { class: "demo__row" },
      h("span", { class: "mono" }, `${acct.username} / ${acct.password}`),
      h(
        "button",
        {
          class: "btn btn--small",
          type: "button",
          onclick: () => {
            setMode("login");
            user.value = acct.username;
            pass.value = acct.password;
            submit.focus();
          },
        },
        "Fill in",
      ),
    ),
  );

  root.append(
    h(
      "div",
      { class: "auth" },
      h(
        "section",
        { class: "auth__poster enter", "aria-hidden": "true" },
        h("p", { class: "display" }, h("span", null, "The"), h("span", null, "Night"), h("span", null, "Programme.")),
        h("div", { class: "auth__art" }, plate("night-programme-frontispiece")),
        h("p", { class: "lede" }, "A booking desk for a replicated box office: every seat you take is committed by a majority of three Raft nodes before it is yours."),
      ),
      h(
        "section",
        { class: "auth__panel enter", style: { "--i": "2" }, "aria-labelledby": "auth-h" },
        h("h1", { class: "label", id: "auth-h" }, "Box office · Sign in to book"),
        h("div", { class: "seg", role: "tablist", "aria-label": "Sign in or create an account" }, tabIn, tabUp),
        h("form", { novalidate: true, onsubmit: onSubmit }, userField, passField, submit, status),
        demos.length
          ? h(
              "aside",
              { class: "demo", "aria-label": "Repository demo accounts" },
              h("p", { class: "label" }, "Repository demo accounts"),
              h("p", null, "Seeded test users from this project's README (ticket_booking/auth.py). They are sample credentials for the local demo, not real accounts."),
              demos,
            )
          : null,
      ),
    ),
  );
  setMode("login");
  user.focus({ preventScroll: true });
}
