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

import pytest
import os
import time
from typing import List, Dict, Any, Iterable
from tests.utils import build_url
from tests.test_files import assert_upload_files
from tests.test_search import assert_search_upload
from nomad import config, files, infrastructure
from nomad.processing import Upload, Calc, SUCCESS
from nomad.files import UploadFiles, PublicUploadFiles
from nomad.app.v1.routers.auth import generate_upload_token
from nomad.datamodel import EntryMetadata

'''
These are the tests for all API operations below ``entries``. The tests are organized
using the following type of methods: fixtures, ``perfrom_*_test``, ``assert_*``, and
``test_*``. While some ``test_*`` methods test individual API operations, some
test methods will test multiple API operations that use common aspects like
supporting queries, pagination, or the owner parameter. The test methods will use
``perform_*_test`` methods as an parameter. Similarely, the ``assert_*`` methods allow
to assert for certain aspects in the responses.
'''


def perform_get(client, base_url, user_auth=None, **query_args):
    headers = user_auth
    response = client.get(build_url(base_url, query_args), headers=headers)
    return response


def perform_post_uploads(client, mode, file, user_auth=None, token=None, **query_args):
    ''' Posts a new upload. '''
    if mode == 'local_path':
        query_args.update(local_path=file)
    if token:
        query_args.update(token=token)
    url = build_url('uploads', query_args)
    if mode == 'multipart':
        with open(file, 'rb') as f:
            response = client.post(
                url, files={'file': f}, headers=user_auth)
    elif mode == 'stream':
        with open(file, 'rb') as f:
            response = client.post(url, data=f.read(), headers=user_auth)
    elif mode == 'local_path':
        response = client.post(url, headers=user_auth)
    else:
        assert False, 'Invalid value for mode provided'

    return response


def perform_post_uploads_id_action(client, user_auth, upload_id, action, **query_args):
    return client.post(
        build_url(f'uploads/{upload_id}/action/{action}', query_args), headers=user_auth)


def perform_delete_uploads(client, upload_id, user_auth=None, **query_args):
    headers = user_auth
    response = client.delete(build_url(f'uploads/{upload_id}', query_args), headers=headers)
    return response


def assert_upload(response_json, **kwargs):
    data = response_json['data']
    assert 'upload_id' in response_json
    assert 'upload_id' in data
    assert 'create_time' in data

    for key, value in kwargs.items():
        assert data.get(key, None) == value
    return data


def assert_upload_does_not_exist(client, upload_id: str, user_auth):
    block_until_completed(client, upload_id, user_auth)

    response = perform_get(client, 'uploads/{upload_id}', user_auth)
    assert response.status_code == 404

    assert Upload.objects(upload_id=upload_id).first() is None
    assert Calc.objects(upload_id=upload_id).count() is 0

    mongo_db = infrastructure.mongo_client[config.mongo.db_name]
    mongo_collection = mongo_db['archive']
    assert mongo_collection.find({}).count() == 0

    upload_files = UploadFiles.get(upload_id)
    assert upload_files is None or isinstance(upload_files, PublicUploadFiles)


def assert_uploads(
        client, user_auth, test_case: str, query_params={},
        expected_status_code=200, expected_upload_ids=None, expected_pagination={}):
    response = perform_get(client, 'uploads', user_auth=user_auth, **query_params)
    assert response.status_code == expected_status_code
    if expected_status_code == 200:
        response_json = response.json()
        response_data = response_json['data']

        if expected_upload_ids is not None:
            assert len(response_data) == len(expected_upload_ids), (
                f'Wrong number of records returned, expected {len(expected_upload_ids)}, got {len(response_data)}')
            for ind, upload in enumerate(response_data):
                upload_id = upload['upload_id']
                assert upload_id == expected_upload_ids[ind], f'Test case {test_case} failed - wrong upload_id list returned.'

        assert_pagination(response_json['pagination'], expected_pagination)


