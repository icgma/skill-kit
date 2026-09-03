import { decryptPayload } from "./crypto.js";

const SESSION = "skillkit.pw";
const gate = document.getElementById("gate");
const app = document.getElementById("app");
const form = document.getElementById("gate-form");
const errorEl = document.getElementById("gate-error");
const toastEl = document.getElementById("toast");
const cardsEl = document.getElementById("cards");
const emptyEl = document.getElementById("empty");
const countEl = document.getElementById("count");
const qEl = document.getElementById("q");
const filtersEl = document.getElementById("filters");
const detailEl = document.getElementById("view-detail");

const pwInput = document.getElementById("password");
const pwToggle = document.getElementById("pw-toggle");
const rememberInput = document.getElementById("remember");

let payload = null;
let catalog = null;
let activeTag = "";
let toastTimer = 0;

const payloadP = fetch("./payload.json").then((r) => {
  if (!r.ok) throw new Error("payload missing");
  return r.json();
});

if (pwToggle && pwInput) {
  pwToggle.addEventListener("click", () => {
    const isPw = pwInput.type === "password";
    pwInput.type = isPw ? "text" : "password";
    pwToggle.setAttribute("aria-label", isPw ? "隐藏口令" : "显示口令");
    const eyeIcon = pwToggle.querySelector(".icon-eye");
    const eyeOffIcon = pwToggle.querySelector(".icon-eye-off");
    if (eyeIcon) eyeIcon.hidden = isPw;
    if (eyeOffIcon) eyeOffIcon.hidden = !isPw;
    pwInput.focus();
  });
}

if (pwInput) {
  pwInput.addEventListener("input", () => {
    errorEl.hidden = true;
    gate.classList.remove("shake");
  });
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const password = (pwInput?.value || "").trim();
  if (!password) {
    pwInput?.focus();
    return;
  }
  const btn = document.getElementById("gate-btn") || form.querySelector("button[type=submit]");
  if (btn) {
    btn.disabled = true;
    btn.textContent = "核对中…";
  }
  try {
    if (!payload) payload = await payloadP;
    const shouldRemember = rememberInput ? rememberInput.checked : true;
    await unlock(password, shouldRemember);
  } catch (err) {
    console.error("Unlock failed:", err);
    errorEl.textContent = payload ? "口令不正确" : "档案数据加载失败，请刷新重试。";
    errorEl.hidden = false;
    gate.classList.remove("shake");
    void gate.offsetWidth;
    gate.classList.add("shake");
    if (btn) {
      btn.disabled = false;
      btn.textContent = "开启档案";
    }
    pwInput?.focus();
    pwInput?.select();
  }
});

document.getElementById("lock").addEventListener("click", () => {
  localStorage.removeItem(SESSION);
  sessionStorage.removeItem(SESSION);
  catalog = null;
  location.hash = "";
  location.reload();
});

qEl.addEventListener("input", renderCatalog);
window.addEventListener("hashchange", renderRoute);
document.addEventListener("keydown", (e) => {
  if (e.key === "/" && catalog && document.activeElement !== qEl && !e.metaKey && !e.ctrlKey) {
    e.preventDefault();
    if (routeName() === "") qEl.focus();
  }
  if (e.key === "Escape" && routeName().startsWith("s/")) location.hash = "#/";
});

boot();

async function boot() {
  try {
    payload = await payloadP;
  } catch {
    errorEl.textContent = "档案数据加载失败，请检查网络或重新构建。";
    errorEl.hidden = false;
    return;
  }
  const saved = localStorage.getItem(SESSION) || sessionStorage.getItem(SESSION);
  if (saved) {
    try {
      await unlock(saved, Boolean(localStorage.getItem(SESSION)));
      return;
    } catch {
      localStorage.removeItem(SESSION);
      sessionStorage.removeItem(SESSION);
    }
  }
}

async function unlock(password, persist) {
  catalog = await decryptPayload(payload, password);
  if (persist) {
    localStorage.setItem(SESSION, password);
    sessionStorage.removeItem(SESSION);
  } else {
    sessionStorage.setItem(SESSION, password);
    localStorage.removeItem(SESSION);
  }
  const btn = document.getElementById("gate-btn") || form.querySelector("button[type=submit]");
  if (btn) {
    btn.disabled = false;
    btn.textContent = "开启档案";
  }
  gate.hidden = true;
  app.hidden = false;
  renderRoute();
}

