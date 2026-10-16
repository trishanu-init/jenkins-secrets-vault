(() => {
  const { team, csrf } = window.VAULT;
  const base = `/${encodeURIComponent(team)}/api`;
  let knownPaths = [];

  const api = async (method, path, body) => {
    const url = path === null ? `${base}/files` : `${base}/file?path=${encodeURIComponent(path)}`;
    const res = await fetch(url, {
      method,
      credentials: "same-origin",
      headers: { "X-CSRF-Token": csrf, "Content-Type": "text/plain; charset=utf-8" },
      body,
    });
    if (!res.ok) {
      let msg = `${res.status} ${res.statusText}`;
      try { msg = (await res.json()).error || msg; } catch (_) {}
      throw new Error(msg);
    }
    return res;
  };

  // ---- file list ----
  const tbody = document.querySelector("#file-table tbody");
  const empty = document.getElementById("empty");

  async function loadList() {
    const { files } = await (await api("GET", null)).json();
    knownPaths = files.map((f) => f.path);
    tbody.replaceChildren();
    empty.hidden = files.length > 0;
    for (const f of files) {
      const tr = document.createElement("tr");
      const pathTd = document.createElement("td");
      pathTd.innerHTML = "<code></code>";
      pathTd.firstChild.textContent = f.path;
      const dateTd = document.createElement("td");
      dateTd.textContent = new Date(f.updated_at).toLocaleString();
      dateTd.className = "muted";
      const actions = document.createElement("td");
      actions.className = "actions";
      actions.append(
        button("View / edit", async () => openEditor(f.path, await (await api("GET", f.path)).text())),
        button("Download", async () => download(f.path, await (await api("GET", f.path)).text())),
        button("Delete", async () => {
          if (!confirm(`Delete ${f.path}?`)) return;
          await api("DELETE", f.path);
          await loadList();
        }, "danger"),
      );
      tr.append(pathTd, dateTd, actions);
      tbody.append(tr);
    }
  }

  function button(label, onClick, cls = "ghost") {
    const b = document.createElement("button");
    b.type = "button";
    b.className = cls;
    b.textContent = label;
    b.addEventListener("click", () => onClick().catch((e) => alert(e.message)));
    return b;
  }

  function download(path, text) {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
    a.download = path.split("/").pop();
    a.click();
    URL.revokeObjectURL(a.href);
  }

  // ---- editors ----
  const editors = document.getElementById("editors");
  const tpl = document.getElementById("editor-tpl");

  function openEditor(path = "", content = "") {
    const existing = [...editors.querySelectorAll(".editor")].find((e) => path && e.querySelector(".path").value === path);
    if (existing) existing.remove();

    const node = tpl.content.firstElementChild.cloneNode(true);
    const pathInput = node.querySelector(".path");
    const textarea = node.querySelector(".content");
    const status = node.querySelector(".status");
    pathInput.value = path;
    textarea.value = content;
    textarea.rows = Math.min(Math.max(content.split("\n").length + 1, 8), 30);

    node.querySelector(".close").addEventListener("click", () => node.remove());
    node.querySelector(".save").addEventListener("click", async () => {
      const p = pathInput.value.trim();
      if (!p) { status.textContent = "Enter a path first."; return; }
      if (knownPaths.includes(p) && p !== path && !confirm(`${p} already exists. Overwrite?`)) return;
      status.textContent = "Saving…";
      try {
        await api("PUT", p, textarea.value);
        path = p;
        status.textContent = `Saved ${p} at ${new Date().toLocaleTimeString()}`;
        await loadList();
      } catch (e) {
        status.textContent = `Error: ${e.message}`;
      }
    });

    editors.prepend(node);
    (path ? textarea : pathInput).focus();
  }

  // Guess a stored path for a dropped file: reuse an existing path with the same file name.
  function guessPath(name) {
    const matches = knownPaths.filter((p) => p.split("/").pop() === name);
    return matches.length === 1 ? matches[0] : name;
  }

  async function openFiles(fileList) {
    for (const file of fileList) {
      openEditor(guessPath(file.name), await file.text());
    }
  }

  const zone = document.getElementById("dropzone");
  ["dragenter", "dragover"].forEach((ev) => zone.addEventListener(ev, (e) => {
    e.preventDefault();
    zone.classList.add("over");
  }));
  ["dragleave", "drop"].forEach((ev) => zone.addEventListener(ev, () => zone.classList.remove("over")));
  zone.addEventListener("drop", (e) => {
    e.preventDefault();
    if (e.dataTransfer.files.length) {
      openFiles(e.dataTransfer.files);
    } else {
      const text = e.dataTransfer.getData("text/plain");
      if (text) openEditor("", text);
    }
  });
  // Stop the browser from opening files dropped outside the zone.
  window.addEventListener("dragover", (e) => e.preventDefault());
  window.addEventListener("drop", (e) => e.preventDefault());

  document.getElementById("file-input").addEventListener("change", (e) => {
    openFiles(e.target.files);
    e.target.value = "";
  });
  document.getElementById("new-file").addEventListener("click", () => openEditor());

  // ---- one-time token box ----
  const teamUrl = document.getElementById("team-url");
  if (teamUrl) teamUrl.textContent = `${location.origin}/${team}`;
  document.querySelectorAll("[data-copy]").forEach((b) => b.addEventListener("click", async () => {
    const input = document.querySelector(b.dataset.copy);
    input.select();
    try { await navigator.clipboard.writeText(input.value); } catch (_) { document.execCommand("copy"); }
    b.textContent = "Copied";
  }));

  loadList().catch((e) => alert(e.message));
})();
