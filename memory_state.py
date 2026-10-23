"""Versioned current views and independent memory lifecycle dimensions."""

import hashlib
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from favorite_tags import has_favorite_memory_tag

VALIDITY = {"current", "superseded", "expired", "uncertain"}
PROGRESS = {"not_applicable", "pending", "ongoing", "ended"}
RETENTION = {"kept", "favorite", "archived"}


def memory_revision(bucket, *, include_state=True):
    meta = bucket.get("metadata", {})
    fields = ["name", "date", "comments"]
    if include_state:
        fields += ["resolved", "digested", "pinned", "protected", "type", "memory_lifecycle", "tarn_conflicts"]
    payload = {"content": bucket.get("content", ""), **{key: meta.get(key) for key in fields}}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def current_view(bucket):
    view = bucket.get("metadata", {}).get("tarn_view", {})
    if isinstance(view, dict) and view.get("source_revision") == memory_revision(bucket, include_state=False):
        return view
    return {}


def memory_lifecycle(bucket):
    meta = bucket.get("metadata", {})
    view = current_view(bucket)
    state = {**view.get("state", {}), **(meta.get("memory_lifecycle") or {})}
    validity = state.get("validity", "current")
    if meta.get("tarn_conflicts") and not (meta.get("memory_lifecycle") or {}).get("validity"):
        validity = "uncertain"
    progress = state.get("progress", "ended" if meta.get("resolved") else "not_applicable")
    default_retention = "favorite" if has_favorite_memory_tag(meta.get("tags") or []) else "kept"
    retention = state.get("retention", "archived" if meta.get("type") == "archived" or meta.get("resolved") or meta.get("digested") else default_retention)
    deadline = str(state.get("valid_until") or "")
    if deadline and validity == "current":
        try:
            if datetime.now(ZoneInfo("Asia/Shanghai")).date() > datetime.strptime(deadline, "%Y-%m-%d").date():
                validity = "expired"
        except (ValueError, TypeError):
            pass
    return {"validity": validity if validity in VALIDITY else "current",
            "progress": progress if progress in PROGRESS else "not_applicable",
            "retention": retention if retention in RETENTION else "kept",
            "valid_until": deadline}


def lifecycle_label(bucket):
    state = memory_lifecycle(bucket)
    labels = []
    if state["validity"] != "current":
        labels.append({"superseded": "已替代", "expired": "已过期", "uncertain": "待确认"}[state["validity"]])
    if state["progress"] != "not_applicable":
        labels.append({"pending": "待处理", "ongoing": "进行中", "ended": "已结束"}[state["progress"]])
    labels.append({"kept": "保留", "favorite": "收藏", "archived": "归档"}[state["retention"]])
    return " / ".join(labels)


def current_view_text(bucket):
    view = current_view(bucket)
    if not view:
        if bucket.get("metadata", {}).get("tarn_view"):
            rings = [ring for ring in bucket.get("metadata", {}).get("comments", []) if isinstance(ring, dict)]
            updates = "\n".join(str(ring.get("content") or "") for ring in reversed(rings[-3:]))
            return "当前情况待重新整理\n最近年轮：\n" + updates + "\n原记录：\n" + str(bucket.get("content") or "")
        if bucket.get("metadata", {}).get("tarn_conflicts"):
            links = ", ".join(item["bucket_id"] for item in bucket["metadata"]["tarn_conflicts"] if item.get("bucket_id"))
            return "待确认；冲突记录: " + links + "\n" + str(bucket.get("content") or "")
        return ""
    facts = view.get("facts", [])
    if not facts:
        return ""
    parts = [f"当前情况（{lifecycle_label(bucket)}）"]
    seen = set()
    for fact in facts:
        text = str(fact.get("text") or "").strip()
        if text and text not in seen:
            parts.append(text)
            seen.add(text)
    for reflection in view.get("reflections", []):
        text = str(reflection.get("text") or "").strip()
        if text and text not in seen:
            parts.append("### reflection\n" + text)
            seen.add(text)
    return "\n".join(parts)
