// Review: the deck (preview, player, waveform with spliced-out cuts) above the
// transcript, which you mark up like a script: select words to cut them, click
// struck words to bring them back. Real episodes also get a zoomed waveform for precise
// selections, a switch to hear the original sound, and a panel above the transcript with
// the cold open (or teaser), sign-off and dead-air settings (the panel alone until they're transcribed).

import { api, epPath } from "../api.js";
import { h, add, clear, icons, fmtTime, fmtDur, plural, toast, toastError, confirmDialog } from "../ui.js";

const REASON = { "dead air": "Dead air", "filler word": "Filler word", words: "Words", manual: "Section", chosen: "Chosen" };
// How each show opens and closes with stretches you pick from the talk (the API calls them cold_open and outro).
const PICK_WORDS = {
  "the-dig": {
    open: "Cold open", openAbout: "Highlights that open the episode over the theme, before the sound bites.",
    openEmpty: "None yet. Season Debrief Part 2 opened with three short highlights from late in the show.",
    closeAbout: "Plays at the very end, after the conversation, over the theme.",
    closeEmpty: "None yet. Optional: the theme ends the episode either way.",
  },
  "the-new-heretics": {
    open: "Teaser", openAbout: "Highlights from the talk that open the episode with the spectrum, before the montage.",
    openEmpty: "None yet. Spooky Sode's teaser was three short clips from late in the show, about 18 s in all.",
    closeAbout: "Plays after the talk, over Full Flames and the quiet ending music.",
    closeEmpty: "None yet. Optional: the ending music and flames close the episode either way.",
  },
};
const BY = { auto: "Suggested by Cutzo", you: "You", claude: "Claude" };
const CAM_COLORS = ["#8ec5ff", "#ffcf6e", "#9ee6b8", "#f6a7c8", "#c7b8ff", "#ffb48a"];
const SHOW_LOOK = {
  "the-new-heretics": { top: "#14090a", bottom: "#2f0f08", glow: "255, 106, 61", bar: "#ff8a5c", name: "The New Heretics" },
  "the-dig": { top: "#0c1b33", bottom: "#081324", glow: "217, 164, 65", bar: "#e8b85a", name: "The Dig" },
};

