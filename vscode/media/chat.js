// The chat panel. Text from people and documents is inserted with textContent; the only HTML
// set directly is an answer rendered (and escaped) by the extension.
(function () {
  const vscode = acquireVsCodeApi();
  const log = document.getElementById("log");
  const form = document.getElementById("ask");
  const q = document.getElementById("q");
  const send = document.getElementById("send");
  const stop = document.getElementById("stop");
  const who = document.getElementById("who");
  let pending = null; // the answer being written
  let signedIn = false;

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function scroll() {
    log.scrollTop = log.scrollHeight;
  }

  function welcome(server) {
    log.textContent = "";
    const box = el("div", "welcome");
    if (signedIn) {
      box.append(el("p", "", "Ask anything your company's documents answer. Every answer cites its sources; only documents you can open are used."));
      box.append(el("p", "hint", "Tip: select code, right-click → “Ask VaultMind about this”."));
    } else {
      box.append(el("p", "", "Sign in to ask " + (server || "VaultMind") + "."));
      const b = el("button", "primary", "Sign in");
      b.addEventListener("click", () => vscode.postMessage({ type: "signIn" }));
      box.append(b);
    }
    log.append(box);
  }

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const text = q.value.trim();
    if (!text) return;
    vscode.postMessage({ type: "ask", text });
    q.value = "";
  });
  q.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      form.requestSubmit();
    }
  });
  stop.addEventListener("click", () => vscode.postMessage({ type: "stop" }));
  log.addEventListener("click", (e) => {
    const t = e.target.closest("[data-doc]");
    if (!t) return;
    e.preventDefault();
    vscode.postMessage({ type: "open", id: Number(t.dataset.doc), p: t.dataset.p ? Number(t.dataset.p) : undefined });
  });

  window.addEventListener("message", ({ data: m }) => {
    if (m.type === "state") {
      signedIn = m.signedIn;
      who.textContent = m.signedIn ? m.name : "";
      q.disabled = send.disabled = !m.signedIn;
      if (!log.querySelector(".msg")) welcome(m.server);
    } else if (m.type === "clear") {
      welcome();
    } else if (m.type === "user") {
      log.querySelector(".welcome")?.remove();
      log.append(el("div", "msg user", m.text));
      pending = el("div", "msg answer writing");
      pending.append(el("p", "muted", "Searching your documents…"));
      log.append(pending);
      scroll();
    } else if (m.type === "delta" && pending) {
      if (!pending.dataset.text) pending.textContent = "";
      pending.dataset.text = (pending.dataset.text || "") + m.text;
      pending.textContent = pending.dataset.text.replace(/\s?\[D\d+(?:\.\d+)?\]/g, "");
      scroll();
    } else if (m.type === "answer" && pending) {
      const box = pending;
      pending = null;
      box.className = "msg answer";
      box.removeAttribute("data-text");
      box.textContent = "";
      box.append(el("div", "label", m.label + (m.scope ? " · " + m.scope : "")));
      const body = el("div", "body");
      body.innerHTML = m.html; // escaped by the extension
      box.append(body);
      if (m.notEnough) box.append(el("p", "warn", "The documents didn't hold enough to answer this fully."));
      if (m.sources.length) {
        const list = el("ol", "sources");
        for (const s of m.sources) {
          const li = el("li");
          const a = el("a", "", s.title);
          a.href = "#";
          a.dataset.doc = s.id;
          if (s.passages.length) a.dataset.p = s.passages[0];
          li.value = s.n;
          li.append(a);
          list.append(li);
        }
        box.append(el("div", "sources-title", "Sources"), list);
      }
      if (m.locked.length) {
        const p = el("p", "locked", "You can't open these, but they may help (request access on the website): ");
        m.locked.forEach((l, i) => {
          const a = el("a", "", l.title);
          a.href = "#";
          a.dataset.doc = l.id;
          p.append(a);
          if (i < m.locked.length - 1) p.append(", ");
        });
        box.append(p);
      }
      scroll();
    } else if (m.type === "error") {
      if (pending) {
        pending.className = "msg error";
        pending.textContent = m.text;
        pending = null;
      } else log.append(el("div", "msg error", m.text));
      scroll();
    } else if (m.type === "busy") {
      send.disabled = m.busy || !signedIn;
      stop.hidden = !m.busy;
    }
  });
  welcome();
})();
