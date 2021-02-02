#
# Copyright The NOMAD Authors.
#
# This file is part of NOMAD. See https://nomad-lab.eu for further info.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

from typing import Generator, Tuple
import pytest
from datetime import datetime
import os.path
import re
import shutil

from nomad import utils, infrastructure, config, datamodel
from nomad.archive import read_partial_archive_from_mongo
from nomad.files import UploadFiles, StagingUploadFiles, PublicUploadFiles
from nomad.processing import Upload, Calc
from nomad.processing.base import task as task_decorator, FAILURE, SUCCESS

from tests.test_search import assert_search_upload
from tests.test_files import assert_upload_files
from tests.app.flask.conftest import client, oasis_central_nomad_client, session_client  # pylint: disable=unused-import
from tests.app.conftest import other_test_user_auth, test_user_auth  # pylint: disable=unused-import


def test_send_mail(mails, monkeypatch):
    infrastructure.send_mail('test name', 'test@email.de', 'test message', 'subject')

    for message in mails.messages:
        assert re.search(r'test message', message.data.decode('utf-8')) is not None


@pytest.fixture(scope='function', autouse=True)
def mongo_forall(mongo):
    pass


@pytest.fixture
def uploaded_id_with_warning(raw_files) -> Generator[Tuple[str, str], None, None]:
    example_file = 'tests/data/proc/examples_with_warning_template.zip'
    example_upload_id = os.path.basename(example_file).replace('.zip', '')

    yield example_upload_id, example_file


def run_processing(uploaded: Tuple[str, str], test_user, **kwargs) -> Upload:
    uploaded_id, uploaded_path = uploaded
    upload = Upload.create(
        upload_id=uploaded_id, user=test_user, upload_path=uploaded_path, **kwargs)
    upload.upload_time = datetime.utcnow()

    assert upload.tasks_status == 'RUNNING'
    assert upload.current_task == 'uploading'

    upload.process_upload()  # pylint: disable=E1101
    upload.block_until_complete(interval=.01)

    return upload


def assert_processing(upload: Upload, published: bool = False):
    assert not upload.tasks_running
    assert upload.current_task == 'cleanup'
    assert upload.upload_id is not None
    assert len(upload.errors) == 0
    assert upload.tasks_status == SUCCESS

    upload_files = UploadFiles.get(upload.upload_id, is_authorized=lambda: True)
    if published:
        assert isinstance(upload_files, PublicUploadFiles)
    else:
        assert isinstance(upload_files, StagingUploadFiles)

    for calc in Calc.objects(upload_id=upload.upload_id):
        assert calc.parser is not None
        assert calc.mainfile is not None
        assert calc.tasks_status == SUCCESS
        assert calc.metadata['published'] == published

        with upload_files.read_archive(calc.calc_id) as archive:
            calc_archive = archive[calc.calc_id]
            assert 'section_run' in calc_archive
            assert 'section_metadata' in calc_archive
            assert 'processing_logs' in calc_archive

            has_test_event = False
            for log_data in calc_archive['processing_logs']:
                for key in ['event', 'calc_id', 'level']:
                    key in log_data
                has_test_event = has_test_event or log_data['event'] == 'a test log entry'

            assert has_test_event
        assert len(calc.errors) == 0

        archive = read_partial_archive_from_mongo(calc.calc_id)
        assert archive.section_metadata is not None
        assert archive.section_workflow.calculation_result_ref \
            .single_configuration_calculation_to_system_ref.atom_labels is not None

        with upload_files.raw_file(calc.mainfile) as f:
            f.read()

        entry_metadata = calc.entry_metadata(upload_files)

        for path in entry_metadata.files:
            with upload_files.raw_file(path) as f:
                f.read()

        # check some domain metadata
        assert entry_metadata.n_atoms > 0
        assert len(entry_metadata.atoms) > 0

        assert upload.get_calc(calc.calc_id) is not None

        upload_files.close()


def test_processing(processed, no_warn, mails, monkeypatch):
    assert_processing(processed)

    assert len(mails.messages) == 1
    assert re.search(r'Processing completed', mails.messages[0].data.decode('utf-8')) is not None


def test_processing_two_runs(test_user, proc_infra):
    processed = run_processing(
        ('test_upload_id', 'tests/data/proc/examples_template_tworuns.zip',), test_user)
    assert_processing(processed)


def test_processing_with_large_dir(test_user, proc_infra):
    upload_path = 'tests/data/proc/examples_large_dir.zip'
    upload_id = upload_path[:-4]
    upload = run_processing((upload_id, upload_path), test_user)
    for calc in upload.calcs:
        assert len(calc.warnings) == 1


