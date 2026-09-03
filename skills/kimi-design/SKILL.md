---
name: kimi-design
description: Create design assets such as infographics, posters, social media posters, and resumes. Use this skill when the user requests visual content such as an infographic or poster. The skill uses an internally defined DSL to construct designs and supports common elements including text, shapes, images, tables, and charts. The DSL can export to image or PPTX format. Unless the user explicitly requests an infographic or poster in HTML format, this skill must be used. Direct use of image-generation tools to create infographics, posters, or similar content is strictly prohibited.
category: 数据方法
---
# Definition
kimi-design is a design-asset generation skill built by Moonshot AI. It uses a YAML-based intermediate DSL (.pptd) to represent designs and supports common elements including text, shapes, images, tables, and charts.

## The pptd format
The .pptd format is a YAML-based design syntax. It supports common design elements, and every page is self-contained—what you see is what you get. Read `reference/pptd.md` for the complete definition of this DSL.

## Companion CLI
The skill also ships with a companion CLI tool (pre-installed in the environment) for pptd validation, visual rendering of pptd files, and other operations. It also supports converting .pptx files to pptd format. Read `reference/cli.md` for the complete CLI usage instructions.

## Design asset production workflow

### step1. Read the context thoroughly
Read **all files uploaded by the user**, the provided URLs, and the pptd format guide `reference/pptd.md` to fully understand the user's requirements.

### step2. Generate the design asset based on the user's requirements

Before generating, first read `reference/pptd.md` to understand the pptd format definition and constraints, and read `reference/cli.md` to learn how to use the companion CLI.

#### Replicating a visual asset
- Analyze the image to estimate element positions, fonts, font sizes, and other properties, and **replicate it 1:1 as closely as possible**.
- When an image contains elements that are hard to replicate directly and cannot be approximated with icons or shapes (such as photos or avatars), tools such as bash or python may be used to crop or capture parts of the original image.

#### Editing a visual asset in PPTX format
- Convert the user's uploaded pptx file to .pptd format.
- Take screenshots of the converted file, then stitch and compress the screenshots for an overview. Read a few key pages individually afterward.
- Locate the pages to edit, and be careful not to affect parts outside the intended scope.
> The `kimi-slides convert` command does not provide completely lossless conversion. If the user later reports formatting errors, garbled text, or similar issues, compare the converted file with the original pptx and repair the pptd based on the comparison.

#### Generating a visual asset
Read `reference/general-poster.md` and create the asset as an editable, single-page or few-page PPTD.

### step3. Validation
1. Use the `kimi-slides check` command to validate and repair the generated file over multiple rounds.
  - Interpret the results returned by `check` carefully. Some warnings may be produced by heuristic calculations and may contain errors; use the actual design as the basis for judgment.
  - Ignore intentional design choices such as bleed effects, deliberately overlapping text, or content extending beyond the page margins.
2. If the user reports an issue that `kimi-slides check` did not catch, you may use `kimi-slides screenshot` to take screenshots, then refine the affected pages and run multiple rounds of validation and repair.
> The `screenshot` command uses simulated rendering, which may differ from the actual result in the editor. Follow the semantics defined in pptd.md.

### step4. Delivery
The main entry file must be named with a `.kanva.pptd` suffix (e.g., `poster.kanva.pptd`); the `xxx.pptd` example names in `pptd.md` should be adapted to end with `.kanva.pptd`. Use kimi_ref to deliver the .kanva.pptd file to the user. The path must point directly to the .kanva.pptd file.

Tell the user how to use the .kanva.pptd file as follows:
1. When you output kimi_ref, the frontend renders it as a clickable card. The user can click it to open the preview and editing page for the .kanva.pptd file.
2. On the editing page, the user can edit the file manually by modifying elements or adding and deleting pages. The user can also add comments, which are passed to you as system_reminder messages for further revisions.
3. On the editing page, the user can click the "Export" button to export the pptd file in a suitable format. Both image and pptx formats are supported.

## Important notes
1. Artifact location: the .kanva.pptd file must be written to `mnt/agents/output/<folder_name>`; writing it to directories such as `/tmp` or `/work` is strictly prohibited.
2. If the user asks for revisions over multiple turns, output kimi_ref in every turn. **If kimi_ref is omitted, the user cannot see the asset card and therefore cannot edit, preview, or export the asset.**
3. The skill includes built-in version management. When the output kimi_ref points to the same .kanva.pptd file, the system automatically keeps each version, and the user can preview or restore earlier versions in the frontend. **Do not move the .kanva.pptd file or its dependencies unless necessary.**
