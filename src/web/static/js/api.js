// Browser -> gateway boundary. The gRPC session token never reaches this file: the gateway keeps
// it server-side behind an HttpOnly cookie. Only the CSRF token is held here, in memory.

let csrf = "";
const listeners = new Set();

export class ApiError extends Error {
  constructor(kind, message, http = 0, data = null) {
    super(message);
    this.kind = kind; // "network" | "timeout" | "http"
    this.http = http;
    this.data = data;
  }
}

export function setCsrf(token) {
  if (token) csrf = token;
}

export function onSignedOut(fn) {
  listeners.add(fn);
}

async function raw(path, { method = "GET", body, timeout = 20000 } = {}) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeout);
  const headers = { Accept: "application/json" };
  if (method !== "GET") {
    headers["Content-Type"] = "application/json";
    headers["X-CSRF-Token"] = csrf;
  }
  let res;
  try {
    res = await fetch(path, {
      method,
      headers,
      body: method === "GET" ? undefined : JSON.stringify(body || {}),
      credentials: "same-origin",
      cache: "no-store",
      signal: ctl.signal,
    });
  } catch (err) {
    if (err.name === "AbortError") {
      throw new ApiError("timeout", `The gateway did not answer within ${Math.round(timeout / 1000)} s.`);
    }
    throw new ApiError("network", "The local gateway is not reachable. Is web.gateway still running?");
  } finally {
    clearTimeout(timer);
  }
  let data = null;
  try {
    data = await res.json();
  } catch {
    data = null;
  }
  return { res, data };
}

export async function api(path, opts = {}) {
  let { res, data } = await raw(path, opts);
  if (res.status === 403 && data && data.status === "CSRF_REJECTED" && !opts._retried) {
    // the session was rotated in another tab: pick up the new CSRF token once, then repeat
    const fresh = await raw("/api/session");
    if (fresh.data && fresh.data.csrf) {
      setCsrf(fresh.data.csrf);
      ({ res, data } = await raw(path, { ...opts, _retried: true }));
    }
  }
  if (data && data.csrf) setCsrf(data.csrf);
  if (res.status === 401) {
    const reason = data && data.status === "SESSION_EXPIRED" ? "expired" : "required";
    for (const fn of listeners) fn(reason);
    throw new ApiError("http", (data && data.message) || "Sign in to continue.", 401, data);
  }
  if (!res.ok) {
    throw new ApiError("http", (data && data.message) || `The gateway answered ${res.status}.`, res.status, data);
  }
  return data;
}

export const post = (path, body, opts = {}) => api(path, { ...opts, method: "POST", body });
