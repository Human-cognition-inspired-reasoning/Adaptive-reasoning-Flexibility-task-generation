"""Run Mirror with GPT-6.1 Sol, Claude Opus 5.5, or Claude Sonnet 5.5.

One entry point controls model selection, trial limits, API conversation state,
saved receipts, offline replay, and resumption. All saved artifacts are JSON.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import datetime as _dt
import errno
import getpass
import hashlib
import http.client
import os
from pathlib import Path
import platform as _platform
import re
import shutil
import signal as signals
import socket
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit
import warnings

from judge import judge, boardForRound, js_dumps, json_loads, js_truthy, js_string, UNDEFINED
from dataset import validateDataset
from results import (readJSON, writeJSON, atomicWrite, readCalls, exportReports,
                     priceUsage, pricingForModel, normalizeUsage)

CODE = Path(__file__).resolve().parent
ROOT = CODE.parent
runtimeFiles = ['run_experiment.py', 'judge.py', 'dataset.py', 'results.py']
MODELS = {'sol': ('gpt-6.1-sol', 'openai'),
          'opus': ('claude-opus-5-5', 'anthropic'),
          'sonnet': ('claude-sonnet-5-5', 'anthropic')}
ENDPOINTS = {'openai': 'https://api.openai.com/v1/responses',
             'anthropic': 'https://api.anthropic.com/v1/messages'}
MAX_RESPONSE_BYTES = 32 * 1024 * 1024

PROTOCOL = 'mirror-light-all-moving-v3-flexible-layout-sensor-feedback-en-every-attempt-no-rule-disclosure-v4-correct-incorrect-100x5-p001-100-k2-T20'
CONFIG = {'model': 'gpt-6.1-sol', 'reasoning_effort': 'medium', 'k': 2, 'T': 20, 'rounds': 5, 'problems': 100, 'source_dataset_problems': 100, 'first_problem': 1, 'last_problem': 100, 'model_input_language': 'en', 'prompt_file': 'prompt.json', 'board_delivery': 'every_attempt', 'rule_existence_disclosed': False, 'feedback': 'per-sensor-color-correctness-and-overall-correct-incorrect-only', 'sensor_feedback_definition': 'actual_current_trace_matches_target_ignoring_rule_pass_flag', 'feedback_received_colors': False, 'feedback_rule_violation_reason': False, 'history': 'complete-output-items-with-encrypted-reasoning-per-problem', 'reasoning_context': 'all_turns', 'after_failed_round': 'skip_remaining_rounds_if_no_correct_submission', 'local_timeout_ms': 0, 'service_tier': 'default', 'per_definition': 'previous_round_any_full_success AND current_valid_failure AND same_layout_on_current_board_satisfies_previous_rule_IGNORING_previous_target_colors', 'per_reference_board': 'current_round', 'source_policy': 'all_grid_cells_no_initial_or_step_distance_limit', 'board_policy': 'source_both_filters_both_sensors_move_every_round', 'object_min_manhattan_distance': None, 'filter_position_domain': 'all_grid_cells', 'rule_order_sampling': 'random_permutation_with_balanced_first_rule', 'pass_avoid_regions': 'independent_all_25_regions_empty_in_active_round', 'target_color_sampling': 'random_pairs_without_replacement_per_problem_reject_axis_white_white', 'target_pairs_unique_within_problem': True, 'adjacent_target_pairs_must_differ': True, 'filter_axis_requires_nonwhite_target': True, 'source_direction_sampling': 'max_distance_to_grid_boundary_random_ties', 'device_types': ['mirror', 'splitter'], 'device_count_limit': None, 'provider': 'openai', 'max_output_tokens': None, 'api_timeout_seconds': 0}

OUTPUT_SCHEMA = {
    'type':'object','additionalProperties':False,'required':['placements'],
    'properties':{'placements':{'type':'array','items':{
        'type':'object','additionalProperties':False,
        'required':['type','row','col','direction'],
        'properties':{
            'type':{'type':'string','enum':['mirror','splitter']},
            'row':{'type':'integer','minimum':1,'maximum':10},
            'col':{'type':'integer','minimum':1,'maximum':10},
            'direction':{'type':'string','enum':['R','L']}
        }
    }}}
}




def _now():
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def _integer(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and float(value).is_integer()


def _text(path):
    return Path(path).read_bytes().decode('utf-8')


def hash(value):
    if isinstance(value, (bytes, bytearray, memoryview)):
        data = bytes(value)
    else:
        text = value if isinstance(value,str) else js_dumps(value)
        if text is UNDEFINED:
            raise TypeError('The data argument must be a string or bytes')
        # Normalize unpaired UTF-16 surrogates before computing UTF-8 hashes.
        text = text.encode('utf-16-le','surrogatepass').decode('utf-16-le','replace')
        data = text.encode('utf-8')
    return hashlib.sha256(data).hexdigest()


def normalizeConfig(config=None):
    """Validate runtime options without rewriting the released dataset."""
    supplied = dict(config or {})
    out = {**CONFIG, **supplied}
    model = out['model']
    if model in MODELS:
        model = MODELS[model][0]
    known = {v[0]: v[1] for v in MODELS.values()}
    if model not in known:
        raise ValueError('지원 모델은 sol, opus, sonnet입니다')
    out['model'], out['provider'] = model, known[model]
    for key in ('T', 'k', 'first_problem', 'last_problem'):
        if not _integer(out[key]):
            raise ValueError(key + '에는 정수를 입력하세요')
        out[key] = int(out[key])
    if not 1 <= out['k'] <= out['T']:
        raise ValueError('1 ≤ k ≤ T 조건을 만족해야 합니다')
    if not 1 <= out['first_problem'] <= out['last_problem'] <= 100:
        raise ValueError('문제 범위는 1~100 안이어야 합니다')
    out['problems'] = out['last_problem'] - out['first_problem'] + 1
    if out['reasoning_effort'] not in ('low', 'medium', 'high', 'xhigh', 'max'):
        raise ValueError('추론 설정은 low, medium, high, xhigh, max 중 하나입니다')
    limit = out['max_output_tokens']
    if limit is not None and (not _integer(limit) or not 1 <= limit <= 128000):
        raise ValueError('출력 토큰 상한은 1~128000의 정수여야 합니다')
    if out['provider'] == 'anthropic':
        out['max_output_tokens'] = 128000 if limit is None else int(limit)
        out['history'] = 'complete-message-content-with-thinking-per-problem'
    elif limit is not None:
        out['max_output_tokens'] = int(limit)
    timeout = out['api_timeout_seconds']
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 <= timeout < float('inf'):
        raise ValueError('API 대기 제한은 0 이상의 초 단위 숫자여야 합니다 (0: 제한 없음)')
    out['protocol'] = protocolFor(out)
    return out


def protocolFor(config):
    prefix = PROTOCOL.rsplit('-p001-100-k2-T20', 1)[0]
    return (f"{prefix}-p{config['first_problem']:03d}-{config['last_problem']:03d}"
            f"-k{config['k']}-T{config['T']}")


def readPrompt(file):
    value = readJSON(file)
    if not isinstance(value, dict) or value.get('language') != 'en' or not isinstance(value.get('text'), str):
        raise ValueError('프롬프트 JSON에는 language=en과 text 문자열이 필요합니다')
    return value['text']



def publicBoard(board):
    # Preserve this allowlist and property order. No private rule/target leaks.
    return {key:board[key] for key in ('grid_size','source','filters','sensors')}


def initialPrompt(problem, prompt=None):
    if prompt is None:
        prompt = readPrompt(ROOT / 'dataset/prompt.json')
    # Accept BOM and NBSP as input whitespace, but not U+0085.
    whitespace = '\u0009\u000a\u000b\u000c\u000d\u0020\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff'
    return prompt.strip(whitespace)+'\n\n'+updatedBoardInput(problem,0)


def updatedBoardInput(problem, roundIndex):
    return 'Current board. Submit your complete device layout for this board.\n'+js_dumps(publicBoard(boardForRound(problem,roundIndex)))


def _claudeSchema():
    """Use equivalent coordinate enums because Claude does not accept min/max."""
    schema = deepcopy(OUTPUT_SCHEMA)
    fields = schema['properties']['placements']['items']['properties']
    for key in ('row', 'col'):
        fields[key].pop('minimum')
        fields[key].pop('maximum')
        fields[key]['enum'] = list(range(1, 11))
    return schema


def buildRequest(s):
    if s['done']:
        raise ValueError('The experiment is complete')
    ensureHistory(s)
    config = s['config']
    if config['provider'] == 'anthropic':
        return {'model': config['model'], 'max_tokens': config['max_output_tokens'],
                'messages': deepcopy(s['history']), 'thinking': {'type': 'adaptive'},
                'output_config': {'effort': config['reasoning_effort'],
                                  'format': {'type': 'json_schema', 'schema': _claudeSchema()}},
                'stream': True}
    body = {'model':config['model'],'input':deepcopy(s['history']),
            'reasoning':{'effort':config['reasoning_effort'],'context':config['reasoning_context']},
            'tools':[],'tool_choice':'none','store':False,'stream':False,
            'truncation':'disabled','include':['reasoning.encrypted_content'],
            'service_tier':config['service_tier'],'prompt_cache_options':{'mode':'implicit','ttl':'30m'},
            'text':{'format':{'type':'json_schema','name':'optical_layout','strict':True,'schema':deepcopy(OUTPUT_SCHEMA)}}}
    if config['max_output_tokens'] is not None:
        body['max_output_tokens'] = config['max_output_tokens']
    return body


def responseText(response):
    if response.get('type') == 'message' and isinstance(response.get('content'), list):
        return ''.join(x.get('text') or '' for x in response['content'] if x.get('type') == 'text')
    content=[c for x in (response.get('output') or []) if x.get('type')=='message' for c in (x.get('content') or [])]
    text=''.join(x.get('text') or '' for x in content if x.get('type')=='output_text')
    return text or '\n'.join(x.get('refusal') or '' for x in content if x.get('type')=='refusal')


def completeResponse(response, provider=None):
    if not isinstance(response, dict):
        return False
    if provider == 'anthropic' or (provider is None and response.get('type') == 'message'):
        content = response.get('content')
        return (response.get('type') == 'message' and response.get('role') == 'assistant'
                and response.get('stop_reason') in ('end_turn', 'stop_sequence', 'refusal')
                and isinstance(content, list)
                and any(isinstance(x, dict) and x.get('type') == 'text' for x in content)
                and all(isinstance(x, dict) and x.get('type') in ('text','thinking','redacted_thinking') for x in content))
    if response.get('status') != 'completed':
        return False
    output = response.get('output')
    return (isinstance(output,list) and any(isinstance(x,dict) and x.get('type')=='message' for x in output)
            and all(isinstance(x,dict) and x.get('type') in ('reasoning','message') for x in output)
            and all(x.get('role')=='assistant' and x.get('status') not in ('in_progress','incomplete') for x in output if x.get('type')=='message'))


def parseClaudeStream(text):
    """Accumulate native Messages SSE blocks without discarding thinking signatures.

    The raw stream is saved separately. An EOF without message_stop is incomplete,
    even if a text block or stop_reason has already arrived.
    """
    message, blocks, open_blocks = None, {}, set()
    complete, supported, error = False, True, None
    frames = re.split(r'\r?\n\r?\n', text)
    for frame in frames:
        data = [line[5:].lstrip(' ').rstrip('\r') for line in frame.split('\n') if line.startswith('data:')]
        if not data:
            continue
        event = json_loads('\n'.join(data))
        kind = event.get('type')
        if kind == 'message_start':
            if message is not None:
                raise ValueError('Duplicate message_start')
            message = deepcopy(event['message'])
            message['content'] = []
        elif kind == 'content_block_start':
            index = event['index']
            if message is None or not _integer(index) or index < 0 or index in blocks:
                raise ValueError('Invalid content block order')
            blocks[index] = deepcopy(event['content_block'])
            open_blocks.add(index)
        elif kind == 'content_block_delta':
            index, delta = event['index'], event['delta']
            if index not in open_blocks:
                raise ValueError('Content delta outside an open block')
            field = {'text_delta':'text', 'thinking_delta':'thinking', 'signature_delta':'signature'}.get(delta.get('type'))
            if field:
                blocks[index][field] = blocks[index].get(field, '') + delta[field]
            else:
                # Tools and undocumented content changes are not valid layouts.
                supported = False
        elif kind == 'content_block_stop':
            index = event['index']
            if index not in open_blocks:
                raise ValueError('Content stop outside an open block')
            open_blocks.remove(index)
        elif kind == 'message_delta':
            if message is None:
                raise ValueError('Message delta before message_start')
            message.update(deepcopy(event.get('delta', {})))
            message.setdefault('usage', {}).update(deepcopy(event.get('usage', {})))
        elif kind == 'message_stop':
            complete = message is not None and not open_blocks
        elif kind == 'error':
            error = deepcopy(event.get('error', {'type':'stream_error','message':'Unknown stream error'}))
        # Ignore pings and future event names as required by the SSE contract.
    if message is None:
        message = {}
    if blocks:
        if sorted(blocks) != list(range(len(blocks))):
            raise ValueError('Content block indices are not contiguous')
        message['content'] = [blocks[i] for i in range(len(blocks))]
    if error is not None:
        message['error'] = error
    return message, bool(complete and supported and error is None and not open_blocks)



def createState(dataset,prompt,config=None):
    return {'config':normalizeConfig(config),'dataset':dataset,'prompt':prompt,'problemIndex':0,'roundIndex':0,
            'attempt':1,'streak':0,'history':[],'attempts':[],'rounds':[],
            'skippedRounds':[],'done':False}


def ensureHistory(s):
    if not s['history'] and not s['done']:
        s['history']=[{'role':'user','content':initialPrompt(s['dataset']['problems'][s['problemIndex']],s['prompt'])}]


def applyResponse(s,response,call):
    config = s['config']
    if s['done'] or not completeResponse(response, config['provider']):
        raise ValueError('Cannot apply an incomplete or unsupported response')
    ensureHistory(s)
    p=s['dataset']['problems'][s['problemIndex']]
    r=s['roundIndex']+1
    prev=next((x for x in s['rounds'] if x['problem']==p['problem_number'] and x['round']==r-1),None)
    previous_success=prev is not None and prev['correct_count']>0
    verdict=judge(p,s['roundIndex'],responseText(response),previous_success)
    s['streak']=s['streak']+1 if verdict['correct'] else 0
    ended=s['streak']>=config['k'] or s['attempt']>=config['T']
    row={
        'problem':p['problem_number'],'problem_id':p['problem_id'],'round':r,'attempt':s['attempt'],'call_id':call['call_id'],
        'response_id':response.get('id'),'served_model':response.get('model'),
        'effective_reasoning_context':(response.get('reasoning') or {}).get('context'),
        'submitted_at':call.get('finished_at',UNDEFINED),
        'started_at':call.get('started_at'),'finished_at':call.get('finished_at'),
        'experiment_submission':len(s['attempts'])+1,
        'problem_submission':sum(a['problem']==p['problem_number'] for a in s['attempts'])+1,
        'raw_output':responseText(response),**verdict,'previous_rule_error':verdict['previous_rule_only_error'],
        'current_source':deepcopy(boardForRound(p,s['roundIndex'])['source']),
        'current_board':publicBoard(boardForRound(p,s['roundIndex'])),'next_model_input':verdict['feedback'],
        'previous_round_had_success':previous_success,'previous_round_reached_k':prev.get('reached_k',False) if prev else False,
        'consecutive_correct':s['streak'],'round_ended':ended,
        'round_end_reason':('k_consecutive_correct' if s['streak']>=config['k'] else 'attempt_limit') if ended else None,
        'problem_ended':False,'problem_end_reason':None,'skipped_rounds':[],
        'current_constraints':deepcopy(p['rounds'][s['roundIndex']]['constraints']),
        'previous_constraints':deepcopy(p['rounds'][s['roundIndex']-1]['constraints']) if s['roundIndex'] else None,
        'current_target':deepcopy(p['rounds'][s['roundIndex']]['target_colors']),
        'previous_target':deepcopy(p['rounds'][s['roundIndex']-1]['target_colors']) if s['roundIndex'] else None,
        'usage':response.get('usage'),'api_seconds':call.get('api_seconds'),'cost':call.get('cost',UNDEFINED)
    }
    s['attempts'].append(row)
    row['round_incorrect_so_far']=sum(a['problem']==p['problem_number'] and a['round']==r and not a['correct'] for a in s['attempts'])
    row['problem_incorrect_so_far']=sum(a['problem']==p['problem_number'] and not a['correct'] for a in s['attempts'])
    # Preserve every provider-native reasoning block and signature.
    if config['provider'] == 'openai':
        s['history'].extend(deepcopy(response['output']))
    else:
        s['history'].append({'role': 'assistant', 'content': deepcopy(response['content'])})
    s['history'].append({'role':'user','content':row['feedback']})
    if ended:
        rr=[a for a in s['attempts'] if a['problem']==p['problem_number'] and a['round']==r]
        correctCount=sum(bool(a['correct']) for a in rr)
        s['rounds'].append({'problem':p['problem_number'],'round':r,'attempts':len(rr),
                            'correct_count':correctCount,'reached_k':s['streak']>=config['k'],
                            'end_reason':row['round_end_reason'],'previous_round_had_success':previous_success})
        if correctCount==0:
            for next_round in range(r+1,config['rounds']+1):
                s['skippedRounds'].append({'problem':p['problem_number'],'round':next_round,
                    'reason':'earlier_round_without_correct_submission','blocked_by_round':r})
                row['skipped_rounds'].append(next_round)
        s['roundIndex']+=1
        s['attempt']=1
        s['streak']=0
        if correctCount==0 or s['roundIndex']==config['rounds']:
            row['problem_ended']=True
            row['problem_end_reason']='round_without_correct_submission' if correctCount==0 else 'all_rounds_finished'
            s['problemIndex']+=1
            s['roundIndex']=0
            s['history']=[]
            s['done']=s['problemIndex']==len(s['dataset']['problems'])
    else:
        s['attempt']+=1
    if not row['problem_ended']:
        row['next_public_board']=publicBoard(boardForRound(p,s['roundIndex']))
        row['next_model_input']=row['feedback']+'\n'+updatedBoardInput(p,s['roundIndex'])
        s['history'][-1]['content']=row['next_model_input']
    return row


def loadFullDataset(root=ROOT):
    folder = Path(root) / 'dataset'
    file = folder / 'private' / 'dataset.json'
    dataset = readJSON(file)
    validateDataset(dataset, readJSON(folder / 'private' / 'solution_witnesses.json'), readJSON(folder / 'exclusivity-proof.json'))
    manifest = readJSON(folder / 'manifest.json')
    if manifest.get('prompt_file') != CONFIG['prompt_file'] or manifest.get('prompt_language') != CONFIG['model_input_language']:
        raise ValueError('프롬프트 파일 또는 언어 설정이 manifest와 다릅니다')
    if hash(readPrompt(folder / CONFIG['prompt_file'])) != manifest.get('prompt_sha256'):
        raise ValueError('프롬프트 파일 해시가 manifest와 다릅니다')
    if hash((folder / CONFIG['prompt_file']).read_bytes()) != manifest.get('prompt_json_sha256'):
        raise ValueError('프롬프트 JSON 파일 해시가 manifest와 다릅니다')
    if (hash((folder / 'private' / 'solution_witnesses.json').read_bytes()) != manifest.get('witness_sha256')
            or hash((folder / 'exclusivity-proof.json').read_bytes()) != manifest.get('proof_sha256')):
        raise ValueError('정답/정답 재사용 배제 증명 파일 해시가 다릅니다')
    if len(dataset['problems']) != CONFIG['source_dataset_problems']:
        raise ValueError(f"{CONFIG['source_dataset_problems']}문제 데이터셋이 아닙니다")
    if hash(file.read_bytes()) != manifest.get('dataset_sha256'):
        raise ValueError('데이터셋 파일 해시가 manifest와 다릅니다')
    st = dataset['settings']
    if st.get('device_count_limit', UNDEFINED) is not None:
        raise ValueError('데이터셋 설정 불일치')
    for i, p in enumerate(dataset['problems']):
        if p['problem_number'] != i + 1 or any(r['round_index'] != j + 1 for j, r in enumerate(p['rounds'])):
            raise ValueError('문제 또는 라운드 순서 불일치')
        for ri in range(5):
            public = folder / 'public_boards' / f"problem_{p['problem_number']:03d}" / f'round_{ri + 1}.json'
            if hash(readJSON(public)) != hash(publicBoard(boardForRound(p, ri))):
                raise ValueError('공개 배치와 평가 데이터가 다릅니다')
    return dataset


def loadDataset(root=ROOT,config=None):
    config = normalizeConfig(config)
    full = loadFullDataset(root)
    first, last = config['first_problem'], config['last_problem']
    if not _integer(first) or not _integer(last) or first < 1 or last > len(full['problems']) or last - first + 1 != config['problems']:
        raise ValueError('잘못된 실행 범위')
    return {**full, 'selection': {'source_problem_count': len(full['problems']), 'first_problem': first,
            'last_problem': last, 'selected_problem_count': config['problems'], 'order': 'original_dataset_order'},
            'problems': full['problems'][int(first) - 1:int(last)]}


def dateFolder():
    date = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(hours=9)
    return date.strftime('%Y-%m-%d_%H-%M-%S.') + f'{date.microsecond // 1000:03d}_KST'


def createRun(root=ROOT,config=None):
    settings = normalizeConfig(config)
    root = Path(root)
    dataset = loadDataset(root,settings)
    prompt = readPrompt(root / 'dataset' / settings['prompt_file'])
    pricing = pricingForModel(settings['model'])
    (root / 'result').mkdir(parents=True, exist_ok=True)
    for n in range(100):
        folder = root / 'result' / (dateFolder() + (f'_{n}' if n else ''))
        try:
            folder.mkdir()
            break
        except FileExistsError:
            if n == 99:
                raise
    arch = {'AMD64': 'x64', 'x86_64': 'x64', 'ARM64': 'arm64', 'aarch64': 'arm64'}.get(_platform.machine(), _platform.machine())
    created_at = _now()
    config = {**settings, 'created_at': created_at, 'dataset_id': dataset['dataset_id'],
              'record_source': 'live_api', 'snapshot_role': 'execution_and_evaluation',
              'snapshot_created_at': created_at,
              'selection': dataset['selection'], 'selected_problem_ids': [p['problem_id'] for p in dataset['problems']],
              'python_version': _platform.python_version(), 'runtime': 'python',
              'platform': 'win32' if os.name == 'nt' else ('darwin' if _platform.system() == 'Darwin' else 'linux'),
              'architecture': arch, 'time_zone': 'Asia/Seoul', 'dataset_hash': hash(dataset),
              'dataset_file_sha256': hash((root / 'dataset/private/dataset.json').read_bytes()),
              'prompt_hash': hash(prompt), 'pricing_hash': hash(pricing),
              'witness_file_sha256': hash((root / 'dataset/private/solution_witnesses.json').read_bytes()),
              'proof_file_sha256': hash((root / 'dataset/exclusivity-proof.json').read_bytes()),
              'manifest_file_sha256': hash((root / 'dataset/manifest.json').read_bytes()), 'code_hashes': {}}
    source_files = {name: _text(CODE / name) for name in runtimeFiles}
    config['code_hashes'] = {name: hash(text.encode('utf-8')) for name, text in source_files.items()}
    writeJSON(folder / 'code-snapshot.json', {'files': source_files})
    writeJSON(folder / 'dataset-snapshot.json', dataset)
    writeJSON(folder / 'prompt-snapshot.json', {'language': 'en', 'text': prompt})
    for src, dest in [('private/dataset.json', 'source-dataset.json'), ('private/solution_witnesses.json', 'solution-witnesses-snapshot.json'),
                      ('manifest.json', 'dataset-manifest-snapshot.json'), ('exclusivity-proof.json', 'exclusivity-proof-snapshot.json')]:
        shutil.copyfile(root / 'dataset' / src, folder / dest)
    writeJSON(folder / 'selection.json', {**dataset['selection'], 'source_sha256': config['dataset_file_sha256'], 'selected_problem_ids': config['selected_problem_ids']})
    writeJSON(folder / 'pricing-snapshot.json', pricing)
    writeJSON(folder / 'config.json', config)
    return str(folder)


def resolveResume(value, root=ROOT):
    if value != 'latest':
        return str(Path(value).resolve())
    result = Path(root) / 'result'
    names = sorted(p.name for p in result.iterdir() if (p / 'config.json').exists()) if result.exists() else []
    if not names:
        raise ValueError('이어서 실행할 결과 폴더가 없습니다')
    return str(result / names[-1])


def loadRun(folder):
    folder = Path(folder)
    config, dataset = readJSON(folder / 'config.json'), readJSON(folder / 'dataset-snapshot.json')
    prompt, pricing = readPrompt(folder / 'prompt-snapshot.json'), readJSON(folder / 'pricing-snapshot.json')
    checked = normalizeConfig(config)
    adjustable = {'model','provider','T','k','first_problem','last_problem','problems',
                  'reasoning_effort','max_output_tokens','api_timeout_seconds','history'}
    if config.get('protocol') != checked['protocol']:
        raise ValueError('저장된 실행 범위 또는 T/k와 protocol이 일치하지 않습니다')
    for key in CONFIG:
        if key not in adjustable and hash(config.get(key, UNDEFINED)) != hash(CONFIG[key]):
            raise ValueError('저장된 실험 설정이 현재 채점 방식과 다릅니다: ' + key)
    for key in adjustable:
        if key in config and hash(config[key]) != hash(checked[key]):
            raise ValueError('저장된 실행 설정이 잘못되었습니다: ' + key)
    if hash(dataset) != config['dataset_hash'] or hash(prompt) != config['prompt_hash'] or hash(pricing) != config['pricing_hash']:
        raise ValueError('저장된 데이터·프롬프트·단가 스냅샷이 변경됐습니다')
    for name, field in [('source-dataset.json','dataset_file_sha256'),
                        ('solution-witnesses-snapshot.json','witness_file_sha256'),
                        ('exclusivity-proof-snapshot.json','proof_file_sha256'),
                        ('dataset-manifest-snapshot.json','manifest_file_sha256')]:
        if hash((folder / name).read_bytes()) != config[field]:
            raise ValueError('검증 스냅샷이 변경됐습니다: ' + name)
    snapshot = readJSON(folder / 'code-snapshot.json')
    files, code_hashes = snapshot.get('files', {}), config['code_hashes']
    if set(code_hashes) != set(runtimeFiles) or set(files) != set(runtimeFiles):
        raise ValueError('실행 코드 스냅샷 목록이 현재 Python 런타임과 다릅니다')
    for name, expected in code_hashes.items():
        if not isinstance(files[name], str) or hash(files[name].encode('utf-8')) != expected:
            raise ValueError('저장된 코드 스냅샷이 변경됐습니다: ' + name)
        current = CODE / name
        if not current.is_file() or hash(current.read_bytes()) != expected:
            raise ValueError('실행 코드가 변경됐습니다. 저장된 code-snapshot.json의 코드와 일치시킨 뒤 재개하세요: ' + name)
    return {'config': config, 'dataset': dataset, 'prompt': prompt, 'pricing': pricing}



def replay(dataset, prompt, calls, config=None):
    state, seen = createState(dataset, prompt, config), set()
    for i, call in enumerate(calls):
        if call['call_id'] != i + 1:
            raise ValueError('API 기록 파일이 누락되었거나 순서가 다릅니다')
        if state['done']:
            raise ValueError('완료된 데이터 뒤에 요청 기록이 있습니다')
        if call['problem'] != state['dataset']['problems'][state['problemIndex']]['problem_number'] or call['round'] != state['roundIndex'] + 1 or call['attempt'] != state['attempt']:
            raise ValueError('요청 순서가 저장된 실험 진행과 다릅니다')
        if hash(buildRequest(state)) != call['request_sha256']:
            raise ValueError('이전 대화 또는 API 요청 설정이 바뀌었습니다')
        if call['status'] == 'completed':
            response = call.get('response') or {}
            if not response.get('id') or response['id'] in seen:
                raise ValueError('누락되거나 중복된 응답 ID')
            seen.add(response['id'])
            if hash(response) != call.get('response_sha256'):
                raise ValueError('응답 원본이 변경됐습니다')
            applyResponse(state, response, call)
    return state


def inspectResume(folder):
    loaded = loadRun(folder)
    dataset = loaded['dataset']
    state = replay(dataset, loaded['prompt'], readCalls(folder), loaded['config'])
    body = None if state['done'] else buildRequest(state)
    history = body.get('input', body.get('messages', [])) if body else []
    if loaded['config']['provider'] == 'anthropic':
        assistant_count = sum(x.get('role') == 'assistant' for x in history)
        reasoning_count = sum(x.get('type') in ('thinking', 'redacted_thinking')
                              for message in history if message.get('role') == 'assistant'
                              for x in message.get('content', []) if isinstance(x, dict))
    else:
        assistant_count = sum(x.get('type') == 'message' for x in history)
        reasoning_count = sum(x.get('type') == 'reasoning' for x in history)
    return {'done': state['done'], 'preserved_attempts': len(state['attempts']),
            'next_problem': None if state['done'] else dataset['problems'][state['problemIndex']]['problem_number'],
            'next_round': None if state['done'] else state['roundIndex'] + 1,
            'next_attempt': None if state['done'] else state['attempt'], 'consecutive_correct': state['streak'],
            'retained_assistant_messages': assistant_count,
            'retained_reasoning_items': reasoning_count, 'api_calls': 0}


def rebuildReports(folder):
    if (Path(folder) / 'run-lock.json').exists():
        raise ValueError('실행 중에는 보고서를 재생성하지 마세요. 종료 후 다시 실행하세요')
    loaded, calls = loadRun(folder), readCalls(folder)
    return exportReports(folder, replay(loaded['dataset'], loaded['prompt'], calls, loaded['config']), calls, loaded['config'])


def reconstructRequest(folder, callId):
    loaded, calls = loadRun(folder), readCalls(folder)
    if not _integer(callId) or callId < 1 or callId > len(calls):
        raise ValueError('저장된 API 호출 번호가 아닙니다')
    callId = int(callId)
    body = buildRequest(replay(loaded['dataset'], loaded['prompt'], calls[:callId - 1], loaded['config']))
    if hash(body) != calls[callId - 1]['request_sha256']:
        raise ValueError('재구성한 요청 해시가 원본과 다릅니다')
    return body


def _pidAlive(pid):
    if os.name == 'nt':
        # os.kill(pid, 0) is not a harmless liveness probe on Windows Python.
        import ctypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if handle:
            kernel.CloseHandle(handle)
            return True
        return ctypes.get_last_error() == 5  # access denied => process exists
    try:
        os.kill(pid, 0)
        return True
    except OSError as exc:
        return exc.errno == errno.EPERM


def acquireLock(folder):
    file = Path(folder) / 'run-lock.json'
    if file.exists():
        old = readJSON(file)
        if not _integer(old.get('pid')) or old['pid'] <= 0:
            raise ValueError('잘못된 run-lock.json 파일입니다')
        if _pidAlive(int(old['pid'])):
            raise ValueError(f"이 결과 폴더를 실행 중인 프로세스가 있습니다 (PID {old['pid']})")
        file.unlink()
    fd = os.open(file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        payload = js_dumps({'pid': os.getpid(), 'started_at': _now()}).encode('utf-8')
        with os.fdopen(fd, 'wb', closefd=False) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(fd)
    def release():
        if file.exists():
            file.unlink()
    return release


def _redactFailedReceipt(call, apiKey):
    """Remove an echoed credential from failed receipts without altering successes.

    Only the exact key used for this request is removed. Successful responses,
    including opaque reasoning data, retain their original bytes and hashes.
    """
    if call.get('status') == 'completed' or not isinstance(apiKey, str) or not apiKey:
        return

    def clean(value):
        if isinstance(value, str):
            return value.replace(apiKey, '[REDACTED]')
        if isinstance(value, list):
            return [clean(item) for item in value]
        if isinstance(value, dict):
            return {clean(key): clean(item) for key, item in value.items()}
        return value

    changed = False
    for field in ('raw_http_body', 'response', 'error', 'request_id'):
        if field not in call:
            continue
        old, new = call[field], clean(call[field])
        if old != new:
            call[field] = new
            changed = True
            if field == 'response':
                # Re-serialize when JSON escapes concealed the literal key in the
                # raw body; neither raw text nor parsed content may retain it.
                call['raw_http_body'] = js_dumps(new)
                call['response_sha256'] = hash(new)
    if changed:
        call['credential_redacted'] = True


class _Heartbeat:
    def __init__(self, log, start):
        self.stop = threading.Event()
        def beat():
            while not self.stop.wait(60):
                log(f'응답 대기 중 ({int(time.perf_counter() - start)}초)')
        self.thread = threading.Thread(target=beat, daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()


def redact(value, key=''):
    text = '' if value is None or value is UNDEFINED else js_string(value)
    return re.sub(r'sk-[A-Za-z0-9_-]+', '[REDACTED]', text.replace(key or '\x00', '[REDACTED]'))


class AbortError(RuntimeError):
    pass


class AbortSignal:
    """AbortController-compatible signal for synchronous calls and offline stubs."""
    def __init__(self):
        self.aborted = False
        self._callbacks = []
        self._lock = threading.RLock()

    def throwIfAborted(self):
        if self.aborted:
            raise AbortError('사용자가 요청을 중단했습니다')

    def add_abort_callback(self, callback):
        with self._lock:
            self._callbacks.append(callback)
            already = self.aborted
        if already:
            callback()
        return lambda: self.remove_abort_callback(callback)

    def remove_abort_callback(self, callback):
        with self._lock:
            if callback in self._callbacks:
                self._callbacks.remove(callback)

    def abort(self):
        with self._lock:
            if self.aborted:
                return
            self.aborted = True
            callbacks = list(self._callbacks)
        for callback in callbacks:
            try:
                callback()
            except Exception:
                pass


class AbortController:
    def __init__(self):
        self.signal = AbortSignal()

    def abort(self):
        self.signal.abort()


def _checkSignal(signal):
    if signal is None:
        return
    throw = getattr(signal, 'throwIfAborted', None)
    if throw:
        throw()
    elif getattr(signal, 'aborted', False):
        raise AbortError('사용자가 요청을 중단했습니다')


def _httpsRequest(endpoint, request):
    """One request, no redirects or automatic retries; returns a live HTTPResponse."""
    url = urlsplit(endpoint)
    if url.scheme != 'https':
        raise ValueError('HTTPS endpoint required')
    connection = http.client.HTTPSConnection(url.hostname, url.port or 443, timeout=request.get('timeout'))
    signal = request.get('signal')
    live_socket = [None]

    def cancel():
        sock = live_socket[0] or connection.sock
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        connection.close()

    unregister = signal.add_abort_callback(cancel) if hasattr(signal, 'add_abort_callback') else lambda: None
    try:
        _checkSignal(signal)
        connection.connect()
        live_socket[0] = connection.sock
        connection.auto_open = 0  # Never reopen after cancellation or a transport failure.
        _checkSignal(signal)
        connection.request(request['method'], url.path + ('?' + url.query if url.query else ''),
                           body=request['payload'], headers=request['headers'])
        response = connection.getresponse()
        response._benchmark_close = lambda: (unregister(), connection.close())
        return response
    except BaseException:
        unregister()
        connection.close()
        raise


def postJSON(options=None, **kwargs):
    """Send once. requestImpl(endpoint, request_dict) may inject an HTTPResponse stub.

    request_dict contains method, headers, payload bytes, signal, and a timeout.
    Raw UTF-8 response text (including opaque reasoning) is never redacted.
    """
    opts = dict(options or {})
    opts.update(kwargs)
    body, key, signal = opts['body'], opts['key'], opts.get('signal')
    payload = js_dumps(body).encode('utf-8')
    request = {'method': 'POST', 'signal': signal, 'timeout': opts.get('timeout') or None,
               'headers': {'authorization': f'Bearer {key}', 'content-type': 'application/json',
                           'accept': 'application/json', 'content-length': str(len(payload))},
               'payload': payload}
    provider = opts.get('provider', 'openai')
    if provider == 'anthropic':
        request['headers'].pop('authorization')
        request['headers'].update({'x-api-key': key, 'anthropic-version': '2023-06-01'})
    elif provider != 'openai':
        raise ValueError('Unknown API provider')
    response = None
    try:
        _checkSignal(signal)
        response = opts.get('requestImpl', _httpsRequest)(ENDPOINTS[provider], request)
        parts, total = [], 0
        getheader = getattr(response, 'getheader', None)
        headers = getattr(response, 'headers', {})
        header = lambda name: getheader(name) if getheader else headers.get(name)
        expected = header('content-length')
        expected = int(expected) if expected is not None else None
        while True:
            _checkSignal(signal)
            chunk = response.read(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_RESPONSE_BYTES:
                raise RuntimeError('API 응답이 32 MiB를 초과했습니다')
            parts.append(chunk)
        _checkSignal(signal)
        if getattr(response, 'complete', True) is False or (expected is not None and expected != total):
            raise RuntimeError('불완전한 HTTP 응답')
        return {'status': getattr(response, 'status', getattr(response, 'statusCode', None)),
                'request_id': header('x-request-id') or header('request-id'),
                'content_type': header('content-type'),
                'text': b''.join(parts).decode('utf-8', errors='replace')}
    except (KeyboardInterrupt, AbortError):
        raise AbortError('사용자가 요청을 중단했습니다') from None
    except http.client.IncompleteRead:
        raise RuntimeError('API 연결이 응답 완료 전에 끊겼습니다') from None
    except Exception as exc:
        message = '사용자가 요청을 중단했습니다' if getattr(signal, 'aborted', False) else redact(str(exc), key)
        raise RuntimeError(message) from None
    finally:
        if response is not None:
            close = getattr(response, '_benchmark_close', None) or getattr(response, 'close', None)
            if close:
                close()


def validateKey(value, provider='openai'):
    label = 'OpenAI' if provider == 'openai' else 'Anthropic'
    if not isinstance(value, str) or not re.fullmatch(r'sk-[A-Za-z0-9_-]+', value.strip()):
        raise ValueError(f'유효한 {label} API 키가 필요합니다. 키 형식을 확인하세요')
    return value.strip()



def _execute(command, args, options):
    kwargs = {'capture_output': True, 'encoding': options.get('encoding', 'utf8'), 'timeout': options.get('timeout', 15000) / 1000,
              'check': True}
    if os.name == 'nt':
        kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
    result = subprocess.run([command, *args], **kwargs)
    if len(result.stdout.encode('utf8')) > options.get('maxBuffer', 65536):
        raise ValueError('Clipboard output exceeds limit')
    return {'stdout': result.stdout}


def readClipboardApiKey(provider='openai', execute=_execute):
    powershell = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    script = '$OutputEncoding = [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false); $clipboardText = Get-Clipboard -Raw; if ($null -ne $clipboardText) { [Console]::Write($clipboardText) }'
    try:
        result = execute(str(powershell), ['-NoProfile', '-NonInteractive', '-Command', script],
                         {'encoding': 'utf8', 'windowsHide': True, 'timeout': 15000, 'maxBuffer': 65536})
    except Exception:
        raise RuntimeError('클립보드를 읽을 수 없습니다. API 키를 복사한 뒤 다시 실행하세요') from None
    value = result.get('stdout', '') if isinstance(result, dict) else getattr(result, 'stdout', '')
    return validateKey(str(value or '').strip(), provider)


def readApiKey(provider='openai', clipboard=False, environ=None, platform=None, secret_input=None):
    """Read an explicit clipboard key, an environment key, or a hidden prompt.

    Clipboard access is opt-in and available on Windows only. A terminal that
    cannot hide input is rejected rather than falling back to echoed input.
    """
    label = 'OpenAI' if provider == 'openai' else 'Anthropic'
    env_name = 'OPENAI_API_KEY' if provider == 'openai' else 'ANTHROPIC_API_KEY'
    if clipboard:
        if (os.name if platform is None else platform) != 'nt':
            raise ValueError('--clipboard는 Windows에서만 사용할 수 있습니다')
        print(f'{label} API 키를 복사한 뒤 Enter를 누르세요. 키를 터미널에 붙여넣지 마세요')
        input('복사 완료 후 Enter: ')
        return readClipboardApiKey(provider)
    env = os.environ if environ is None else environ
    value = env.get(env_name, '').strip()
    if value:
        return validateKey(value, provider)
    prompt = getpass.getpass if secret_input is None else secret_input
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)
            value = prompt(f'{label} API key (입력 내용은 표시되지 않습니다): ')
    except (getpass.GetPassWarning, EOFError):
        raise RuntimeError(f'키를 숨겨 입력할 수 없습니다. {env_name} 환경변수를 설정하거나 Windows에서 --clipboard를 사용하세요') from None
    return validateKey(value, provider)


def run(folder, options=None, **kwargs):
    options = {**(options or {}), **kwargs}
    apiKey, send, log, signal = options.get('apiKey'), options.get('send', postJSON), options.get('log', print), options.get('signal')
    folder = Path(folder)
    release = acquireLock(folder)
    try:
        loaded, calls = loadRun(folder), readCalls(folder)
        config, dataset, prompt, pricing = (loaded[k] for k in ('config', 'dataset', 'prompt', 'pricing'))
        for call in calls:
            if call['status'] == 'started':
                call.update(status='interrupted', error='이전 프로세스가 응답 저장 전에 중단됨', cost={'estimated_cost_usd': None, 'cost_status': 'usage_unknown_interruption'})
                writeJSON(folder / 'calls' / f"{call['call_id']:05d}.json", call)
        state = replay(dataset, prompt, calls, config)
        exportReports(folder, state, calls, config)
        log(f'결과 폴더: {folder}')
        if any(c['status'] in ('interrupted', 'transport_error') for c in calls):
            log('응답을 저장하지 못한 요청의 비용은 미확인으로 남습니다. 해당 게임 시도부터 이어갑니다')
        while not state['done']:
            _checkSignal(signal)
            body, problem = buildRequest(state), dataset['problems'][state['problemIndex']]
            message_key = 'input' if config['provider'] == 'openai' else 'messages'
            call = {'call_id': len(calls) + 1, 'problem': problem['problem_number'], 'round': state['roundIndex'] + 1,
                    'attempt': state['attempt'], 'status': 'started', 'problem_id': problem['problem_id'],
                    'previous_completed_call_ids': [c['call_id'] for c in calls if c['problem'] == problem['problem_number'] and c['status'] == 'completed'],
                    'started_at': _now(), 'finished_at': None, 'api_seconds': None, 'request_sha256': hash(body),
                    'input_items': len(body[message_key]), 'new_input': body[message_key][-1]['content'],
                    'request_settings': {k: v for k, v in body.items() if k != message_key}}
            file = folder / 'calls' / f"{call['call_id']:05d}.json"
            call['request_file'] = f"requests/{call['call_id']:05d}.json"
            writeJSON(folder / call['request_file'], body)
            calls.append(call)
            writeJSON(file, call)
            log(f"\n{'=' * 60}\n문제 {call['problem']}/{config['last_problem']} | 이번 실행 {state['problemIndex'] + 1}/{len(dataset['problems'])} | 라운드 {call['round']}/{config['rounds']} | 시도 {call['attempt']}/{config['T']}")
            start = time.perf_counter()
            heartbeat = _Heartbeat(log, start)
            try:
                http = send({'body': body, 'key': apiKey, 'signal': signal, 'provider': config['provider'],
                             'timeout': config.get('api_timeout_seconds') or None})
                call.update(api_seconds=time.perf_counter() - start, finished_at=_now(), http_status=http['status'],
                            request_id=http.get('request_id'), raw_http_body=http['text'])
                try:
                    is_stream = (config['provider'] == 'anthropic' and
                                 ('text/event-stream' in (http.get('content_type') or '')
                                  or http['text'].lstrip().startswith(('event:', 'data:'))))
                    if is_stream:
                        call['response_format'] = 'sse'
                        call['response'], call['stream_complete'] = parseClaudeStream(http['text'])
                    else:
                        call['response'] = json_loads(http['text'])
                except (ValueError, TypeError, KeyError, AttributeError, IndexError):
                    pass
                response = call.get('response')
                call['cost'] = priceUsage(response, pricing)
                if call.get('response_format') == 'sse' and not call.get('stream_complete'):
                    call['cost'] = {'estimated_cost_usd': None, 'cost_status': 'usage_unknown_incomplete_stream'}
                call['response_sha256'] = hash(response) if js_truthy(response) else None
                response_error = response.get('error') if isinstance(response, dict) else None
                if http['status'] < 200 or http['status'] >= 300 or js_truthy(response_error):
                    call['status'] = 'api_error'
                    message = response_error.get('message') if isinstance(response_error, dict) else None
                    call['error'] = redact(message if message is not None else f"HTTP {http['status']}", apiKey)
                elif not completeResponse(response, config['provider']) or call.get('stream_complete') is False:
                    call['status'] = 'incomplete'
                    status = (response.get('status') or response.get('stop_reason')) if isinstance(response, dict) else None
                    call['error'] = f"API 응답 상태: {status if status is not None else '알 수 없음'}. 게임 시도로 반영하지 않음"
                else:
                    call['status'] = 'completed'
            except (Exception, KeyboardInterrupt) as exc:
                call.update(api_seconds=time.perf_counter() - start, finished_at=_now(), status='transport_error',
                            error=redact('사용자가 요청을 중단했습니다' if isinstance(exc, KeyboardInterrupt) else str(exc), apiKey),
                            cost={'estimated_cost_usd': None, 'cost_status': 'usage_unknown_transport_error'})
            finally:
                heartbeat.close()
            _redactFailedReceipt(call, apiKey)
            # Store the full receipt before judging; replay commits it exactly once.
            writeJSON(file, call)
            if call['status'] != 'completed':
                exportReports(folder, state, calls, config)
                raise RuntimeError(call['error'])
            row = applyResponse(state, call['response'], call)
            summary = exportReports(folder, state, calls, config)
            log(f"모델 응답\n{row['raw_output']}\n판정결과\n{row['next_model_input']}")
            usage = normalizeUsage(call.get('response')) or {}
            log(f"입력 {usage.get('input_tokens', '?')} / 출력 {usage.get('output_tokens', '?')} 토큰 (추론 {(usage.get('output_tokens_details') or {}).get('reasoning_tokens', '?')} 포함)")
            cost = call['cost'].get('estimated_cost_usd')
            cost_text = f'{cost:.8f}' if cost is not None else '미확인'
            log(f"API {call['api_seconds']:.2f}초 | 이번 추정 비용 ${cost_text} | 누적 확인액 ${summary['known_cost_subtotal_usd']:.8f}")
            if row['round_ended']:
                why = f"연속 정답 {config['k']}회" if row['round_end_reason'] == 'k_consecutive_correct' else f"{config['T']}회 제한 도달"
                log(f'라운드 종료: {why} (모델에는 알리지 않음)')
            if row['skipped_rounds']:
                log(f"전체 정답 0회: 남은 라운드 {', '.join(map(str, row['skipped_rounds']))} 제외 (모델에는 알리지 않음)")
        log(f"\n{len(dataset['problems'])}문제 실험 완료. summary.json, rounds.json, problems.json, attempts.json 저장 완료")
        return {'state': state, 'calls': calls, 'summary': exportReports(folder, state, calls, config)}
    finally:
        release()


def dryRun(config=None, root=ROOT):
    config = normalizeConfig(config)
    dataset = loadDataset(root, config)
    state = createState(dataset, readPrompt(Path(root) / 'dataset/prompt.json'), config)
    body = buildRequest(state)
    return {'model':config['model'],'provider':config['provider'],'reasoning':config['reasoning_effort'],
            'T':config['T'],'k':config['k'],'problems':len(dataset['problems']), 'rounds':config['rounds'],
            'selection':dataset['selection'],'max_output_tokens':config['max_output_tokens'],
            'first_request_hash':hash(body),'api_calls':0}


def verifyResults(folder):
    """Read-only replay against every stored attempt and generated JSON report."""
    folder = Path(folder)
    loaded, calls = loadRun(folder), readCalls(folder)
    state = replay(loaded['dataset'], loaded['prompt'], calls, loaded['config'])
    saved = readJSON(folder / 'attempts.json')
    if js_dumps(saved) != js_dumps(state['attempts']):
        raise ValueError('저장된 시도 판정과 API 원문 재채점 결과가 다릅니다')
    for call in calls:
        request = readJSON(folder / call['request_file'])
        if hash(request) != call['request_sha256']:
            raise ValueError('저장된 API 요청 파일이 다릅니다')
        if call.get('status') == 'completed' and 'raw_http_body' in call:
            if call.get('response_format') == 'sse':
                parsed, complete = parseClaudeStream(call['raw_http_body'])
                if not complete:
                    raise ValueError('완료된 요청의 SSE 응답이 끝까지 수신되지 않았습니다')
            else:
                parsed = json_loads(call['raw_http_body'])
            if hash(parsed) != call['response_sha256']:
                raise ValueError('API 응답 원문과 응답 JSON이 다릅니다')
    count = 0
    with tempfile.TemporaryDirectory(prefix='mirror-verify-') as temp:
        summary = exportReports(temp, state, calls, loaded['config'])
        for file in Path(temp).rglob('*.json'):
            actual = readJSON(file)
            expected = readJSON(folder / file.relative_to(temp))
            if file.name == 'progress.json':
                actual.pop('updated_at', None)
                expected.pop('updated_at', None)
            if js_dumps(actual) != js_dumps(expected):
                raise ValueError('결과 집계 불일치: ' + str(file.relative_to(temp)))
            count += 1
    return {'status':'passed','api_calls':0,'requests_checked':len(calls),
            'attempts_checked':len(saved),'reports_checked':count,
            'mpe_percent':summary['per_percent'],'done':state['done']}


def _interactiveConfig():
    def value(label, default):
        return input(f'{label} [{default}]: ').strip() or str(default)
    model = value('모델 (sol / opus / sonnet)', 'sol')
    t = int(value('라운드당 최대 시도 T', 20))
    k = int(value('연속 정답 기준 k', 2))
    first = int(value('시작 문제', 1))
    last = int(value('마지막 문제', 100))
    effort = value('추론 설정 (low / medium / high / xhigh / max)', 'medium')
    return normalizeConfig({'model':model,'T':t,'k':k,'first_problem':first,
                            'last_problem':last,'reasoning_effort':effort})


def main(args=None):
    args = sys.argv[1:] if args is None else list(args)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', choices=list(MODELS), help='sol / opus / sonnet (default sol)')
    parser.add_argument('--T', '--attempts', type=int, dest='T', help='maximum judged attempts per round')
    parser.add_argument('--k', type=int, help='consecutive correct submissions to pass')
    parser.add_argument('--first', type=int, help='first problem, inclusive')
    parser.add_argument('--last', type=int, help='last problem, inclusive')
    parser.add_argument('--effort', choices=['low','medium','high','xhigh','max'])
    parser.add_argument('--max-output-tokens', type=int, help='total output limit including reasoning; Claude default 128000')
    parser.add_argument('--timeout', type=float, help='API timeout in seconds, 0 means unlimited')
    parser.add_argument('--resume', metavar='FOLDER', help='saved run path or latest')
    parser.add_argument('--inspect', action='store_true', help='show saved progress without API calls')
    parser.add_argument('--dry-run', action='store_true', help='validate data/options without API calls')
    parser.add_argument('--verify-results', metavar='FOLDER', help='replay and verify saved JSON without API calls')
    parser.add_argument('--rebuild', metavar='FOLDER', nargs='?', const='latest', help='rebuild saved JSON reports without API calls')
    parser.add_argument('--clipboard', action='store_true', help='read an API key explicitly from Windows clipboard')
    ns = parser.parse_args(args)
    setting_names = {'model':'model','T':'T','k':'k','first':'first_problem','last':'last_problem',
                     'effort':'reasoning_effort','max_output_tokens':'max_output_tokens',
                     'timeout':'api_timeout_seconds'}
    overrides = {dest:getattr(ns, name) for name,dest in setting_names.items() if getattr(ns,name) is not None}
    modes = sum(bool(x) for x in (ns.inspect, ns.dry_run, ns.verify_results, ns.rebuild))
    if modes > 1:
        parser.error('검사·미리보기·재생성 모드는 하나만 선택하세요')
    if (ns.resume or ns.verify_results or ns.rebuild) and overrides:
        parser.error('저장된 실험은 모델·T·k 등 기존 설정으로만 재개·검증합니다')
    if ns.verify_results:
        print(js_dumps(verifyResults(resolveResume(ns.verify_results)), indent=2))
        return 0
    if ns.rebuild:
        target = ns.resume if ns.rebuild == 'latest' and ns.resume else ns.rebuild
        print(js_dumps(rebuildReports(resolveResume(target)), indent=2))
        return 0
    if ns.inspect:
        print(js_dumps(inspectResume(resolveResume(ns.resume or 'latest')), indent=2))
        return 0
    if ns.resume and ns.dry_run:
        print(js_dumps(inspectResume(resolveResume(ns.resume)), indent=2))
        return 0
    config = _interactiveConfig() if not args else normalizeConfig(overrides)
    if ns.dry_run:
        print(js_dumps(dryRun(config), indent=2))
        return 0
    folder = resolveResume(ns.resume) if ns.resume else None
    if folder:
        loaded = loadRun(folder)
        config = normalizeConfig(loaded['config'])
        if inspectResume(folder)['done']:
            print('이미 완료된 실험입니다. API를 호출하지 않았습니다.')
            return 0
    else:
        dryRun(config)
    print(f"{config['model']} · {config['reasoning_effort']} · {config['first_problem']}~{config['last_problem']}번 · T={config['T']} · k={config['k']}")
    if config['max_output_tokens'] is not None:
        print(f"출력 상한(추론 포함): {config['max_output_tokens']} 토큰")
    print('저장된 기록에 이어서 유료 API 실험을 진행합니다' if folder else '새 유료 API 실험을 시작합니다')
    key = readApiKey(config['provider'], clipboard=ns.clipboard)
    folder = folder or createRun(ROOT, config)
    abort = AbortController()
    previous_handler = signals.getsignal(signals.SIGINT)
    stopped = False
    def stop(signum, frame):
        nonlocal stopped
        if stopped:
            signals.default_int_handler(signum, frame)
        stopped = True
        print('\n중단 요청. 현재 API 요청 기록을 저장합니다.')
        abort.abort()
    signals.signal(signals.SIGINT, stop)
    try:
        run(folder, {'apiKey':key, 'signal':abort.signal})
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        print(redact(str(exc), key) or '사용자가 중단했습니다', file=sys.stderr)
        print(f'이어서 실행: python "{Path(__file__).resolve()}" --resume "{folder}"', file=sys.stderr)
        return 1
    finally:
        signals.signal(signals.SIGINT, previous_handler)
        key = ''


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(redact(str(exc)), file=sys.stderr)
        raise SystemExit(1)

