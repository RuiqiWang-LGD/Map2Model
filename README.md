<p align="center">
  <img src="assets/brand/mark.svg" alt="Map2Model logo" height="112">
</p>

<h1 align="center">Map2Model</h1>

<p align="center">
  <strong>Turn site imagery and CAD into editable design bases.</strong><br>
  <sub>An AI skill for architecture, landscape and planning.</sub>
</p>

<p align="center"><strong>English</strong> | <a href="README_CN.md">简体中文</a></p>

<p align="center">
  <img src="assets/brand/badge-1.svg" alt="version: v0.9.11" height="22">
  <img src="assets/brand/badge-2.svg" alt="platform: Codex" height="22">
  <img src="assets/brand/badge-3.svg" alt="workflow: CAD · SketchUp" height="22">
  <img src="assets/brand/badge-4.svg" alt="stage: Early design" height="22">
</p>

<p align="center">
  Reduce repetitive tracing, linework cleanup and face creation.<br>
  Move from source material to editable CAD bases and SketchUp faces.
</p>

<p align="center"><a href="#quick-start"><strong>Quick Start</strong></a> · <a href="#how-it-works"><strong>How It Works</strong></a> · <a href="#installation"><strong>Installation</strong></a> · <a href="#limitations"><strong>Limitations</strong></a></p>

---

## From source material to design geometry

| Image to CAD | CAD cleanup | SketchUp faces |
| --- | --- | --- |
| Extract site structure from imagery | Check gaps, overlaps and closed loops | Organize editable faces by area |
| Imagery → CAD base | CAD → Clean linework | Linework → Editable faces |

---

<a id="quick-start"></a>

## Quick Start

Upload site imagery or existing CAD, then describe the result you need:

~~~text
Use Map2Model to create a CAD base from this aerial image. Export DWG and a source-image overlay.
~~~

For an existing CAD file:

~~~text
Use Map2Model to clean this CAD and create editable SketchUp faces. Use the satellite image to help identify roads, water and greenery.
~~~

For a simplified 2D plan, specify that the plan is the main geometric base and that supporting satellite imagery is only for classification. You can request district structure, site detail, or explicit local combinations.

<a id="installation"></a>

## Installation

Download the complete repository using **Code → Download ZIP** and retain all folders.

**Assisted installation:** drag the ZIP into a local Codex task and ask it to read the included installation guide, install Map2Model, configure the environment and check actual available capabilities.

**Manual installation:**

1. Extract the archive and rename the folder containing SKILL.md to **map2model**.
2. Copy the complete folder into your existing personal skills directory. Common locations include `%USERPROFILE%\.agents\skills\map2model` on Windows and `~/.agents/skills/map2model` on macOS/Linux.
3. Keep using the skills location already configured on your machine; avoid duplicate installations.
4. Restart Codex if necessary and try $map2model.

See the [installation guide (Chinese)](./Map2Model_安装指引.docx) and [Python dependencies](requirements.txt). Native DWG and SKP delivery requires working local conversion tools.

<a id="how-it-works"></a>

## How It Works

The initial imagery guidance covers the recommended mode, scale reference and design boundary. Existing information and authorization carry forward. Relative scale is acceptable when reliable dimensions are unavailable; without a design boundary, the workflow prepares existing conditions.

When a simplified image is useful, the available built-in image tool produces a candidate that is reviewed against the source before CAD construction. Existing simplified plans and direct-source requests have their own routes. Existing CAD goes directly through linework checks and face creation, without first being converted back to an image.

Construction begins with main roads, water and major enclosed areas, then adds the supported internal connections, selected buildings and details. Each addition is checked against the accepted outer structure. Final DWG geometry is read back before creating a source-image overlay. SKP is checked separately for actual faces, holes and editing organization.

### Processing modes

| Mode | Suitable inputs and use | Main priorities |
| --- | --- | --- |
| District structure | Large areas, uncertain fine detail, surrounding context | Primary/secondary roads, water, complete major parcels and credible important buildings; smaller connections where needed |
| Site detail | Clearly visible footprints, edges, paths and boundaries | Supported corners, courtyards, holes, curves and useful narrow passages; correct projection artifacts and duplicate edges |

Purpose, coverage and visible evidence determine the mode together; image resolution alone does not. See [processing modes](references/processing-modes.md).

### Main base, supporting imagery and mixed detail

CAD or a standalone simplified plan retains its geometry, dimensions and layout when designated as the main base. Satellite imagery assists classification and does not automatically authorize geometry changes. A satellite overlay is optional for these inputs; use reliable registration, a side-by-side comparison or a clearly identified approximate reference. See [source roles](references/source-roles.md) and [existing CAD → SU](references/existing-cad-to-su.md).

