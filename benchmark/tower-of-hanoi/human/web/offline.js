/* Local transport for the self-contained HTML edition. No network requests. */
(() => {
  'use strict';
  if (!window.HANOI_BUNDLE) return;
  const engine = window.HanoiHumanEngine.createEngine(window.HANOI_BUNDLE);
  const prefix = 'hanoi-human-offline-' + window.HANOI_BUNDLE.bundle_id + '-';
  const keys = {session: prefix + 'session', draft: prefix + 'draft'};
  const memory = new Map(), dirty = new Set();
  let current = null, lastStored = null, storageFailed = false;
  const storage = {
    get(key) {
      if (dirty.has(key)) return memory.get(key) ?? null;
      try { const value = localStorage.getItem(key); if (value !== null) { memory.set(key, value); return value; } }
      catch { storageFailed = true; }
      return memory.get(key) ?? null;
    },
    set(key, value) {
      memory.set(key, value);
      try { localStorage.setItem(key, value); dirty.delete(key); }
      catch { storageFailed = true; dirty.add(key); }
    },
    remove(key) {
      memory.delete(key);
      try { localStorage.removeItem(key); dirty.delete(key); }
      catch { storageFailed = true; dirty.add(key); }
    },
  };
  function status() {
    return storageFailed ? '자동 저장 불가 · JSON을 내려받아 보관하세요' : '이 브라우저에 임시 저장됨 · JSON으로 보관';
  }
  function recordKey(token) { return prefix + 'record-' + token; }
  function persist(next) {
    const serialized = JSON.stringify(engine.export(next));
    storage.set(recordKey(next.session_id), serialized);
    storage.set(keys.session, next.session_id);
    current = next; lastStored = serialized;
    return engine.public(current);
  }
  function load(token) {
    if (typeof token !== 'string' || !/^[a-f0-9]{32}$/.test(token)) throw Error('저장된 진행 JSON을 불러오세요.');
    const serialized = storage.get(recordKey(token));
    if (!serialized) throw Error('이 브라우저에 저장된 진행이 없습니다. 내려받은 JSON을 불러오세요.');
    // A newer checkpoint from another tab must be used before grading a click.
    if (!current || current.session_id !== token || serialized !== lastStored) {
      current = engine.import(JSON.parse(serialized)); lastStored = serialized;
    }
    return current;
  }
  function normalizeDraft(draft, state) {
    if (!draft || draft.session_id !== state.session_id || draft.turn_token !== state.turn_token ||
        !Array.isArray(draft.plan) || draft.plan.length > 30 ||
        !draft.plan.every(move => typeof move === 'string' && /^[ABCD]{2}$/.test(move))) return null;
    return {session_id: draft.session_id, turn_token: draft.turn_token, plan: [...draft.plan], pending: null};
  }
  const perform = (path, body) => {
    if (path === '/api/instructions') return {instructions: window.HANOI_BUNDLE.instructions};
    if (path === '/api/start') {
      storage.remove(keys.draft);
      return persist(engine.create(body.participant_id));
    }
    if (path.startsWith('/api/session?')) return engine.public(load(decodeURIComponent(path.split('session=')[1])));
    if (path === '/api/submit' || path === '/api/continue') {
      const state = load(body.session_id);
      if (path === '/api/submit') return persist(engine.submit(state, body.turn_token, body.moves));
      return persist(engine.advance(state, body.turn_token));
    }
    throw Error('지원하지 않는 로컬 작업입니다.');
  };
  async function api(path, body) {
    if (body && typeof navigator !== 'undefined' && navigator.locks) {
      return navigator.locks.request(prefix + 'write', () => perform(path, body));
    }
    return perform(path, body);
  }
  function exportDocument(draft) {
    if (!current) throw Error('먼저 과제를 시작하세요.');
    const state = load(current.session_id);
    const data = engine.export(state);
    const normalized = normalizeDraft(draft, state);
    if (normalized) data.ui_draft = normalized;
    return data;
  }
  function download(draft) {
    const data = exportDocument(draft);
    const blob = new Blob([JSON.stringify(data, null, 2)], {type: 'application/json;charset=utf-8'});
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = 'Hanoi_' + data.participant_id + '_' + data.session_id.slice(0, 8) + '.json';
    document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
  }
  function importDocument(data) {
    const next = engine.import(data);
    const draft = normalizeDraft(data.ui_draft, next);
    const view = persist(next);
    storage.remove(keys.draft);
    if (draft) storage.set(keys.draft, JSON.stringify(draft));
    return view;
  }
  window.HANOI_OFFLINE = {api, keys, storage, status, storageWarning: () => storageFailed, download, importDocument, exportDocument};
})();
