from html.parser import HTMLParser
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import patch
from urllib.parse import unquote, urlsplit

from tools.query_analyzer.app.build import build_site
from tools.query_analyzer.app.cli import main
from tools.query_analyzer.core.common import atomic_write
from tools.query_analyzer.core.sources import read_timings
from tools.query_analyzer.tests.helpers import PLAN


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.targets = []
        self.ids = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'id' in attrs:
            self.ids.add(attrs['id'])
        for attr in ('href', 'src'):
            if attr in attrs:
                self.targets.append(attrs[attr])


def assert_site_links(test, folder):
    cache = {}
    def parse(path):
        if path not in cache:
            cache[path] = Links()
            cache[path].feed(path.read_text(encoding='utf-8'))
        return cache[path]
    for page in folder.glob('*.html'):
        parsed = parse(page)
        for target in parsed.targets:
            url = urlsplit(target)
            if url.scheme or url.netloc:
                continue
            file = folder / unquote(url.path) if url.path else page
            test.assertTrue(file.is_file(), f'{page.name}: missing {target}')
            if url.fragment:
                destination = parse(file)
                test.assertIn(unquote(url.fragment), destination.ids, f'{page.name}: missing anchor {target}')


class BuildTests(unittest.TestCase):
    def setup_inputs(self, root):
        (root / 'plans').mkdir()
        (root / 'plans/query_001.xml').write_text(PLAN)
        (root / 'timings.csv').write_text('query_id,average_ms\nquery_001,123\n')
        config = root / 'config.json'
        config.write_text(json.dumps({scale: {'plans': 'plans' if scale in ('1GB','2GB') else scale,
                                              'summary': 'timings.csv'} for scale in ('1GB','2GB','5GB','10GB')}))
        return config

    def test_complete_site_pending_states_and_all_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = self.setup_inputs(root)
            site = root / 'site'
            sources, errors = build_site(config, site)
            self.assertFalse(errors)
            self.assertEqual(len(sources['1GB']['plans']), 1)
            self.assertIn('No execution plans have been contributed', (site/'report_5GB.html').read_text())
            self.assertIn('No execution plans have been contributed', (site/'report_10GB.html').read_text())
            self.assertTrue((site/'assets/dashboard.js').exists())
            self.assertTrue((site/'assets/comparison.css').exists())
            assert_site_links(self, site)

    def test_contributing_and_removing_5gb_plans_refreshes_pending_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = self.setup_inputs(root)
            (root/'5GB').mkdir()
            plan = root/'5GB/query_001.sqlplan'
            plan.write_text(PLAN)
            site = root/'site'
            sources, errors = build_site(config,site)
            self.assertFalse(errors)
            self.assertEqual(len(sources['5GB']['plans']),1)
            self.assertIn('detail-query_001',(site/'report_5GB.html').read_text())
            assert_site_links(self,site)
            plan.unlink()
            build_site(config,site)
            self.assertEqual(json.loads((site/'analysis_5GB.json').read_text())['queries'],[])
            self.assertIn('No execution plans have been contributed',(site/'report_5GB.html').read_text())

    def test_invalid_timings_are_not_silently_ranked(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'timings.csv'
            path.write_text('query_id,average_ms\na,NaN\nb,inf\nc,-1\nd,10\nd,20\n')
            timings, errors=read_timings(path)
            self.assertEqual(timings,{'d':10})
            self.assertEqual(len(errors),4)

    def test_malformed_plan_returns_nonzero_and_visible_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            config=self.setup_inputs(root)
            (root/'plans/query_002.xml').write_text('not XML')
            self.assertEqual(main(['build','--config',str(config),'--output',str(root/'site')]),1)
            self.assertIn('query_002.xml',(root/'site/report.html').read_text())

    def test_atomic_write_preserves_old_page_on_replace_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'page.html'
            path.write_text('old valid page')
            with patch('tools.query_analyzer.core.common.os.replace', side_effect=OSError('replace failed')):
                with self.assertRaises(OSError):
                    atomic_write(path,'new page')
            self.assertEqual(path.read_text(),'old valid page')
            self.assertEqual(list(Path(tmp).glob('*.tmp')),[])

    def test_duplicate_plan_stems_fail_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            config=self.setup_inputs(root)
            (root/'plans/query_001.sqlplan').write_text(PLAN)
            sources,errors=build_site(config,root/'site')
            self.assertTrue(errors)
            self.assertEqual(len(sources['1GB']['plans']),1)

    def test_zip_and_loose_plans_produce_the_same_analysis(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            config=self.setup_inputs(root)
            loose,_=build_site(config,root/'loose-site')
            with zipfile.ZipFile(root/'plans.zip','w',compression=zipfile.ZIP_DEFLATED) as archive:
                archive.write(root/'plans/query_001.xml','query_001.xml')
            values=json.loads(config.read_text())
            values['1GB']['plans']='plans.zip'
            config.write_text(json.dumps(values))
            packed,errors=build_site(config,root/'zip-site')
            self.assertFalse(errors)
            a=loose['1GB']['plans']['query_001']; b=packed['1GB']['plans']['query_001']
            for key in ('statements','estimated_cost','shape','sql','trees'):
                self.assertEqual(a[key],b[key])
            self.assertFalse((root/'zip-site/query_001.xml').exists())
            assert_site_links(self,root/'zip-site')

    def test_pack_is_repeatable_and_preserves_original_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            config=self.setup_inputs(root)
            args=['pack','5GB','--input',str(root/'plans'),'--config',str(config)]
            self.assertEqual(main(args),0)
            archive_path=root/'datasets/5GB/plans.zip'
            before=archive_path.read_bytes()
            with zipfile.ZipFile(archive_path) as archive:
                self.assertEqual(archive.read('query_001.xml'),(root/'plans/query_001.xml').read_bytes())
            self.assertEqual(main(args),0)
            self.assertEqual(archive_path.read_bytes(),before)
            self.assertEqual(json.loads(config.read_text())['5GB']['plans'],'datasets/5GB/plans.zip')

    def test_nested_zip_member_is_rejected_without_extraction(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            config=self.setup_inputs(root)
            with zipfile.ZipFile(root/'unsafe.zip','w') as archive:
                archive.writestr('../outside.xml',PLAN)
            values=json.loads(config.read_text()); values['1GB']['plans']='unsafe.zip'
            config.write_text(json.dumps(values))
            sources,errors=build_site(config,root/'site')
            self.assertTrue(errors)
            self.assertFalse(sources['1GB']['plans'])
            self.assertFalse((root/'outside.xml').exists())


if __name__=='__main__':
    unittest.main()
