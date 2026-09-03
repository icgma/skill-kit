---
name: kimi-design
description: Create design assets such as infographics, posters, social media posters, and resumes. Use this skill when the user requests visual content such as an infographic or poster. The skill uses an internally defined DSL to construct designs and supports common elements including text, shapes, images, tables, and charts. The DSL can export to image or PPTX format. Unless the user explicitly requests an infographic or poster in HTML format, this skill must be used. Direct use of image-generation tools to create infographics, posters, or similar content is strictly prohibited.
category: 数据方法
---
# Definition
kimi-design is a design-asset generation skill built by Moonshot AI. It uses a YAML-based intermediate DSL (.pptd) to represent designs and supports common elements including text, shapes, images, tables, and charts.

## The pptd format
The .pptd format is a YAML-based design syntax. It supports common design elements, and every page is self-contained—what you see is what you get. `reference/pptd.md` is the complete definition of this DSL.

## Companion CLI
The skill also ships with a companion CLI tool (pre-installed in the environment) for pptd validation, visual rendering of pptd files, and other operations. It also supports converting .pptx files to pptd format. Read `reference/cli.md` for the complete CLI usage instructions.

## Design asset production workflow

### step1. Read the context thoroughly
Read **all files uploaded by the user**, the provided URLs, and the pptd format guide `reference/pptd.md` to fully understand the user's requirements.
- While reading, develop a complete understanding of `pptd.md`'s capabilities. For requirements within its scope (such as custom shapes, text, and Google Fonts), prefer the native definitions in `pptd.md` to keep the output editable and modifiable. For requirements beyond `pptd.md`'s scope (such as background removal, image color-curve adjustment, or complex shapes), rely on other capabilities first (such as bash, Python, image search, or image generation); never downgrade the result directly.
- When the asset requires large numbers of repeated, regular elements (such as tiled textures or pixel blocks), you may use Python or similar tools to compute and generate element positions, reducing repetitive output and improving precision.

### step2. Understand the user's requirement
Determine the requirement from the context:
- Create a design asset: create a new design
- Edit a design asset: edit a user-uploaded design in pptx format (local modifications, single-page beautification, etc.)
- Replicate a design asset: replicate a bitmap such as an image or PDF into a vector, editable format


#### Clarification and follow-up questions
When the user's requirement falls into any of the following cases, resolve it with the ask tool:
1. Ambiguous requirements
- The user's intent is unclear or hard to understand
- The files/URLs provided by the user are inaccessible
2. Conflicting intent
- The user's intents contradict each other. For example:
  * Requesting a style completely inconsistent with the design system (e.g., using a McKinsey style while requiring large areas of whitespace on the page)
  * Requesting both "make 10 pages" and "deliver 30+ pages"
3. Unable to judge the requirement on your own
- When the purpose, design direction, input type, page count, etc. are hard to distinguish or determine by yourself

### step3. Generate based on the user's requirement

Before generating, make sure you have read `reference/pptd.md` and `reference/cli.md` to understand the format definition and CLI usage.

#### Generating a visual asset

**Establish the frame of reference first.** You must read the `reference/design.md` design guide in full, and read the corresponding profile under `reference/profiles/` for the task's genre. These are not optional references—every later decision must be traceable to them.

**Then position the value orientation and complete the corresponding thinking.** Every genre belongs to one of three categories:

- **Visual expression** (art posters, visual creation, illustration and characters): concept, emotion, and visual language are the value themselves. Let the idea come through space and form, color and material, proportion and rhythm, composition and balance, and visual hierarchy; establish a dominant visual metaphor causally tied to the subject, letting information be expressed through visual weight and spatial tension rather than explanation.
- **Communication and conversion** (social media content, campaign posters and key visuals, product e-commerce, brand identity): visuals serve recognition, memory, and action. Define the viewer's first-glance focus, memory point, and action guidance; authentic assets and brand materials are inviolable hard constraints, and the product, logo, and key information must always be the most prominent presence on the canvas.
- **Information explanation** (infographics, diagrammatic structures, document editorial, UI design): accuracy, hierarchy, and reading efficiency come first. Establish the grouping order of information and an at-a-glance focus; distill abstract concepts into visualizable core relationships (contrast, process, causality, containment, evolution...), letting the structure speak for itself; high text density is the norm, not a defect.

**Complete these decisions before starting** (no need to write them into a document, but think each one through): what problem to solve; the core message and its hierarchy; the dominant visual concept and style direction, and what explicitly not to do; colors, fonts, grid, and the scale of the main subject; which images are needed, where they come from, and how to treat them. Information should be conveyed through space, form, color, and composition as much as possible, not explained through text. If any decision would hold equally for a different subject, it is not specific enough—make it specific before starting. There is only one bar for completeness: the final asset should look like the work of a top-tier author in the field—meticulously crafted and able to withstand detail-by-detail review, not the default appearance of a tool.

