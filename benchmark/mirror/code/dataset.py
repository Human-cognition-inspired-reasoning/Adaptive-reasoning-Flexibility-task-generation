"""Generate and verify the released 100-problem dataset without API calls.

Usage:
    python code/dataset.py generate
    python code/dataset.py verify

The fixed seed and candidate order reproduce the existing boards exactly.
Generated solutions are checked with the same simulator used for experiments.
Existing different files are never overwritten. T and k are run settings;
the dataset retains its original default-setting metadata.
"""
from __future__ import annotations

import argparse
import collections
from concurrent.futures import ProcessPoolExecutor
import hashlib
import itertools
import json
import multiprocessing
from pathlib import Path
import random
import re
import shutil
import tempfile
import time

from judge import (UNDEFINED, js_dumps, js_truthy, simulate, evaluate, allRegions,
                   regionId, boardForRound, DELTA, DIR, REFLECT,
                   _strict, _same, _integer, _get, _string, _includes)

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / 'dataset'
DIRECTIONS = tuple(DIR)
regions = allRegions
region_id = regionId

# Board geometry and rule checks.

def _keys(value):
    if value is None or value is UNDEFINED:
        raise TypeError('Cannot convert undefined or null to object')
    if isinstance(value, dict):
        return list(value)
    if isinstance(value, (list, str)):
        return [str(i) for i in range(len(value))]
    return []


def proveSuccessExclusion(board, old, current):
    ruleKind(old)
    ruleKind(current)
    ids, colors = [s['id'] for s in board['sensors']], ['red', 'blue', 'white']
    for r in [old, current]:
        target = _get(r, 'target_colors')
        targetKeys = _keys({} if target is None or target is UNDEFINED else target)
        if len(targetKeys) != len(ids) or not all(_includes(colors, _get(target, s)) for s in ids):
            return None
    if any(not _strict(_get(old['target_colors'], s), _get(current['target_colors'], s)) for s in ids):
        return 'distinct_targets_under_common_passing_physics'
    return None


def ruleKind(r):
    constraints = _get(r, 'constraints')
    if not _strict(_get(constraints, 'length', optional=True), 1):
        raise ValueError('Exactly one path rule is required')
    q = _get(constraints, 0)
    typ, value = _get(q, 'type'), _get(q, 'value')
    if _strict(typ, 'filter_axis') and _includes(['horizontal', 'vertical'], value) and len(_keys(q)) == 2:
        return 'H' if value == 'horizontal' else 'V'
    if (_strict(typ, 'region') and _includes(['pass', 'avoid'], value)
            and re.fullmatch('[A-Y]', _string(_get(q, 'id'))) is not None and len(_keys(q)) == 3):
        return 'P' if value == 'pass' else 'A'
    if _strict(typ, 'different_color_overlap') and _strict(value, 'forbidden') and len(_keys(q)) == 2:
        return 'N'
    raise ValueError('Unknown path rule')


def geometryKey(b):
    pts = [b['source']['position'], *[f['position'] for f in b['filters']], *[s['position'] for s in b['sensors']]]
    vector = {'up': [-1, 0], 'right': [0, 1], 'down': [1, 0], 'left': [0, -1]}[b['source']['direction']]
    variants = []
    for flip in [False, True]:
        for turns in range(4):
            def transform(position):
                r, c = position
                if flip:
                    c = -c
                for _ in range(turns):
                    r, c = c, -r
                return [r, c]

            pp = [transform(p) for p in pts]
            mr, mc = min(p[0] for p in pp), min(p[1] for p in pp)
            norm = [[r-mr, c-mc] for r, c in pp]
            variants.append(js_dumps([norm[0], transform(vector), sorted(norm[1:3], key=_string), sorted(norm[3:], key=_string)]))
    return sorted(variants)[0]


def _distance(a, b):
    return sum(abs(v-b[i]) for i, v in enumerate(a))


def _replay(layout, board, round):
    try:
        optics = simulate(board, layout, round)
        evaluation = evaluate(round, optics)
        return {'valid': True, 'rule_satisfied': evaluation['rule_satisfied'],
                'success': evaluation['success'], 'received_colors': optics['received_colors']}
    except Exception:
        return {'valid': False, 'rule_satisfied': None, 'success': False, 'received_colors': None}


