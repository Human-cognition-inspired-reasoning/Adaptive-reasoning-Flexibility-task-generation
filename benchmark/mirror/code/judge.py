"""Simulate light paths and judge submitted layouts for every supported model.

The simulator advances each ray one grid cell at a time. It applies mirror
reflections, splitter branches, filter direction/color checks, and sensor stops.
The same implementation checks generated solutions and real model submissions.
JSON helpers keep stored request hashes and feedback serialization reproducible.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import json
import math
import re

class _Undefined:
    def __repr__(self):
        return 'undefined'

    def __bool__(self):
        return False

    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        return self


UNDEFINED = _Undefined()


def js_truthy(value):
    if value is UNDEFINED or value is None or value is False:
        return False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value != 0 and not (isinstance(value, float) and math.isnan(value))
    if isinstance(value, str):
        return len(value) != 0
    return True  # Containers are present values, including empty lists and dictionaries.


def js_number_string(value):
    value = float(value)
    if math.isnan(value):
        return 'NaN'
    if math.isinf(value):
        return '-Infinity' if value < 0 else 'Infinity'
    if value == 0:
        return '0'
    negative = value < 0
    value = abs(value)
    shortest = repr(value)
    if 1e-6 <= value < 1e21:
        out = format(Decimal(shortest), 'f')
        if '.' in out:
            out = out.rstrip('0').rstrip('.')
    else:
        if 'e' not in shortest.lower():
            shortest = format(value, '.15e')
        mantissa, exponent = shortest.lower().split('e')
        mantissa = mantissa.rstrip('0').rstrip('.') if '.' in mantissa else mantissa
        number = int(exponent)
        out = mantissa + 'e' + ('+' if number >= 0 else '-') + str(abs(number))
    return ('-' if negative else '') + out


def js_string(value):
    if value is UNDEFINED:
        return 'undefined'
    if value is None:
        return 'null'
    if value is True:
        return 'true'
    if value is False:
        return 'false'
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return js_number_string(value)
    if isinstance(value, list):
        return ','.join('' if x is None or x is UNDEFINED else js_string(x) for x in value)
    return '[object Object]'


def js_strict_equal(a, b):
    if a is UNDEFINED or b is UNDEFINED:
        return a is b
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    if isinstance(a, (list, dict)) or isinstance(b, (list, dict)):
        return a is b
    return type(a) is type(b) and a == b


def js_deep_strict_equal(a, b):
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if math.isnan(float(a)) and math.isnan(float(b)):
            return True
        if a == b == 0:
            return math.copysign(1, a) == math.copysign(1, b)
        return a == b
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(js_deep_strict_equal(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(js_deep_strict_equal(x, y) for x, y in zip(a, b))
    return a == b


def _quote(value):
    # Keep Unicode readable and escape unpaired UTF-16 surrogates.
    value = value.encode('utf-16-le', errors='surrogatepass').decode('utf-16-le', errors='surrogatepass')
    result = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    return re.sub('[\ud800-\udfff]', lambda m: '\\u%04x' % ord(m.group()), result)


def _object_items(value):
    indexed, others = [], []
    for key, item in value.items():
        key = js_string(key)
        if re.fullmatch(r'0|[1-9][0-9]*', key) and int(key) < 4294967295:
            indexed.append((int(key), key, item))
        else:
            others.append((key, item))
    return [(k, v) for _, k, v in sorted(indexed)] + others


def js_dumps(value, indent=None):
    gap = '' if indent is None else (' ' * min(10, max(0, int(indent))) if isinstance(indent, (int, float)) else str(indent)[:10])
    active = set()

    def encode(v, depth=0):
        if v is UNDEFINED:
            return UNDEFINED
        if v is None:
            return 'null'
        if v is True:
            return 'true'
        if v is False:
            return 'false'
        if isinstance(v, str):
            return _quote(v)
        if isinstance(v, (int, float)):
            return js_number_string(v) if math.isfinite(float(v)) else 'null'
        if not isinstance(v, (dict, list, tuple)):
            raise TypeError(f'Unsupported JSON type: {type(v).__name__}')
        if id(v) in active:
            raise ValueError('Converting circular structure to JSON')
        active.add(id(v))
        if isinstance(v, dict):
            pairs = []
            for k, item in _object_items(v):
                encoded = encode(item, depth + 1)
                if encoded is not UNDEFINED:
                    pairs.append(_quote(k) + (': ' if gap else ':') + encoded)
            opening, closing = '{', '}'
        else:
            pairs = []
            for item in v:
                encoded = encode(item, depth + 1)
                pairs.append('null' if encoded is UNDEFINED else encoded)
            opening, closing = '[', ']'
        active.remove(id(v))
        if not pairs:
            return opening + closing
        if not gap:
            return opening + ','.join(pairs) + closing
        return opening + '\n' + gap * (depth + 1) + (',\n' + gap * (depth + 1)).join(pairs) + '\n' + gap * depth + closing

    return encode(value)


def _utf16_length(text):
    return len(text.encode('utf-16-le', 'surrogatepass')) // 2


def _parse_error(text, error):
    pos = error.pos
    message = error.msg
    diagnostic = None
    if message.startswith('Illegal trailing comma before end of '):
        pos += 1
        while pos < len(text) and text[pos] in ' \t\r\n':
            pos += 1
        if message.endswith('object'):
            message = 'Expecting property name enclosed in double quotes'
        else:
            message = 'Expecting value'
    if message.startswith('Invalid \\escape'):
        pos += 1
    elif message.startswith('Invalid \\u'):
        pos += 1
        while pos < len(text) and text[pos] in '0123456789abcdefABCDEF':
            pos += 1
    elif message == 'Expecting value' and pos < len(text):
        char = text[pos]
        if char == '-':
            pos += 1
            diagnostic = 'No number after minus sign in JSON'
        elif char in 'tfn':
            literal = {'t':'true','f':'false','n':'null'}[char]
            start = pos
            while pos < len(text) and pos-start < len(literal) and text[pos] == literal[pos-start]:
                pos += 1
    if diagnostic is None and message in ('Extra data', "Expecting ',' delimiter") and pos < len(text):
        char = text[pos]
        if char.isascii() and char.isdigit() and pos and text[pos-1] == '0':
            diagnostic = 'Unexpected number in JSON'
        elif char == '.' and pos and text[pos-1].isdigit():
            pos += 1
            diagnostic = 'Unterminated fractional number in JSON'
        elif char in 'eE' and pos and text[pos-1].isdigit():
            pos += 1
            if pos < len(text) and text[pos] in '+-':
                pos += 1
            diagnostic = 'Exponent part is missing a number in JSON'
    prefix = text[:pos]
    position = _utf16_length(prefix)
    line = prefix.count('\n') + 1
    column = _utf16_length(prefix.rsplit('\n', 1)[-1]) + 1
    at = f' at position {position} (line {line} column {column})'
    tail = text[pos:]
    stripped = text.strip()
    if diagnostic is not None:
        return diagnostic + at
    if message == 'Extra data':
        return 'Unexpected non-whitespace character after JSON' + at
    if message.startswith('Unterminated string'):
        return 'Unterminated string in JSON' + f' at position {_utf16_length(text)} (line {text.count(chr(10))+1} column {_utf16_length(text.rsplit(chr(10),1)[-1])+1})'
    if message.startswith('Invalid control character'):
        return 'Bad control character in string literal in JSON' + at
    if message.startswith('Invalid \\escape'):
        return 'Bad escaped character in JSON' + at
    if message.startswith('Invalid \\u'):
        return 'Bad Unicode escape in JSON' + at
    if message == 'Expecting property name enclosed in double quotes':
        previous = prefix.rstrip()
        return ('Expected double-quoted property name in JSON' if previous.endswith(',') else "Expected property name or '}' in JSON") + at
    if message == "Expecting ':' delimiter":
        return "Expected ':' after property name in JSON" + at
    if message == "Expecting ',' delimiter":
        # Determine the innermost still-open container, ignoring quoted strings.
        stack=[]; quoted=False; escaped=False
        for char in prefix:
            if quoted:
                if escaped: escaped=False
                elif char=='\\': escaped=True
                elif char=='"': quoted=False
            elif char=='"': quoted=True
            elif char in '[{': stack.append(char)
            elif char in ']}':
                if stack: stack.pop()
        return ("Expected ',' or ']' after array element in JSON" if stack and stack[-1]=='[' else "Expected ',' or '}' after property value in JSON") + at
    if pos >= len(text):
        return 'Unexpected end of JSON input'
    if stripped in ('undefined', 'NaN', 'Infinity', '-Infinity', '[object Object]'):
        return f'"{stripped}" is not valid JSON'
    if len(text) <= 20:
        excerpt = '"' + text + '"'
    elif pos < 10:
        excerpt = '"' + text[:10] + '"...'
    elif pos >= len(text)-10:
        excerpt = '...' + '"' + text[-10:] + '"'
    else:
        excerpt = '...' + '"' + text[pos-10:pos+10] + '"' + '...'
    return f"Unexpected token '{tail[0]}', {excerpt} is not valid JSON"


def json_loads(text):
    """Parse strict JSON with finite literals and IEEE-754 numeric precision."""
    if not isinstance(text, str):
        text = js_string(text)
    def parse_integer(token):
        number = int(token)
        return number if abs(number) <= 9007199254740991 else float(token)
    def reject_constant(token):
        raise json.JSONDecodeError('Expecting value', text, text.find(token))
    try:
        return json.loads(text, parse_int=parse_integer, parse_constant=reject_constant)
    except json.JSONDecodeError as error:
        raise ValueError(_parse_error(text, error)) from None


_strict = js_strict_equal
_same = js_deep_strict_equal
_string = js_string

COLORS = ['red', 'blue', 'white']


BITS = {'red': 1, 'blue': 2, 'white': 4}


DIR = ['up', 'right', 'down', 'left']


DELTA = [[-1, 0], [0, 1], [1, 0], [0, -1]]


REFLECT = {'R': [1, 0, 3, 2], 'L': [3, 2, 1, 0]}


def _get(obj, key, optional=False):
    if obj is None or obj is UNDEFINED:
        if optional:
            return UNDEFINED
        raise TypeError(f"Cannot read properties of {_string(obj)} (reading '{key}')")
    if isinstance(obj, dict):
        return obj.get(str(key), UNDEFINED)
    if isinstance(obj, (list, str)):
        if key == 'length':
            return len(obj)
        text = _string(key)
        if re.fullmatch(r'0|[1-9][0-9]*', text):
            index = int(text)
            return obj[index] if index < len(obj) else UNDEFINED
    return UNDEFINED


def _number(value):
    if value is UNDEFINED:
        return math.nan
    if value is None:
        return 0
    if type(value) is bool:
        return int(value)
    if type(value) in (int, float):
        return value
    text = _string(value).strip()
    if not text:
        return 0
    try:
        if text.lower().startswith(('0x', '0b', '0o')):
            return int(text, 0)
        return float(text)
    except ValueError:
        return math.nan


def _integer(value):
    return type(value) in (int, float) and math.isfinite(value) and value == math.trunc(value)


def _key(r, c):
    return f'{_string(r)},{_string(c)}'


def _includes(values, target):
    return any(_strict(value, target) for value in values)


def regionId(r=UNDEFINED, c=UNDEFINED):
    r, c = _number(r), _number(c)
    if not math.isfinite(r) or not math.isfinite(c):
        return '\x00'
    return chr((65 + 5 * math.floor((r - 1) / 2) + math.floor((c - 1) / 2)) % 65536)


def allRegions():
    return [{'id': chr(65+i), 'rows': [2*(i//5)+1, 2*(i//5)+2],
             'cols': [2*(i % 5)+1, 2*(i % 5)+2]} for i in range(25)]


def simulate(board, layout, round=None):
    """Trace branched beams one cell at a time until blocked, sensed, or repeated.

    Direction and color form part of each visited state, so loops terminate
    without suppressing distinct beams. Forbidden filter-axis entries stop
    that branch. The returned trace also records regions and color overlaps.
    """
    axis = UNDEFINED
    if round is not None and round is not UNDEFINED:
        constraints = _get(round, 'constraints')
        if constraints is None or constraints is UNDEFINED:
            raise TypeError(f"Cannot read properties of {_string(constraints)} (reading 'find')")
        if not isinstance(constraints, list):
            raise TypeError('round?.constraints.find is not a function')
        for rule in constraints:
            if _strict(_get(rule, 'type'), 'filter_axis'):
                axis = _get(rule, 'value', optional=True)
                break
    allowed = ['up', 'down'] if _strict(axis, 'vertical') else ['left', 'right']
    if not isinstance(layout, dict) or list(layout) != ['placements'] or not isinstance(layout['placements'], list):
        raise ValueError('Expected exactly a placements array')
    n = board['grid_size']
    source = board['source']['position']
    filters = {_key(*f['position']): f['color'] for f in board['filters']}
    sensors = {_key(*s['position']): s['id'] for s in board['sensors']}
    reserved = {_key(*source), *filters, *sensors}
    devices = {}
    for item in layout['placements']:
        if not isinstance(item, dict) or sorted(item) != ['col', 'direction', 'row', 'type']:
            raise ValueError('Expected type, row, col, direction')
        typ, r, c, orientation = item['type'], item['row'], item['col'], item['direction']
        if not _includes(['mirror', 'splitter'], typ) or not _includes(['R', 'L'], orientation):
            raise ValueError('Invalid device or direction')
        if not _integer(r) or not _integer(c) or r < 1 or r > n or c < 1 or c > n:
            raise ValueError('Coordinates must be integers inside the grid')
        cell = _key(r, c)
        if cell in reserved or cell in devices:
            raise ValueError('Occupied or duplicate cell')
        devices[cell] = [typ, orientation]
    queue, visited, trace, segments = [], set(), [], []
    received, arrivals = {'S1': 0, 'S2': 0}, {'S1': [], 'S2': []}
    cellColors, filterArrivals = {}, []

    def emit(r, c, d, color):
        if d < 0 or d >= len(DELTA):
            raise TypeError('undefined is not iterable (cannot read property Symbol(Symbol.iterator))')
        dr, dc = DELTA[d]
        segments.append([r, c, r+dr, c+dc, color])
        queue.append([r+dr, c+dc, d, color])

    direction = board['source']['direction']
    emit(*source, DIR.index(direction) if direction in DIR else -1, 'white')
    at = 0
    while at < len(queue):
        r, c, d, color = queue[at]
        at += 1
        if r < 1 or r > n or c < 1 or c > n:
            continue
        state, cell = (r, c, d, color), _key(r, c)
        if state in visited:
            continue
        visited.add(state)
        trace.append([r, c, DIR[d], color])
        if cell not in reserved:
            cellColors.setdefault(cell, set()).add(color)
        if cell in sensors:
            sid = sensors[cell]
            received[sid] |= BITS[color]
            arrivals[sid].append({'direction': DIR[d], 'color': color})
            continue
        if cell in filters:
            reason = ('axis' if js_truthy(axis) and DIR[d] not in allowed else
                      'color' if color != 'white' and color != filters[cell] else None)
            filterArrivals.append({'position': [r, c], 'direction': DIR[d],
                                   'incoming_color': color, 'blocked_reason': reason,
                                   'transmitted_color': None if reason else filters[cell]})
            if reason:
                continue
            color = filters[cell]
        outs = [d]
        if cell in devices:
            typ, orientation = devices[cell]
            outs = [REFLECT[orientation][d]] if typ == 'mirror' else [d, REFLECT[orientation][d]]
        for out in outs:
            emit(r, c, out, color)
    overlaps = [{'position': [int(v) for v in p.split(',')],
                 'colors': [c for c in COLORS if c in colors]}
                for p, colors in cellColors.items() if len(colors) > 1]
    overlaps.sort(key=lambda a: (a['position'][0], a['position'][1]))
    return {'received_masks': received,
            'received_colors': {s: [c for c in COLORS if mask & BITS[c]] for s, mask in received.items()},
            'sensor_arrivals': arrivals, 'filter_arrivals': filterArrivals,
            'trace': trace, 'segments': segments, 'overlap_cells': overlaps,
            'visited_regions': sorted({regionId(*source), *(regionId(t[0], t[1]) for t in trace)}),
            'visited_state_count': len(visited)}


def evaluate(round, optics):
    """Compare traced paths with the active rule and each sensor's target color."""
    results = []
    constraints = _get(round, 'constraints')
    if constraints is None or constraints is UNDEFINED:
        raise TypeError(f"Cannot read properties of {_string(constraints)} (reading 'map')")
    if not isinstance(constraints, list):
        raise TypeError('round.constraints.map is not a function')
    for rule in constraints:
        typ = _get(rule, 'type')
        value = _get(rule, 'value')
        if _strict(typ, 'filter_axis'):
            if not _includes(['vertical', 'horizontal'], value):
                raise ValueError('Unknown filter axis')
            allowed = ['up', 'down'] if value == 'vertical' else ['left', 'right']
            passed = all(a['direction'] in allowed for a in optics['filter_arrivals'])
        elif _strict(typ, 'region'):
            region = _get(rule, 'id')
            if not _includes(['pass', 'avoid'], value) or re.fullmatch('[A-Y]', _string(region)) is None:
                raise ValueError('Unknown region rule')
            visited = _includes(optics['visited_regions'], region)
            passed = visited if value == 'pass' else not visited
        elif _strict(typ, 'different_color_overlap'):
            if not _strict(value, 'forbidden'):
                raise ValueError('Only forbidden overlap is supported')
            passed = len(optics['overlap_cells']) == 0
        else:
            raise ValueError('Unknown rule')
        results.append({'constraint': rule, 'passed': passed})
    valid = all(x['passed'] for x in results)
    matches = {s: len(optics['received_colors'][s]) == 1 and
               _strict(optics['received_colors'][s][0], _get(_get(round, 'target_colors'), s))
               for s in ['S1', 'S2']}
    return {'rule_satisfied': valid, 'constraint_results': results, 'raw_color_matches': matches,
            'detected_colors': deepcopy(optics['received_colors']) if valid else None,
            'sensor_matches': {s: valid and matches[s] for s in ['S1', 'S2']},
            'success': valid and all(matches.values())}