def assert_processing(client, upload_id, user_auth, check_search=True, check_files=True, published=False):
    response_data = block_until_completed(client, upload_id, user_auth)

    assert len(response_data['tasks']) == 4
    assert response_data['tasks_status'] == SUCCESS
    assert response_data['current_task'] == 'cleanup'
    assert not response_data['process_running']

    response = perform_get(client, f'uploads/{upload_id}/entries', user_auth)
    assert response.status_code == 200
    response_json = response.json()
    response_data = response_json['data']
    for entry in response_json['data']:
        assert entry['tasks_status'] == SUCCESS
        assert entry['current_task'] == 'archiving'
        assert len(entry['tasks']) == 3
        assert response_json['pagination']['total'] < response_json['pagination']['page_size']

    entries = get_upload_entries_metadata(response_data)
    if check_files:
        expected_file_class = files.PublicUploadFiles if published else files.StagingUploadFiles
        assert_upload_files(upload_id, entries, expected_file_class)
    if check_search:
        assert_search_upload(entries, additional_keys=['atoms', 'dft.system'])


def assert_publish(
        client, user_auth, upload_id, proc_infra, expected_status_code=200, **query_args):
    '''
    Attempts to publish the given upload and check that it is successful (unless failure
    is expected).
    '''
    response = client.get('uploads/%s' % upload_id, headers=user_auth)
    upload = assert_upload(response.json())

    # Api call to actually publish the upload
    response = perform_post_uploads_id_action(client, user_auth, upload_id, 'publish', **query_args)

    assert response.status_code == expected_status_code
    if expected_status_code == 200:
        upload = assert_upload(response.json())
        assert upload['current_process'] == 'publish_upload'
        assert upload['process_running']

        assert_gets_published(client, upload_id, user_auth, **query_args)


def assert_gets_published(client, upload_id, user_auth, from_oasis=False, **query_args):
    with_embargo = query_args.get('with_embargo', True)
    embargo_length = query_args.get('embargo_length', 36)

    block_until_completed(client, upload_id, user_auth)

    upload_proc = Upload.objects(upload_id=upload_id).first()
    assert upload_proc is not None
    assert upload_proc.published is True
    assert upload_proc.from_oasis == from_oasis
    if with_embargo:
        assert upload_proc.embargo_length == embargo_length

    with upload_proc.entries_metadata() as entries:
        for entry in entries:
            assert entry.with_embargo == with_embargo

    assert_upload_files(upload_id, entries, files.PublicUploadFiles, published=True)
    assert_search_upload(entries, additional_keys=['with_embargo'], published=True)


def assert_entry_content(entry, **kwargs):
    ''' Checks the content of a returned entry dictionary. '''
    assert 'upload_id' in entry
    assert 'entry_id' in entry
    assert 'calc_id' not in entry
    assert 'create_time' in entry
    assert not entry['process_running'] and not entry['tasks_running']
    for key, value in kwargs.items():
        assert entry.get(key, None) == value


def assert_entry(client, user_auth, upload_id, entry_id, expected_status_code=200):
    ''' Fetches an entry via a call to uploads/{upload_id}/entries/{entry_id} and checks it.'''
    response = perform_get(client, f'uploads/{upload_id}/entries/{entry_id}', user_auth)
    assert response.status_code == expected_status_code
    if expected_status_code == 200:
        response_json = response.json()
        assert response_json['entry_id'] == entry_id
        response_data = response_json['data']
        assert_entry_content(response_data)


def assert_entries(
        client, user_auth, upload_id, query_args={},
        expected_status_code=200,
        expected_data_len=None,
        expected_response={},
        expected_pagination={}):
    '''
    Fetches the entries for a specific upload, by calling uploads/{upload_id}/entries,
    with the provided query paramters, and checks the result.
    '''
    response = perform_get(client, f'uploads/{upload_id}/entries', user_auth, **query_args)
    assert response.status_code == expected_status_code
    if expected_status_code == 200:
        response_json = response.json()
        response_data = response_json['data']

        if expected_data_len is not None:
            assert len(response_data) == expected_data_len

        for entry in response_data:
            assert_entry_content(entry)
            entry_id = entry['entry_id']
            assert_entry(client, user_auth, upload_id, entry_id)

        for key, value in expected_response.items():
            assert response_json.get(key, None) == value

        pagination = response_json['pagination']
        assert_pagination(pagination, expected_pagination)

        return response_data
    return None


