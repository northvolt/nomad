# Copyright 2018 Markus Scheidgen
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an"AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from io import StringIO
import json
import numpy as np
import pytest
import os

from nomadcore.local_meta_info import loadJsonFile
import nomad_meta_info

from nomad import utils, files
from nomad.parsing import JSONStreamWriter, parser_dict, match_parser
from nomad.parsing import LocalBackend, BadContextURI

parser_examples = [
    ('parsers/random', 'test/data/parsers/random_0'),
    ('parsers/template', 'tests/data/parsers/template.json'),
    ('parsers/exciting', 'tests/data/parsers/exciting/Ag/INFO.OUT'),
    ('parsers/exciting', 'tests/data/parsers/exciting/GW/INFO.OUT'),
    ('parsers/vasp', 'tests/data/parsers/vasp/vasp.xml'),
    ('parsers/vasp', 'tests/data/parsers/vasp_compressed/vasp.xml.gz'),
    ('parsers/vaspoutcar', 'tests/data/parsers/vasp_outcar/OUTCAR'),
    ('parsers/fhi-aims', 'tests/data/parsers/fhi-aims/aims.out'),
    ('parsers/cp2k', 'tests/data/parsers/cp2k/si_bulk8.out'),
    ('parsers/crystal', 'tests/data/parsers/crystal/si.out'),
    ('parsers/cpmd', 'tests/data/parsers/cpmd/geo_output.out'),
    ('parsers/nwchem', 'tests/data/parsers/nwchem/single_point/output.out'),
    ('parsers/bigdft', 'tests/data/parsers/bigdft/n2_output.out'),
    ('parsers/wien2k', 'tests/data/parsers/wien2k/AlN/AlN_ZB.scf'),
    # ('parsers/band', 'tests/data/parsers/band_adf.out'),  # causes spglib to segfault
    ('parsers/gaussian', 'tests/data/parsers/gaussian/aniline.out'),
    ('parsers/abinit', 'tests/data/parsers/abinit/Fe.out'),
    ('parsers/quantumespresso', 'tests/data/parsers/quantum-espresso/benchmark.out'),
    ('parsers/orca', 'tests/data/parsers/orca/orca3dot2706823.out'),
    ('parsers/castep', 'tests/data/parsers/castep/BC2N-Pmm2-Raman.castep'),
    # ('parsers/dl-poly', 'tests/data/parsers/dl-poly/OUTPUT'), # timeout on Matid System Classification
    ('parsers/lib-atoms', 'tests/data/parsers/lib-atoms/gp.xml'),
    ('parsers/octopus', 'tests/data/parsers/octopus/stdout.txt'),
    ('parsers/phonopy', 'tests/data/parsers/phonopy/control.in'),
    ('parsers/gpaw', 'tests/data/parsers/gpaw/Fe2.gpw'),
    ('parsers/gpaw2', 'tests/data/parsers/gpaw2/H2_lcao.gpw2'),
    ('parsers/atk', 'tests/data/parsers/atk/Si2.nc'),
    ('parsers/gulp', 'tests/data/parsers/gulp/example6.got'),
    ('parsers/siesta', 'tests/data/parsers/siesta/Fe/out'),
    ('parsers/elk', 'tests/data/parsers/elk/Al/INFO.OUT'),
    ('parsers/elastic', 'dependencies/parsers/elastic/test/examples/2nd/INFO_ElaStic'),  # 70Mb file 2big4git
    ('parsers/turbomole', 'tests/data/parsers/turbomole/acrolein.out'),
    ('parsers/gamess', 'tests/data/parsers/gamess/exam01.out')
]

# We need to remove some cases with external mainfiles, which might not exist
# in all testing environments (e.g. in the nomad docker image)
fixed_parser_examples = []
for parser, mainfile in parser_examples:
    if os.path.exists(mainfile) or mainfile.startswith('tests'):
        fixed_parser_examples.append((parser, mainfile))
parser_examples = fixed_parser_examples


correct_num_output_files = 34


