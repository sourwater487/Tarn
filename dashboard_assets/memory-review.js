(() => {
  'use strict';
  const panel = document.getElementById('memory-review-view');
  const labels = {pending:'待审批', deferred:'暂缓', approved:'已合并', rejected:'已拒绝', undone:'已撤销'};
  let filter = 'pending';
  let selected = '';
  let busy = false;
  const text = value => esc(String(value ?? ''));
  const score = value => typeof value === 'number' ? value.toFixed(3) : '暂无';
  const style = document.createElement('style');
  style.textContent = `
    #memory-review-view {max-width:1120px;margin:0 auto;padding:28px 24px 80px;}
    #memory-review-view .review-toolbar {display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin:18px 0;}
    #memory-review-view button,#memory-review-view select,#memory-review-view input {font:inherit;color:var(--text);background:var(--surface,transparent);border:1px solid var(--border,#aaa5);border-radius:7px;padding:10px 12px;min-height:44px;}
    #memory-review-view button {cursor:pointer;} #memory-review-view button:disabled {opacity:.45;cursor:default;}
    #memory-review-view .review-row {display:block;width:100%;text-align:left;margin:10px 0;padding:18px;}
    #memory-review-view .review-meta {font-size:12px;color:var(--text-secondary,var(--text));opacity:.7;margin:8px 0;}
    #memory-review-view .review-columns {display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:24px;}
    #memory-review-view article {min-width:0;border-top:1px solid var(--border,#aaa5);padding-top:16px;}
    #memory-review-view pre {white-space:pre-wrap;overflow-wrap:anywhere;font:inherit;line-height:1.7;}
    #memory-review-view details {margin:14px 0;} #memory-review-view summary {cursor:pointer;padding:8px 0;}
    #memory-review-view .review-notice {padding:12px 0;line-height:1.7;} #memory-review-view input {max-width:100%;box-sizing:border-box;}
    @media(max-width:640px) {#memory-review-view{padding:20px 16px 60px;} #memory-review-view .review-columns{grid-template-columns:1fr;}}
  `;
  document.head.appendChild(style);
  async function api(path = '', body) {
    const response = await authFetch(BASE + '/api/memory-review' + path, body ? {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)} : {});
    if (!response) throw new Error('请先登录');
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || '请求失败');
    return data;
  }
  function state(bucket) {
    const meta = bucket.metadata || {};
    return JSON.stringify(bucket.lifecycle || meta.memory_lifecycle || meta.tarn_view?.state || {}, null, 2);
  }
  function bucketView(bucket, title) {
    if (!bucket) return '<article>源桶已不存在</article>';
    const meta = bucket.metadata || {};
    const view = bucket.current_view_valid === false ? null : meta.tarn_view;
    return '<article><div class="review-meta">' + text(title) + '</div><h3>' + text(meta.name || bucket.id) + '</h3>' +
      '<div class="review-meta">' + text(bucket.id) + '</div>' +
      (view ? '<details open><summary>当前情况（以完整原文核对）</summary><pre>' + text((view.facts || []).map(f => f.text).join('\n\n')) + '</pre></details>' : '') +
      '<details open><summary>完整正文</summary><pre>' + text(bucket.content) + '</pre></details>' +
      '<details><summary>状态</summary><pre>' + text(state(bucket)) + '</pre></details>' +
      '<details open><summary>完整年轮 · ' + (meta.comments || []).length + '</summary>' +
      [...(meta.comments || [])].reverse().map(r => '<div class="review-meta">' + text(r.created) + ' · ' + text(r.kind) + (r.source_bucket_id ? ' · 来源 ' + text(r.source_bucket_id) : '') + '</div><pre>' + text(r.content) + '</pre>').join('') + '</details></article>';
  }
  function error(message) {
    let node = panel.querySelector('[role="alert"]');
    if (!node) {node = document.createElement('div'); node.setAttribute('role','alert');panel.prepend(node);}
    node.textContent = message;
  }
  async function load() {
    selected = '';
    panel.innerHTML = '<h2>合并审批</h2><p>正在读取…</p>';
    try {
      const data = await api();
      const items = data.proposals.filter(p => filter === 'all' || p.status === filter);
      panel.innerHTML = '<h2>合并审批</h2><p class="review-notice">先核对两条记忆，再决定是否合并。原文和年轮保留；相似度不是重复概率。</p>' +
        '<div class="review-toolbar"><label>状态 <select id="review-filter">' + Object.entries({pending:'待审批', deferred:'暂缓', approved:'已合并', rejected:'已拒绝', undone:'已撤销', all:'全部'}).map(([k,v]) => '<option value="'+k+'"'+(filter===k?' selected':'')+'>'+v+'</option>').join('') + '</select></label><button data-action="refresh">刷新</button><label>相似度 ≥ <input id="review-threshold" type="number" min="0.5" max="1" step="0.01" value="0.85" style="width:88px"></label><button data-action="scan">查找相似候选</button></div>' +
        '<div class="review-meta">已轮到回看的桶：' + data.reviewed_count + ' · 候选：' + items.length + '</div>' +
        (items.length ? items.map(p => '<button class="review-row" data-proposal="' + text(p.id) + '"><strong>' + text(p.names.join(' ↔ ')) + '</strong><div class="review-meta">' + text(labels[p.status]) + ' · 向量余弦相似度 ' + score(p.similarity) + ' · ' + text(p.created.slice(0,10)) + '</div><div>' + text(p.reason) + '</div></button>').join('') : '<p class="review-notice">这里暂时没有候选。发现疑似重复后，可以提交到这里一起核对。</p>') +
        '<details><summary>手动提交两个桶</summary><form id="review-submit"><div class="review-toolbar"><input name="target" required placeholder="保留桶 ID" aria-label="保留桶 ID"><input name="source" required placeholder="并入桶 ID" aria-label="并入桶 ID"><input name="reason" required maxlength="2000" placeholder="合并理由" aria-label="合并理由"><button type="submit">提交候选</button></div></form></details>';
    } catch(e) {error(e.message);}
  }
  async function detail(id) {
    selected = id;
    try {
      const p = await api('/' + encodeURIComponent(id));
      if (selected !== id) return;
      const pending = ['pending','deferred'].includes(p.status);
      panel.innerHTML = '<button data-action="back">← 候选列表</button><h2>' + text(p.names.join(' ↔ ')) + '</h2><div class="review-meta">' + text(labels[p.status]) + ' · 向量余弦相似度 ' + score(p.similarity) + '</div><p>' + text(p.reason) + '</p>' +
        (pending && p.stale ? '<p role="alert">源桶已有变化，此候选不能批准。请重新提交并核对。</p>' : '') +
        '<div class="review-columns">' + bucketView(p.buckets[0], '保留这个桶的 ID 与状态') + bucketView(p.buckets[1], '并入这个桶；原桶保留，退出自动浮现') + '</div>' +
        (p.preview ? '<h3>合并后预览</h3><p class="review-notice">保留目标桶正文与状态，并入另一桶正文和年轮作为来源。当前情况仅去掉完全相同的条目，不自动判断冲突事实。</p>' + bucketView(p.preview, '拟合并结果 · 完整内容') + '<details><summary>当前情况的来源依据</summary><pre>' + text(JSON.stringify(p.preview.metadata.tarn_view.facts, null, 2)) + '</pre></details>' : '') +
        '<div class="review-toolbar">' + (pending ? '<button data-action="approve" '+(p.stale?'disabled':'')+'>批准这个合并结果</button><button data-action="defer">暂缓</button><button data-action="reject">拒绝</button>' : '') + (p.status === 'approved' ? '<button data-action="undo">撤销合并</button>' : '') + '</div>';
    } catch(e) {error(e.message);}
  }
  panel.addEventListener('change', e => {if(e.target.id === 'review-filter') {filter=e.target.value;load();}});
  panel.addEventListener('click', async e => {
    const button = e.target.closest('button');
    if (!button || busy) return;
    if (button.dataset.proposal) return detail(button.dataset.proposal);
    const action = button.dataset.action;
    if (['refresh','back'].includes(action)) return load();
    if (action === 'scan') {
      busy = true;
      button.disabled = true;
      const threshold = Number(panel.querySelector('#review-threshold').value);
      try {const result = await api('/scan', {threshold});await load();error('已检查 ' + result.with_vectors + ' 个有向量的桶，新增 ' + result.created + ' 组候选（每次最多 30 组）。');}
      catch(e) {error(e.message);button.disabled = false;}
      finally {busy = false;}
      return;
    }
    if (!['approve','reject','defer','undo'].includes(action)) return;
    busy = true;
    panel.querySelectorAll('button').forEach(b => b.disabled = true);
    try {await api('/' + encodeURIComponent(selected), {action});await detail(selected);}
    catch(e) {await detail(selected);error(e.message);}
    finally {busy = false;}
  });
  panel.addEventListener('submit', async e => {
    if (e.target.id !== 'review-submit') return;
    e.preventDefault();
    if (busy) return;
    busy = true;
    const fields = new FormData(e.target);
    try {const p = await api('', {target_id:fields.get('target').trim(),source_id:fields.get('source').trim(),reason:fields.get('reason').trim()});await detail(p.id);}
    catch(e) {error(e.message);}
    finally {busy = false;}
  });
  window.loadMemoryReview = load;
  if (document.querySelector('.tab.active')?.dataset.tab === 'memory-review') load();
})();
