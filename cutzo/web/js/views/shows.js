// Shows: what every episode of The Dig and The New Heretics gets by default.

import { api } from "../api.js";
import { h, add, plural, showColor } from "../ui.js";

export async function render(main) {
  const shows = await api("/api/shows");
  add(main, h("header", { class: "page-head" },
    h("div", {},
      h("h1", { class: "page-title" }, "Shows"),
      h("p", { class: "lede" }, "Each show has a template: the intro, background, sounds and exports every episode gets. You can change any part for a single episode on its Show kit tab."))));
  for (const show of shows) {
    add(main, h("section", { class: "panel show-panel", style: { "--show": showColor(show.id) } },
      h("div", { class: "row", style: { justifyContent: "space-between", alignItems: "flex-start" } },
        h("div", {},
          h("h2", { class: "page-title" }, show.name),
          h("p", { class: "lede" }, show.summary)),
        h("a", { class: "btn", href: "#/" }, `${plural(show.episode_count, "episode")}`)),
      h("dl", { class: "facts" },
        show.network ? [h("dt", {}, "Network"), h("dd", {}, show.network)] : null,
        h("dt", {}, "Format"), h("dd", {}, show.format === "video" ? "Video podcast" : "Audio podcast"),
        h("dt", {}, "Exports"), h("dd", {}, show.outputs.map((o) => o.label).join(", ")),
        h("dt", {}, "Voice cleanup"), h("dd", {}, `${show.voice.preset}, strength ${show.voice.strength} of 100`),
        h("dt", {}, "Loudness"), h("dd", {}, `${show.loudness.podcast} LUFS for podcast audio, ${show.loudness.video} LUFS for video`),
        h("dt", {}, "Next build step"), h("dd", {}, show.phase_note)),
      h("h3", { style: { marginTop: "22px" } }, "Template"),
      h("div", { class: "slot-grid" }, show.slots.map((s) => h("div", { class: "slot" },
        h("div", { class: "slot-label" }, s.label),
        h("div", { class: "slot-asset" }, s.asset.name),
        h("div", { class: "slot-note" }, s.note || ""),
        s.choices.length > 1 ? h("div", { class: "slot-note" }, `Other choices: ${s.choices.filter((c) => c.id !== s.asset.id).map((c) => c.name).join(", ")}`) : null))),
      show.segment_titles.length
        ? [h("h3", { style: { marginTop: "22px" } }, "Voiced segment titles"),
           h("p", { class: "muted" }, "A chapter with one of these names plays its voice clip at that point."),
           h("p", {}, show.segment_titles.map((a) => a.name.replace(/\.[a-z0-9]+$/i, "")).join(", "))]
        : null));
  }
}
