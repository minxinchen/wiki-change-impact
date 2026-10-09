import copy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import wiki_impact as w


class ImpactTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()
        shutil.copytree(Path(__file__).resolve().parents[1]/'examples',self.root/'example')
        self.config_path=self.root/'example/config.json'
        self.c=w.load_config(self.config_path)
        self.v=self.root/'example/vault'
        self.before=w.snapshot(self.c)
        self.assertEqual(self.before['issues'],[])

    def test_unchanged(self):
        self.assertEqual(w.compare(self.before,w.snapshot(self.c))['status'],'unchanged')

    def test_revision_switch_propagates_only_source_dependencies(self):
        page=self.v/'wiki/concept.md'
        page.write_text(page.read_text().replace('spec-v1','spec-v2'))
        report=w.compare(self.before,w.snapshot(self.c))
        affected={x['page'] for x in report['impacted_pages']}
        self.assertEqual(affected,{'demo:wiki/concept.md','demo:wiki/product.md','demo:wiki/comparison.md'})
        comparison=next(x for x in report['impacted_pages'] if x['page'].endswith('comparison.md'))
        self.assertIn(['demo:raw/spec-v2.txt','demo:wiki/concept.md','demo:wiki/product.md','demo:wiki/comparison.md'],[x['path'] for x in comparison['reasons']])
        self.assertTrue((self.v/'raw/spec-v1.txt').exists())

    def test_switch_between_already_tracked_sources_includes_changed_page(self):
        extra=self.v/'wiki/both.md'
        extra.write_text('---\nsources: [raw/spec-v1.txt, raw/spec-v2.txt]\n---\nreference')
        before=w.snapshot(self.c)
        concept=self.v/'wiki/concept.md'
        concept.write_text(concept.read_text().replace('spec-v1','spec-v2'))
        report=w.compare(before,w.snapshot(self.c))
        self.assertEqual({x['page'] for x in report['impacted_pages']},
                         {'demo:wiki/concept.md','demo:wiki/product.md','demo:wiki/comparison.md'})
        item=next(x for x in report['impacted_pages'] if x['page']=='demo:wiki/concept.md')
        self.assertEqual(item['reasons'][0]['kind'],'dependency_changed')
        self.assertEqual(item['reasons'][0]['added_sources'],['demo:raw/spec-v2.txt'])

    def test_scan_error_blocks(self):
        def failing_walk(root, followlinks=False, onerror=None):
            onerror(PermissionError('unreadable directory'))
            return iter([])
        with patch('wiki_impact.os.walk', side_effect=failing_walk):
            after=w.snapshot(self.c)
        self.assertEqual(w.compare(self.before,after)['status'],'blocked')

    def test_source_modified(self):
        (self.v/'raw/spec-v1.txt').write_text('changed')
        r=w.compare(self.before,w.snapshot(self.c))
        self.assertEqual(len(r['impacted_pages']),3)
        self.assertEqual(r['changes'],[{'file':'demo:raw/spec-v1.txt','kind':'modified'}])

    def test_source_missing_blocks(self):
        (self.v/'raw/spec-v1.txt').unlink()
        r=w.compare(self.before,w.snapshot(self.c))
        self.assertEqual(r['status'],'blocked');self.assertEqual(len(r['impacted_pages']),3)

    def test_cycle_terminates(self):
        (self.v/'wiki/concept.md').write_text('---\nsources: [raw/spec-v1.txt, wiki/product.md]\n---\nconcept')
        before=w.snapshot(self.c)
        (self.v/'raw/spec-v1.txt').write_text('changed')
        self.assertEqual(len(w.compare(before,w.snapshot(self.c))['impacted_pages']),3)

    def test_cross_vault(self):
        other=self.root/'other';other.mkdir()
        (other/'source.txt').write_text('evidence')
        self.c['other']={'root':str(other),'pages':['*.md']}
        (self.v/'wiki/concept.md').write_text('---\nsources: [other:source.txt]\n---\nx')
        before=w.snapshot(self.c)
        (other/'source.txt').write_text('corrected')
        self.assertEqual(len(w.compare(before,w.snapshot(self.c))['impacted_pages']),3)

    def test_unknown_vault_and_unsafe_paths(self):
        for ref in ['unknown:a.md','../outside.txt','/tmp/outside.txt','raw/../spec.txt','raw\\bad.txt']:
            with self.subTest(ref=ref):
                with self.assertRaises(ValueError): w.reference('demo',ref,self.c)

    def test_symlink_not_read(self):
        target=self.root/'outside';target.write_text('private')
        (self.v/'raw/spec-v1.txt').unlink();(self.v/'raw/spec-v1.txt').symlink_to(target)
        s=w.snapshot(self.c)
        self.assertNotIn('demo:raw/spec-v1.txt',s['files'])
        self.assertTrue(s['issues'])

    def test_bad_yaml_and_duplicate_keys(self):
        for raw in ['---\nsources: [x]\nsources: [y]\n---\n','---\nsources: [\n---\n','---\nsources: hi\n---\n','no frontmatter']:
            with self.assertRaises((ValueError,w.yaml.YAMLError)): w.metadata(raw.encode())

    def test_changed_config_refused(self):
        other=copy.deepcopy(self.before);other['config_sha256']='a'*64
        with self.assertRaisesRegex(ValueError,'configuration changed'): w.compare(other,w.snapshot(self.c))

    def test_invalid_baseline_refused(self):
        for key,value in [('pages',{}),('issues',[{'page':'x'}]),('files',{}),('dependencies',{'x':[]})]:
            invalid=copy.deepcopy(self.before);invalid[key]=value
            with self.assertRaises(ValueError): w.compare(invalid,w.snapshot(self.c))

    def test_output_cannot_overwrite_or_enter_vault(self):
        with self.assertRaises(ValueError): w.write_new(self.v/'new.json','{}',self.c)
        dest=self.root/'report.json';w.write_new(dest,'first',self.c)
        with self.assertRaises(FileExistsError): w.write_new(dest,'second',self.c)
        self.assertEqual(dest.read_text(),'first')

    def test_output_protects_inputs(self):
        with self.assertRaises(ValueError): w.write_new(self.config_path,'{}',self.c,[self.config_path])

    def test_html_escapes_paths(self):
        report={'status':'needs_review','changes':[{'file':'<script>bad</script>','kind':'modified'}],'impacted_pages':[],'issues':[]}
        self.assertNotIn('<script>',w.render(report));self.assertIn('&lt;script&gt;',w.render(report))

    def test_snapshot_does_not_modify_vault(self):
        def content(): return {p.relative_to(self.v).as_posix():p.read_bytes() for p in self.v.rglob('*') if p.is_file()}
        before=content();w.snapshot(self.c);self.assertEqual(content(),before)

    def test_empty_scope_not_clean(self):
        self.c['demo']['pages']=['none/*.md']
        self.assertTrue(w.snapshot(self.c)['issues'])


if __name__=='__main__':unittest.main()
