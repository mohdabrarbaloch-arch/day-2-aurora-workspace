/* ==========================================================================
   Aurora — frontend controller
   Vanilla JS, no build step, no inline handlers (so the CSP in
   middleware.py can stay strict).
   ========================================================================== */

(() => {
  "use strict";

  const API = "/api";

  /* ---------------------------------------------------------------- state */
  const state = {
    token: localStorage.getItem("aurora.token") || "",
    user: null,
    workspaces: [],
    activeId: null,
    assets: [],
    streamed: false,
  };

  const $ = (sel) => document.querySelector(sel);
  const el = {
    viewAuth: $("#view-auth"),
    viewApp: $("#view-app"),
    formAuth: $("#form-auth"),
    email: $("#auth-email"),
    password: $("#auth-password"),
    name: $("#auth-name"),
    fieldName: $("#field-name"),
    authError: $("#auth-error"),
    authSubmit: $("#auth-submit"),
    tabs: document.querySelectorAll(".tab"),
    workspaceList: $("#workspace-list"),
    assetList: $("#asset-list"),
    assetCount: $("#asset-count"),
    dropzone: $("#dropzone"),
    fileInput: $("#file-input"),
    messages: $("#messages"),
    chatTitle: $("#chat-title"),
    formAsk: $("#form-ask"),
    askInput: $("#ask-input"),
    askSubmit: $("#ask-submit"),
    providerBadge: $("#provider-badge"),
    btnLogout: $("#btn-logout"),
    btnNewWorkspace: $("#btn-new-workspace"),
    toast: $("#toast"),
  };

  let authMode = "login";
  let toastTimer = null;

  /* ---------------------------------------------------------------- utils */
  function toast(message, isError = false) {
    el.toast.textContent = message;
    el.toast.hidden = false;
    el.toast.classList.toggle("is-error", isError);
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.toast.hidden = true; }, 4200);
  }

  function bytes(n) {
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
    return `${(n / 1024 / 1024).toFixed(1)} MB`;
  }

  async function api(path, options = {}) {
    const headers = Object.assign({}, options.headers || {});
    if (state.token) headers.Authorization = `Bearer ${state.token}`;
    if (options.body && !(options.body instanceof FormData)) {
      headers["Content-Type"] = "application/json";
    }

    const res = await fetch(`${API}${path}`, Object.assign({}, options, { headers }));

    if (res.status === 401 && state.token) {
      signOut();
      throw new Error("Your session expired. Please sign in again.");
    }

    if (res.status === 204) return null;

    const isJson = (res.headers.get("content-type") || "").includes("application/json");
    const payload = isJson ? await res.json() : await res.text();

    if (!res.ok) {
      const detail =
        (payload && payload.detail) ||
        (typeof payload === "string" && payload) ||
        `Request failed (${res.status})`;
      throw new Error(Array.isArray(detail) ? detail[0]?.msg || "Invalid input" : detail);
    }
    return payload;
  }

  /* ----------------------------------------------------------------- auth */
  el.tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      authMode = tab.dataset.tab;
      el.tabs.forEach((t) => {
        const on = t === tab;
        t.classList.toggle("is-active", on);
        t.setAttribute("aria-selected", String(on));
      });
      const registering = authMode === "register";
      el.fieldName.hidden = !registering;
      el.authSubmit.textContent = registering ? "Create account" : "Sign in";
      el.password.autocomplete = registering ? "new-password" : "current-password";
      el.authError.hidden = true;
    });
  });

  el.formAuth.addEventListener("submit", async (event) => {
    event.preventDefault();
    el.authError.hidden = true;
    el.authSubmit.disabled = true;
    el.authSubmit.textContent = "Please wait…";

    try {
      const body =
        authMode === "register"
          ? {
              email: el.email.value.trim(),
              password: el.password.value,
              display_name: el.name.value.trim(),
            }
          : { email: el.email.value.trim(), password: el.password.value };

      const data = await api(`/auth/${authMode}`, {
        method: "POST",
        body: JSON.stringify(body),
      });

      state.token = data.access_token;
      state.user = data.user;
      localStorage.setItem("aurora.token", state.token);
      el.password.value = "";
      await enterApp();
    } catch (error) {
      el.authError.textContent = error.message;
      el.authError.hidden = false;
    } finally {
      el.authSubmit.disabled = false;
      el.authSubmit.textContent = authMode === "register" ? "Create account" : "Sign in";
    }
  });

  function signOut() {
    state.token = "";
    state.user = null;
    state.workspaces = [];
    state.activeId = null;
    state.assets = [];
    localStorage.removeItem("aurora.token");
    el.viewApp.hidden = true;
    el.viewAuth.hidden = false;
  }

  el.btnLogout.addEventListener("click", signOut);

  /* ----------------------------------------------------------- boot logic */
  async function enterApp() {
    el.viewAuth.hidden = true;
    el.viewApp.hidden = false;

    try {
      const health = await api("/health");
      el.providerBadge.textContent = health.provider;
      el.providerBadge.title =
        health.provider === "gemini"
          ? "Answers rewritten by Gemini from retrieved passages"
          : "Built-in retrieval engine — offline, no API key";
    } catch (_) { /* health is informational only */ }

    await loadWorkspaces();

    if (!state.workspaces.length) {
      try {
        const ws = await api("/workspaces", {
          method: "POST",
          body: JSON.stringify({ title: "My first workspace" }),
        });
        state.workspaces = [ws];
      } catch (error) {
        toast(error.message, true);
      }
    }

    if (state.workspaces.length) {
      await selectWorkspace(state.workspaces[0].id);
    } else {
      renderWorkspaces();
      renderAssets();
    }
  }

  /* ---------------------------------------------------------- workspaces */
  async function loadWorkspaces() {
    state.workspaces = await api("/workspaces");
    renderWorkspaces();
  }

  function renderWorkspaces() {
    el.workspaceList.replaceChildren();

    if (!state.workspaces.length) {
      const li = document.createElement("li");
      li.className = "ws-item";
      li.textContent = "No workspaces yet";
      el.workspaceList.append(li);
      return;
    }

    state.workspaces.forEach((ws) => {
      const li = document.createElement("li");
      li.className = "ws-item" + (ws.id === state.activeId ? " is-active" : "");
      li.tabIndex = 0;
      li.setAttribute("role", "button");

      const label = document.createElement("span");
      label.textContent = ws.title;
      li.append(label);

      const count = document.createElement("em");
      count.className = "ws-n";
      count.textContent = `${ws.asset_count}`;
      li.append(count);

      const open = () => selectWorkspace(ws.id);
      li.addEventListener("click", open);
      li.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); }
      });

      el.workspaceList.append(li);
    });
  }

  async function selectWorkspace(id) {
    state.activeId = id;
    const ws = state.workspaces.find((w) => w.id === id);
    el.chatTitle.textContent = ws ? ws.title : "Conversation";
    el.dropzone.setAttribute("aria-disabled", "false");
    renderWorkspaces();

    await Promise.all([loadAssets(), loadMessages()]);
  }

  el.btnNewWorkspace.addEventListener("click", async () => {
    const title = window.prompt("Name this workspace", "New workspace");
    if (!title || !title.trim()) return;
    try {
      const ws = await api("/workspaces", {
        method: "POST",
        body: JSON.stringify({ title: title.trim() }),
      });
      state.workspaces.unshift(ws);
      await selectWorkspace(ws.id);
      toast("Workspace created");
    } catch (error) {
      toast(error.message, true);
    }
  });

  /* -------------------------------------------------------------- assets */
  async function loadAssets() {
    if (!state.activeId) return;
    try {
      state.assets = await api(`/workspaces/${state.activeId}/assets`);
      renderAssets();
    } catch (error) {
      toast(error.message, true);
    }
  }

  function assetGlyph(asset) {
    if (asset.kind === "pdf") return "PDF";
    if (asset.kind === "text") return "TXT";
    return "IMG";
  }

  function renderAssets() {
    el.assetList.replaceChildren();
    el.assetCount.textContent = String(state.assets.length);

    state.assets.forEach((asset) => {
      const li = document.createElement("li");
      li.className = "asset-item";

      // Images get a real thumbnail served from the API; other types get a
      // monospace type badge. No placeholder art either way.
      let thumb;
      if (asset.kind === "image") {
        thumb = document.createElement("img");
        thumb.className = "asset-thumb";
        thumb.alt = "";
        thumb.loading = "lazy";
        thumb.src = `${API}/workspaces/${state.activeId}/assets/${asset.id}/file`;
      } else {
        thumb = document.createElement("span");
        thumb.className = "asset-thumb";
        thumb.textContent = assetGlyph(asset);
      }
      li.append(thumb);

      const meta = document.createElement("div");
      meta.className = "asset-meta";

      const name = document.createElement("span");
      name.className = "asset-name";
      name.textContent = asset.filename;
      name.title = asset.filename;
      meta.append(name);

      const sub = document.createElement("span");
      sub.className = "asset-sub";
      const bits = [bytes(asset.size_bytes)];
      if (asset.kind === "pdf") bits.push(`${asset.page_count}p`);
      if (!asset.has_text) bits.push("no text");
      sub.textContent = bits.join(" · ");
      meta.append(sub);

      // Show the sampled palette for images — it is real extracted data, and it
      // makes the multimodal side of the product visible at a glance.
      const palette = (asset.meta && asset.meta.palette) || [];
      if (palette.length) {
        const wrap = document.createElement("span");
        wrap.className = "palette";
        palette.slice(0, 4).forEach((hex) => {
          const dot = document.createElement("i");
          dot.style.background = hex;
          dot.title = hex;
          wrap.append(dot);
        });
        sub.append(wrap);
      }

      li.append(meta);

      const del = document.createElement("button");
      del.className = "asset-del";
      del.type = "button";
      del.setAttribute("aria-label", `Remove ${asset.filename}`);
      del.innerHTML =
        '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" ' +
        'stroke-width="2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>';
      del.addEventListener("click", () => removeAsset(asset.id));
      li.append(del);

      el.assetList.append(li);
    });
  }

  async function removeAsset(id) {
    try {
      await api(`/workspaces/${state.activeId}/assets/${id}`, { method: "DELETE" });
      state.assets = state.assets.filter((a) => a.id !== id);
      renderAssets();
    } catch (error) {
      toast(error.message, true);
    }
  }

  /* ------------------------------------------------------------- uploads */
  el.dropzone.addEventListener("click", () => el.fileInput.click());
  el.dropzone.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); el.fileInput.click(); }
  });
  el.fileInput.addEventListener("change", () => {
    if (el.fileInput.files.length) uploadFiles(el.fileInput.files);
    el.fileInput.value = "";
  });

  ["dragenter", "dragover"].forEach((type) => {
    el.dropzone.addEventListener(type, (e) => {
      e.preventDefault();
      el.dropzone.classList.add("is-drag");
    });
  });
  ["dragleave", "drop"].forEach((type) => {
    el.dropzone.addEventListener(type, (e) => {
      e.preventDefault();
      el.dropzone.classList.remove("is-drag");
    });
  });
  el.dropzone.addEventListener("drop", (e) => {
    if (e.dataTransfer.files.length) uploadFiles(e.dataTransfer.files);
  });

  async function uploadFiles(files) {
    if (!state.activeId) {
      toast("Pick a workspace first", true);
      return;
    }

    const list = Array.from(files);
    let ok = 0;

    for (const file of list) {
      const form = new FormData();
      form.append("file", file);
      try {
        el.dropzone.classList.add("is-busy");
        const asset = await api(`/workspaces/${state.activeId}/assets`, {
          method: "POST",
          body: form,
        });
        state.assets.push(asset);
        ok += 1;
        renderAssets();
      } catch (error) {
        toast(`${file.name}: ${error.message}`, true);
      } finally {
        el.dropzone.classList.remove("is-busy");
      }
    }

    if (ok) {
      toast(ok === 1 ? "Added 1 file" : `Added ${ok} files`);
      await loadWorkspaces();
    }
  }

  /* ------------------------------------------------------------ messages */
  function clearEmptyState() {
    const empty = el.messages.querySelector(".empty-state");
    if (empty) empty.remove();
  }

  function addUserMessage(text) {
    clearEmptyState();
    const wrap = document.createElement("div");
    wrap.className = "msg msg-user";

    const role = document.createElement("div");
    role.className = "msg-role";
    role.textContent = "You";
    wrap.append(role);

    const body = document.createElement("div");
    body.className = "msg-body";
    body.textContent = text;
    wrap.append(body);

    el.messages.append(wrap);
    scrollToEnd();
  }

  function addAssistantShell() {
    const wrap = document.createElement("div");
    wrap.className = "msg msg-assistant";

    const role = document.createElement("div");
    role.className = "msg-role";
    role.textContent = "Aurora";
    wrap.append(role);

    const body = document.createElement("div");
    body.className = "msg-body";
    body.textContent = "";
    wrap.append(body);

    const caret = document.createElement("span");
    caret.className = "caret";
    body.append(caret);

    const citations = document.createElement("div");
    citations.className = "citations";
    wrap.append(citations);

    el.messages.append(wrap);
    scrollToEnd();
    return { wrap, body, citations, caret };
  }

  function scrollToEnd() {
    el.messages.scrollTop = el.messages.scrollHeight;
  }

  async function loadMessages() {
    if (!state.activeId) return;
    el.messages.replaceChildren();
    try {
      const items = await api(`/workspaces/${state.activeId}/messages`);
      if (!items.length) {
        renderEmptyState();
        return;
      }
      items.forEach(renderStoredMessage);
      scrollToEnd();
    } catch (error) {
      toast(error.message, true);
    }
  }

  function renderEmptyState() {
    const box = document.createElement("div");
    box.className = "empty-state";

    const mark = document.createElement("div");
    mark.className = "empty-mark";
    box.append(mark);

    const title = document.createElement("h3");
    title.textContent = "Ask across everything at once";
    box.append(title);

    const copy = document.createElement("p");
    copy.textContent =
      "Upload a screenshot, a PDF and a text file — then ask one question that spans all three.";
    box.append(copy);

    el.messages.append(box);
  }

  function renderStoredMessage(message) {
    clearEmptyState();
    if (message.role === "user") {
      addUserMessage(message.content);
      return;
    }

    const wrap = document.createElement("div");
    wrap.className = "msg msg-assistant";

    const role = document.createElement("div");
    role.className = "msg-role";
    role.textContent = "Aurora";
    wrap.append(role);

    const body = document.createElement("div");
    body.className = "msg-body";
    body.textContent = message.content;
    wrap.append(body);

    if (!message.grounded) {
      const note = document.createElement("div");
      note.className = "msg-note";
      note.textContent = "Not found in your uploaded files — nothing was invented to fill the gap.";
      wrap.append(note);
    }

    if (message.citations && message.citations.length) {
      wrap.append(buildCitations(message.citations));
    }

    el.messages.append(wrap);
  }

  function buildCitations(citations) {
    const holder = document.createElement("div");
    holder.className = "citations";

    citations.forEach((cite, index) => {
      const card = document.createElement("div");
      card.className = "citation";

      const head = document.createElement("div");
      head.className = "citation-head";

      const marker = document.createElement("strong");
      marker.textContent = `[${index + 1}] ${cite.filename}`;
      marker.title = cite.filename;
      head.append(marker);

      if (cite.page != null) {
        const page = document.createElement("span");
        page.className = "citation-page";
        page.textContent = `p.${cite.page}`;
        head.append(page);
      }

      const score = document.createElement("span");
      score.className = "citation-score";
      score.textContent = cite.score.toFixed(2);
      score.title = "BM25 relevance score";
      head.append(score);

      card.append(head);

      const snippet = document.createElement("p");
      snippet.className = "citation-snippet";
      snippet.textContent = cite.snippet;
      card.append(snippet);

      holder.append(card);
    });

    return holder;
  }

  /* -------------------------------------------------------- ask + stream */
  el.askInput.addEventListener("input", () => {
    el.askInput.style.height = "auto";
    el.askInput.style.height = `${Math.min(el.askInput.scrollHeight, 132)}px`;
  });

  el.askInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      el.formAsk.requestSubmit();
    }
  });

  el.formAsk.addEventListener("submit", async (event) => {
    event.preventDefault();
    const question = el.askInput.value.trim();
    if (!question || !state.activeId) return;

    if (!state.assets.length) {
      toast("Add a file first — then I can answer from it.", true);
      return;
    }

    el.askInput.value = "";
    el.askInput.style.height = "auto";
    el.askSubmit.disabled = true;

    addUserMessage(question);
    const shell = addAssistantShell();

    try {
      await streamAnswer(question, shell);
    } catch (error) {
      shell.caret.remove();
      shell.body.textContent = `Something went wrong: ${error.message}`;
      toast(error.message, true);
    } finally {
      el.askSubmit.disabled = false;
      el.askInput.focus();
    }
  });

  async function streamAnswer(question, shell) {
    const res = await fetch(`${API}/workspaces/${state.activeId}/messages`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${state.token}`,
      },
      body: JSON.stringify({ question }),
    });

    if (!res.ok) {
      let detail = `Request failed (${res.status})`;
      try {
        const payload = await res.json();
        if (payload && payload.detail) detail = payload.detail;
      } catch (_) { /* non-JSON error body */ }
      throw new Error(detail);
    }

    if (!res.body) throw new Error("This browser cannot stream responses.");

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // SSE frames are separated by a blank line; a trailing partial frame
      // stays in the buffer until more bytes arrive.
      const frames = buffer.split("\n\n");
      buffer = frames.pop() || "";

      for (const frame of frames) {
        if (!frame.trim()) continue;
        let event = "message";
        let data = "";
        frame.split("\n").forEach((line) => {
          if (line.startsWith("event:")) event = line.slice(6).trim();
          else if (line.startsWith("data:")) data += line.slice(5).trim();
        });
        if (!data) continue;

        let payload;
        try { payload = JSON.parse(data); } catch (_) { continue; }

        if (event === "delta") {
          shell.caret.remove();
          shell.body.append(document.createTextNode(payload.text));
          scrollToEnd();
        } else if (event === "done") {
          shell.caret.remove();
          if (!payload.grounded) {
            const note = document.createElement("div");
            note.className = "msg-note";
            note.textContent =
              "Not found in your uploaded files — nothing was invented to fill the gap.";
            shell.wrap.insertBefore(note, shell.citations);
          }
          shell.citations.replaceChildren();
          if (payload.citations && payload.citations.length) {
            shell.citations.append(buildCitations(payload.citations));
          }
          scrollToEnd();
        } else if (event === "error") {
          shell.caret.remove();
          throw new Error(payload.message || "The provider failed.");
        }
      }
    }

    shell.caret.remove();
    await loadWorkspaces();
  }

  /* -------------------------------------------------------------- startup */
  // Re-attach the stream caret if the server produced no text at all (e.g. the
  // connection closed before the first delta) so the bubble is never blank.
  function finaliseEmptyBubble(body) {
    if (!body.textContent.trim()) {
      body.textContent = "No answer was returned. Try rephrasing the question.";
    }
  }

  (async function boot() {
    if (!state.token) {
      el.viewAuth.hidden = false;
      el.viewApp.hidden = true;
      return;
    }
    try {
      state.user = await api("/auth/me");
      await enterApp();
    } catch (_) {
      signOut();
    }
  })();

  window.addEventListener("beforeunload", () => {
    document.querySelectorAll(".msg-body").forEach(finaliseEmptyBubble);
  });
})();