def assert_pagination(pagination, expected_pagination):
    ''' Checks that the contents of `paginaion` matches what is expected. '''
    for key, value in expected_pagination.items():
        if value is None:
            assert key not in pagination, f'No value expected for {key}, got {pagination[key]}'
        elif value is Any:
            assert pagination.get(key) is not None, f'Value expected for {key}, got None'
        else:
            assert pagination.get(key) == value, f'For {key} we expecte {value}, but got {pagination.get(key)}'


def block_until_completed(client, upload_id: str, user_auth):
    ''' Blocks until the processing of the given upload is finished. '''
    start_time = time.time()
    while time.time() - start_time < config.tests.default_timeout:
        time.sleep(0.1)
        response = client.get('uploads/%s' % upload_id, headers=user_auth)
        if response.status_code == 200:
            response_json = response.json()
            assert_upload(response_json)
            response_data = response_json['data']
            if not response_data['process_running'] and not response_data['tasks_running']:
                return response_data
        elif response.status_code == 404:
            return None
        else:
            raise Exception(
                'unexpected status code while blocking for upload processing: %s' %
                str(response.status_code))
    raise Exception('Timed out while waiting for upload processing to finish')


def get_upload_entries_metadata(entries: List[Dict[str, Any]]) -> Iterable[EntryMetadata]:
    ''' Create a iterable of :class:`EntryMetadata` from a API upload json record. '''
    return [
        EntryMetadata(domain='dft', calc_id=entry['entry_id'], mainfile=entry['mainfile'])
        for entry in entries]


@pytest.fixture(scope='function')
def slow_processing(monkeypatch):
    ''' Slow down processing to mitigate race conditions. '''
    old_cleanup = Upload.cleanup

    def slow_cleanup(self):
        time.sleep(2)
        old_cleanup(self)

    monkeypatch.setattr('nomad.processing.data.Upload.cleanup', slow_cleanup)
    yield True
    monkeypatch.setattr('nomad.processing.data.Upload.cleanup', old_cleanup)


def test_get_uploads_empty_list(client, mongo, test_user_auth):
    ''' Gets user's uploads, without having submitted anything -> empty list. '''
    response = perform_get(client, 'uploads', test_user_auth)
    assert response.status_code == 200
    assert len(response.json()['data']) == 0


