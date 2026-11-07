import tempfile,unittest
from pathlib import Path
import render_publication as r
class RenderPublicationTests(unittest.TestCase):
    def test_exact_pin_no_old_layout_or_private_builder_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)/'layer';result=r.render('a'*40,output)
            self.assertEqual(set(result['files']),set(r.FILES.values()))
            for target in r.FILES.values():
                text=(output/target).read_text()
                for old in ('__ENGINEERING_POLICY_REVISION__','build_jni','lib/spinner','molly-build.json','secrets.'):
                    self.assertNotIn(old,text)
            self.assertIn('ref: '+'a'*40,(output/'.github/workflows/codeql.yml').read_text())
            self.assertIn('languages: actions',(output/'.github/workflows/codeql.yml').read_text())
            self.assertIn('NOT RUN',result['coverage'])
            self.assertIn('tools/screenshots',(output/'.github/workflows/publish-screenshots.yml').read_text())
    def test_mutable_pins_and_existing_outputs_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            for value in ('main','v1',True,'a'*39):
                with self.assertRaises(ValueError):r.render(value,Path(tmp)/'new')
            with self.assertRaises(ValueError):r.render('a'*40,Path(tmp))