def validateDataset(dataset, witnesses, certificates):
    def check(ok, message):
        if not js_truthy(ok):
            raise ValueError('Dataset verification: '+message)

    check(_strict(_get(dataset, 'dataset_id'), 'mirror-light-all-moving-v3-flexible-layout-100x5') and len(dataset['problems']) == 100,
          '100 problems / flexible-layout v3 schema')
    check(_strict(_get(witnesses, 'dataset_id'), dataset['dataset_id']) and len(witnesses['problems']) == 100, 'witness identity')
    check(_strict(_get(certificates, 'dataset_id'), dataset['dataset_id']) and len(certificates['problems']) == 100
          and _strict(_get(certificates, 'transitions_assessed'), 400)
          and _get(certificates, 'full_exclusivity_required') is False
          and _get(certificates, 'cross_board_universal_success_exclusion_claimed') is False, 'proof scope')
    seen, sources = set(), set()
    first = {'H': 0, 'V': 0, 'P': 0, 'A': 0, 'N': 0}
    regionCounts = {'empty_independent_regions': 0}
    verified = transitions = proven = invalidReplays = maxStep = 0
    changeCounts = {'0': 0, '1': 0, '2': 0}
    directions = {'up': 0, 'right': 0, 'down': 0, 'left': 0}
    colorCounts = {'S1': {'red': 0, 'blue': 0, 'white': 0}, 'S2': {'red': 0, 'blue': 0, 'white': 0}}
    repeatedDirections = axisRounds = axisWitnessesWithFilterVisit = maxSpaceRounds = 0
    nonadjacentAxes = nonadjacentRegions = differentRegions = emptyRegions = outsideCentralFilters = adjacentObjects = closeSensors = 0
    regionIds, orders = set(), set()
    for pi, p in enumerate(dataset['problems']):
        tag, w = f'P{pi+1}', witnesses['problems'][pi]
        check(_strict(_get(p, 'problem_number'), pi+1) and _strict(_get(p, 'problem_id'), f'all_moving_{pi+1:03d}')
              and len(p['rounds']) == 5, tag+' numbering')
        check('board' not in p and _strict(_get(w, 'problem_id'), p['problem_id']) and len(w['rounds']) == 5, tag+' per-round boards')
        kinds = [ruleKind(r) for r in p['rounds']]
        check(_same(sorted(kinds), ['A', 'H', 'N', 'P', 'V']), tag+' five rule kinds')
        first[kinds[0]] += 1
        orders.add(''.join(kinds))
        if abs(kinds.index('H')-kinds.index('V')) > 1:
            nonadjacentAxes += 1
        if abs(kinds.index('P')-kinds.index('A')) > 1:
            nonadjacentRegions += 1
        check('designated_region' not in p and _same(_get(p, 'designated_regions'), {
            'pass_region': p['rounds'][kinds.index('P')]['constraints'][0]['id'],
            'avoid_region': p['rounds'][kinds.index('A')]['constraints'][0]['id']}), tag+' separate pass/avoid region metadata')
        check(_strict(_get(p['region_design'], 'pass_round'), kinds.index('P')+1)
              and _strict(_get(p['region_design'], 'avoid_round'), kinds.index('A')+1), tag+' region round metadata')
        if not _strict(p['designated_regions']['pass_region'], p['designated_regions']['avoid_region']):
            differentRegions += 1
        check(_get(p['region_design'], 'kind') in regionCounts, tag+' region design')
        regionCounts[p['region_design']['kind']] += 1
        totalMirrors, uniqueEntities, usedTargets = 0, {}, set()
        for ri, r in enumerate(p['rounds']):
            b, wr, where = boardForRound(p, ri), w['rounds'][ri], tag+f' R{ri+1}'
            check(_strict(_get(r, 'round_index'), ri+1) and _strict(_get(b, 'grid_size'), 10)
                  and _strict(_get(b['source'], 'color'), 'white'), where+' board')
            check(_same(_get(b, 'regions'), allRegions()), where+' regions')
            check(_same([[f['id'], f['color']] for f in b['filters']], [['F1', 'red'], ['F2', 'blue']])
                  and _same([s['id'] for s in b['sensors']], ['S1', 'S2']), where+' objects')
            entities = [['source', b['source']], *[[f['id'], f] for f in b['filters']], *[[s['id'], s] for s in b['sensors']]]
            check(all(isinstance(_get(e, 'position'), list) and len(e['position']) == 2
                      and all(_integer(v) and 1 <= v <= 10 for v in e['position']) for _, e in entities), where+' coordinates')
            join = lambda pos: ','.join(_string(v) for v in pos)
            check(len({join(e['position']) for _, e in entities}) == 5, where+' occupied cells')
            for ident, e in entities:
                uniqueEntities.setdefault(ident, set())
                check(join(e['position']) not in uniqueEntities[ident], where+' each object moves')
                uniqueEntities[ident].add(join(e['position']))
            delta = {'up': [-1, 0], 'right': [0, 1], 'down': [1, 0], 'left': [0, -1]}.get(b['source']['direction'])
            check(delta is not None and all(1 <= v+delta[i] <= 10 for i, v in enumerate(b['source']['position'])), where+' source points inside')
            sr, sc = b['source']['position']
            room = {'up': sr-1, 'right': 10-sc, 'down': 10-sr, 'left': sc-1}
            check(_strict(room[b['source']['direction']], max(room.values())), where+' source faces longest available grid distance')
            maxSpaceRounds += 1
            if kinds[ri] in ['H', 'V']:
                check(any(c == 'red' or c == 'blue' for c in r['target_colors'].values()), where+' filter axis requires a nonwhite target')
                axisRounds += 1
            targetKey = js_dumps([_get(r['target_colors'], 'S1'), _get(r['target_colors'], 'S2')])
            check(targetKey not in usedTargets, where+' target pairs must be unique within a problem')
            usedTargets.add(targetKey)
            directions[b['source']['direction']] += 1
            q = r['constraints'][0]
            if _strict(_get(q, 'type'), 'region'):
                check(all(not _strict(regionId(*e['position']), q['id']) for _, e in entities), where+' designated region must contain no source, filter or sensor')
                emptyRegions += 1
                regionIds.add(q['id'])
            outsideCentralFilters += sum(any(v < 4 or v > 7 for v in f['position']) for f in b['filters'])
            closeSensors += sum(_distance(b['source']['position'], s['position']) < 7 for s in b['sensors'])
            if any(any(_distance(a['position'], other['position']) == 1 for _, other in entities[i+1:]) for i, (_, a) in enumerate(entities)):
                adjacentObjects += 1
            geometry = geometryKey(b)
            check(geometry not in seen, where+' duplicate geometry including rotations/reflections/translations')
            seen.add(geometry)
            sources.add(join(b['source']['position']))
            check(_same(sorted(r['target_colors']), ['S1', 'S2']) and all(c in ['red', 'blue', 'white'] for c in r['target_colors'].values()), where+' targets')
            for s in ['S1', 'S2']:
                colorCounts[s][r['target_colors'][s]] += 1
            if ri:
                old = p['rounds'][ri-1]
                ob = old['board']
                changed = [s for s in ['S1', 'S2'] if not _strict(old['target_colors'][s], r['target_colors'][s])]
                check(len(changed) >= 1, where+' adjacent target pairs must differ')
                check(_same(_get(r, 'changed_sensors'), changed) and 'changed_sensor' not in r, where+' target transition metadata')
                changeCounts[str(len(changed))] += 1
                if _strict(ob['source']['direction'], b['source']['direction']):
                    repeatedDirections += 1
                check(all(all(not _same(f['position'], of['position']) for of in ob['filters']) for f in b['filters'])
                      and all(all(not _same(s['position'], os['position']) for os in ob['sensors']) for s in b['sensors']), where+' both position sets move')
                maxStep = max(maxStep, _distance(ob['source']['position'], b['source']['position']))
                c = certificates['problems'][pi]['transitions'][ri-1]
                theorem = proveSuccessExclusion(b, old, r)
                check(_strict(_get(c, 'theorem'), theorem) and _strict(_get(c, 'scope'), 'both_rule_evaluations_on_current_round_board')
                      and _same(_get(c, 'reference_board'), b) and _get(c, 'device_count_limit') is None
                      and _same(_get(c, 'previous_targets'), old['target_colors']) and _same(_get(c, 'current_targets'), r['target_colors']), where+' same-current-board proof')
                check(_strict(_get(c, 'status'), 'proven_success_exclusive' if theorem else 'not_claimed'), where+' no false exclusivity claim for repeated targets')
                if theorem:
                    proven += 1
                transitions += 1
                oldLayout = w['rounds'][ri-1]['layout']
                a, prev = _replay(oldLayout, b, r), _replay(oldLayout, b, old)
                check(not a['success'] and _same(a, _get(wr, 'previous_layout_current'))
                      and _same(prev, _get(wr, 'previous_layout_previous_rule')), where+' witness replay')
                check(_get(c, 'previous_stored_witness_current_success') is False
                      and _strict(_get(c, 'previous_stored_witness_previous_rule_satisfied'), prev['rule_satisfied']), where+' replay certificate')
                if not a['valid']:
                    invalidReplays += 1
            else:
                check(_same(_get(r, 'changed_sensors'), []) and 'changed_sensor' not in r, where+' first target')
            optics = simulate(b, wr['layout'], r)
            evaluation = evaluate(r, optics)
            check(evaluation['success'] and _same(optics, _get(wr, 'optics')) and _same(evaluation, _get(wr, 'evaluation')), where+' stored solution trace and evaluation')
            if kinds[ri] in ['H', 'V']:
                check(len(optics['filter_arrivals']) > 0, where+' successful axis witness reaches a filter')
                axisWitnessesWithFilterVisit += 1
            mirrors = sum(x['type'] == 'mirror' for x in wr['layout']['placements'])
            splitters = sum(x['type'] == 'splitter' for x in wr['layout']['placements'])
            check(4 <= mirrors <= 5 and splitters == 1 and _strict(mirrors, _get(wr, 'mirror_count'))
                  and _strict(_get(wr, 'device_count'), mirrors+1), where+' witness complexity')
            totalMirrors += mirrors
            verified += 1
        check(20 <= totalMirrors <= 23, tag+' witness complexity')
    check(all(v == 20 for v in first.values()), 'first rule balance')
    st = dataset['settings']
    check(_same(regionCounts, _get(st, 'region_design_counts')) and regionCounts['empty_independent_regions'] == 100 and emptyRegions == 200, 'all pass/avoid regions empty in their active round')
    check(_same(_get(st, 'adjacent_rule_pairs'), []) and _strict(_get(st, 'rule_order_sampling'), 'random_permutation_with_balanced_first_rule') and _strict(_get(st, 'first_rule_counts_per_type'), 20), 'no paired rule adjacency requirement')
    check(_same(_get(st, 'filter_rows_and_cols'), list(range(1, 11))) and _get(st, 'sensor_min_manhattan_from_source') is None and _get(st, 'min_filter_sensor_separation') is None and _get(st, 'object_min_manhattan_distance') is None, 'full grid and no minimum object spacing')
    check(_same(_get(st, 'designated_region_ids'), [chr(65+i) for i in range(25)]) and _get(st, 'pass_avoid_regions_independently_sampled') is True and _get(st, 'designated_regions_must_be_empty') is True and _strict(_get(st, 'designated_region_empty_scope'), 'active_round'), 'independent empty regions across the full grid')
    check(_get(st, 'sensor_color_changes_per_transition') is None and _get(st, 'sensor_changes_alternate') is False and _strict(_get(st, 'target_color_sampling'), 'random_pairs_without_replacement_per_problem_reject_axis_white_white') and _get(st, 'target_pair_repeat_allowed') is False and _get(st, 'target_pairs_unique_within_problem') is True and _get(st, 'adjacent_target_pairs_must_differ') is True and _get(st, 'filter_axis_requires_nonwhite_target') is True, 'random colors without repeated pairs within a problem and meaningful filter-axis targets')
    check(_strict(_get(st, 'source_direction_sampling'), 'max_distance_to_grid_boundary_random_ties') and _get(st, 'source_direction_repeat_allowed') is True and _get(st, 'target_schedule_frozen_before_geometry_search') is True, 'inward source direction policy')
    check(_get(st, 'full_success_exclusion_required') is False and _strict(_get(certificates, 'transitions_proven'), proven) and _strict(_get(certificates, 'transitions_not_claimed'), 400-proven), 'exclusion audit counts')
    check(_integer(_get(st, 'max_attempts_per_round')) and _integer(_get(st, 'consecutive_correct_to_advance')) and 1 <= st['consecutive_correct_to_advance'] <= st['max_attempts_per_round'] and _get(st, 'device_count_limit') is None, 'valid default settings')
    check(js_truthy(_get(st, 'source_changes_every_round')) and js_truthy(_get(st, 'filters_change_every_round')) and js_truthy(_get(st, 'sensors_change_every_round')) and len(st['fixed_objects']) == 0, 'all positions change')
    check(_get(st, 'source_max_manhattan_distance_from_initial') is None and _get(st, 'source_max_step_manhattan_distance') is None and _get(st, 'initial_source_at_corner') is False and maxStep > 2, 'unrestricted movement')
    check(_strict(_get(st, 'per_reference_board'), 'current_round') and _get(st, 'per_previous_target_colors_used') is False, 'PER')
    return {'passed': True, 'problems': 100, 'rounds': 500, 'solution_witnesses_verified': verified,
            'unique_round_geometries': len(seen), 'unique_source_cells': len(sources), 'max_source_step': maxStep,
            'first_rule_counts': first, 'region_design_counts': regionCounts,
            'problems_with_nonadjacent_filter_axes': nonadjacentAxes, 'problems_with_nonadjacent_pass_avoid': nonadjacentRegions,
            'problems_with_different_pass_avoid_regions': differentRegions, 'empty_designated_region_rounds': emptyRegions,
            'distinct_designated_regions_used': len(regionIds), 'distinct_rule_orders': len(orders),
            'filters_outside_previous_central_area': outsideCentralFilters, 'rounds_with_adjacent_objects': adjacentObjects,
            'sensors_closer_than_previous_minimum': closeSensors, 'current_board_transitions_audited': transitions,
            'current_board_success_exclusion_verified': proven, 'exclusion_not_claimed_for_repeated_targets': 400-proven,
            'target_change_count_histogram': changeCounts, 'source_direction_counts': directions,
            'repeated_direction_transitions': repeatedDirections, 'target_color_counts': colorCounts,
            'invalid_previous_witness_replays': invalidReplays, 'filter_axis_rounds': axisRounds,
            'filter_axis_witnesses_with_filter_visit': axisWitnessesWithFilterVisit,
            'source_points_to_maximum_space_count': maxSpaceRounds, 'paid_api_calls': 0}


