#!/usr/bin/env python3
"""Reproducible synthetic revision-switch experiment; never reads live wikis."""
import argparse
import json
from pathlib import Path
import shutil
import wiki_impact as w


def run(out):
    out=Path(out).resolve();out.mkdir(parents=True,exist_ok=False)
    shutil.copytree(Path(__file__).parent/'examples',out/'case')
    config=w.load_config(out/'case/config.json')
    baseline=w.snapshot(config);w.validate_snapshot(baseline)
    w.write_new(out/'baseline.json',json.dumps(baseline,indent=2),config)
    concept=out/'case/vault/wiki/concept.md'
    concept.write_text(concept.read_text().replace('spec-v1.txt','spec-v2.txt'))
    changed=w.snapshot(config);report=w.compare(baseline,changed)
    w.write_new(out/'report.json',json.dumps(report,ensure_ascii=False,indent=2),config)
    w.write_new(out/'report.html',w.render(report),config)
    affected={p['page'] for p in report['impacted_pages']}
    expected={'demo:wiki/concept.md','demo:wiki/product.md','demo:wiki/comparison.md'}
    if affected != expected or report['status'] != 'needs_review':
        raise ValueError('demo acceptance failed')
    # Scripted fixture correction illustrates that changed prose is not approval.
    for name in ['concept','product','comparison']:
        page=out/f'case/vault/wiki/{name}.md'
        page.write_text(page.read_text().replace('1200','1000'))
    corrected=w.compare(baseline,w.snapshot(config))
    w.write_new(out/'after-correction.json',json.dumps(corrected,ensure_ascii=False,indent=2),config)
    if corrected['status'] != 'needs_review': raise ValueError('correction must not auto-approve')
    return {'fixture':'synthetic; no LLM','affected_pages':sorted(affected),
            'unrelated_page_affected':False,'after_scripted_correction':corrected['status'],
            'report':str(out/'report.html')}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',required=True)
    try: print(json.dumps(run(p.parse_args().out),ensure_ascii=False,indent=2))
    except (ValueError,OSError) as exc: p.exit(2,str(exc)+'\n')
