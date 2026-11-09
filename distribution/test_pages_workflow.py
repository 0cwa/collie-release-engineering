"""Static workflow boundaries, not GitHub expression execution or live protections."""
from pathlib import Path
import re,unittest
class WorkflowBoundaryTest(unittest.TestCase):
    def setUp(self):self.root=Path(__file__).resolve().parents[1]/'.github/workflows'

    def block(self,text,header):
        """Read one indentation-scoped block in these fixed-format workflows.

        This deliberately is not a YAML parser; fail if a reviewed header moves,
        disappears or becomes ambiguous instead of matching it in another job.
        """
        lines=text.splitlines()
        matches=[i for i,line in enumerate(lines) if line==header]
        self.assertEqual(len(matches),1,header)
        start=matches[0]+1;indent=len(header)-len(header.lstrip())
        end=start
        while end<len(lines):
            line=lines[end]
            if line.strip() and len(line)-len(line.lstrip())<=indent:break
            end+=1
        return '\n'.join(lines[start:end])+'\n'

    def field(self,text,key,indent):
        matches=re.findall(r'^'+(' '*indent)+re.escape(key)+r': (.+)$',text,re.MULTILINE)
        self.assertEqual(len(matches),1,key)
        return matches[0]

    def jobs(self):
        text=(self.root/'pages-release.yml').read_text()
        body=self.block(text,'jobs:')
        names=re.findall(r'^  ([a-z0-9-]+):$',body,re.MULTILINE)
        self.assertEqual(set(names),{'prepare','phase1','phase1-readback','phase2','final-readback'})
        self.assertEqual(len(names),len(set(names)))
        return {name:self.block(body,'  '+name+':') for name in names}

    def steps(self,job):
        body=self.block(job,'    steps:')
        starts=[match.start() for match in re.finditer(r'^      - ',body,re.MULTILINE)]
        self.assertTrue(starts,'job must contain executable steps')
        ends=starts[1:]+[len(body)]
        # Normalize the first mapping key to the same indentation as later keys.
        return ['        '+body[start+8:end] for start,end in zip(starts,ends)]

    def step(self,job,key,value):
        pattern=r'^        '+re.escape(key)+r': '+re.escape(value)+r'$'
        matches=[step for step in self.steps(job) if re.search(pattern,step,re.MULTILINE)]
        self.assertEqual(len(matches),1,key+': '+value)
        return matches[0]

    def test_pr_checks_have_no_deployment_or_privileged_event(self):
        text=(self.root/'pages-validation.yml').read_text()
        for forbidden in ('pull_request_target','workflow_run','pages: write','id-token: write','environment:','deploy-pages@','secrets.'):
            self.assertNotIn(forbidden,text)
        self.assertIn('persist-credentials: false',text)
    def test_manual_two_phase_gate_and_immutable_action_refs(self):
        text=(self.root/'pages-release.yml').read_text()
        self.assertNotRegex(text,r'(?m)^\s*(pull_request_target|workflow_run)\s*:')
        self.assertNotIn('secrets.',text)
        self.assertIn("github.ref == 'refs/heads/main'",text)
        self.assertIn("vars.COLLIE_PAGES_PRODUCTION_ENABLED == 'true'",text)
        self.assertIn('test "$APPROVED_POLICY" = "$RUN_POLICY"',text)
        self.assertIn('needs: phase1-readback',text)
        self.assertIn("if: ${{ !cancelled() && needs['phase1-readback'].result == 'success' }}",text)
        self.assertIn('pages.py preflight',text)
        self.assertEqual(text.count('--timeout-seconds 1800'),2)
        self.assertEqual(text.count('python distribution/deploy_pages.py'),2)
        self.assertIn("phase1_artifact_id: ${{ steps['phase1-upload'].outputs.artifact_id }}",text)
        self.assertIn("phase2_artifact_id: ${{ steps['phase2-upload'].outputs.artifact_id }}",text)
        self.assertIn("phase2_artifact_id: ${{ needs.prepare.outputs.phase2_artifact_id }}",text)
        self.assertIn("ARTIFACT_ID: ${{ needs.prepare.outputs.phase1_artifact_id }}",text)
        self.assertIn("ARTIFACT_ID: ${{ needs['phase1-readback'].outputs.phase2_artifact_id }}",text)
        self.assertEqual(text.count('name: github-pages'),2)
        self.assertEqual(text.count('pages: write'),2)
        self.assertEqual(text.count('id-token: write'),2)
        self.assertEqual(text.count('contents: read'),3)
        for action in re.findall(r'uses:\s*(\S+)',text):self.assertRegex(action,r'^actions/[a-z-]+@[0-9a-f]{40}$')
        self.assertEqual(text.count('actions/upload-pages-artifact@5f327015a708106f6f1b7c9a633901af30fe80cb'),2)
        self.assertEqual(text.count('include-hidden-files: true'),2)
        self.assertIn('path: ${{ runner.temp }}/pages/phase1/site',text)
        self.assertIn('path: ${{ runner.temp }}/pages/phase2/site',text)
        self.assertIn("if: inputs.preparation_mode == 'feed'",text)
        self.assertEqual(len(re.findall(r'(?m)^\s+name: collie-pages-phase1$',text)),1)
        self.assertEqual(len(re.findall(r'(?m)^\s+name: collie-pages-phase2$',text)),1)
        self.assertEqual(text.count('name: collie-pages-phase1'),1)
        self.assertEqual(text.count('name: collie-pages-phase2'),1)
        self.assertIn('path: ${{ runner.temp }}/control/',text)

    def test_phase_artifact_ids_are_bound_to_the_correct_upload_and_deploy_steps(self):
        jobs=self.jobs();prepare=jobs['prepare']
        outputs=self.block(prepare,'    outputs:')
        self.assertEqual(self.field(outputs,'mode',6),'${{ steps.prepare.outputs.mode }}')
        routes={
            'phase1':'${{ needs.prepare.outputs.phase1_artifact_id }}',
            'phase2':"${{ needs['phase1-readback'].outputs.phase2_artifact_id }}",
        }
        for phase,route in routes.items():
            with self.subTest(phase=phase):
                self.assertEqual(self.field(outputs,phase+'_artifact_id',6),
                                 "${{ steps['"+phase+"-upload'].outputs.artifact_id }}")
                upload=self.step(prepare,'id',phase+'-upload')
                self.assertEqual(self.field(upload,'uses',8),
                                 'actions/upload-pages-artifact@5f327015a708106f6f1b7c9a633901af30fe80cb')
                inputs=self.block(upload,'        with:')
                self.assertEqual(self.field(inputs,'name',10),'collie-pages-'+phase)
                self.assertEqual(self.field(inputs,'path',10),'${{ runner.temp }}/pages/'+phase+'/site')
                if phase=='phase1':
                    self.assertEqual(self.field(upload,'if',8),"inputs.preparation_mode == 'feed'")
                else:self.assertNotRegex(upload,r'(?m)^        if:')
                deploy=self.step(jobs[phase],'id','deploy')
                env=self.block(deploy,'        env:')
                self.assertEqual(self.field(env,'ARTIFACT_ID',10),route)
                self.assertEqual(self.field(env,'GITHUB_TOKEN',10),'${{ github.token }}')
                self.assertEqual(self.field(deploy,'run',8),
                                 'python distribution/deploy_pages.py --artifact-id "$ARTIFACT_ID" --timeout-seconds 1800')
        relay=self.block(jobs['phase1-readback'],'    outputs:')
        self.assertEqual(self.field(relay,'phase2_artifact_id',6),'${{ needs.prepare.outputs.phase2_artifact_id }}')

    def test_credentials_and_revision_checkout_belong_to_the_correct_jobs(self):
        text=(self.root/'pages-release.yml').read_text()
        self.assertEqual(self.block(text,'permissions:').strip(),'contents: read')
        for name,job in self.jobs().items():
            with self.subTest(job=name):
                if name in ('phase1','phase2'):
                    self.assertEqual(self.block(job,'    permissions:').splitlines(),
                                     ['      contents: read','      pages: write','      id-token: write'])
                    environment=self.block(job,'    environment:')
                    self.assertEqual(self.field(environment,'name',6),'github-pages')
                    self.assertEqual(self.field(environment,'url',6),'${{ steps.deploy.outputs.page_url }}')
                else:
                    self.assertNotRegex(job,r'(?m)^    (permissions|environment):')
                checkout=self.step(job,'uses','actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803 # v6')
                inputs=self.block(checkout,'        with:')
                self.assertEqual(self.field(inputs,'ref',10),'${{ github.sha }}')
                self.assertEqual(self.field(inputs,'persist-credentials',10),'false')
                self.assertNotRegex(checkout,r'(?m)^        (if|continue-on-error):')

    def test_feed_and_final_readback_are_executable_success_gates(self):
        jobs=self.jobs()
        expected={
            'phase1':('prepare',"needs.prepare.outputs.mode == 'feed'"),
            'phase1-readback':('[prepare, phase1]',"${{ !cancelled() && needs.prepare.result == 'success' && (needs.phase1.result == 'success' || needs.prepare.outputs.mode == 'activate-links') }}"),
            'phase2':('phase1-readback',"${{ !cancelled() && needs['phase1-readback'].result == 'success' }}"),
            'final-readback':('phase2',"${{ !cancelled() && needs.phase2.result == 'success' }}"),
        }
        for name,(needs,condition) in expected.items():
            self.assertEqual(self.field(jobs[name],'needs',4),needs)
            self.assertEqual(self.field(jobs[name],'if',4),condition)
            self.assertNotRegex(jobs[name],r'(?m)^\s*continue-on-error:')
        for name,phase in (('phase1-readback','phase1'),('final-readback','phase2')):
            with self.subTest(job=name):
                command='python distribution/pages.py readback "$RUNNER_TEMP/control/plan.json" "$RUNNER_TEMP/control/journal.json" --phase '+phase
                readback=self.step(jobs[name],'run',command)
                if phase=='phase1':
                    self.assertEqual(self.field(readback,'if',8),"inputs.preparation_mode == 'feed'")
                else:self.assertNotRegex(readback,r'(?m)^        if:')

    def test_control_artifacts_transfer_the_readback_journal_in_order(self):
        jobs=self.jobs()
        routes=(
            ('prepare','upload','collie-pages-control'),
            ('phase1-readback','download','collie-pages-control'),
            ('phase1-readback','upload','collie-pages-after-phase1'),
            ('final-readback','download','collie-pages-after-phase1'),
            ('final-readback','upload','collie-pages-completed-receipt'),
        )
        for name,operation,artifact in routes:
            with self.subTest(job=name,artifact=artifact):
                action=('actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # v4'
                        if operation=='upload' else 'actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093 # v4')
                transfer=self.step(jobs[name],'uses',action)
                self.assertNotRegex(transfer,r'(?m)^        (if|continue-on-error):')
                inputs=self.block(transfer,'        with:')
                self.assertEqual(self.field(inputs,'name',10),artifact)
                self.assertEqual(self.field(inputs,'path',10),'${{ runner.temp }}/control/')
                if operation=='download':
                    self.assertEqual(len(inputs.splitlines()),2,'download must remain scoped to this run')
                else:self.assertEqual(self.field(inputs,'if-no-files-found',10),'error')
                if name!='prepare':
                    steps=self.steps(jobs[name]);transfer_index=steps.index(transfer)
                    readback_indexes=[i for i,step in enumerate(steps) if re.search(r'^        run: python distribution/pages.py readback ',step,re.MULTILINE)]
                    self.assertEqual(len(readback_indexes),1)
                    if operation=='download':self.assertLess(transfer_index,readback_indexes[0])
                    else:self.assertGreater(transfer_index,readback_indexes[0])

    def test_activation_prepares_and_reads_phase1_before_control_upload(self):
        prepare=self.jobs()['prepare']
        step=self.step(prepare,'id','prepare')
        self.assertNotRegex(step,r'(?m)^        (if|continue-on-error):')
        env=self.block(step,'        env:')
        self.assertEqual(self.field(env,'PREPARATION_MODE',10),'${{ inputs.preparation_mode }}')
        script=self.block(step,'        run: |')
        branch='''          if [ "$PREPARATION_MODE" = feed ]; then
            python distribution/pages.py prepare "$RUNNER_TEMP/inputs" "$RUNNER_TEMP/pages" --policy "$APPROVED_POLICY"
          else
            python distribution/pages_activation.py prepare "$RUNNER_TEMP/inputs" "$RUNNER_TEMP/pages" --policy "$APPROVED_POLICY"
          fi'''
        preflight='          python distribution/pages.py preflight "$RUNNER_TEMP/pages/plan.json" "$RUNNER_TEMP/pages/journal.json"'
        readback='''          if [ "$PREPARATION_MODE" = activate-links ]; then
            python distribution/pages.py readback "$RUNNER_TEMP/pages/plan.json" "$RUNNER_TEMP/pages/journal.json" --phase phase1
          fi'''
        copy='          cp "$RUNNER_TEMP/pages/plan.json" "$RUNNER_TEMP/pages/journal.json" "$RUNNER_TEMP/control/"'
        for command in (branch,preflight,readback,copy):self.assertIn(command,script)
        self.assertLess(script.index(branch),script.index(preflight))
        self.assertLess(script.index(preflight),script.index(readback))
        self.assertLess(script.index(readback),script.index(copy))
        upload=self.step(prepare,'uses','actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # v4')
        steps=self.steps(prepare)
        self.assertLess(steps.index(step),steps.index(upload))
