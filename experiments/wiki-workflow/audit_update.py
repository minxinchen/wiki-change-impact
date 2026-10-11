"""Mechanical promotion checks. Passing is necessary, not semantic approval."""
import argparse
import json
from pathlib import Path
import yaml
from run import sha, dump

def metadata(text):
    if not text.startswith('---\n'): return {}
    value=yaml.safe_load(text.split('---',2)[1])
    return value if isinstance(value,dict) else {}

def audit(before, after, cfg):
    issues=[]
    original={p.relative_to(before).as_posix():p.read_bytes() for p in before.rglob('*.md')}
    current={p.relative_to(after).as_posix():p.read_bytes() for p in after.rglob('*.md')}
    for path,data in original.items():
        if path.startswith('raw/') and current.get(path)!=data: issues.append({'path':path,'code':'raw_modified'})
    raw=cfg['new_source']
    if raw not in current or sha(current[raw])!=cfg['source_sha256']:issues.append({'path':raw,'code':'injected_source_mismatch'})
    changed=[p for p in original if current.get(p)!=original[p]]
    for path in changed:
        if not path.startswith('wiki/'):continue
        try:m=metadata(current.get(path,b'').decode())
        except (ValueError,yaml.YAMLError):m={}
        sources=m.get('sources',[])
        if not isinstance(sources,list) or raw not in sources:issues.append({'path':path,'code':'missing_new_source'})
        if str(m.get('updated',''))!=cfg['date']:issues.append({'path':path,'code':'stale_updated_date'})
    if 'log.md' not in changed:issues.append({'path':'log.md','code':'log_not_updated'})
    return {'status':'blocked' if issues else 'mechanical_pass_needs_semantic_review','issues':issues,'changed_paths':changed,'limitations':['Does not verify source entailment, terminology, routing semantics or completeness of affected pages.','Case-specific gate: checks only previously existing changed wiki pages; new pages are outside scope and deletion has no dedicated diagnostic.', 'Requiring every changed page to cite the injected source is specific to this update, not a universal policy.', 'Check operates on trusted local experiment paths, not a general sandbox.']}

if __name__=='__main__':
    p=argparse.ArgumentParser()
    for name in ['before','after','protocol','output']:p.add_argument(name,type=Path)
    a=p.parse_args()
    if a.output.exists():raise FileExistsError(a.output)
    result=audit(a.before,a.after,json.loads(a.protocol.read_text()));dump(a.output,result)
    print(json.dumps(result,ensure_ascii=False));raise SystemExit(2 if result['status']=='blocked' else 0)
