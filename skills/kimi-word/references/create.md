# Creating New DOCX Files

Route selection for **new** documents (for editing existing ones, see `editing.md`):

- **docx-js (preferred)** — declarative API with native wrappers for TOC, footnotes, headers/footers, shading, and positional tabs.
- **python-docx (acceptable for simple documents)** — no native footnote/TOC/equation API; see "python-docx Notes" below.

Every created file must pass the final gate before delivery:

```bash
python3 {skill_path}/scripts/verify/verify.py /absolute/path/output.docx
```

With no baseline the whole file is your product: deterministic generation
defects (a missing attribute on the default template's zoom, solid shading
without a color — spec says solid paints the *foreground*, default `auto` =
black — missing `xml:space`, literal newlines) are **auto-repaired and listed
one by one**; that pre-repair list is itself a diff to review (`--no-repair`
to only report). Remaining warnings (hand-typed `•`, CJK font chain missing
`eastAsia`, tables without widths, content wider than the container, TOC with
no visible source) do not block the exit code, but each one must be handled:
width-class findings need a render check, the rest are fixed or confirmed as
intentional. If the gate cannot run at all (`lxml` missing and not
installable), disclose that validation was skipped — there is no lighter
fallback validator (see SKILL.md).

## Build Setup

Before building, run `python3 {skill_path}/scripts/docx.py check-docx /path/with/script`. It tells you whether the `docx` npm package resolves from the script directory upward; if it reports `MISSING`, run the printed `npm install docx` command and check again. Then run `python3 {skill_path}/scripts/docx.py build`. Do not fix ESM errors by editing `package.json`; the wrapper handles ESM `.js` in CommonJS workspaces.

## Document Shape

Formal documents should feel like finished Word files, not code dumps:

- Title/cover page when the task is a report, proposal, contract, brief, paper, or client-facing deliverable.
- Visible TOC after the title/cover for multi-section documents.
- Body headings use `HeadingLevel.HEADING_1/2/3` so TOC and navigation work.
- Header carries the document title or section name; footer page numbering uses the current page number only, without labels or total pages.
- Tables have padding, stable widths, and restrained borders.
- Figures preserve aspect ratio and include captions when they support an argument.
- Output filename matches the topic and user's language; Word requests end in `.docx`.

Visual defaults:
- Use one low-saturation palette with 3-4 tiers. Avoid pure `#FF0000`, `#0000FF`, and all-blue/all-purple documents.
- Give headings more space before than after; body text needs comfortable line height; table cells need small margins.
- Chinese body text normally uses a two-character first-line indent and an explicit `eastAsia` font.

## docx-js Pitfall Table

Each row has a real failure history — these are rules, not style advice.

| # | Trap | Rule |
|---|------|------|
| 1 | Page size | Default is A4. US Letter must be explicit: `page:{size:{width:12240,height:15840}}` (DXA; 1440 = 1 inch) |
| 2 | Landscape | Pass **portrait** dimensions plus `orientation: LANDSCAPE` — the library swaps width/height internally; pre-swapping double-flips |
| 3 | Table width | Write both: `columnWidths` on the table **and** `width` on every cell, all `WidthType.DXA` (`PERCENTAGE` breaks layout in Google Docs); column widths must sum to the table width |
| 4 | Shading | `ShadingType.CLEAR` + `fill` color; `SOLID` renders as a black block |
| 5 | Lists | Use the `numbering` config (`LevelFormat.BULLET`); never type `•` as a literal character — that is not a list, and continuation-line indents break |
| 6 | Images | `ImageRun` must carry `type:` (`"png"`/`"jpg"`…); a missing type explodes only at runtime |
| 7 | Page breaks | `PageBreak` must be wrapped inside a `Paragraph`; it cannot be a top-level child |
| 8 | Line breaks | Never use `\n` (it lands in the XML verbatim and is swallowed); a new paragraph is another `Paragraph` |
| 9 | TOC | TOC only sees built-in `HeadingLevel.*`; a custom heading style must set `outlineLevel` or its entries never appear |
| 10 | Horizontal rule | Use a paragraph bottom border (`border.bottom`), not a one-row table |
| 11 | Dot leaders | Same-line "left text … right page number" uses `PositionalTab` (`RIGHT` + `LEADER` dot); padding with dots or spaces breaks when the font changes |

## Standard Pattern

Keep prose as data, then render it. This prevents JS quoting bugs in exams, contracts, bilingual reports, and quoted source text.