class TestLocalBackend(object):

    @pytest.fixture(scope='session')
    def meta_info(self):
        file_dir = os.path.dirname(os.path.abspath(nomad_meta_info.__file__))
        path = os.path.join(file_dir, 'all.nomadmetainfo.json')
        meta_info, _ = loadJsonFile(path)
        return meta_info

    @pytest.fixture(scope='function')
    def backend(self, meta_info):
        return LocalBackend(meta_info, debug=True)

    def test_meta_info(self, meta_info, no_warn):
        assert 'section_topology' in meta_info

    def test_section(self, backend, no_warn):
        g_index = backend.openSection('section_run')
        assert g_index == 0
        backend.addValue('program_name', 't0')
        backend.closeSection('section_run', 0)

        g_index = backend.openSection('section_run')
        assert g_index == 1

        g_index = backend.openSection('section_run')
        assert g_index == 2

        backend.addValue('program_name', 't1', 1)
        backend.addValue('program_name', 't2', 2)

        backend.closeSection('section_run', 1)
        backend.closeSection('section_run', 2)

        assert backend.get_sections('section_run') == [0, 1, 2]
        for i in range(0, 3):
            assert backend.get_value('program_name', i) == 't%d' % i

    def test_section_override(self, backend, no_warn):
        """ Test whether we can overwrite values already in the backend."""
        expected_value = ['Cl', 'Zn']
        backend.openSection('section_run')
        backend.openSection('section_system')
        backend.addArrayValues('atom_labels', np.array(['Al', 'Zn']))
        backend.addArrayValues('atom_labels', np.array(expected_value), override=True)
        backend.closeSection('section_system', 0)

        backend.closeSection('section_run', 0)
        output = StringIO()
        backend.write_json(output)
        assert backend.get_value('atom_labels').tolist() == expected_value

    def test_two_sections(self, backend, no_warn):
        g_index = backend.openSection('section_run')
        assert g_index == 0
        backend.addValue('program_name', 't0')
        backend.closeSection('section_run', 0)

        g_index = backend.openSection('section_entry_info')
        assert g_index == 0
        backend.addValue('parser_name', 'p0')
        backend.closeSection('section_entry_info', 0)

        assert backend.get_sections('section_run') == [0]
        assert backend.get_sections('section_entry_info') == [0]

        output = StringIO()
        backend.write_json(output)
        archive = json.loads(output.getvalue())
        assert 'section_run' in archive
        assert 'section_entry_info' in archive

    def test_subsection(self, backend: LocalBackend, no_warn):
        backend.openSection('section_run')
        backend.openSection('section_method')
        backend.closeSection('section_method', -1)

        backend.openSection('section_method')
        backend.closeSection('section_method', -1)

        backend.openSection('section_run')
        backend.closeSection('section_run', 0)
        backend.closeSection('section_run', 1)

        backend.openSection('section_method')
        backend.closeSection('section_method', -1)

        runs = backend.data['section_run']
        assert len(runs) == 2
        assert len(runs[0]['section_method']) == 2
        assert len(runs[1]['section_method']) == 1

    def test_context(self, backend: LocalBackend, no_warn):
        backend.openSection('section_run')
        backend.openSection('section_method')
        backend.closeSection('section_method', -1)
        backend.closeSection('section_run', -1)

        backend.openSection('section_run')
        backend.closeSection('section_run', -1)

        backend.openContext('/section_run/0')
        backend.addValue('program_name', 't1')
        backend.closeContext('/section_run/0')

        backend.openContext('/section_run/1')
        backend.addValue('program_name', 't2')
        backend.closeContext('/section_run/1')

        backend.openContext('/section_run/0/section_method/0')
        backend.closeContext('/section_run/0/section_method/0')

        runs = backend.data['section_run']
        assert runs[0]['program_name'] == 't1'
        assert runs[1]['program_name'] == 't2'

    def test_multi_context(self, backend: LocalBackend, no_warn):
        backend.openSection('section_run')
        backend.closeSection('section_run', -1)

        backend.openContext('/section_run/0')
        backend.openSection('section_method')
        backend.closeSection('section_method', -1)
        backend.closeContext('/section_run/0')

        backend.openContext('/section_run/0')
        backend.openSection('section_method')
        backend.closeSection('section_method', -1)
        backend.closeContext('/section_run/0')

        assert len(backend.data['section_method']) == 1

    def test_bad_context(self, backend: LocalBackend, no_warn):
        try:
            backend.openContext('section_run/0')
            assert False
        except BadContextURI:
            pass

        try:
            backend.openContext('dsfds')
            assert False
        except BadContextURI:
            pass


