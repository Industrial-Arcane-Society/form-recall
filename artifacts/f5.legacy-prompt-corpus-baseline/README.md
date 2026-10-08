# Legacy Quick prompt corpus baseline

## Source and method

- Input: `src/legacy/datasets/训练提示词/全部提示词.jsonl`
- SHA-256: `fa2b839aaddd0d55821e0e2ca34f37f43a5e5d25b0a0f25dbd9de5e9ba477c26`
- JSONL: 1,828 nonblank rows, all valid objects with the same ten fields: `id`, `prompt`, `category`, `model`, `envelope`, `envelope_str`, `trust`, `source`, `group`, `variant`.
- `prompt` is the description field. There are 1,828 exact unique prompts, no duplicate prompt rows, and no blank/malformed rows.
- Each distinct prompt was passed to a **new** legacy `Parser` instance. The audit reads `ParseResult.ok` and `ParseResult.doc.features`; it does not call `Document.build()`, `Document.stats()`, B-Rep construction, or any Rust/Cargo path.
- The replay also writes one ordered result row per nonblank dataset row to `legacy_parser_results.jsonl.gz`. The decompressed JSONL has 1,828 rows; it stores dataset `id` and SHA-256 of UTF-8 prompt text for alignment, but does not copy any raw prompt text. Rows and object keys are emitted deterministically (source order and sorted JSON keys); the gzip header uses `mtime=0`.
- The parser stores features in its `Document`. Reusing a parser instance contaminates results: a two-call probe with a single instance changes the first result's observed feature count from 7 to 14. The production module helper avoids that by returning `Parser().parse(text)` each call (`nlparser.py:1886-1887`). The F5.89 audit harness also constructs `Parser()` for each input (`tools/audit_quick_legacy_samples.py:40-43`).
- The actual legacy Quick bridge defaults `new_model=True` and calls `NLP.parse(text)` (`ui/bridge.py:338-339`), matching the independent-prompt baseline. Its explicit append mode calls `NLP.parse(text, self.doc)` (`ui/bridge.py:340-341`). The separate lifecycle probe reproduces that append behavior: first document 7 features, second call adds 7, final document 14. This session-mode result is not included in the independent corpus counts.

## Parser results

- 1,822/1,828 prompts (`99.67%`) returned `ok=true` and at least one feature; 6 returned `ok=false` and no features; there were no exceptions, `ok`/feature inconsistencies, or `ok=true` empty documents.
- The 6 failures are IDs `neg:01`, `neg:04`, `neg:05`, `neg:06`, `neg:08`, and `neg:10`. The legacy parser message is “没有从描述中识别出建模指令。” The raw descriptions are intentionally not reproduced here.
- The source labels 12 rows as `group=负例`, `variant=库外型号`. Six are rejected (the IDs above), but six are accepted and produce features: `neg:02`, `neg:03`, `neg:07`, `neg:09`, `neg:11`, `neg:12`. This is a parser false-acceptance signal for the dataset's negative group; acceptance here means only that Parser emitted features, not that geometry was built or validated.
- Per-prompt feature count: minimum 0, median 1, nearest-rank p95 58, maximum 122; 14,975 feature instances total.

| Feature kind | Prompts containing kind | Feature instances |
| --- | ---: | ---: |
| `box` | 340 | 346 |
| `cylinder` | 428 | 1,315 |
| `extrude_holes` | 1,068 | 1,068 |
| `hole` | 634 | 12,025 |
| `prism` | 221 | 221 |

The audit recorded 65 distinct ordered feature-combination signatures. The most common is one `extrude_holes:add` feature (1,068 prompts); next are a 7-feature cylinder/hole/prism assembly (220 prompts), and a box with 12 holes (64 prompts). Signatures in `summary.json` retain feature-kind/operation multiplicities, first/last sequence tokens, and a sequence hash without copying the source prompts.

## Difference from the F5.89 fixed catalog

- The saved F5.89 66-row audit fixture has 66 unique inputs. They exactly equal the current `native_parts_catalog.json` 66 `parts[].sample_prompt` strings (set equality checked).
- Exact prompt-string intersection between the training corpus and those 66 fixed catalog inputs is **0**. Thus all 66 catalog sample strings are absent from this corpus and all 1,828 corpus strings are outside that exact fixed-input set.
- This is an exact-string difference only; it does not imply that category names or semantic variants are absent. The corpus contains many wording variants in groups such as EGT profiles, EGT connectors, socket-head screws, and locating pins.

## Artifacts and replay

- `summary.json`: full aggregate counts, compact feature-combination groups, F5.89 exact-set difference, input SHA-256, and failed item IDs without prompt text.
- `legacy_parser_results.jsonl.gz`: deterministic gzip of the full ordered legacy parser snapshot. Decompressed top-level fields are `schema_version`, `dataset_id`, `prompt_sha256`, `parse_ok`, `feature_count`, `features`, `warnings`, `steps`, `recognized`, `message`, `questions`, `assumed`, and `used_templates`. Each ordered feature preserves `kind`, `op`, `params`, `name`, `feature_id`, `relations`, `enabled`, `provenance`, `warnings`, and `note`, matching `Feature.to_dict()` plus an explicit warnings slot. `summary.json` records both compressed and uncompressed hashes and sizes.
- Current snapshot: compressed SHA-256 `3b4d3518c8e8fdfb3804647fdd51f1b6198e0a12065e4758c575cc699e968632` (**310,668 bytes**); uncompressed SHA-256 `a64e0d2c8b459be0ec8a77bd4a763dbde8c1af96598d53818a3f45913edf2dae` (**22,149,210 bytes**), a 98.60% size reduction.
- `audit_legacy_prompt_corpus.py`: reruns the isolated Parser.parse batch and rewrites both `summary.json` and `legacy_parser_results.jsonl.gz`. It streams stable JSONL through gzip (`mtime=0`) to a temporary file and replaces the prior compressed artifact after the batch completes.
- `parser_lifecycle_probe.json` and `verify_parser_lifecycle.py`: separate two-row check of independent and explicit append-mode lifecycles.

From the repository root, replay the batch with:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
python -B -u artifacts/f5.legacy-prompt-corpus-baseline/audit_legacy_prompt_corpus.py
```

Re-run the append-mode diagnostic with:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
python -B artifacts/f5.legacy-prompt-corpus-baseline/verify_parser_lifecycle.py
```

## Rust parser-only comparison feasibility

The Rust library exposes `native_quick_parser::parse(&str) -> serde_json::Value` (`src/apps/desktop/src-tauri/src/native_quick_parser.rs:41`, module is public from `src/apps/desktop/src-tauri/src/lib.rs:35`). A minimal follow-up can add a parse-only batch harness that reads the dataset JSONL, calls this function once per row, and emits JSONL keyed by `dataset_id` and the same UTF-8 `prompt_sha256`; it should not call `native_quick::build` or any geometry/OCCT code. The comparison can join on both keys, then compare `parse_ok` to the Rust status mapping, feature count/order, kind/op/name/params, relation/source fields, and parser warnings after documenting field-name differences. This is a feasibility design only: no Rust file, binary, or test was added or run in this audit.

## Limits

These counts describe old parser output only. No model meshes, B-Reps, feature validity, dimensions, volumes, or Rust parity were tested. Parser “success” does not certify geometric buildability. The exact catalog difference is not semantic coverage, and the separate append-mode probe is not a full UI session audit.
