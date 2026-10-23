import ast
import copy
import unittest
from pathlib import Path
from types import SimpleNamespace

from memory_preview import recent_ring_preview


class MemoryPreviewTests(unittest.TestCase):
    def bucket(self):
        return {"id": "abc123", "content": "原始正文" * 200, "metadata": {"comments": [
            {"created": "2026-10-21T12:26:11+08:00", "content": "暂定黑猫"},
            {"created": "2026-10-21T15:20:21+08:00", "content": "改选 Jellycat 黑龙"},
            {"created": "2026-10-22T01:13:06+08:00", "content": "改成 USB 接线"},
            {"created": "2026-10-22T11:29:20+08:00", "content": "找到560那家"},
        ]}}

    def test_long_body_cannot_hide_recent_updates(self):
        bucket = self.bucket()
        before = copy.deepcopy(bucket)
        text = recent_ring_preview(bucket, bucket["content"])
        self.assertLess(text.index("560"), text.index("USB"))
        self.assertLess(text.index("USB"), text.index("Jellycat"))
        self.assertLess(text.index("Jellycat"), text.index("原始正文"))
        self.assertNotIn("暂定黑猫", text)
        self.assertIn("另有 1 条年轮未展示", text)
        self.assertIn("已截断", text)
        self.assertIn('read_bucket(bucket_id="abc123")', text)
        self.assertEqual(before, bucket)

    def test_timezone_order_not_lexical_or_list_order(self):
        bucket = {"id": "x", "metadata": {"comments": [
            {"created": "2026-10-22T10:00:00+08:00", "content": "早"},
            {"created": "2026-10-22T03:00:00Z", "content": "晚"},
        ]}}
        text = recent_ring_preview(bucket, "body")
        self.assertLess(text.index("晚"), text.index("早"))

    def test_invalid_comments_and_missing_dates(self):
        bucket = {"id": "x", "metadata": {"comments": [None, {}, {"content": "旧"}, {"content": "新"}]}}
        text = recent_ring_preview(bucket, "body")
        self.assertIn("显示 2/2 条", text)
        self.assertLess(text.index("]\n新"), text.index("]\n旧"))
        bucket["metadata"]["comments"] = None
        self.assertIn("年轮：0 条", recent_ring_preview(bucket, "body"))

    def test_long_rings_have_independent_bounded_previews(self):
        bucket = self.bucket()
        for ring in bucket["metadata"]["comments"]:
            ring["content"] *= 1000
        text = recent_ring_preview(bucket, bucket["content"])
        self.assertIn("Jellycat", text)
        self.assertIn("USB", text)
        self.assertIn("560", text)
        self.assertEqual(text.count("已截断"), 4)
        self.assertLess(len(text), 2000)


class IntrospectionRegression(unittest.IsolatedAsyncioTestCase):
    async def test_tool_uses_ring_preview_without_writes(self):
        tree = ast.parse(Path(__file__).with_name("server.py").read_text())
        fn = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "introspection")
        fn.decorator_list = []
        bucket = MemoryPreviewTests().bucket()
        bucket["metadata"].update({"created": "2026-10-20", "type": "dynamic"})
        async def started(): pass
        async def list_all(**kwargs): return [bucket]
        namespace = {
            "decay_engine": SimpleNamespace(ensure_started=started),
            "bucket_mgr": SimpleNamespace(list_all=list_all),
            "_int_between": lambda value, default, low, high: max(low, min(high, value)),
            "_filter_by_created_date": lambda items, **kwargs: (items, ""),
            "bucket_content_for_recall": lambda b: b["content"],
            "recent_ring_preview": recent_ring_preview,
            "embedding_engine": None,
            "_profile_fact_candidate_hint": lambda *args: "",
        }
        exec(compile(ast.Module(body=[fn], type_ignores=[]), "introspection-test", "exec"), namespace)
        text = await namespace["introspection"]()
        self.assertIn("Jellycat", text)
        self.assertIn("USB", text)
        self.assertIn("560", text)
        self.assertNotIn("暂定黑猫", text)


if __name__ == "__main__":
    unittest.main()
