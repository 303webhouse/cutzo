// One episode: where it stands, the next step, and tabs for Review, Recordings,
// Show kit and Export.

import { api, epPath } from "../api.js";
import { h, add, clear, icons, fmtDur, fmtSize, plural, toast, toastError, confirmDialog, showColor, relTime } from "../ui.js";
import { nextAction, statusWord } from "./episodes.js";
import { mountReview } from "./review.js";

const TABS = [
  { key: "review", label: "Review" },
  { key: "recordings", label: "Recordings" },
  { key: "kit", label: "Show kit" },
  { key: "export", label: "Export" },
];

export async function render(main, params, ctx) {
  let ep = await api(epPath(params.id));
  const built = () => ep.stages.find((s) => s.key === "auto_edit").status === "done";
  let tab = params.tab || (built() ? "review" : "recordings");
  if (!TABS.some((t) => t.key === tab)) tab = "review";

  const headBox = h("div", {});
  const tabsBox = h("nav", { class: "tabs", "aria-label": "Episode sections" });
  const body = h("div", {});
  add(main, [headBox, tabsBox, body]);
  let review = null;
  let destroyed = false;
  // Things you are in the middle of typing or choosing survive live refreshes.
  const draft = { paths: "", picks: null, confirm: null };

  const view = {
    async refresh() {
      if (destroyed) return;
      try {
        ep = await api(epPath(ep.id));
      } catch (err) {
        if (err.status === 404) ctx.go("#/");
        return;
      }
      drawHead();
      if (tab === "review" && review) await review.refresh(ep);
      else drawTab();
    },
    async tick(jobs) {
      const mine = jobs.filter((j) => j.episode === ep.id);
      if (!mine.length || destroyed) return;
      main.querySelectorAll("[data-job-progress]").forEach((el) => {
        const job = mine.find((j) => j.kind === el.dataset.jobProgress);
        if (!job) return;
        const pct = Math.round(job.progress * 100);
        el.querySelector(".bar > span").style.width = `${pct}%`;
        el.querySelector("p").textContent = `${job.step || "Working"} (${pct}%)`;
      });
      // Steps tick off one by one during a build; redraw the header when they change.
      try {
        const fresh = await api(epPath(ep.id));
        if (!destroyed && JSON.stringify(fresh.stages) !== JSON.stringify(ep.stages)) {
          ep = fresh;
          drawHead();
        }
      } catch {
        /* the next poll tries again */
      }
    },
    destroy() {
      destroyed = true;
      if (review) review.destroy();
    },
  };

  async function act(fn, success) {
    try {
      const res = await fn();
      if (success) toast(typeof success === "function" ? success(res) : success, { kind: "ok" });
      ctx.changed();
      await view.refresh();
      return res;
    } catch (err) {
      toastError(err);
      return null;
    }
  }
  view.act = act;

  // ----------------------------------------------------------- header
  function drawHead() {
    clear(headBox);
    const next = nextAction(ep);
    const show = { id: ep.show, name: ep.show_name };
    headBox.style.setProperty("--show", showColor(ep.show));
    const undo = h("button", {
      class: "btn btn-quiet",
      disabled: !ep.can_undo,
      title: ep.last_change ? `Undo: ${ep.last_change}` : "Nothing to undo",
      svg: icons.undo,
      onclick: () => act(() => api(epPath(ep.id) + "/undo", { method: "POST" }), (r) => r.summary),
    });
    undo.append("Undo");
    add(headBox, [
      h("p", { class: "crumbs" },
        h("a", { href: "#/" }, "Episodes"), h("span", { "aria-hidden": "true" }, "/"),
        h("span", { class: "swatch", style: { "--show": showColor(show.id) }, "aria-hidden": "true" }),
        h("span", {}, show.name)),
      h("div", { class: "ep-head" },
        h("div", {},
          h("h1", { class: "page-title" }, ep.title),
          h("div", { class: "lengths" },
            ep.sample ? h("span", { class: "tag tag-sample" }, "Sample") : null,
            ep.test_episode ? h("span", { class: "tag tag-test" }, "Test episode") : null,
            ep.duration
              ? [h("span", {}, "Recording ", h("b", {}, fmtDur(ep.duration))),
                 h("span", {}, "After cuts ", h("b", {}, fmtDur(ep.final_duration))),
                 ep.finished_duration ? h("span", { title: "With the intro, breaks and ending" }, "Finished file ", h("b", {}, fmtDur(ep.finished_duration))) : null,
                 h("span", {}, [
                   ep.cut_count > (ep.pause_count || 0) || !ep.pause_count ? plural(ep.cut_count - (ep.pause_count || 0), "cut") : null,
                   ep.pause_count ? `${plural(ep.pause_count, "pause")} shortened` : null,
                   plural(ep.chapter_count, "chapter")].filter(Boolean).join(", "))]
              : h("span", {}, plural(ep.media_count, "recording"), ", not built yet"))),
        h("div", { class: "row" }, undo)),
      h("ol", { class: "stages", "aria-label": "Steps" },
        ep.stages.map((s) => h("li", { class: s.status },
          h("span", { class: "mark", svg: s.status === "done" ? icons.check : "" }),
          h("span", {}, s.label, s.status === "later" ? h("span", { class: "later-note" }, " (not yet)") : null),
          h("span", { class: "sr-only", style: { position: "absolute", left: "-9999px" } }, `: ${statusWord(s.status)}`)))),
      nextStep(next),
    ]);
  }

  function nextStep(next) {
    const job = (ep.busy || [])[0];
    if (job) {
      const live = ctx.activeJobs.find((j) => j.id === job.id);
      const pct = Math.round(((live && live.progress) || 0) * 100);
      return h("div", { class: "next-step", "data-job-progress": job.kind },
        h("div", { style: { flex: "1 1 320px" } },
          h("strong", {}, job.title),
          h("div", { class: "progress-line" },
            h("div", { class: "bar" }, h("span", { style: { width: `${pct}%` } })),
            h("p", {}, `${(live && live.step) || "Starting"} (${pct}%)`))));
    }
    const copy = {
      "Add recordings": ["Add the raw recordings", "Paste the paths of the files, or the folder they're in."],
      "Build episode": ["Build the episode", ep.sample
        ? "Cutzo lines up the recordings, cleans the voices, writes the transcript and suggests cuts."
        : "Cutzo lines up the recordings, cleans the voices, writes down every word and finds dead air. A 90-minute episode takes about 15 minutes."],
      "Build again": ["Build again", "New recordings or a new cleanup setting. Your own cuts, chapters and picks stay."],
      "Review cuts": ["Review the episode", ep.has_transcript
        ? "Read the transcript and select words to cut them, mark chapters and the cold open, then export."
        : ep.sample ? "Listen through, mark chapters and the cold open, check the dead-air cuts, then export."
          : "No transcript yet. Transcribe it (about 2 minutes) to cut by words, or listen through and cut by time."],
      "See exports": ["Exported", "The finished files and their checks are on the Export tab."],
    }[next.label] || [next.label, ""];
    let button;
    if (next.label === "Review cuts" && tab === "review") {
      button = h("a", { class: "btn btn-primary", href: `#/episode/${encodeURIComponent(ep.id)}/export` }, "Go to export");
    } else if (next.label === "Build episode" || next.label === "Build again") {
      button = h("button", { class: "btn btn-primary", onclick: build }, next.label);
    } else {
      button = h("a", { class: `btn ${next.quiet ? "" : "btn-primary"}`, href: `#/episode/${encodeURIComponent(ep.id)}/${next.tab}` }, next.label);
    }
    const extra = next.label === "Review cuts" && needsTranscript()
      ? h("button", { class: "btn btn-primary", onclick: transcribe }, "Transcribe") : null;
    if (extra) button.classList.remove("btn-primary");
    return h("div", { class: "next-step" }, h("p", {}, h("strong", {}, copy[0]), copy[1]), h("div", { class: "row" }, extra, button));
  }

  const needsTranscript = () => !ep.sample && built() && !ep.has_transcript;

  async function build() {
    await act(() => api(epPath(ep.id) + "/build", { method: "POST", body: {} }), "Building started. Progress shows on the left.");
  }

  async function transcribe() {
    await act(() => api(epPath(ep.id) + "/transcribe", { method: "POST", body: {} }), "Transcribing started. Progress shows on the left.");
  }

  async function removeEpisode() {
    const ok = await confirmDialog({
      title: "Remove this episode?",
      text: "Cutzo moves its project files to the trash folder in your Cutzo library. Your original recordings are never touched.",
      confirm: "Remove episode",
      danger: true,
    });
    if (!ok) return;
    try {
      await api(epPath(ep.id), { method: "DELETE" });
      toast(`Removed \u201c${ep.title}\u201d.`, { kind: "ok" });
      ctx.changed();
      ctx.go("#/");
    } catch (err) {
      toastError(err);
    }
  }

  // ------------------------------------------------------------- tabs
  function drawTabs() {
    clear(tabsBox);
    for (const t of TABS) {
      tabsBox.append(h("a", {
        href: `#/episode/${encodeURIComponent(ep.id)}/${t.key}`,
        "aria-selected": t.key === tab ? "true" : "false",
        role: "tab",
      }, t.label));
    }
  }

  function drawTab() {
    if (review) {
      review.destroy();
      review = null;
    }
    clear(body);
    if (tab === "review") {
      if (!built()) {
        body.append(h("div", { class: "panel" },
          h("h3", {}, "Nothing to review yet"),
          h("p", { class: "muted" }, ep.sample
            ? "Build the episode first. Cutzo then shows the transcript, the waveform and its suggested cuts here."
            : "Build the episode first. Cutzo then shows the waveform, the cleaned sound and its suggested cuts here."),
          h("a", { class: "btn btn-primary", href: `#/episode/${encodeURIComponent(ep.id)}/recordings` }, "Go to recordings")));
        return;
      }
      review = mountReview(body, ep, { ...ctx, act, reload: () => view.refresh() });
    } else if (tab === "recordings") drawRecordings();
    else if (tab === "kit") drawKit();
    else if (tab === "export") drawExport();
  }

  // ------------------------------------------------------- recordings
  function drawRecordings() {
    const paths = h("textarea", {
      id: "add-paths",
      placeholder: "F:\\Recordings\\Episode 12\\Nick.wav\nF:\\Recordings\\Episode 12\\Guest.wav",
      value: draft.paths,
      oninput: (e) => { draft.paths = e.target.value; },
    });
    const media = ep.media || [];
    add(body, [
      h("section", { class: "panel" },
        h("h3", {}, media.length ? plural(media.length, "recording") : "No recordings yet"),
        media.length
          ? h("div", { class: "table-wrap" }, h("table", { class: "grid" },
              h("thead", {}, h("tr", {}, h("th", {}, "File"), h("th", {}, "Who"), h("th", {}, "What it is"), h("th", {}, "Lined up"))),
              h("tbody", {}, media.map((m) => h("tr", {},
                h("td", { class: "name" }, m.name, h("span", { class: "sub" }, m.path),
                  m.probe ? h("span", { class: "sub" }, describeProbe(m.probe)) : null,
                  m.problem ? h("span", { class: "sub check-fail" }, m.problem) : null),
                h("td", {}, m.person || (m.source === "mix" || m.source === "streamyard-mix" ? "Everyone" : "\u2014")),
                h("td", {}, `${m.source_label}${m.kind ? ` (${m.kind})` : ""}`),
                h("td", {}, m.sync && m.sync.offset != null ? [h("b", { class: "num" }, `${m.sync.offset >= 0 ? "+" : ""}${m.sync.offset.toFixed(3)} s`), h("span", { class: "sub" }, m.sync.method)]
                  : m.sync ? h("span", { class: "muted" }, m.sync.method) : h("span", { class: "muted" }, "After building")))))))
          : h("p", { class: "muted" }, "Add the raw files below. Cutzo reads them and never changes them."),
        ep.sample ? h("p", { class: "small muted" }, "These sample paths are examples; Cutzo doesn't look for them on disk.") : null),
      h("div", { class: "two-col" },
        h("section", { class: "panel" },
          h("h3", {}, "Add recordings"),
          h("label", { class: "field", for: "add-paths" },
            h("span", { class: "hint" }, "One path per line: files, or the folder they're in. In File Explorer, right-click and choose Copy as path."),
            paths),
          h("button", {
            class: "btn",
            onclick: async () => {
              const list = paths.value.split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
              if (!list.length) return toast("Paste at least one path first.", { kind: "error" });
              const res = await act(() => api(epPath(ep.id) + "/import", { method: "POST", body: { paths: list } }),
                (r) => `Importing ${plural(r.files.length, "recording")}.`);
              if (res) {
                draft.paths = "";
                const box = document.getElementById("add-paths");
                if (box) box.value = "";
              }
            },
          }, "Add recordings")),
        h("section", { class: "panel" },
          h("h3", {}, built() ? "Build again" : "Build the episode"),
          h("p", { class: "muted" }, "Cutzo runs these steps, in order:"),
          ep.sample
            ? h("ol", { class: "steps-list" },
                h("li", {}, h("b", {}, "Sync: "), "lines up everyone's recordings."),
                h("li", {}, h("b", {}, "Podcast voice: "), "removes noise and room echo, evens out voices."),
                h("li", {}, h("b", {}, "Transcribe: "), "writes down every word with its time."),
                h("li", {}, h("b", {}, "Auto-edit: "), "suggests cuts for dead air and filler words, and chapters."))
            : h("ol", { class: "steps-list" },
                h("li", {}, h("b", {}, "Sync: "), "lines up everyone's recordings (StreamYard's file names, then by sound)."),
                h("li", {}, h("b", {}, "Podcast voice: "), "removes background noise, softens harsh S sounds, evens out voices."),
                h("li", {}, h("b", {}, "Transcribe: "), "writes down every word with its time, “um”s included, on your graphics card."),
                h("li", {}, h("b", {}, "Auto-edit: "), "finds long silences and suggests shortening them.")),
          built() ? h("p", { class: "small muted" }, "Building again replaces Cutzo's suggestions. Your own cuts and chapters, and Claude's, stay.") : null,
          ep.sample ? h("p", { class: "small muted" }, "Sample episode: these steps are pretend and use a short test clip.") : null,
          ep.voice_settings ? cleanupControl() : null,
          h("div", { class: "row" },
            h("button", { class: "btn btn-primary", disabled: !media.length || (ep.busy || []).length > 0, onclick: build }, built() ? "Build again" : "Build episode"),
            needsTranscript() ? h("button", { class: "btn", disabled: (ep.busy || []).length > 0, onclick: transcribe }, "Just transcribe") : null),
          needsTranscript() ? h("p", { class: "small muted" }, "Built before speech to text was installed? Just transcribe: about 2 minutes, nothing else changes.") : null)),
      h("p", { class: "small muted", style: { marginTop: "8px" } },
        "Done with this episode in Cutzo? ",
        h("button", { class: "btn btn-quiet btn-small", onclick: removeEpisode }, "Remove it from Cutzo"),
        " Your recordings stay where they are."),
    ]);
  }

  // ----------------------------------------------------------- kit
  function drawKit() {
    const panel = h("section", { class: "panel" },
      h("h3", {}, `${ep.show_name} template`),
      h("p", { class: "muted" }, "What this episode gets from the show kit. A change here applies to this episode only."));
    const grid = h("div", { class: "slot-grid" });
    for (const slot of ep.slots) {
      const select = h("select", {
        "aria-label": slot.label,
        onchange: (e) => act(() => api(epPath(ep.id) + "/assets", { method: "POST", body: { slot: slot.slot, asset: e.target.value } }), (r) => r.summary),
      }, slot.choices.map((c) => h("option", { value: c.id, selected: c.id === slot.asset.id }, c.id === slot.default ? `${c.name} (default)` : c.name)));
      grid.append(h("div", { class: "slot" },
        h("div", { class: "slot-label" }, slot.label),
        h("div", { class: "slot-asset" }, slot.asset.name, slot.overridden ? h("span", { class: "tag tag-claude", style: { marginLeft: "8px" } }, "Changed for this episode") : null),
        h("div", { class: "slot-note" }, slot.asset.folder ? `${slot.asset.folder}` : slot.note || ""),
        slot.choices.length > 1 ? select : null));
    }
    panel.append(grid);
    body.append(panel);
    const voiced = (ep.slots.length && ep.show === "the-new-heretics");
    if (voiced) {
      body.append(h("section", { class: "panel" },
        h("h3", {}, "Voiced segment titles"),
        h("p", { class: "muted" }, "Name a chapter (after the first) like one of these and its voice clip plays there instead of the page flip: The New Heretics, Define Your Terms, Why Should We Care, Building a Better Apostasy, Heretical Haiku, Logo Loses Us.")));
    }
  }

  // ---------------------------------------------------------- export
  function drawExport() {
    const presetsBox = h("div", {});
    const exportsBox = h("div", {});
    add(body, [presetsBox, exportsBox]);
    api("/api/presets").then((presets) => {
      if (!draft.picks) draft.picks = new Set(ep.outputs);
      const pick = draft.picks;
      for (const p of presets) if (p.kind === "video" && !ep.sample && (!ep.has_picture || !p.spec)) pick.delete(p.id);
      const main = presets.filter((p) => !p.reserve);
      const reserve = presets.filter((p) => p.reserve);
      const final = ep.final_duration || 0;
      // Why a target can't be made for this episode (real episodes only), or null.
      const blocked = (p) => {
        if (ep.sample || p.kind !== "video") return null;
        if (!ep.has_picture) return `${ep.show_name} is audio only.`;
        if (!p.spec) return "Cutzo makes video at 1080p for now.";
        return null;
      };
      const option = (p) => h("label", { class: "choice" },
        h("input", {
          type: "checkbox",
          value: p.id,
          checked: pick.has(p.id) && !blocked(p),
          disabled: !!blocked(p),
          onchange: (e) => { if (e.target.checked) pick.add(p.id); else pick.delete(p.id); draft.confirm = null; },
        }),
        h("span", {},
          h("b", {}, p.label),
          h("span", { class: "small muted" }, p.summary),
          h("span", { class: "small", style: { display: "block", marginTop: "4px" } },
            blocked(p) || `This episode: ${fmtSize((p.gb_per_hour * (ep.finished_duration || final)) / 3600)}${ep.sample ? " (sample length)" : ""}`
              + (p.kind === "video" && !ep.sample ? ". Video takes a while; the first time, Cutzo also makes 1080p copies of the show's videos." : ""))));
      const confirmBox = h("div", {});
      const showConfirm = () => {
        clear(confirmBox);
        if (!draft.confirm) return;
        add(confirmBox, h("div", { class: "next-step", style: { borderLeftColor: "var(--marker)" } },
          h("p", {}, h("strong", {}, "Replace finished files?"), draft.confirm),
          h("div", { class: "row" },
            h("button", { class: "btn", onclick: () => { draft.confirm = null; showConfirm(); } }, "Keep them"),
            h("button", { class: "btn btn-cut", onclick: () => go(true) }, "Replace"))));
      };
      const go = async (confirm = false) => {
        const targets = [...pick];
        if (!targets.length) return toast("Pick at least one target.", { kind: "error" });
        draft.confirm = null;
        showConfirm();
        try {
          await api(epPath(ep.id) + "/export", { method: "POST", body: { targets, confirm } });
          toast("Exporting started. Progress shows on the left.", { kind: "ok" });
          ctx.changed();
          await view.refresh();
        } catch (err) {
          if (err.status === 409) {
            draft.confirm = err.message;
            showConfirm();
          } else toastError(err);
        }
      };
      showConfirm();
      const busy = (ep.busy || []).some((j) => j.kind === "export");
      add(presetsBox, h("section", { class: "panel" },
        h("h3", {}, "Export"),
        h("p", { class: "muted" }, `Ticked: the usual exports for ${ep.show_name}. Each finished file is checked against its platform's rules.`),
        h("div", { class: "choice-row" }, main.map(option)),
        reserve.length ? h("details", { style: { marginTop: "12px" } }, h("summary", { class: "small" }, "More targets"), h("div", { class: "choice-row", style: { marginTop: "10px" } }, reserve.map(option))) : null,
        confirmBox,
        h("div", { class: "row", style: { marginTop: "16px" } },
          h("button", { class: "btn btn-primary", disabled: busy || !built(), onclick: () => go(false) }, busy ? "Exporting\u2026" : "Export"),
          h("span", { class: "small muted" }, ep.sample
            ? "Sample episode: no file is written."
            : "Writes the finished file to the exports folder in your Cutzo library, then checks it."))));
    }).catch(toastError);

    const done = (ep.exports || []).slice().reverse();
    const reveal = (file) => api("/api/reveal", { method: "POST", body: { file } })
      .then((r) => toast(r.shown ? "Opened the folder in File Explorer." : (r.note || "Done."), {})).catch(toastError);
    if (done.length) {
      add(exportsBox, h("h2", { class: "section-title" }, "Finished"));
      const extras = ep.extras || {};
      if (extras.captions_file || extras.chapters_file) {
        const line = (label, file, what) => h("p", { class: "small", style: { wordBreak: "break-all", margin: "8px 0 0" } },
          h("b", {}, `${label}: `), file, h("span", { class: "muted" }, ` (${what})`),
          h("button", { class: "btn btn-quiet btn-small", style: { marginLeft: "8px" }, onclick: () => reveal(file) }, "Show in folder"));
        add(exportsBox, h("section", { class: "panel", id: "extras" },
          h("h3", { style: { margin: 0 } }, "Captions and chapters"),
          h("p", { class: "small muted", style: { margin: "6px 0 0" } },
            "Made with the exports and timed to the finished file. On YouTube, upload the captions file (with timing) and paste the chapter list into the description."),
          extras.captions_file
            ? line("Captions", extras.captions_file, `${extras.captions} captions`)
            : h("p", { class: "small muted", style: { margin: "8px 0 0" } }, "No captions: this episode has no transcript yet."),
          extras.chapters_file ? line("Chapter list", extras.chapters_file, `${extras.chapters} chapters`) : null));
      }
      for (const x of done) {
        add(exportsBox, h("section", { class: "panel" },
          h("div", { class: "row", style: { justifyContent: "space-between" } },
            h("h3", { style: { margin: 0 } }, x.label),
            h("span", { class: x.check.passed ? "tag tag-ok" : "tag tag-cut" }, x.check.passed ? "All checks passed" : "Some checks failed")),
          h("p", { class: "small", style: { wordBreak: "break-all", margin: "8px 0 4px" } }, x.file,
            x.written ? h("button", {
              class: "btn btn-quiet btn-small", style: { marginLeft: "8px" }, onclick: () => reveal(x.file),
            }, "Show in folder") : null),
          h("p", { class: "small muted" }, x.written
            ? `${relTime(x.at)}. ${x.final_length} long, ${fmtSize((x.bytes || 0) / 1e9)}.`
            : `${relTime(x.at)}. ${x.final_length} long, ${fmtSize(x.estimated_gb)}. Not written: sample episode.`),
          h("div", { class: "table-wrap" }, h("table", { class: "grid" },
            h("thead", {}, h("tr", {}, h("th", {}, "Rule"), h("th", {}, "Needs"), h("th", {}, "Result"))),
            h("tbody", {}, x.check.rules.map((r) => h("tr", {},
              h("td", { class: "name" }, r.rule),
              h("td", {}, r.expected),
              h("td", { class: r.pass ? "check-pass" : "check-fail" }, r.pass ? "Pass" : "Fail", h("span", { class: "sub" }, r.found))))))),
          x.check.note ? h("p", { class: "small muted", style: { marginTop: "10px" } }, x.check.note) : null));
      }
    }
  }

  function describeProbe(pr) {
    const parts = [];
    if (pr.duration) parts.push(fmtDur(pr.duration));
    if (pr.audio) parts.push(`${(pr.audio.codec || "").toUpperCase()} ${pr.audio.channels === 1 ? "mono" : pr.audio.channels === 2 ? "stereo" : `${pr.audio.channels} ch`}, ${Math.round((pr.audio.rate || 0) / 100) / 10} kHz`);
    if (pr.video) parts.push(`${pr.video.width}x${pr.video.height} ${pr.video.fps || ""} fps${pr.video.variable_frame_rate ? " (variable)" : ""}`);
    if (!pr.audio && !pr.video) parts.push("no sound or picture found");
    return parts.join(" \u00b7 ");
  }

  function cleanupControl() {
    const value = ep.voice_settings.strength;
    const out = h("output", {}, String(value));
    const slider = h("input", {
      type: "range", min: "0", max: "100", step: "5", value: String(value), "aria-label": "Cleanup strength",
      oninput: (e) => { out.textContent = e.target.value; },
      onchange: (e) => act(() => api(epPath(ep.id) + "/voice", { method: "POST", body: { strength: Number(e.target.value) } }), (r) => r.summary),
    });
    return h("div", { class: "field", style: { marginTop: "12px" } },
      h("span", {}, "Cleanup strength: ", out, " of 100"),
      slider,
      h("span", { class: "hint" }, "How far background noise is turned down. 25 is the light setting you picked in the listening test. Build again to hear a change."));
  }

  drawHead();
  drawTabs();
  drawTab();
  return view;
}
