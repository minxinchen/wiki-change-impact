#!/usr/bin/env python3
"""Agent-authored development cases, not an independent accuracy benchmark.

Author: Nio (Master). Implementation: Codex, 2026-10-10.
"""
import argparse
import hashlib
import html
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import wiki_impact as w


def score(predicted, expected):
    predicted, expected = set(predicted), set(expected)
    tp, fp, fn = predicted & expected, predicted - expected, expected - predicted
    return {'listed':sorted(predicted), 'correct':sorted(tp), 'extra':sorted(fp),
            'missed':sorted(fn), 'tp':len(tp), 'fp':len(fp), 'fn':len(fn),
            'precision':len(tp)/len(predicted) if predicted else None,
            'recall':len(tp)/len(expected) if expected else None}


def write_fixture(root, files):
    for name, content in files.items():
        path = root/name
        if not path.resolve().is_relative_to(root) or Path(name).is_absolute():
            raise ValueError('fixture outside temporary root')
        if content is None:
            if path.exists(): path.unlink()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding='utf-8')


def evaluate(path=None):
    path = Path(path or Path(__file__).with_name('cases.json'))
    raw = path.read_bytes(); data = json.loads(raw)
    rows = []
    for case in data['cases']:
        before_files = {**data['base_files'], **case['before_overrides']}
        after_files = {**before_files, **case['after_updates']}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            write_fixture(root, before_files)
            config_path = root/'config.json'
            config_path.write_text(json.dumps({'vaults':case['vaults']}))
            config = w.load_config(config_path)
            before = w.snapshot(config); w.validate_snapshot(before)
            write_fixture(root, case['after_updates'])
            after = w.snapshot(config); report = w.compare(before, after)
            pages = after['pages']
            # Baseline: known file delta + one literal scan of before/after page text.
            # No parsing of sources, no recursive following of references.
            direct = set()
            changed = {name for name in set(before_files)|set(after_files)
                       if before_files.get(name) != after_files.get(name)}
            for identity in pages:
                vault, rel = identity.split(':',1)
                name = str(Path(case['vaults'][vault]['root'])/rel)
                text = (before_files.get(name) or '')+'\n'+(after_files.get(name) or '')
                for filename in changed:
                    for source_vault, spec in case['vaults'].items():
                        try: source_rel = Path(filename).relative_to(spec['root']).as_posix()
                        except ValueError: continue
                        needle = source_rel if source_vault == vault else source_vault+':'+source_rel
                        if name == filename or needle in text: direct.add(identity)
            expected = case['expected_review_pages']
            if not set(expected) <= set(pages): raise ValueError('label outside selected pages')
            methods = {'literal_scan':score(direct,expected),
                       'review_all':score(pages,expected),
                       'dependency_tool':score([x['page'] for x in report['impacted_pages']],expected)}
            # Exclude temporary-root-dependent snapshot/config identifiers from this
            # portable report. All actual file hashes and dependency paths are retained.
            snapshots = {label:{k:v for k,v in snap.items() if k != 'config_sha256'}
                         for label,snap in [('before',before),('after',after)]}
            portable = {k:v for k,v in report.items()
                        if k not in ('baseline_sha256','current_sha256')}
            rows.append({'id':case['id'], 'title':case['title'], 'rationale':case['rationale'],
                         'expected_review_pages':expected, 'methods':methods,
                         'status_contract_matches':report['status']==case['expected_status'],
                         'report':portable,'snapshots':snapshots})
    totals = {}
    for method in ('literal_scan','review_all','dependency_tool'):
        counts = {k:sum(r['methods'][method][k] for r in rows) for k in ('tp','fp','fn')}
        counts['listed'] = counts['tp']+counts['fp']
        counts['precision'] = counts['tp']/counts['listed'] if counts['listed'] else None
        counts['recall'] = counts['tp']/(counts['tp']+counts['fn']) if counts['tp']+counts['fn'] else None
        totals[method] = counts
    return {'schema_version':1,'provenance':data['provenance'],
            'cases_sha256':hashlib.sha256(raw).hexdigest(),
            'implementation_sha256':hashlib.sha256(Path(w.__file__).read_bytes()).hexdigest(),
            'runner_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'interpretation':'Descriptive counts on 10 agent-authored development cases, not population accuracy or time saved. Known misses/extras are retained; status correctness and semantic usefulness are separate.',
            'all_status_contracts_match':all(r['status_contract_matches'] for r in rows),
            'totals':totals,'cases':rows}


def render(result):
    esc = html.escape
    parts = ['<!doctype html><html lang="zh-Hant"><meta charset="utf-8">'
             '<meta name="viewport" content="width=device-width,initial-scale=1">'
             '<title>Wiki 影響分析評估</title><style>body{font:17px/1.7 system-ui;'
             'max-width:1000px;margin:35px auto;padding:0 20px;color:#183047;background:#f3f6f8}'
             'section{background:white;padding:22px;margin:18px 0;border:1px solid #d2dde6;border-radius:12px}'
             'table{border-collapse:collapse;width:100%}td,th{padding:10px;border-bottom:1px solid #ddd;text-align:left}'
             'pre{white-space:pre-wrap;overflow-wrap:anywhere}summary{cursor:pointer}</style>'
             '<h1>Wiki 來源變更：找得到什麼、漏掉什麼？</h1>'
             '<p>新製合成開發集，預期集合由 Codex 依正文先行編寫；不是人類 gold standard、歷史業績或獨立泛化測試。'
             '未量測人工耗時，列出頁數也不等於節省時間。</p>']
    names={'literal_scan':'直接文字搜尋（單層）','review_all':'全部核對','dependency_tool':'依賴工具'}
    parts.append('<section><h2>跨案例頁面判定累計</h2><table><tr><th>方法</th><th>正確列出</th><th>多列</th><th>漏列</th></tr>')
    for method, counts in result['totals'].items():
        parts.append('<tr><td>'+names[method]+'</td>'+''.join('<td>'+str(counts[k])+'</td>' for k in ('tp','fp','fn'))+'</tr>')
    parts.append('</table><p>相同頁面在不同變更案例中分別計數。不是這組開發案例之外的準確率。</p></section>')
    for row in result['cases']:
        parts.append('<section><h2>'+esc(row['title'])+'</h2><p>'+esc(row['rationale'])+'</p>'
                     '<p>工具狀態：'+esc(row['report']['status'])+'；預期應核對：'+esc(', '.join(row['expected_review_pages']) or '無')+'</p>')
        for method,m in row['methods'].items():
            parts.append('<p><strong>'+names[method]+'</strong>：列出 '+str(len(m['listed']))+'；多列 '+str(m['fp'])+'；漏列 '+str(m['fn'])+'</p>')
        parts.append('<details><summary>檢查路徑、版本與完整判定</summary><pre>'+esc(json.dumps(row,ensure_ascii=False,indent=2))+'</pre></details></section>')
    return ''.join(parts)+'</html>'


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',required=True)
    args=parser.parse_args()
    destination=Path(args.out).resolve()
    destination.mkdir(parents=True,exist_ok=False)
    result=evaluate()
    (destination/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    (destination/'report.html').write_text(render(result))
    print(json.dumps({'status_contracts_match':result['all_status_contracts_match'],'totals':result['totals']},ensure_ascii=False,indent=2))
    sys.exit(0 if result['all_status_contracts_match'] else 1)
