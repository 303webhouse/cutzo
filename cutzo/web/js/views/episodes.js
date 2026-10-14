// Episodes: every episode by show, with where it is and the one thing to do next.

import { api, epPath } from "../api.js";
import { h, add, clear, fmtDur, plural, openModal, toastError, showColor, relTime } from "../ui.js";

export function nextAction(ep) {
  const busy = ep.busy || [];
  const building = busy.find((j) => j.kind === "build");
  const exporting = busy.find((j) => j.kind === "export");
  const importing = busy.find((j) => j.kind === "import");
  if (importing) return { label: "Importing\u2026", tab: "recordings", busy: true };
  if (building) return { label: "Building\u2026", tab: "recordings", busy: true };
  if (exporting) return { label: "Exporting\u2026", tab: "export", busy: true };
  if (!ep.media_count) return { label: "Add recordings", tab: "recordings" };
  if (ep.needs_rebuild) return { label: "Build again", tab: "recordings" };
  const built = ep.stages.find((s) => s.key === "auto_edit").status === "done";
  if (!built) return { label: "Build episode", tab: "recordings" };
  if (ep.stage === "done" || ep.export_count) return { label: "See exports", tab: "export", quiet: true };
  return { label: "Review cuts", tab: "review", primary: true };
}

export function chain(stages) {
  return h("span", { class: "chain", role: "img", "aria-label": stages.map((s) => `${s.label}: ${s.status}`).join(", ") },
    stages.map((s) => h("i", { class: s.status, title: `${s.label}: ${statusWord(s.status)}` })));
}

export function statusWord(status) {
  return { done: "done", running: "running", ready: "ready", todo: "to do", later: "comes in Phase 3" }[status] || status;
}

export async function render(main, params, ctx) {
  const [episodes, shows] = await Promise.all([api("/api/episodes"), api("/api/shows")]);
  const head = h("header", { class: "page-head" },
    h("div", {},
      h("h1", { class: "page-title" }, "Episodes"),
      h("p", { class: "lede" }, "Each episode moves from raw recordings to finished files. The button on the right is the next thing to do.")),
    h("button", { class: "btn btn-primary", onclick: () => newEpisodeDialog(shows, ctx) }, "New episode"));
  const body = h("div", {});
  add(main, [head, body]);

  function draw(list) {
    clear(body);
    for (const show of shows) {
      const mine = list.filter((e) => e.show === show.id);
      const group = h("section", { class: "show-group", style: { "--show": showColor(show.id) } },
        h("div", { class: "show-head" },
          h("span", { class: "swatch", "aria-hidden": "true" }),
          h("h2", {}, show.name),
          h("span", { class: "muted small" }, show.format === "video" ? "Video podcast" : "Audio podcast")));
      if (!mine.length) {
        group.append(h("p", { class: "empty" }, "No episodes yet. Use New episode to start one."));
      } else {
        group.append(h("ul", { class: "ep-list" }, mine.map((ep) => row(ep, ctx))));
      }
      body.append(group);
    }
  }
  draw(episodes);

  return {
    refresh: async () => draw(await api("/api/episodes")),
  };
}

function row(ep, ctx) {
  const next = nextAction(ep);
  const link = `#/episode/${encodeURIComponent(ep.id)}`;
  const lengths = ep.duration
    ? `${fmtDur(ep.duration)} recording, ${fmtDur(ep.final_duration)} after cuts`
    : "Not built yet";
  return h("li", { class: "ep-row", style: { "--show": showColor(ep.show) } },
    h("span", { class: "ep-edge", "aria-hidden": "true" }),
    h("div", {},
      h("a", { class: "ep-title", href: link }, ep.title),
      h("div", { class: "ep-meta" },
        ep.sample ? h("span", { class: "tag tag-sample" }, "Sample") : null,
        ep.test_episode ? h("span", { class: "tag tag-test", title: "This is one of the two episodes Cutzo rebuilds and compares with your finished version." }, "Test episode") : null,
        h("span", {}, lengths),
        ep.duration && (ep.cut_count > (ep.pause_count || 0) || !ep.pause_count)
          ? h("span", {}, plural(ep.cut_count - (ep.pause_count || 0), "cut")) : null,
        ep.pause_count ? h("span", {}, `${plural(ep.pause_count, "pause")} shortened`) : null,
        ep.duration ? h("span", {}, plural(ep.chapter_count, "chapter")) : null,
        h("span", {}, `Changed ${relTime(ep.updated)}`))),
    chain(ep.stages),
    h("a", {
      class: `btn ep-action ${next.primary ? "btn-primary" : ""}`,
      href: `${link}/${next.tab}`,
      "aria-disabled": next.busy ? "true" : null,
    }, next.label));
}