function routeName() {
  return location.hash.replace(/^#\/?/, "");
}

function renderRoute() {
  if (!catalog) return;
  const route = routeName();
  const catalogView = document.getElementById("view-catalog");
  const guideView = document.getElementById("view-guide");
  catalogView.hidden = true;
  guideView.hidden = true;
  detailEl.hidden = true;
  setActiveNav(route);
  if (route === "guide") {
    guideView.hidden = false;
    return;
  }
  if (route.startsWith("s/")) {
    const rawId = decodeURIComponent(route.slice(2));
    let skill = catalog.skills.find((s) => s.id === rawId);
    let matchedAlias = null;
    if (!skill) {
      skill = catalog.skills.find((s) => s.aliases && s.aliases.includes(rawId));
      if (skill) matchedAlias = rawId;
    }
    if (!skill) {
      location.hash = "#/";
      toast("该技能不存在或已移除");
      catalogView.hidden = false;
      renderCatalog();
      return;
    }
    renderDetail(skill, matchedAlias);
    detailEl.hidden = false;
    return;
  }
  catalogView.hidden = false;
  renderCatalog();
}

function setActiveNav(route) {
  document.querySelectorAll(".nav a").forEach((a) => {
    const href = a.getAttribute("href");
    a.classList.toggle("active", href === "#/guide" ? route === "guide" : route === "" || route === "/");
  });
}

function renderCatalog() {
  const q = (qEl.value || "").trim().toLowerCase();
  const cats = catalog.categories?.length
    ? catalog.categories.filter((c) => catalog.skills.some((s) => s.category === c))
    : unique(catalog.skills.map((s) => s.category).filter(Boolean));
  if (cats.length) {
    filtersEl.hidden = false;
    filtersEl.innerHTML = ["全部", ...cats]
      .map((t) => `<button type="button" data-tag="${escapeAttr(t === "全部" ? "" : t)}" class="${(t === "全部" ? "" : t) === activeTag ? "on" : ""}">${escapeHtml(t)}</button>`)
      .join("");
    filtersEl.querySelectorAll("button").forEach((btn) => {
      btn.addEventListener("click", () => {
        activeTag = btn.dataset.tag;
        renderCatalog();
      });
    });
  }
  const items = catalog.skills.filter((s) => {
    if (activeTag && s.category !== activeTag) return false;
    if (!q) return true;
    const blob = [
      s.name,
      s.id,
      s.description,
      s.body,
      s.en?.name || "",
      s.en?.description || "",
      (s.aliases || []).join(" "),
      (s.tags || []).join(" "),
    ].join("\n").toLowerCase();
    return blob.includes(q);
  });
  countEl.textContent = String(catalog.skills.length);
  cardsEl.innerHTML = items
    .map((s, i) => {
      const n = s.files?.length || 1;
      const tag = s.category || (s.tags && s.tags[0]) || `${n} 个文件`;
      const biBadge = s.hasEn ? `<span class="badge-bilingual">中 / EN</span>` : "";
      return `<a class="card" href="#/s/${encodeURIComponent(s.id)}" style="animation-delay:${i * 40}ms">
        <h3>${escapeHtml(s.name)}${biBadge}</h3>
        <p>${escapeHtml(s.description || "（无描述）")}</p>
        <div class="meta">${escapeHtml(tag)}</div>
      </a>`;
    })
    .join("");
  if (!catalog.skills.length) emptyEl.textContent = "档案室还是空的。把技能放到 skills/ 后重新构建即可。";
  else if (!items.length) emptyEl.textContent = "没有匹配的技能。";
  emptyEl.hidden = items.length > 0;
}

function renderDetail(skill, matchedAlias) {
  let currentLang = "zh";

  function renderInner() {
    const isEn = currentLang === "en" && skill.hasEn;
    const currentName = isEn && skill.en?.name ? skill.en.name : skill.name;
    const currentDesc = isEn && skill.en?.description ? skill.en.description : (skill.description || "");
    const files = skill.files?.length ? skill.files : [{ path: "SKILL.md", encoding: "utf8", content: skill.body }];
    
    const targetPath = isEn ? "SKILL.en.md" : "SKILL.md";
    let activeFile = files.find((f) => f.path === targetPath) || files.find((f) => f.path === "SKILL.md") || files[0];
    let activeIndex = files.indexOf(activeFile);
    if (activeIndex === -1) activeIndex = 0;

    const copyBtnLabel = isEn ? "复制 SKILL.md (EN)" : "复制 SKILL.md";
    const aliasNotice = matchedAlias
      ? `<span class="alias-notice">（已从别名 <code>${escapeHtml(matchedAlias)}</code> 自动定位至此技能）</span>`
      : "";

    const langSwitchHtml = skill.hasEn
      ? `
      <div class="lang-switch-box">
        <div class="lang-switch" role="group" aria-label="语言切换">
          <button type="button" class="lang-btn ${currentLang === "zh" ? "on" : ""}" data-setlang="zh">中文版本</button>
          <button type="button" class="lang-btn ${currentLang === "en" ? "on" : ""}" data-setlang="en">English Version</button>
        </div>
        <span class="lang-hint">中英双语已整合</span>
      </div>`
      : "";

    detailEl.innerHTML = `
      <p><a class="back" href="#/">← 返回目录</a></p>
      <div class="detail-head">
        <div>
          <p class="eyebrow">${escapeHtml(skill.category || "Skill")}</p>
          <h2>${escapeHtml(currentName)}</h2>
          ${aliasNotice}
          <p class="desc">${escapeHtml(currentDesc)}</p>
          ${langSwitchHtml}
        </div>
      </div>
      <div class="actions">
        <button class="action primary" data-copy="skill">${copyBtnLabel}</button>
        <button class="action" data-copy="all">复制全部文本</button>
        <button class="action" data-zip>下载 ZIP</button>
      </div>
      <div class="install-box">
        <h3>安装路径 · ${escapeHtml(skill.id)}</h3>
        ${pathRow("Claude Code", `~/.claude/skills/${skill.id}/`)}
        ${pathRow("Cursor", `~/.cursor/skills/${skill.id}/`)}
        ${pathRow("Codex / pi", `~/.agents/skills/${skill.id}/`)}
        ${pathRow("当前项目", `.agents/skills/${skill.id}/`)}
        <p class="desc" style="margin:.6rem 0 .8rem">Windows 把 <code>~</code> 换成 <code>%USERPROFILE%</code>。请复制整个技能目录，不要只贴正文。</p>
      </div>
      <div class="files" id="file-tabs">
        ${files.map((f, i) => `<button type="button" class="file-btn${i === activeIndex ? " on" : ""}" data-i="${i}">${escapeHtml(f.path)}</button>`).join("")}
      </div>
      <article class="paper" id="file-paper"><pre><code>${escapeHtml(fileText(activeFile))}</code></pre></article>
    `;

    detailEl.querySelectorAll("[data-setlang]").forEach((btn) => {
      btn.addEventListener("click", () => {
        currentLang = btn.dataset.setlang;
        renderInner();
      });
    });

    detailEl.querySelector("[data-copy=skill]").addEventListener("click", () => {
      const targetMd = (isEn ? files.find((f) => f.path === "SKILL.en.md") : null) || files.find((f) => f.path === "SKILL.md") || files[0];
      copyText(fileText(targetMd));
    });

    detailEl.querySelector("[data-copy=all]").addEventListener("click", () => {
      copyText(files.filter((f) => f.encoding === "utf8").map((f) => `--- ${f.path} ---\n${f.content}`).join("\n\n"));
    });

    detailEl.querySelector("[data-zip]").addEventListener("click", () => downloadZip(skill, currentLang));

    detailEl.querySelectorAll(".path-btn").forEach((btn) => {
      btn.addEventListener("click", () => copyText(btn.dataset.path));
    });

    const paper = document.getElementById("file-paper");
    detailEl.querySelectorAll(".file-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        detailEl.querySelectorAll(".file-btn").forEach((b) => b.classList.remove("on"));
        btn.classList.add("on");
        const file = files[Number(btn.dataset.i)];
        paper.innerHTML = file.encoding === "utf8"
          ? `<pre><code>${escapeHtml(file.content)}</code></pre>`
          : `<p>大文件或二进制未打进站点包（${file.bytes || "?"} 字节）。请从仓库 <code>skills/${escapeHtml(skill.id)}</code> 复制完整目录。</p>`;
      });
    });
  }

  renderInner();
}

