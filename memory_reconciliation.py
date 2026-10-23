"""Source-grounded reconciliation using the configured secondary model.

Only new writes / ring updates invoke this module. No startup backfill.
"""

import asyncio
import copy
import json
import logging
import re
import uuid
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from memory_state import memory_revision, current_view, memory_lifecycle, VALIDITY, PROGRESS
from self_anchor import is_self_anchor_metadata

logger = logging.getLogger(__name__)
POLICY_PATH = Path(__file__).with_name("resources") / "tarn-write-policy.txt"
RELATIONS = {"new", "duplicate", "progress", "supersedes", "related", "conflict"}
REFERENCE_SCHEMA = {
    "type": "object", "required": ["source_id", "quote"],
    "properties": {"source_id": {"type": "string"}, "quote": {"type": "string", "description": "Exact source substring"}},
}
EVIDENCE_SCHEMA = {"type": "array", "items": REFERENCE_SCHEMA}
RESPONSE_SCHEMA = {
    "type": "object", "required": ["relation", "target_id", "reason", "relation_evidence", "facts", "reflections", "state"],
    "properties": {
        "relation": {"type": "string", "enum": sorted(RELATIONS)},
        "target_id": {"type": "string", "description": "Candidate bucket ID or fixed_target_id; empty for new"},
        "reason": {"type": "string"}, "relation_evidence": EVIDENCE_SCHEMA,
        "facts": {"type": "array", "minItems": 1, "maxItems": 16, "items": {
            "type": "object", "required": ["text", "evidence"],
            "properties": {"text": {"type": "string", "maxLength": 600}, "evidence": EVIDENCE_SCHEMA}}},
        "reflections": {"type": "array", "maxItems": 8, "items": {
            "type": "object", "required": ["text", "evidence", "about"],
            "properties": {"text": {"type": "string", "description": "Exact existing reflection"}, "evidence": EVIDENCE_SCHEMA, "about": {**EVIDENCE_SCHEMA, "description": "Event excerpts the reflection responds to, outside the reflection itself"}}}},
        "state": {"type": "object", "required": ["validity", "progress", "valid_until", "evidence"], "properties": {
            "validity": {"type": "string", "enum": sorted(VALIDITY)}, "progress": {"type": "string", "enum": sorted(PROGRESS)},
            "valid_until": {"type": "string", "description": "Explicit deadline YYYY-MM-DD, or empty"}, "evidence": EVIDENCE_SCHEMA}},
    },
}


def eligible(bucket):
    meta = bucket.get("metadata", {})
    state = memory_lifecycle(bucket)
    return not (state["retention"] == "archived" or state["validity"] in {"expired", "superseded"} or meta.get("pinned") or meta.get("protected") or is_self_anchor_metadata(meta) or meta.get("profile_kind") or meta.get("resolved") or meta.get("digested") or meta.get("type") in {"permanent", "feel", "archived", "source", "raw", "chat_log", "diary_source"})


def sources_for(bucket):
    bid = bucket["id"]
    meta = bucket.get("metadata", {})
    sources = [{"source_id": bid + ":body", "bucket_id": bid, "kind": "body", "text": bucket.get("content", ""), "date": meta.get("date", ""), "created": meta.get("created", "")}]
    for index, ring in enumerate(meta.get("comments") or []):
        if isinstance(ring, dict) and str(ring.get("content") or "").strip():
            sources.append({"source_id": ring.get("source_id") or bid + ":ring:" + str(ring.get("id") or index), "bucket_id": bid,
                            "kind": ring.get("kind", "comment"), "text": ring["content"], "created": ring.get("created", ""), "date": ring.get("date", "")})
    separated = []
    for source in sources:
        matches = list(re.finditer(r"(?ims)^#{1,6}\s+reflection\s*\n(.*?)(?=^#{1,6}\s|\Z)", source["text"]))
        if not matches:
            separated.append(source)
            continue
        body = source["text"]
        for match in reversed(matches):
            body = body[:match.start()] + body[match.end():]
        if body.strip():
            separated.append({**source, "text": body.strip()})
        for index, match in enumerate(matches):
            if match.group(1).strip():
                separated.append({**source, "source_id": source["source_id"] + f":reflection:{index}", "kind": "reflection", "text": match.group(1).strip()})
    return separated


