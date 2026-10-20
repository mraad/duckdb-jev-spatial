import hashlib
import json

import pytest
import yaml

from jev_spatial import Catalog, compile_query, parse_question


def legacy_files(project):
    (project / 'okf').mkdir()
    (project / 'okf/Wells.md').write_text('## `water_depth`\nAlso called: water depth, depth\n')
    (project / 'okf/Pipelines.md').write_text('IN SERVICE')


def test_knowledge_sources_and_depth_aliases(catalog_project):
    legacy_files(catalog_project)
    catalog = Catalog(catalog_project)
    assert catalog.metadata_layout == 'knowledge/datasets'
    expected = ['NorthSea.ossie.yaml', 'NorthSea.ontology.yaml',
                'knowledge/datasets/Wells.md', 'knowledge/datasets/Pipelines.md']
    assert catalog.sources == {str(catalog_project / p): hashlib.sha256((catalog_project / p).read_bytes()).hexdigest()
                               for p in expected}
    plans = [compile_query(parse_question(f'Show wells with {alias} > 350 meters', catalog), catalog)
             for alias in ('depth', 'water depth', 'well water depth')]
    assert all(p.sql == plans[0].sql and p.parameters == [350.0] for p in plans)


def test_legacy_layout_remains_supported(catalog_project):
    for p in (catalog_project / 'knowledge/datasets').iterdir():
        p.unlink()
    (catalog_project / 'knowledge/datasets').rmdir()
    legacy_files(catalog_project)
    catalog = Catalog(catalog_project)
    assert catalog.metadata_layout == 'okf'
    assert any(p.endswith('/okf/Wells.md') for p in catalog.sources)
    assert compile_query(parse_question('Show wells with depth > 350 meters', catalog), catalog).parameters == [350.0]


def test_partial_knowledge_never_falls_back_to_legacy(catalog_project):
    legacy_files(catalog_project)
    (catalog_project / 'knowledge/datasets/Wells.md').unlink()
    with pytest.raises(FileNotFoundError, match='knowledge/datasets/Wells.md'):
        Catalog(catalog_project)


@pytest.mark.parametrize('header', [
    'type: Ossie Dataset\ntitle: Wells\nsource: Other.main.Wells',
    'type: Ossie Concept\ntitle: Wells\nsource: NorthSea.main.Wells',
    'type: Ossie Dataset\ntitle: Pipelines\nsource: NorthSea.main.Wells',
    '- not a mapping',
])
def test_dataset_identity_is_required(catalog_project, header):
    legacy_files(catalog_project)
    (catalog_project / 'knowledge/datasets/Wells.md').write_text(f'---\n{header}\n---\n')
    with pytest.raises(ValueError, match='dataset identity'):
        Catalog(catalog_project)


@pytest.mark.parametrize('change, message', [
    ('depth_synonym', 'supported depth synonym'),
    ('malformed_synonyms', 'Invalid synonyms'),
    ('ambiguous_synonym', 'Ambiguous depth alias'),
    ('ambiguous_field', 'Ambiguous depth alias'),
    ('depth_type', 'must be numeric'),
    ('depth_unit', 'measured in meters'),
    ('phase_values', 'IN SERVICE'),
    ('phase_type', 'IN SERVICE'),
    ('geometry_crs', 'EPSG:4326'),
    ('distance_crs', 'EPSG:3035'),
    ('table_source', 'Unexpected source'),
])
def test_contract_drift_is_rejected(catalog_project, change, message):
    path = catalog_project / 'NorthSea.ossie.yaml'
    model = yaml.safe_load(path.read_text())
    wells, pipelines = model['datasets'][:2]
    depth = wells['fields'][1]
    phase = pipelines['fields'][1]
    if change == 'depth_synonym':
        depth['ai_context']['synonyms'] = ['total drilled depth']
    elif change == 'malformed_synonyms':
        depth['ai_context']['synonyms'] = 'well water depth'
    elif change == 'ambiguous_synonym':
        wells['fields'][2]['ai_context'] = {'synonyms': ['Depth']}
    elif change == 'ambiguous_field':
        wells['fields'].append({'name': 'depth', 'datatype': 'Float'})
    elif change == 'depth_type':
        depth['datatype'] = 'String'
    elif change == 'depth_unit':
        depth['custom_extensions'][0]['data'] = json.dumps({'unit': 'feet'})
    elif change == 'phase_values':
        phase['ai_context']['sample_values'] = ['DECOMMISSIONED']
    elif change == 'phase_type':
        phase['datatype'] = 'Integer'
    elif change == 'geometry_crs':
        wells['fields'][-1]['custom_extensions'][0]['data'] = json.dumps({'wkid': 3857})
    elif change == 'distance_crs':
        model['ai_context']['instructions'] = 'Measure in EPSG:4326'
    elif change == 'table_source':
        wells['source'] = 'NorthSea.other.Wells'
        (catalog_project / 'knowledge/datasets/Wells.md').write_text(
            '---\ntype: Ossie Dataset\ntitle: Wells\nsource: NorthSea.other.Wells\n---\n')
    path.write_text(yaml.safe_dump(model))
    with pytest.raises(ValueError, match=message):
        Catalog(catalog_project)
