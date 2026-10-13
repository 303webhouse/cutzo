// Show kit: every background, intro, sound and piece of art, where it lives, and what uses it.

import { api } from "../api.js";
import { h, add, clear } from "../ui.js";

const FILTERS = [
  { key: "all", label: "Everything" },
  { key: "the-dig", label: "The Dig" },
  { key: "the-new-heretics", label: "The New Heretics" },
  { key: "*", label: "Built into Cutzo" },
];

export async function render(main) {
  const data = await api("/api/assets");
  let filter = "all";
  add(main, h("header", { class: "page-head" },
    h("div", {},
      h("h1", { class: "page-title" }, "Show kit"),
      h("p", { class: "lede" }, data.note))));
  const bar = h("div", { class: "filters", role: "group", "aria-label": "Filter by show" });
  const body = h("div", {});
  add(main, [bar, body]);

  function draw() {
    clear(bar);
    for (const f of FILTERS) {
      bar.append(h("button", { "aria-pressed": f.key === filter ? "true" : "false", onclick: () => { filter = f.key; draw(); } }, f.label));
    }
    clear(body);
    const items = data.assets.filter((a) => filter === "all" || a.show === filter);
    const folders = new Map();
    for (const a of items) {
      if (!folders.has(a.folder)) folders.set(a.folder, []);
      folders.get(a.folder).push(a);
    }
    for (const [folder, assets] of folders) {
      body.append(h("div", { class: "folder-head" }, folder === "Built into Cutzo" ? folder : `Google Drive: ${folder.replaceAll("/", " / ")}`));
      body.append(h("div", { class: "table-wrap" }, h("table", { class: "grid kit-table" },
        h("colgroup", {}, h("col", { class: "c-file" }), h("col", { class: "c-use" }), h("col", { class: "c-by" }), h("col", { class: "c-link" })),
        h("thead", {}, h("tr", {}, h("th", {}, "File"), h("th", {}, "Used for"), h("th", {}, "Used by"), h("th", {}, ""))),
        h("tbody", {}, assets.map((a) => h("tr", {},
          h("td", { class: "name" }, a.name, a.note ? h("span", { class: "sub" }, a.note) : null, a.size ? h("span", { class: "sub" }, a.size) : null),
          h("td", {}, a.use),
          h("td", {}, a.used_by && a.used_by.length ? a.used_by.join("; ") : h("span", { class: "muted" }, "Not in a template")),
          h("td", {}, a.drive_url ? h("a", { href: a.drive_url, target: "_blank", rel: "noopener" }, "Open in Drive") : null)))))));
    }
  }
  draw();
}
