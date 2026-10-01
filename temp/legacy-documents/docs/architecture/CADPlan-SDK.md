# CADPlan Python SDK and CLI

CADPlan is a versioned, provider-neutral data format between a multimodal model and
the existing CATIA V5 feature builder. A vision model inspects text and images and
returns JSON. The SDK validates that JSON, builds a local preview, translates it to
the current CATIA feature table, then uses the existing cscript/COM bridge. The
model must not produce executable Python or CATScript.

## Install and import

Use the repository's current Python environment. The SDK has no new required
third-party package.

~~~python
from ai_modeler.sdk.cadplan import validate_plan, preview_plan, build_plan
~~~

Public entry points:

- validate_plan(plan) accepts a Python mapping or a UTF-8 JSON path. It returns
  valid, a state, structured errors, confirmations, per-feature CATIA support, and
  warnings. Missing required values or provenance yield needs_confirmation; they
  are not filled from modeler defaults.
- preview_plan(plan, image_path=None) builds the local mesh and CATIA operation
  list, writes a front/right/top PNG, and returns its path as `preview_image`. If
  `image_path` is omitted, it writes to a unique temporary path. It never starts
  CATIA. Its image and dimensions come from the local kernel, not CATIA B-Rep
  verification. The three orthographic panels use a shared pixel/mm scale;
  `preview_image_layout` reports each view's physical extents and the model's
  length/height, width/height, and length/width ratios.
- build_plan(plan, catpart_path=None, strict=False) writes an AIMODEL project,
  STL, three-view and four-view PNGs, and a JSON geometry report next to a new
  CATPart path, then invokes CATIA. The result marks the PNGs as
  `awaiting_agent_inspection` and returns their paths.
  When catpart_path is omitted, output goes to a timestamped directory under
  artifacts/cadplan/. Existing output files are not overwritten.
- support_matrix() returns the feature mapping table; schema_document() and
  write_schema(path) expose the JSON Schema.

The geometry report labels its bounding box and volume as local-kernel values.
The current COM bridge does not reopen the saved CATPart to measure its B-Rep, so
the report does not claim CATIA-side geometric verification. Local preview images
can catch plan mistakes but do not prove that the saved CATIA B-Rep matches the
local mesh.

strict=True runs validation and the local preview first. It returns strict_blocked
and does not invoke CATIA when any feature is approximate or unsupported. Automatic
mode follows the current translator: it builds available operations, skips
unsupported features, returns structured warning records, and shows a CATIA
message box after a successful build when approximation or skip warnings exist.

## CLI

The CLI always writes one JSON result to stdout. Invalid plans and incomplete plans
use nonzero exit codes; a build can be piped to another program.

~~~powershell
python -m ai_modeler --cadplan --help
python -m ai_modeler --cadplan schema --out .\schemas\cadplan-v1.schema.json
python -m ai_modeler --cadplan validate .\plan.json
python -m ai_modeler --cadplan preview .\plan.json
python -m ai_modeler --cadplan preview .\plan.json --image .\out\plan.three-view.png
python -m ai_modeler --cadplan build .\plan.json --catpart .\out\part.CATPart
python -m ai_modeler --cadplan build .\plan.json --strict
~~~

schema writes the versioned schema. validate does not launch CATIA. preview does not
launch CATIA; its image shows front, right, and top orthographic views in that order.
build writes the editable CATPart, AIMODEL, STL, three-view and four-view PNGs, and
a *.geometry-report.json report. The three-view image order is front/right/top; the
four-view order is iso/front/right/top. The LLM workflow must open and inspect the
preview image before building and inspect both build images afterward. If an image
reveals a plan error, revise the JSON plan and rebuild; a successful CATIA operation
count alone is not visual acceptance. Compare the measured front silhouette ratio
against `preview_image_layout.dimension_ratios` and use the shared-scale right and
top views to check depth against height and length.