```js
const T = String.raw;

const sections = [
  {
    title: T`一、研究背景`,
    level: 1,
    page: 3,
    paragraphs: [
      T`用户侧储能收益来自峰谷价差、需量管理与辅助服务。`,
      T`合同文本 may define each party as a "Party" without breaking JS strings.`,
      T`试题可写：给正确读音画上"√"，用"____"画出关键句。`,
    ],
  },
];

for (const section of sections) {
  children.push(h1(section.title));
  for (const item of section.paragraphs) children.push(p(item));
}
```

`String.raw` avoids escaping ordinary quotes and backslashes. If text contains a backtick or `${`, escape it or store that text in JSON and read it at runtime. Do not globally replace quote characters in a JS file.

## Skeleton

Prefer this full-form style. `Paragraph.children` is the standard path for plain, styled, mixed, linked, and field content.

```js
import fs from "node:fs";
import path from "node:path";
import {
  AlignmentType, Document, Footer, Header, HeadingLevel, Packer, PageNumber,
  Paragraph, TextRun, convertInchesToTwip,
} from "docx";

const outputPath = process.argv[2];
if (!outputPath) throw new Error("Usage: node create.js /absolute/path/output.docx");

const outputDir = path.dirname(outputPath);
const assetDir = path.join(outputDir, "assets");
fs.mkdirSync(assetDir, { recursive: true });

// Per-channel font object. Warning: the `name` shorthand (string or {name})
// overwrites ALL four channels (ascii/hAnsi/cs/eastAsia) with that single
// value — a separate `eastAsia` key next to `name` is silently discarded.
const font = {
  ascii: "Times New Roman",
  hAnsi: "Times New Roman",
  cs: "Times New Roman",
  eastAsia: "SimSun",
};
const run = (text, options = {}) => new TextRun({ text, font, size: 24, ...options });
const para = (children, options = {}) => new Paragraph({
  spacing: { after: 160, line: 300 },
  ...options,
  children: Array.isArray(children) ? children : [children],
});

const p = (text) => para(run(text), { indent: { firstLine: convertInchesToTwip(0.33) } });
const h1 = (text) => para(run(text, { bold: true, size: 30 }), { heading: HeadingLevel.HEADING_1 });

const doc = new Document({
  features: { updateFields: false },
  sections: [{
    headers: { default: new Header({ children: [para(run("Document Title", { bold: true }), { alignment: AlignmentType.CENTER })] }) },
    footers: { default: new Footer({ children: [para(
      new TextRun({ children: [PageNumber.CURRENT] }),
      { alignment: AlignmentType.CENTER },
    )] }) },
    children: [
      h1("Document Title"),
      p("Body text."),
    ],
  }],
});

fs.writeFileSync(outputPath, await Packer.toBuffer(doc));
```

## Paragraph Few-Shot

```js
// Good: one shape for plain, styled, and mixed content.
para(run("Plain body text."));
para([run("Important: ", { bold: true }), run("details follow.")]);

// Wrong: docx-js emits both text and children, duplicating visible text.
new Paragraph({ text: "Title", children: [run("Title", { bold: true })] });
```

If a helper builds paragraph options, it should return `children`, not `text`.

```js
const heading = (text) => ({
  heading: HeadingLevel.HEADING_1,
  children: [run(text, { bold: true, size: 30 })],
});
new Paragraph(heading("Chapter 1"));
```

## Tables

