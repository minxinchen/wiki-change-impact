import tempfile
import unittest
import json
from unittest.mock import patch
from pathlib import Path
from run import run, sha, call
from update import validate
from audit_update import audit

class Guards(unittest.TestCase):
    def test_request_snapshot_does_not_mutate(self):
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self):return b'{"choices": []}'
        messages=[{'role':'user','content':'first'}]
        with patch('urllib.request.urlopen',return_value=Response()):
            body,_,_=call(messages,'mock',10)
        messages[0]['content']='changed'
        messages.append({'role':'assistant','content':'later'})
        self.assertEqual(body['messages'],[{'role':'user','content':'first'}])

    def test_body_update_without_provenance_is_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);a=root/'before';b=root/'after'
            for x in [a,b]:
                (x/'wiki').mkdir(parents=True);(x/'raw').mkdir()
                (x/'wiki/a.md').write_text('---\nsources: []\nupdated: 2026-01-01\n---\nold')
                (x/'log.md').write_text('old log')
            (b/'wiki/a.md').write_text('---\nsources: []\nupdated: 2026-01-01\n---\nnew fact')
            (b/'raw/new.md').write_text('source')
            result=audit(a,b,{'new_source':'raw/new.md','source_sha256':sha(b'source'),'date':'2026-10-11'})
            self.assertEqual(result['status'],'blocked')
            self.assertEqual({i['code'] for i in result['issues']},{'missing_new_source','stale_updated_date','log_not_updated'})

    def test_changed_corpus_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);corpus=root/'corpus';corpus.mkdir();(corpus/'x.md').write_text('changed')
            protocol={'temperature':0,'limits':{'search':2,'read':6,'lines_per_read':80,'tool_characters':18000,'steps':10},'manifest':{'x.md':sha(b'original')}}
            with self.assertRaisesRegex(ValueError,'manifest'):run(corpus,protocol,root/'out')
    def test_protected_or_unknown_path(self):
        for path in ['raw/x','../x','wiki/unknown.md']:
            with self.subTest(path=path),self.assertRaises(ValueError):validate({'wiki/a.md':'abcdef'},[{'path':path,'old':'a','new':'x'}],['wiki/a.md'])
    def test_overlapping_edits(self):
        with self.assertRaisesRegex(ValueError,'overlapping'):validate({'wiki/a.md':'abcdef'},[{'path':'wiki/a.md','old':'abc','new':'x'},{'path':'wiki/a.md','old':'bc','new':'y'}],['wiki/a.md'])
    def test_ambiguous_match(self):
        with self.assertRaisesRegex(ValueError,'unique'):validate({'wiki/a.md':'abcabc'},[{'path':'wiki/a.md','old':'abc','new':'x'}],['wiki/a.md'])
    def test_disjoint_offsets(self):
        actual=validate({'wiki/a.md':'abcdef'},[{'path':'wiki/a.md','old':'bc','new':'X'},{'path':'wiki/a.md','old':'ef','new':'Y'}],['wiki/a.md'])
        self.assertEqual(actual,{'wiki/a.md':'aXdY'})

if __name__=='__main__': unittest.main()
