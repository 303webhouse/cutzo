// Small helpers shared by every screen: building elements, formatting times,
// toasts and dialogs.

export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value == null || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "style" && typeof value === "object") {
      for (const [prop, v] of Object.entries(value)) {
        if (prop.startsWith("--")) el.style.setProperty(prop, v);
        else el.style[prop] = v;
      }
    }
    else if (key === "dataset") Object.assign(el.dataset, value);
    else if (key.startsWith("on") && typeof value === "function") el.addEventListener(key.slice(2).toLowerCase(), value);
    else if (key === "svg") el.innerHTML = value; // trusted icon markup only
    else if (key === "value" || key === "checked" || key === "disabled" || key === "selected") el[key] = value;
    else el.setAttribute(key, value === true ? "" : value);
  }
  add(el, children);
  return el;
}

export function add(el, children) {
  for (const child of [children].flat(Infinity)) {
    if (child == null || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

export function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
  return el;
}

export const icons = {
  play: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M6 3.5v13l11-6.5z" fill="currentColor"/></svg>',
  pause: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M5 3.5h3.6v13H5zM11.4 3.5H15v13h-3.6z" fill="currentColor"/></svg>',
  check: '<svg viewBox="0 0 12 12" aria-hidden="true"><path d="M2.2 6.3l2.4 2.4 5.2-5.4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  undo: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M7.5 5.5L3.5 9.5l4 4M4 9.5h7.5a5 5 0 010 10H9" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" transform="translate(0 -2)"/></svg>',
  plus: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M10 4v12M4 10h12" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
};

export function fmtTime(seconds, precise = false) {
  let s = Math.max(0, Number(seconds) || 0);
  let tenths = null;
  if (precise) {
    const t = Math.round(s * 10);
    s = Math.floor(t / 10);
    tenths = t % 10;
  } else {
    s = Math.floor(s);
  }
  const hrs = Math.floor(s / 3600);
  const mins = Math.floor((s % 3600) / 60);
  const secs = s % 60;
  let out = hrs ? `${hrs}:${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}` : `${mins}:${String(secs).padStart(2, "0")}`;
  if (tenths !== null) out += `.${tenths}`;
  return out;
}

export function fmtDur(seconds) {
  const s = Math.max(0, Math.round(Number(seconds) || 0));
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  const r = s % 60;
  if (m < 60) return r ? `${m} min ${r} s` : `${m} min`;
  const hh = Math.floor(m / 60);
  const mm = m % 60;
  return mm ? `${hh} h ${mm} min` : `${hh} h`;
}

export function fmtSize(gb) {
  if (gb == null) return "";
  if (gb < 0.001) return "under 1 MB";
  if (gb < 1) return `about ${Math.max(1, Math.round(gb * 1000))} MB`;
  return `about ${gb.toFixed(1)} GB`;
}

export function relTime(iso) {
  if (!iso) return "";
  const then = new Date(iso);
  const secs = (Date.now() - then.getTime()) / 1000;
  if (secs < 45) return "just now";
  if (secs < 3600) return `${Math.round(secs / 60)} min ago`;
  const sameDay = new Date().toDateString() === then.toDateString();
  if (sameDay) return then.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  return then.toLocaleDateString([], { month: "short", day: "numeric" }) + ", " +
    then.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

export function plural(n, one, many = `${one}s`) {
  return `${n} ${n === 1 ? one : many}`;
}

// ---------------------------------------------------------------- toasts
export function toast(text, { who = null, kind = "", timeout = 5500 } = {}) {
  const root = document.getElementById("toasts");
  if (!root) return;
  const el = h("div", { class: `toast ${kind}`, role: "status", title: "Click to dismiss", onclick: () => el.remove() },
    who ? h("small", {}, who) : null, h("span", {}, text));
  root.append(el);
  while (root.children.length > 4) root.firstChild.remove();
  setTimeout(() => el.remove(), timeout);
}

export function toastError(err) {
  const msg = err?.message || String(err);
  toast(err?.hint ? `${msg} ${err.hint}` : msg, { kind: "error", timeout: 9000 });
}

// --------------------------------------------------------------- dialogs
export function openModal(build) {
  const root = document.getElementById("modal-root");
  const previous = document.activeElement;
  const close = () => {
    backdrop.remove();
    document.removeEventListener("keydown", onKey);
    if (previous && previous.focus) previous.focus();
  };
  const onKey = (e) => {
    if (e.key === "Escape") close();
  };
  const dialog = h("div", { class: "modal", role: "dialog", "aria-modal": "true" });
  const backdrop = h("div", { class: "modal-backdrop", onmousedown: (e) => { if (e.target === backdrop) close(); } }, dialog);
  build(dialog, close);
  root.append(backdrop);
  document.addEventListener("keydown", onKey);
  const first = dialog.querySelector("input, textarea, select, button");
  if (first) first.focus();
  return close;
}

export function confirmDialog({ title, text, confirm = "Continue", danger = false }) {
  return new Promise((resolve) => {
    let answered = false;
    openModal((dialog, close) => {
      const done = (value) => {
        answered = true;
        close();
        resolve(value);
      };
      dialog.setAttribute("aria-labelledby", "confirm-title");
      add(dialog, [
        h("h2", { id: "confirm-title" }, title),
        h("p", { class: "muted" }, text),
        h("div", { class: "modal-actions" },
          h("button", { class: "btn", onclick: () => done(false) }, "Cancel"),
          h("button", { class: danger ? "btn btn-cut" : "btn btn-primary", onclick: () => done(true) }, confirm)),
      ]);
      const observer = new MutationObserver(() => {
        if (!dialog.isConnected) {
          observer.disconnect();
          if (!answered) resolve(false);
        }
      });
      observer.observe(document.getElementById("modal-root"), { childList: true });
    });
  });
}

export function showColor(showId) {
  return showId === "the-dig" ? "var(--dig)" : showId === "the-new-heretics" ? "var(--tnh)" : "var(--ink)";
}
