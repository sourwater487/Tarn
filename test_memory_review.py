import ast
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from bucket_manager import BucketManager
from memory_review import MemoryReview
from memory_state import current_view
from memory_layers import can_bucket_be_recent_context


class ReviewTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.manager = BucketManager({'buckets_dir': self.temp.name + '/buckets'})
        self.review = MemoryReview(self.manager, self.temp.name + '/state')
        self.a = await self.manager.create(content='旧决定：黑猫', name='玩偶', created='2026-01-01T00:00:00+00:00')
        self.b = await self.manager.create(content='后来确定黑龙', name='后续决定', created='2026-02-01T00:00:00+00:00')

    async def proposal(self):
        return await self.review.submit(self.a, self.b, '同一玩偶的后续记录')

    async def test_rotation_persists_without_touching_buckets(self):
        before = await self.manager.list_all()
        first, second = await asyncio.gather(self.review.rotate(before, 1), self.review.rotate(before, 1))
        self.assertNotEqual(first[0]['id'], second[0]['id'])
        self.assertEqual(before, await self.manager.list_all())
        restarted = MemoryReview(self.manager, self.temp.name + '/state')
        self.assertEqual((await restarted.rotate(before, 1))[0]['id'], first[0]['id'])

    async def test_protected_excluded(self):
        await self.manager.update(self.a, pinned=True)
        selected = await self.review.rotate(await self.manager.list_all(), 5)
        self.assertEqual([b['id'] for b in selected], [self.b])
        with self.assertRaises(ValueError):
            await self.proposal()

    async def test_submit_read_only_and_repeat_rejected_suppressed(self):
        before = await self.manager.list_all()
        p = await self.proposal()
        self.assertEqual(before, await self.manager.list_all())
        await self.review.decide(p['id'], 'reject')
        repeated = await self.review.submit(self.b, self.a, '再次提出')
        self.assertEqual(repeated['id'], p['id'])
        self.assertEqual(repeated['status'], 'rejected')
        await self.manager.update(self.b, content='新增证据')
        self.assertNotEqual((await self.proposal())['id'], p['id'])

    async def test_approve_retains_sources_and_undo_restores(self):
        await self.manager.add_comment(self.b, '配线也改了', kind='update')
        before = {bid: Path(self.manager._find_bucket_file(bid)).read_text() for bid in (self.a, self.b)}
        p = await self.proposal()
        detail = await self.review.detail(p['id'])
        expected = detail['preview']
        await self.review.decide(p['id'], 'approve')
        a, b = await self.manager.get(self.a), await self.manager.get(self.b)
        self.assertEqual(a['content'], '旧决定：黑猫')
        self.assertEqual(b['content'], '后来确定黑龙')
        self.assertEqual(a['metadata']['comments'], expected['metadata']['comments'])
        self.assertTrue(current_view(a))
        self.assertTrue(b['metadata']['digested'])
        self.assertEqual(b['metadata']['merged_into'], self.a)
        self.assertFalse(can_bucket_be_recent_context(b))
        self.assertTrue(any(r['content'] == '配线也改了' for r in a['metadata']['comments']))
        await self.review.decide(p['id'], 'undo')
        self.assertEqual(before, {bid: Path(self.manager._find_bucket_file(bid)).read_text() for bid in before})

    async def test_changed_sources_block_approval(self):
        p = await self.proposal()
        await self.manager.update(self.a, content='后来又变了')
        self.assertTrue((await self.review.detail(p['id']))['stale'])
        with self.assertRaises(ValueError):
            await self.review.decide(p['id'], 'approve')
        self.assertNotIn('merged_into', (await self.manager.get(self.b))['metadata'])

    async def test_changed_source_blocks_undo(self):
        p = await self.proposal()
        await self.review.decide(p['id'], 'approve')
        await self.manager.update(self.a, content='合并后新内容')
        with self.assertRaises(ValueError):
            await self.review.decide(p['id'], 'undo')
        self.assertEqual((await self.manager.get(self.a))['content'], '合并后新内容')

    async def test_concurrent_approval_runs_once(self):
        p = await self.proposal()
        results = await asyncio.gather(self.review.decide(p['id'], 'approve'), self.review.decide(p['id'], 'approve'), return_exceptions=True)
        self.assertEqual(sum(isinstance(r, ValueError) for r in results), 1)
        self.assertEqual(len((await self.manager.get(self.a))['metadata']['comments']), 1)

    async def test_restart_recovers_interrupted_transaction(self):
        p = await self.proposal()
        data = self.review.load()
        path = Path(self.manager._find_bucket_file(self.a))
        original = path.read_text()
        after = original + '\npartial merge'
        data['transaction'] = [{'id': self.a, 'before': original, 'after': after}]
        self.review.save(data)
        path.write_text(after)
        restarted = MemoryReview(self.manager, self.temp.name + '/state')
        self.assertEqual(path.read_text(), original)
        self.assertEqual(restarted.load()['proposals'][p['id']]['status'], 'pending')

    async def test_missing_embedding_not_fake_zero(self):
        self.review.embeddings = type('Embeddings', (), {'get_embeddings': AsyncMock(return_value={})})()
        self.assertIsNone((await self.proposal())['similarity'])

    async def test_target_state_changes_invalidate_proposal(self):
        p = await self.proposal()
        await self.manager.update(self.a, extra_metadata={'memory_lifecycle': {'progress': 'ended'}})
        with self.assertRaises(ValueError):
            await self.review.decide(p['id'], 'approve')


    async def test_scan_uses_cached_vectors_and_does_not_repeat_rejection(self):
        embeddings = type('Embeddings', (), {
            'get_embeddings': AsyncMock(return_value={self.a: [1.0, 0.0], self.b: [0.99, 0.01]}),
            '_cosine_similarity': staticmethod(lambda a, b: 0.9999),
        })()
        self.review.embeddings = embeddings
        result = await self.review.scan(0.85)
        self.assertEqual(result['created'], 1)
        p = self.review.listing()['proposals'][0]
        await self.review.decide(p['id'], 'reject')
        self.assertEqual((await self.review.scan(0.85))['created'], 0)
        with self.assertRaises(ValueError):
            await self.review.scan(float('nan'))

    async def test_http_routes_require_auth_and_return_conflicts(self):
        from starlette.responses import JSONResponse
        from types import SimpleNamespace
        tree = ast.parse(Path('server.py').read_text())
        route_names = {'api_memory_review', 'api_memory_review_detail', 'api_memory_review_scan'}
        nodes = [n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name in route_names]
        for node in nodes:
            node.decorator_list = []
        namespace = {'memory_review': self.review, 'MEMORY_ID_RE': __import__('re').compile(r'^[A-Za-z0-9_.:-]{1,128}$'),
                     '_require_dashboard_auth': lambda request: JSONResponse({'error':'unauthorized'},status_code=401)}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), 'routes', 'exec'), namespace)
        request = SimpleNamespace(method='POST', path_params={'proposal_id':'absent'}, json=AsyncMock(return_value={'action':'approve'}))
        for name in route_names:
            self.assertEqual((await namespace[name](request)).status_code, 401)
        request.json.assert_not_called()
        namespace['_require_dashboard_auth'] = lambda request: None
        self.assertEqual((await namespace['api_memory_review_detail'](request)).status_code, 409)
        self.assertEqual((await namespace['api_memory_review'](request)).status_code, 400)

    async def test_failed_file_write_rolls_back(self):
        from unittest.mock import patch
        from memory_review import atomic_write
        p = await self.proposal()
        before = {bid: Path(self.manager._find_bucket_file(bid)).read_text() for bid in (self.a,self.b)}
        failed = False
        def write(path, text):
            nonlocal failed
            if str(path) == self.manager._find_bucket_file(self.b) and not failed:
                failed = True
                raise OSError('simulated full disk')
            atomic_write(path, text)
        with patch('memory_review.atomic_write', side_effect=write):
            with self.assertRaises(OSError):
                await self.review.decide(p['id'], 'approve')
        self.assertEqual(before, {bid:Path(self.manager._find_bucket_file(bid)).read_text() for bid in before})
        self.assertEqual(self.review.load()['proposals'][p['id']]['status'], 'pending')


if __name__ == '__main__':
    unittest.main()