def test_get_uploads(client, mongo, proc_infra, slow_processing, test_user_auth, non_empty_example_upload):
    ''' Tests various ways of getting the uppload with different filtering. '''
    upload_id_to_name = {}

    # Upload #1 - published
    response = perform_post_uploads(client, 'stream', non_empty_example_upload, test_user_auth, name='name1')
    assert response.status_code == 200
    upload_id_1 = response.json()['upload_id']
    upload_id_to_name[upload_id_1] = '#1'
    assert_processing(client, upload_id_1, test_user_auth)
    assert_publish(client, test_user_auth, upload_id_1, proc_infra)

    # Upload #2 - wait for processing to finish, but do not publish
    response = perform_post_uploads(client, 'stream', non_empty_example_upload, test_user_auth, name='name2')
    assert response.status_code == 200
    upload_id_2 = response.json()['upload_id']
    upload_id_to_name[upload_id_2] = '#2'
    # Note, we set check_search to False, because it assumes that there is only one upload
    assert_processing(client, upload_id_2, test_user_auth, check_search=False)

    # Upload #3 - do NOT wait for processing to finish
    response = perform_post_uploads(client, 'stream', non_empty_example_upload, test_user_auth, name='name3')
    assert response.status_code == 200
    upload_id_3 = response.json()['upload_id']
    upload_id_to_name[upload_id_3] = '#3'

    assert_uploads(
        client, test_user_auth, 'no_query_args',
        expected_upload_ids=[upload_id_1, upload_id_2, upload_id_3],
        expected_pagination={
            'total': 3, 'page': 1, 'page_after_value': None, 'next_page_after_value': None,
            'page_url': Any, 'next_page_url': None, 'prev_page_url': None, 'first_page_url': Any})
    # Test filter: UploadQuery.processing
    # TODO: race condition here, need to solve it
    # assert_uploads(
    #     client, test_user_auth, 'is_processing_true', query_params={'is_processing': True},
    #     expected_upload_ids=[upload_id_3])
    # assert_uploads(
    #     client, test_user_auth, 'is_processing_false', query_params={'is_processing': False},
    #     expected_upload_ids=[upload_id_1, upload_id_2])

    # Let #3 finish processing
    assert_processing(client, upload_id_2, test_user_auth, check_search=False)

    # Test filter: published/staging
    assert_uploads(
        client, test_user_auth, 'is_published_False', query_params={'is_published': False},
        expected_upload_ids=[upload_id_2, upload_id_3])
    assert_uploads(
        client, test_user_auth, 'is_published_True', query_params={'is_published': True},
        expected_upload_ids=[upload_id_1])
    # Test filter: upload_id
    assert_uploads(
        client, test_user_auth, 'upload_id_single', query_params={'upload_id': upload_id_1},
        expected_upload_ids=[upload_id_1])
    assert_uploads(
        client, test_user_auth, 'upload_id_multiple', query_params={'upload_id': [upload_id_1, upload_id_3]},
        expected_upload_ids=[upload_id_1, upload_id_3])
    # Test filter: upload_name
    assert_uploads(
        client, test_user_auth, 'upload_name_single', query_params={'upload_name': 'name1'},
        expected_upload_ids=[upload_id_1])
    assert_uploads(
        client, test_user_auth, 'upload_name_multiple', query_params={'upload_name': ['name1', 'name3']},
        expected_upload_ids=[upload_id_1, upload_id_3])
    # Test pagination
    assert_uploads(
        client, test_user_auth, 'page_1_of_3', query_params={'page_size': 1},
        expected_upload_ids=[upload_id_1],
        expected_pagination={
            'total': 3, 'page': 1, 'page_after_value': None, 'next_page_after_value': '0',
            'page_url': Any, 'next_page_url': Any, 'prev_page_url': None, 'first_page_url': Any})
    assert_uploads(
        client, test_user_auth, 'page_2_of_3', query_params={'page_size': 1, 'page': 2},
        expected_upload_ids=[upload_id_2],
        expected_pagination={
            'total': 3, 'page': 2, 'page_after_value': '0', 'next_page_after_value': '1',
            'page_url': Any, 'next_page_url': Any, 'prev_page_url': Any, 'first_page_url': Any})
    assert_uploads(
        client, test_user_auth, 'page_3_of_3', query_params={'page_size': 1, 'page': 3},
        expected_upload_ids=[upload_id_3],
        expected_pagination={
            'total': 3, 'page': 3, 'page_after_value': '1', 'next_page_after_value': None,
            'page_url': Any, 'next_page_url': None, 'prev_page_url': Any, 'first_page_url': Any})
    assert_uploads(
        client, test_user_auth, 'page_out_of_range', query_params={'page_after_value': '999'},
        expected_status_code=400)
    assert_uploads(
        client, test_user_auth, 'reversely_sorted', query_params={'page_size': 2, 'order': 'desc'},
        expected_upload_ids=[upload_id_3, upload_id_2],
        expected_pagination={
            'total': 3, 'page': 1, 'page_after_value': None, 'next_page_after_value': '1',
            'page_url': Any, 'next_page_url': Any, 'prev_page_url': None, 'first_page_url': Any})
    assert_uploads(
        client, test_user_auth, 'illegal_order_by', query_params={'order_by': 'upload_id'},
        expected_status_code=422)


def test_get_uploads_id(
        client, mongo, proc_infra, test_user_auth, other_test_user_auth, admin_user_auth,
        non_empty_example_upload):
    response = perform_post_uploads(
        client, 'stream', non_empty_example_upload, test_user_auth)
    assert response.status_code == 200
    response_json = response.json()
    upload_id = response_json['upload_id']
    assert_upload(response_json)
    assert_processing(client, upload_id, test_user_auth)

    response = perform_get(client, 'uploads/1234567890', test_user_auth)
    assert response.status_code == 404
    response = perform_get(client, f'uploads/{upload_id}', test_user_auth)
    assert response.status_code == 200
    assert_upload(response.json())
    response = perform_get(client, f'uploads/{upload_id}', admin_user_auth)
    assert response.status_code == 200
    assert_upload(response.json())
    response = perform_get(client, f'uploads/{upload_id}', other_test_user_auth)
    assert response.status_code == 401


