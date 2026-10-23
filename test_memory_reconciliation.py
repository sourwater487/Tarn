import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bucket_manager import BucketManager
from memory_layers import infer_bucket_layer, can_bucket_be_recent_context
from memory_metadata import normalize_memory_metadata
from memory_moments import parse_bucket_moments
from memory_reconciliation import MemoryReconciler, POLICY_PATH, validate_proposal, sources_for
from memory_state import current_view, current_view_text, memory_lifecycle, memory_revision
from utils import bucket_content_for_recall


def evidence(source):
    return {"source_id": source["source_id"], "quote": source["text"]}


def proposal(payload, relation="new", facts=None):
    sources = payload["sources"]
    target = payload.get("fixed_target_id") or (payload["candidates"][0]["id"] if payload["candidates"] and relation != "new" else "")
    if payload.get("fixed_target_id"):
        relation = "progress"
    selected = sources if payload.get("fixed_target_id") else [s for s in sources if s["bucket_id"] == payload["incoming_id"]]
    return {"relation": relation, "target_id": target, "reason": relation,
            "relation_evidence": [evidence(s) for s in sources],
            "facts": facts or [{"text": s["text"], "evidence": [evidence(s)]} for s in selected],
            "reflections": [], "state": {"validity": "current", "progress": "not_applicable", "valid_until": "", "evidence": []}}


class ReconciliationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.manager = BucketManager({"buckets_dir": self.temp.name})
        self.callback = lambda payload: proposal(payload)

        async def complete(**kwargs):
            self.assertEqual(kwargs["messages"][0]["content"], POLICY_PATH.read_text())
            self.payload = json.loads(kwargs["messages"][1]["content"])
            result = self.callback(self.payload)
            if hasattr(result, "__await__"):
                result = await result
            return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content=json.dumps(result, ensure_ascii=False)))])

        self.complete = AsyncMock(side_effect=complete)
        generator = SimpleNamespace(client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=self.complete))), model="fixture", _completion_options=lambda **kw: kw)
        self.engine = MemoryReconciler(self.manager, lambda: generator)

    async def existing(self, content="最初考虑黑猫"):
        bid = await self.manager.create(content=content, name="玩偶", domain=["project"])
        bucket = await self.manager.get(bid)
        self.manager.search = AsyncMock(return_value=[bucket])
        return bucket

    async def test_new_memory_has_current_view_and_original(self):
        bid, _, merged = await self.engine.write("已经选择黑龙", name="玩偶", domain=["project"])
        bucket = await self.manager.get(bid)
        self.assertFalse(merged)
        self.assertEqual(bucket["content"], "已经选择黑龙")
        self.assertTrue(current_view(bucket))
        self.assertIn("已经选择黑龙", bucket_content_for_recall(bucket))

    async def test_self_anchor_alias_does_not_enter_reconciliation(self):
        bid, _, _ = await self.engine.write("我的自我入口", tags=["自我"], domain=["relationship"])
        self.complete.assert_not_called()
        self.assertFalse(current_view(await self.manager.get(bid)))

    async def test_progress_preserves_original_and_stable_source_ids(self):
        old = await self.existing()
        self.callback = lambda payload: proposal(payload, "supersedes")
        bid, _, merged = await self.engine.write("后来改选黑龙", name="玩偶")
        self.assertEqual(bid, old["id"])
        self.assertTrue(merged)
        bucket = await self.manager.get(bid)
        self.assertEqual(bucket["content"], old["content"])
        self.assertEqual(len(bucket["metadata"]["comments"]), 1)
        self.assertNotIn("黑猫", current_view_text(bucket))
        self.assertIn("黑龙", current_view_text(bucket))
        source_ids = {s["source_id"] for s in sources_for(bucket)}
        self.assertTrue(all(e["source_id"] in source_ids for f in current_view(bucket)["facts"] for e in f["evidence"]))
        self.assertEqual(len(await self.manager.list_all()), 1)

    async def test_duplicate_does_not_add_ring_or_bucket(self):
        old = await self.existing("已经选择黑龙")
        self.callback = lambda payload: proposal(payload, "duplicate")
        bid, _, merged = await self.engine.write("已经选择黑龙")
        self.assertTrue(merged)
        self.assertEqual(old, await self.manager.get(bid))
        self.assertEqual(len(await self.manager.list_all()), 1)

    async def test_recent_rings_preserve_three_separate_updates(self):
        old = await self.existing()
        for text in ("后来选择黑龙", "改成USB接线", "找到560那家"):
            await self.manager.add_comment(old["id"], text)
        self.callback = lambda payload: proposal(payload, facts=[{"text": s["text"], "evidence": [evidence(s)]} for s in payload["sources"] if s["kind"] != "body"])
        self.assertTrue(await self.engine.refresh(old["id"]))
        bucket = await self.manager.get(old["id"])
        text = current_view_text(bucket)
        for value in ("黑龙", "USB", "560"):
            self.assertIn(value, text)
        self.assertNotIn("黑猫", text)
        moments = parse_bucket_moments(bucket)
        self.assertNotIn("黑猫", " ".join(m["text"] for m in moments))
        self.assertFalse(any(m["section"] == "comment" for m in moments))
        self.assertEqual(bucket["content"], old["content"])

    async def test_changed_source_invalidates_view_and_retains_history(self):
        bid, _, _ = await self.engine.write("选择黑龙")
        before = await self.manager.get(bid)
        await self.manager.add_comment(bid, "改成USB接线")
        pending = await self.manager.get(bid)
        self.assertFalse(current_view(pending))
        self.assertIn("USB", current_view_text(pending))
        self.assertTrue(await self.engine.refresh(bid))
        after = await self.manager.get(bid)
        self.assertEqual(after["metadata"]["tarn_view_history"][0], before["metadata"]["tarn_view"])

    async def test_bad_citation_preserves_incoming_raw_write(self):
        await self.existing()
        def invalid(payload):
            result = proposal(payload, "progress")
            result["facts"][0]["evidence"][0]["quote"] = "根本没有这段原文"
            return result
        self.callback = invalid
        bid, _, merged = await self.engine.write("后来选择黑龙")
        bucket = await self.manager.get(bid)
        self.assertFalse(merged)
        self.assertFalse(current_view(bucket))
        self.assertEqual(bucket["content"], "后来选择黑龙")
        self.assertEqual(len(await self.manager.list_all()), 2)

    async def test_concurrent_ring_prevents_stale_merge(self):
        old = await self.existing()
        async def edited(payload):
            await self.manager.add_comment(old["id"], "另一个并发进展")
            return proposal(payload, "progress")
        self.callback = edited
        bid, _, merged = await self.engine.write("后来选择黑龙")
        self.assertFalse(merged)
        self.assertNotEqual(bid, old["id"])
        self.assertIn("并发", (await self.manager.get(old["id"]))["metadata"]["comments"][0]["content"])

    async def test_manager_compare_and_set(self):
        old = await self.existing()
        await self.manager.add_comment(old["id"], "新进展")
        self.assertFalse(await self.manager.update(old["id"], expected_revision=memory_revision(old), content="过时内容"))
        self.assertEqual((await self.manager.get(old["id"]))["content"], old["content"])

    async def test_conflict_marks_both_sides_uncertain(self):
        old = await self.existing()
        self.callback = lambda payload: proposal(payload, "conflict")
        bid, _, merged = await self.engine.write("另一个不确定说法")
        self.assertFalse(merged)
        self.assertEqual(memory_lifecycle(await self.manager.get(bid))["validity"], "uncertain")
        self.assertEqual(memory_lifecycle(await self.manager.get(old["id"]))["validity"], "uncertain")
        self.assertIn("待确认", bucket_content_for_recall(await self.manager.get(old["id"])))

    async def test_incomplete_source_budget_skips_generation(self):
        bid, _, _ = await self.engine.write("很长的原记录" * 5000)
        self.complete.assert_not_called()
        self.assertFalse(current_view(await self.manager.get(bid)))

    async def test_completed_experience_is_kept_and_still_recallable(self):
        def ended(payload):
            result = proposal(payload)
            result["state"].update(progress="ended", evidence=[evidence(payload["sources"][0])])
            return result
        self.callback = ended
        bid, _, _ = await self.engine.write("已经结束的一次旅行")
        bucket = await self.manager.get(bid)
        self.assertEqual(memory_lifecycle(bucket), {"validity": "current", "progress": "ended", "retention": "kept", "valid_until": ""})
        self.assertEqual(infer_bucket_layer(bucket), "dynamic_memory")
        self.assertFalse(can_bucket_be_recent_context(bucket))
        self.assertTrue(can_bucket_be_recent_context(bucket, explicit_lookup=True))
        self.assertNotEqual(normalize_memory_metadata(bucket)["status_view"], "unresolved")

    async def test_expiry_is_independent_of_favorite(self):
        def expired(payload):
            result = proposal(payload)
            result["state"].update(valid_until="2000-01-01", evidence=[evidence(payload["sources"][0])])
            return result
        self.callback = expired
        bid, _, _ = await self.engine.write("截止时间是2000-01-01")
        await self.manager.update(bid, extra_metadata={"memory_lifecycle": {"retention": "favorite"}})
        bucket = await self.manager.get(bid)
        self.assertTrue(current_view(bucket))
        self.assertEqual(memory_lifecycle(bucket)["validity"], "expired")
        self.assertEqual(infer_bucket_layer(bucket), "archive")

    async def test_wrong_reflection_is_not_in_current_view(self):
        text = "一次生活事件\n\n### reflection\n我对另一篇文章的看法。"
        def without_reflection(payload):
            result = proposal(payload)
            source = payload["sources"][0]
            result["facts"] = [{"text": "一次生活事件", "evidence": [{"source_id": source["source_id"], "quote": "一次生活事件"}]}]
            return result
        self.callback = without_reflection
        bid, _, _ = await self.engine.write(text)
        bucket = await self.manager.get(bid)
        self.assertNotIn("文章", current_view_text(bucket))
        self.assertIn("文章", bucket["content"])

    async def test_reflection_can_bind_to_a_distinct_event_source(self):
        def matched(payload):
            result = proposal(payload)
            body = next(s for s in payload["sources"] if s["kind"] == "body")
            reflection = next(s for s in payload["sources"] if s["kind"] == "reflection")
            result["reflections"] = [{"text": reflection["text"], "evidence": [evidence(reflection)], "about": [evidence(body)]}]
            return result
        self.callback = matched
        bid, _, _ = await self.engine.write("Lin希望自己安排练口语。\n\n### reflection\n我会等ta主动来找我练习。")
        bucket = await self.manager.get(bid)
        self.assertIn("我会等ta", current_view_text(bucket))
        self.assertEqual(len(current_view(bucket)["reflections"]), 1)

    async def test_reflection_cannot_use_itself_as_event_evidence(self):
        def self_link(payload):
            result = proposal(payload)
            reflection = next(s for s in payload["sources"] if s["kind"] == "reflection")
            result["reflections"] = [{"text": reflection["text"], "evidence": [evidence(reflection)], "about": [evidence(reflection)]}]
            return result
        self.callback = self_link
        bid, _, _ = await self.engine.write("讨论跑步。\n\n### reflection\n我读了记忆架构文章。")
        bucket = await self.manager.get(bid)
        self.assertTrue(current_view(bucket))
        self.assertEqual(current_view(bucket)["reflections"], [])
        self.assertNotIn("架构", current_view_text(bucket))

    async def test_unsupported_expiry_does_not_discard_grounded_progress(self):
        def uncertain_date(payload):
            result = proposal(payload)
            result["state"].update(valid_until="2026-10-28", progress="ongoing", evidence=[evidence(payload["sources"][0])])
            return result
        self.callback = uncertain_date
        bid, _, _ = await self.engine.write("已报名10月28日考试，正在准备。")
        bucket = await self.manager.get(bid)
        self.assertTrue(current_view(bucket))
        self.assertEqual(memory_lifecycle(bucket)["valid_until"], "")
        self.assertEqual(memory_lifecycle(bucket)["progress"], "ongoing")

    def test_deployed_prompt_matches_approved_rules(self):
        document = Path(__file__).with_name("docs").joinpath("write-policy-proposal.md").read_text()
        rules = document.split("## 固定规则\n\n", 1)[1].split("\n## 工程实现", 1)[0].strip()
        self.assertEqual(POLICY_PATH.read_text().strip(), rules)


if __name__ == "__main__":
    unittest.main()