def boardForRound(problem, roundIndex):
    """Return the complete source, filter, and sensor geometry for one round."""
    board = _get(_get(_get(problem, 'rounds'), roundIndex), 'board', optional=True)
    if not js_truthy(board):
        raise ValueError('Every round requires a complete board')
    return board


def judge(problem, roundIndex, text, previousRoundHadSuccess=False):
    """Validate a submitted layout and produce correctness, feedback, and PER labels.

    Persistent error requires a valid current failure and a previous rule that
    accepts the same layout on the current board. Previous target colors do not
    enter that label. Eligibility requires an earlier full success in the
    immediately preceding round.
    """
    layout = optics = error = None
    try:
        layout = json_loads(text)
        optics = simulate(boardForRound(problem, roundIndex), layout, _get(problem['rounds'], roundIndex))
    except Exception as exc:
        error = str(exc)
    previousRule = _get(problem['rounds'], _number(roundIndex)-1)
    current = evaluate(_get(problem['rounds'], roundIndex), optics) if optics is not None else None
    previousOptics = (simulate(boardForRound(problem, roundIndex), layout, previousRule)
                      if optics is not None and js_truthy(previousRule) else None)
    previous = evaluate(previousRule, previousOptics) if previousOptics is not None else None
    correct = current is not None and current['success'] is True
    sensorResults = {s: 'correct' if current is not None and current['raw_color_matches'][s] is True else 'incorrect'
                     for s in ['S1', 'S2']}
    eligible = previousRoundHadSuccess if _number(roundIndex) > 0 else False
    persistent = (error is None and not correct and previous is not None and previous['rule_satisfied'] is True
                  if js_truthy(eligible) else eligible)
    return {'layout': layout, 'valid': error is None, 'validation_error': error,
            'optics': optics, 'previous_optics': previousOptics, 'current_evaluation': current,
            'previous_evaluation': previous, 'correct': correct, 'sensor_results': sensorResults,
            'feedback_received_colors': None,
            'feedback': js_dumps({'sensor_results': sensorResults, 'result': 'correct' if correct else 'incorrect'}),
            'previous_rule_accepts': previous['rule_satisfied'] if previous is not None else None,
            'previous_full_success': previous['success'] if previous is not None else None,
            'per_eligible': eligible, 'previous_rule_only_error': persistent,
            'per_reference_source': deepcopy(boardForRound(problem, roundIndex)['source']),
            'per_reference_geometry': deepcopy(boardForRound(problem, roundIndex)),
            'symbol': 'O' if correct else 'triangle' if js_truthy(persistent) else 'X'}