def test_get_uploads_id_entries(
        client, mongo, proc_infra, test_user_auth, other_test_user_auth, admin_user_auth,
        non_empty_example_upload_vasp_with_binary):
    ''' Uploads a file with two entries, and lists these entries in various ways. '''
    response = perform_post_uploads(
        client, 'stream', non_empty_example_upload_vasp_with_binary, test_user_auth)
    assert response.status_code == 200
    response_json = response.json()
    upload_id = response_json['upload_id']
    assert_upload(response_json)
    assert_processing(client, upload_id, test_user_auth)

    entries = assert_entries(
        client, test_user_auth, upload_id,
        expected_data_len=2,
        expected_response={'processing_successful': 2, 'processing_failed': 0},
        expected_pagination={
            'total': 2, 'page': 1, 'page_after_value': None, 'next_page_after_value': None,
            'page_url': Any, 'next_page_url': None, 'prev_page_url': None, 'first_page_url': Any})
    assert_entries(client, other_test_user_auth, upload_id, expected_status_code=401)
    assert_entries(client, admin_user_auth, upload_id, expected_data_len=2)
    assert_entries(client, test_user_auth, 'an_invalid_upload_id', expected_status_code=404)

    a_valid_entry_id = entries[0]['entry_id']
    assert_entry(client, test_user_auth, 'an_invalid_upload_id', a_valid_entry_id, expected_status_code=404)
    assert_entry(client, test_user_auth, upload_id, 'an_invalid_entry_id', expected_status_code=404)
    assert_entry(client, other_test_user_auth, upload_id, a_valid_entry_id, expected_status_code=401)
    assert_entry(client, admin_user_auth, upload_id, a_valid_entry_id)

    # Test pagination
    assert_entries(
        client, test_user_auth, upload_id,
        query_args={'page_size': 1},  # Gets the first of two pages
        expected_data_len=1,
        expected_response={'processing_successful': 2, 'processing_failed': 0},
        expected_pagination={
            'total': 2, 'page': 1, 'page_after_value': None, 'next_page_after_value': '0', 'order_by': 'mainfile',
            'page_url': Any, 'next_page_url': Any, 'prev_page_url': None, 'first_page_url': Any})
    assert_entries(
        client, test_user_auth, upload_id,
        query_args={'page_size': 1, 'page': 1},  # Gets the first of two pages, by explicitly using page
        expected_data_len=1,
        expected_response={'processing_successful': 2, 'processing_failed': 0},
        expected_pagination={
            'total': 2, 'page': 1, 'page_after_value': None, 'next_page_after_value': '0', 'order_by': 'mainfile',
            'page_url': Any, 'next_page_url': Any, 'prev_page_url': None, 'first_page_url': Any})
    assert_entries(
        client, test_user_auth, upload_id,
        query_args={'page_size': 1, 'page': 2},  # Gets the last of two pages using page
        expected_data_len=1,
        expected_response={'processing_successful': 2, 'processing_failed': 0},
        expected_pagination={
            'total': 2, 'page': 2, 'page_after_value': '0', 'next_page_after_value': None, 'order_by': 'mainfile',
            'page_url': Any, 'next_page_url': None, 'prev_page_url': Any, 'first_page_url': Any})
    assert_entries(
        client, test_user_auth, upload_id,
        query_args={'page_size': 1, 'page_after_value': '0'},  # Gets the last of two pages using page_after_value
        expected_data_len=1,
        expected_response={'processing_successful': 2, 'processing_failed': 0},
        expected_pagination={
            'total': 2, 'page': 2, 'page_after_value': '0', 'next_page_after_value': None, 'order_by': 'mainfile',
            'page_url': Any, 'next_page_url': None, 'prev_page_url': Any, 'first_page_url': Any})
    assert_entries(
        client, test_user_auth, upload_id,
        query_args={'page_size': 0},  # Empty result list
        expected_data_len=0,
        expected_response={'processing_successful': 2, 'processing_failed': 0},
        expected_pagination={
            'total': 2, 'page': 1, 'page_after_value': None, 'next_page_after_value': None, 'order_by': 'mainfile',
            'page_url': Any, 'next_page_url': None, 'prev_page_url': None, 'first_page_url': None})
    assert_entries(
        client, test_user_auth, upload_id,
        query_args={'page_size': 1, 'order_by': 'parser'},  # Non-standard ordering
        expected_pagination={
            'total': 2, 'page': 1, 'page_after_value': None, 'next_page_after_value': '0', 'order_by': 'parser',
            'page_url': Any, 'next_page_url': Any, 'prev_page_url': None, 'first_page_url': Any})
    assert_entries(
        client, test_user_auth, upload_id,
        query_args={'page_size': 1, 'order_by': 'calc_id'},  # Invalid order_by
        expected_status_code=422)
    assert_entries(
        client, test_user_auth, upload_id,
        query_args={'page_size': 1, 'page': 3},  # Out of range (page)
        expected_status_code=400)
    assert_entries(
        client, test_user_auth, upload_id,
        query_args={'page_size': 1, 'page_after_value': '1'},  # Out of range (page_after_value)
        expected_status_code=400)
    assert_entries(
        client, test_user_auth, upload_id,
        query_args={'page_size': 1, 'page': 2, 'page_after_value': '0'},  # Overspecified
        expected_status_code=422)


