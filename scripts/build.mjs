import { webcrypto as crypto } from "node:crypto";
import {
  copyFileSync,
  existsSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { dirname, extname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const SKILLS_DIR = join(ROOT, "skills");
const SITE_DIR = join(ROOT, "site");
const DIST_DIR = join(ROOT, "dist");
const ITERATIONS = 210_000;
const TEXT_EXT = new Set([
  ".md",
  ".txt",
  ".json",
  ".yml",
  ".yaml",
  ".js",
  ".mjs",
  ".cjs",
  ".ts",
  ".tsx",
  ".css",
  ".html",
  ".xml",
  ".csv",
  ".sh",
  ".bash",
  ".py",
  ".toml",
  ".svg",
]);

loadEnv(join(ROOT, ".env"));
const password = process.env.SKILLKIT_PASSWORD;
if (!password || password === "change-me") {
  console.error(
    "Missing SKILLKIT_PASSWORD. Copy .env.example to .env, or set the GitHub Secret.",
  );
  process.exit(1);
}

const CATEGORIES = [
  "视听创作",
  "图文创作",
  "学术工作",
  "数据方法",
  "商业金融",
  "工程研发",
  "办公文档",
  "营销增长",
  "法律合规",
  "生活效率",
  "技能工具",
];
const OMIT_EXT = new Set([
  ".png",
  ".jpg",
  ".jpeg",
  ".gif",
  ".webp",
  ".zip",
  ".dll",
  ".pyc",
  ".woff",
  ".woff2",
  ".ttf",
  ".otf",
  ".mp3",
  ".mp4",
  ".pdf",
  ".xlsx",
  ".docx",
  ".pptx",
]);
const MAX_TEXT_BYTES = 200_000;

const catalog = {
  generatedAt: new Date().toISOString(),
  categories: CATEGORIES,
  skills: collectSkills(SKILLS_DIR),
};

rmSync(DIST_DIR, { recursive: true, force: true });
mkdirSync(DIST_DIR, { recursive: true });
copyDir(SITE_DIR, DIST_DIR);

const payload = await encryptJson(catalog, password);
writeFileSync(join(DIST_DIR, "payload.json"), JSON.stringify(payload));
writeFileSync(
  join(DIST_DIR, "robots.txt"),
  "User-agent: *\nDisallow: /\n",
);

console.log(
  `Built ${catalog.skills.length} skill(s) → dist/ (${payload.data.length} bytes encrypted)`,
);

function loadEnv(file) {
  if (!existsSync(file)) return;
  for (const raw of readFileSync(file, "utf8").split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const idx = line.indexOf("=");
    if (idx === -1) continue;
    const key = line.slice(0, idx).trim();
    let val = line.slice(idx + 1).trim();
    if (
      (val.startsWith('"') && val.endsWith('"')) ||
      (val.startsWith("'") && val.endsWith("'"))
    ) {
      val = val.slice(1, -1);
    }
    if (!process.env[key]) process.env[key] = val;
  }
}

function collectSkills(dir) {
  if (!existsSync(dir)) return [];
  const skills = [];
  for (const name of readdirSync(dir)) {
    const skillDir = join(dir, name);
    if (!statSync(skillDir).isDirectory()) continue;
    const skillFile = join(skillDir, "SKILL.md");
    if (!existsSync(skillFile)) continue;
    const raw = readFileSync(skillFile, "utf8");
    const { meta, body } = parseFrontmatter(raw);

    const enFile = join(skillDir, "SKILL.en.md");
    let enMeta = null;
    let enBody = null;
    if (existsSync(enFile)) {
      const enRaw = readFileSync(enFile, "utf8");
      const parsedEn = parseFrontmatter(enRaw);
      enMeta = parsedEn.meta;
      enBody = parsedEn.body;
    }

    const aliases = parseList(meta.aliases);
    const hasEn = Boolean(enBody);

    const files = walkFiles(skillDir).map((abs) => {
      const rel = relative(skillDir, abs).replaceAll("\\", "/");
      const ext = extname(abs).toLowerCase();
      const bytes = statSync(abs).size;
      const isSkill = rel === "SKILL.md" || rel === "SKILL.en.md";
      if (!isSkill && (OMIT_EXT.has(ext) || bytes > MAX_TEXT_BYTES)) {
        return { path: rel, encoding: "omit", bytes };
      }
      if (TEXT_EXT.has(ext) || ext === "" || isSkill) {
        return { path: rel, encoding: "utf8", content: readFileSync(abs, "utf8") };
      }
      return {
        path: rel,
        encoding: "base64",
        content: readFileSync(abs).toString("base64"),
      };
    });
    skills.push({
      id: name,
      name: String(meta.name || name),
      description: String(meta.description || "").trim(),
      category: String(meta.category || (enMeta && enMeta.category) || "").trim(),
      tags: parseList(meta.tags),
      aliases,
      hasEn,
      en: hasEn
        ? {
            name: String(enMeta?.name || meta.name || name),
            description: String(enMeta?.description || "").trim(),
            body: enBody,
          }
        : null,
      body,
      files,
    });
  }
  skills.sort((a, b) => a.name.localeCompare(b.name, "en"));
  return skills;
}

function parseList(value) {
  if (!value) return [];
  if (Array.isArray(value)) return value.map(String).filter(Boolean);
  return String(value)
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

function parseFrontmatter(md) {
  const m = md.match(/^---\r?\n([\s\S]*?)\r?\n---\r?\n?([\s\S]*)$/);
  if (!m) return { meta: {}, body: md };
  const meta = {};
  const lines = m[1].split(/\r?\n/);
  let currentKey = null;
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;

    if (trimmed.startsWith("- ") && currentKey) {
      if (!Array.isArray(meta[currentKey])) {
        meta[currentKey] = [];
      }
      let val = trimmed.slice(2).trim();
      if (
        (val.startsWith('"') && val.endsWith('"')) ||
        (val.startsWith("'") && val.endsWith("'"))
      ) {
        val = val.slice(1, -1);
      }
      meta[currentKey].push(val);
      continue;
    }

    const idx = line.indexOf(":");
    if (idx === -1) continue;
    const key = line.slice(0, idx).trim();
    let val = line.slice(idx + 1).trim();
    if (
      (val.startsWith('"') && val.endsWith('"')) ||
      (val.startsWith("'") && val.endsWith("'"))
    ) {
      val = val.slice(1, -1);
    }
    if (val === "") {
      currentKey = key;
      meta[key] = [];
    } else {
      currentKey = key;
      meta[key] = val;
    }
  }
  return { meta, body: m[2] };
}

function walkFiles(dir, acc = []) {
  for (const name of readdirSync(dir)) {
    if (name === ".DS_Store" || name === "Thumbs.db" || name === "__pycache__") continue;
    const abs = join(dir, name);
    const st = statSync(abs);
    if (st.isDirectory()) walkFiles(abs, acc);
    else acc.push(abs);
  }
  return acc;
}

function copyDir(from, to) {
  mkdirSync(to, { recursive: true });
  for (const name of readdirSync(from)) {
    const src = join(from, name);
    const dest = join(to, name);
    if (statSync(src).isDirectory()) copyDir(src, dest);
    else {
      mkdirSync(dirname(dest), { recursive: true });
      copyFileSync(src, dest);
    }
  }
}

async function encryptJson(obj, pwd) {
  const enc = new TextEncoder();
  const salt = crypto.getRandomValues(new Uint8Array(16));
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const material = await crypto.subtle.importKey(
    "raw",
    enc.encode(pwd),
    "PBKDF2",
    false,
    ["deriveKey"],
  );
  const key = await crypto.subtle.deriveKey(
    { name: "PBKDF2", salt, iterations: ITERATIONS, hash: "SHA-256" },
    material,
    { name: "AES-GCM", length: 256 },
    false,
    ["encrypt"],
  );
  const cipher = await crypto.subtle.encrypt(
    { name: "AES-GCM", iv },
    key,
    enc.encode(JSON.stringify(obj)),
  );
  return {
    v: 1,
    iter: ITERATIONS,
    salt: b64(salt),
    iv: b64(iv),
    data: b64(new Uint8Array(cipher)),
  };
}

function b64(bytes) {
  return Buffer.from(bytes).toString("base64");
}