def create_reference(data, pretty):
    if (pretty):
        return json.dumps(data, indent=2)
    else:
        return json.dumps(data, separators=(',', ':'))


@pytest.mark.parametrize("pretty", [False, True])
def test_stream_generator(pretty, no_warn):
    example_data = [
        {
            'key1': 'value',
            'key2': 1
        },
        {
            'key': {
                'key': 'value'
            }
        }
    ]

    out = StringIO()
    writer = JSONStreamWriter(out, pretty=pretty)
    writer.open_array()
    writer.open_object()
    writer.key('key1')
    writer.value('value')
    writer.key('key2')
    writer.value(1)
    writer.close_object()
    writer.open_object()
    writer.key('key')
    writer.open_object()
    writer.key('key')
    writer.value('value')
    writer.close_object()
    writer.close_object()
    writer.close_array()
    writer.close()

    assert create_reference(example_data, pretty) == out.getvalue()


def assert_parser_result(backend):
    status, errors = backend.status
    assert status == 'ParseSuccess'
    assert errors is None or len(errors) == 0


def assert_parser_dir_unchanged(previous_wd, current_wd):
    """Assert working directory has not been changed from parser."""
    assert previous_wd == current_wd


def run_parser(parser_name, mainfile):
    parser = parser_dict[parser_name]
    result = parser.run(mainfile, logger=utils.get_logger(__name__))
    return add_calculation_info(result, parser_name=parser_name)


@pytest.fixture
def parsed_vasp_example() -> LocalBackend:
    return run_parser(
        'parsers/vasp', 'dependencies/parsers/vasp/test/examples/xml/perovskite.xml')


@pytest.fixture
def parsed_template_example() -> LocalBackend:
    return run_parser(
        'parsers/template', 'tests/data/parsers/template.json')


# Function used by normalizer tests.
def parse_file(parser_name_and_mainfile) -> LocalBackend:
    parser_name, mainfile = parser_name_and_mainfile
    return run_parser(parser_name, mainfile)


@pytest.fixture(params=parser_examples, ids=lambda spec: '%s-%s' % spec)
def parsed_example(request) -> LocalBackend:
    parser_name, mainfile = request.param
    result = run_parser(parser_name, mainfile)
    return result


def add_calculation_info(backend: LocalBackend, **kwargs) -> LocalBackend:
    backend.openNonOverlappingSection('section_entry_info')
    backend.addValue('upload_id', 'test_upload_id')
    backend.addValue('calc_id', 'test_calc_id')
    backend.addValue('calc_hash', 'test_calc_hash')
    backend.addValue('mainfile', 'test/mainfile.txt')
    for key, value in kwargs.items():
        backend.addValue(key, value)
    backend.closeNonOverlappingSection('section_entry_info')
    return backend


@pytest.mark.parametrize('parser_name, mainfile', parser_examples)
def test_parser(parser_name, mainfile):
    previous_wd = os.getcwd()  # Get Working directory before parsing.
    parsed_example = run_parser(parser_name, mainfile)
    assert_parser_result(parsed_example)
    # Check that cwd has not changed.
    assert_parser_dir_unchanged(previous_wd, current_wd=os.getcwd())


def test_match(raw_files, no_warn):
    example_upload_id = 'example_upload_id'
    upload_files = files.StagingUploadFiles(example_upload_id, create=True, is_authorized=lambda: True)
    upload_files.add_rawfiles('tests/data/parsers')

    count = 0
    for mainfile in upload_files.raw_file_manifest():
        parser = match_parser(mainfile, upload_files)
        if parser is not None:
            count += 1

    assert count == correct_num_output_files