@pytest.mark.parametrize('mode, name, user, use_upload_token, expected_status_code', [
    pytest.param('multipart', 'test_name', 'test_user', False, 200, id='post-multipart'),
    pytest.param('multipart', None, 'test_user', False, 200, id='post-multipart-no-name'),
    pytest.param('stream', 'test_name', 'test_user', False, 200, id='post-stream'),
    pytest.param('stream', None, 'test_user', False, 200, id='post-stream-no-name'),
    pytest.param('multipart', None, 'invalid', False, 401, id='post-multipart-no-name-invalid-cred'),
    pytest.param('stream', None, 'invalid', False, 401, id='post-stream-no-name-invalid-cred'),
    pytest.param('multipart', 'test_name', 'test_user', True, 200, id='post-multipart-token'),
    pytest.param('stream', 'test_name', 'test_user', True, 200, id='post-stream-token'),
    pytest.param('multipart', 'test_name', 'invalid', True, 401, id='post-multipart-token-invalid-cred'),
    pytest.param('stream', 'test_name', 'invalid', True, 401, id='post-stream-token-invalid-cred'),
    pytest.param('local_path', None, 'admin_user', False, 200, id='post-local_path'),
    pytest.param('multipart', None, None, False, 401, id='post-not-logged-in-multipart'),
    pytest.param('stream', None, None, False, 401, id='post-not-logged-in-stream'),
    pytest.param('local_path', None, None, False, 401, id='post-not-logged-in-local_path'),
    pytest.param('local_path', None, 'test_user', False, 401, id='post-not-admin-local_path')])
def test_post_uploads(
        client, mongo, proc_infra, test_user, admin_user, test_user_auth, admin_user_auth, non_empty_example_upload,
        mode, name, user, use_upload_token, expected_status_code):
    '''
    Posts an upload, with different arguments.
    '''
    if user == 'test_user':
        user_auth = test_user_auth
        token = generate_upload_token(test_user)
    elif user == 'admin_user':
        user_auth = admin_user_auth
        token = generate_upload_token(admin_user)
    elif user == 'invalid':
        user_auth = {'Authorization': 'Bearer JUST-MADE-IT-UP'}
        token = 'invalid.token'
    else:
        user_auth = None
        token = None
    # Use either token or bearer token for the post operation
    user_auth_post = user_auth
    if use_upload_token:
        user_auth_post = None
    else:
        token = None

    response = perform_post_uploads(client, mode, non_empty_example_upload, user_auth_post, token, name=name)
    assert response.status_code == expected_status_code
    if expected_status_code == 200:
        response_json = response.json()
        upload_id = response_json['upload_id']
        expected_name = name
        if not expected_name and mode in ('multipart', 'local_path'):
            expected_name = os.path.basename(non_empty_example_upload)
        assert_upload(response_json, name=expected_name)
        if mode == 'local_path':
            assert response_json['data']['upload_path'] == non_empty_example_upload

        assert_processing(client, upload_id, user_auth)


@pytest.mark.parametrize('empty', [
    pytest.param(False, id='non-empty'),
    pytest.param(True, id='empty')])
