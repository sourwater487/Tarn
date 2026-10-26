"""Bounded read previews; stored bodies and rings remain untouched."""

from datetime import datetime, timezone
from memory_state import current_view, current_view_text


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit].rstrip() + "…[已截断]"


def recent_ring_preview(bucket: dict, body: str) -> str:
    """Give recent rings their own budget, independent of the original body."""
    raw = bucket.get("metadata", {}).get("comments", [])
    rings = [item for item in raw if isinstance(item, dict) and item.get("kind") != "merge_source" and str(item.get("content") or "").strip()] if isinstance(raw, list) else []
    if current_view(bucket) and not bucket.get("metadata", {}).get("merge_content_version"):
        return (f'[记忆预览；完整读取: read_bucket(bucket_id="{bucket["id"]}")]\n'
                + _clip(current_view_text(bucket), 1400)
                + f"\n[原记录及 {len(rings)} 条年轮保留在完整记忆中]")

    def date_key(index_item):
        index, item = index_item
        try:
            date = datetime.fromisoformat(str(item.get("created") or "").replace("Z", "+00:00"))
            # Legacy naive timestamps use the application's local timezone.
            if date.tzinfo is None:
                from zoneinfo import ZoneInfo
                date = date.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
            return (0, date.astimezone(timezone.utc).timestamp(), index)
        except (ValueError, TypeError, OverflowError):
            # Unknown dates stay visible, in reverse append order.
            return (1, 0, index)

    ordered = [item for _, item in sorted(enumerate(rings), key=date_key, reverse=True)]
    shown = ordered[:3]
    parts = [f"[记忆预览；完整读取: read_bucket(bucket_id=\"{bucket['id']}\")]"]
    if shown:
        parts.append(f"年轮（日期缺失项优先，其余最新在前；显示 {len(shown)}/{len(rings)} 条）")
        for ring in shown:
            created = _clip(str(ring.get("created") or "日期未知"), 40)
            kind = _clip(str(ring.get("kind") or "comment"), 24)
            parts.append(f"[{created} | {kind}]\n{_clip(str(ring['content']).strip(), 300)}")
        if len(rings) > len(shown):
            parts.append(f"[另有 {len(rings) - len(shown)} 条年轮未展示]")
    else:
        parts.append("年轮：0 条")
    parts.append("原记忆正文（独立预览）\n" + _clip(str(body or "").strip(), 500))
    return "\n".join(parts)