# Seeded candidate generation and bounded solution search.

def dump(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def geometry_key(board):
    """Ignore filter colors and sensor IDs when detecting geometrically equivalent boards."""
    pts = [board['source']['position']] + [x['position'] for x in board['filters'] + board['sensors']]
    vec = DELTA[DIRECTIONS.index(board['source']['direction'])]
    variants = []
    for flip in (False, True):
        for turns in range(4):

            def tr(p):
                r, c = p
                if flip:
                    c = -c
                for _ in range(turns):
                    r, c = (c, -r)
                return (r, c)
            pp = list(map(tr, pts))
            mr = min((p[0] for p in pp))
            mc = min((p[1] for p in pp))
            pp = [(r - mr, c - mc) for r, c in pp]
            variants.append((pp[0], tr(vec), tuple(sorted(pp[1:3])), tuple(sorted(pp[3:]))))
    return min(variants)


RULES = {'H': dict(type='filter_axis', value='horizontal'), 'V': dict(type='filter_axis', value='vertical'), 'N': dict(type='different_color_overlap', value='forbidden')}


def rule(k, z='C'):
    return RULES[k].copy() if k in RULES else dict(type='region', id=z, value='pass' if k == 'P' else 'avoid')


def rr(k, colors, z='C'):
    return dict(constraints=[rule(k, z)], target_colors=dict(zip(['S1', 'S2'], colors)))


def board(source, red, blue, s1, s2, direction='right'):
    return dict(grid_size=10, source=dict(position=source, direction=direction, color='white'), filters=[dict(id='F1', position=red, color='red'), dict(id='F2', position=blue, color='blue')], sensors=[dict(id='S1', position=s1), dict(id='S2', position=s2)], regions=regions())


def ray_paths(board, start, sid, desired, max_new, rng, fixed, protected, forbidden, axis=None, max_results=40, budget=35000):
    """Search bounded ray routes toward one target sensor."""
    filters = {tuple(x['position']): x['color'] for x in board['filters']}
    sensors = {tuple(x['position']): x['id'] for x in board['sensors']}
    reserved = {tuple(board['source']['position']), *filters, *sensors}
    goal = next((p for p, s in sensors.items() if s == sid))
    waypoint = next((p for p, c in filters.items() if c == desired), goal)
    allowed = range(4) if axis is None else (0, 2) if axis == 'vertical' else (1, 3)
    results = []
    nodes = 0

    def dfs(r, c, d, color, seen, route, added):
        nonlocal nodes
        nodes += 1
        if nodes > budget or len(results) >= max_results:
            return
        if not (1 <= r <= board['grid_size'] and 1 <= c <= board['grid_size']) or (r, c) in seen or (r, c) in forbidden:
            return
        if (r, c) in sensors:
            if sensors[r, c] == sid and color == desired:
                results.append((added, route + [(r, c, d, color, None, None)]))
            return
        if (r, c) in filters:
            if d not in allowed:
                return
            if color != 'white' and color != filters[r, c]:
                return
            color = filters[r, c]
        if color != 'white' and color != desired:
            return
        if (r, c) in fixed:
            actions = [(REFLECT[fixed[r, c]][d], None)]
        elif (r, c) in reserved or (r, c) in protected or len(added) >= max_new:
            actions = [(d, None)]
        else:
            actions = [(d, None), (REFLECT['R'][d], 'R'), (REFLECT['L'][d], 'L')]
        aim = waypoint if color == 'white' and desired != 'white' else goal
        rng.shuffle(actions)
        actions.sort(key=lambda x: abs(r + DELTA[x[0]][0] - aim[0]) + abs(c + DELTA[x[0]][1] - aim[1]) + (1 if x[1] else 0))
        for out, o in actions:
            dr, dc = DELTA[out]
            dfs(r + dr, c + dc, out, color, seen | {(r, c)}, route + [(r, c, d, color, out, o)], added | {(r, c): o} if o else added)
    dfs(*start, set(), [], {})
    return results


def solve(board, rr, rng, max_mirrors=6, accept=None, protected_cells=()):
    """Join sensor routes through one splitter and verify the complete layout."""
    reserved = {tuple(board['source']['position']), *(tuple(x['position']) for x in board['filters'] + board['sensors'])}
    reserved.update(protected_cells)
    axis = next((r['value'] for r in rr['constraints'] if r['type'] == 'filter_axis'), None)
    avoid = next((r['id'] for r in rr['constraints'] if r['type'] == 'region' and r['value'] == 'avoid'), None)
    blocked = {(r, c) for r in range(1, board['grid_size'] + 1) for c in range(1, board['grid_size'] + 1) if region_id(r, c) == avoid}
    target = rr['target_colors']
    src = board['source']['position']
    d = DIRECTIONS.index(board['source']['direction'])
    dr, dc = DELTA[d]
    start = (src[0] + dr, src[1] + dc, d, 'white')
    candidates = []
    for sid in ('S1', 'S2'):
        for max_main in (2, 3, 4):
            paths = ray_paths(board, start, sid, target[sid], max_main, rng, {}, set(protected_cells), blocked, axis=axis, max_results=55, budget=25000)
            candidates.extend(((sid, m, p) for m, p in paths))
            if len(paths) >= 20:
                break
    rng.shuffle(candidates)
    candidates.sort(key=lambda x: len(x[1]))
    seen = set()
    for sid, fixed, path in candidates:
        other = 'S2' if sid == 'S1' else 'S1'
        protected = {(r, c) for r, c, *_ in path} | set(protected_cells)
        forks = []
        for r, c, d, color, out, old_o in path:
            if (r, c) in reserved or (color != 'white' and color != target[other]):
                continue
            for o in ('R', 'L'):
                ref = REFLECT[o][d]
                if out == d:
                    forks.append((r, c, o, ref, color))
                elif out == ref:
                    forks.append((r, c, o, d, color))
        rng.shuffle(forks)
        for r, c, o, d, color in forks:
            remain = {p: o2 for p, o2 in fixed.items() if p != (r, c)}
            if len(remain) > max_mirrors:
                continue
            dr, dc = DELTA[d]
            paths = ray_paths(board, (r + dr, c + dc, d, color), other, target[other], min(4, max_mirrors - len(remain)), rng, remain, protected, blocked | {(r, c)}, axis=axis, max_results=60, budget=20000)
            for added, _ in paths:
                mirrors = remain | added
                key = (r, c, o, tuple(sorted(mirrors.items())))
                if key in seen:
                    continue
                seen.add(key)
                pp = [dict(type='mirror', row=a, col=b, direction=orient) for (a, b), orient in sorted(mirrors.items())]
                pp.append(dict(type='splitter', row=r, col=c, direction=o))
                layout = {'placements': pp}
                op = simulate(board, layout, rr)
                ev = evaluate(rr, op)
                if ev['success'] and (accept is None or accept(layout)):
                    return dict(layout=layout, mirror_count=len(mirrors), splitter_count=1, device_count=len(pp), optics=op, evaluation=ev)
    return None


kind = ruleKind

def success_certificate(board, old, new):
    """Certify incompatible target colors under common passing physics on one board."""
    kind(old)
    kind(new)
    ids = [s['id'] for s in board['sensors']]
    assert all((set(r['target_colors']) == set(ids) and all((c in ('red', 'blue', 'white') for c in r['target_colors'].values())) for r in (old, new)))
    changed = [s for s in ids if old['target_colors'][s] != new['target_colors'][s]]
    if not changed:
        return dict(status='not_claimed', criterion='full_success_exclusion', theorem=None, device_count_limit=None, previous_targets=old['target_colors'], current_targets=new['target_colors'], changed_sensors=[], reason='Identical independently sampled targets are allowed; this audit does not claim universal exclusion for this transition.')
    return dict(status='proven_success_exclusive', criterion='full_success_exclusion', theorem='distinct_targets_under_common_passing_physics', method='checked_mathematical_theorem', query='Success(previous,x) AND Success(current,x)', quantification='all legal placements', free_cells=board['grid_size'] ** 2 - 1 - len(board['filters']) - len(board['sensors']), choices_per_free_cell=5, device_count_limit=None, changed_sensors=changed, previous_targets=old['target_colors'], current_targets=new['target_colors'], argument='On this fixed board, a passing H/V trace has no wrong-axis arrivals and equals open-filter physics. P/A/N never change ray propagation. Thus if a layout passes both rounds its received-color sets are identical in both. Full success requires each sensor to receive exactly its singleton target color. At least one target differs, so no layout can succeed in both rounds. This includes every reachable branch and cycle and places no bound on the number of devices.', requires_previous_solution_rule_violation=False)



SEED = 2026102806


ID = 'mirror-light-all-moving-v3-flexible-layout-100x5'


ORDERS = sorted((''.join(p) for p in itertools.permutations('HVPAN')))


FIRST = ['H', 'V', 'P', 'A', 'N']


CELLS = list(itertools.product(range(1, 11), repeat=2))


ZONE_IDS = sorted({region_id(r, c) for r, c in CELLS})


def random_plan(number):
    """Choose rule order, unique target pairs, and independent designated regions."""
    color_rng = random.Random(SEED + number * 100003 + 101)
    order_rng = random.Random(SEED + number * 100003 + 303)
    zone_rng = random.Random(SEED + number * 100003 + 505)
    order = order_rng.choice([o for o in ORDERS if o[0] == FIRST[(number - 1) % 5]])
    targets = []
    for kind in order:
        while True:
            pair = tuple((color_rng.choice(['red', 'blue', 'white']) for _ in range(2)))
            if kind in ('H', 'V') and pair == ('white', 'white'):
                continue
            if pair in targets:
                continue
            break
        targets.append(pair)
    zones = {kind: zone_rng.choice(ZONE_IDS) for kind in ('P', 'A')}
    return dict(targets=targets, order=order, regions=zones)


def direction_room(position, n=10):
    r, c = position
    return dict(up=r - 1, right=n - c, down=n - r, left=c - 1)


def widest_directions(position, n=10):
    """Return all directions tied for the greatest distance to the grid boundary."""
    room = direction_room(position, n)
    maximum = max(room.values())
    return [d for d in DIRECTIONS if room[d] == maximum]


PLANS = {n: random_plan(n) for n in range(1, 101)}


def replay(layout, b, r):
    """Evaluate a saved layout on a specified board and rule."""
    try:
        o = simulate(b, layout, r)
        e = evaluate(r, o)
        return dict(valid=True, rule_satisfied=e['rule_satisfied'], success=e['success'], received_colors=o['received_colors'])
    except (ValueError, KeyError, TypeError):
        return dict(valid=False, rule_satisfied=None, success=False, received_colors=None)


def random_board(rng, zone, prior_boards):
    """Sample moving objects outside the currently active designated region."""
    available = [p for p in CELLS if region_id(*p) != zone]
    previous = prior_boards[-1] if prior_boards else None
    used_sources = {tuple(b['source']['position']) for b in prior_boards}
    old_filter_cells = {tuple(f['position']) for f in previous['filters']} if previous else set()
    old_sensor_cells = {tuple(s['position']) for s in previous['sensors']} if previous else set()
    used_filters = {color: {tuple(f['position']) for b in prior_boards for f in b['filters'] if f['color'] == color} for color in ['red', 'blue']}
    used_sensors = {sid: {tuple(s['position']) for b in prior_boards for s in b['sensors'] if s['id'] == sid} for sid in ['S1', 'S2']}
    for _ in range(1000):
        red, blue = rng.sample(available, 2)
        if red in used_filters['red'] or blue in used_filters['blue'] or {red, blue} & old_filter_cells:
            continue
        sources = [p for p in available if p not in used_sources and p not in (red, blue)]
        if not sources:
            continue
        src = rng.choice(sources)
        direction = rng.choice(widest_directions(src))
        free = [p for p in available if p not in (src, red, blue) and p not in old_sensor_cells]
        possible1 = [p for p in free if p not in used_sensors['S1']]
        if not possible1:
            continue
        s1 = rng.choice(possible1)
        possible2 = [p for p in free if p not in used_sensors['S2'] and p != s1]
        if not possible2:
            continue
        s2 = rng.choice(possible2)
        return board(list(src), list(red), list(blue), list(s1), list(s2), direction)
    return None


def candidate(number, retry=0):
    """Search five solvable rounds while preserving the preselected rule and target schedule."""
    started = time.monotonic()
    rng = random.Random(SEED + number * 100003 + retry * 1000000007)
    plan = PLANS[number]
    order = plan['order']
    targets = plan['targets']
    for restart in range(30):
        counts = [4] * 5
        for i in rng.sample(range(5), rng.randrange(4)):
            counts[i] = 5
        rounds = []
        witnesses = []
        keys = set()
        tries = 0
        for i, (kind, target) in enumerate(zip(order, targets)):
            zone = plan['regions'].get(kind)
            r = rr(kind, target, zone)
            r['round_index'] = i + 1
            r['changed_sensors'] = [] if i == 0 else [s for s in ['S1', 'S2'] if r['target_colors'][s] != rounds[-1]['target_colors'][s]]
            found = False
            for attempt in range(120):
                tries += 1
                b = random_board(rng, zone, [x['board'] for x in rounds])
                if b is None:
                    break
                g = geometry_key(b)
                if g in keys:
                    continue
                if witnesses and replay(witnesses[-1]['layout'], b, r)['success']:
                    continue
                w = solve(b, r, rng, max_mirrors=counts[i], accept=lambda layout: sum((p['type'] == 'mirror' for p in layout['placements'])) == counts[i])
                if w is None:
                    continue
                r['board'] = b
                rounds.append(r)
                keys.add(g)
                w['round_index'] = i + 1
                if i:
                    w['previous_layout_current'] = replay(witnesses[-1]['layout'], b, r)
                    w['previous_layout_previous_rule'] = replay(witnesses[-1]['layout'], b, rounds[-2])
                witnesses.append(w)
                found = True
                break
            if not found:
                break
        if len(rounds) == 5:
            p = dict(problem_number=number, problem_id=f'all_moving_{number:03d}', designated_regions=dict(pass_region=plan['regions']['P'], avoid_region=plan['regions']['A']), region_design=dict(kind='empty_independent_regions', pass_round=order.index('P') + 1, avoid_round=order.index('A') + 1), rounds=rounds)
            return dict(problem=p, witness=dict(problem_number=number, problem_id=p['problem_id'], rounds=witnesses), generation=dict(retry=retry, restarts=restart, candidates=tries, seconds=round(time.monotonic() - started, 3)))
    raise RuntimeError(f'No solution for problem {number}')


def package(items):
    """Write the unchanged problem content, solutions, and public boards as JSON."""
    problems = [x['problem'] for x in items]
    n = len(items)
    settings = dict(problem_count=n, rounds_per_problem=5, grid_size=10, max_attempts_per_round=20, consecutive_correct_to_advance=2, device_count_limit=None, witness_max_mirrors=5, witness_splitters=1, single_rule_per_round=True, adjacent_rule_pairs=[], rule_order_sampling='random_permutation_with_balanced_first_rule', first_rule_counts_per_type=20, source_changes_every_round=True, filters_change_every_round=True, sensors_change_every_round=True, fixed_objects=[], source_at_corner=False, initial_source_at_corner=False, source_max_manhattan_distance_from_initial=None, source_max_step_manhattan_distance=None, source_position_domain='all 100 cells subject to occupancy, empty active region and solvability', source_positions_distinct_per_problem=True, filter_rows_and_cols=list(range(1, 11)), sensor_min_manhattan_from_source=None, min_filter_sensor_separation=None, object_min_manhattan_distance=None, designated_region_ids=ZONE_IDS, pass_avoid_regions_independently_sampled=True, designated_regions_must_be_empty=True, designated_region_empty_scope='active_round', sensor_color_changes_per_transition=None, sensor_changes_alternate=False, target_color_sampling='random_pairs_without_replacement_per_problem_reject_axis_white_white', target_pair_repeat_allowed=False, target_pairs_unique_within_problem=True, adjacent_target_pairs_must_differ=True, filter_axis_requires_nonwhite_target=True, source_direction_sampling='max_distance_to_grid_boundary_random_ties', source_direction_repeat_allowed=True, target_schedule_frozen_before_geometry_search=True, per_reference_board='current_round', per_previous_target_colors_used=False, previous_color_probe_required=False, requires_previous_solution_rule_violation=False, transition_exclusion='audit_only_not_a_sampling_constraint', full_success_exclusion_required=False, cross_board_universal_success_exclusion_claimed=False, stored_previous_witness_reuse_must_fail=True, selected_using_model_results=False, generation_seed=SEED, region_design_counts=dict(collections.Counter((p['region_design']['kind'] for p in problems))))
    dataset = dict(dataset_id=ID, settings=settings, problems=problems)
    witnesses = dict(dataset_id=ID, problems=[x['witness'] for x in items])
    proof_items = []
    for p, w in zip(problems, witnesses['problems']):
        certs = []
        for i in range(1, 5):
            old, new = p['rounds'][i - 1:i + 1]
            c = success_certificate(new['board'], old, new)
            c.update(from_round=i, to_round=i + 1, scope='both_rule_evaluations_on_current_round_board', reference_board=new['board'], previous_stored_witness_current_success=w['rounds'][i]['previous_layout_current']['success'], previous_stored_witness_previous_rule_satisfied=w['rounds'][i]['previous_layout_previous_rule']['rule_satisfied'])
            certs.append(c)
        proof_items.append(dict(problem_number=p['problem_number'], transitions=certs))
    proven = sum((c['status'] == 'proven_success_exclusive' for p in proof_items for c in p['transitions']))
    proof = dict(dataset_id=ID, audit_passed=True, criterion='same_current_board_full_success_exclusion_audit', full_exclusivity_required=False, cross_board_universal_success_exclusion_claimed=False, transitions_assessed=n * 4, transitions_proven=proven, transitions_not_claimed=n * 4 - proven, problems=proof_items)
    dump(ROOT / 'dataset/private/dataset.json', dataset)
    dump(ROOT / 'dataset/private/solution_witnesses.json', witnesses)
    dump(ROOT / 'dataset/exclusivity-proof.json', proof)
    for p in problems:
        for r in p['rounds']:
            public = {k: r['board'][k] for k in ['grid_size', 'source', 'filters', 'sensors']}
            dump(ROOT / f"dataset/public_boards/problem_{p['problem_number']:03d}/round_{r['round_index']}.json", public)
    digest = lambda f: hashlib.sha256((ROOT / f).read_bytes()).hexdigest()
    dump(ROOT / 'dataset/manifest.json', dict(dataset_id=ID, problems=n, rounds=n * 5, dataset_sha256=digest('dataset/private/dataset.json'), witness_sha256=digest('dataset/private/solution_witnesses.json'), proof_sha256=digest('dataset/exclusivity-proof.json'), prompt_file='prompt.json', prompt_language='en', prompt_sha256=hashlib.sha256(read_prompt(ROOT / 'dataset').encode('utf-8')).hexdigest(), prompt_json_sha256=digest('dataset/prompt.json')))


def read_json(path):
    """Read an ordinary UTF-8 JSON artifact."""
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_prompt(folder=DATASET):
    """Read the exact model-visible prompt from its JSON envelope."""
    value = read_json(Path(folder) / 'prompt.json')
    if not isinstance(value, dict) or value.get('language') != 'en' or not isinstance(value.get('text'), str):
        raise ValueError('prompt.json must contain language="en" and a text string')
    return value['text']


def verify(folder=DATASET):
    """Check all 500 solutions, geometry constraints, public boards, and hashes."""
    folder = Path(folder)
    dataset = read_json(folder / 'private/dataset.json')
    witnesses = read_json(folder / 'private/solution_witnesses.json')
    proof = read_json(folder / 'exclusivity-proof.json')
    report = validateDataset(dataset, witnesses, proof)
    manifest = read_json(folder / 'manifest.json')
    if manifest.get('prompt_file') != 'prompt.json' or manifest.get('prompt_language') != 'en':
        raise ValueError('The dataset must use the English prompt.json file.')
    for key, relative in [
        ('dataset_sha256', 'private/dataset.json'),
        ('witness_sha256', 'private/solution_witnesses.json'),
        ('proof_sha256', 'exclusivity-proof.json'),
        ('prompt_json_sha256', 'prompt.json'),
    ]:
        if sha256(folder / relative) != manifest.get(key):
            raise ValueError('Manifest mismatch: ' + relative)
    if hashlib.sha256(read_prompt(folder).encode('utf-8')).hexdigest() != manifest.get('prompt_sha256'):
        raise ValueError('Model-visible prompt text hash mismatch')
    public_count = 0
    for problem in dataset['problems']:
        for round_ in problem['rounds']:
            expected = {key: round_['board'][key] for key in ('grid_size', 'source', 'filters', 'sensors')}
            path = folder / 'public_boards' / f"problem_{problem['problem_number']:03d}" / f"round_{round_['round_index']}.json"
            if read_json(path) != expected:
                raise ValueError('Public board mismatch: ' + str(path))
            public_count += 1
    return {**report, 'public_boards_verified': public_count, 'dataset_sha256': manifest['dataset_sha256']}


def publish_identical_or_missing(generated, destination=DATASET):
    """Check every generated file before writing only missing JSON files."""
    generated, destination = Path(generated), Path(destination)
    missing = []
    identical = 0
    for source in sorted(generated.rglob('*')):
        if not source.is_file():
            continue
        if source.suffix != '.json':
            raise ValueError('Only JSON artifacts may be published')
        relative = source.relative_to(generated)
        target = destination / relative
        if target.exists():
            same = target.read_bytes() == source.read_bytes()
            if not same and relative.as_posix() == 'manifest.json':
                same = read_json(target) == read_json(source)
            if not same:
                raise ValueError(f'Existing file differs; nothing was published: {relative}')
            identical += 1
        else:
            missing.append((source, target))
    for source, target in missing:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    return {'existing_files_preserved': identical, 'missing_files_created': len(missing)}


def generate(folder=DATASET, workers=4):
    """Reproduce all 100 problems, verify them, and publish missing JSON files."""
    global ROOT
    folder = Path(folder).resolve()
    if not isinstance(workers, int) or not 1 <= workers <= 32:
        raise ValueError('workers must be between 1 and 32')
    prompt_path = folder / 'prompt.json'
    read_prompt(folder)
    print(f'Generating 100 problems / 500 rounds; seed={SEED}; workers={workers}; no API.', flush=True)
    if workers == 1:
        iterator = map(candidate, range(1, 101))
        pool = None
    else:
        pool = ProcessPoolExecutor(max_workers=workers)
        iterator = pool.map(candidate, range(1, 101))
    try:
        items = []
        for item in iterator:
            items.append(item)
            if len(items) % 10 == 0:
                print(f'Generated {len(items)}/100', flush=True)
    finally:
        if pool is not None:
            pool.shutdown()
    seen = set()
    for index, item in enumerate(items):
        retry = 0
        number = item['problem']['problem_number']
        while any(geometry_key(round_['board']) in seen for round_ in item['problem']['rounds']):
            retry += 1
            item = candidate(number, retry)
        seen.update(geometry_key(round_['board']) for round_ in item['problem']['rounds'])
        items[index] = item
    with tempfile.TemporaryDirectory(prefix='mirror-generate-') as temporary:
        work = Path(temporary).resolve()
        (work / 'dataset').mkdir()
        shutil.copyfile(prompt_path, work / 'dataset/prompt.json')
        previous_root = ROOT
        try:
            ROOT = work
            package(items)
        finally:
            ROOT = previous_root
        report = verify(work / 'dataset')
        publication = publish_identical_or_missing(work / 'dataset', folder)
        return {
            'passed': True, 'seed': SEED, 'problems': 100, 'rounds': 500,
            'solution_witnesses_verified': report['solution_witnesses_verified'],
            'public_boards_verified': report['public_boards_verified'],
            'unique_round_geometries': report['unique_round_geometries'],
            'candidate_attempts': sum(item['generation']['candidates'] for item in items),
            'dataset_sha256': report['dataset_sha256'], 'output_folder': str(folder),
            'cache_used': False, 'paid_api_calls': 0, **publication,
        }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['generate', 'verify'])
    parser.add_argument('--folder', type=Path, default=DATASET, help='Dataset folder containing prompt.json')
    parser.add_argument('--workers', type=int, default=4, help='Parallel generation workers (1-32)')
    args = parser.parse_args(argv)
    if not 1 <= args.workers <= 32:
        parser.error('--workers must be between 1 and 32')
    report = verify(args.folder) if args.command == 'verify' else generate(args.folder, args.workers)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    multiprocessing.freeze_support()
    main()