export function mountReview(container, initialEp, ctx) {
  let ep = initialEp;
  let paragraphs = [];
  let words = [];
  let wordEls = new Map();
  let peaks = [];
  let duration = ep.duration || 1;
  let cuts = [];
  let selection = null;
  let prevCut = null;
  let raf = 0;
  let destroyed = false;
  let audioCtx = null;
  let analyser = null;
  let freq = null;
  let skipCuts = true;
  let nowIndex = -1;
  let lastDraw = 0;
  let drag = null;
  let chapterTimeTouched = false;
  const chapterDraft = { time: null, title: "", segment: false };
  const titleDraft = { touched: false, label: "", title: "", guests: "" };
  // Extras: one-off sounds or videos for this episode (the haiku in Spooky Sode).
  let extraTimeTouched = false;
  const extraDraft = { where: "time", time: null, file: "", kind: "", start: "", end: "", gain: "", label: "", more: false };
  let kitItems = null; // the show kit's sound and video items, suggested in the file box
  let cutSignature = "";
  const look = SHOW_LOOK[ep.show] || SHOW_LOOK["the-dig"];
  const real = !ep.sample;
  let hearOriginal = false;
  // The zoom strip: a close-up of the waveform around the playhead, for long recordings.
  const zoom = { span: 30, start: 0, peaks: [], key: "", timer: 0 };
  const deadDraft = { longer: null, keep: null };

  // ------------------------------------------------------------ layout
  const audioUrl = () => `${epPath(ep.id)}/audio?which=${hearOriginal ? "original" : "cleaned"}&d=${ep.duration}`;
  const audio = h("audio", { preload: "auto", src: audioUrl() });
  const preview = h("canvas", { width: 640, height: 360, role: "img", "aria-label": "Preview picture of the episode" });
  const playBtn = h("button", { class: "play", "aria-label": "Play", svg: icons.play, onclick: togglePlay });
  const clock = h("div", { class: "clock" });
  const finalLen = h("div", { class: "final-len" });
  const skip = h("input", { type: "checkbox", checked: true, onchange: (e) => { skipCuts = e.target.checked; } });
  const original = h("input", { type: "checkbox", onchange: (e) => switchSound(e.target.checked) });
  const flags = h("div", { class: "flags" });
  const wave = h("canvas", { class: "wave", tabindex: "0", role: "img", "aria-label": "Waveform. Click to move the playhead; drag to select a section." });
  const waveZoom = h("canvas", { class: "wave wave-zoom", role: "img", "aria-label": "Close-up of the waveform around the playhead. Drag to select precisely." });
  const zoomBar = h("div", { class: "zoom-bar" });
  const zoomArea = h("div", { class: "zoom-area" }, zoomBar, waveZoom);
  const camLabel = h("div", { class: "lane-label" }, "Camera");
  const camLane = h("div", { class: "cam-lane" });
  const selbar = h("div", { class: "selbar" });
  const deck = h("section", { class: "deck", "aria-label": "Player" },
    h("div", {}, h("div", { class: "preview" }, preview),
      h("p", { class: "preview-note" }, !real
        ? "Sample preview: pretend data, nothing is rendered."
        : ep.has_picture
          ? "A quick sketch that moves with the sound. The real video is drawn when you export; watch it on the Export tab."
          : "Audio only: the picture moves with the sound while you listen.")),
    h("div", {},
      h("div", { class: "transport" }, playBtn, clock, finalLen,
        ep.has_original ? h("label", { class: "switch", title: "Compare with the recording before cleanup" }, original, "Hear the original") : null,
        h("label", { class: "switch" }, skip, "Skip cuts while playing")),
      h("div", { class: "wave-area" }, flags, wave, zoomArea, camLabel, camLane),
      selbar),
    audio);
  const panelBox = h("div", { class: "panel-box" });
  const textBox = h("div", { class: "text-box" }, h("p", { class: "muted" }, "Loading the transcript…"));
  const script = h("section", { class: "script", "aria-label": "Transcript" }, panelBox, textBox);
  const side = h("aside", { class: "side", "aria-label": "Cuts and chapters" });
  add(container, [deck, h("div", { class: "review" }, script, side)]);
  const floatBar = h("div", { class: "float-bar", role: "toolbar", "aria-label": "Selection", hidden: true });
  document.body.append(floatBar);

  // -------------------------------------------------------------- data
  function computeCuts() {
    // [start, end, pause?]: a range is a "pause" when only Cutzo's dead-air suggestions cover it.
    const ranges = ep.edits.filter((e) => e.type === "cut")
      .map((e) => [e.start, e.end, e.reason === "dead air" && e.by === "auto"]).sort((a, b) => a[0] - b[0]);
    const merged = [];
    for (const [a, b, pause] of ranges) {
      const last = merged[merged.length - 1];
      if (last && a <= last[1] + 1e-6) {
        last[1] = Math.max(last[1], b);
        last[2] = last[2] && pause;
      } else merged.push([a, b, pause]);
    }
    cuts = merged;
  }

  const cutAt = (t) => cuts.find(([a, b]) => t >= a && t < b) || null;

  async function loadTranscript() {
    if (!ep.has_transcript) {
      paragraphs = [];
      words = [];
      return;
    }
    const t = await api(`${epPath(ep.id)}/transcript?format=words`);
    paragraphs = t.paragraphs;
    words = [];
    for (const p of paragraphs) for (const w of p.words) words[w.i] = { ...w, speaker: p.speaker };
  }

  async function loadWave() {
    const w = await api(`${epPath(ep.id)}/waveform?buckets=1600`);
    peaks = w.peaks;
    duration = w.duration || ep.duration || 1;
  }

  // ----------------------------------------------------------- camera
  function cameraSegments() {
    const edits = ep.edits.filter((e) => e.type === "camera").sort((a, b) => Number(a.id.slice(1)) - Number(b.id.slice(1)));
    if (!edits.length) return [];
    const points = [...new Set([0, duration, ...edits.flatMap((e) => [e.start, e.end])])].sort((a, b) => a - b);
    const segs = [];
    for (let i = 0; i < points.length - 1; i++) {
      const a = points[i];
      const b = points[i + 1];
      if (b - a < 1e-3) continue;
      const mid = (a + b) / 2;
      let angle = null;
      for (const e of edits) if (mid >= e.start && mid < e.end) angle = e.angle;
      if (!angle) continue;
      const last = segs[segs.length - 1];
      if (last && last.angle === angle && Math.abs(last.b - a) < 1e-3) last.b = b;
      else segs.push({ a, b, angle });
    }
    return segs;
  }
  let camSegs = [];
  const cameraAt = (t) => (camSegs.find((s) => t >= s.a && t < s.b) || {}).angle || null;
  const camColor = (angle) => CAM_COLORS[Math.max(0, (ep.cameras || []).indexOf(angle)) % CAM_COLORS.length];

  function drawCamLane() {
    clear(camLane);
    const show = (ep.cameras || []).length > 0;
    camLane.hidden = !show;
    camLabel.hidden = !show;
    if (!show) return;
    camSegs = cameraSegments();
    for (const s of camSegs) {
      camLane.append(h("span", {
        class: "cam-seg",
        title: `${s.angle}: ${fmtTime(s.a)} to ${fmtTime(s.b)}`,
        style: { left: `${(s.a / duration) * 100}%`, width: `${((s.b - s.a) / duration) * 100}%`, background: camColor(s.angle) },
      }, s.angle));
    }
  }

  // -------------------------------------------------------- transcript
  function drawScript() {
    if (real) drawPanel();
    clear(textBox);
    if (!ep.has_transcript) return;
    const nowCut = new Set(words.filter((w) => w && w.cut).map((w) => w.i));
    wordEls = new Map();
    add(textBox, h("div", { class: "script-head", style: { marginTop: real ? "26px" : "0" } },
      h("h3", {}, "Transcript"),
      h("p", {}, "Select words to cut them. Click struck words to bring them back.")));
    for (const p of paragraphs) {
      const text = h("p", { class: "words" });
      const segments = [];
      for (const w of p.words) {
        const last = segments[segments.length - 1];
        if (last && last.cut === w.cut) last.words.push(w);
        else segments.push({ cut: w.cut, words: [w] });
      }
      segments.forEach((seg, n) => {
        const spans = [];
        seg.words.forEach((w, k) => {
          const span = h("span", { class: "w", "data-i": String(w.i) }, w.w);
          wordEls.set(w.i, span);
          spans.push(span);
          if (k < seg.words.length - 1) spans.push(" ");
        });
        if (seg.cut) {
          const fresh = prevCut && seg.words.some((w) => !prevCut.has(w.i));
          const first = seg.words[0];
          const last = seg.words[seg.words.length - 1];
          const run = h("span", {
            class: `cut-run${fresh ? " fresh" : ""}`,
            role: "button",
            tabindex: "0",
            title: "Cut. Click to bring these words back.",
            "aria-label": `Cut words: ${seg.words.map((w) => w.w).join(" ")}. Press Enter to bring them back.`,
            onclick: (e) => { e.stopPropagation(); offerRestore(run, first.s, last.e); },
            onkeydown: (e) => { if (e.key === "Enter") offerRestore(run, first.s, last.e); },
          }, spans);
          text.append(run);
        } else {
          text.append(...spans);
        }
        if (n < segments.length - 1) text.append(" ");
      });
      textBox.append(h("div", { class: "para" },
        h("div", { class: "who-col" },
          h("span", { class: "who-name" }, p.speaker_id || ep.sample ? p.speaker : ""),
          h("button", { class: "para-time", title: "Play from here", onclick: () => seek(p.start, true) }, fmtTime(p.start))),
        text));
    }
    textBox.append(h("p", { class: "script-tip" },
      "Keys: Space plays or pauses, the arrow keys move a second (Shift for five), Ctrl+Z undoes the last change.",
      ep.sample ? " This transcript is sample text." : " Cuts land in the quiet between words."));
    prevCut = nowCut;
    nowIndex = -1;
  }

  // Real episodes: the cold open (The New Heretics' teaser), the sign-off and how tight the dead-air trimming is.
  function drawPanel() {
    clear(panelBox);
    const words = PICK_WORDS[ep.show];
    if (words) {
      const picks = ep.picks || { cold_open: [], outro: [] };
      const list = (key, title, about, empty) => {
        const items = picks[key] || [];
        const total = items.reduce((t, p) => t + p.length, 0);
        return h("div", { class: "pick-group" },
          h("div", { class: "side-head" }, h("h3", {}, title), h("span", {}, items.length ? fmtDur(total) : "")),
          h("p", { class: "small muted" }, about),
          items.length
            ? h("ul", { class: "items" }, items.map((p, n) => h("li", { class: "item" },
                h("div", { class: "item-main", onclick: () => seek(p.start, true), title: "Play this stretch" },
                  h("div", { class: "item-time" }, `${n + 1}. ${p.start_text}–${p.end_text}`),
                  h("div", { class: "item-text" }, `${p.length.toFixed(1)} s`),
                  h("div", { class: "item-tags" }, h("span", { class: p.by === "claude" ? "tag tag-claude" : "tag" }, BY[p.by] || p.by))),
                h("button", {
                  class: "btn btn-quiet btn-small",
                  onclick: () => ctx.act(() => api(`${epPath(ep.id)}/picks/${encodeURIComponent(p.id)}`, { method: "DELETE" }), (r) => r.summary),
                }, "Remove"))))
            : h("p", { class: "muted small" }, empty));
      };
      add(panelBox, [
        h("div", { class: "script-head" }, h("h3", {}, "How the episode opens and closes"),
          h("p", {}, `${ep.has_transcript ? "Select words in the transcript, or drag across the waveform" : "Drag across the waveform"}, `
            + `then choose Add to ${words.open.toLowerCase()} or Add to sign-off.`)),
        h("div", { class: "two-col" },
          list("cold_open", words.open, words.openAbout, words.openEmpty),
          list("outro", "Sign-off", words.closeAbout, words.closeEmpty)),
      ]);
    }
    const da = ep.dead_air || { longer_than: 1, keep: 0.5 };
    const longer = h("input", { type: "text", inputmode: "decimal", "aria-label": "Silences longer than (seconds)", value: deadDraft.longer ?? String(da.longer_than), oninput: (e) => { deadDraft.longer = e.target.value; } });
    const keep = h("input", { type: "text", inputmode: "decimal", "aria-label": "Shrink them to (seconds)", value: deadDraft.keep ?? String(da.keep), oninput: (e) => { deadDraft.keep = e.target.value; } });
    const deadCuts = ep.edits.filter((e) => e.type === "cut" && e.reason === "dead air");
    const saved = deadCuts.reduce((t, e) => t + e.length, 0);
    add(panelBox, [
      h("div", { class: "script-head", style: { marginTop: words ? "18px" : "0" } }, h("h3", {}, "Dead air"),
        h("p", {}, `${plural(deadCuts.length, "long silence")} shortened, ${fmtDur(saved)} out`)),
      h("form", {
        class: "dead-air",
        onsubmit: async (e) => {
          e.preventDefault();
          const res = await ctx.act(() => api(`${epPath(ep.id)}/dead-air`, { method: "POST", body: { longer_than: Number(longer.value), keep: Number(keep.value) } }), (r) => r.summary);
          if (res) { deadDraft.longer = null; deadDraft.keep = null; }
        },
      },
        h("span", {}, "Shorten silences longer than"), longer, h("span", {}, "seconds to"), keep, h("span", {}, "seconds."),
        h("button", { class: "btn btn-small", type: "submit" }, "Apply")),
      h("p", { class: "small muted" }, "Smaller numbers make the conversation tighter. Cuts you brought back stay back."),
      ep.has_transcript ? null : h("p", { class: "script-tip" },
        "No transcript yet: use Transcribe at the top (about 2 minutes) to cut by words. Meanwhile, find moments with the ",
        `waveform; the close-up under it shows ${zoom.span} seconds around the playhead for precise selections. Keys: Space `,
        "plays or pauses, the arrow keys move a second (Shift for five), Ctrl+Z undoes the last change."),
    ]);
  }

  async function switchSound(toOriginal) {
    const at = audio.currentTime;
    const playing = !audio.paused;
    hearOriginal = toOriginal;
    audio.src = audioUrl();
    audio.currentTime = at;
    await new Promise((resolve) => audio.addEventListener("loadedmetadata", resolve, { once: true }));
    audio.currentTime = at;
    if (playing) audio.play().catch(() => {});
    toast(toOriginal ? "Playing the original recording (before cleanup)." : "Playing the cleaned sound.", {});
  }

  function onScriptClick(e) {
    const span = e.target.closest(".w");
    if (!span || span.closest(".cut-run")) return;
    const sel = window.getSelection();
    if (sel && !sel.isCollapsed) return;
    const w = words[Number(span.dataset.i)];
    if (w) seek(w.s);
  }

  function onScriptSelect() {
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed || !sel.rangeCount) return;
    const range = sel.getRangeAt(0);
    if (!script.contains(range.commonAncestorContainer)) return;
    const picked = [...script.querySelectorAll(".w")].filter((el) => sel.containsNode(el, true) && el.textContent.trim());
    if (!picked.length) return;
    const first = words[Number(picked[0].dataset.i)];
    const last = words[Number(picked[picked.length - 1].dataset.i)];
    selection = { a: first.s, b: last.e, source: "text" };
    drawSelbar();
    showFloat(range.getBoundingClientRect(), [
      h("span", { class: "info" }, `${plural(picked.length, "word")}, ${fmtTime(first.s, true)}–${fmtTime(last.e, true)}`),
      h("button", { class: "btn btn-cut", onclick: () => cutRange(first.s - 0.02, last.e + 0.04, "words") }, "Cut words"),
      h("button", { class: "btn", onclick: () => { prefillChapter(first.s); hideFloat(); } }, "Add chapter here"),
      h("button", { class: "btn", onclick: () => { seek(first.s, true); hideFloat(); } }, "Play"),
    ]);
  }

  function offerRestore(el, a, b) {
    const hit = ep.edits.filter((e) => e.type === "cut" && e.start < b && e.end > a);
    if (!hit.length) return;
    const longest = hit.reduce((m, e) => Math.max(m, e.end - e.start), 0);
    showFloat(el.getBoundingClientRect(), [
      h("span", { class: "info" }, hit.length === 1 ? `${REASON[hit[0].reason] || "Cut"}, ${BY[hit[0].by] || hit[0].by}` : `${hit.length} cuts here`),
      h("button", { class: "btn", onclick: async () => { hideFloat(); for (const e of hit) await restore(e, true); } },
        longest > 20 ? `Bring back ${fmtDur(longest)}` : "Bring back"),
      h("button", { class: "btn", onclick: () => { seek(Math.max(0, a - 1.5), true); hideFloat(); } }, "Play around it"),
    ]);
  }

  function highlightWord() {
    const t = audio.currentTime;
    let idx = -1;
    let lo = 0;
    let hi = words.length - 1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      const w = words[mid];
      if (!w) { hi = mid - 1; continue; }
      if (w.s <= t) { idx = mid; lo = mid + 1; } else hi = mid - 1;
    }
    if (idx >= 0 && words[idx] && t > words[idx].e + 0.35) idx = -1;
    if (idx === nowIndex) return;
    if (nowIndex >= 0 && wordEls.get(nowIndex)) wordEls.get(nowIndex).classList.remove("now");
    nowIndex = idx;
    const el = idx >= 0 ? wordEls.get(idx) : null;
    if (el) {
      el.classList.add("now");
      if (!audio.paused) {
        const r = el.getBoundingClientRect();
        if (r.top < 80 || r.bottom > window.innerHeight - 40) el.scrollIntoView({ block: "center", behavior: "smooth" });
      }
    }
  }

  // ------------------------------------------------------------- side
  function drawSide() {
    clear(side);
    const cutEdits = ep.edits.filter((e) => e.type === "cut");
    const dead = cutEdits.filter((e) => e.reason === "dead air" && e.by === "auto");
    const removed = Math.max(0, (ep.duration || 0) - (ep.final_duration || 0));
    const counted = dead.length ? `${plural(cutEdits.length - dead.length, "cut")} + ${plural(dead.length, "pause")}` : plural(cutEdits.length, "cut");
    const cutsSec = h("section", {},
      h("div", { class: "side-head" }, h("h3", {}, "Cuts"), h("span", {}, `${counted}, ${fmtDur(removed)} out`)));
    const cutItem = (e) => h("li", { class: "item" },
      h("div", { class: "item-main", onclick: () => seek(Math.max(0, e.start - 1.5)), title: "Jump to this cut" },
        h("div", { class: "item-time" }, `${e.start_text}–${e.end_text}`),
        h("div", { class: "item-text", title: e.text || "" }, e.text || REASON[e.reason] || "Cut"),
        h("div", { class: "item-tags" },
          h("span", { class: "tag tag-cut" }, REASON[e.reason] || "Cut"),
          h("span", { class: e.by === "claude" ? "tag tag-claude" : "tag" }, BY[e.by] || e.by))),
      h("button", { class: "btn btn-quiet btn-small", onclick: () => restore(e) }, "Bring back"));
    if (!cutEdits.length) {
      cutsSec.append(h("p", { class: "muted small" }, ep.has_transcript
        ? "No cuts yet. Select words in the transcript, or drag across the waveform."
        : "No cuts yet. Drag across the waveform to select a section, then Cut section."));
    } else if (dead.length > 8) {
      const others = cutEdits.filter((e) => !dead.includes(e));
      const deadTime = dead.reduce((t, e) => t + e.length, 0);
      if (others.length) cutsSec.append(h("ul", { class: "items" }, others.map(cutItem)));
      cutsSec.append(h("details", { class: "dead-list" },
        h("summary", {}, `${plural(dead.length, "dead-air cut")} suggested by Cutzo (${fmtDur(deadTime)})`),
        h("ul", { class: "items" }, dead.map(cutItem))));
    } else {
      cutsSec.append(h("ul", { class: "items" }, cutEdits.map((e) => h("li", { class: "item" },
        h("div", { class: "item-main", onclick: () => seek(Math.max(0, e.start - 1.5)), title: "Jump to this cut" },
          h("div", { class: "item-time" }, `${e.start_text}–${e.end_text}`),
          h("div", { class: "item-text", title: e.text || "" }, e.text || REASON[e.reason] || "Cut"),
          h("div", { class: "item-tags" },
            h("span", { class: "tag tag-cut" }, REASON[e.reason] || "Cut"),
            h("span", { class: e.by === "claude" ? "tag tag-claude" : "tag" }, BY[e.by] || e.by))),
        h("button", { class: "btn btn-quiet btn-small", onclick: () => restore(e) }, "Bring back")))));
    }

    const timeInput = h("input", {
      type: "text",
      id: "chapter-time",
      "aria-label": "Chapter time",
      value: chapterTimeTouched && chapterDraft.time != null ? chapterDraft.time : fmtTime(audio.currentTime || 0),
      oninput: (e) => { chapterTimeTouched = true; chapterDraft.time = e.target.value; },
    });
    const titleInput = h("input", {
      type: "text",
      id: "chapter-title",
      "aria-label": "Chapter title",
      placeholder: ep.show === "the-new-heretics" ? "Heretical Haiku" : "Chapter title",
      value: chapterDraft.title,
      oninput: (e) => { chapterDraft.title = e.target.value; },
    });
    const segments = ep.breaks_at === "segments";
    const segBox = h("input", { type: "checkbox", checked: chapterDraft.segment, onchange: (e) => { chapterDraft.segment = e.target.checked; } });
    const chapters = h("section", { id: "chapters" },
      h("div", { class: "side-head" }, h("h3", {}, "Chapters"), h("span", {}, plural(ep.chapters.length, "chapter"))),
      ep.chapters.length
        ? h("ul", { class: "items" }, ep.chapters.map((c, k) => h("li", { class: "item" },
            h("div", { class: "item-main", onclick: () => seek(c.time, true), title: "Play from this chapter" },
              h("div", {}, h("span", { class: "item-time" }, c.time_text), " ", h("span", { class: "chapter-title" }, c.title)),
              h("div", { class: "item-text" }, `At ${c.output_text} in the finished file`),
              h("div", { class: "item-tags" },
                c.segment && segments ? h("span", { class: "tag tag-ok" }, "Music break before") : null,
                c.voiced_title ? h("span", { class: "tag tag-ok", title: `Plays ${c.voiced_title}` }, "Voiced title plays") : null,
                h("span", { class: c.by === "claude" ? "tag tag-claude" : "tag" }, BY[c.by] || c.by)),
              segments && k > 0 ? h("label", {
                class: "seg-toggle small",
                title: "A new segment gets a music break before it; otherwise it's a new topic with a page flip.",
                onclick: (e) => e.stopPropagation(),
              }, h("input", {
                type: "checkbox",
                checked: !!c.segment,
                onchange: (e) => ctx.act(() => api(`${epPath(ep.id)}/chapters/${encodeURIComponent(c.id)}/segment`,
                  { method: "POST", body: { segment: e.target.checked } }), (r) => r.summary),
              }), " New segment (music break before it)") : null),
            h("button", {
              class: "btn btn-quiet btn-small",
              "aria-label": `Remove chapter ${c.title}`,
              onclick: () => ctx.act(() => api(`${epPath(ep.id)}/chapters/${encodeURIComponent(c.id)}`, { method: "DELETE" }), (r) => r.summary),
            }, "Remove"))))
        : h("p", { class: "muted small" }, "No chapters yet."),
      h("form", {
        class: "add-chapter",
        onsubmit: async (e) => {
          e.preventDefault();
          if (!titleInput.value.trim()) return titleInput.focus();
          const body = { time: timeInput.value.trim(), title: titleInput.value.trim() };
          if (segments) body.segment = chapterDraft.segment;
          const res = await ctx.act(() => api(`${epPath(ep.id)}/chapters`, { method: "POST", body }),
            (r) => r.voiced_title ? `${r.summary}. ${r.voiced_title} plays there.` : r.summary);
          if (res) {
            chapterTimeTouched = false;
            chapterDraft.time = null;
            chapterDraft.title = "";
            chapterDraft.segment = false;
            const title = document.getElementById("chapter-title");
            if (title) title.value = "";
          }
        },
      }, timeInput, titleInput, h("button", { class: "btn", type: "submit" }, "Add")),
      segments ? h("label", { class: "seg-toggle small" }, segBox, " New segment (music break before it)") : null,
      h("p", { class: "small muted", style: { marginTop: "6px" } }, segments
        ? "The time fills in from the playhead. Times are in the original recording. Each new chapter gets a page flip; a new segment gets a music break instead."
        : "The time fills in from the playhead. Times are in the original recording."));

    let graphic = null;
    if (ep.video_title) {
      if (!titleDraft.touched) Object.assign(titleDraft, ep.video_title);
      const line = (key, label, hint) => h("label", { class: "field" }, label,
        h("input", { type: "text", value: titleDraft[key], placeholder: hint,
          oninput: (e) => { titleDraft[key] = e.target.value; titleDraft.touched = true; } }));
      graphic = h("section", { id: "title-graphic" },
        h("div", { class: "side-head" }, h("h3", {}, "Title graphic")),
        h("p", { class: "small muted" }, "Shown under the show's name while you talk. The first two lines show in capitals."),
        h("form", {
          onsubmit: async (e) => {
            e.preventDefault();
            const res = await ctx.act(() => api(`${epPath(ep.id)}/video-title`, { method: "POST",
              body: { label: titleDraft.label, title: titleDraft.title, guests: titleDraft.guests } }), (r) => r.summary);
            if (res) titleDraft.touched = false;
          },
        },
        line("label", "Top line", "Halloween Special:"),
        line("title", "Title", "JW Ghost Stories"),
        line("guests", "Guests", "ft. Two exJWs"),
        h("button", { class: "btn", type: "submit" }, "Save title graphic")));
    }

    const yt = h("textarea", { class: "copybox", readonly: true, "aria-label": "YouTube chapters" });
    yt.value = ep.youtube_chapters || "";
    const video = ep.show === "the-new-heretics";
    const youtube = h("section", {},
      h("div", { class: "side-head" }, h("h3", {}, video ? "For YouTube" : "Chapter list"),
        h("button", {
          class: "btn btn-quiet btn-small",
          onclick: async () => {
            try {
              await navigator.clipboard.writeText(yt.value);
              toast("Copied the chapter list.", { kind: "ok" });
            } catch {
              yt.select();
              toast("Press Ctrl+C to copy.", {});
            }
          },
        }, "Copy")),
      h("p", { class: "small muted" }, video ? "Paste into the video description. Times match the finished file."
        : "Paste into the episode notes. Times match the finished file, intro and breaks included."),
      yt,
      ep.chapter_warnings && ep.chapter_warnings.length ? h("ul", { class: "warn-list" }, ep.chapter_warnings.map((w) => h("li", {}, w))) : null);
    add(side, [cutsSec, chapters, real ? extrasSection() : null, graphic, youtube]);
  }

  function extrasSection() {
    const items = ep.inserts || [];
    if (kitItems === null) {
      kitItems = [];
      api(`/api/assets?show=${encodeURIComponent(ep.show)}`).then((r) => {
        kitItems = r.assets.filter((a) => a.kind === "audio" || a.kind === "video");
        const list = document.getElementById("extra-kit");
        if (list) add(list, kitItems.map((a) => h("option", { value: a.name.replace(/\.[^.]+$/, "") })));
      }).catch(() => {});
    }
    const segments = ep.breaks_at === "segments";
    const timeBox = h("input", {
      type: "text", id: "extra-time", "aria-label": "Time in the recording", hidden: extraDraft.where !== "time",
      value: extraTimeTouched && extraDraft.time != null ? extraDraft.time : fmtTime(audio.currentTime || 0),
      oninput: (e) => { extraTimeTouched = true; extraDraft.time = e.target.value; },
    });
    const whereRow = h("div", { class: `where${extraDraft.where === "time" ? "" : " no-time"}` });
    const whereSel = h("select", {
      "aria-label": "Where the extra goes",
      onchange: (e) => {
        extraDraft.where = e.target.value;
        timeBox.hidden = e.target.value !== "time";
        whereRow.classList.toggle("no-time", e.target.value !== "time");
      },
    },
    h("option", { value: "time", selected: extraDraft.where === "time" }, "At a time in the talk"),
    ep.chapters.slice(1).map((c) => h("option", { value: c.id, selected: extraDraft.where === c.id },
      !segments || c.segment ? `In the break before \u201c${c.title}\u201d` : `Before the topic \u201c${c.title}\u201d`)));
    add(whereRow, [whereSel, timeBox]);
    const fileBox = h("input", {
      type: "text", list: "extra-kit", "aria-label": "File or show-kit item", value: extraDraft.file,
      placeholder: "G:\\My Drive\\...\\Spooky Haiku.wav, or Haiku Intro",
      oninput: (e) => { extraDraft.file = e.target.value; },
    });
    const kindSel = h("select", { "aria-label": "What it is", onchange: (e) => { extraDraft.kind = e.target.value; } },
      [["", "Cutzo guesses"], ["voice", "Spoken"], ["music", "Music"], ["video", "Video"]]
        .map(([v, t]) => h("option", { value: v, selected: extraDraft.kind === v }, t)));
    const small = (key, label, hint) => h("label", { class: "field" }, label,
      h("input", { type: "text", value: extraDraft[key], placeholder: hint, oninput: (e) => { extraDraft[key] = e.target.value; } }));
    const more = h("details", { open: extraDraft.more, ontoggle: (e) => { extraDraft.more = e.target.open; } },
      h("summary", {}, "Part of the file, level, name"),
      h("div", { class: "pair" }, small("start", "From", "0:00"), small("end", "To", "the end")),
      h("div", { class: "pair" }, small("gain", "Level change (dB)", "0"), small("label", "Name", "the file's name")));
    const submit = async (e) => {
      e.preventDefault();
      if (!extraDraft.file.trim()) return fileBox.focus();
      const gain = extraDraft.gain.trim() ? Number(extraDraft.gain.trim().replace(",", ".")) : null;
      if (Number.isNaN(gain)) return toast("The level change must be a number, like -3 or 2.", { kind: "error" });
      const body = { file: extraDraft.file.trim(), kind: extraDraft.kind || null, gain_db: gain,
                     start: extraDraft.start.trim() || null, end: extraDraft.end.trim() || null, label: extraDraft.label.trim() || null };
      if (extraDraft.where === "time") body.time = timeBox.value.trim();
      else body.chapter = extraDraft.where;
      const res = await ctx.act(() => api(`${epPath(ep.id)}/extras`, { method: "POST", body }), (r) => r.summary);
      if (res) {
        Object.assign(extraDraft, { file: "", start: "", end: "", gain: "", label: "", time: null });
        extraTimeTouched = false;
      }
    };
    return h("section", { id: "extras-side" },
      h("div", { class: "side-head" }, h("h3", {}, "Extras"), h("span", {}, items.length ? plural(items.length, "extra") : "")),
      h("p", { class: "small muted" }, "One-off sounds or videos for this episode, like the haiku in Spooky Sode. "
        + "The talk pauses for them; you hear them in the finished file."),
      items.length ? h("ul", { class: "items" }, items.map((x) => h("li", { class: "item" },
        h("div", { class: "item-main", onclick: () => seek(Math.max(0, x.at_time - 3), true), title: "Play the talk around this spot" },
          h("div", {}, h("span", { class: "item-time" }, x.time_text), " ", h("span", { class: "chapter-title" }, x.label)),
          h("div", { class: "item-text item-wrap" }, x.where),
          h("div", { class: "item-text", title: x.source }, [
            `${x.kind_label[0].toUpperCase()}${x.kind_label.slice(1)}`, x.length_text, x.part,
            x.gain_db ? `${x.gain_db > 0 ? "+" : "−"}${Math.abs(x.gain_db)} dB` : null].filter(Boolean).join(" · ")),
          h("div", { class: "item-tags" }, h("span", { class: x.by === "claude" ? "tag tag-claude" : "tag" }, BY[x.by] || x.by))),
        h("button", {
          class: "btn btn-quiet btn-small",
          "aria-label": `Remove extra ${x.label}`,
          onclick: () => ctx.act(() => api(`${epPath(ep.id)}/extras/${encodeURIComponent(x.id)}`, { method: "DELETE" }), (r) => r.summary),
        }, "Remove")))) : null,
      h("form", { onsubmit: submit },
        h("div", { class: "field" }, h("span", {}, "Where"), whereRow),
        h("label", { class: "field" }, "File or show-kit item",
          h("span", { class: "hint" }, "In File Explorer, right-click the file and choose Copy as path."), fileBox),
        h("datalist", { id: "extra-kit" }, (kitItems || []).map((a) => h("option", { value: a.name.replace(/\.[^.]+$/, "") }))),
        h("label", { class: "field" }, "What it is", kindSel),
        more,
        h("button", { class: "btn", type: "submit" }, "Add extra")));
  }

  function prefillChapter(t) {
    const input = document.getElementById("chapter-time");
    chapterDraft.time = fmtTime(t);
    chapterTimeTouched = true;
    if (input) input.value = chapterDraft.time;
    const title = document.getElementById("chapter-title");
    if (title) {
      title.scrollIntoView({ block: "center", behavior: "smooth" });
      title.focus({ preventScroll: true });
    }
  }

  // ------------------------------------------------------------- flags
  function drawFlags() {
    clear(flags);
    for (const c of ep.chapters) {
      flags.append(h("button", {
        class: "flag",
        style: { left: `${(c.time / duration) * 100}%` },
        title: `${c.title} (${c.time_text})`,
        onclick: () => seek(c.time, true),
      }, c.title));
    }
  }

  // ------------------------------------------------------------ actions
  async function cutRange(a, b, reason, confirm = false) {
    hideFloat();
    window.getSelection()?.removeAllRanges();
    const body = { start: Math.max(0, a), end: Math.min(duration, b), reason, confirm };
    try {
      const r = await api(`${epPath(ep.id)}/cuts`, { method: "POST", body });
      selection = null;
      drawSelbar();
      toast(`${r.summary}. Now ${r.final_length}.`, { kind: "ok" });
      ctx.changed();
      await ctx.reload();
      return r;
    } catch (err) {
      if (err.status === 409 && !confirm) {
        const ok = await confirmDialog({ title: "Make this long cut?", text: `${err.message} You can bring it back afterwards.`, confirm: "Cut it", danger: true });
        if (ok) return cutRange(a, b, reason, true);
        return null;
      }
      toastError(err);
      return null;
    }
  }

  async function restore(edit, quiet = false) {
    return ctx.act(() => api(`${epPath(ep.id)}/edits/${encodeURIComponent(edit.id)}`, { method: "DELETE" }), quiet ? null : (r) => r.summary);
  }

  function seek(t, play = false) {
    audio.currentTime = Math.max(0, Math.min(duration - 0.05, t));
    follow(audio.currentTime);
    if (!chapterTimeTouched) {
      const input = document.getElementById("chapter-time");
      if (input && document.activeElement !== input) input.value = fmtTime(t);
    }
    if (!extraTimeTouched) {
      const input = document.getElementById("extra-time");
      if (input && document.activeElement !== input) input.value = fmtTime(t);
    }
    if (play && audio.paused) togglePlay();
    draw(true);
  }

  async function togglePlay() {
    if (!audioCtx) {
      try {
        audioCtx = new AudioContext();
        const source = audioCtx.createMediaElementSource(audio);
        analyser = audioCtx.createAnalyser();
        analyser.fftSize = 512;
        analyser.smoothingTimeConstant = 0.72;
        freq = new Uint8Array(analyser.frequencyBinCount);
        source.connect(analyser);
        analyser.connect(audioCtx.destination);
      } catch {
        analyser = null;
      }
    }
    if (audioCtx && audioCtx.state === "suspended") await audioCtx.resume().catch(() => {});
    if (audio.paused) {
      const c = skipCuts ? cutAt(audio.currentTime) : null;
      if (c) audio.currentTime = c[1];
      try {
        await audio.play();
      } catch (err) {
        toastError(new Error(`Couldn't play the audio: ${err.message}`));
      }
    } else {
      audio.pause();
    }
  }

  function setPlayIcon() {
    const playing = !audio.paused;
    playBtn.innerHTML = playing ? icons.pause : icons.play;
    playBtn.setAttribute("aria-label", playing ? "Pause" : "Play");
  }
  audio.addEventListener("play", setPlayIcon);
  audio.addEventListener("pause", setPlayIcon);
  audio.addEventListener("ended", setPlayIcon);

  // ---------------------------------------------------------- selection
  function drawSelbar() {
    clear(selbar);
    if (!selection) {
      selbar.append(h("span", {}, ep.has_transcript
        ? "Drag across the waveform to select a section, or select words in the transcript."
        : "Drag across the waveform (or the close-up under it) to select a section."));
      return;
    }
    const { a, b } = selection;
    const parts = [
      h("span", {}, "Selected ", h("b", {}, `${fmtTime(a, true)}–${fmtTime(b, true)}`), ` (${(b - a).toFixed(1)} s)`),
      h("button", { class: "btn btn-cut btn-small", onclick: () => cutRange(a, b, selection.source === "text" ? "words" : "manual") }, "Cut section"),
      h("button", { class: "btn btn-small", onclick: () => prefillChapter(a) }, "Add chapter at start"),
    ];
    if (real && PICK_WORDS[ep.show]) {
      const pick = (kind) => ctx.act(() => api(`${epPath(ep.id)}/picks`, { method: "POST", body: { kind, start: a, end: b } }),
        (r) => `${r.summary}. Now ${r.total}.`).then((r) => { if (r) { selection = null; drawSelbar(); draw(true); } });
      parts.push(
        h("button", { class: "btn btn-small", onclick: () => pick("cold_open") }, `Add to ${PICK_WORDS[ep.show].open.toLowerCase()}`),
        h("button", { class: "btn btn-small", onclick: () => pick("outro") }, "Add to sign-off"));
    }
    if ((ep.cameras || []).length) {
      const pick = h("select", { "aria-label": "Camera", style: { width: "auto", padding: "6px 8px" } }, ep.cameras.map((c) => h("option", { value: c }, c)));
      parts.push(pick, h("button", {
        class: "btn btn-small",
        onclick: () => ctx.act(() => api(`${epPath(ep.id)}/camera`, { method: "POST", body: { start: a, end: b, angle: pick.value } }), (r) => r.summary),
      }, "Show this camera"));
    }
    parts.push(h("button", { class: "btn btn-small", onclick: () => { selection = null; drawSelbar(); draw(true); } }, "Clear"));
    add(selbar, parts);
  }

  function showFloat(rect, children) {
    clear(floatBar);
    add(floatBar, children);
    floatBar.hidden = false;
    const width = floatBar.offsetWidth;
    const left = Math.min(window.scrollX + window.innerWidth - width - 12, Math.max(window.scrollX + 12, window.scrollX + rect.left + rect.width / 2 - width / 2));
    const top = window.scrollY + rect.top - floatBar.offsetHeight - 10;
    floatBar.style.left = `${left}px`;
    floatBar.style.top = `${top < window.scrollY + 8 ? window.scrollY + rect.bottom + 10 : top}px`;
  }

  function hideFloat() {
    floatBar.hidden = true;
  }

  // ------------------------------------------------------------- waveform
  // Both strips share the drawing and the mouse handling; each shows the stretch a..b.
  const spanOf = (canvas) => (canvas === waveZoom ? [zoom.a ?? zoom.start, zoom.b ?? zoom.start + zoom.span] : [0, duration]);
  const tAt = (canvas, clientX) => {
    const r = canvas.getBoundingClientRect();
    const [a, b] = spanOf(canvas);
    return Math.max(0, Math.min(duration, a + ((clientX - r.left) / r.width) * (b - a)));
  };

  for (const canvas of [wave, waveZoom]) {
    canvas.addEventListener("mousedown", (e) => {
      drag = { canvas, x: e.clientX, t: tAt(canvas, e.clientX) };
      hideFloat();
    });
  }
  window.addEventListener("mousemove", onDragMove);
  window.addEventListener("mouseup", onDragEnd);
  function onDragMove(e) {
    if (!drag) return;
    if (Math.abs(e.clientX - drag.x) < 4) return;
    const t = tAt(drag.canvas, e.clientX);
    selection = { a: Math.min(drag.t, t), b: Math.max(drag.t, t), source: "wave" };
    draw(true);
  }
  function onDragEnd(e) {
    if (!drag) return;
    const moved = Math.abs(e.clientX - drag.x) >= 4;
    if (!moved) {
      selection = null;
      seek(drag.t);
      if (drag.canvas === wave) follow(drag.t, true);
    }
    drag = null;
    drawSelbar();
    draw(true);
  }

  function sizeCanvas(canvas, cssHeight) {
    const dpr = window.devicePixelRatio || 1;
    const w = Math.max(50, canvas.clientWidth);
    if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(cssHeight * dpr)) {
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(cssHeight * dpr);
    }
    return dpr;
  }

  function paintWave(canvas, cssHeight, a, b, bars, { window: frame = null, ticks = false } = {}) {
    const dpr = sizeCanvas(canvas, cssHeight);
    const g = canvas.getContext("2d");
    const W = canvas.width;
    const H = canvas.height;
    g.clearRect(0, 0, W, H);
    const span = Math.max(0.001, b - a);
    const x = (t) => ((t - a) / span) * W;
    const now = audio.currentTime;
    const mid = H / 2;
    const n = bars.length || 1;
    const step = W / n;
    const barW = Math.max(1, step * 0.62);
    for (let i = 0; i < bars.length; i++) {
      const t = a + ((i + 0.5) / n) * span;
      const inCut = cutAt(t);
      const amp = Math.max(1 * dpr, bars[i] * (H * 0.46));
      g.fillStyle = inCut ? "rgba(236, 238, 241, 0.16)" : t <= now ? "#ffffff" : "#9aa2ae";
      g.fillRect(i * step, mid - amp, barW, amp * 2);
    }
    // Spliced-out cuts: slanted edges, like tape cut on a splicing block. A cut too narrow to see
    // at this zoom gets a thin mark instead: short and faint for a shortened pause, full height
    // for a cut someone made. Otherwise an hour of trimmed pauses looks shredded.
    for (const [ca, cb, pause] of cuts) {
      if (cb < a || ca > b) continue;
      const x0 = x(ca);
      const wide = x(cb) - x0;
      if (wide < 4 * dpr) {
        g.fillStyle = pause ? "rgba(224, 52, 43, 0.55)" : "#e0342b";
        if (pause) g.fillRect(x0, H - 10 * dpr, Math.max(1 * dpr, wide), 10 * dpr);
        else g.fillRect(x0, 0, Math.max(2 * dpr, wide), H);
        continue;
      }
      const slant = Math.min(7 * dpr, wide / 2);
      const x1 = x0 + wide;
      g.beginPath();
      g.moveTo(x0 + slant, 0);
      g.lineTo(x1 + slant, 0);
      g.lineTo(x1 - slant, H);
      g.lineTo(x0 - slant, H);
      g.closePath();
      g.fillStyle = "rgba(224, 52, 43, 0.22)";
      g.fill();
      g.save();
      g.clip();
      g.strokeStyle = "rgba(224, 52, 43, 0.45)";
      g.lineWidth = 1 * dpr;
      for (let k = x0 - H; k < x1 + H; k += 7 * dpr) {
        g.beginPath();
        g.moveTo(k, H);
        g.lineTo(k + H, 0);
        g.stroke();
      }
      g.restore();
      g.strokeStyle = "#e0342b";
      g.lineWidth = 2 * dpr;
      g.beginPath();
      g.moveTo(x0 + slant, 0);
      g.lineTo(x0 - slant, H);
      g.moveTo(x1 + slant, 0);
      g.lineTo(x1 - slant, H);
      g.stroke();
    }
    // Cold open and sign-off picks: a gold band along the bottom.
    const picks = ep.picks || {};
    g.fillStyle = "rgba(232, 184, 90, 0.9)";
    for (const p of [...(picks.cold_open || []), ...(picks.outro || [])]) {
      if (p.end < a || p.start > b) continue;
      g.fillRect(x(p.start), H - 5 * dpr, Math.max(2 * dpr, x(p.end) - x(p.start)), 5 * dpr);
    }
    // Chapter marks
    g.strokeStyle = "rgba(236, 238, 241, 0.55)";
    g.setLineDash([3 * dpr, 4 * dpr]);
    g.lineWidth = 1 * dpr;
    for (const c of ep.chapters) {
      if (c.time < a || c.time > b) continue;
      g.beginPath();
      g.moveTo(x(c.time) + 0.5, 0);
      g.lineTo(x(c.time) + 0.5, H);
      g.stroke();
    }
    g.setLineDash([]);
    // Time marks on the close-up, so precise selections are easy.
    if (ticks) {
      const every = span <= 12 ? 1 : span <= 40 ? 5 : span <= 150 ? 15 : 60;
      g.fillStyle = "rgba(236, 238, 241, 0.6)";
      g.font = `${11 * dpr}px "Atkinson Hyperlegible Next", sans-serif`;
      g.textBaseline = "top";
      for (let t = Math.ceil(a / every) * every; t <= b; t += every) {
        g.fillRect(x(t), 0, 1 * dpr, 5 * dpr);
        g.fillText(fmtTime(t), x(t) + 3 * dpr, 2 * dpr);
      }
    }
    // Where the close-up is, on the full waveform.
    if (frame) {
      g.strokeStyle = "rgba(232, 184, 90, 0.85)";
      g.lineWidth = 1.5 * dpr;
      g.strokeRect(x(frame[0]), 1 * dpr, Math.max(3 * dpr, x(frame[1]) - x(frame[0])), H - 2 * dpr);
    }
    // Selection
    if (selection && selection.b >= a && selection.a <= b) {
      g.fillStyle = "rgba(255, 255, 255, 0.16)";
      g.fillRect(x(selection.a), 0, x(selection.b) - x(selection.a), H);
      g.fillStyle = "#ffffff";
      g.fillRect(x(selection.a), 0, 1.5 * dpr, H);
      g.fillRect(x(selection.b) - 1.5 * dpr, 0, 1.5 * dpr, H);
    }
    // Playhead
    if (now >= a && now <= b) {
      const px = x(now);
      g.fillStyle = "#ffffff";
      g.fillRect(px - 1 * dpr, 0, 2 * dpr, H);
      g.beginPath();
      g.moveTo(px - 6 * dpr, 0);
      g.lineTo(px + 6 * dpr, 0);
      g.lineTo(px, 8 * dpr);
      g.closePath();
      g.fill();
    }
  }

  function drawWave() {
    const zoomed = zoomOn();
    paintWave(wave, 128, 0, duration, peaks, { window: zoomed ? [zoom.a ?? zoom.start, zoom.b ?? zoom.start + zoom.span] : null });
    if (zoomed) paintWave(waveZoom, 96, zoom.a ?? zoom.start, zoom.b ?? zoom.start + zoom.span, zoom.peaks, { ticks: true });
  }

  // ------------------------------------------------------------ zoom strip
  const zoomOn = () => duration > 180;
  function drawZoomBar() {
    clear(zoomBar);
    zoomArea.hidden = !zoomOn();
    if (!zoomOn()) return;
    zoomBar.append(h("span", { class: "lane-label" }, "Close-up"),
      ...[10, 30, 120].map((sec) => h("button", {
        class: "chip",
        "aria-pressed": zoom.span === sec ? "true" : "false",
        onclick: () => { zoom.span = sec; follow(audio.currentTime, true); drawZoomBar(); drawScriptTipSpan(); },
      }, sec < 60 ? `${sec} s` : `${sec / 60} min`)));
  }
  function drawScriptTipSpan() {
    if (real && !ep.has_transcript) drawPanel();
  }
  function follow(t, force = false) {
    if (!zoomOn()) return;
    const span = Math.min(zoom.span, duration);
    let start = zoom.start;
    if (force || t < start || t > start + span * 0.97) start = Math.max(0, Math.min(duration - span, t - span * 0.1));
    if (force || start !== zoom.start || !zoom.peaks.length) {
      zoom.start = start;
      zoom.a = start;
      zoom.b = start + span;
      loadZoom();
    }
  }
  function loadZoom() {
    clearTimeout(zoom.timer);
    zoom.timer = setTimeout(async () => {
      const a = zoom.start;
      const b = Math.min(duration, zoom.start + zoom.span);
      const width = Math.max(200, Math.min(4000, Math.round(waveZoom.clientWidth || 800)));
      const key = `${a.toFixed(2)}-${b.toFixed(2)}-${width}`;
      if (key === zoom.key) return;
      try {
        const w = await api(`${epPath(ep.id)}/waveform?buckets=${width}&start=${a.toFixed(3)}&end=${b.toFixed(3)}`);
        if (destroyed) return;
        zoom.peaks = w.peaks;
        zoom.key = key;
        zoom.a = w.start;
        zoom.b = w.end;
        draw(true);
      } catch {
        /* the next move tries again */
      }
    }, 120);
  }

  // -------------------------------------------------------------- preview
  function drawPreview(stamp) {
    const g = preview.getContext("2d");
    const W = preview.width;
    const H = preview.height;
    const t = audio.currentTime;
    const bg = g.createLinearGradient(0, 0, 0, H);
    bg.addColorStop(0, look.top);
    bg.addColorStop(1, look.bottom);
    g.fillStyle = bg;
    g.fillRect(0, 0, W, H);
    const flicker = 0.42 + 0.1 * Math.sin(stamp / 170) + 0.06 * Math.sin(stamp / 53);
    const glow = g.createRadialGradient(W / 2, H + 60, 10, W / 2, H + 60, W * 0.62);
    glow.addColorStop(0, `rgba(${look.glow}, ${flicker})`);
    glow.addColorStop(1, `rgba(${look.glow}, 0)`);
    g.fillStyle = glow;
    g.fillRect(0, 0, W, H);

    const cam = (ep.cameras || []).length ? cameraAt(t) : null;
    if (cam) {
      // A stand-in for the camera picture, below the show title and above the spectrum.
      const bx = W * 0.27;
      const by = H * 0.36;
      const bw = W * 0.46;
      const bh = H * 0.4;
      g.fillStyle = "rgba(255, 255, 255, 0.07)";
      g.strokeStyle = camColor(cam);
      g.lineWidth = 3;
      roundRect(g, bx, by, bw, bh, 14);
      g.fill();
      g.stroke();
      g.fillStyle = camColor(cam);
      g.beginPath();
      g.arc(W / 2, by + bh * 0.38, 27, 0, Math.PI * 2);
      g.fill();
      g.fillStyle = "#111";
      g.font = '700 24px "Atkinson Hyperlegible Next", sans-serif';
      g.textAlign = "center";
      g.textBaseline = "middle";
      g.fillText(initials(cam), W / 2, by + bh * 0.38 + 1);
      g.fillStyle = "#fff";
      g.font = '700 22px "Atkinson Hyperlegible Next", sans-serif';
      g.fillText(`Camera: ${cam}`, W / 2, by + bh * 0.8);
    }

    // Spectrum: live from the audio while playing, otherwise a still outline.
    const bars = 48;
    const levels = new Array(bars).fill(0);
    if (analyser && !audio.paused) {
      analyser.getByteFrequencyData(freq);
      const usable = Math.floor(freq.length * 0.55);
      for (let i = 0; i < bars; i++) {
        const lo = Math.floor(Math.pow(i / bars, 1.6) * usable);
        const hi = Math.max(lo + 1, Math.floor(Math.pow((i + 1) / bars, 1.6) * usable));
        let m = 0;
        for (let k = lo; k < hi; k++) m = Math.max(m, freq[k]);
        levels[i] = m / 255;
      }
    } else if (peaks.length) {
      // Paused: a still spectrum shaped by how loud the moment under the playhead is.
      const centre = Math.floor((t / duration) * peaks.length);
      let level = 0;
      for (let k = -6; k <= 6; k++) level = Math.max(level, peaks[Math.min(peaks.length - 1, Math.max(0, centre + k))] || 0);
      for (let i = 0; i < bars; i++) {
        const shape = Math.pow(Math.sin(((i + 0.5) / bars) * Math.PI), 0.8) * (0.62 + 0.38 * Math.abs(Math.sin(i * 1.7)));
        levels[i] = Math.min(1, (0.08 + level * 1.5) * shape);
      }
    }
    const left = W * 0.08;
    const span = W * 0.84;
    const bw = span / bars;
    const base = H * 0.93;
    g.fillStyle = look.bar;
    const tallest = cam ? H * 0.13 : H * 0.3;  // keep clear of the camera picture
    for (let i = 0; i < bars; i++) {
      const hgt = Math.max(3, levels[i] * tallest);
      roundRect(g, left + i * bw + bw * 0.18, base - hgt, bw * 0.64, hgt, 2);
      g.fill();
    }

    g.textAlign = "left";
    g.textBaseline = "alphabetic";
    g.fillStyle = "#fff";
    g.font = '800 54px "Big Shoulders Display", sans-serif';
    g.fillText(look.name, 28, 70);
    g.fillStyle = "rgba(255,255,255,0.78)";
    g.font = '400 24px "Atkinson Hyperlegible Next", sans-serif';
    g.fillText(ep.title.length > 40 ? `${ep.title.slice(0, 39)}…` : ep.title, 28, 106);

    const speaker = nowIndex >= 0 && words[nowIndex] ? words[nowIndex].speaker : null;
    if (speaker && !cam) {
      g.font = '700 26px "Atkinson Hyperlegible Next", sans-serif';
      const tw = g.measureText(speaker).width;
      g.fillStyle = "rgba(0,0,0,0.5)";
      roundRect(g, 26, H * 0.6 - 38, tw + 40, 52, 10);
      g.fill();
      g.fillStyle = "#fff";
      g.fillText(speaker, 46, H * 0.6 - 3);
    }
    if (cutAt(t)) {
      g.font = '700 22px "Atkinson Hyperlegible Next", sans-serif';
      g.textAlign = "right";
      g.fillStyle = "rgba(255, 120, 110, 0.95)";
      g.fillText("In a cut: skipped", W - 26, 44);
    }
  }

  function roundRect(g, x, y, w, hgt, r) {
    g.beginPath();
    g.moveTo(x + r, y);
    g.arcTo(x + w, y, x + w, y + hgt, r);
    g.arcTo(x + w, y + hgt, x, y + hgt, r);
    g.arcTo(x, y + hgt, x, y, r);
    g.arcTo(x, y, x + w, y, r);
    g.closePath();
  }

  const initials = (name) => name.split(/\s+/).filter(Boolean).map((p) => p[0].toUpperCase()).slice(0, 2).join("");

  // ---------------------------------------------------------------- loop
  function updateClock() {
    clock.replaceChildren(fmtTime(audio.currentTime, true), h("span", {}, ` / ${fmtTime(duration, true)}`));
    finalLen.replaceChildren(...(ep.finished_duration
      ? ["Finished file ", h("b", { title: "With the intro, breaks and ending" }, fmtTime(ep.finished_duration))]
      : ["After cuts ", h("b", {}, fmtTime(ep.final_duration || 0))]));
    if (!chapterTimeTouched && !audio.paused) {
      const input = document.getElementById("chapter-time");
      if (input && document.activeElement !== input) input.value = fmtTime(audio.currentTime);
    }
    if (!extraTimeTouched && !audio.paused) {
      const input = document.getElementById("extra-time");
      if (input && document.activeElement !== input) input.value = fmtTime(audio.currentTime);
    }
  }

  function draw(force = false) {
    const stamp = performance.now();
    if (!force && audio.paused && stamp - lastDraw < 90) return;
    lastDraw = stamp;
    drawPreview(stamp);
    drawWave();
    updateClock();
    highlightWord();
  }

  function frame() {
    if (destroyed) return;
    raf = requestAnimationFrame(frame);
    if (!audio.paused && skipCuts) {
      const c = cutAt(audio.currentTime);
      if (c) audio.currentTime = Math.min(c[1] + 0.001, duration);
    }
    if (!audio.paused) follow(audio.currentTime);
    draw();
  }

  // ------------------------------------------------------------ keyboard
  function onKey(e) {
    const tag = (e.target.tagName || "").toLowerCase();
    if (["input", "textarea", "select"].includes(tag) || e.target.isContentEditable) return;
    if (e.key === " " && tag !== "button") {
      e.preventDefault();
      togglePlay();
    } else if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
      e.preventDefault();
      const stepBy = (e.shiftKey ? 5 : 1) * (e.key === "ArrowRight" ? 1 : -1);
      seek(audio.currentTime + stepBy);
    } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") {
      e.preventDefault();
      ctx.act(() => api(`${epPath(ep.id)}/undo`, { method: "POST" }), (r) => r.summary);
    } else if (e.key === "Escape") {
      hideFloat();
      selection = null;
      drawSelbar();
      draw(true);
    }
  }
  function onDocDown(e) {
    if (!floatBar.hidden && !floatBar.contains(e.target)) hideFloat();
  }
  const onResize = () => {
    zoom.key = "";
    if (zoomOn()) loadZoom();
    draw(true);
  };

  script.addEventListener("click", onScriptClick);
  script.addEventListener("mouseup", () => setTimeout(onScriptSelect, 0));
  script.addEventListener("keyup", (e) => { if (e.shiftKey) onScriptSelect(); });
  document.addEventListener("keydown", onKey);
  document.addEventListener("mousedown", onDocDown);
  window.addEventListener("resize", onResize);

  // ---------------------------------------------------------------- init
  async function init() {
    computeCuts();
    try {
      await Promise.all([loadTranscript(), loadWave()]);
    } catch (err) {
      clear(textBox);
      textBox.append(h("p", { class: "form-error" }, `Couldn't load the review: ${err.message}`));
      return;
    }
    if (destroyed) return;
    cutSignature = JSON.stringify(cuts);
    drawScript();
    drawSide();
    drawFlags();
    drawCamLane();
    drawSelbar();
    drawZoomBar();
    follow(0, true);
    if (document.fonts && document.fonts.load) {
      Promise.all([
        document.fonts.load('800 34px "Big Shoulders Display"'),
        document.fonts.load('700 17px "Atkinson Hyperlegible Next"'),
      ]).then(() => draw(true)).catch(() => {});
    }
    raf = requestAnimationFrame(frame);
  }
  init();

  return {
    async refresh(next) {
      const rebuilt = next.duration !== ep.duration;
      ep = next;
      computeCuts();
      const signature = JSON.stringify(cuts);
      const cutsChanged = signature !== cutSignature;
      try {
        await Promise.all([cutsChanged || rebuilt ? loadTranscript() : null, rebuilt ? loadWave() : null]);
      } catch (err) {
        toastError(err);
        return;
      }
      if (rebuilt) {
        audio.src = audioUrl();
        prevCut = null;
        zoom.key = "";
        drawZoomBar();
        follow(audio.currentTime || 0, true);
      }
      if (destroyed) return;
      if (cutsChanged || rebuilt) {
        cutSignature = signature;
        drawScript();
      } else if (real) {
        drawPanel(); // the teaser / cold open and sign-off lists (drafts in the dead-air boxes are kept)
      }
      drawSide();
      drawFlags();
      drawCamLane();
      draw(true);
    },
    destroy() {
      destroyed = true;
      cancelAnimationFrame(raf);
      audio.pause();
      if (audioCtx) audioCtx.close().catch(() => {});
      floatBar.remove();
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onDocDown);
      window.removeEventListener("resize", onResize);
      window.removeEventListener("mousemove", onDragMove);
      window.removeEventListener("mouseup", onDragEnd);
    },
  };
}