def validate_proposal(value, sources, target_ids, incoming_id, fixed_target=""):
    if not isinstance(value, dict) or value.get("relation") not in RELATIONS:
        raise ValueError("invalid relation")
    target = str(value.get("target_id") or "")
    relation = value["relation"]
    if fixed_target:
        if target != fixed_target or relation not in {"progress", "supersedes", "conflict"}:
            raise ValueError("ring refresh must describe its own bucket")
    elif relation == "new":
        if target:
            raise ValueError("new memory has a target")
    elif target not in target_ids:
        raise ValueError("unknown target")
    source_map = {source["source_id"]: source for source in sources}
    allowed = {incoming_id}
    if relation in {"duplicate", "progress", "supersedes"} or fixed_target:
        allowed.add(target)

    def evidence(items, *, allowed_buckets=allowed):
        if not isinstance(items, list) or not items:
            raise ValueError("missing evidence")
        for item in items:
            source = source_map.get(item.get("source_id")) if isinstance(item, dict) else None
            quote = str(item.get("quote") or "") if isinstance(item, dict) else ""
            if not source or source["bucket_id"] not in allowed_buckets or not quote.strip() or quote not in source["text"]:
                raise ValueError("invalid source quotation")
        return items

    if relation != "new" and not fixed_target:
        refs = evidence(value.get("relation_evidence"), allowed_buckets={incoming_id, target})
        if {source_map[item["source_id"]]["bucket_id"] for item in refs} != {incoming_id, target}:
            raise ValueError("relation needs both sources")
    facts = value.get("facts")
    if not isinstance(facts, list) or not 1 <= len(facts) <= 16:
        raise ValueError("invalid facts")
    def is_event_ref(ref):
        source = source_map[ref["source_id"]]
        event_text = re.sub(r"(?ims)^#{1,6}\s+reflection\s*\n.*?(?=^#{1,6}\s|\Z)", "", source["text"])
        return source["kind"] not in {"feel", "reflection"} and ref["quote"] in event_text

    for fact in facts:
        if not isinstance(fact.get("text"), str) or not fact["text"].strip() or len(fact["text"]) > 600:
            raise ValueError("invalid fact text")
        evidence(fact.get("evidence"))
    value["facts"] = [fact for fact in facts if any(is_event_ref(ref) for ref in fact["evidence"])]
    if not value["facts"]:
        raise ValueError("no event-grounded facts")
    reflections = value.get("reflections", [])
    if not isinstance(reflections, list) or len(reflections) > 8:
        raise ValueError("invalid reflections")
    retained_reflections = []
    for reflection in reflections:
        refs = evidence(reflection.get("evidence"))
        evidence(reflection.get("about"))
        if not any(is_event_ref(ref) for ref in reflection["about"]):
            continue
        text = reflection.get("text", "")
        if not isinstance(text, str) or not text.strip() or len(text) > 1000 or not any(text == ref["quote"] for ref in refs):
            raise ValueError("reflection must preserve existing words")
        if not any(source_map[ref["source_id"]]["kind"] in {"feel", "reflection"} for ref in refs):
            raise ValueError("reflection has no reflection source")
        retained_reflections.append(reflection)
    value["reflections"] = retained_reflections
    state = value.get("state", {})
    if state.get("validity") not in VALIDITY or state.get("progress") not in PROGRESS:
        raise ValueError("invalid lifecycle")
    if state["validity"] != "current" or state["progress"] != "not_applicable" or state.get("valid_until"):
        evidence(state.get("evidence"))
    deadline = str(state.get("valid_until") or "")
    if deadline:
        try:
            parsed = date.fromisoformat(deadline)
            forms = {deadline, f"{parsed.year}年{parsed.month}月{parsed.day}日", f"{parsed.year}/{parsed.month}/{parsed.day}"}
            explicit = any(any(form in ref["quote"] for form in forms) for ref in state["evidence"])
        except (ValueError, TypeError):
            explicit = False
        if not explicit:
            # Keep grounded facts/progress even when the optional expiry is unsupported.
            state["valid_until"] = ""
            if state["validity"] == "expired":
                state["validity"] = "uncertain"
    if relation == "conflict":
        state["validity"] = "uncertain"
    value["state"] = {key: state[key] for key in ("validity", "progress", "valid_until", "evidence") if key in state}
    return value


