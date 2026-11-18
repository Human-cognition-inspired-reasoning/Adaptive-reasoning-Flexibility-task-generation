(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const PEGS = ['A', 'B', 'C', 'D'];
  const offline = window.HANOI_OFFLINE;
  if (!offline) {
    $('error').textContent = 'Hanoi_Human_Evaluation.html 파일을 열어 주세요.';
    $('error').hidden = false;
    return;
  }
  const KEY = offline.keys.session, DRAFT = offline.keys.draft;
  let state = null, plan = [], source = null, busy = false, pending = null, instructions = [];
  const storage = offline.storage;
  async function api(path, body) { return offline.api(path, body); }
  function renderSaveStatus() {
    $('save-status').textContent = offline.status();
    $('save-status').setAttribute('data-warning', String(offline.storageWarning()));
  }
  function error(value) { $('error').textContent = value.message || String(value); $('error').hidden = false; }
  function clearError() { $('error').hidden = true; }
  function el(tag, className, text) { const node = document.createElement(tag); if (className) node.className = className; if (text !== undefined) node.textContent = text; return node; }
  function boardFrom(positions) {
    const board = Object.fromEntries(PEGS.map(p => [p, []]));
    for (let disk = 3; disk >= 1; disk--) board[positions[disk - 1]].push(disk);
    return board;
  }
  function replay(positions, moves) {
    const board = boardFrom(positions), steps = [];
    for (const command of moves) {
      if (typeof command !== 'string' || command.length !== 2 || !PEGS.includes(command[0]) || !PEGS.includes(command[1]) || command[0] === command[1]) return null;
      const from = board[command[0]], to = board[command[1]], disk = from.at(-1);
      if (disk === undefined || (to.length && to.at(-1) < disk)) return null;
      from.pop(); to.push(disk); steps.push({command, disk});
    }
    return {board, steps};
  }
  function renderBoard(mount, board, interactive = false) {
    mount.replaceChildren();
    for (const peg of PEGS) {
      const button = el('button', 'peg' + (interactive && source === peg ? ' selected' : ''));
      button.type = 'button'; button.disabled = !interactive || busy || !!pending || state?.awaiting_continue;
      button.setAttribute('aria-label', `기둥 ${peg}: ${board[peg].length ? '아래부터 원반 ' + board[peg].join(', ') : '비어 있음'}`);
      if (interactive) { button.setAttribute('aria-pressed', String(source === peg)); button.addEventListener('click', () => clickPeg(peg)); }
      button.append(el('span', 'rod'), el('span', 'base'), el('span', 'peg-letter', peg));
      const stack = el('span', 'stack');
      for (const disk of board[peg]) stack.append(el('span', `disk disk-${disk}`, disk));
      button.append(stack); mount.append(button);
    }
  }
  function moveList(mount, steps) {
    mount.replaceChildren();
    steps.forEach(({command, disk}, index) => {
      const item = el('li', 'move-chip');
      item.append(el('small', '', index + 1), el('span', 'disk-label', `원반 ${disk}`), el('strong', '', command.split('').join(' → ')));
      mount.append(item);
    });
  }
  function saveDraft() {
    if (!state) return;
    storage.set(DRAFT, JSON.stringify({session_id: state.session_id, turn_token: state.turn_token, plan, pending}));
    renderSaveStatus();
  }
  function clickPeg(peg) {
    if (busy || pending || state.awaiting_continue || plan.length >= 30) return;
    const result = replay(state.initial_positions, plan);
    if (!source) {
      if (!result.board[peg].length) { $('move-hint').textContent = '원반이 있는 출발 기둥을 선택하세요.'; return; }
      source = peg; renderPlan(); return;
    }
    if (source === peg) { source = null; renderPlan(); return; }
    const candidate = [...plan, source + peg];
    if (!replay(state.initial_positions, candidate)) { $('move-hint').textContent = '큰 원반을 작은 원반 위에 놓을 수 없습니다.'; return; }
    plan = candidate; source = null; saveDraft(); renderPlan();
  }
  function renderPlan() {
    if (!state || !state.initial_positions) return;
    const result = replay(state.initial_positions, plan);
    renderBoard($('board'), result.board, true); moveList($('moves'), result.steps);
    $('move-count').textContent = `${plan.length} / 30`; $('empty-plan').hidden = !!plan.length;
    $('undo').disabled = !plan.length || busy || !!pending || state.awaiting_continue;
    $('reset').disabled = !plan.length || busy || !!pending || state.awaiting_continue;
    $('submit').disabled = busy || (!pending && !plan.length);
    $('submit').textContent = pending ? '저장 확인 / 다시 전송' : '계획 제출';
    $('continue').disabled = busy;
    $('submission-bar').hidden = state.awaiting_continue;
    $('feedback-panel').hidden = !state.awaiting_continue;
    $('move-hint').textContent = state.awaiting_continue ? '피드백을 확인한 뒤 계속하기를 누르세요.' : pending ? '제출 저장을 확인해 주세요.' : plan.length >= 30 ? '이동 계획을 제출하거나 수정하세요.' : source ? `${source}에서 이동할 도착 기둥을 선택하세요.` : '출발 기둥을 선택하세요.';
  }
  function badgeClass(feedback) { return feedback === '정답' ? 'correct' : feedback === '오답' ? 'incorrect' : 'violation'; }
  function renderHistory() {
    $('history').replaceChildren(); $('history-count').textContent = state.history.length;
    if (!state.history.length) $('history').append(el('p', 'history-empty', '아직 제출한 계획이 없습니다.'));
    for (const entry of [...state.history].reverse()) {
      const item = el('details', 'history-item'), summary = el('summary');
      summary.append(el('strong', '', `제출 ${entry.number}`), el('span', `badge ${badgeClass(entry.feedback)}`, entry.feedback));
      const detail = el('div', 'history-detail'); detail.append(el('p', '', '이 제출의 초기 배치'));
      const board = el('div', 'board'); renderBoard(board, boardFrom(entry.initial_positions)); detail.append(board);
      const list = el('ol', 'move-list'); const result = entry.moves && replay(entry.initial_positions, entry.moves);
      if (result) moveList(list, result.steps); else list.append(el('li', 'subtle', '이동 계획을 확인할 수 없습니다.'));
      detail.append(list);
      const copy = el('button', 'secondary', '이 계획을 불러와 수정하기');
      copy.disabled = busy || !!pending || state.awaiting_continue || !entry.moves;
      copy.addEventListener('click', () => {
        if (!replay(state.initial_positions, entry.moves)) return error(Error('현재 초기 배치에서 실행할 수 없는 이동이 포함되어 있습니다. 새 계획을 작성하세요.'));
        plan = [...entry.moves]; source = null; clearError(); saveDraft(); renderPlan();
      });
      detail.append(copy); item.append(summary, detail); $('history').append(item);
    }
  }
  function renderInstructions() {
    for (const id of ['welcome-instructions', 'help-instructions']) { $(id).replaceChildren(); instructions.forEach(text => $(id).append(el('p', '', text))); }
  }
  function accept(next, preserveDraft = false) {
    const prior = state; state = next; instructions = next.instructions; storage.set(KEY, next.session_id);
    $('welcome').hidden = true; $('task').hidden = next.complete; $('complete').hidden = !next.complete; $('export').hidden = false;
    renderSaveStatus(); renderInstructions(); source = null; pending = null;
    plan = [];
    if (next.awaiting_continue) plan = next.history.at(-1)?.moves || [];
    else if (preserveDraft) {
      try {
        const draft = JSON.parse(storage.get(DRAFT));
        if (draft?.session_id === next.session_id && draft.turn_token === next.turn_token && Array.isArray(draft.plan) && draft.plan.length <= 30 && replay(next.initial_positions, draft.plan)) {
          plan = draft.plan; pending = draft.pending || null;
        }
      } catch {}
    }
    if (next.awaiting_continue || next.complete || (prior && prior.turn_token !== next.turn_token && !preserveDraft)) storage.remove(DRAFT);
    $('problem-title').replaceChildren(document.createTextNode(`문제 ${next.problem_number} `), el('span', '', `/ ${next.problem_count}`));
    $('participant-label').textContent = `참여 코드 ${next.participant_id}`;
    $('feedback').textContent = next.feedback || ''; $('feedback').className = badgeClass(next.feedback);
    if (!next.complete) { renderPlan(); renderHistory(); }
  }
  async function resume(token) {
    clearError(); busy = true;
    try { const next = await api('/api/session?session=' + encodeURIComponent(token)); busy = false; accept(next, true); }
    catch (value) { error(value); }
    finally { busy = false; }
  }
  $('start-form').addEventListener('submit', async event => {
    event.preventDefault(); if (busy) return; busy = true; $('start').disabled = true; clearError();
    try { const next = await api('/api/start', {participant_id: $('participant').value.trim()}); storage.remove(DRAFT); busy = false; accept(next); }
    catch (value) { error(value); }
    finally { busy = false; $('start').disabled = false; }
  });
  $('resume').addEventListener('click', () => resume(storage.get(KEY)));
  $('undo').addEventListener('click', () => { if (busy || pending || state.awaiting_continue) return; plan.pop(); source = null; saveDraft(); renderPlan(); });
  $('reset').addEventListener('click', () => { if (busy || pending || state.awaiting_continue) return; plan = []; source = null; saveDraft(); renderPlan(); });
  $('submit').addEventListener('click', async () => {
    if (busy || state.awaiting_continue || (!pending && !plan.length)) return;
    if (!pending) pending = {session_id: state.session_id, turn_token: state.turn_token, moves: [...plan]};
    saveDraft(); busy = true; clearError(); renderPlan(); renderHistory();
    try { const next = await api('/api/submit', pending); busy = false; accept(next); }
    catch (value) { error(Error(`${value.message} 같은 제출을 다시 전송해 저장 여부를 확인할 수 있습니다.`)); }
    finally { busy = false; renderPlan(); renderHistory(); }
  });
  $('continue').addEventListener('click', async () => {
    if (busy) return; busy = true; clearError(); renderPlan();
    try { const next = await api('/api/continue', {session_id: state.session_id, turn_token: state.turn_token}); busy = false; accept(next); }
    catch (value) { error(value); }
    finally { busy = false; renderPlan(); }
  });
  function download() {
    if (!state || busy) return;
    try {
      offline.download({session_id: state.session_id, turn_token: state.turn_token, plan});
    } catch (value) { error(value); }
  }
  $('export').addEventListener('click', download); $('export-complete').addEventListener('click', download);
  $('import').addEventListener('change', async event => {
    const file = event.target.files[0]; if (!file || busy) return;
    busy = true; clearError();
    try {
      if (file.size > 10 * 1024 * 1024) throw Error('파일이 너무 큽니다.');
      const data = JSON.parse(await file.text());
      const next = offline.importDocument(data); busy = false; accept(next, true);
    } catch (value) { error(value); }
    finally { busy = false; }
    event.target.value = '';
  });
  $('help-button').addEventListener('click', () => $('help').showModal());
  $('close-help').addEventListener('click', () => $('help').close());
  $('show-initial').addEventListener('click', () => { renderBoard($('initial-board'), boardFrom(state.initial_positions)); $('initial-dialog').showModal(); });
  $('close-initial').addEventListener('click', () => $('initial-dialog').close());
  renderBoard($('intro-board'), boardFrom(['B', 'A', 'C']));
  $('resume').hidden = !storage.get(KEY);
  api('/api/instructions').then(data => { instructions = data.instructions; renderInstructions(); }).catch(error);
})();
