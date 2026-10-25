# Tarn memory evolution

## Confirmed direction

Tarn develops independently from Lin's Ombre-Brain fork in a public repository. Preserve provenance, Git history, memory IDs, original text and rings. Do not delete or rewrite existing memories as part of repository migration. Runtime service names and storage paths remain compatible.

## First repair: introspection visibility

The toy decision was already updated in rings. The defect was `introspection` concatenating body plus rings and slicing the first 500 characters, silently hiding later updates. This is not evidence that the model failed to save the decision.

The read preview now places the newest three dated rings before an independently bounded body preview. Each ring has its own text budget and date/kind label. Undated rings are shown first in reverse append order rather than silently discarded. Omitted rings and truncated text are explicitly marked; the bucket's full `read_bucket` entry is included. Stored order, body, comments, embedding text and fixed model instructions are unchanged.

Regression: a long body and four rings (initial toy, revised toy, wiring change, seller update) must show the last three updates. Timezone-aware sorting, malformed comments, bounds and no mutations are covered. This does not establish that the last ring is the final factual state, and does not repair all other recall entry points.

## Implemented after instruction review

- Write reconciliation: distinguish exact repetition, progress, superseding decisions, related events and unresolved conflicts. Similarity is candidate discovery, not permission to merge. Preserve provenance and reversible versions.
- Current state: derive a compact current view from body and relevant rings, with source references and event time. Keep history. Distinguish selection, intention and completed action. A new seller update must not erase a wiring decision.
- Validity, progress and retention: model these separately. A past event can remain worth recalling; a completed task need not become a current concern. New decisions supersede old decisions without deleting their historical meaning.
- Reflection: attach only to supported events/source segments; permit absent or independently stored reflections. Mixed topics must not inherit one blanket classification.
- Moments: remove forced duplicate prose for short memories while retaining necessary search indexes. Measure duplication in actual model injection, not only UI storage.

Lin approved the simplified instructions in `write-policy-proposal.md`. The fixed rules alone are deployed as `resources/tarn-write-policy.txt`; a test checks exact equivalence. The main chat prompt is managed through Lin's uploaded files, and the obsolete `CLAUDE_PROMPT.md` is removed from the current tree.

### Runtime behavior

- `hold` and the three `grow` paths use the configured dehydration model to reconcile new input with up to five relevant, editable memories. A similarity-only rejection in the automatic write gate is passed to reconciliation. New writes to pinned/self-anchor memories retain their existing special behavior.
- Exact repetition reuses a bucket. Progress and replacement decisions append the original incoming text as a ring, preserve the original body, and save a derived `tarn_view` with source references. Conflicting evidence remains in separate records and is marked uncertain on both sides when the source version still matches.
- Ring creation/deletion and explicit body edits queue a view refresh. Complete source input is required; oversized material is skipped rather than truncated. Model or validation failure preserves the original write without automatic merging. Invalid optional expiry dates are omitted without discarding valid progress.
- Event text and reflection are separate source packets. Reflections must quote existing text and cite an event outside themselves. Unattached reflections remain in the original record; they are not moved or deleted automatically.
- Recall text and moment indexes use the current view. Full reads and the dashboard retain originals and rings. Changed sources invalidate the old view immediately, and pending reads expose recent rings. Introspection shows a current view once available instead of repeating the same updates twice.
- `validity`, `progress`, and `retention` are separate dimensions. `trace` and dashboard PATCH accept them. Ordinary experiences default to no task status. Ended experiences remain available to relevant queries but do not enter automatic recent context. Explicit expiry and supersession take precedence over favorite status. Legacy resolved/digested behavior remains compatible; explicitly keeping a record clears those legacy sinking flags.
- Source-version checks prevent applying a proposal to a changed bucket. Embedding refreshes serialize per bucket and recheck their input after generation. Previous views are retained in `tarn_view_history`; the authenticated reconciliation endpoint can restore a previous view when its sources are still unchanged.

The authenticated `POST /api/bucket/{id}/reconciliation` endpoint returns a read-only proposal by default. `restore_view_id` explicitly requests restoration. There is no startup backfill or automatic bulk rewrite. Existing memories are reconsidered only on a subsequent relevant write/edit/ring update.

### Verification

`test_memory_reconciliation.py` covers real temporary Markdown writes, deduplication, retained history, source validation, concurrent changes, expiry, independent lifecycle dimensions, and reflection binding. `scripts/verify_tarn_model.py` is an opt-in check against the configured secondary API using synthetic examples and a temporary cache; it does not write production memory.

## Human merge review and old-memory rotation

The dashboard now has a merge review queue with complete sources, a deterministic preview, cached-vector cosine similarity, approve/reject/defer, and guarded undo. Existing `trace` accepts `merge_with` / `merge_reason`; existing `introspection` accepts `order="oldest_review"` (1–5 items), while explicit recent viewing remains available; omitted calls now rotate three old memories. No MCP tool names or fixed model instructions are added.

Rotation timestamps and proposals live in the existing persistent state directory, in `memory-review.json`, separate from bucket activation and update timestamps. Exact source fingerprints suppress duplicate rejected proposals and reject stale approvals. A write-ahead journal and atomic file replacements restore interrupted merges before the service accepts more requests. Source records and rings remain available; merged sources use the existing digested flag to leave automatic recall. Index refresh happens only after approval or undo, never for rejection/defer. Undo is blocked if meaningful content or metadata has changed since approval.

The optional dashboard scan uses existing embeddings only, queues up to 30 pairs per invocation, and does not call another model or merge automatically. Lin approved the heartbeat explanation verbatim. It is stored in `resources/tarn-wake-review.txt` and tested for exact equivalence. Light keepalive and model-appointed wakes reserve up to three buckets through a token-authenticated internal HTTP route; normal chat does not call it. Session/run batch IDs make retries idempotent and share rotation with introspection. Reservations last one hour; successful provider returns acknowledge delivery without altering bucket timestamps. Memory previews stay inside the existing dynamic context, outside user utterances and stable cached sections. A failed memory request does not block the wake. No MCP tools were added.

Validation includes isolated temporary-bucket merge/undo, source edits, concurrent approvals, failure rollback, restart recovery, rejected-pair suppression, vector scanning, route authentication, and mobile/desktop dashboard interactions in both appearances. Tests never merge production buckets.

## Default review without changing existing tool prefixes

Omitted introspection arguments now execute `order="oldest_review", limit=3`. Explicit `order="recent"` remains available. The published introspection descriptor, including its legacy default annotations and description, stays byte-identical so existing provider tool prefixes and snapshots are not invalidated by this change. FastMCP 1.27.1 stores the published schema separately from runtime argument validation; an integration test checks both paths. The model guide explains the runtime defaults until a later deliberate schema refresh. No chat API restart, snapshot reset, or prompt/file change is required for this deployment.
