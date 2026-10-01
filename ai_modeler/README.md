# `ai_modeler` package map

The package root is reserved for bootstrapping, stable path helpers, and
backward-compatible command launchers. Product implementation is grouped by
responsibility:

| Package | Responsibility | Examples |
|---|---|---|
| `cli/` | Package command dispatch and command-specific handlers | `main.py`, `catia.py`, `legacy.py` |
| `agent/` | LLM adapters, image measurements, and agent orchestration | `agentloop.py`, `llm.py`, `llm_plan.py`, `image_analysis.py` |
| `sdk/` | Structured CADPlan API, CLI, and image input helpers | `cadplan.py`, `cadplan_cli.py`, `clipboard_image.py` |
| `core/model/` | Editable feature tree and document model | `docmodel.py` |
| `core/geometry/` | Geometry kernels, meshing, slicing, and measurement | `kernel.py`, `directmesh.py`, `meshslice.py` |
| `core/parsing/` | Text parsing and parameter extraction | `nlparser.py`, `extract.py`, `params.py` |
| `core/validation/` | Geometric constraints and corrective rules | `constraints.py`, `corrections.py` |
| `core/export/` | Generic model exporters | `exporters.py` |
| `parts/library/` | Part catalog, candidate lookup, and knowledge retrieval | `partlib.py`, `candidates.py`, `retrieval.py` |
| `parts/standard/` | Standard mechanical parts | `std_bolt.py`, `std_bearing.py`, `std_pulley.py` |
| `parts/measured/` | Measured or reverse-engineered parts | `realparts.py`, `realholes.py`, `reallib.py` |
| `catia_backend/` | CATIA connection, export, service, and script templates | `catia.py`, `catia_export.py`, `catia_tpl_*.txt` |
| `ui/` | Legacy Tk fallback, Tauri JSONL bridge, shared examples, and local image rendering | `app.py`, `bridge.py`, `examples.py`, `viewer3d.py`, `softrender.py` |
| `runtime/` | Configuration, MCP service, and self-test | `config.py`, `mcp_server.py`, `selftest.py` |
| `catia_mcp/` | CATIA MCP server and its tool modules | `server.py`, `bridge.py`, `tools/` |

## Package layout rules

- Put new implementation in the matching domain package; do not add new
  implementation modules at this package root.
- Keep cross-domain orchestration in `agent/`. In `core/`, place each module in
  the matching `model/`, `geometry/`, `parsing/`, `validation/`, or `export/`
  package; keep reusable part data in the matching `parts/` category.
- Keep compatibility behavior in `_aliases.py` rather than adding a forwarding
  file for every historical top-level module.
- Route new CLI behavior through `cli/`; keep `__main__.py` as a thin package
  entry point.
- Keep the small root launchers only while their historical ``python -m``
  commands remain supported. They must forward to the implementation packages.

## Imports and entry points

New code should import implementation modules from their domain package, for
example:

```python
from ai_modeler.sdk.cadplan import validate_plan, preview_plan, build_plan
from ai_modeler.agent.agentloop import run_agent_loop
from ai_modeler.core.model.docmodel import Document
from ai_modeler.core.geometry.kernel import bbox
```

Historical imports such as ``from ai_modeler import kernel`` and
``from ai_modeler.catia_export import ...`` remain available through one lazy
compatibility router. The first package layout's nested paths, such as
``ai_modeler.core.kernel`` and ``ai_modeler.parts.std_bolt``, are also routed
to their new locations. New code should use the domain paths shown above. The
package-level launchers remain ``python -m ai_modeler`` and
``python -m ai_modeler.catia_mcp``; the retained root launchers also preserve
the existing ``python -m ai_modeler.app``, ``mcp_server``, ``cadplan_cli``,
``catia``, and ``selftest`` commands.

`ai_modeler.paths.PROJECT_ROOT` is the canonical repository path. Modules
should use it for runtime files instead of deriving the repository root from
their own `__file__` location.

## DSH local part knowledge

DSH searches `datasets/训练提示词/全部提示词.jsonl` before its first model
response. Exact model identifiers can include measured metadata and the
existing parser's feature output as reference context. Similar descriptions
are context-only and must not replace the user's dimensions. Add compatible
JSONL sources under `kb/knowledge_sources/`; future text or image index Providers
can register through the same search interface. The schema and extension point
are documented in [knowledge_sources/README.md](../kb/knowledge_sources/README.md).

For image requests, DSH attaches a single contact sheet from paired local
real-photo and model-render samples in `examples/`. This lets the multimodal
model compare visual examples, but it is not a ranked image embedding index.

Reusable modeling procedures live in [`kb/modeling_recipes.json`](../kb/modeling_recipes.json).
DSH can search them with `cad_search_recipes`; executable recipes are applied
with one `cad_apply_recipe` call. The first deterministic builder creates
ISO 4762 socket-head cap screws from the existing bolt standard table, including
a faceted helical thread in the local preview/STL mesh. The current CATIA
exporter still emits a smooth shank; the recipe and export warning keep that
limitation visible. Hole, counterbore, and general-thread cards document their
construction steps and current geometry limits.
