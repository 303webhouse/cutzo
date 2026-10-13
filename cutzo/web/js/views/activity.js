// Activity: every change, who made it (you, Claude, or Cutzo itself) and when.

import { api } from "../api.js";
import { h, add, clear, relTime } from "../ui.js";

export async function render(main) {
  let reads = false;
  const list = h("ol", { class: "log", reversed: true });
  const toggle = h("input", { type: "checkbox", onchange: (e) => { reads = e.target.checked; load(); } });
  add(main, [
    h("header", { class: "page-head" },
      h("div", {},
        h("h1", { class: "page-title" }, "Activity"),
        h("p", { class: "lede" }, "Every change to your episodes, newest first. Changes Claude makes from the Claude app are marked Claude.")),
      h("label", { class: "toggle-line" }, toggle, "Show Claude's look-ups too")),
    list,
  ]);

  async function load() {
    const data = await api(`/api/activity?limit=150&reads=${reads}`);
    clear(list);
    if (!data.entries.length) {
      list.append(h("li", { class: "empty" }, "Nothing yet."));
      return;
    }
    for (const e of data.entries) {
      const cls = [e.ok ? "" : "failed", e.action === "read" ? "read" : ""].join(" ").trim();
      list.append(h("li", { class: cls || null },
        h("time", { datetime: e.at, title: new Date(e.at).toLocaleString() }, relTime(e.at)),
        h("span", { class: `who who-${e.source}` }, e.who),
        h("div", {},
          h("span", { class: "log-text" }, e.summary),
          e.episode ? h("a", { class: "log-ep", href: `#/episode/${encodeURIComponent(e.episode)}` }, "Open episode") : null)));
    }
  }
  await load();
  return { refresh: load };
}
