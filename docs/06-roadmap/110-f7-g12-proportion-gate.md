# F7.G12 host-computed proportion gate

`native_proportion_gate` adds an auditable silhouette-envelope check to the native aggregate review. It computes a dimensionless projected body width/height ratio from each reference image and a current native preview. Background segmentation uses a corner-derived flat background and three foreground thresholds; the interval spans the observed ratios with an additional 8% margin. The gate compares intervals rather than a single point estimate.

## Comparability and evidence binding

- `reference_similarity_history` supplies the previously reviewed pair identity and its `view_comparable`/`view`; the gate does not use score, findings, or the manifest for view labels. A row must bind the same reference ID/source SHA, current revision/generation/fingerprint, and a full-image pair (`roi_norm=null`). False comparability, missing/stale history, or view mismatch stays unknown.
- The matching view selects a native preview for that current geometry; the history `preview_sha256` must match both preview metadata and the current bytes. Projection comes from that preview's native `render_options`; the reference projection is not guessed independently because the vision review already establishes pair comparability.
- The original reference bytes must match the host manifest `sha256`.
- Image reads are capped at 20 MiB; decoded dimensions are capped at 16,384 per side and 16 megapixels with a 128 MiB decoder allocation limit. Hashing and decoding use the same in-memory bytes. Limit/decode failures return fixed unknown reason codes.
- Missing view/comparability/projection metadata, stale/missing preview, hash mismatch, textured/ambiguous background, or failed segmentation yields `unknown`. The aggregate preserves that status and reason as diagnostic evidence; it does not block existing sessions because the current host reference-manifest writer has no operation to add these annotations.
- Non-overlapping uncertainty intervals yield `mismatch`, adding a host aggregate reason and preventing finish. Overlapping intervals yield `consistent` for this one projected-envelope metric only.
- No-reference sessions return `not_applicable`; this check does not estimate absolute dimensions and does not replace detailed feature/geometry validation.

## Limits and verification

This compares a 2D silhouette proxy, not three independent 3D body dimensions. It can be conservative or unavailable for cropped, textured, transparent, occluded, or non-flat-background reference photos. The 8% guard band is a conservative heuristic/tunable threshold, and the threshold sweep also needs a labeled real-image calibration set before either is treated as validated. The existing vision review can provide view comparability, but rows with unknown or false comparability remain diagnostic-only so they do not block ordinary sessions. A proportion pass only means this one projected envelope interval overlaps, and does not establish visual correctness. A comparable, hash-bound mismatch blocks finish and becomes an open host finding.

Focused Rust unit test covers same-view agreement, a clear mismatch, false or missing comparability, and stale generation/fingerprint/view remaining unknown. It passes through the repository's `scripts/test-lib.ps1` activation-manifest wrapper, as do all seven `native_review_gate` unit tests. No DSH cold start was run.