#### Imagery and visual materials
1. Images are an effective way to enrich visual impact; use suitable images to enrich the page, aid understanding, or support decisions.
2. Images are used to show concrete objects, explain content, provide evidence, or establish a scene. Logos, icons, decorative textures, and very small thumbnails do not count as substantive imagery.
3. Image priority: images provided by the user; images from official websites, official reports, and trusted sources; images obtained through search that are directly relevant to the content; images generated for conceptual expression or atmosphere.
4. After deciding which images to use, finish searching, generating, and downloading them in one batch first, then design the page around the image aspect ratios. Save images to the media directory, keep them sharp, and never stretch or distort them.
5. When analytical, technical, or academic design assets involve products, experiments, interfaces, cases, or on-site materials, use the corresponding evidence images. Do not let every page degenerate into text, color blocks, and shapes.
6. Do not add irrelevant images to meet a quantity target. Every image must be directly related to the page's conclusion or communicative purpose.

##### Content guidelines
1. Language style: unless the user explicitly requests otherwise, overly abstract expressions and uncommon metaphors are strictly prohibited.
- Do not overuse metaphors, slogans, or abstract buzzwords (e.g., "distribution", "N-step argument", "understand it in one page", "closed loop", "hands-on", "verify", "break down", "second-class citizen", "poison pill", "wall clock").
- Do not use typical AI phrasings, such as "not X, but Y", "X is Y", rhetorical "why / on what grounds / how" constructions, "core conclusion", or "N fronts / N paths".
- Do not use overly colloquial expressions (e.g., "where to aim the ammo", "thing number N", "can't pick accurately", "can't be used as X").

#### Replicating a visual asset
- Analyze the image to estimate element positions, fonts, font sizes, and other properties, and **replicate it 1:1 as closely as possible**.
- For parts that are hard to discern, use gridlines, local zoom-ins, or similar methods to improve understanding.
- Replicate simple content in the image with elements; icons may be approximated with icons provided by fontawesome; elements that cannot be approximated with icons/shapes (such as photos or avatars) may be cropped or sliced from the original image using bash, python, or similar tools, and added as image elements.
- Inserting the user's uploaded bitmap image directly into the page as a single element is strictly prohibited.

#### Editing a visual asset in PPTX format
- Convert the user's uploaded pptx file to pptd format.
- For the converted screenshots, first stitch and compress the pages with Python for an overview; read key pages that are hard to discern individually afterward.
- Determine the editing tasks (pages, elements, actions) and execute them. Repetitive edits (such as adjusting a logo's position across multiple pages) may be executed in batch with bash commands like grep and cat. (Probe with a dry run first; batch operations are strictly prohibited without full confidence.)
> `kimi-slides convert` is not a lossless conversion. If the user reports layout errors or style errors outside the edited area, unpack and parse the original pptx file for a more detailed style judgment, then adjust based on the findings.

### step4. Validation
1. Use the `kimi-slides check` command to validate and repair the generated file over multiple rounds.
  - Distinguish carefully among the results returned by `check`. Some warnings are produced by heuristic calculations and may be inaccurate; use the actual design as the basis for judgment.
  - Ignore by-design choices such as bleed effects, deliberately overlapping text, or content extending beyond the page margins.
2. If the user reports an issue that `kimi-slides check` did not catch, use `kimi-slides screenshot` to take screenshots, then refine the affected pages and run multiple rounds of validation and repair.
> The `screenshot` command uses simulated rendering, which may differ from the actual rendering in the editor. Follow the semantics defined in pptd.md.

### step5. Delivery
The main entry file must be named with a `.kanva.pptd` suffix (e.g., `poster.kanva.pptd`); the `xxx.pptd` example names in `pptd.md` should be adapted to end with `.kanva.pptd`. Use kimi_ref to deliver the .kanva.pptd file to the user. The path must point directly to the .kanva.pptd file; **converting to pptx format for delivery is strictly prohibited**. The user's only way to use the .kanva.pptd file is to click the card below to enter the editor, where they can preview, edit, and export to pptx format. Guide the user to use the .kanva.pptd file as follows:
1. When you output kimi_ref, the frontend renders it as a clickable card. The user can click it to open the preview/editing page for the .kanva.pptd file.
2. The user can edit manually on the editing page (modify elements, add or delete pages, etc.), and can also add comments on the editing page (comments are passed to you as system_reminder messages for further revision rounds).
3. The user can click the "Export" button on the editing page to export the pptd file in a suitable format. Both image and pptx formats are supported.

## Important notes
1. Artifact location: the .kanva.pptd file must be written to `mnt/agents/output/<folder_name>`; writing it to directories such as `/tmp` or `/work` is strictly prohibited.
2. Animation usage: use animations only when the user explicitly requests them, or when the asset is clearly intended for live presentation or screening and animation clearly benefits step-by-step revelation, process demonstration, causal explanation, pacing control, visual impact, or brand narrative; assets meant for reading, printing, or sending/browsing get no animations by default.
3. Speaker notes (`note`) usage: add them only when the user explicitly requests them. Adding them is prohibited in all other cases.
4. Parallel tool calls: parallelize tool calls as much as possible during production—call the `write_file tool` multiple times per turn to write more pages and reduce steps.
5. If the user asks for revisions over multiple turns, output kimi_ref in every turn. **Omitting kimi_ref makes the asset card invisible to the user, making it impossible to edit, preview, or export the asset!**
6. The skill includes built-in version management: when the output kimi_ref points to the same .kanva.pptd file, the system automatically keeps each version, and the user can switch versions in the frontend to preview or restore them. **Do not move the .kanva.pptd file or its dependencies unless necessary!**