## CADPlan v1 essentials

- schema_version is "1.0".
- Length units: mm, cm, m, or in; angles: deg or rad. The adapter converts values
  to millimetres and degrees before creating Document features.
- The current frame is a right-handed global Cartesian coordinate system with
  axes [X,Y,Z] and origin [0,0,0]. For predictable orthographic previews and CATIA
  operations, use X for left/right width, Y for front/back depth, and Z for
  bottom/top height. The front view is XZ (viewed along Y), right is YZ (viewed
  along X), and top is XY (viewed along Z). A box's length/width/height map to
  X/Y/Z. If a box has a `center` parameter, it is the geometric center of the
  box; the adapter centers the extrusion unless `base` is explicitly supplied.
  Without `center`, the underlying Document primitive keeps its legacy base
  placement. A through-hole normal to the front face usually uses axis Y.
- Each feature has a stable id, kind, operation (add or cut), a parameters object,
  and sources.
- Every parameter containing numbers must have a corresponding source entry:
  {"type":"user"}, {"type":"image_estimate","evidence":"front view, ... "},
  {"type":"derived","note":"... "}, or {"type":"existing_model","note":"... "}
  when rebasing from a local feature tree whose original value source is unknown.
  Existing-model notes must say whether the original source was retained. Image
  estimates need a short evidence description. Source is recorded in the AIMODEL
  feature provenance and the full plan is preserved in the project.
- relations can declare depends_on and pattern_source. A pattern source must be the
  immediately preceding feature because that is the current Document pattern
  semantics.
- Missing required values, unclear numeric provenance, and pending_questions are
  returned as confirmations. Fix those before preview or build.

### CATIA mapping status

| Status | Feature kinds | Meaning |
|---|---|---|
| Exact | box, cylinder (equal radii), prism, gear, hole, pocket, pattern, rotor | Current exporter can express the requested nominal shape through editable sketches and pads/pockets. pattern expands to explicit operations. |
| Approximate | sphere, revolve, loft, spoked_wheel, vbelt_pulley, timing_pulley, worm, helical_gear, bevel_gear, crankshaft, camshaft, impeller, piston, conrod, cross_joint, scroll_disc; cylinder when tapered | Current exporter omits or simplifies geometry details. See the per-feature reason in support_matrix() and the generated schema. |
| Unsupported | helix, shell; non-Z box, prism, gear, pocket, revolve, loft, or pattern; non-Z tapered cylinder | The current CATIA translator skips these feature geometries. Automatic builds report the skip; strict builds stop before CATIA. |

This table describes the checked-in translator, not general CATIA capability. A
future translator can refine the registry without changing CADPlan's feature IDs,
parameters, or source records.

## Example

~~~json
{
  "schema_version": "1.0",
  "name": "Mounting plate",
  "source_prompt": "Create a rectangular plate with a through hole.",
  "units": {"length": "mm", "angle": "deg"},
  "coordinate_system": {
    "type": "global_cartesian",
    "handedness": "right",
    "axes": ["X", "Y", "Z"],
    "origin": [0, 0, 0]
  },
  "features": [
    {
      "id": "base",
      "kind": "box",
      "operation": "add",
      "parameters": {"length": 80, "width": 50, "height": 12},
      "sources": {
        "length": {"type": "user"},
        "width": {"type": "user"},
        "height": {"type": "user"}
      }
    },
    {
      "id": "center-hole",
      "kind": "hole",
      "operation": "cut",
      "parameters": {"diameter": 8, "through": true, "pos": "center"},
      "sources": {"diameter": {"type": "user"}}
    }
  ]
}
~~~

## Current limits

The current Document feature tree is sequential and global-frame only. Feature
relations are checked and preserved, while the legacy builder still applies
features in array order. Local preview measurements are not a substitute for
opening or measuring the resulting CATPart in CATIA. An image estimate records its
source but is not a manufacturing dimension.