Clear central areas and uncertain edges may combine detail and local district simplification. Record the simplified boundary, retained objects and limitations; preserve connecting roads and boundaries across transitions. There is no universal percentage threshold.

With an explicit design boundary, prepare the existing-condition base and derive a cleared version retaining roads inside the boundary. Outside areas and unmarked holes remain. Detail and clearing boundaries are recorded separately. Recommend one reviewed image at handoff and keep another useful version as an alternative. See [design scope](references/design-scope.md).

### Input routes

| Input | Main treatment |
| --- | --- |
| Satellite / orthographic imagery | Preserve visible position and proportions; choose detail according to purpose and evidence |
| Oblique aerial imagery | Separate roof, facade and ground; infer reasonable footprints while protecting roads |
| Simplified 2D plan | Check classes, boundaries and connections; verify positions against the source when available |
| Existing CAD | Check gaps, overlaps, intersections and coplanarity; generate editable SketchUp faces |
| CAD with imagery | Keep the CAD base; use layers, labels and imagery to assist classification |
| Local feedback | Keep accepted geometry and modify the authorized area plus necessary adjoining connections |

## Outputs

Use the requested location, or a project folder on the actual system Desktop. Continuing work reuses the existing folder.

~~~text
Project_ModelingBase/
├─ 成果/
│  ├─ Project_ModelingBase.dwg
│  └─ Project_ModelingBase.skp
└─ 其他/
   ├─ Project_Plan.png
   └─ Project_SourceOverlay.jpg
~~~

The Chinese folder labels are the current delivery convention. Deliver DWG, SKP or both according to the task. Pure CAD/SU work does not require generated imagery. Source overlays use final DWG readback geometry, not the simplified generated plan.

Runtime materials stay outside the delivery folder, preferably under .map2model-work/&lt;project&gt;/&lt;run&gt;/ in the current workspace. Prompts are temporary by default; useful references can be delivered, while scripts, intermediate drafts and checks stay in the external work area. See [output lifecycle](references/output-lifecycle.md).

## Package and Tools

| Item | Purpose |
| --- | --- |
| [SKILL.md](SKILL.md) | Entry point, workflow and essential constraints |
| [references/](references/) and [prompts/](prompts/) | Modes, source interpretation, construction and acceptance guidance |
| [scripts/](scripts/) | Repeatable construction, continuation, checking, conversion and overlays |
| [agents/](agents/) and [assets/brand/](assets/brand/) | Skill display information and logo assets |
| [tests/](tests/), [evals/](evals/) and [examples/](examples/) | Tests, evaluation records and examples |
| [CHANGELOG.md](CHANGELOG.md) | Version history and validation scope |

Tools handle repeatable file/geometry work. Mode selection, road semantics, footprints and uncertainty still need source-based judgment. See [CAD modules](references/cad-modules.md).

Python dependencies include ezdxf, Shapely, Pillow and NumPy. Native DWG requires a converter; existing scripts support AutoCAD Core Console. Native SKP requires an available SketchUp interface, legitimate SDK or real import workflow. The package has no universal one-click converter for arbitrary CAD. Without a DWG converter, preserve editable DXF and state that DWG delivery remains incomplete.

<a id="limitations"></a>

## Limitations

- Single-image geometry is an early-design aid. Relative scale and inferred footprints are not cadastral, construction or statutory-setback evidence.
- A connected-looking image does not prove closed CAD faces. CAD checks do not prove SketchUp import, face selection or push/pull behavior. Record each check actually performed.
- No uniform processing time, fixed savings percentage or success rate is promised. Visibility, density, camera angle and local tools affect the work.
- If primary roads and parcel structure cannot be identified, obtain better source material.

## Version and Validation

Skill version: **v0.9.11**. This branding/documentation update does not change the functional version.

The historical 2026-09-17 synchronization record reports 200 existing automated checks and the structure/hash checks for case library v0.8.3 (14 issue categories, 35 variants, 13 representative images). That record did not rerun native CAD/SU site tests. These are historical results, not fresh functional validation for this README update. See [validation records](evals/validation.md).

## About

Map2Model turns site imagery or existing CAD into editable CAD bases and SketchUp faces for early design.

[简体中文 documentation](README_CN.md) · [Insight-Space](https://github.com/RuiqiWang-LGD/Insight-Space) · [TalkTrack](https://github.com/RuiqiWang-LGD/TalkTrack--)
