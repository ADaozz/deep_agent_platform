# Model visual (aesthetic) review

The deterministic checks prove that a slide is intact; they cannot judge composition. That judgement comes from a vision-capable model that looks at the real Impress render. It runs on the platform host, never in the sandbox, and it only reports issues — the main Agent repairs them through the normal atomic operations and approvals.

## Flow

`check-slide` → Impress render → host re-renders the exact revision → model reviews the PNG → structured issues → Agent repairs locally → `check-slide` → re-review.

1. `check-slide` stores `technical_review`, `visual_integrity` and `aesthetic_review: pending`. A slide with deterministic errors is `blocked` and is not sent to the model.
2. The host sees the check event, re-renders the revision into a host-private directory (the sandbox cannot swap the screenshot), verifies the revision hash and calls the model.
3. The answer is schema-validated, its status is derived (below) and the result is ledgered and applied only if the slide's check is still the current one. A late answer for an older revision is recorded as not applied and never overwrites the newer state.
4. `review-status --slide-id … --wait 90` returns the result and the next permitted action.

The model's input: the PNG as an image block (never a path or URL), the design profile, layout and page purpose (`--intent`), user layout constraints (`--constraint`), the native element manifest (IDs, types, text, geometry, styles), chart data, diagram nodes and edges with their bindings, and the automatic findings. Only the current presentation's own renders are sent, and only to the configured model endpoint.

## Model selection

The session's current model is used when its configuration declares `input: [text, image]`. Otherwise the host uses `llm.vision_model: "<source>/<model>"` (or the `PLATFORM_VISION_MODEL` environment variable) when that profile declares image input. With neither, the review is `unavailable` — it is never assumed to pass. No API key is visible to skill scripts.

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
| `failed` | at least one `critical`/`major` issue with medium/high confidence and no pending verification — whatever the model's own verdict |
| `uncertain` | a `critical`/`major` issue that is low-confidence or needs verification; the model says uncertain; any dimension is uncertain; evidence is generic ("看起来很好", "设计专业") or repeated; or the model says fail without a confirmed blocking issue |
| `passed` | none of the above; `minor` issues remain as optional suggestions |
| `unavailable` | no image-capable model, timeout, provider error, invalid JSON, schema mismatch, missing image, render failure or hash mismatch |
| `pending` | requested, not answered yet |

A generous-whitespace minimal page with a clear focal point is not a defect. A layout the user explicitly asked to keep (`--constraint`) is an accepted decision; readability and data correctness are still checked.

## Persistence

Each answer is stored in the host-private ledger (`<session>/artifact-review/pptx/vision-reviews.sqlite`) with server-generated `review id`, `presentation_id`, `slide_id`, `revision`, `render_sha256`, pixel hash, snapshot hash, `model_id`, `prompt_version`, issues, status, response hash and `review_timestamp`. None of these are taken from the model.

## Repair budget and gates

- The same confirmed issue (category + element IDs, or region bucket) may be auto-repaired for two rounds. On the third report the review carries `auto_repair: false` and `escalated_issues`; ask the user.
- `slide.add` needs only the deterministic checks of existing slides (strict mode also needs aesthetic acceptance).
- `finalize` needs, per slide, automatic + content checks and an aesthetic `passed` or a recorded human acceptance; for 3+ slides a deck review that is current (no slide changed since). The host then verifies the finalized revision against its ledger and its own render and publishes `finalize_verified`.
- Human decisions (accept, reject, dismiss issue) are made in the preview UI, need notes, and are ledgered. `review-slide` from the sandbox is an advisory self-inspection only.
- `request-review` may re-request an `unavailable` review (at most 3 times per check); a failed or uncertain review is not re-rolled by the Agent.

## Deck review

`request-deck-review --base-revision N` (all slides checked) makes the host render every slide, build a numbered contact sheet and ask for cross-slide issues only: design_system, transitions, repetition, card_overuse, consistency, density_rhythm, bookends. Every issue must name 1–6 concrete pages; issues covering most of a 5+ page deck or asking to redo the whole deck are rejected and listed separately. Statuses follow the same rules.

## Evaluating the reviewer

Prompt changes are measured against hand-labelled slides, not model self-scores: `python pptx-ooxml-skill/tests/vision_eval.py build <dir>` produces the labelled regression slides (meaningless connector, low-contrast chart, crowded cards, truncated title, repeated three-column pages, empty page without focus, chart without unit/labels, a good minimal page) and `… run <dir> --model <source/model>` reports the major-defect miss rate, the false-positive rate on clean pages and element/region localisation accuracy.