def test_publish(non_empty_processed: Upload, no_warn, internal_example_user_metadata, monkeypatch):
    processed = non_empty_processed
    processed.compress_and_set_metadata(internal_example_user_metadata)

    additional_keys = ['with_embargo']

    processed.publish_upload()
    try:
        processed.block_until_complete(interval=.01)
    except Exception:
        pass

    with processed.entries_metadata(internal_example_user_metadata) as entries:
        assert_upload_files(processed.upload_id, entries, PublicUploadFiles, published=True)
        assert_search_upload(entries, additional_keys, published=True)

    assert_processing(Upload.get(processed.upload_id, include_published=True), published=True)


def test_publish_directly(non_empty_uploaded, test_user, proc_infra, no_warn, monkeypatch):
    processed = run_processing(non_empty_uploaded, test_user, publish_directly=True)

    with processed.entries_metadata() as entries:
        assert_upload_files(processed.upload_id, entries, PublicUploadFiles, published=True)
        assert_search_upload(entries, [], published=True)

    assert_processing(Upload.get(processed.upload_id, include_published=True), published=True)


def test_republish(non_empty_processed: Upload, no_warn, internal_example_user_metadata, monkeypatch):
    processed = non_empty_processed
    processed.compress_and_set_metadata(internal_example_user_metadata)

    additional_keys = ['with_embargo']

    processed.publish_upload()
    processed.block_until_complete(interval=.01)
    assert Upload.get('examples_template') is not None

    processed.publish_upload()
    processed.block_until_complete(interval=.01)

    with processed.entries_metadata(internal_example_user_metadata) as entries:
        assert_upload_files(processed.upload_id, entries, PublicUploadFiles, published=True)
        assert_search_upload(entries, additional_keys, published=True)


def test_publish_failed(
        non_empty_uploaded: Tuple[str, str], internal_example_user_metadata, test_user,
        monkeypatch, proc_infra):

    mock_failure(Calc, 'parsing', monkeypatch)

    processed = run_processing(non_empty_uploaded, test_user)
    processed.compress_and_set_metadata(internal_example_user_metadata)

    additional_keys = ['with_embargo']

    processed.publish_upload()
    try:
        processed.block_until_complete(interval=.01)
    except Exception:
        pass

    with processed.entries_metadata(internal_example_user_metadata) as entries:
        assert_search_upload(entries, additional_keys, published=True, processed=False)


@pytest.mark.timeout(config.tests.default_timeout)
def test_oasis_upload_processing(proc_infra, oasis_example_uploaded: Tuple[str, str], test_user, no_warn):
    uploaded_id, uploaded_path = oasis_example_uploaded

    # create a dataset to force dataset joining of one of the datasets in the example
    # upload
    datamodel.Dataset(
        dataset_id='cn_dataset_2', name='dataset_2_name',
        user_id=test_user.user_id).a_mongo.save()

    upload = Upload.create(
        upload_id=uploaded_id, user=test_user, upload_path=uploaded_path)
    upload.from_oasis = True
    upload.oasis_deployment_id = 'an_oasis_id'

    assert upload.tasks_status == 'RUNNING'
    assert upload.current_task == 'uploading'

    upload.process_upload()  # pylint: disable=E1101
    upload.block_until_complete(interval=.01)

    assert upload.published
    assert upload.from_oasis
    assert upload.oasis_deployment_id == 'an_oasis_id'
    assert str(upload.upload_time) == '2020-01-01 00:00:00'
    assert_processing(upload, published=True)
    calc = Calc.objects(upload_id='oasis_upload_id').first()
    assert calc.calc_id == 'test_calc_id'
    assert calc.metadata['published']
    assert calc.metadata['datasets'] == ['oasis_dataset_1', 'cn_dataset_2']


@pytest.fixture(scope='function')
def oasis_publishable_upload(
        client, proc_infra, non_empty_uploaded, oasis_central_nomad_client, monkeypatch,
        other_test_user):

    upload = run_processing(non_empty_uploaded, other_test_user)
    upload.publish_upload()
    upload.block_until_complete(interval=.01)

    # create a dataset to also test this aspect of oasis uploads
    calc = Calc.objects(upload_id=upload.upload_id).first()
    datamodel.Dataset(
        dataset_id='dataset_id', name='dataset_name',
        user_id=other_test_user.user_id).a_mongo.save()
    calc.metadata['datasets'] = ['dataset_id']
    calc.save()

    cn_upload_id = 'cn_' + upload.upload_id

    # We need to alter the ids, because we do this test by uploading to the same NOMAD
    def normalize_oasis_upload_metadata(upload_id, metadata):
        for entry in metadata['entries'].values():
            entry['calc_id'] = utils.create_uuid()
        upload_id = 'cn_' + upload_id
        return upload_id, metadata

    monkeypatch.setattr(
        'nomad.processing.data._normalize_oasis_upload_metadata',
        normalize_oasis_upload_metadata)

    def put(url, headers, data):
        return client.put(url, headers=headers, data=data.read())

    monkeypatch.setattr(
        'requests.put', put)
    monkeypatch.setattr(
        'nomad.config.oasis.central_nomad_api_url', '/api')

    return cn_upload_id, upload


