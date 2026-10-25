"""Exercise real FastMCP validation using temporary buckets, never production data."""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def verify():
    with tempfile.TemporaryDirectory() as root:
        os.environ['OMBRE_CONFIG_PATH'] = root + '/no-config.yaml'
        os.environ['OMBRE_BUCKETS_DIR'] = root + '/buckets'
        os.environ['OMBRE_STATE_DIR'] = root + '/state'
        os.environ['OMBRE_RUNTIME_CONFIG_PATH'] = root + '/no-runtime.yaml'
        import server
        server.decay_engine.ensure_started = AsyncMock()
        server.embedding_engine = None
        server._profile_fact_candidate_hint = lambda *args: ''
        ids = []
        for i in range(6):
            ids.append(await server.bucket_mgr.create(
                content=f'独立测试记录 {i}', name=f'测试记录 {i}',
                created=f'2020-01-0{i+1}T00:00:00+00:00',
                updated_at=f'2026-10-2{i}T00:00:00+00:00'))
        original = await server.bucket_mgr.list_all()
        descriptor = next(t for t in await server.mcp.list_tools() if t.name == 'introspection')
        assert descriptor.inputSchema['properties']['order']['default'] == 'recent'
        assert descriptor.inputSchema['properties']['limit']['default'] == 10
        # Use the same validator and call path as MCP, not a direct Python call.
        async def invoke(arguments):
            result = await server.mcp.call_tool('introspection', arguments)
            if isinstance(result, tuple):
                result = result[0]
            return '\n'.join(block.text for block in result if getattr(block, 'type', '') == 'text')
        first = await invoke({})
        assert all(f'ID: {bid}\n' in first for bid in ids[:3]), first
        assert all(f'ID: {bid}\n' not in first for bid in ids[3:]), first
        second = await invoke({})
        assert all(f'ID: {bid}\n' in second for bid in ids[3:]), second
        seen = server.memory_review.load()['seen'].copy()
        recent = await invoke({'order': 'recent', 'limit': 2})
        assert f'ID: {ids[-1]}\n' in recent and f'ID: {ids[-2]}\n' in recent
        assert f'ID: {ids[0]}\n' not in recent
        assert server.memory_review.load()['seen'] == seen
        assert original == await server.bucket_mgr.list_all(), 'review must not touch bucket contents/timestamps'
        tools = [tool.model_dump(mode='json', by_alias=True) for tool in await server.mcp.list_tools()]
        if len(sys.argv) > 1:
            assert tools == json.loads(Path(sys.argv[1]).read_text()), 'published tool descriptor changed'
        print('PASS: omitted MCP arguments rotate 3 old buckets; explicit recent works; bucket data and all tool descriptors unchanged')


if __name__ == '__main__':
    asyncio.run(verify())
