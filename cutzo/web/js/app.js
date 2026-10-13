// Cutzo's browser screens: routing, the running-jobs dock, and live updates
// (so changes Claude makes show up here within a couple of seconds).

import { api } from "./api.js";
import { h, clear, toast, toastError } from "./ui.js";
import * as episodes from "./views/episodes.js";
import * as episode from "./views/episode.js";
import * as shows from "./views/shows.js";
import * as kit from "./views/kit.js";
import * as activity from "./views/activity.js";
import * as settings from "./views/settings.js";

const main = document.getElementById("view");
const state = {
  view: null,
  route: null,
  lastActivity: null,
  activeJobs: [],
  online: null,
  refreshTimer: null,
  closed: false,
};

const routes = [
  { pattern: /^#?\/?$/, nav: "episodes", module: episodes },
  { pattern: /^#\/episode\/([^/]+)(?:\/([a-z]+))?$/, nav: "episodes", module: episode, params: (m) => ({ id: decodeURIComponent(m[1]), tab: m[2] || null }) },
  { pattern: /^#\/shows$/, nav: "shows", module: shows },
  { pattern: /^#\/kit$/, nav: "kit", module: kit },
  { pattern: /^#\/activity$/, nav: "activity", module: activity },
  { pattern: /^#\/settings$/, nav: "settings", module: settings },
];

export function go(hash) {
  if (location.hash === hash) render();
  else location.hash = hash;
}

const ctx = {
  go,
  // Called by a screen after it changes something, so the running-jobs dock updates quickly.
  // (The screen refreshes itself; the poll only refreshes for changes made elsewhere.)
  changed: () => poll(),
  get activeJobs() {
    return state.activeJobs;
  },
  closed: () => {
    state.closed = true;
  },
};

async function render() {
  if (state.closed) return;
  const hash = location.hash || "#/";
  let match = null;
  let route = null;
  for (const r of routes) {
    match = hash.match(r.pattern);
    if (match) {
      route = r;
      break;
    }
  }
  if (state.view && state.view.destroy) state.view.destroy();
  state.view = null;
  clear(main);
  document.querySelectorAll("[data-nav]").forEach((a) => {
    if (route && a.dataset.nav === route.nav) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  if (!route) {
    main.append(h("div", { class: "empty" }, h("p", {}, "That page doesn't exist."), h("a", { class: "btn", href: "#/" }, "Go to episodes")));
    return;
  }
  state.route = route;
  const params = route.params ? route.params(match) : {};
  const token = Symbol("render");
  state.renderToken = token;
  // Build the page off-screen; if another page was opened meanwhile, throw this one away.
  const page = h("div", { class: "page" });
  try {
    const view = await route.module.render(page, params, ctx);
    if (state.renderToken !== token) {
      if (view && view.destroy) view.destroy();
      return;
    }
    clear(main);
    main.append(page);
    state.view = view || null;
  } catch (err) {
    if (state.renderToken !== token) return;
    clear(main);
    main.append(h("div", { class: "empty" },
      h("h1", { class: "page-title" }, "Couldn't load this page"),
      h("p", { class: "lede" }, err.message || String(err)),
      h("a", { class: "btn", href: "#/" }, "Go to episodes")));
  }
  if (route.nav === "activity") document.getElementById("activity-dot").hidden = true;
}

// ------------------------------------------------------------ jobs dock
function renderJobs(jobs) {
  const root = document.getElementById("jobs");
  clear(root);
  if (!jobs.length) {
    root.append(h("p", { class: "dock-empty" }, "Nothing running."));
    return;
  }
  for (const job of jobs) {
    const pct = Math.round((job.progress || 0) * 100);
    root.append(h("div", { class: "job" },
      h("div", { class: "job-title", title: job.title }, job.title),
      h("div", { class: "job-step" }, h("span", {}, job.step || "Waiting"), h("span", { class: "num" }, `${pct}%`)),
      h("div", { class: "bar", role: "progressbar", "aria-valuenow": String(pct), "aria-valuemin": "0", "aria-valuemax": "100", "aria-label": job.title },
        h("span", { style: { width: `${pct}%` } })),
      h("button", {
        class: "link",
        onclick: async () => {
          try {
            await api(`/api/jobs/${encodeURIComponent(job.id)}/cancel`, { method: "POST" });
            toast(`Stopping: ${job.title}`);
          } catch (err) {
            toastError(err);
          }
        },
      }, "Stop")));
  }
}

function setOnline(online, info) {
  const foot = document.getElementById("engine-status");
  if (online) {
    foot.textContent = info ? `Cutzo ${info.version}, Phase ${info.phase}. Library: ${info.library}` : "Connected.";
    foot.classList.remove("down");
  } else {
    foot.textContent = "Can't reach Cutzo's engine. Open Cutzo from the desktop shortcut.";
    foot.classList.add("down");
  }
  if (state.online === true && !online) toast("Lost contact with Cutzo's engine. If you closed it, open Cutzo again from the desktop shortcut.", { kind: "error", timeout: 10000 });
  state.online = online;
}

// ---------------------------------------------------------- live updates
async function poll(force = false) {
  if (state.closed) return;
  let s;
  try {
    s = await api("/api/state");
  } catch {
    setOnline(false);
    return;
  }
  if (state.online !== true) {
    try {
      setOnline(true, await api("/api/health"));
    } catch {
      setOnline(true);
    }
  }
  const jobsChanged = JSON.stringify(s.active_jobs.map((j) => [j.id, j.status])) !== JSON.stringify(state.activeJobs.map((j) => [j.id, j.status]));
  state.activeJobs = s.active_jobs;
  renderJobs(s.active_jobs);
  if (state.lastActivity === null) {
    state.lastActivity = s.activity_last_id;
    return;
  }
  let refresh = jobsChanged || force;
  if (s.activity_last_id !== state.lastActivity) {
    const since = state.lastActivity;
    state.lastActivity = s.activity_last_id;
    try {
      const news = await api(`/api/activity?since=${since}&reads=false&limit=50`);
      announce(news.entries.slice().reverse());
      // Redraw only for real changes made elsewhere (Claude, finished jobs). The screen
      // already redrew after your own changes, and refusals change nothing.
      if (news.entries.some((e) => e.ok && e.source !== "you")) refresh = true;
    } catch {
      /* the next poll will catch up */
    }
  }
  if (refresh) scheduleRefresh();
  else if (s.active_jobs.length && state.view && state.view.tick) state.view.tick(s.active_jobs);
}

function announce(entries) {
  for (const e of entries) {
    if (e.source === "you") continue;
    if (e.source === "claude") {
      toast(e.summary, { who: "Claude", kind: "claude" });
      if (state.route && state.route.nav !== "activity") document.getElementById("activity-dot").hidden = false;
    } else if (e.ok) {
      toast(e.summary, { who: "Cutzo", kind: "ok" });
    } else {
      toast(e.summary, { who: "Cutzo", kind: "error", timeout: 9000 });
    }
  }
}

function scheduleRefresh() {
  clearTimeout(state.refreshTimer);
  state.refreshTimer = setTimeout(() => {
    if (state.view && state.view.refresh) state.view.refresh().catch(() => {});
  }, 150);
}

// --------------------------------------------------------------- startup
window.addEventListener("hashchange", render);
render();
poll();
setInterval(() => {
  if (!document.hidden) poll();
}, 1500);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) poll();
});

