import subprocess,tempfile,unittest
from pathlib import Path
from unittest import mock
import render_publication as r
class RenderPublicationTests(unittest.TestCase):
    def test_exact_pin_no_old_layout_or_private_builder_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            revision=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
            output=Path(tmp)/'layer';result=r.render(revision,output)
            self.assertEqual(set(result['files']),set(r.FILES.values()))
            for target in r.FILES.values():
                text=(output/target).read_text()
                for old in ('__ENGINEERING_POLICY_REVISION__','build_jni','lib/spinner','molly-build.json','secrets.'):
                    self.assertNotIn(old,text)
            self.assertIn('ref: '+revision,(output/'.github/workflows/codeql.yml').read_text())
            self.assertIn('languages: actions',(output/'.github/workflows/codeql.yml').read_text())
            self.assertIn('NOT RUN',result['coverage'])
            self.assertIn('tools/screenshots',(output/'.github/workflows/publish-screenshots.yml').read_text())
    def test_working_tree_templates_cannot_mislabel_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            repository=Path(tmp)/'repo';repository.mkdir()
            subprocess.run(['git','init',str(repository)],check=True,capture_output=True)
            for name in r.FILES:
                target=repository/'tools/codeql/publication'/name
                target.parent.mkdir(parents=True,exist_ok=True)
                target.write_bytes((Path(__file__).with_name('publication')/name).read_bytes())
            subprocess.run(['git','-C',str(repository),'add','.'],check=True,capture_output=True)
            subprocess.run(['git','-C',str(repository),'-c','user.name=test','-c','user.email=test@example.com','commit','-m','policy'],check=True,capture_output=True)
            revision=subprocess.check_output(['git','-C',str(repository),'rev-parse','HEAD'],text=True).strip()
            mutable=repository/'tools/codeql/publication/codeql.yml.in'
            mutable.write_text('tampered mutable template')
            output=Path(tmp)/'layer'
            r.render(revision,output,repository)
            rendered=(output/'.github/workflows/codeql.yml').read_text()
            self.assertIn('Published workflow CodeQL',rendered)
            self.assertNotIn('tampered mutable template',rendered)
    def test_mutable_pins_and_existing_outputs_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            for value in ('main','v1',True,'a'*39):
                with self.assertRaises(ValueError):r.render(value,Path(tmp)/'new')
            with self.assertRaises(ValueError):r.render('a'*40,Path(tmp))