@pytest.mark.timeout(config.tests.default_timeout)
def test_publish_from_oasis(oasis_publishable_upload, other_test_user, no_warn):
    cn_upload_id, upload = oasis_publishable_upload

    upload.publish_from_oasis()
    upload.block_until_complete()
    assert_processing(upload, published=True)

    cn_upload = Upload.objects(upload_id=cn_upload_id).first()
    cn_upload.block_until_complete()
    assert_processing(cn_upload, published=True)
    assert cn_upload.user_id == other_test_user.user_id
    assert len(cn_upload.published_to) == 0
    assert cn_upload.from_oasis
    assert cn_upload.oasis_deployment_id == config.meta.deployment_id
    assert upload.published_to[0] == config.oasis.central_nomad_deployment_id
    cn_calc = Calc.objects(upload_id=cn_upload_id).first()
    calc = Calc.objects(upload_id=upload.upload_id).first()
    assert cn_calc.calc_id != calc.calc_id
    assert cn_calc.metadata['datasets'] == ['dataset_id']
    assert datamodel.Dataset.m_def.a_mongo.objects().count() == 1


@pytest.mark.timeout(config.tests.default_timeout)
def test_processing_with_warning(proc_infra, test_user, with_warn):
    example_file = 'tests/data/proc/examples_with_warning_template.zip'
    example_upload_id = os.path.basename(example_file).replace('.zip', '')

    upload = run_processing((example_upload_id, example_file), test_user)
    assert_processing(upload)


@pytest.mark.timeout(config.tests.default_timeout)
def test_process_non_existing(proc_infra, test_user, with_error):
    upload = run_processing(('__does_not_exist', '__does_not_exist'), test_user)

    assert not upload.tasks_running
    assert upload.current_task == 'extracting'
    assert upload.tasks_status == FAILURE
    assert len(upload.errors) > 0


@pytest.mark.timeout(config.tests.default_timeout)
@pytest.mark.parametrize('with_failure', [None, 'before', 'after', 'not-matched'])
def test_re_processing(published: Upload, internal_example_user_metadata, monkeypatch, with_failure):
    if with_failure == 'not-matched':
        monkeypatch.setattr('nomad.config.reprocess_unmatched', False)

    if with_failure == 'before':
        calc = published.all_calcs(0, 1).first()
        calc.tasks_status = FAILURE
        calc.errors = ['example error']
        calc.save()
        assert published.failed_calcs > 0

    assert published.published
    assert published.upload_files.to_staging_upload_files() is None

    old_upload_time = published.last_update
    first_calc = published.all_calcs(0, 1).first()
    old_calc_time = first_calc.metadata['last_processing']

    with published.upload_files.read_archive(first_calc.calc_id) as archive:
        archive[first_calc.calc_id]['processing_logs']

    old_archive_files = list(
        archive_file
        for archive_file in os.listdir(published.upload_files.os_path)
        if 'archive' in archive_file)

    with published.entries_metadata(internal_example_user_metadata) as entries_generator:
        entries = list(entries_generator)

    if with_failure != 'not-matched':
        for archive_file in old_archive_files:
            with open(published.upload_files.join_file(archive_file).os_path, 'wt') as f:
                f.write('')

    if with_failure == 'after':
        raw_files = 'tests/data/proc/examples_template_unparsable.zip'
    elif with_failure == 'not-matched':
        monkeypatch.setattr('nomad.parsing.artificial.TemplateParser.is_mainfile', lambda *args, **kwargs: False)
        raw_files = 'tests/data/proc/examples_template_different_atoms.zip'
    else:
        raw_files = 'tests/data/proc/examples_template_different_atoms.zip'
    shutil.copyfile(
        raw_files, published.upload_files.join_file('raw-restricted.plain.zip').os_path)

    # reprocess
    monkeypatch.setattr('nomad.config.meta.version', 're_process_test_version')
    monkeypatch.setattr('nomad.config.meta.commit', 're_process_test_commit')
    published.reset()
    published.re_process_upload()
    try:
        published.block_until_complete(interval=.01)
    except Exception:
        pass

    published.reload()
    first_calc.reload()

    # assert new process time
    if with_failure != 'not-matched':
        assert published.last_update > old_upload_time
        assert first_calc.metadata['last_processing'] > old_calc_time

    # assert new process version
    if with_failure != 'not-matched':
        assert first_calc.metadata['nomad_version'] == 're_process_test_version'
        assert first_calc.metadata['nomad_commit'] == 're_process_test_commit'

    # assert changed archive files
    if with_failure == 'after':
        with published.upload_files.read_archive(first_calc.calc_id) as archive:
            assert list(archive[first_calc.calc_id].keys()) == ['processing_logs', 'section_metadata']

    else:
        with published.upload_files.read_archive(first_calc.calc_id) as archive:
            assert len(archive[first_calc.calc_id]) > 2  # contains more then logs and metadata

    # assert maintained user metadata (mongo+es)
    assert_upload_files(published.upload_id, entries, PublicUploadFiles, published=True)
    assert_search_upload(entries, published=True)
    if with_failure not in ['after', 'not-matched']:
        assert_processing(Upload.get(published.upload_id, include_published=True), published=True)

    # assert changed calc metadata (mongo)
    entry_metadata = first_calc.entry_metadata(published.upload_files)
    if with_failure not in ['after', 'not-matched']:
        assert entry_metadata.atoms[0] == 'H'
    elif with_failure == 'not-matched':
        assert entry_metadata.atoms[0] == 'Si'
    else:
        assert entry_metadata.atoms == []


