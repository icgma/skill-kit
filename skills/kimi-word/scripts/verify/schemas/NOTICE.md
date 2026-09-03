# Third-Party Notices — `scripts/verify/schemas/`

This directory contains XSD schema files used only by the offline document
validator. They are **not** original Moonshot AI materials and are **not**
covered by the Moonshot AI Skill License (`skills/kimi-word/LICENSE.txt`). Each
remains under its own terms as listed below.

## `mce/mc.xsd` — Apache License 2.0

A modified version of `xsd/mce/markup-compatibility-2006-MINIMAL.xsd` from the
**docx4j** project (https://github.com/plutext/docx4j), as stated in the
file's own header comment. docx4j is licensed under the Apache License,
Version 2.0 — the full license text is included alongside this file in
`LICENSE-Apache-2.0.txt`. The modification notice required by §4(b) of that
license is preserved in the file header.

## `iso29500/` — ECMA-376 / ISO/IEC 29500 standard schemas

Schema definitions of the Office Open XML file formats (ECMA-376, also
published as ISO/IEC 29500), organized following the layout of the docx4j
project's `xsd/iso29500` directory. These files express the public standard's
schema definitions and are distributed by ECMA International with the standard.

## `opc/` — ECMA-376 Part 2 (Open Packaging Conventions) schemas

Schema definitions of the OPC package parts from the same standard,
distributed with the ECMA-376 specification.

## `microsoft/` — Microsoft Office extension schemas

WordprocessingML extension schemas (Word 2010/2012/2015/2016/2018/2020
namespaces) describing Microsoft Office extensions to the standard, as
published by Microsoft for interoperability.

## `iso29500/xml.xsd` — W3C

The W3C schema document for the XML namespace
(http://www.w3.org/XML/1998/namespace), © W3C, redistributed per the file's
own documentation block.