Give tables stable grids. Widths are DXA everywhere (pitfall #3). For merged cells, use docx-js `columnSpan`/`rowSpan`; for `columnSpan`, set the cell width to the sum of spanned columns. For shading, use docx-js API names, not OOXML attribute names.

```js
import { ShadingType, Table, TableRow, TableCell, WidthType } from "docx";

const widths = [2400, 2400, 2400];
const cell = (text, options = {}) => new TableCell({
  children: [para(run(text))],
  margins: { top: 120, bottom: 120, left: 120, right: 120 },
  ...options,
});

new Table({
  width: { size: widths.reduce((a, b) => a + b, 0), type: WidthType.DXA },
  columnWidths: widths,
  rows: [
    new TableRow({ children: [
      cell("Header", {
        shading: { type: ShadingType.CLEAR, fill: "EEF3F6" },
        width: { size: widths[0], type: WidthType.DXA },
      }),
    ] }),
  ],
});
```

Do not write `shading: { val: "clear" }`; docx-js expects `type: ShadingType.CLEAR`.

## Table Of Contents

For formal reports, include a TOC that is visible before fields are refreshed. Entries should mirror the actual H1/H2/H3 structure 1:1. Page numbers may be estimates because Word/WPS can refresh them.

```js
import { ImportedXmlComponent } from "docx";

const xmlEscape = (value) => String(value)
  .replaceAll("&", "&amp;").replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;").replaceAll('"', "&quot;");

const toc = (entries) => {
  const cached = entries.map(({ title, level, page }) => {
    const indent = Math.max(0, level - 1) * 360;
    return `<w:p><w:pPr><w:pStyle w:val="TOC${level}"/>
      <w:tabs><w:tab w:val="right" w:leader="dot" w:pos="9000"/></w:tabs>
      <w:ind w:left="${indent}"/></w:pPr>
      <w:r><w:t>${xmlEscape(title)}</w:t></w:r><w:r><w:tab/></w:r><w:r><w:t>${page}</w:t></w:r></w:p>`;
  }).join("");

  return ImportedXmlComponent.fromXmlString(`<w:sdt xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
    <w:sdtPr><w:alias w:val="目录"/></w:sdtPr>
    <w:sdtContent>
      <w:p><w:r><w:fldChar w:fldCharType="begin" w:dirty="true"/>
        <w:instrText xml:space="preserve"> TOC \\o &quot;1-3&quot; \\h \\z \\u </w:instrText>
        <w:fldChar w:fldCharType="separate"/></w:r></w:p>
      ${cached}
      <w:p><w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>
    </w:sdtContent>
  </w:sdt>`).root[0]; // Keep .root[0]; docx-js 9.x adds a parser wrapper.
};

children.push(toc(sections.map(({ title, level, page }) => ({ title, level, page }))));
```

Avoid an empty TOC field as the only front-matter navigation. Use the helper above or the build-tested `assets/example.js`; do not invent a different TOC XML shape during document creation.

When a document contains a TOC, set `features.updateFields = true` so Word/WPS can refresh the TOC. For documents without TOC, keep `updateFields` false or omit it; current-page footers do not need open-time field updates. Avoid total page count unless the user asks for it.

Note: LibreOffice does not refresh fields — a rendered TOC page shows the placeholder cache. That is the field-cache rule at work, not a defect; do not "fix" it.

## Equations

docx-js has a full Math API (`MathRun` / `MathFraction` / `MathSuperScript` / `MathSubScript` / `MathRadical` / `MathIntegral` / `MathSum` / bracket family) — use it directly for fractions, scripts, radicals, and integrals; **do not hand-write OMML** for these. Only matrices (`m:m`) lack a wrapper and need raw XML. python-docx has no equation API.

When hand-writing OMML, read `references/omml.md` first. The order of children inside `m:oMath` is the visible formula order; do not rely on validation to reorder math XML.

## Footnotes And Citations

Use native footnotes for precise citations. `FootnoteReferenceRun` is a paragraph child; do not wrap it inside `TextRun`.

```js
const doc = new Document({
  footnotes: {
    1: { children: [para(run("Source details."))] },
  },
  sections: [{ children: [
    para([run("A precise claim"), new FootnoteReferenceRun(1), run(".")]),
  ] }],
});
```

If you ever hand-write footnote XML (python-docx has no footnote API), three invariants:

- The first two entries of the footnotes part must be id=-1 (`separator`) and id=0 (`continuationSeparator`) — missing either, no footnote renders at all.
- Fields are three-stage: Begin → FieldCode → Separate → placeholder/cache → End; the footnote reference run lives inside an existing body paragraph, never as its own paragraph.
- A placeholder TOC mirrors the real headings 1:1 (an empty TOC is useless to the reader), and `settings` gets `updateFields` so Word refreshes on open.

## Internal And External Links

For multiple internal targets, avoid `new Bookmark(...)`; current docx-js versions can reuse numeric bookmark IDs. Use explicit `BookmarkStart`/`BookmarkEnd` pairs.

```js
const targetId = 101;

para([
  new BookmarkStart("method", targetId),
  run("Method"),
  new BookmarkEnd(targetId),
], { heading: HeadingLevel.HEADING_1 });

para(new InternalHyperlink({
  anchor: "method",
  children: [run("Jump to Method", { style: "Hyperlink", color: "0563C1" })],
}));

para(new ExternalHyperlink({
  link: "https://example.com",
  children: [run("External source", { style: "Hyperlink", color: "0563C1" })],
}));
```

Keep hyperlinks as a simple paragraph or simple run group unless the surrounding mixed content has already been build-tested. If every company/name/source must be linked, audit `word/_rels/document.xml.rels` and `w:hyperlink`; visible blue text is not enough.

## Images And Charts

Derive resource paths from `outputPath`; do not hardcode container paths.

```js
const chartPath = path.join(assetDir, "chart.png");

para(new ImageRun({
  type: "png",
  data: fs.readFileSync(chartPath),
  transformation: { width: 500, height: 281 },
}));
```

Image sizing: width follows content (a wide chart may approach the text width; a pie chart needs only 10–11 cm) — do not give every figure the same width. **Height is always derived from the natural aspect ratio; never pin both width and height** — a stretched figure is only visible to the eye, so block it at the source. For cropping, crop the image file first instead of stretching the DOCX run.

Charts: matplotlib or ECharts both work; when ECharts is available in the environment the ceiling is higher (radar/heatmap/funnel/sankey — do not stop at bar/line/pie). Either way: the title states the conclusion (a judgment, not a metric name), axes carry units, two or more series get a legend, key data points are labeled directly, and library default colors are not used. "No overlap, no clipping" cannot be machine-checked — confirm visually.

## Sections

Put page size, margins, orientation, columns, section breaks, headers, and footers in section objects. Do not post-edit `w:sectPr` for normal document creation.

```js
sections: [
  { properties: { page: { margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 } } }, children: [...] },
  { properties: { type: SectionType.NEXT_PAGE, page: { size: { orientation: PageOrientation.LANDSCAPE } } }, children: [...] },
]
```

A typical formal-document section chain: cover (`titlePg` + zero margins) → TOC (normal margins) → body (header/footer references + page-number field) → back cover (zero margins). Each section ends with an in-paragraph `sectPr`; **the last section's `sectPr` hangs directly on `body`**. When hand-writing XML, `headerReference`/`footerReference` must come before `pgSz`/`pgMar` (`xsd:sequence` ordering; validation catches disorder). Headers, footers, and page numbers are not optional: anything the user or template specifies must be present and correctly placed, and long formal documents should carry page numbers even when not explicitly requested.

## Cover

A cover is allowed: cover section with zero margins + `titlePg`, background image as a `behindDoc` floating image (note: `behindDoc` may overlap headers/footers). Cover text and background must form an independent visual space: sufficient contrast, and font size/weight clearly distinct from the body — an independent visual space, not an enlarged first paragraph.

## Color

Three negative bans: pure saturated primaries (`#FF0000`/`#0000FF` level), high-saturation gradient backgrounds, and unsystematic rainbow palettes. No fixed positive palette — the palette follows the document's nature (official red-header, academic black-and-white, business brand colors); user or template specifications always win. One color system per document: the same entity keeps the same color, and charts share the body's source palette.

## python-docx Notes

- CJK fonts: `style.font.name` only writes the ascii/hAnsi channels. Set the East Asian channel via XML: `rPr.rFonts.set(qn('w:eastAsia'), '宋体')`, or Chinese falls back to the default font.
- Referenced styles must already exist in the document (built-ins use English names like `"Heading 1"`; add new styles with `add_style` first) — assigning a nonexistent style raises `KeyError`.
- The verify gate auto-repairs the python-docx default template's known quirks (zoom attributes, colorless solid shading, missing `xml:space`, literal newlines); review the repair list it prints.

## Markdown Conversion

Plain Markdown → docx: **bare pandoc** (standard `[^1]` footnote syntax becomes real Word footnotes natively). Exception: Markdown with platform citation markers (`[^1^]` style) or a companion `citation.jsonl` — bare pandoc turns those markers into plain text and loses the source mapping; use the md2docx pipeline (`references/md2docx.md`).

## Build Discipline

`build` success means the package generated and validated; it is not a substitute for task-specific checks:

- Exams with A3 landscape/two columns: inspect `w:pgSz`, `w:cols`, margins, and whether requested PDF output exists.
- Long reports: extract final text and count words/characters; check headings, TOC, footnotes, hyperlinks, captions, and chart images.
- Legal/redline documents: if the user asked for revision mode, verify real `w:ins`/`w:del`, not just colored text.

For visual QA when LibreOffice is available: convert with `soffice --headless --convert-to pdf`, then render every page yourself (managed Python has `pypdfium2` + `Pillow`: render each page to PNG, assemble a contact sheet, and flag near-blank pages). Review every page and warning before delivery. If no renderer is available, say that visual QA was unavailable; do not describe a structure-only validation as a rendered visual check.

## Delivery Checklist

- The file opens cleanly and the validation gate passes (`docx.py validate` / `verify.py` — same engine; if it could not run, that is disclosed).
- Headers, footers, and page numbers are present and correctly placed (see "Sections").
- No placeholder text remains (`[Company Name]`, `TODO`, and the like).
- Images really went in (`ImageRun` needs `type:`; confirm via render — 0 images rendered = not inserted).
- Cover text contrasts sufficiently with its background (see "Cover").
- Word-count targets phrased as "about X words" are held to ±20%; the filename follows the topic (e.g. `Energy_Report.docx`); the whole document is one language (body, charts, headers/footers).

## When Not To Create

When the task hands you a template or an existing document, **always edit the original** (see `editing.md`) — never "read it and regenerate". Regeneration loses every part you did not model: numbering definitions, theme, settings, macros. Collateral warnings will fire, but the content is already gone.
