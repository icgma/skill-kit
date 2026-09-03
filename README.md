# 技能库 · Skill Kit

专为 AI Agent（Claude Code、Cursor、Codex、pi 等）打造的私人技能库与检索站点。

站点前端采用纯静态架构，技能正文在构建阶段经由 **AES-256-GCM** 算法加密（PBKDF2 210,000 次哈希迭代衍生密钥）。静态网页在输入正确口令前完全无法解密正文，兼顾了简易托管与私密安全。

> 🤖 **AI Agent 调度查阅**：如果你是 AI 助手，请直接查阅 [**AI_README.md**](AI_README.md)（或 [AGENTS.md](AGENTS.md)），内置「意图分类秒查路由表」与 192 份技能全景索引，可根据用户需求快速定位并按需读取执行。

---

## 🌟 核心特性

- **📚 192 份精选技能**：覆盖视听创作、图文创作、学术工作、数据方法、商业金融、工程研发、办公文档、营销增长、法律合规、生活效率、技能工具 11 大核心分类。
- **🌐 中英双语一体化**：全面整合 95 对中英文技能。双语技能在同一详情页提供一键无缝切换，并支持按所选语言单独复制 `SKILL.md` 或打包下载标准 ZIP。
- **🔐 坚固门禁与流畅交互**：
  - **内容真加密**：不仅是前端拦截，正文以密文形式存储在 `payload.json` 中，没有口令无法还原。
  - **体验增强**：支持明文/密文查看切换、口令前后空格自动过滤、即时错误消除、以及「记住此设备」本地持久化。
- **⚡ 极速离线检索**：支持按名称、用途、正文、分类与别名多维实时检索，快捷键 `/` 一键聚焦检索框，`Esc` 快速返回目录。
- **📦 标准 Agent 规范**：每个技能均符合标准 Agent 技能定义（包含 `SKILL.md` / `SKILL.en.md` 及配套流程代码），整夹下载即插即用。

---

## 🚀 本地使用

### 1. 配置共享口令

复制 `.env.example` 为 `.env`，设置专属口令：

```bash
cp .env.example .env
```

编辑 `.env`：
```ini
SKILLKIT_PASSWORD=your-shared-password
```

### 2. 构建与启动预览

```bash
# 构建：扫描 skills/ 并打包加密为 dist/
npm run build

# 启动本地预览（带防缓存优化，端口默认 4173）
npm run preview
```

浏览器访问提示地址（例如 `http://127.0.0.1:4173/`），输入口令即可开启档案库。

---

## 🛠️ 技能管理与扩展

### 增加或修改技能

每个技能对应 `skills/<skill-name>/` 目录，必须包含 `SKILL.md`：

```text
skills/my-new-skill/
├── SKILL.md          # 必须（YAML frontmatter 包含 name, description, category 等）
├── SKILL.en.md       # 可选（英文版本）
├── workflows/        # 可选（分步执行工作流）
├── references/       # 可选（参考资料与规范）
└── scripts/          # 可选（执行脚本或辅助工具）
```

添加或修改完成后，执行 `npm run build` 重新打包即可。

---

## ☁️ 部署到 GitHub Pages

仓库地址：[https://github.com/icgma/skill-kit](https://github.com/icgma/skill-kit)

### 1. 配置 Actions Secret

在仓库 **Settings → Secrets and variables → Actions** 中新增 Repository secret：
- **名称**：`SKILLKIT_PASSWORD`
- **内容**：与本地 `.env` 保持一致的解密口令

### 2. 启用 GitHub Pages

在仓库 **Settings → Pages** 中：
- **Build and deployment > Source** 选 **GitHub Actions**。

> [!NOTE]
> **关于仓库可见性与 Pages 权限**：
> - **Public 仓库**：GitHub 免费支持任意公共仓库通过 GitHub Actions 部署 Pages。即使仓库公开，**站点依然受 AES-256 口令保护**，但他人可直接在 GitHub 查看技能源码 Markdown。
> - **Private 仓库**：若保持 Private 状态，GitHub 官方要求账号需具备 **GitHub Pro** 或 **Team** 计划，方可开启 Pages 部署。

### 3. 推送发布

推送到 `main` 分支后，GitHub Actions 自动化流水线（`.github/workflows/pages.yml`）会自动完成加密构建并部署上线：

```bash
git push origin main
```

站点上线地址：`https://icgma.github.io/skill-kit/`

---

## 💻 安装技能到本地 Agent

登录站点后点击任意技能详情页即可查看安装路径，或直接复制整夹到本地：

| Agent 工具 | 个人全局路径 | 当前项目私有路径 |
| :--- | :--- | :--- |
| **Claude Code** | `~/.claude/skills/<name>/` | `.claude/skills/<name>/` |
| **Cursor** | `~/.cursor/skills/<name>/` | `.cursor/skills/<name>/` |
| **Codex / pi / 多数 Agent** | `~/.agents/skills/<name>/` | `.agents/skills/<name>/` |

> Windows 用户请将 `~` 替换为 `%USERPROFILE%`，例如 `%USERPROFILE%\.claude\skills\<name>\`。
