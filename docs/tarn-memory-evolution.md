# Tarn memory evolution

## Confirmed direction

Tarn develops independently from the existing private Ombre-Brain fork. Preserve provenance, Git history, memory IDs, original text and rings. Do not delete or rewrite existing memories as part of repository migration. Runtime service names and storage paths remain compatible.

## First repair: introspection visibility

The toy decision was already updated in rings. The defect was `introspection` concatenating body plus rings and slicing the first 500 characters, silently hiding later updates. This is not evidence that the model failed to save the decision.

The read preview now places the newest three dated rings before an independently bounded body preview. Each ring has its own text budget and date/kind label. Undated rings are shown first in reverse append order rather than silently discarded. Omitted rings and truncated text are explicitly marked; the bucket's full `read_bucket` entry is included. Stored order, body, comments, embedding text and fixed model instructions are unchanged.

Regression: a long body and four rings (initial toy, revised toy, wiring change, seller update) must show the last three updates. Timezone-aware sorting, malformed comments, bounds and no mutations are covered. This does not establish that the last ring is the final factual state, and does not repair all other recall entry points.

## Subsequent changes requiring reviewed model instructions

- Write reconciliation: distinguish exact repetition, progress, superseding decisions, related events and unresolved conflicts. Similarity is candidate discovery, not permission to merge. Preserve provenance and reversible versions.
- Current state: derive a compact current view from body and relevant rings, with source references and event time. Keep history. Distinguish selection, intention and completed action. A new seller update must not erase a wiring decision.
- Validity, progress and retention: model these separately. A past event can remain worth recalling; a completed task need not become a current concern. New decisions supersede old decisions without deleting their historical meaning.
- Reflection: attach only to supported events/source segments; permit absent or independently stored reflections. Mixed topics must not inherit one blanket classification.
- Moments: remove forced duplicate prose for short memories while retaining necessary search indexes. Measure duplication in actual model injection, not only UI storage.

No new fixed model instructions or automatic historical reconciliation are enabled in this first repair. Review exact instruction text with Lin before implementation. Changes to existing memory data and schema require an explicit migration scope and reversible plan.
