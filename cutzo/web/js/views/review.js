// Review: the deck (preview, player, waveform with spliced-out cuts) above the
// transcript, which you mark up like a script: select words to cut them, click
// struck words to bring them back.

import { api, epPath } from "../api.js";
import { h, add, clear, icons, fmtTime, fmtDur, plural, toast, toastError, confirmDialog } from "../ui.js";

const REASON = { "dead air": "Dead air", "filler word": "Filler word", words: "Words", manual: "Section", chosen: "Chosen" };
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
  const chapterDraft = { time: null, title: "" };
  let cutSignature = "";
  const look = SHOW_LOOK[ep.show] || SHOW_LOOK["the-dig"];

  // ------------------------------------------------------------ layout
  const audio = h("audio", { preload: "auto", src: `${epPath(ep.id)}/audio?d=${ep.duration}` });
  const preview = h("canvas", { width: 640, height: 360, role: "img", "aria-label": "Sample preview of the finished episode" });
  const playBtn = h("button", { class: "play", "aria-label": "Play", svg: icons.play, onclick: togglePlay });
  const clock = h("div", { class: "clock" });
  const finalLen = h("div", { class: "final-len" });
  const skip = h("input", { type: "checkbox", checked: true, onchange: (e) => { skipCuts = e.target.checked; } });
  const flags = h("div", { class: "flags" });
  const wave = h("canvas", { class: "wave", tabindex: "0", role: "img", "aria-label": "Waveform. Click to move the playhead; drag to select a section." });
  const camLabel = h("div", { class: "lane-label" }, "Camera");
  const camLane = h("div", { class: "cam-lane" });
  const selbar = h("div", { class: "selbar" });
  const deck = h("section", { class: "deck", "aria-label": "Player" },
    h("div", {}, h("div", { class: "preview" }, preview),
      h("p", { class: "preview-note" }, "Sample preview: the real video renders arrive in Phase 3.")),
    h("div", {},
      h("div", { class: "transport" }, playBtn, clock, finalLen,
        h("label", { class: "switch" }, skip, "Skip cuts while playing")),
      h("div", { class: "wave-area" }, flags, wave, camLabel, camLane),
      selbar),
    audio);
  const script = h("section", { class: "script", "aria-label": "Transcript" }, h("p", { class: "muted" }, "Loading the transcript…"));
  const side = h("aside", { class: "side", "aria-label": "Cuts and chapters" });
  add(container, [deck, h("div", { class: "review" }, script, side)]);
  const floatBar = h("div", { class: "float-bar", role: "toolbar", "aria-label": "Selection", hidden: true });
  document.body.append(floatBar);

  // -------------------------------------------------------------- data
  function computeCuts() {
    const ranges = ep.edits.filter((e) => e.type === "cut").map((e) => [e.start, e.end]).sort((a, b) => a[0] - b[0]);
    const merged = [];
    for (const [a, b] of ranges) {
      if (merged.length && a <= merged[merged.length - 1][1] + 1e-6) merged[merged.length - 1][1] = Math.max(merged[merged.length - 1][1], b);
      else merged.push([a, b]);
    }
    cuts = merged;
  }

  const cutAt = (t) => cuts.find(([a, b]) => t >= a && t < b) || null;

  async function loadTranscript() {
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
    const nowCut = new Set(words.filter((w) => w && w.cut).map((w) => w.i));
    clear(script);
    wordEls = new Map();
    add(script, h("div", { class: "script-head" },
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
      script.append(h("div", { class: "para" },
        h("div", { class: "who-col" },
          h("span", { class: "who-name" }, p.speaker),
          h("button", { class: "para-time", title: "Play from here", onclick: () => seek(p.start, true) }, fmtTime(p.start))),
        text));
    }
    script.append(h("p", { class: "script-tip" },
      "Keys: Space plays or pauses, the arrow keys move a second (Shift for five), Ctrl+Z undoes the last change.",
      ep.sample ? " This transcript is sample text." : ""));
    prevCut = nowCut;
    nowIndex = -1;
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
    const removed = Math.max(0, (ep.duration || 0) - (ep.final_duration || 0));
    const cutsSec = h("section", {},
      h("div", { class: "side-head" }, h("h3", {}, "Cuts"), h("span", {}, `${plural(cutEdits.length, "cut")}, ${fmtDur(removed)} out`)));
    if (!cutEdits.length) {
      cutsSec.append(h("p", { class: "muted small" }, "No cuts yet. Select words in the transcript, or drag across the waveform."));
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
    const chapters = h("section", { id: "chapters" },
      h("div", { class: "side-head" }, h("h3", {}, "Chapters"), h("span", {}, plural(ep.chapters.length, "chapter"))),
      ep.chapters.length
        ? h("ul", { class: "items" }, ep.chapters.map((c) => h("li", { class: "item" },
            h("div", { class: "item-main", onclick: () => seek(c.time, true), title: "Play from this chapter" },
              h("div", {}, h("span", { class: "item-time" }, c.time_text), " ", h("span", { class: "chapter-title" }, c.title)),
              h("div", { class: "item-text" }, `At ${c.output_text} in the finished file`),
              h("div", { class: "item-tags" },
                c.voiced_title ? h("span", { class: "tag tag-ok", title: `Plays ${c.voiced_title}` }, "Voiced title plays") : null,
                h("span", { class: c.by === "claude" ? "tag tag-claude" : "tag" }, BY[c.by] || c.by))),
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
          const res = await ctx.act(() => api(`${epPath(ep.id)}/chapters`, { method: "POST", body: { time: timeInput.value.trim(), title: titleInput.value.trim() } }),
            (r) => r.voiced_title ? `${r.summary}. ${r.voiced_title} plays there.` : r.summary);
          if (res) {
            chapterTimeTouched = false;
            chapterDraft.time = null;
            chapterDraft.title = "";
            const title = document.getElementById("chapter-title");
            if (title) title.value = "";
          }
        },
      }, timeInput, titleInput, h("button", { class: "btn", type: "submit" }, "Add")),
      h("p", { class: "small muted", style: { marginTop: "6px" } }, "The time fills in from the playhead. Times are in the original recording."));

    const yt = h("textarea", { class: "copybox", readonly: true, "aria-label": "YouTube chapters" });
    yt.value = ep.youtube_chapters || "";
    const youtube = h("section", {},
      h("div", { class: "side-head" }, h("h3", {}, "For YouTube"),
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
      h("p", { class: "small muted" }, "Paste into the video description. Times match the finished file."),
      yt,
      ep.chapter_warnings && ep.chapter_warnings.length ? h("ul", { class: "warn-list" }, ep.chapter_warnings.map((w) => h("li", {}, w))) : null);
    add(side, [cutsSec, chapters, youtube]);
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
    if (!chapterTimeTouched) {
      const input = document.getElementById("chapter-time");
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
      selbar.append(h("span", {}, "Drag across the waveform to select a section, or select words in the transcript."));
      return;
    }
    const { a, b } = selection;
    const parts = [
      h("span", {}, "Selected ", h("b", {}, `${fmtTime(a, true)}–${fmtTime(b, true)}`), ` (${(b - a).toFixed(1)} s)`),
      h("button", { class: "btn btn-cut btn-small", onclick: () => cutRange(a, b, selection.source === "text" ? "words" : "manual") }, "Cut section"),
      h("button", { class: "btn btn-small", onclick: () => prefillChapter(a) }, "Add chapter at start"),
    ];
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
  const tAt = (clientX) => {
    const r = wave.getBoundingClientRect();
    return Math.max(0, Math.min(duration, ((clientX - r.left) / r.width) * duration));
  };

  wave.addEventListener("mousedown", (e) => {
    drag = { x: e.clientX, t: tAt(e.clientX) };
    hideFloat();
  });
  window.addEventListener("mousemove", onDragMove);
  window.addEventListener("mouseup", onDragEnd);
  function onDragMove(e) {
    if (!drag) return;
    if (Math.abs(e.clientX - drag.x) < 4) return;
    const t = tAt(e.clientX);
    selection = { a: Math.min(drag.t, t), b: Math.max(drag.t, t), source: "wave" };
    draw(true);
  }
  function onDragEnd(e) {
    if (!drag) return;
    const moved = Math.abs(e.clientX - drag.x) >= 4;
    if (!moved) {
      selection = null;
      seek(drag.t);
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

  function drawWave() {
    const dpr = sizeCanvas(wave, 128);
    const g = wave.getContext("2d");
    const W = wave.width;
    const H = wave.height;
    g.clearRect(0, 0, W, H);
    const x = (t) => (t / duration) * W;
    const now = audio.currentTime;
    const mid = H / 2;
    const n = peaks.length || 1;
    const step = W / n;
    const barW = Math.max(1, step * 0.62);
    for (let i = 0; i < peaks.length; i++) {
      const t = ((i + 0.5) / n) * duration;
      const inCut = cutAt(t);
      const amp = Math.max(1 * dpr, peaks[i] * (H * 0.46));
      g.fillStyle = inCut ? "rgba(236, 238, 241, 0.16)" : t <= now ? "#ffffff" : "#9aa2ae";
      g.fillRect(i * step, mid - amp, barW, amp * 2);
    }
    // Spliced-out cuts: slanted edges, like tape cut on a splicing block.
    const slant = 7 * dpr;
    for (const [a, b] of cuts) {
      const x0 = x(a);
      const x1 = Math.max(x(b), x0 + 2 * dpr);
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
    // Chapter marks
    g.strokeStyle = "rgba(236, 238, 241, 0.55)";
    g.setLineDash([3 * dpr, 4 * dpr]);
    g.lineWidth = 1 * dpr;
    for (const c of ep.chapters) {
      g.beginPath();
      g.moveTo(x(c.time) + 0.5, 0);
      g.lineTo(x(c.time) + 0.5, H);
      g.stroke();
    }
    g.setLineDash([]);
    // Selection
    if (selection) {
      g.fillStyle = "rgba(255, 255, 255, 0.16)";
      g.fillRect(x(selection.a), 0, x(selection.b) - x(selection.a), H);
      g.fillStyle = "#ffffff";
      g.fillRect(x(selection.a), 0, 1.5 * dpr, H);
      g.fillRect(x(selection.b) - 1.5 * dpr, 0, 1.5 * dpr, H);
    }
    // Playhead
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
    finalLen.replaceChildren("After cuts ", h("b", {}, fmtTime(ep.final_duration || 0)));
    if (!chapterTimeTouched && !audio.paused) {
      const input = document.getElementById("chapter-time");
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
  const onResize = () => draw(true);

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
      clear(script);
      script.append(h("p", { class: "form-error" }, `Couldn't load the review: ${err.message}`));
      return;
    }
    if (destroyed) return;
    cutSignature = JSON.stringify(cuts);
    drawScript();
    drawSide();
    drawFlags();
    drawCamLane();
    drawSelbar();
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
        audio.src = `${epPath(ep.id)}/audio?d=${ep.duration}`;
        prevCut = null;
      }
      if (destroyed) return;
      if (cutsChanged || rebuilt) {
        cutSignature = signature;
        drawScript();
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