def test_post_uploads_with_publish_directly(
        client, test_user_auth, empty_upload, non_empty_example_upload, proc_infra, empty):
    ''' Posts uploads with publish_directly = True. '''
    if empty:
        file = empty_upload
    else:
        file = non_empty_example_upload
    response = perform_post_uploads(client, 'stream', file, test_user_auth, publish_directly=True)
    assert response.status_code == 200
    response_json = response.json()
    upload_id = response_json['upload_id']
    assert_upload(response_json)
    assert_processing(client, upload_id, test_user_auth, published=not empty)
    upload_proc = Upload.objects(upload_id=upload_id).first()
    if empty:
        assert not upload_proc.published
    else:
        assert_gets_published(client, upload_id, test_user_auth, with_embargo=False)


def test_post_uploads_oasis_not_admin(
        client, mongo, non_empty_example_upload, other_test_user_auth, test_user):
    response = perform_post_uploads(
        client, 'stream', non_empty_example_upload, other_test_user_auth,
        oasis_upload_id='oasis_upload_id',
        oasis_uploader_id=test_user.user_id,
        oasis_deployment_id='an_id')
    assert response.status_code == 401


def test_post_uploads_oasis_duplicate(
        client, mongo, non_empty_example_upload, test_user, test_user_auth):
    Upload.create(upload_id='oasis_upload_id', user=test_user).save()
    response = perform_post_uploads(
        client, 'stream', non_empty_example_upload, test_user_auth,
        oasis_upload_id='oasis_upload_id',
        oasis_uploader_id=test_user.user_id,
        oasis_deployment_id='an_id')
    assert response.status_code == 400


def test_post_uploads_oasis_missing_parameters(
        client, mongo, non_empty_example_upload, test_user_auth, test_user):
    ''' Attempts to make an oasis upload with one of the mandatory arguments missing. '''
    query_args_full = dict(
        oasis_upload_id='oasis_upload_id',
        oasis_uploader_id=test_user.user_id,
        oasis_deployment_id='an_id')

    for k in query_args_full:
        query_args = dict(**query_args_full)
        query_args.pop(k)
        assert perform_post_uploads(
            client, 'stream', non_empty_example_upload, test_user_auth,
            **query_args).status_code == 400


def test_post_uploads_oasis(client, mongo, proc_infra, test_user_auth, test_user, oasis_example_upload):
    response = perform_post_uploads(
        client, 'stream', oasis_example_upload, test_user_auth,
        oasis_upload_id='oasis_upload_id',
        oasis_uploader_id=test_user.user_id,
        oasis_deployment_id='an_id')

    assert response.status_code == 200

    response_json = response.json()
    upload_id = response_json['upload_id']
    assert upload_id == 'oasis_upload_id'
    assert_upload(response_json)
    assert_processing(client, upload_id, test_user_auth, published=True)
    assert_gets_published(client, upload_id, test_user_auth, from_oasis=True, with_embargo=False)


@pytest.mark.parametrize('query_args, expected_status_code', [
    pytest.param({}, 200, id='no-args'),
    pytest.param(dict(with_embargo=True, embargo_length=12), 200, id='non-standard-embargo'),
    pytest.param(dict(embargo_length=24), 200, id='non-standard-embargo-length-only'),
    pytest.param(dict(embargo_length=100), 400, id='illegal-embargo-length'),
    pytest.param(dict(with_embargo=False), 200, id='no-embargo')])
def test_publish(
        client, test_user_auth, non_empty_example_upload, proc_infra,
        query_args, expected_status_code):
    ''' Tests the publish action with various arguments. '''
    response = perform_post_uploads(client, 'stream', non_empty_example_upload, test_user_auth)
    assert response.status_code == 200
    response_json = response.json()
    upload_id = response_json['upload_id']
    assert_upload(response_json)
    assert_processing(client, upload_id, test_user_auth)
    assert_publish(
        client, test_user_auth, upload_id, proc_infra,
        expected_status_code=expected_status_code, **query_args)


def test_publish_empty(client, test_user_auth, empty_upload, proc_infra):
    ''' Tries to publish an empty upload (without entries). Should fail. '''
    response = perform_post_uploads(client, 'stream', empty_upload, test_user_auth)
    assert response.status_code == 200
    response_json = response.json()
    upload_id = response_json['upload_id']
    assert_upload(response_json)
    assert_processing(client, upload_id, test_user_auth)
    assert_publish(client, test_user_auth, upload_id, proc_infra, expected_status_code=400)


