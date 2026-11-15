import copy
import unittest
import report


def location(uri, index=None):
    a = {'uri': uri, 'uriBaseId': '%SRCROOT%'}
    if index is not None:
        a['index'] = index
    return {'physicalLocation': {'artifactLocation': a, 'region': {'startLine': 7}}, 'message': {'text': 'source'}}


def fixture():
    run = {
        'tool': {'driver': {'name': 'CodeQL'}, 'extensions': [{
            'name': 'pack', 'semanticVersion': '1.2', 'locations': [{'uri': 'pack-root'}],
            'taxa': [{'id': 'taxon-a', 'properties': {'detail': 'A'}}, {'id': 'taxon-b', 'properties': {'detail': 'B'}}]
        }]},
        'artifacts': [
            {'location': {'uri': 'a.kt', 'uriBaseId': '%SRCROOT%', 'index': 0}},
            {'location': {'uri': 'b.kt', 'uriBaseId': '%SRCROOT%', 'index': 1}},
        ],
    }
    context = {
        'codeFlows': [{'threadFlows': [{'locations': [
            {'location': location('a.kt', 0), 'taxa': [{'index': 0, 'toolComponent': {'index': 0}, 'properties': {'role': 'step'}}]},
            {'location': location('b.kt', 1), 'importance': 'essential'},
        ]}]}],
        'relatedLocations': [location('b.kt', 1)],
    }
    return run, context