function pathRow(label, path) {
  return `<div class="path-row"><span>${label}</span><code>${escapeHtml(path)}</code>
    <button type="button" class="path-btn" data-path="${escapeAttr(path)}">复制</button></div>`;
}

function fileText(file) {
  if (!file) return "";
  return file.encoding === "base64" ? "" : file.content || "";
}

async function copyText(text) {
  await navigator.clipboard.writeText(text);
  toast("已复制");
}

function downloadZip(skill, lang = "zh") {
  const files = skill.files || [];
  const parts = [];
  const isEn = lang === "en" && skill.hasEn;

  for (const file of files) {
    if (file.encoding === "omit" || !file.content) continue;
    let filePath = file.path;
    let body = file.encoding === "base64" ? atob(file.content) : file.content;

    if (isEn) {
      if (file.path === "SKILL.en.md") {
        filePath = "SKILL.md";
      } else if (file.path === "SKILL.md") {
        filePath = "SKILL.zh.md";
      }
    }
    parts.push({ path: `${skill.id}/${filePath}`, body });
  }
  // Minimal uncompressed zip so we don't need a CDN library.
  const bytes = buildZip(parts);
  const blob = new Blob([bytes], { type: "application/zip" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `${skill.id}${isEn ? "-en" : ""}.zip`;
  a.click();
  URL.revokeObjectURL(a.href);
  toast(`已开始下载${isEn ? "（英文版）" : ""}`);
}

function buildZip(entries) {
  const encoder = new TextEncoder();
  const files = [];
  let offset = 0;
  const chunks = [];
  for (const entry of entries) {
    const name = encoder.encode(entry.path);
    const data = typeof entry.body === "string" ? encoder.encode(entry.body) : bytesFromBinaryString(entry.body);
    const crc = crc32(data);
    const local = new Uint8Array(30 + name.length + data.length);
    const v = new DataView(local.buffer);
    v.setUint32(0, 0x04034b50, true);
    v.setUint16(4, 20, true);
    v.setUint16(8, 0, true);
    v.setUint16(10, 0, true);
    v.setUint32(14, crc, true);
    v.setUint32(18, data.length, true);
    v.setUint32(22, data.length, true);
    v.setUint16(26, name.length, true);
    local.set(name, 30);
    local.set(data, 30 + name.length);
    chunks.push(local);
    files.push({ name, crc, size: data.length, offset });
    offset += local.length;
  }
  const central = [];
  let centralSize = 0;
  for (const f of files) {
    const rec = new Uint8Array(46 + f.name.length);
    const v = new DataView(rec.buffer);
    v.setUint32(0, 0x02014b50, true);
    v.setUint16(4, 20, true);
    v.setUint16(6, 20, true);
    v.setUint32(16, f.crc, true);
    v.setUint32(20, f.size, true);
    v.setUint32(24, f.size, true);
    v.setUint16(28, f.name.length, true);
    v.setUint32(42, f.offset, true);
    rec.set(f.name, 46);
    central.push(rec);
    centralSize += rec.length;
  }
  const end = new Uint8Array(22);
  const ev = new DataView(end.buffer);
  ev.setUint32(0, 0x06054b50, true);
  ev.setUint16(8, files.length, true);
  ev.setUint16(10, files.length, true);
  ev.setUint32(12, centralSize, true);
  ev.setUint32(16, offset, true);
  const out = new Uint8Array(offset + centralSize + 22);
  let p = 0;
  for (const c of chunks) { out.set(c, p); p += c.length; }
  for (const c of central) { out.set(c, p); p += c.length; }
  out.set(end, p);
  return out;
}

function bytesFromBinaryString(str) {
  const out = new Uint8Array(str.length);
  for (let i = 0; i < str.length; i++) out[i] = str.charCodeAt(i) & 0xff;
  return out;
}

const CRC_TABLE = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c;
  }
  return t;
})();

function crc32(bytes) {
  let c = 0xffffffff;
  for (let i = 0; i < bytes.length; i++) c = CRC_TABLE[(c ^ bytes[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

function toast(msg) {
  toastEl.textContent = msg;
  toastEl.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toastEl.hidden = true; }, 1600);
}

function unique(arr) {
  return [...new Set(arr.filter(Boolean))];
}

function escapeHtml(s) {
  return String(s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function escapeAttr(s) {
  return escapeHtml(s).replaceAll("'", "&#39;");
}
