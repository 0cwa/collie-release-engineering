import subprocess,tempfile,unittest
from pathlib import Path
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
    def test_missing_trusted_base_config_fails_before_codeql_init(self):
        with tempfile.TemporaryDirectory() as tmp:
            repository=Path(__file__).resolve().parents[2]
            revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=repository,text=True).strip()
            output=Path(tmp)/'layer';r.render(revision,output,repository)
            workflow=(output/'.github/workflows/codeql.yml').read_text()
            guard='''test -f trusted-policy/.github/codeql/codeql-config.yml
          test -s trusted-policy/.github/codeql/codeql-config.yml'''
            self.assertIn(guard,workflow)
            self.assertIn('config-file: ./trusted-policy/.github/codeql/codeql-config.yml',workflow)
            self.assertLess(workflow.index('Require trusted base CodeQL configuration'),
                            workflow.index('github/codeql-action/init@'))
            with tempfile.TemporaryDirectory() as checkout:
                missing=subprocess.run(['bash','-euo','pipefail','-c',guard],cwd=checkout,
                                       capture_output=True)
                self.assertNotEqual(missing.returncode,0)
                config=Path(checkout)/'trusted-policy/.github/codeql/codeql-config.yml'
                config.parent.mkdir(parents=True)
                config.write_text('name: trusted base policy\n')
                present=subprocess.run(['bash','-euo','pipefail','-c',guard],cwd=checkout,
                                       capture_output=True)
                self.assertEqual(present.returncode,0,present.stderr.decode())
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
    def test_trusted_review_checkout_includes_manifest_with_cone_sparse_checkout(self):
        repository_root=Path(__file__).resolve().parents[2]
        workflows=[repository_root/'tools/codeql/publication/codeql.yml.in']
        direct=repository_root/'.github/workflows/codeql.yml'
        if direct.is_file() and 'path: trusted-policy' in direct.read_text():
            workflows.insert(0,direct)
        for workflow in workflows:
            with self.subTest(workflow=workflow.name):
                lines=workflow.read_text().splitlines()
                position=next(i for i,line in enumerate(lines) if line.strip()=='path: trusted-policy')
                key,value=lines[position+1].strip().split(':',1)
                self.assertEqual(key,'sparse-checkout')
                sparse_paths=value.strip().split()
                self.assertIn('.github/codeql',sparse_paths)
                with tempfile.TemporaryDirectory() as tmp:
                    root=Path(tmp);source=root/'source';checkout=root/'checkout'
                    subprocess.run(['git','init','-q',str(source)],check=True)
                    files={
                        '.github/codeql/reviewed-findings.json':'{"schema":1,"reviews":[]}\n',
                        '.github/codeql/other-policy.json':'{}\n',
                        '.github/workflows/codeql.yml':'name: CodeQL\n',
                        'tools/codeql/report.py':'pass\n',
                    }
                    for relative,content in files.items():
                        path=source/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(content)
                    subprocess.run(['git','-C',str(source),'add','.'],check=True,capture_output=True)
                    subprocess.run(['git','-C',str(source),'-c','user.name=fixture','-c','user.email=fixture@example.invalid','commit','-m','base'],check=True,capture_output=True)
                    revision=subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()
                    subprocess.run(['git','clone','-q','--no-checkout',str(source),str(checkout)],check=True,capture_output=True)
                    subprocess.run(['git','-C',str(checkout),'sparse-checkout','init','--cone'],check=True,capture_output=True)
                    subprocess.run(['git','-C',str(checkout),'sparse-checkout','set',*sparse_paths],check=True,capture_output=True)
                    subprocess.run(['git','-C',str(checkout),'checkout','--force',revision],check=True,capture_output=True)
                    self.assertEqual((checkout/'.github/codeql/reviewed-findings.json').read_text(),files['.github/codeql/reviewed-findings.json'])
                    self.assertFalse((checkout/'.github/workflows/codeql.yml').exists())
                    self.assertFalse((checkout/'tools/codeql/report.py').exists())
    def test_mutable_pins_and_existing_outputs_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            for value in ('main','v1',True,'a'*39):
                with self.assertRaises(ValueError):r.render(value,Path(tmp)/'new')
            with self.assertRaises(ValueError):r.render('a'*40,Path(tmp))