// -------------------------------------------------------- new episode
function newEpisodeDialog(shows, ctx) {
  openModal((dialog, close) => {
    let found = [];
    const error = h("p", { class: "form-error", hidden: true });
    const title = h("input", { type: "text", id: "ne-title", placeholder: "For example: Episode 12, The Haunted Library", autocomplete: "off" });
    const folder = h("input", { type: "text", id: "ne-folder", placeholder: "F:\\Recordings\\Episode 12", autocomplete: "off" });
    const foundBox = h("div", {});
    const choices = shows.map((s, i) =>
      h("label", { class: "choice" },
        h("input", { type: "radio", name: "ne-show", value: s.id, checked: i === 0 }),
        h("span", {}, h("b", {}, s.name), h("span", { class: "muted small" }, s.format === "video" ? "Video podcast" : "Audio podcast"))));
    const pickedShow = () => dialog.querySelector('input[name="ne-show"]:checked')?.value;

    async function look() {
      error.hidden = true;
      clear(foundBox);
      if (!folder.value.trim()) return;
      try {
        const res = await api(`/api/scan?folder=${encodeURIComponent(folder.value.trim())}`);
        found = res.files;
        if (res.show_guess) {
          const radio = dialog.querySelector(`input[name="ne-show"][value="${res.show_guess}"]`);
          if (radio) radio.checked = true;
        }
        if (!title.value.trim()) title.value = res.folder.split(/[\\/]/).filter(Boolean).pop() || "";
        add(foundBox, [
          h("p", { class: "small muted" }, found.length ? `Found ${plural(found.length, "recording")}. They'll be imported when you create the episode.` : "No recordings in that folder yet."),
          found.length ? h("ul", { class: "found-list" }, found.map((f) => h("li", {}, h("span", {}, f.name), h("span", { class: "muted" }, f.person || f.source_label)))) : null,
        ]);
      } catch (err) {
        error.textContent = err.message;
        error.hidden = false;
      }
    }

    async function create(e) {
      e.preventDefault();
      error.hidden = true;
      try {
        const res = await api("/api/episodes", {
          method: "POST",
          body: { show: pickedShow(), title: title.value.trim() || null, folder: folder.value.trim() || null },
        });
        close();
        ctx.changed();
        ctx.go(`#/episode/${encodeURIComponent(res.episode.id)}/recordings`);
      } catch (err) {
        error.textContent = err.message;
        error.hidden = false;
      }
    }

    dialog.setAttribute("aria-labelledby", "ne-heading");
    add(dialog, h("form", { onsubmit: create },
      h("h2", { id: "ne-heading" }, "New episode"),
      h("p", { class: "muted" }, "Pick the show, name the episode, and point Cutzo at the folder with the raw recordings. Cutzo only reads them."),
      error,
      h("fieldset", { style: { border: 0, padding: 0, margin: "0 0 16px" } },
        h("legend", { class: "field", style: { marginBottom: "6px" } }, "Show"),
        h("div", { class: "choice-row" }, choices)),
      h("label", { class: "field", for: "ne-title" }, "Title", title),
      h("label", { class: "field", for: "ne-folder" }, "Recordings folder",
        h("span", { class: "hint" }, "Optional. In File Explorer, right-click the folder and choose Copy as path, then paste it here."),
        h("div", { class: "row" }, folder, h("button", { type: "button", class: "btn", onclick: look }, "Look in folder"))),
      foundBox,
      h("div", { class: "modal-actions" },
        h("button", { type: "button", class: "btn", onclick: close }, "Cancel"),
        h("button", { type: "submit", class: "btn btn-primary" }, "Create episode"))));
  });
}

export { toastError };