def test_publish_again(client, test_user_auth, admin_user_auth, non_empty_example_upload, proc_infra):
    ''' Tries to publish an upload after it has already been published. Should fail. '''
    response = perform_post_uploads(client, 'stream', non_empty_example_upload, test_user_auth)
    assert response.status_code == 200
    response_json = response.json()
    upload_id = response_json['upload_id']
    assert_upload(response_json)
    assert_processing(client, upload_id, test_user_auth)
    assert_publish(client, test_user_auth, upload_id, proc_infra, embargo_length=24)
    assert_publish(client, test_user_auth, upload_id, proc_infra, embargo_length=18, expected_status_code=401)
    assert_publish(client, admin_user_auth, upload_id, proc_infra, embargo_length=18, expected_status_code=401)


def test_re_process(client, published, test_user_auth, monkeypatch):
    monkeypatch.setattr('nomad.config.meta.version', 're_process_test_version')
    monkeypatch.setattr('nomad.config.meta.commit', 're_process_test_commit')

    upload_id = published.upload_id

    response = perform_post_uploads_id_action(client, test_user_auth, upload_id, 're-process')
    assert response.status_code == 200
    assert_processing(client, upload_id, test_user_auth, check_files=False, published=True)


@pytest.mark.timeout(config.tests.default_timeout)
def test_upload_limit(client, mongo, test_user, test_user_auth, proc_infra, non_empty_example_upload):
    ''' Tries to violate the limit on the number of unpublished uploads. '''
    old_upload_limit = config.services.upload_limit
    config.services.upload_limit = 5
    try:
        for _ in range(0, config.services.upload_limit):
            Upload.create(user=test_user)
        response = perform_post_uploads(client, 'stream', non_empty_example_upload, test_user_auth)
        assert response.status_code == 400
        assert Upload.user_uploads(test_user).count() == config.services.upload_limit
    finally:
        config.services.upload_limit = old_upload_limit


def test_delete_id_invalid(client, mongo, test_user_auth):
    ''' Trys to delete an invalid upload_id'''
    response = perform_delete_uploads(client, upload_id='1234567890', user_auth=test_user_auth)
    assert response.status_code == 404


@pytest.mark.parametrize('publish, delete_user, expected_status_code', [
    pytest.param(False, 'test_user', 200, id='delete-own'),
    pytest.param(False, 'other_test_user', 401, id='delete-others-not-admin'),
    pytest.param(False, 'admin_user', 200, id='delete-others-admin'),
    pytest.param(True, 'test_user', 401, id='delete-own-published'),
    pytest.param(True, 'admin_user', 200, id='delete-others-published-admin')])
def test_delete(
        client, mongo, proc_infra, non_empty_example_upload,
        test_user_auth, other_test_user_auth, admin_user_auth,
        publish, delete_user, expected_status_code):
    ''' Uploads a file, and then tries to delete it, with different parameters and users. '''
    delete_auth = {
        'test_user': test_user_auth,
        'other_test_user': other_test_user_auth,
        'admin_user': admin_user_auth
    }[delete_user]

    response = perform_post_uploads(
        client, 'multipart', non_empty_example_upload, test_user_auth)
    assert response.status_code == 200
    response_json = response.json()
    upload_id = response_json['upload_id']
    assert_upload(response_json)
    assert_processing(client, upload_id, test_user_auth)
    if publish:
        assert_publish(client, test_user_auth, upload_id, proc_infra)

    response = perform_delete_uploads(client, upload_id, user_auth=delete_auth)
    assert response.status_code == expected_status_code
    if expected_status_code == 200:
        assert_upload_does_not_exist(client, upload_id, test_user_auth)


def test_get_command_examples(client, test_user_auth):
    response = perform_get(client, 'uploads/command-examples', user_auth=None)
    assert response.status_code == 401
    response = perform_get(client, 'uploads/command-examples', user_auth=test_user_auth)
    assert response.status_code == 200
    data = response.json()
    for k in (
            'upload_url', 'upload_command', 'upload_command_with_name',
            'upload_progress_command', 'upload_command_form', 'upload_tar_command'):
        assert k in data
    assert '/api/v1/uploads' in data['upload_command']
