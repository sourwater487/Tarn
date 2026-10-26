"""Old-memory rotation and human-approved, reversible pair merges.

State is separate from bucket timestamps. No model calls or new MCP tools.
"""
import asyncio
import copy
import hashlib
import json
import os
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

import frontmatter
from memory_reconciliation import eligible
from memory_state import current_view, memory_lifecycle, memory_revision


def now():
    return datetime.now(timezone.utc).isoformat()


def atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def fingerprint(bucket):
    meta = {k: v for k, v in bucket['metadata'].items()
            if k not in {'last_active', 'activation_count', 'updated_at'}}
    return hashlib.sha256(json.dumps([bucket['content'], meta], sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


class MemoryReview:
    def __init__(self, manager, state_dir, embeddings=None):
        self.manager = manager
        self.embeddings = embeddings
        self.path = Path(state_dir) / 'memory-review.json'
        self.lock = asyncio.Lock()
        self.recover()

    def load(self):
        if not self.path.exists():
            return {'seen': {}, 'proposals': {}}
        return json.loads(self.path.read_text())

    def save(self, data):
        atomic_write(self.path, json.dumps(data, ensure_ascii=False, indent=2))

    def recover(self):
        data = self.load()
        journal = data.get('transaction')
        if not journal:
            return
        # Interrupted operations roll back before any further merge is accepted.
        for item in journal:
            path = self.manager._find_bucket_file(item['id'])
            if not path or Path(path).read_text() not in (item['before'], item['after']):
                raise RuntimeError('memory merge recovery needs manual review: ' + item['id'])
        for item in journal:
            atomic_write(self.manager._find_bucket_file(item['id']), item['before'])
        data.pop('transaction')
        self.save(data)

    def transact(self, data, changes, proposal, status):
        # This synchronous section does not yield between source checks and commit.
        journal = []
        for bid, after in changes.items():
            path = self.manager._find_bucket_file(bid)
            if not path:
                raise ValueError('源桶已不存在')
            journal.append({'id': bid, 'before': Path(path).read_text(), 'after': after})
        data['transaction'] = journal
        self.save(data)
        try:
            for item in journal:
                atomic_write(self.manager._find_bucket_file(item['id']), item['after'])
            proposal['status'] = status
            proposal['decided_at'] = now()
            data.pop('transaction')
            self.save(data)
        except Exception:
            self.recover()
            raise

    def reserved_ids(self, data):
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        return {bid for batch in data.get('wake_batches', {}).values()
                if not batch.get('delivered_at') and batch['reserved_at'] > cutoff
                for bid in batch['ids']}

    async def wake_batch(self, batch_id, *, acknowledge=False):
        async with self.lock:
            data = self.load()
            batches = data.setdefault('wake_batches', {})
            batch = batches.get(batch_id)
            if acknowledge:
                if not batch:
                    raise ValueError('unknown wake batch')
                if not batch.get('delivered_at'):
                    batch['delivered_at'] = now()
                    for bid in batch['ids']:
                        data['seen'][bid] = batch['delivered_at']
                    self.save(data)
                return batch
            if batch is None:
                reserved = self.reserved_ids(data)
                buckets = await self.manager.list_all(include_archive=False)
                candidates = [b for b in buckets if eligible(b) and b['id'] not in reserved]
                candidates.sort(key=lambda b: (data['seen'].get(b['id'], ''), b['metadata'].get('created', ''), b['id']))
                batch = {'ids': [b['id'] for b in candidates[:3]], 'reserved_at': now()}
                batches[batch_id] = batch
                self.save(data)
            # A retry keeps its original IDs; never silently reserves another batch.
            selected = [await self.manager.get(bid) for bid in batch['ids']]
            return [b for b in selected if b and eligible(b)]

    async def rotate(self, buckets, limit=3):
        async with self.lock:
            data = self.load()
            reserved = self.reserved_ids(data)
            candidates = [b for b in buckets if eligible(b) and b['id'] not in reserved]
            candidates.sort(key=lambda b: (data['seen'].get(b['id'], ''), b['metadata'].get('created', ''), b['id']))
            selected = candidates[:max(1, min(int(limit), 5))]
            stamp = now()
            for bucket in selected:
                data['seen'][bucket['id']] = stamp
            self.save(data)
            return selected

    async def similarity(self, a, b):
        if not self.embeddings:
            return None
        try:
            vectors = await self.embeddings.get_embeddings([a, b])
            if a in vectors and b in vectors:
                return round(self.embeddings._cosine_similarity(vectors[a], vectors[b]), 4)
        except Exception:
            pass
        return None

    async def submit(self, target_id, source_id, reason, origin='model'):
        if target_id == source_id or not str(reason).strip() or len(str(reason)) > 2000:
            raise ValueError('请提供两个不同的桶和简短的合并理由')
        async with self.lock:
            buckets = [await self.manager.get(bid) for bid in (target_id, source_id)]
            if any(not b or not eligible(b) for b in buckets):
                raise ValueError('桶不存在、受保护或已退出整理范围')
            revisions = {b['id']: fingerprint(b) for b in buckets}
            data = self.load()
            for p in data['proposals'].values():
                if p['revisions'] == revisions and p['status'] != 'undone':
                    return p
            score = await self.similarity(target_id, source_id)
            p = {'id': uuid.uuid4().hex[:16], 'target_id': target_id, 'source_id': source_id,
                 'reason': str(reason).strip(), 'origin': origin, 'created': now(), 'status': 'pending',
                 'similarity': score, 'revisions': revisions,
                 'names': [b['metadata'].get('name') or b['id'] for b in buckets]}
            data['proposals'][p['id']] = p
            self.save(data)
            return p

    def preview(self, target, source, proposal):
        merged = copy.deepcopy(target)
        meta = merged['metadata']
        comments = copy.deepcopy(meta.get('comments') or [])
        comments.append({'id': 'merge-' + proposal['id'], 'content': source['content'], 'kind': 'merge_source',
                         'created': source['metadata'].get('created', ''), 'source_bucket_id': source['id'],
                         'source_id': source['id'] + ':body'})
        for index, ring in enumerate(source['metadata'].get('comments') or []):
            if isinstance(ring, dict):
                comments.append({**ring, 'id': source['id'] + '-' + str(ring.get('id', index)),
                                 'source_bucket_id': source['id'],
                                 'source_id': ring.get('source_id') or source['id'] + ':ring:' + str(ring.get('id', index))})
        meta['updated_at'] = now()
        meta['comments'] = comments
        meta['comment_count'] = len(comments)
        meta['merge_sources'] = list(dict.fromkeys([*(meta.get('merge_sources') or []), source['id']]))
        facts, reflections, seen = [], [], set()
        for bucket in (target, source):
            view = current_view(bucket)
            items = view.get('facts') or [{'text': bucket['content'], 'evidence': [{'source_id': bucket['id'] + ':body', 'quote': bucket['content']}]}]
            if not view:
                items = [*items, *[{'text': ring['content'], 'evidence': [{'source_id': ring.get('source_id') or bucket['id'] + ':ring:' + str(ring.get('id', index)), 'quote': ring['content']}]} for index, ring in enumerate(bucket['metadata'].get('comments') or []) if isinstance(ring, dict) and ring.get('content') and ring.get('kind') != 'feel']]
            for fact in items:
                text = fact['text'].strip()
                if text and text not in seen:
                    facts.append(copy.deepcopy(fact))
                    seen.add(text)
            reflections.extend(copy.deepcopy(view.get('reflections') or []))
        meta['tarn_view_history'] = [*(meta.get('tarn_view_history') or []), *([meta['tarn_view']] if meta.get('tarn_view') else [])]
        meta['tarn_view'] = {'id': proposal['id'], 'created': now(), 'facts': facts, 'reflections': reflections,
                             'state': memory_lifecycle(target), 'relation': 'human_merge', 'reason': proposal['reason'],
                             'source_revision': memory_revision(merged, include_state=False)}
        # Target lifecycle remains explicit; the UI displays both states before approval.
        merged['current_view_valid'] = True
        return merged

    async def detail(self, proposal_id):
        self.listing()
        data = self.load()
        p = data['proposals'].get(proposal_id)
        if not p:
            raise ValueError('候选不存在')
        buckets = [await self.manager.get(p[key]) for key in ('target_id', 'source_id')]
        stale = any(not b or fingerprint(b) != p['revisions'].get(b['id']) for b in buckets)
        result = {k: v for k, v in p.items() if k not in {'originals', 'after_revisions'}}
        for bucket in buckets:
            if bucket:
                bucket['current_view_valid'] = bool(current_view(bucket))
                bucket['lifecycle'] = memory_lifecycle(bucket)
        result.update(buckets=buckets, stale=stale,
                      preview=self.preview(*buckets, p) if all(buckets) and p['status'] in {'pending', 'deferred'} else None)
        return result

    async def decide(self, proposal_id, action):
        async with self.lock:
            data = self.load()
            p = data['proposals'].get(proposal_id)
            if not p:
                raise ValueError('候选不存在')
            if action == 'undo':
                if p['status'] != 'approved':
                    raise ValueError('仅已合并记录可撤销')
                buckets = [await self.manager.get(bid) for bid in p['after_revisions']]
                if any(not b or fingerprint(b) != p['after_revisions'][b['id']] for b in buckets):
                    raise ValueError('合并后源桶已有修改，不能覆盖；请先人工核对')
                self.transact(data, p['originals'], p, 'undone')
                return p
            if p['status'] not in {'pending', 'deferred'}:
                raise ValueError('候选已经处理')
            if action in {'reject', 'defer'}:
                p['status'] = {'reject': 'rejected', 'defer': 'deferred'}[action]
                self.save(data)
                return p
            if action != 'approve':
                raise ValueError('未知操作')
            buckets = [await self.manager.get(p[key]) for key in ('target_id', 'source_id')]
            if any(not b or not eligible(b) or fingerprint(b) != p['revisions'][b['id']] for b in buckets):
                raise ValueError('源桶已变化，请重新提交候选并核对预览')
            target, source = buckets
            merged = self.preview(target, source, p)
            hidden = copy.deepcopy(source)
            hidden['metadata'].update(digested=True, merged_into=target['id'], updated_at=now())
            p['originals'] = {b['id']: Path(self.manager._find_bucket_file(b['id'])).read_text() for b in buckets}
            p['after_revisions'] = {b['id']: fingerprint(b) for b in (merged, hidden)}
            changes = {b['id']: frontmatter.dumps(frontmatter.Post(b['content'], **b['metadata'])) for b in (merged, hidden)}
            self.transact(data, changes, p, 'approved')
            return p

    async def scan(self, threshold=0.85, limit=30):
        import numpy as np
        threshold = float(threshold)
        if not 0.5 <= threshold <= 1:
            raise ValueError("相似度阈值应在 0.5 到 1 之间")
        if not self.embeddings:
            return {"created": 0, "with_vectors": 0}
        buckets = [b for b in await self.manager.list_all(include_archive=False) if eligible(b)]
        vectors = await self.embeddings.get_embeddings([b['id'] for b in buckets])
        ids = [b['id'] for b in buckets if b['id'] in vectors]
        if len(ids) < 2:
            return {"created": 0, "with_vectors": len(ids)}
        def compare():
            matrix = np.asarray([vectors[bid] for bid in ids], dtype=float)
            norms = np.linalg.norm(matrix, axis=1, keepdims=True)
            matrix = matrix / np.maximum(norms, 1e-12)
            scores = matrix @ matrix.T
            pairs = np.argwhere(np.triu(scores >= threshold, k=1))
            return sorted(((float(scores[i,j]), ids[i], ids[j]) for i,j in pairs), reverse=True)
        pairs = await asyncio.to_thread(compare)
        created = 0
        for score, a, b in pairs:
            before = set(self.load()['proposals'])
            try:
                p = await self.submit(a, b, f"已有向量余弦相似度 {score:.3f}；待人工核对是否重复。", origin='vector_scan')
            except ValueError:
                continue
            created += int(p['id'] not in before)
            if created >= limit:
                break
        return {"created": created, "with_vectors": len(ids)}

    def listing(self):
        data = self.load()
        changed = False
        for p in data['proposals'].values():
            if p['status'] in {'pending', 'deferred'} and any(
                not self.manager._find_bucket_file(p[key]) for key in ('target_id', 'source_id')
            ):
                p.update(status='invalidated', invalid_reason='源桶已删除', decided_at=now())
                changed = True
        if changed:
            self.save(data)
        return {'reviewed_count': len(data['seen']), 'proposals': [
            {k: v for k, v in p.items() if k not in {'originals', 'after_revisions'}}
            for p in sorted(data['proposals'].values(), key=lambda p: p['created'], reverse=True)]}
