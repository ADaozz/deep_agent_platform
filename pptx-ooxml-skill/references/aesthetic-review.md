# Model visual (aesthetic) review

The deterministic checks prove that a slide is intact; they cannot judge composition. That judgement comes from a vision-capable model that looks at the real Impress render. When the optional platform adapter is connected, it makes a separate model API request on the host in the background. It does not use the generating Agent’s own visual ability. Standalone CLI checks only record requests; they do not supply this worker or model configuration. It is advice: it never blocks building or delivering the deck. All visual findings, including critical ones, are suggestions; the Agent changes nothing unless the user requests it.

## Flow

Finish the whole deck → `check-deck` → local overlap repair and recheck until passed → single-slide and deck vision reviews → suggestions only. The request is `{"content_notes":{"slide-id":"specific content checks"}}`. Results include repair_targets, other_errors and overlap_passed. Passing unchanged pages are reused; deferred vision jobs stay deferred across restarts until the deck passes.

1. At the end of generation, `check-deck` uses the page checker to store `technical_review`, `visual_integrity` and `aesthetic_review: pending`. A slide with deterministic errors is `blocked` and is not sent to the model.
2. The host sees the check event, re-renders the revision into a host-private directory (the sandbox cannot swap the screenshot), verifies the revision hash and calls the model.
3. The answer is schema-validated, its status is derived (below) and the result is ledgered and applied only if the slide's check is still the current one. A late answer for an older revision is recorded as not applied and never overwrites the newer state.
4. `review-status [--slide-id …]` returns the result, critical issues and suggestions separately, and the next action. The Agent does not wait for it.

The model also receives selected_design: the real style_id, design Markdown, explicit deck parameters and user overrides. It follows user requirements first, then actual parameters, then the style MD; fallback profile colours and margins are not design requirements. Numeric checks use the same explicit parameters. Review reuse requires the same design fingerprint as well as matching pixels/intent.

The model's input: the PNG as an image block (never a path or URL), the design profile, layout and page purpose (`--intent`), user layout constraints (`--constraint`), the native element manifest (IDs, types, text, geometry, styles), chart data, diagram nodes and edges with their bindings, and the automatic findings. Only the current presentation's own renders are sent, and only to the configured model endpoint.

## Model selection

Model selection below belongs to the platform adapter, not to the standalone skill. The session's current model configuration is used for a separate API request when its configuration declares `input: [text, image]`. Otherwise the host uses `llm.vision_model: "<source>/<model>"` (or the `PLATFORM_VISION_MODEL` environment variable) when that profile declares image input. With neither, the review is `unavailable` — it is never assumed to pass. No API key is visible to skill scripts.

## Dimensions

composition, focal_point, hierarchy, legibility, spacing, chart_quality, diagram_semantics, media_balance, decoration, profile_consistency, templating. For each, the model returns a verdict and concrete evidence that points at visible content. For diagrams it compares the visible lines with the declared edges; a relation it cannot confirm from the inputs is `needs_verification` and low confidence, never assumed correct.

## Output

```json
{"status":"fail","summary":"存在无意义的连接线","checks":{"composition":{"verdict":"ok","evidence":"…"}},
 "issues":[{"id":"visual-001","category":"diagram_semantics","severity":"major","element_ids":["connector-01"],
   "region":[0.38,0.42,0.12,0.10],"observation":"连接线未明确连接两个节点","reason":"无法判断具体依赖关系",
   "suggestion":"绑定正确节点或删除装饰线","confidence":"high","needs_verification":false}]}
```

`region` is normalised `[x, y, w, h]` of the image. Element IDs not in the manifest are removed into `unmapped_element_ids` and flagged; the region stays as the locator. Unknown fields (for example a score) are recorded and ignored.

## Status rules

| status | when |
| --- | --- |
| `failed` | at least one `critical`/`major` issue with medium/high confidence and no pending verification — whatever the model's own verdict; all findings remain advice |
| `uncertain` | a `critical`/`major` issue that is low-confidence or needs verification; the model says uncertain; any dimension is uncertain; evidence is generic ("看起来很好", "设计专业") or repeated; or the model says fail without a confirmed blocking issue |
| `passed` | none of the above; `minor` issues remain as optional suggestions |
| `unavailable` | no image-capable model, timeout, provider error, invalid JSON, schema mismatch, missing image, render failure or hash mismatch |
| `pending` | requested, not answered yet |

A generous-whitespace minimal page with a clear focal point is not a defect. A layout the user explicitly asked to keep (`--constraint`) is an accepted decision; readability and data correctness are still checked.

## Persistence

Each answer is stored in the host-private ledger (`<session>/artifact-review/pptx/vision-reviews.sqlite`) with server-generated `review id`, `presentation_id`, `slide_id`, `revision`, `render_sha256`, pixel hash, snapshot hash, `model_id`, `prompt_version`, issues, status, response hash and `review_timestamp`. None of these are taken from the model.

## Repair policy and delivery

- Only deterministic overlap findings trigger autonomous repairs. Edit listed slide/element IDs in the existing PPT, then repeat `check-deck` until passed. Never regenerate unrelated pages or silently suppress errors.
- All visual findings are advice, including critical ones. Reviews carry `auto_repair: false, advisory_only: true`. Only a user repair request authorizes edits based on these suggestions.
- A slide shows `needs_attention` only while a critical finding is open. That status is informational.
- Slide creation/import/clone has no intermediate check gate, including strict mode. Check after the whole deck is ready.
- `finalize` needs automatic and content checks per slide. Visual review, deck review, `uncertain` and `unavailable` never block it; the finalize event lists outstanding critical findings and suggestion counts for the delivery message. The host then checks that every claimed verdict really exists in its ledger (and matches the finalized pixels) and publishes `finalize_verified`.
- There is no manual single-slide re-review, human accept or human reject UI/API. Issue dismissal and sending a suggestion to the Agent remain; dismissal never converts the model verdict into acceptance. Historical human records are not used to override results. `review-slide` is advisory Agent self-inspection only.
- `request-review` may re-request an `unavailable` review (at most 3 times per check).

## Deck review

`request-deck-review --base-revision N` (all slides checked) makes the host render every slide, build a numbered contact sheet and ask for cross-slide issues only: design_system, transitions, repetition, card_overuse, consistency, density_rhythm, bookends. Every issue must name 1–6 concrete pages; issues covering most of a 5+ page deck or asking to redo the whole deck are rejected and listed separately. Deck findings are suggestions for the user and never block delivery.

## Evaluating the reviewer

Prompt changes are measured against hand-labelled slides, not model self-scores: `python pptx-ooxml-skill/tests/vision_eval.py build <dir>` produces the labelled regression slides (meaningless connector, low-contrast chart, crowded cards, truncated title, repeated three-column pages, empty page without focus, chart without unit/labels, a good minimal page) and `… run <dir> --model <source/model>` reports the major-defect miss rate, the false-positive rate on clean pages and element/region localisation accuracy.
