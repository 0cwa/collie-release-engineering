import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
sys.path.insert(0,str(Path(__file__).parents[1]/'source'))
import publish
import test_publish as fixtures
spec=importlib.util.spec_from_file_location('publication_layer',Path(__file__).parents[1]/'source/layers/publication.py')
layer=importlib.util.module_from_spec(spec);spec.loader.exec_module(layer)
ROOT=Path(__file__).parents[1]


class UserContentPublicationTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.PublicationTests('runTest');self.f.setUp();self.addCleanup(self.f.doCleanups);self.repo=self.f.repo
        for name,content in [('README.md',b'Welcome user. We use a patch-based release system.\n<img src="assets/banner" height="60">\n[LEGAL.md](LEGAL.md)\n'),('LICENSE',b'fixture license\n'),('NOTICE',b'fixture attribution\n'),('assets/banner',b'old matrix artwork\n'),('assets/executable',b'fixture asset\n')]:
            path=self.repo/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(content)
        (self.repo/'assets/executable').chmod(0o755);(self.repo/'assets/link').symlink_to('banner')
        publish.git(self.repo,'add','README.md','LICENSE','NOTICE','assets')
        first=publish.git(self.repo,'write-tree').decode().strip();self.original=publish.commit(self.repo,first,[self.f.old],'Fixture f6-style approved content\n')
        self.readme=(self.repo/'README.md').read_bytes().replace(b'height="60"',b'height="50"');(self.repo/'README.md').write_bytes(self.readme);(self.repo/'assets/banner').write_bytes(b'current approved matrix artwork\n')
        publish.git(self.repo,'add','README.md','assets');current=publish.git(self.repo,'write-tree').decode().strip();self.current=publish.commit(self.repo,current,[self.original],'Fixture 62-style owner update\n')
        self.app=self.repo/'published';self.app.mkdir()
    def test_explicit_current_base_keeps_readme_bytes_matrix_and_asset_modes(self):
        (self.app/'assets').mkdir();(self.app/'assets/unreviewed-leftover').write_text('must not survive')
        layer.preserve_user_files(self.app,self.repo,self.current,{'old_main':self.current})
        self.assertEqual((self.app/'README.md').read_bytes(),self.readme)
        self.assertEqual((self.app/'assets/banner').read_bytes(),b'current approved matrix artwork\n')
        self.assertTrue((self.app/'assets/executable').stat().st_mode&0o100);self.assertEqual(os.readlink(self.app/'assets/link'),'banner')
        self.assertFalse((self.app/'assets/unreviewed-leftover').exists())
        self.assertNotIn(b'Published application source is inspected here',(self.app/'README.md').read_bytes())
    def test_old_base_is_not_silently_substituted_for_the_current_approved_base(self):
        with self.assertRaisesRegex(ValueError,'plan old_main'):
            layer.preserve_user_files(self.app,self.repo,self.original,{'old_main':self.current})
        self.assertFalse((self.app/'README.md').exists())
        with self.assertRaisesRegex(ValueError,'immutable'):
            layer.preserve_user_files(self.app,self.repo,'HEAD',{'old_main':'HEAD'})
    def test_publisher_rejects_stale_readme_assets_or_modes_even_with_reviewed_full_tree(self):
        with self.assertRaisesRegex(ValueError,'README.md'):publish.verify_user_files(self.repo,self.original,self.current)
        publish.verify_user_files(self.repo,self.current,self.current)
        plan={'schema':1,'signal':self.f.signal,'old_main':self.current,'reconstructed_tree':publish.tree(self.repo,self.original),'final_tree':publish.tree(self.repo,self.original),'layers':[{'name':'Fixture source layer','kind':'feature','before_tree':self.f.empty,'after_tree':publish.tree(self.repo,self.original),'provenance':'fixture'}]}
        path=self.repo/'stale-plan.json';path.write_text(json.dumps(plan))
        with self.assertRaisesRegex(ValueError,'user-facing content'):
            publish.publish(self.repo,path,hashlib.sha256(path.read_bytes()).hexdigest(),self.current,historical_messages=True)
    def test_nested_repository_path_does_not_hide_root_owner_file_changes(self):
        nested=self.repo/'nested/worktree';nested.mkdir(parents=True)
        with self.assertRaisesRegex(ValueError,'README.md'):
            publish.verify_user_files(nested,self.original,self.current)
        publish.verify_user_files(nested,self.current,self.current)
    def test_existing_plan_base_and_current_main_are_always_bound(self):
        plan={'schema':1,'signal':self.f.signal,'old_main':self.original,'reconstructed_tree':publish.tree(self.repo,self.original),'final_tree':publish.tree(self.repo,self.original),'layers':[{'name':'Fixture source layer','kind':'feature','before_tree':self.f.empty,'after_tree':publish.tree(self.repo,self.original),'provenance':'fixture'}]}
        path=self.repo/'old-approved-plan.json';path.write_text(json.dumps(plan))
        with self.assertRaisesRegex(ValueError,'current base differs'):
            publish.publish(self.repo,path,hashlib.sha256(path.read_bytes()).hexdigest(),self.current,historical_messages=True)
    def test_asset_content_and_mode_substitution_fail_with_identical_readme(self):
        for mode,contents in [('100644',b'changed approved asset'),('100644',b'fixture asset\n')]:
            with self.subTest(mode=mode,contents=contents):
                publish.git(self.repo,'read-tree',self.current)
                blob=publish.git(self.repo,'hash-object','-w','--stdin',data=contents).decode().strip()
                publish.git(self.repo,'update-index','--cacheinfo',mode,blob,'assets/executable')
                changed=publish.git(self.repo,'write-tree').decode().strip()
                with self.assertRaisesRegex(ValueError,'assets'):publish.verify_user_files(self.repo,changed,self.current)

    def test_publisher_accepts_only_the_documented_asset_and_readme_relayout(self):
        publish.git(self.repo,'read-tree',self.current)
        raw=publish.git(self.repo,'ls-tree','-r','-z','--full-tree',self.current,'--','assets')
        records=[record for record in raw.split(b'\0') if record]
        removals=[]
        for record in records:
            _,name=record.split(b'\t',1);removals.append(name)
        publish.git(self.repo,'update-index','--force-remove','-z','--stdin',data=b'\0'.join(removals)+b'\0')
        for record in records:
            metadata,name=record.split(b'\t',1);mode,kind,blob=metadata.decode().split()
            relative=name.removeprefix(b'assets/')
            publish.git(self.repo,'update-index','--add','--cacheinfo',f'{mode},{blob},docs/assets/{relative.decode()}')
        readme=publish._relayout_readme(self.readme)
        blob=publish.git(self.repo,'hash-object','-w','--stdin',data=readme).decode().strip()
        publish.git(self.repo,'update-index','--cacheinfo','100644',blob,'README.md')
        candidate_tree=publish.git(self.repo,'write-tree').decode().strip()
        candidate=publish.commit(self.repo,candidate_tree,[self.current],'Fixture root-layout publication\n')
        publish.verify_user_files(self.repo,candidate,self.current)

        publish.git(self.repo,'read-tree',candidate)
        changed=publish.git(self.repo,'hash-object','-w','--stdin',data=b'altered approved asset\n').decode().strip()
        publish.git(self.repo,'update-index','--cacheinfo','100644',changed,'docs/assets/banner')
        altered=publish.git(self.repo,'write-tree').decode().strip()
        altered=publish.commit(self.repo,altered,[candidate],'Fixture altered moved asset\n')
        with self.assertRaisesRegex(ValueError,'assets'):
            publish.verify_user_files(self.repo,altered,self.current)

    def test_readme_asset_relayout_preserves_external_urls_and_unrelated_strings(self):
        readme=('![local](assets/logo.svg)\n'
                '[root](/assets/logo.svg)\n'
                '[reference]: assets/logo.svg\n'
                '<img src="assets/logo.svg">\n'
                '<img src="/assets/logo.svg">\n'
                '<img src="https://cdn.example.invalid/assets/logo.svg">\n'
                '[external](https://cdn.example.invalid/assets/logo.svg)\n'
                '[already](docs/assets/logo.svg)\n'
                'literal assets/logo.svg and data-src="assets/logo.svg"\n')
        expected=('![local](docs/assets/logo.svg)\n'
                  '[root](/docs/assets/logo.svg)\n'
                  '[reference]: docs/assets/logo.svg\n'
                  '<img src="docs/assets/logo.svg">\n'
                  '<img src="/docs/assets/logo.svg">\n'
                  '<img src="https://cdn.example.invalid/assets/logo.svg">\n'
                  '[external](https://cdn.example.invalid/assets/logo.svg)\n'
                  '[already](docs/assets/logo.svg)\n'
                  'literal assets/logo.svg and data-src="assets/logo.svg"\n')
        self.assertEqual(layer._replace_path(readme,'assets/','docs/assets/'),expected)
        self.assertEqual(publish._relayout_readme(readme.encode()),expected.encode())

    def test_build_guide_regeneration_retains_approved_published_source_dates(self):
        section = ('## Published source dates\n\n'
                   'This approved release-date clarification remains verbatim.\n')
        root_guide = self.repo/'BUILD.md'
        root_guide.write_text('# Older guide\n\n'+section)
        publish.git(self.repo,'add','BUILD.md')
        tree = publish.git(self.repo,'write-tree').decode().strip()
        root_base = publish.commit(self.repo,tree,[self.current],'Fixture root build guide\n')
        self.assertEqual(layer._published_source_dates(self.repo,root_base),section.rstrip())

        root_guide.unlink()
        grouped_guide = self.repo/'docs/BUILD.md'
        grouped_guide.parent.mkdir(exist_ok=True)
        grouped_guide.write_text('# Grouped guide\n\n'+section)
        publish.git(self.repo,'add','-A')
        tree = publish.git(self.repo,'write-tree').decode().strip()
        grouped_base = publish.commit(self.repo,tree,[root_base],'Fixture grouped build guide\n')
        self.assertEqual(layer._published_source_dates(self.repo,grouped_base),section.rstrip())

    def test_apply_outputs_moves_the_guide_and_retains_approved_source_dates(self):
        section = ('## Published source dates\n\n'
                   'This approved release-date clarification remains verbatim.\n')
        guide = self.repo/'BUILD.md'
        guide.write_text('# Earlier guide\n\n'+section)
        publish.git(self.repo,'add','BUILD.md')
        base_tree = publish.git(self.repo,'write-tree').decode().strip()
        base = publish.commit(self.repo,base_tree,[self.current],'Fixture approved source dates\n')

        files={
            'BUILDING.md':'See (reproducible-builds/README.md).\ncd mollyim-android/reproducible-builds\n',
            'LEGAL.md':'approved legal text\n',
            'VERIFY.md':'stale guide\n',
            'assets/placeholder':'stale asset\n',
            'baseline-profile/placeholder':'profile\n',
            'fast-lint/placeholder':'fast lint\n',
            'lintchecks/placeholder':'lint checks\n',
            'screener/placeholder':'screener\n',
            'mk/android.mk':'all:\n\t@true\n',
            'reproducible-builds/README.md':'cd mollyim-android/reproducible-builds\n',
            'gradle.properties.docker':'org.gradle.daemon=false\n',
            'Dockerfile':'COPY gradle.properties.docker /root/.gradle/gradle.properties\n',
            'Makefile':'# mk/<platform>.mk\ninclude mk/android.mk\n',
            '.gitignore':'/reproducible-builds/certs\n/reproducible-builds/outputs\n',
            '.dockerignore':'reproducible-builds/certs\nreproducible-builds/outputs\n',
            'gradle.properties':'# DRAFT proposal only; not reserved. Direct Android builds remain blocked.\n',
            'settings.gradle.kts':'include(":core-gms:base")\ninclude(":core-gms:cloud-messaging")\ninclude(":core-gms:safeparcel")\ninclude(":core-gms:safeparcel-processor")\ninclude(":core-gms:tasks")\ninclude(":screener")\ninclude(":lintchecks")\ninclude(":fast-lint")\n',
            'app/build.gradle.kts':(
                'val baseAppTitle = getCiEnv("CI_APP_TITLE") ?: properties["baseAppTitle"] as String\n'
                'val baseAppFileName = getCiEnv("CI_APP_FILENAME") ?: properties["baseAppFileName"] as String\n'
                'val basePackageId = getCiEnv("CI_PACKAGE_ID") ?: properties["basePackageId"] as String\n'
                'throw GradleException("see BUILD.md")\n'
                'fun getLastCommitTimestamp(): String {\n'
                '  return try {\n'
                '    git("log", "-1", "--pretty=format:%ct000")\n'
                '  } catch (e: Exception) {\n'
                '    logger.warn("Failed to get Git commit timestamp: ${e.message}. Using mtime of current build script.")\n'
                '    buildFile.lastModified().toString()\n'
                '  }\n'
                '}\n\n'
                'fun getGitHash(): String {\n'
                '  return try {\n'
                '    git("rev-parse", "--short=12", "HEAD")\n'
                '  } catch (e: Exception) {\n'
                '    logger.warn("Failed to get Git commit hash: ${e.message}. Using default value.")\n'
                '    "abc123def456"\n'
                '  }\n'
                '}\n\n'
                'fun requireCommitTag(): String {\n'
                '  val tag = git("describe", "--tags", "--exact-match")\n'
                '  return tag.takeIf { it.isNotEmpty() } ?: "untagged"\n'
                '}\n\n'
                'private fun git(vararg args: String): String =\n'
                '  providers.exec {\n'
                '    commandLine("git", *args)\n'
                '  }.standardOutput.asText.get().trim()\n'
            ),
        }
        for name,content in files.items():
            path=self.app/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(content)
        (self.app/'.github/workflows').mkdir(parents=True,exist_ok=True)
        (self.app/'.github/workflows/old.yml').write_text('old workflow\n')
        (self.app/'core-gms/base').mkdir(parents=True)
        (self.app/'core-gms/base/placeholder').write_text('base\n')
        for name in ('cloud-messaging','safeparcel','safeparcel-processor','tasks'):
            (self.app/'core-gms'/name).mkdir(parents=True)

        layer.apply_outputs(self.app,ROOT,self.repo,application_base=base,
                            publication_plan={'old_main':base})

        output=(self.app/'docs/BUILD.md').read_text()
        self.assertIn(section.rstrip(),output)
        self.assertFalse((self.app/'BUILD.md').exists())
        self.assertTrue((self.app/'docs/VERIFY.md').is_file())
        self.assertFalse((self.app/'VERIFY.md').exists())
        self.assertTrue((self.app/'docs/assets/banner').is_file())
        self.assertTrue((self.app/'tooling/reproducible-builds/README.md').is_file())
        self.assertTrue((self.app/'core/gms/base/placeholder').is_file())

    def test_reconstruction_checks_ignore_inherited_git_directory(self):
        # An inherited GIT_DIR must not redirect the explicit app checkout.
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            publish.git(repo, 'init', '-q')
            publish.git(repo, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                        'commit', '--allow-empty', '-qm', 'Clean reconstructed fixture')
            expected = publish.git(repo, 'rev-parse', 'HEAD^{tree}').decode().strip()
            with mock.patch.dict(os.environ, {'GIT_DIR': str(repo / 'nonexistent.git')}), \
                    mock.patch.object(layer, 'RECONSTRUCTED_TREE', expected), \
                    mock.patch.object(layer, 'apply_outputs') as outputs:
                layer.apply(repo, repo, repo, application_base='HEAD', publication_plan={})
            outputs.assert_called_once()

    def test_root_layout_groups_guides_assets_and_helpers_without_changing_gradle_ids(self):
        legal=b'Keep this legal text unchanged.\n'
        fixtures={
            'README.md':'<img src="assets/collie.svg">\n[LEGAL.md](LEGAL.md)\n',
            'BUILD.md':'# Build\n[VERIFY.md](VERIFY.md)\n',
            'VERIFY.md':'# Verify\n',
            'BUILDING.md':'See (reproducible-builds/README.md).\ncd mollyim-android/reproducible-builds\n',
            'LEGAL.md':legal,
            'assets/collie.svg':'asset bytes\n',
            'reproducible-builds/README.md':'cd mollyim-android/reproducible-builds\n',
            'Makefile':'# live in mk/<platform>.mk\ninclude mk/android.mk\n',
            'Dockerfile':'COPY gradle.properties.docker /root/.gradle/gradle.properties\n',
            'gradle.properties.docker':'org.gradle.daemon=false\n',
            '.gitignore':'/reproducible-builds/certs\n/reproducible-builds/outputs\n',
            '.dockerignore':'reproducible-builds/certs\nreproducible-builds/outputs\n',
            'settings.gradle.kts':('include(":core-gms:base")\ninclude(":core-gms:cloud-messaging")\n'
                                    'include(":core-gms:safeparcel")\ninclude(":core-gms:safeparcel-processor")\n'
                                    'include(":core-gms:tasks")\ninclude(":screener")\ninclude(":lintchecks")\n'
                                    'include(":fast-lint")\nrootProject.name = "Collie"\n'),
            'app/build.gradle.kts':'throw GradleException("see BUILD.md")\n',
        }
        for name,content in fixtures.items():
            path=self.app/name;path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(content if isinstance(content,bytes) else content.encode())
        for name in ('baseline-profile','fast-lint','lintchecks','screener','mk'):
            (self.app/name).mkdir(parents=True,exist_ok=True)
            (self.app/name/'placeholder').write_text(name)
        for name in ('base','cloud-messaging','safeparcel','safeparcel-processor','tasks'):
            path=self.app/'core-gms'/name;path.mkdir(parents=True,exist_ok=True)
            (path/'placeholder').write_text(name)
        original_legal=(self.app/'LEGAL.md').read_bytes()

        layer.apply_root_layout(self.app)
        layer.apply_root_layout(self.app)

        self.assertEqual((self.app/'docs/LEGAL.md').read_bytes(),original_legal)
        self.assertTrue((self.app/'docs/BUILD.md').is_file())
        self.assertTrue((self.app/'docs/BUILDING.md').is_file())
        self.assertTrue((self.app/'docs/VERIFY.md').is_file())
        self.assertEqual((self.app/'docs/assets/collie.svg').read_text(),'asset bytes\n')
        self.assertIn('docs/assets/collie.svg',(self.app/'README.md').read_text())
        self.assertIn('[LEGAL.md](docs/LEGAL.md)',(self.app/'README.md').read_text())
        self.assertIn('../tooling/reproducible-builds/README.md',(self.app/'docs/BUILDING.md').read_text())
        self.assertTrue((self.app/'tooling/reproducible-builds/README.md').is_file())
        self.assertTrue((self.app/'tooling/gradle.properties.docker').is_file())
        self.assertTrue((self.app/'core/gms/base/placeholder').is_file())
        self.assertFalse((self.app/'core-gms').exists())
        for name in ('baseline-profile','fast-lint','lintchecks','screener','mk','reproducible-builds'):
            self.assertTrue((self.app/'tooling'/name).is_dir())
            self.assertFalse((self.app/name).exists())
        self.assertIn('include tooling/mk/android.mk',(self.app/'Makefile').read_text())
        self.assertIn('COPY tooling/gradle.properties.docker',(self.app/'Dockerfile').read_text())
        settings=(self.app/'settings.gradle.kts').read_text()
        self.assertIn('include(":core-gms:base")',settings)
        self.assertIn('project(":core-gms:base").projectDir = file("core/gms/base")',settings)
        self.assertIn('project(":screener").projectDir = file("tooling/screener")',settings)
        self.assertIn('see docs/BUILD.md',(self.app/'app/build.gradle.kts').read_text())
