#!/usr/bin/env python3
"""Read-only source dependency snapshots and wiki change-impact reports."""
import argparse
from collections import deque
import fnmatch
import hashlib
import html
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
import yaml


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(data):
    return json.dumps(data, ensure_ascii=False, sort_keys=True).encode()


def load_config(path):
    path = Path(path).resolve()
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or not isinstance(data.get('vaults'), dict) or not data['vaults']:
        raise ValueError('config requires nonempty vaults object')
    result = {}
    for key, value in data['vaults'].items():
        if not re.fullmatch(r'[a-z][a-z0-9-]*', key) or not isinstance(value, dict):
            raise ValueError('invalid vault key or value')
        if not isinstance(value.get('root'),str) or not value['root']:
            raise ValueError('vault root must be a path')
        root = (path.parent / Path(value['root']).expanduser()).resolve()
        if not root.is_dir():
            raise ValueError('vault root missing: '+key)
        patterns = value.get('pages')
        if not isinstance(patterns,list) or not patterns or not all(isinstance(x,str) and x and not x.startswith('/') and '..' not in x.split('/') for x in patterns):
            raise ValueError('pages must be nonempty relative glob patterns')
        result[key] = {'root':str(root), 'pages':sorted(set(patterns))}
    return result


def reference(vault, ref, config):
    if not isinstance(ref,str) or not ref:
        raise ValueError('source reference must be a nonempty string')
    v, path = ref.split(':',1) if ':' in ref else (vault,ref)
    p = PurePosixPath(path)
    if (v not in config or p.is_absolute() or '..' in p.parts or '\\' in path
            or not path or path != p.as_posix() or '#' in path or ':' in path):
        raise ValueError('source must be a registered vault-relative file: '+ref)
    return v+':'+path


def read_file(config, identity):
    v, rel = identity.split(':',1)
    root = Path(config[v]['root']); path = root/rel
    # Reject links, including directory links. Never read outside registered roots.
    for p in [path,*path.parents]:
        if p == root: break
        if p.is_symlink(): raise ValueError('symlink source is unsupported: '+identity)
    if not path.resolve().is_relative_to(root): raise ValueError('source escaped vault')
    if not path.is_file(): raise ValueError('file missing: '+identity)
    return path.read_bytes()


def metadata(raw):
    text = raw.decode('utf-8-sig')
    lines = text.splitlines()
    if not lines or lines[0] != '---': raise ValueError('missing YAML frontmatter')
    try: end = lines.index('---',1)
    except ValueError: raise ValueError('unterminated YAML frontmatter')
    # Duplicate keys make provenance ambiguous; do not silently select the last.
    class UniqueLoader(yaml.SafeLoader): pass
    def mapping(loader,node,deep=False):
        out = {}
        for key, value in node.value:
            k = loader.construct_object(key,deep=deep)
            if not isinstance(k,str) or k in out: raise ValueError('duplicate or nonstring YAML key')
            out[k] = loader.construct_object(value,deep=deep)
        return out
    UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,mapping)
    fm = yaml.load('\n'.join(lines[1:end]), Loader=UniqueLoader)
    if not isinstance(fm,dict): raise ValueError('frontmatter must be an object')
    sources = fm.get('sources')
    if not isinstance(sources,list) or not sources or not all(isinstance(s,str) and s for s in sources):
        raise ValueError('sources must be a nonempty string list')
    return sources


