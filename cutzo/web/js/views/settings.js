// Settings: where Cutzo keeps things, a self-test, the Claude connector, and quitting.

import { api } from "../api.js";
import { h, add, clear, toast, toastError, confirmDialog } from "../ui.js";

export async function render(main, params, ctx) {
  const info = await api("/api/health");
  const checks = h("ul", { class: "checks" });
  const runTest = async () => {
    try {
      const res = await api("/api/self-test", { method: "POST" });
      clear(checks);
      for (const c of res.checks) {
        checks.append(h("li", {},
          h("span", { class: `dot ${c.ok === true ? "ok" : c.ok === false ? "bad" : ""}`, "aria-label": c.ok === true ? "Passed" : c.ok === false ? "Failed" : "For later" }),
          h("b", {}, c.check),
          h("span", {}, c.detail)));
      }
      toast(res.summary, { kind: res.passed ? "ok" : "error" });
      ctx.changed();
    } catch (err) {
      toastError(err);
    }
  };

  add(main, [
    h("header", { class: "page-head" },
      h("div", {},
        h("h1", { class: "page-title" }, "Settings"),
        h("p", { class: "lede" }, "Where Cutzo keeps its files, a check that everything works, and the Claude connector."))),
    h("div", { class: "two-col" },
      h("section", { class: "panel" },
        h("h3", {}, "This computer"),
        h("dl", { class: "facts" },
          h("dt", {}, "Library folder"), h("dd", {}, info.library),
          h("dt", {}, "Free space there"), h("dd", {}, info.free_gb == null ? "Unknown" : `${Math.round(info.free_gb)} GB`),
          h("dt", {}, "Cutzo"), h("dd", {}, `Version ${info.version}, Phase ${info.phase}`),
          h("dt", {}, "Address"), h("dd", {}, location.origin),
          h("dt", {}, "Python"), h("dd", {}, info.python)),
        h("p", { class: "small muted", style: { marginTop: "12px" } }, "Cutzo writes only inside the library folder. Your original recordings are read, never changed.")),
      h("section", { class: "panel" },
        h("h3", {}, "Self-test"),
        h("p", { class: "muted" }, "Checks the library folder, the safety rule, free space and the Claude connector."),
        h("button", { class: "btn btn-primary", onclick: runTest }, "Run self-test"),
        checks)),
    h("section", { class: "panel" },
      h("h3", {}, "Claude connector"),
      h("p", {}, "In the Claude desktop app, Claude can use Cutzo for you. Changes show up here within a couple of seconds and in Activity, marked Claude. Try asking:"),
      h("ul", { class: "try-list" },
        h("li", {}, "“List my Cutzo episodes.”"),
        h("li", {}, "“In Spooky Sode, cut the part where they say ‘Of course it won't’.”"),
        h("li", {}, "“Read the Season Debrief transcript and suggest chapter titles.”"),
        h("li", {}, "“Build Episode 1, then export it for YouTube.”")),
      h("p", { class: "small muted" }, "If Claude says it can't find Cutzo, quit and reopen the Claude desktop app once after setup.")),
    h("div", { class: "two-col" },
      h("section", { class: "panel" },
        h("h3", {}, "Sample episodes"),
        h("p", { class: "muted" }, "Puts the three sample episodes back the way they started. Episodes you created stay."),
        h("button", {
          class: "btn",
          onclick: async () => {
            const ok = await confirmDialog({ title: "Reset the sample episodes?", text: "Your changes to the sample episodes go to the trash folder and fresh copies replace them.", confirm: "Reset samples" });
            if (!ok) return;
            try {
              await api("/api/samples/reset", { method: "POST" });
              toast("Sample episodes reset.", { kind: "ok" });
              ctx.changed();
            } catch (err) {
              toastError(err);
            }
          },
        }, "Reset sample episodes")),
      h("section", { class: "panel" },
        h("h3", {}, "Quit Cutzo"),
        h("p", { class: "muted" }, "Stops Cutzo's engine. Open it again any time from the desktop shortcut. Claude can also start it when needed."),
        h("button", {
          class: "btn btn-cut",
          onclick: async () => {
            const ok = await confirmDialog({ title: "Quit Cutzo?", text: "Anything still running stops.", confirm: "Quit Cutzo", danger: true });
            if (!ok) return;
            try {
              await api("/api/shutdown", { method: "POST" });
            } catch (err) {
              toastError(err);
              return;
            }
            ctx.closed();
            clear(main);
            main.append(h("div", { class: "closed" },
              h("h1", { class: "page-title" }, "Cutzo is closed"),
              h("p", { class: "lede" }, "You can close this tab. Open Cutzo again from the desktop shortcut.")));
          },
        }, "Quit Cutzo"))),
  ]);
  await runTestSilently();

  async function runTestSilently() {
    // Show the last results without logging a new self-test on every visit.
    clear(checks);
    checks.append(h("li", {}, h("span", { class: "dot" }), h("b", {}, "Not run yet"), h("span", { class: "muted" }, "Press Run self-test.")));
  }
}
