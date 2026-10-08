# F5 Rust Quick parse-only corpus comparison

This batch compares the public Rust `native_quick_parser::parse` API against the saved legacy Python `Parser.parse` snapshot for all prompts in the training corpus. It deliberately does **not** call `native_quick::build`, CADPlan validation, OCCT, or any mesh/export code.

## Inputs

- Prompt JSONL: `src/legacy/datasets/训练提示词/全部提示词.jsonl`
- Legacy parse snapshot: `artifacts/f5.legacy-prompt-corpus-baseline/legacy_parser_results.jsonl.gz`
- Snapshot integrity metadata: `artifacts/f5.legacy-prompt-corpus-baseline/summary.json`
- Rust runner: `src/apps/desktop/src-tauri/src/bin/quick_parser_corpus.rs`
- Comparator: `artifacts/f5.rust-parser-corpus-comparison/compare_parser_corpus.py`

The saved baseline identifies 1,828 unique prompt rows. The comparer verifies the source SHA-256 and compressed legacy-snapshot SHA-256 against `summary.json`, then joins the source, Python snapshot, and Rust output by both dataset ID and SHA-256 of the original UTF-8 prompt.

## Run after the T10 parser work is stable

From the repository root, build and run the parser-only Rust batch:

```powershell
cargo run --manifest-path src/apps/desktop/src-tauri/Cargo.toml --bin quick_parser_corpus -- src/legacy/datasets/训练提示词/全部提示词.jsonl artifacts/f5.rust-parser-corpus-comparison/rust_parser_results.jsonl
```

Then compare it with the saved Python baseline:

```powershell
python artifacts/f5.rust-parser-corpus-comparison/compare_parser_corpus.py --rust artifacts/f5.rust-parser-corpus-comparison/rust_parser_results.jsonl
```

The Rust runner emits one JSONL row per nonblank input with dataset ID, input hash, category, variant, parser status, ordered raw feature signature, feature parameters, relations, source types/evidence hashes, and issue codes. It intentionally omits the prompt text and source evidence text. The comparer writes `comparison.md` and `comparison.json` under this directory. It exits with an error if any input/snapshot/output IDs or hashes fail to align; parse differences are reported, not made a process failure.

## Reading the first report

- Success is mapped as legacy `parse_ok == true` with at least one feature versus Rust `status == "ready"` with at least one plan feature. The full 2×2 status matrix separates missed legacy successes from Rust acceptances of legacy-rejected inputs.
- Raw feature signatures compare ordered legacy `[kind, op]` pairs to Rust `[kind, operation]` pairs literally. No equivalence mapping is assumed. A raw-signature mismatch is a review queue item, not proof of a semantic mismatch.
- Category and variant tables expose where mismatches cluster. The JSON detail contains IDs and prompt hashes but does not reproduce prompts; use those keys to inspect the corresponding source row locally when triaging.
- Parameters and source metadata are preserved in the Rust output projection for subsequent targeted analysis. This first comparer does not infer parameter-name equivalences, compare geometry, or declare semantic parity.

## Current status

The files here define the independent batch/comparison harness only. Cargo has not been run and there is no Rust-versus-Python corpus result yet. The largest old-parser category is `EGT型材` (1,156 prompts; 1,068 old `extrude_holes:add` outputs). Current source inspection shows the Rust exact-name EGT catalog branch only accepts the bare name `EGT型材`; for example, `EGT型材 EGT001 SW80x088` does not enter that exact-name branch. This makes the category a high-priority first triage target, but its actual Rust status and differences remain unmeasured until the batch runs.
