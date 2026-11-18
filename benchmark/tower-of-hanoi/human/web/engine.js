/* Offline human experiment engine. The Python grader remains the parity oracle.
 * Additional restrictions are checked before basic legality; forbidden but legal
 * moves still update the board. Each submission starts from its initial board.
 */
(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.HanoiHumanEngine = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const PEGS = ['A', 'B', 'C', 'D'];
  const PERSISTENT_ERROR_POLICY = 'previous_rule_compliant_current_failure_v1';
  const FEEDBACK = {Correct: '정답', Incorrect: '오답', 'Rule violation': '규칙 위반',
    'Correct, but rule violation': '정답이지만 규칙 위반'};
  const LABELS = {peg: '기둥 조건', occupancy: '기둥 상태 조건',
    source_sequence: '연속 출발 조건', visit: '선행 방문 조건'};
  const clone = value => JSON.parse(JSON.stringify(value));
  const canonical = value => JSON.stringify(normalize(value));
  function normalize(value) {
    if (Array.isArray(value)) return value.map(normalize);
    if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort().map(key => [key, normalize(value[key])]));
    return value;
  }
  const equal = (a, b) => canonical(a) === canonical(b);
  const mean = rows => rows.length ? rows.reduce((a, b) => a + b, 0) / rows.length : null;
  const validMoves = moves => Array.isArray(moves) && moves.length >= 1 && moves.length <= 30 && moves.every(move => typeof move === 'string' && /^[A-D]{2}$/.test(move) && move[0] !== move[1]);
  const validToken = token => typeof token === 'string' && /^[0-9a-f]{32}$/.test(token);
  const validDate = stamp => typeof stamp === 'string' && Number.isFinite(Date.parse(stamp));
  function token() {
    if (typeof globalThis.crypto !== 'undefined' && globalThis.crypto.getRandomValues) {
      return Array.from(globalThis.crypto.getRandomValues(new Uint8Array(16)), x => x.toString(16).padStart(2, '0')).join('');
    }
    if (typeof require === 'function') return require('node:crypto').randomBytes(16).toString('hex');
    throw Error('이 브라우저에서는 참여 기록을 만들 수 없습니다. 최신 브라우저에서 열어 주세요.');
  }
  function evaluate(moves, round) {
    const restriction = round.rule, positions = round.initial_positions.slice();
    const pegs = Object.fromEntries(PEGS.map(p => [p, [3, 2, 1].filter(d => positions[d - 1] === p)]));
    let previousSource = null, visited = restriction.kind === 'visit' && positions[restriction.visitor_disk - 1] === restriction.visited_peg;
    let baseError = null;
    const violations = [];
    if (!Array.isArray(moves) || moves.length < 1 || moves.length > 30) baseError = {step: null, move: null, disk: null, kind: 'invalid_plan'};
    for (let index = 0; !baseError && index < moves.length; index++) {
      const move = moves[index], step = index + 1;
      if (typeof move !== 'string' || !/^[A-D]{2}$/.test(move) || move[0] === move[1]) {
        baseError = {step, move, disk: null, kind: 'invalid_command'}; break;
      }
      const [source, target] = move;
      const disk = pegs[source].length ? pegs[source][pegs[source].length - 1] : null;
      let violates;
      switch (restriction.kind) {
        case 'peg': violates = source === restriction.source && target === restriction.destination; break;
        case 'occupancy': violates = positions.includes(restriction.occupied_peg) && source === restriction.source && target === restriction.destination; break;
        case 'source_sequence': violates = previousSource === restriction.previous_source && source === restriction.source; break;
        case 'visit': violates = disk === restriction.locked_disk && !visited; break;
        default: throw Error('지원하지 않는 이동 제한입니다.');
      }
      if (violates) violations.push({step, move, disk, kind: restriction.kind});
      if (disk === null) { baseError = {step, move, disk, kind: 'empty_source'}; break; }
      if (pegs[target].length && disk > pegs[target][pegs[target].length - 1]) {
        baseError = {step, move, disk, kind: 'larger_on_smaller'}; break;
      }
      pegs[target].push(pegs[source].pop());
      positions[disk - 1] = target;
      previousSource = source;
      if (restriction.kind === 'visit' && disk === restriction.visitor_disk && target === restriction.visited_peg) visited = true;
    }
    const baseValid = baseError === null;
    const goalReached = baseValid ? equal(pegs[round.goal_peg], [3, 2, 1]) : null;
    return {current_success: Boolean(baseValid && !violations.length && goalReached), base_valid: baseValid,
      base_error: baseError, movement_rule_violations: violations, movement_rule_error: Boolean(violations.length),
      goal_reached: goalReached, goal_error: baseValid ? !goalReached : null};
  }
  function grade(input, roundNumber, problem) {
    const moves = validMoves(input) ? clone(input) : null;
    const currentRound = problem.rounds[roundNumber - 1], current = evaluate(moves, currentRound);
    const previous = roundNumber > 1 ? evaluate(moves, {...problem.rounds[roundNumber - 2],
      initial_positions: currentRound.initial_positions, goal_peg: currentRound.goal_peg}) : null;
    // Previous-rule compliance uses the current initial board, independently of either goal.
    const previousRuleCompliant = previous === null ? null : previous.base_valid && !previous.movement_rule_error;
    let feedback = 'Incorrect', code = 'incorrect';
    if (current.current_success) { feedback = 'Correct'; code = 'correct'; }
    else if (current.movement_rule_error) {
      if (current.base_valid && current.goal_reached) { feedback = 'Correct, but rule violation'; code = 'goal_reached_rule_violation'; }
      else { feedback = 'Rule violation'; code = 'rule_violation'; }
    }
    return {moves, parse_valid: moves !== null, ...current,
      previous_success: previous === null ? null : previous.current_success,
      previous_rule_compliant: previousRuleCompliant,
      persistent_error: roundNumber > 1 ? previousRuleCompliant && !current.current_success : null,
      persistent_error_policy: PERSISTENT_ERROR_POLICY,
      feedback, correctness_feedback: current.current_success ? 'Correct' : 'Incorrect',
      feedback_code: code, feedback_policy: 'goal_rule_4way_v1'};
  }
  function stats(rows) {
    const failures = rows.filter(x => !x.current_success).length;
    const pe = rows.filter(x => x.persistent_error).length;
    return {attempts: rows.length, successes: rows.length - failures, failures,
      persistent_errors: pe, general_errors: failures - pe,
      persistent_error_rate: rows.length ? pe / rows.length : null,
      failure_rate: rows.length ? failures / rows.length : null,
      movement_rule_errors: rows.filter(x => x.movement_rule_error === true).length,
      goal_errors: rows.filter(x => x.goal_error === true).length,
      goal_evaluated_attempts: rows.filter(x => x.goal_error !== null).length,
      base_errors: rows.filter(x => x.parse_valid === true && x.base_valid === false).length};
  }
  function createEngine(inputBundle, options = {}) {
    const bundle = clone(inputBundle), {dataset, config, instructions} = bundle;
    if (!dataset || !Array.isArray(dataset.problems) || !dataset.problems.length ||
        !config || !Number.isInteger(config.max_attempts) || config.max_attempts < 1 ||
        !Number.isInteger(config.success_streak) || config.success_streak < 1 || config.success_streak > config.max_attempts ||
        typeof config.r1_apply_k !== 'boolean' || !Array.isArray(instructions) ||
        typeof bundle.bundle_id !== 'string' || typeof bundle.dataset_sha256 !== 'string') throw Error('실험 설정이 올바르지 않습니다.');
    const problems = dataset.problems;
    const clock = options.now || (() => new Date().toISOString());
    const newToken = options.token || token;
    const policies = [1, 2, 3, 4, 5].map(round => ({round, max_attempts: config.max_attempts,
      success_streak_required: round > 1 || config.r1_apply_k ? config.success_streak : 1}));
    const protocol = {version: 'hanoi_human_offline_v2', bundle_id: bundle.bundle_id,
      config: clone(config), feedback_policy: 'goal_rule_4way_v1', dataset: clone(dataset),
      dataset_sha256: bundle.dataset_sha256, participant_instructions: clone(instructions),
      input_policy: 'Peg-click full plan; basic-mechanics invalid clicks are blocked. Hidden restrictions are evaluated only after submission.',
      empty_plan_policy: '1..30 moves required; round trips allowed.',
      persistent_error_policy: PERSISTENT_ERROR_POLICY};
    function create(participant) {
      if (typeof participant !== 'string' || !/^[A-Za-z0-9_-]{1,40}$/.test(participant)) throw Error('참여 코드는 영문, 숫자, 밑줄, 하이픈으로 1~40자 입력하세요.');
      const timestamp = clock();
      return {session_id: newToken(), participant_id: participant, created_at: timestamp, updated_at: timestamp,
        turn_started_at: timestamp, episode: 1, round: 1, attempt: 1, streak: 0, turn_token: newToken(),
        finished: false, awaiting_continue: false, records: [], receipts: {}};
    }
    function publicState(state) {
      const last = state.records[state.records.length - 1], waiting = state.awaiting_continue;
      const complete = state.finished && !waiting;
      const episode = waiting ? last.episode : Math.min(state.episode, problems.length);
      const positions = waiting ? last.initial_positions : complete ? null : problems[episode - 1].rounds[state.round - 1].initial_positions;
      return {session_id: state.session_id, participant_id: state.participant_id, problem_number: episode,
        problem_count: problems.length, initial_positions: positions ? clone(positions) : null,
        instructions: clone(instructions), history: state.records.filter(r => r.episode === episode).map((r, i) => ({
          number: i + 1, initial_positions: clone(r.initial_positions), moves: clone(r.moves), feedback: FEEDBACK[r.feedback]})),
        awaiting_continue: waiting, complete, turn_token: state.turn_token,
        feedback: waiting ? FEEDBACK[last.feedback] : null, updated_at: state.updated_at};
    }
    function submit(before, turnToken, moves, replayAt) {
      if (Object.prototype.hasOwnProperty.call(before.receipts, turnToken)) {
        if (!equal(before.receipts[turnToken], moves)) throw Error('이미 저장된 제출과 내용이 다릅니다.');
        return clone(before);
      }
      if (before.finished || before.awaiting_continue || turnToken !== before.turn_token) throw Error('진행 상태가 변경되었습니다. 저장된 진행을 다시 불러오세요.');
      if (!validMoves(moves)) throw Error('1~30회 이동으로 전체 계획을 작성하세요.');
      const state = clone(before), problem = problems[state.episode - 1], round = problem.rounds[state.round - 1];
      const result = grade(moves, state.round, problem);
      if (!result.base_valid) throw Error('기본 이동법에 맞지 않는 계획입니다. 계획을 다시 확인하세요.');
      const policy = policies[state.round - 1], timestamp = replayAt || clock();
      const elapsed = Math.max(0, (Date.parse(timestamp) - Date.parse(state.turn_started_at)) / 1000);
      state.streak = result.current_success ? state.streak + 1 : 0;
      const passed = state.streak >= policy.success_streak_required;
      state.records.push({...result, call: state.records.length + 1, episode: state.episode,
        problem_id: problem.id, condition: problem.rule_type, round: state.round, attempt: state.attempt,
        initial_positions: clone(round.initial_positions), initial_family: round.initial_family,
        rule_snapshot: {name: round.name, goal_peg: round.goal_peg, candidate_rule: clone(round.rule),
          initial_positions: clone(round.initial_positions), initial_family: round.initial_family},
        raw_output: JSON.stringify({moves}), success_streak: state.streak,
        success_streak_required: policy.success_streak_required, round_success: passed,
        elapsed_seconds: elapsed, submitted_at: timestamp, turn_started_at: state.turn_started_at,
        turn_token: turnToken, refusal: false, usage: null});
      const exhausted = state.attempt >= policy.max_attempts && !passed;
      if (exhausted || (passed && state.round === 5)) Object.assign(state, {episode: state.episode + 1, round: 1, attempt: 1, streak: 0});
      else if (passed) Object.assign(state, {round: state.round + 1, attempt: 1, streak: 0});
      else state.attempt++;
      state.finished = state.episode > problems.length;
      state.receipts[turnToken] = clone(moves);
      state.awaiting_continue = true;
      state.updated_at = timestamp;
      return state;
    }
    function advance(before, turnToken) {
      const state = clone(before);
      if (turnToken !== state.turn_token || !state.awaiting_continue) return state;
      const timestamp = clock();
      Object.assign(state, {awaiting_continue: false, turn_token: newToken(), turn_started_at: timestamp, updated_at: timestamp});
      return state;
    }
    function summarize(records) {
      // Summaries require records with this engine's metric policy and diagnostics.
      if (records.some(row => row.persistent_error_policy !== PERSISTENT_ERROR_POLICY ||
          (row.round === 1 ? row.previous_rule_compliant !== null || row.persistent_error !== null :
            typeof row.previous_rule_compliant !== 'boolean' ||
            row.persistent_error !== (row.previous_rule_compliant && !row.current_success)))) {
        throw Error('지속적 오류 계산 기준이 다른 기록은 함께 집계할 수 없습니다. 원본 기록으로 별도 재분석하세요.');
      }
      const episodes = problems.map((problem, index) => {
        const episode = index + 1, batch = records.filter(x => x.episode === episode);
        let exhaustedAt = null;
        const rounds = policies.map(policy => {
          const number = policy.round, rows = batch.filter(x => x.round === number);
          const entry = {round: number, ...stats(rows), first_success_attempt: (rows.find(x => x.current_success) || {}).attempt ?? null};
          let streak = 0, peak = 0, metAt = null, repeats = 0;
          const failed = new Set();
          for (const row of rows) {
            streak = row.current_success ? streak + 1 : 0;
            peak = Math.max(peak, streak);
            if (streak >= policy.success_streak_required && metAt === null) metAt = row.attempt;
            if (!row.current_success) { const key = canonical(row.moves); if (failed.has(key)) repeats++; failed.add(key); }
          }
          const validSequence = rows.every((x, i) => x.attempt === i + 1) && rows.length <= policy.max_attempts && (metAt === null || metAt === rows.length);
          const complete = Boolean(validSequence && rows.length && (metAt !== null || rows.length === policy.max_attempts));
          Object.assign(entry, {success_streak: streak, max_success_streak: peak, criterion_met_at: metAt,
            success_streak_required: policy.success_streak_required, complete, max_attempts: policy.max_attempts,
            status: complete ? metAt !== null ? 'success' : 'exhausted' : rows.length ? 'in_progress' : 'pending',
            repeat_of_identical_failed_plan: repeats});
          if (number === 1) { entry.persistent_errors = null; entry.persistent_error_rate = null; }
          if (exhaustedAt !== null && !rows.length) Object.assign(entry, {status: 'excluded', excluded_reason: 'prior_round_exhausted', excluded_after_round: exhaustedAt});
          if (entry.status === 'exhausted' && exhaustedAt === null) exhaustedAt = number;
          return entry;
        });
        const scoredRounds = rounds.filter(r => r.round > 1 && r.attempts), errorRounds = rounds.filter(r => r.attempts);
        return {episode, problem_id: problem.id, condition: problem.rule_type,
          complete: rounds.every(r => r.complete || r.status === 'excluded'), exhausted_at_round: exhaustedAt,
          rounds, scored: stats(batch.filter(r => r.round > 1)),
          per_by_round: Object.fromEntries(rounds.filter(r => r.round > 1).map(r => [r.round, r.persistent_error_rate])),
          error_rate_by_round: Object.fromEntries(rounds.map(r => [r.round, r.failure_rate])),
          eligible_scored_rounds: scoredRounds.length, mpe: mean(scoredRounds.map(r => r.persistent_error_rate)),
          mean_error_rate: mean(errorRounds.map(r => r.failure_rate)), eligible_error_rounds: errorRounds.length,
          error_metric_eligible: Boolean(errorRounds.length), metric_eligible: Boolean(scoredRounds.length)};
      });
      const scored = records.filter(r => r.round > 1), mpes = episodes.filter(e => e.metric_eligible).map(e => e.mpe);
      const errors = episodes.filter(e => e.error_metric_eligible).map(e => e.mean_error_rate);
      const conditions = {};
      for (const kind of Object.keys(LABELS)) {
        const members = episodes.filter(e => e.condition === kind);
        if (!members.length) continue;
        const eligible = members.filter(e => e.metric_eligible), errorEligible = members.filter(e => e.error_metric_eligible);
        const completed = members.filter(e => e.complete);
        conditions[kind] = {label: LABELS[kind], problem_count: members.length, completed_problems: completed.length,
          ...stats(scored.filter(r => r.condition === kind)), metric_eligible_problems: eligible.length,
          error_metric_eligible_problems: errorEligible.length, mpe: mean(eligible.map(e => e.mpe)),
          mean_error_rate: mean(errorEligible.map(e => e.mean_error_rate)),
          complete_problem_rate_mean: mean(completed.map(e => e.scored.persistent_error_rate).filter(x => x !== null))};
      }
      const rates = episodes.filter(e => e.complete).map(e => e.scored.persistent_error_rate).filter(x => x !== null);
      let minimum = 0;
      for (const policy of policies.slice().reverse()) minimum = Math.min(policy.max_attempts, policy.success_streak_required + minimum);
      return {source: 'Human', complete: episodes.every(e => e.complete), early_stopping: true,
        success_streak_required: config.success_streak, baseline_until_success: false,
        planned_submissions: config.max_attempts * 5 * problems.length, min_submissions: minimum * problems.length,
        max_submissions: config.max_attempts * 5 * problems.length, scored_max_submissions: config.max_attempts * 4 * problems.length,
        baseline_submissions: records.filter(r => r.round === 1).length, recorded_submissions: records.length,
        completed_rounds: episodes.reduce((sum, e) => sum + e.rounds.filter(r => r.complete).length, 0), total_rounds: 5 * problems.length,
        successful_rounds: episodes.reduce((sum, e) => sum + e.rounds.filter(r => r.status === 'success').length, 0),
        exhausted_rounds: episodes.reduce((sum, e) => sum + e.rounds.filter(r => r.status === 'exhausted').length, 0),
        scored_rounds: [2, 3, 4, 5], pooled: stats(scored),
        persistent_error_policy: PERSISTENT_ERROR_POLICY,
        metric_policy: 'round_per_then_problem_mpe_then_total_mpe_v1',
        error_metric_policy: 'round_error_rate_r1_r5_then_problem_mean_then_total_mean_v2',
        metric_definition: {persistent_error: 'current failure with basic-valid replay from the current initial board and no previous-round movement restriction violation; neither goal is required for previous-rule compliance',
          round_per: 'persistent_errors / actual graded submissions in that round',
          problem_mpe: 'unweighted mean of observed R2-R5 PERs', total_mpe: 'unweighted mean of eligible problem MPEs',
          round_error_rate: 'incorrect submissions / actual graded submissions',
          problem_mean_error_rate: 'unweighted mean of observed R1-R5 error rates',
          total_mean_error_rate: 'unweighted mean of observed problem mean error rates',
          r1: 'excluded from PER/MPE; included in error rate', missing: 'unattempted rounds excluded, never zero-filled', scale: 'fraction in JSON'},
        total_mpe: mean(mpes), total_mean_error_rate: mean(errors), metric_eligible_problems: mpes.length,
        error_metric_eligible_problems: errors.length, problem_mpes: mpes, problem_mean_error_rates: errors,
        problems_in_mpe_target_band: mpes.filter(x => x >= 0.3 && x <= 0.5).length,
        excluding_first_attempt: stats(scored.filter(r => r.attempt > 1)), episodes, conditions,
        complete_episode_rate_mean: mean(rates), complete_episode_rates: rates,
        episodes_in_target_band: rates.filter(x => x >= 0.3 && x <= 0.5).length,
        completed_episodes: episodes.filter(e => e.complete).length, metric_eligible_completed_episodes: rates.length,
        excluded_rounds: episodes.reduce((sum, e) => sum + e.rounds.filter(r => r.status === 'excluded').length, 0),
        exhausted_problems: episodes.filter(e => e.exhausted_at_round !== null).length, stop_problem_on_exhaustion: true,
        format_failures: records.filter(r => !r.parse_valid).length, refusals: records.filter(r => r.refusal).length,
        usage: {input_tokens: 0, output_tokens: 0, total_tokens: 0},
        timing_policy: 'Browser wall time from opening a plan to submission, including idle time; excludes feedback-screen time.',
        total_elapsed_seconds: records.reduce((sum, r) => sum + r.elapsed_seconds, 0)};
    }
    function exportResult(state) {
      const checkpoint = clone(state);
      delete checkpoint.records; delete checkpoint.receipts;
      return {schema_version: 3, kind: 'hanoi_human_result', bundle_id: bundle.bundle_id,
        session_id: state.session_id, participant_id: state.participant_id,
        created_at: state.created_at, updated_at: state.updated_at, finished: state.finished,
        protocol: clone(protocol), records: clone(state.records),
        summary: {...summarize(state.records), execution_finished: state.finished}, checkpoint};
    }
    function importResult(document) {
      const fail = () => { throw Error('저장 파일의 실험 조건이나 진행 기록이 일치하지 않습니다. 이 HTML에서 저장한 원본 JSON을 선택하세요.'); };
      if (document && document.kind === 'hanoi_human_result' && document.protocol &&
          document.protocol.persistent_error_policy !== PERSISTENT_ERROR_POLICY) {
        throw Error('저장 파일의 지속적 오류 계산 기준이 이 평가와 일치하지 않습니다. 이 HTML에서 저장한 JSON을 선택하세요.');
      }
      if (!document || document.schema_version !== 3 || document.kind !== 'hanoi_human_result' ||
          document.bundle_id !== bundle.bundle_id || !equal(document.protocol, protocol) ||
          !validToken(document.session_id) || typeof document.participant_id !== 'string' || !/^[A-Za-z0-9_-]{1,40}$/.test(document.participant_id) ||
          !validDate(document.created_at) || !validDate(document.updated_at) || !Array.isArray(document.records) ||
          document.records.length > problems.length * 5 * config.max_attempts) fail();
      const checkpoint = document.checkpoint;
      if (!checkpoint || !validToken(checkpoint.turn_token) || !validDate(checkpoint.turn_started_at) ||
          typeof checkpoint.awaiting_continue !== 'boolean') fail();
      let state = {session_id: document.session_id, participant_id: document.participant_id,
        created_at: document.created_at, updated_at: document.created_at, turn_started_at: document.created_at,
        episode: 1, round: 1, attempt: 1, streak: 0, turn_token: checkpoint.turn_token,
        finished: false, awaiting_continue: false, records: [], receipts: {}};
      let previousSubmitted = document.created_at;
      for (let index = 0; index < document.records.length; index++) {
        const record = document.records[index];
        if (!record || !validToken(record.turn_token) || Object.prototype.hasOwnProperty.call(state.receipts, record.turn_token) ||
            !validDate(record.submitted_at) || !validDate(record.turn_started_at) ||
            Date.parse(record.turn_started_at) < Date.parse(previousSubmitted) || Date.parse(record.submitted_at) < Date.parse(record.turn_started_at) ||
            (index === 0 && record.turn_started_at !== document.created_at)) fail();
        Object.assign(state, {awaiting_continue: false, turn_token: record.turn_token, turn_started_at: record.turn_started_at});
        try { state = submit(state, record.turn_token, record.moves, record.submitted_at); } catch { fail(); }
        if (!equal(state.records[state.records.length - 1], record)) fail();
        previousSubmitted = record.submitted_at;
      }
      if (checkpoint.awaiting_continue) {
        if (!state.records.length || checkpoint.turn_token !== state.turn_token || checkpoint.turn_started_at !== state.turn_started_at || document.updated_at !== state.updated_at) fail();
      } else {
        if (Date.parse(checkpoint.turn_started_at) < Date.parse(previousSubmitted) || document.updated_at !== checkpoint.turn_started_at ||
            (state.records.length && Object.prototype.hasOwnProperty.call(state.receipts, checkpoint.turn_token)) ||
            (!state.records.length && checkpoint.turn_started_at !== document.created_at)) fail();
        Object.assign(state, {awaiting_continue: false, turn_token: checkpoint.turn_token,
          turn_started_at: checkpoint.turn_started_at, updated_at: checkpoint.turn_started_at});
      }
      const rebuilt = exportResult(state);
      for (const key of ['session_id', 'participant_id', 'created_at', 'updated_at', 'finished', 'records', 'summary', 'checkpoint']) {
        if (!equal(rebuilt[key], document[key])) fail();
      }
      return state;
    }
    return {create, public: publicState, submit, advance, export: exportResult, import: importResult,
      summarize, grade, evaluate, config: clone(config)};
  }
  return {createEngine, grade, evaluate};
});