def snapshot(config):
    files, pages, dependencies, issues = {}, [], {}, []
    for v, spec in sorted(config.items()):
        root = Path(spec['root'])
        def walk_error(exc):
            issues.append({'page':v+':*','message':'directory scan failed: '+str(exc)})
        for parent, dirs, names in os.walk(root,followlinks=False,onerror=walk_error):
            dirs[:] = sorted(d for d in dirs if not d.startswith('.') and not (Path(parent)/d).is_symlink())
            for name in sorted(names):
                rel = (Path(parent)/name).relative_to(root).as_posix()
                if not any(fnmatch.fnmatchcase(rel,p) for p in spec['pages']): continue
                identity = v+':'+rel; pages.append(identity); dependencies[identity] = []
                try:
                    raw = read_file(config,identity); files[identity] = digest(raw)
                    refs = metadata(raw)
                    dependencies[identity] = sorted(set(reference(v,r,config) for r in refs))
                except (ValueError,UnicodeError,yaml.YAMLError,OSError) as exc:
                    issues.append({'page':identity,'message':str(exc)})
    if not pages: issues.append({'page':'*','message':'no pages matched'})
    for refs in dependencies.values():
        for target in refs:
            if target in files: continue
            try: files[target] = digest(read_file(config,target))
            except (ValueError,OSError) as exc:
                issues.append({'page':target,'message':str(exc)})
    return {'schema_version':1,'config_sha256':digest(canonical(config)),
            'files':dict(sorted(files.items())), 'pages':sorted(pages),
            'dependencies':dict(sorted(dependencies.items())),
            'issues':sorted(issues,key=lambda x:(x['page'],x['message']))}


def validate_snapshot(s):
    if not isinstance(s,dict) or s.get('schema_version') != 1:
        raise ValueError('unsupported snapshot schema')
    if not isinstance(s.get('files'),dict) or not isinstance(s.get('dependencies'),dict):
        raise ValueError('invalid snapshot maps')
    if not isinstance(s.get('pages'),list) or not all(isinstance(x,str) for x in s['pages']):
        raise ValueError('invalid snapshot pages')
    if len(s['pages']) != len(set(s['pages'])) or set(s['dependencies']) != set(s['pages']):
        raise ValueError('snapshot page/dependency mismatch')
    if not isinstance(s.get('issues'),list) or s['issues']:
        raise ValueError('baseline has unresolved structural issues')
    if not isinstance(s.get('config_sha256'),str) or not re.fullmatch('[0-9a-f]{64}',s['config_sha256']):
        raise ValueError('invalid config fingerprint')
    for k,v in s['files'].items():
        if not isinstance(k,str) or not isinstance(v,str) or not re.fullmatch('[0-9a-f]{64}',v):
            raise ValueError('invalid file fingerprint')
    if not set(s['pages']) <= set(s['files']): raise ValueError('baseline missing page fingerprint')
    for refs in s['dependencies'].values():
        if not isinstance(refs,list) or not all(isinstance(r,str) and r in s['files'] for r in refs):
            raise ValueError('invalid baseline dependency')


def compare(before, after):
    validate_snapshot(before)
    if before['config_sha256'] != after['config_sha256']:
        raise ValueError('configuration changed; compare with the same vault roots and page patterns')
    changes = []
    for key in sorted(set(before['files']) | set(after['files'])):
        old,new = before['files'].get(key),after['files'].get(key)
        if old != new:
            changes.append({'file':key,'kind':'newly_tracked' if old is None else 'no_longer_tracked' if new is None else 'modified'})
    # Union preserves impact when a dependency is removed by the same update.
    reverse = {}
    for graph in [before['dependencies'],after['dependencies']]:
        for page, refs in graph.items():
            for target in refs: reverse.setdefault(target,set()).add(page)
    impacted = {}
    for page in after['pages']:
        old = set(before['dependencies'].get(page, []))
        new = set(after['dependencies'].get(page, []))
        if old != new:
            impacted[page] = [{'kind':'dependency_changed', 'changed_source':page,
                              'path':[page], 'added_sources':sorted(new-old),
                              'removed_sources':sorted(old-new)}]
    for change in changes:
        seed = change['file']; queue = deque([(seed,[seed])]); seen = {seed}
        while queue:
            node, chain = queue.popleft()
            for dependent in sorted(reverse.get(node,[])):
                if dependent in seen: continue
                seen.add(dependent); route = chain+[dependent]
                if dependent in after['pages']:
                    impacted.setdefault(dependent,[]).append({'kind':'source_changed','changed_source':seed,'path':route})
                queue.append((dependent,route))
    return {'schema_version':1,'baseline_sha256':digest(canonical(before)),
            'current_sha256':digest(canonical(after)),
            'status':'blocked' if after['issues'] else 'needs_review' if changes else 'unchanged',
            'changes':changes,'impacted_pages':[{'page':p,'reasons':impacted[p]} for p in sorted(impacted)],
            'issues':after['issues'], 'meaning':'dependency impact, not a claim that page content is wrong'}