class CanonicalContextTests(unittest.TestCase):
    def digest(self, run, context):
        return report.canonical_context_sha256(run, context)

    def test_reordered_artifact_and_taxon_tables_preserve_exact_semantics(self):
        run, context = fixture()
        before = self.digest(run, context)
        changed = copy.deepcopy(run)
        changed['artifacts'].reverse()
        for i, item in enumerate(changed['artifacts']):
            item['location']['index'] = i
        changed['tool']['extensions'][0]['taxa'].reverse()
        after = copy.deepcopy(context)
        entries = after['codeFlows'][0]['threadFlows'][0]['locations']
        entries[0]['location']['physicalLocation']['artifactLocation']['index'] = 1
        entries[1]['location']['physicalLocation']['artifactLocation']['index'] = 0
        after['relatedLocations'][0]['physicalLocation']['artifactLocation']['index'] = 0
        entries[0]['taxa'][0]['index'] = 1
        self.assertEqual(before, self.digest(changed, after))

    def test_indexed_and_direct_uri_or_taxon_id_equivalents(self):
        run, context = fixture()
        direct = copy.deepcopy(context)
        for entry in direct['codeFlows'][0]['threadFlows'][0]['locations']:
            entry['location']['physicalLocation']['artifactLocation'].pop('index')
        direct['relatedLocations'][0]['physicalLocation']['artifactLocation'].pop('index')
        direct['codeFlows'][0]['threadFlows'][0]['locations'][0]['taxa'][0] = {
            'id': 'taxon-a', 'toolComponent': {'name': 'pack'}, 'properties': {'role': 'step'}
        }
        self.assertEqual(self.digest(run, context), self.digest(run, direct))

    def test_reference_target_or_unknown_metadata_changes_digest(self):
        run, context = fixture()
        before = self.digest(run, context)
        mutations = []
        ctx = copy.deepcopy(context); ctx['codeFlows'][0]['threadFlows'][0]['locations'][0]['importance'] = 'new'; mutations.append((run, ctx))
        ctx = copy.deepcopy(context); ctx['relatedLocations'][0]['physicalLocation']['region']['startLine'] += 1; mutations.append((run, ctx))
        ctx = copy.deepcopy(context); ctx['codeFlows'][0]['threadFlows'][0]['locations'][0]['taxa'][0]['index'] = 1; mutations.append((run, ctx))
        changed = copy.deepcopy(run); changed['tool']['extensions'][0]['taxa'][0]['properties']['detail'] = 'changed'; mutations.append((changed, context))
        changed = copy.deepcopy(run); changed['artifacts'][0]['unknownFutureField'] = {'security': 'new'}; mutations.append((changed, context))
        changed = copy.deepcopy(run); changed['tool']['extensions'][0]['unknownFutureField'] = 'new'; mutations.append((changed, context))
        ctx = copy.deepcopy(context); ctx['codeFlows'][0]['threadFlows'][0]['locations'][0]['taxa'][0]['unknownFutureField'] = 'new'; mutations.append((run, ctx))
        for r, c in mutations:
            with self.subTest(mutation=len(str(c))):
                self.assertNotEqual(before, self.digest(r, c))

    def test_standalone_heuristic_is_opaque_and_exact(self):
        run, context = fixture()
        entry = context['codeFlows'][0]['threadFlows'][0]['locations'][0]
        entry['taxa'] = [{'id': 'heuristic-callback', 'properties': {'CodeQL/DataflowRole': 'step'}}]
        before = self.digest(run, context)
        for key, value in [('id', 'heuristic-changed'), ('properties', {'CodeQL/DataflowRole': 'sink'})]:
            with self.subTest(key=key):
                changed = copy.deepcopy(context)
                changed['codeFlows'][0]['threadFlows'][0]['locations'][0]['taxa'][0][key] = value
                self.assertNotEqual(before, self.digest(run, changed))
        # A standalone object is not equated with a resolved table entry.
        changed_run = copy.deepcopy(run)
        changed_run['tool']['driver']['taxa'] = [{'id': 'heuristic-callback'}]
        self.assertNotEqual(before, self.digest(changed_run, context))

    def test_invalid_or_conflicting_references_fail_closed(self):
        for kind in ('artifact', 'taxon', 'component'):
            for value in (True, False, -1, 2.5, '0', 999, None):
                with self.subTest(kind=kind, value=value):
                    run, context = fixture()
                    item = context['codeFlows'][0]['threadFlows'][0]['locations'][0]
                    if kind == 'artifact': item['location']['physicalLocation']['artifactLocation']['index'] = value
                    elif kind == 'taxon': item['taxa'][0]['index'] = value
                    else: item['taxa'][0]['toolComponent']['index'] = value
                    with self.assertRaises(report.SarifError): self.digest(run, context)
        for kind in ('uri', 'base', 'id', 'component_name', 'missing_id', 'missing_component'):
            with self.subTest(kind=kind):
                run, context = fixture(); item = context['codeFlows'][0]['threadFlows'][0]['locations'][0]
                if kind == 'uri': item['location']['physicalLocation']['artifactLocation']['uri'] = 'b.kt'
                elif kind == 'base': item['location']['physicalLocation']['artifactLocation']['uriBaseId'] = '%OTHER%'
                elif kind == 'id': item['taxa'][0]['id'] = 'taxon-b'
                elif kind == 'component_name': item['taxa'][0]['toolComponent']['name'] = 'other'
                elif kind == 'missing_id': item['taxa'][0] = {'id': 'missing', 'toolComponent': {'name': 'pack'}}
                else: item['taxa'][0]['toolComponent'] = {'name': 'missing'}
                with self.assertRaises(report.SarifError): self.digest(run, context)

    def test_missing_or_conflicting_target_metadata_fails_closed(self):
        for variant in ('missing-uri', 'self-index', 'future-field-conflict', 'no-taxon-id', 'duplicate-indexed-id'):
            with self.subTest(variant=variant):
                run, context = fixture()
                item = context['codeFlows'][0]['threadFlows'][0]['locations'][0]
                if variant == 'missing-uri':
                    del run['artifacts'][0]['location']['uri']
                elif variant == 'self-index':
                    run['artifacts'][0]['location']['index'] = True
                elif variant == 'future-field-conflict':
                    run['artifacts'][0]['location']['future'] = {'x': 1}
                    item['location']['physicalLocation']['artifactLocation']['future'] = {'x': 2}
                elif variant == 'no-taxon-id':
                    del run['tool']['extensions'][0]['taxa'][0]['id']
                else:
                    run['tool']['extensions'][0]['taxa'].append({'id': 'taxon-a'})
                with self.assertRaises(report.SarifError): self.digest(run, context)

    def test_opaque_fields_preserve_schema_like_keys_literally(self):
        run, context = fixture()
        entry = context['codeFlows'][0]['threadFlows'][0]['locations'][0]
        entry['properties'] = {'taxa': [{'index': -1}], 'artifactLocation': {'index': True}}
        entry['location']['properties'] = {'artifactLocation': {'index': 0}, 'taxa': 'opaque'}
        before = self.digest(run, context)
        for path in ('taxa', 'artifactLocation'):
            changed = copy.deepcopy(context)
            changed['codeFlows'][0]['threadFlows'][0]['locations'][0]['properties'][path] = {'changed': True}
            self.assertNotEqual(before, self.digest(run, changed))
        standalone = {'id': 'heuristic-callback', 'properties': {'taxa': [{'index': -1}], 'artifactLocation': {'index': True}}}
        self.assertEqual(standalone, report.canonical_context(run, standalone, 'taxa-entry'))
        run['tool']['driver']['taxa'] = [{'id': 'another'}]
        self.assertEqual(standalone, report.canonical_context(run, standalone, 'taxa-entry'))

    def test_component_location_resolves_reordered_artifact_and_binds_target(self):
        run, context = fixture()
        run['tool']['extensions'][0]['locations'] = [{'index': 0}]
        before = self.digest(run, context)
        changed = copy.deepcopy(run)
        changed['artifacts'].reverse()
        for i, artifact in enumerate(changed['artifacts']): artifact['location']['index'] = i
        changed['tool']['extensions'][0]['locations'][0]['index'] = 1
        updated = copy.deepcopy(context)
        entries = updated['codeFlows'][0]['threadFlows'][0]['locations']
        entries[0]['location']['physicalLocation']['artifactLocation']['index'] = 1
        entries[1]['location']['physicalLocation']['artifactLocation']['index'] = 0
        updated['relatedLocations'][0]['physicalLocation']['artifactLocation']['index'] = 0
        self.assertEqual(before, self.digest(changed, updated))
        changed['tool']['extensions'][0]['locations'][0]['index'] = 0
        self.assertNotEqual(before, self.digest(changed, updated))

    def test_component_identity_ambiguity_rejected_for_index_and_implicit_driver(self):
        run, context = fixture()
        run['tool']['extensions'].append(copy.deepcopy(run['tool']['extensions'][0]))
        with self.assertRaises(report.SarifError): self.digest(run, context)
        run, context = fixture()
        run['tool']['driver']['taxa'] = [{'id': 'taxon-a'}]
        run['tool']['extensions'].append({'name': 'CodeQL'})
        context['codeFlows'][0]['threadFlows'][0]['locations'][0]['taxa'] = [{'index': 0}]
        with self.assertRaises(report.SarifError): self.digest(run, context)

    def test_integral_float_self_indices_rejected(self):
        for index in (0.0, 1.0):
            run, context = fixture()
            run['artifacts'][int(index)]['location']['index'] = index
            with self.subTest(index=index), self.assertRaises(report.SarifError): self.digest(run, context)

    def test_indexed_thread_flow_locations_fail_closed_with_inline_evidence(self):
        for index in (0, True, -1, 0.0, '0', None):
            for target in ({'location': location('a.kt'), 'state': {'x': {'text': 'first'}}},
                           {'location': location('b.kt'), 'taxa': [{'id': 'changed'}]}):
                with self.subTest(index=index, target=target):
                    run, context = fixture()
                    run['threadFlowLocations'] = [target]
                    # Keep a legitimate inline source path along with the unsupported reference.
                    context['codeFlows'][0]['threadFlows'][0]['locations'].append({'index': index})
                    with self.assertRaisesRegex(report.SarifError, 'Indexed thread-flow'):
                        self.digest(run, context)

    def test_missing_or_malformed_taxon_target_fails_closed(self):
        for value in ({}, {'id': 'missing', 'toolComponent': {'name': 'pack'}}, {'index': 1}):
            with self.subTest(value=value):
                run, context = fixture()
                context['codeFlows'][0]['threadFlows'][0]['locations'][0]['taxa'] = [value]
                with self.assertRaises(report.SarifError): self.digest(run, context)

    def test_logical_location_references_reject_changed_table_targets(self):
        for field in ('index', 'parentIndex'):
            for target in ({'name': 'original'}, {'name': 'changed', 'kind': 'function'}):
                with self.subTest(field=field, target=target):
                    run, context = fixture()
                    run['logicalLocations'] = [target]
                    context['relatedLocations'][0]['logicalLocations'] = [
                        {'name': 'inline', field: 0}]
                    with self.assertRaisesRegex(report.SarifError, 'Indexed logical'):
                        self.digest(run, context)
        run, context = fixture()
        context['relatedLocations'][0]['logicalLocations'] = [{'name': 'inline'}]
        before = self.digest(run, context)
        context['relatedLocations'][0]['logicalLocations'][0]['name'] = 'different'
        self.assertNotEqual(before, self.digest(run, context))

    def test_artifact_parent_references_reject_changed_parent_targets(self):
        for target in ({'location': {'uri': 'original.zip'}},
                       {'location': {'uri': 'changed.zip'}, 'hashes': {'sha-256': 'changed'}}):
            with self.subTest(target=target):
                run, context = fixture()
                run['artifacts'].append(target)
                run['artifacts'][0]['parentIndex'] = 2
                with self.assertRaisesRegex(report.SarifError, 'Indexed artifact parents'):
                    self.digest(run, context)

    def test_id_based_messages_reject_changed_templates_at_each_schema_path(self):
        for path in ('codeFlow', 'threadFlow', 'location', 'related', 'stack',
                     'stackFrameLocation', 'region', 'contextRegion', 'annotation', 'artifact'):
            for template in ('Original {0}', 'Different {0}'):
                with self.subTest(path=path, template=template):
                    run, context = fixture()
                    run['tool']['driver']['rules'] = [
                        {'id': 'rule', 'messageStrings': {'step': {'text': template}}}]
                    flow = context['codeFlows'][0]
                    thread = flow['threadFlows'][0]
                    entry = thread['locations'][0]
                    loc = entry['location']
                    physical = loc['physicalLocation']
                    if path == 'codeFlow': container = flow
                    elif path == 'threadFlow': container = thread
                    elif path == 'location': container = loc
                    elif path == 'related': container = context['relatedLocations'][0]
                    elif path in ('stack', 'stackFrameLocation'):
                        entry['stack'] = {'frames': [{'location': location('a.kt', 0)}]}
                        container = (entry['stack'] if path == 'stack'
                                     else entry['stack']['frames'][0]['location'])
                    elif path == 'region': container = physical['region']
                    elif path == 'contextRegion':
                        container = physical.setdefault('contextRegion', {'startLine': 1})
                    elif path == 'annotation':
                        loc['annotations'] = [{'startLine': 7}]
                        container = loc['annotations'][0]
                    else:
                        run['artifacts'][0]['description'] = {'id': 'step', 'arguments': ['value']}
                        container = None
                    if container is not None:
                        container['message'] = {'id': 'step', 'arguments': ['value']}
                    with self.assertRaisesRegex(report.SarifError, 'ID-based context messages'):
                        self.digest(run, context)

    def test_reference_shaped_property_bags_remain_literal(self):
        run, context = fixture()
        properties = {
            'logicalLocations': [{'index': 0, 'parentIndex': 1}],
            'parentIndex': 2, 'message': {'id': 'opaque'},
        }
        context['relatedLocations'][0]['properties'] = copy.deepcopy(properties)
        canonical = report.canonical_context(run, context)
        self.assertEqual(properties, canonical['relatedLocations'][0]['properties'])
        before = self.digest(run, context)
        context['relatedLocations'][0]['properties']['message']['id'] = 'changed'
        self.assertNotEqual(before, self.digest(run, context))

    def test_relationship_descriptions_reject_changed_message_templates(self):
        for template in ('Original {0}', 'Different {0}'):
            with self.subTest(template=template):
                run, context = fixture()
                run['tool']['driver']['rules'] = [
                    {'id': 'rule', 'messageStrings': {'step': {'text': template}}}]
                context['relatedLocations'][0]['relationships'] = [{
                    'target': 1, 'description': {'id': 'step', 'arguments': ['value']}}]
                with self.assertRaisesRegex(report.SarifError, 'ID-based context messages'):
                    self.digest(run, context)

    def test_ambiguous_ids_and_component_names_fail_closed(self):
        run, context = fixture(); item = context['codeFlows'][0]['threadFlows'][0]['locations'][0]
        run['tool']['extensions'][0]['taxa'].append({'id': 'taxon-a'})
        item['taxa'][0] = {'id': 'taxon-a', 'toolComponent': {'name': 'pack'}}
        with self.assertRaises(report.SarifError): self.digest(run, context)
        run, context = fixture(); item = context['codeFlows'][0]['threadFlows'][0]['locations'][0]
        run['tool']['extensions'].append(copy.deepcopy(run['tool']['extensions'][0]))
        item['taxa'][0]['toolComponent'] = {'name': 'pack'}
        with self.assertRaises(report.SarifError): self.digest(run, context)


if __name__ == '__main__': unittest.main()