@pytest.mark.parametrize('publish,old_staging', [
    (False, False), (True, True), (True, False)])
def test_re_process_staging(non_empty_processed, publish, old_staging):
    upload = non_empty_processed

    if publish:
        upload.publish_upload()
        try:
            upload.block_until_complete(interval=.01)
        except Exception:
            pass

        if old_staging:
            StagingUploadFiles(upload.upload_id, create=True)

    upload.reset()
    upload.re_process_upload()
    try:
        upload.block_until_complete(interval=.01)
    except Exception:
        pass

    assert_processing(upload, published=publish)
    if publish:
        with pytest.raises(KeyError):
            StagingUploadFiles(upload.upload_id)
    else:
        StagingUploadFiles(upload.upload_id)


@pytest.mark.timeout(config.tests.default_timeout)
@pytest.mark.parametrize('with_failure', [None, 'before', 'after'])
def test_re_pack(published: Upload, monkeypatch, with_failure):
    upload_id = published.upload_id
    calc = Calc.objects(upload_id=upload_id).first()
    assert calc.metadata['with_embargo']
    calc.metadata['with_embargo'] = False
    calc.save()

    published.re_pack()
    try:
        published.block_until_complete(interval=.01)
    except Exception:
        pass

    upload_files = PublicUploadFiles(upload_id)
    for raw_file in upload_files.raw_file_manifest():
        with upload_files.raw_file(raw_file) as f:
            f.read()

    for calc in Calc.objects(upload_id=upload_id):
        with upload_files.read_archive(calc.calc_id) as archive:
            archive[calc.calc_id].to_dict()


def mock_failure(cls, task, monkeypatch):
    def mock(self):
        raise Exception('fail for test')

    mock.__name__ = task
    mock = task_decorator(mock)

    monkeypatch.setattr('nomad.processing.data.%s.%s' % (cls.__name__, task), mock)


@pytest.mark.parametrize('task', ['extracting', 'parse_all', 'cleanup', 'parsing'])
@pytest.mark.timeout(config.tests.default_timeout)
def test_task_failure(monkeypatch, uploaded, task, proc_infra, test_user, with_error):
    upload_id, _ = uploaded
    # mock the task method to through exceptions
    if hasattr(Upload, task):
        cls = Upload
    elif hasattr(Calc, task):
        cls = Calc
    else:
        assert False

    mock_failure(cls, task, monkeypatch)

    # run the test
    upload = run_processing(uploaded, test_user)

    assert not upload.tasks_running

    if task != 'parsing':
        assert upload.tasks_status == FAILURE
        assert upload.current_task == task
        assert len(upload.errors) > 0
    else:
        # there is an empty example with no calcs, even if past parsing_all task
        utils.get_logger(__name__).error('fake')
        if upload.total_calcs > 0:  # pylint: disable=E1101
            assert upload.tasks_status == SUCCESS
            assert upload.current_task == 'cleanup'
            assert len(upload.errors) == 0
            for calc in upload.all_calcs(0, 100):  # pylint: disable=E1101
                assert calc.tasks_status == FAILURE
                assert calc.current_task == 'parsing'
                assert len(calc.errors) > 0

    calc = Calc.objects(upload_id=upload_id).first()
    if calc is not None:
        with upload.upload_files.read_archive(calc.calc_id) as archive:
            calc_archive = archive[calc.calc_id]
            assert 'section_metadata' in calc_archive
            assert calc_archive['section_metadata']['dft']['code_name'] not in [
                config.services.unavailable_value, config.services.not_processed_value]
            assert 'processing_logs' in calc_archive
            if task != 'parsing':
                assert 'section_run' in calc_archive


