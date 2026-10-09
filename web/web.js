// In-browser backend for the GitHub Pages build: runs the same Python engine as the desktop app,
// under Pyodide, entirely in this tab. Loaded before ui.html's own script, which picks up
// window.srtBackend instead of talking to the local server.
(() => {
  let py, api;
  const $ = s => document.querySelector(s);
  const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
  const json = s => JSON.parse(s);

  // Python errors arrive with a full traceback; the last line is the useful part.
  const clean = e => {
    const lines = String(e && e.message || e).trim().split("\n");
    return new Error(lines[lines.length - 1].replace(/^\w+Error: /, ""));
  };
  const call = async fn => { try { return await fn(); } catch (e) { throw clean(e); } };
  const persist = () => new Promise(res => py.FS.syncfs(false, () => res()));

  function panel(html) {
    let el = $("#web-panel");
    if (!el) {
      el = document.createElement("section");
      el.className = "panel";
      el.id = "web-panel";
      $("#extras").append(el);
    }
    el.innerHTML = html;
  }

  async function showSaved() {
    const s = json(api.settings_summary());
    panel(`<div class="row" style="margin-top:0;justify-content:space-between">
      <span>Saved in this browser: <b>${s.words}</b> accepted word${s.words === 1 ? "" : "s"},
        <b>${s.files}</b> finished file${s.files === 1 ? "" : "s"}.
        <span class="muted">Export them as a backup, or to move to another browser or computer.</span></span>
      <span><button class="small" id="exp">Export</button> <button class="small" id="imp">Import</button>
        <button class="small" id="forget">Forget</button>
        <input type="file" id="impfile" accept=".json,.txt" hidden></span></div>`);
    $("#exp").onclick = () => {
      const url = URL.createObjectURL(new Blob([api.export_settings()], {type: "application/json"}));
      Object.assign(document.createElement("a"), {href: url, download: "srt-scanner-settings.json"}).click();
    };
    $("#imp").onclick = () => $("#impfile").click();
    $("#impfile").onchange = async e => {
      const f = e.target.files[0];
      if (!f) return;
      try {
        const r = json(await call(async () => api.import_settings(f.name, await f.text())));
        await persist();
        await showSaved();
        alert(`Imported ${r.words} new word${r.words === 1 ? "" : "s"} and ${r.files} finished file${r.files === 1 ? "" : "s"}.`);
      } catch (err) { alert("Couldn't import that file: " + err.message); }
    };
    $("#forget").onclick = async () => {
      if (!confirm("Forget every accepted word and finished file saved in this browser?")) return;
      api.forget_settings();
      await persist();
      showSaved();
    };
  }

  async function boot() {
    panel(`<span class="muted">Loading the checker. The first visit downloads about 12 MB; after that it loads
      from your browser's cache and works offline.</span>`);
    py = await loadPyodide({indexURL: new URL("pyodide/", location.href).href});
    py.FS.mkdirTree("/data");
    py.FS.mount(py.FS.filesystems.IDBFS, {}, "/data");
    await new Promise((res, rej) => py.FS.syncfs(true, err => err ? rej(err) : res()));
    const bundle = await (await fetch("app.zip")).arrayBuffer();
    py.unpackArchive(bundle, "zip", {extractDir: "/srt"});
    py.runPython("import sys; sys.path.insert(0, '/srt')");
    api = py.pyimport("app.webapi");
    api.start();
    await persist();
    await showSaved();
  }

  const ready = boot().catch(e => {
    panel(`<p class="err">The checker couldn't load: ${esc(e.message || e)}</p>
      <p class="muted">Reload the page to try again. It needs a modern browser (Chrome, Edge, Firefox or Safari from the last few years).</p>`);
    throw e;
  });

  window.srtBackend = {
    kind: "web",
    ready,
    newRun: () => call(() => api.new_run()),
    upload: (run, name, buf) => call(() => json(api.upload(run, name, new Uint8Array(buf)))),
    check: (run, recheck) => call(() => json(api.check(run, recheck))),
    apply: (run, picked, edits) => call(async () => {
      const r = json(api.apply(run, JSON.stringify(picked), JSON.stringify(edits)));
      await persist();
      showSaved();
      return r;
    }),
    acceptWord: word => call(async () => { api.accept_word(word); await persist(); showSaved(); }),
    downloadUrl: async (run, file) => {
      const path = api.file_path(run, file);
      if (!path) return "#";
      const type = file.endsWith(".zip") ? "application/zip" : "text/plain;charset=utf-8";
      return URL.createObjectURL(new Blob([py.FS.readFile(path)], {type}));
    },
  };

  if ("serviceWorker" in navigator && window.isSecureContext) {
    // A new version installs in the background; switch to it at once unless a check is on screen.
    const hadController = !!navigator.serviceWorker.controller;
    navigator.serviceWorker.addEventListener("controllerchange", () => {
      if (!hadController) return;
      if (!document.querySelector("section[data-file]")) return location.reload();
      const note = document.createElement("div");
      note.className = "note";
      note.innerHTML = 'A newer version of SRT Scanner is ready. <a href="">Reload</a> to use it (finish this batch first).';
      $("#extras").prepend(note);
    });
    navigator.serviceWorker.register("sw.js").then(reg => reg.update()).catch(() => {});
  }
})();