class MemoryReconciler:
    def __init__(self, manager, generator):
        self.manager = manager
        self.generator = generator
        self.lock = asyncio.Lock()

    async def propose(self, incoming, candidates, *, fixed_target=""):
        generator = self.generator()
        if not getattr(generator, "client", None):
            return None
        sources = sources_for(incoming)
        for candidate in candidates:
            sources.extend(sources_for(candidate))
        schema = copy.deepcopy(RESPONSE_SCHEMA)
        if fixed_target:
            schema["properties"]["relation"]["enum"] = ["progress", "supersedes", "conflict"]
        payload = {"output_format": "json", "as_of": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(), "incoming_id": incoming["id"], "fixed_target_id": fixed_target,
                   "candidates": [{"id": b["id"], "name": b.get("metadata", {}).get("name", "")} for b in candidates],
                   "sources": sources, "output_schema": schema}
        material = json.dumps(payload, ensure_ascii=False, default=str)
        if len(material) > 24000:
            logger.info("Reconciliation skipped: complete sources exceed input budget")
            return None
        try:
            response = await generator.client.chat.completions.create(
                model=generator.model,
                messages=[{"role": "system", "content": POLICY_PATH.read_text()}, {"role": "user", "content": material}],
                response_format={"type": "json_object"},
                **generator._completion_options(max_tokens=4096, temperature=0.1),
            )
            if not response.choices or response.choices[0].finish_reason == "length":
                return None
            value = json.loads(response.choices[0].message.content or "")
            return validate_proposal(value, sources, {b["id"] for b in candidates}, incoming["id"], fixed_target)
        except Exception as exc:
            detail = str(exc) if isinstance(exc, ValueError) else getattr(exc, "code", "")
            logger.warning("Reconciliation skipped: %s %s", type(exc).__name__, detail)
            return None

    def view_metadata(self, bucket, proposal):
        meta = bucket.get("metadata", {})
        view = {key: copy.deepcopy(proposal[key]) for key in ("facts", "reflections", "state") if key in proposal}
        view.update({"id": uuid.uuid4().hex[:12], "created": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
                     "source_revision": memory_revision(bucket, include_state=False), "relation": proposal["relation"], "reason": proposal.get("reason", ""),
                     "relation_evidence": proposal.get("relation_evidence", [])})
        history = list(meta.get("tarn_view_history") or [])
        if meta.get("tarn_view"):
            history.append(meta["tarn_view"])
        return {"tarn_view": view, "tarn_view_history": history}

    async def refresh(self, bucket_id):
        async with self.lock:
            for _ in range(2):
                bucket = await self.manager.get(bucket_id)
                if not bucket or not eligible(bucket):
                    return False
                if current_view(bucket):
                    return False
                revision = memory_revision(bucket)
                proposal = await self.propose(bucket, [], fixed_target=bucket_id)
                if not proposal:
                    return False
                if await self.manager.update(bucket_id, expected_revision=revision, extra_metadata=self.view_metadata(bucket, proposal), last_active=bucket["metadata"].get("last_active")):
                    return True
            return False

    async def write(self, content, **create_args):
        async with self.lock:
            incoming_id = uuid.uuid4().hex[:12]
            incoming = {"id": incoming_id, "content": content, "metadata": {"name": create_args.get("name"), "date": create_args.get("date"), "created": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(), "type": "dynamic"}}
            if is_self_anchor_metadata(create_args):
                bid = await self.manager.create(content=content, bucket_id=incoming_id, **create_args)
                return bid, create_args.get("name") or bid, False
            try:
                hits = await self.manager.search(content, limit=5, include_archive=False)
                candidates = []
                for hit in hits:
                    bucket = await self.manager.get(hit["id"])
                    if bucket and eligible(bucket):
                        candidates.append(bucket)
            except Exception:
                candidates = []
            proposal = await self.propose(incoming, candidates)
            target = next((b for b in candidates if proposal and b["id"] == proposal.get("target_id")), None)
            if target and proposal["relation"] in {"duplicate", "progress", "supersedes"}:
                revision = memory_revision(target)
                fresh = await self.manager.get(target["id"])
                if fresh and memory_revision(fresh) == revision:
                    if proposal["relation"] == "duplicate":
                        return target["id"], target["metadata"].get("name", ""), True
                    ring = {"id": incoming_id, "content": content, "kind": "update", "source": "tarn_reconciliation",
                            "created": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(), "source_id": incoming_id + ":body",
                            **{key: create_args[key] for key in ("date", "name", "tags", "valence", "arousal", "importance") if key in create_args}}
                    updated = copy.deepcopy(fresh)
                    comments = [*(updated["metadata"].get("comments") or []), ring]
                    updated["metadata"]["comments"] = comments
                    metadata = self.view_metadata(updated, proposal)
                    if await self.manager.update(target["id"], expected_revision=revision, comments=comments, comment_count=len(comments), extra_metadata=metadata):
                        return target["id"], target["metadata"].get("name", ""), True
                # Concurrent edits invalidate the decision. Preserve the incoming write.
                proposal = None
            bucket_id = await self.manager.create(content=content, bucket_id=incoming_id, **create_args)
            bucket = await self.manager.get(bucket_id)
            if bucket and proposal and proposal["relation"] not in {"duplicate", "progress", "supersedes"}:
                metadata = self.view_metadata(bucket, proposal)
                if target:
                    metadata["tarn_related"] = {"bucket_id": target["id"], "relation": proposal["relation"]}
                await self.manager.update(bucket_id, expected_revision=memory_revision(bucket), extra_metadata=metadata)
                if target and proposal["relation"] == "conflict":
                    conflicts = [*(target["metadata"].get("tarn_conflicts") or []), {"bucket_id": bucket_id, "evidence": proposal["relation_evidence"]}]
                    await self.manager.update(target["id"], expected_revision=memory_revision(target), extra_metadata={"tarn_conflicts": conflicts})
            return bucket_id, create_args.get("name") or bucket_id, False