# TODO timeout
# consume_ram, segfault, and exit are not testable with the celery test worker
@pytest.mark.parametrize('failure', ['exception'])
def test_malicious_parser_task_failure(proc_infra, failure, test_user):
    example_file = 'tests/data/proc/chaos_%s.zip' % failure
    example_upload_id = os.path.basename(example_file).replace('.zip', '')

    upload = run_processing((example_upload_id, example_file), test_user)

    assert not upload.tasks_running
    assert upload.current_task == 'cleanup'
    assert len(upload.errors) == 0
    assert upload.tasks_status == SUCCESS

    calcs = Calc.objects(upload_id=upload.upload_id)
    assert calcs.count() == 1
    calc = next(calcs)
    assert not calc.tasks_running
    assert calc.tasks_status == FAILURE
    assert len(calc.errors) == 1


def test_ems_data(proc_infra, test_user):
    upload = run_processing(('test_ems_upload', 'tests/data/proc/examples_ems.zip'), test_user)

    additional_keys = [
        'ems.method', 'ems.data_type', 'formula', 'n_atoms', 'atoms', 'ems.chemical', 'ems.origin_time']
    assert upload.total_calcs == 3
    assert len(upload.calcs) == 3

    with upload.entries_metadata() as entries:
        assert_upload_files(upload.upload_id, entries, StagingUploadFiles, published=False)
        assert_search_upload(entries, additional_keys, published=False)


def test_qcms_data(proc_infra, test_user):
    upload = run_processing(('test_qcms_upload', 'tests/data/proc/examples_qcms.zip'), test_user)

    additional_keys = ['qcms.chemical', 'formula']
    assert upload.total_calcs == 1
    assert len(upload.calcs) == 1

    with upload.entries_metadata() as entries:
        assert_upload_files(upload.upload_id, entries, StagingUploadFiles, published=False)
        assert_search_upload(entries, additional_keys, published=False)


def test_read_metadata_from_file(proc_infra, test_user, other_test_user):
    upload = run_processing(
        ('test_upload', 'tests/data/proc/examples_with_metadata_file.zip'), test_user)

    calcs = Calc.objects(upload_id=upload.upload_id)
    calcs = sorted(calcs, key=lambda calc: calc.mainfile)

    comment = ['Calculation 1 of 2', 'Calculation 2 of 2', None]
    with_embargo = [True, False, True]
    references = [['http://test'], ['http://ttest'], None]
    coauthors = [[other_test_user], [], []]

    for i in range(len(calcs)):
        entry_metadata = calcs[i].entry_metadata(upload.upload_files)
        assert entry_metadata.comment == comment[i]
        assert entry_metadata.with_embargo == with_embargo[i]
        assert entry_metadata.references == references[i]
        entry_coauthors = [a.m_proxy_resolve() for a in entry_metadata.coauthors]
        for j in range(len(entry_coauthors)):
            assert entry_coauthors[j].user_id == coauthors[i][j].user_id
            assert entry_coauthors[j].username == coauthors[i][j].username
            assert entry_coauthors[j].email == coauthors[i][j].email
            assert entry_coauthors[j].first_name == coauthors[i][j].first_name
            assert entry_coauthors[j].last_name == coauthors[i][j].last_name


@pytest.mark.parametrize('user,uploader', [
    ('admin_user', 'other_test_user'),
    ('test_user', 'test_user')
])
def test_read_adminmetadata_from_file(proc_infra, test_user, other_test_user, admin_user, user, uploader):
    def user_from_name(user_name):
        if user_name == 'test_user':
            return test_user
        if user_name == 'other_test_user':
            return other_test_user
        if user_name == 'admin_user':
            return admin_user

    user = user_from_name(user)
    uploader = user_from_name(uploader)

    upload = run_processing(
        ('test_upload', 'tests/data/proc/examples_with_adminmetadata.zip'), user)
    calc = Calc.objects(upload_id=upload.upload_id).first()
    assert calc.metadata['uploader'] == uploader.user_id