def render(report):
    esc = lambda x: html.escape(str(x))
    rows = ''.join('<tr><td>'+esc(c['file'])+'</td><td>'+esc(c['kind'])+'</td></tr>' for c in report['changes'])
    def reason_label(r):
        if r.get('kind') == 'dependency_changed':
            return ('引用集合變更：'+r['changed_source']+'；新增：'+', '.join(r['added_sources'])
                    +'；移除：'+', '.join(r['removed_sources']))
        return ' → '.join(r['path'])
    impacts = ''.join('<section><h3>'+esc(p['page'])+'</h3>'+''.join('<p>'+esc(reason_label(r))+'</p>' for r in p['reasons'])+'</section>' for p in report['impacted_pages'])
    issues = ''.join('<li>'+esc(i['page']+': '+i['message'])+'</li>' for i in report['issues'])
    return '''<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Wiki 來源變更影響</title><style>body{font:17px/1.7 system-ui;max-width:1000px;margin:40px auto;padding:0 24px;background:#f4f6f8;color:#172838}h1{line-height:1.3}section,table{background:white;border:1px solid #ccd6de;padding:16px;border-radius:8px;width:100%;box-sizing:border-box;margin:16px 0}td{padding:8px;overflow-wrap:anywhere}p{overflow-wrap:anywhere}.status{font-weight:bold;color:#145676}</style><h1>Wiki 來源變更影響</h1><p>來源版本 → 依賴關係 → 待核對頁面。此報告只讀檔案，不修改 Wiki；依賴變動不等於內容錯誤。</p><p class="status">'''+esc(report['status'])+'</p><h2>檔案變動</h2><table><tr><th>檔案</th><th>變動</th></tr>'+rows+'</table><h2>受影響頁面與依賴路徑</h2>'+ (impacts or '<p>未找到受影響頁面。</p>')+'<h2>結構問題</h2><ul>'+issues+'</ul></html>'


def write_new(path, data, config, protected=()):
    path = Path(path).resolve()
    if any(path.is_relative_to(Path(v['root'])) for v in config.values()) or path in [Path(p).resolve() for p in protected]:
        raise ValueError('output must be outside vaults and input files')
    # Exclusive create also refuses overwriting a previous baseline or report.
    with path.open('x',encoding='utf-8') as f: f.write(data)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',required=True)
    sub=p.add_subparsers(dest='action',required=True)
    s=sub.add_parser('snapshot');s.add_argument('--out',required=True)
    c=sub.add_parser('check');c.add_argument('--baseline',required=True);c.add_argument('--html')
    a=p.parse_args()
    try:
        config=load_config(a.config);current=snapshot(config)
        if a.action=='snapshot':
            if current['issues']:
                print(json.dumps(current,ensure_ascii=False,indent=2));return 2
            write_new(a.out,json.dumps(current,ensure_ascii=False,indent=2)+'\n',config,[a.config])
            print(json.dumps({'status':'snapshot_saved','files':len(current['files']),'pages':len(current['pages'])}));return 0
        report=compare(json.loads(Path(a.baseline).read_text()),current)
        if a.html: write_new(a.html,render(report),config,[a.config,a.baseline])
        print(json.dumps(report,ensure_ascii=False,indent=2))
        return {'unchanged':0,'needs_review':1,'blocked':2}[report['status']]
    except (ValueError,OSError,yaml.YAMLError) as exc:
        print(str(exc),file=sys.stderr);return 2


if __name__=='__main__': sys.exit(main())
