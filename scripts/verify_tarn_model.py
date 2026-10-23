"""Opt-in live secondary-model check using synthetic sources and a temporary cache."""

import asyncio
import copy
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dehydrator import Dehydrator
from memory_reconciliation import MemoryReconciler
from utils import load_config


async def main():
    config = copy.deepcopy(load_config())
    with tempfile.TemporaryDirectory(prefix="tarn-model-check-") as root:
        config["buckets_dir"] = root
        generator = Dehydrator(config)
        if not generator.client:
            raise RuntimeError("Secondary model not configured")
        engine = MemoryReconciler(None, lambda: generator)
        try:
            toy = {"id": "fixture-toy", "content": "最初考虑黑猫玩偶。", "metadata": {"comments": [
                {"id": "r1", "created": "2026-10-21T12:00:00+08:00", "content": "黑猫太小，改选Jellycat黑龙大号。"},
                {"id": "r2", "created": "2026-10-21T18:00:00+08:00", "content": "不放内置电池，改成USB接线。"},
                {"id": "r3", "created": "2026-10-22T10:00:00+08:00", "content": "找到卖560元的卖家，尚未下单。"},
            ]}}
            result = await engine.propose(toy, [], fixed_target=toy["id"])
            assert result, "toy proposal rejected"
            text = " ".join(fact["text"] for fact in result["facts"])
            assert all(term in text for term in ("黑龙", "USB", "560")), "a current detail was lost"
            assert "已购买" not in text and "已下单" not in text, "intention became completion"
            print("PASS: current choice, wiring and seller retained separately", flush=True)

            previous = {"id": "fixture-exam", "content": "复议结果没有变化，接下来可能重考。", "metadata": {"name": "复议结果"}}
            incoming = {"id": "fixture-new", "content": "复议分数未变，已报名10月28日重考，正在选口语时间。", "metadata": {}}
            result = await engine.propose(incoming, [previous])
            assert result and result["target_id"] == previous["id"] and result["relation"] in {"progress", "supersedes"}, "exam progress not recognized"
            print("PASS: result and subsequent registration recognized as progress", flush=True)

            mixed = {"id": "fixture-reflection", "content": "今天讨论睡姿和体测800米。\n\n### reflection\n我读完一篇记忆架构文章，更喜欢把记忆保存在模型外部。", "metadata": {}}
            result = await engine.propose(mixed, [], fixed_target=mixed["id"])
            if result and result["reflections"]:
                print("Synthetic reflection result:", result, flush=True)
            assert result and not result["reflections"], "unrelated reflection remains attached"
            print("PASS: unrelated reflection excluded from the current event view", flush=True)
        finally:
            await generator.client.close()


if __name__ == "__main__":
    asyncio.run(main())
