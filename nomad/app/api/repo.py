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

"""
The repository API of the nomad@FAIRDI APIs. Currently allows to resolve repository
meta-data.
"""

from typing import List, Dict, Any
from flask_restplus import Resource, abort, fields
from flask import request, g
from elasticsearch.exceptions import NotFoundError
import elasticsearch.helpers

from nomad import search, utils, datamodel, processing as proc, infrastructure
from nomad.app.utils import rfc3339DateTime, RFC3339DateTime, with_logger
from nomad.app.optimade import filterparser

from .api import api
from .auth import authenticate
from .common import pagination_model, pagination_request_parser, calc_route, metadata_model

ns = api.namespace('repo', description='Access repository metadata.')


@calc_route(ns)
class RepoCalcResource(Resource):
    @api.response(404, 'The upload or calculation does not exist')
    @api.response(401, 'Not authorized to access the calculation')
    @api.response(200, 'Metadata send', fields.Raw)
    @api.doc('get_repo_calc')
    @authenticate()
    def get(self, upload_id, calc_id):
        """
        Get calculation metadata in repository form.

        Repository metadata only entails the quantities shown in the repository.
        Calcs are references via *upload_id*, *calc_id* pairs.
        """
        try:
            calc = search.Entry.get(calc_id)
        except NotFoundError:
            abort(404, message='There is no calculation %s/%s' % (upload_id, calc_id))

        if calc.with_embargo or not calc.published:
            if g.user is None:
                abort(401, message='Not logged in to access %s/%s.' % (upload_id, calc_id))

            if not (any(g.user.user_id == user.user_id for user in calc.owners) or g.user.is_admin):
                abort(401, message='Not authorized to access %s/%s.' % (upload_id, calc_id))

        return calc.to_dict(), 200


repo_calcs_model = api.model('RepoCalculations', {
    'pagination': fields.Nested(pagination_model, skip_none=True),
    'scroll': fields.Nested(allow_null=True, skip_none=True, model=api.model('Scroll', {
        'total': fields.Integer(description='The total amount of hits for the search.'),
        'scroll_id': fields.String(allow_null=True, description='The scroll_id that can be used to retrieve the next page.'),
        'size': fields.Integer(help='The size of the returned scroll page.')})),
    'results': fields.List(fields.Raw, description=(
        'A list of search results. Each result is a dict with quantitie names as key and '
        'values as values')),
    'statistics': fields.Raw(description=(
        'A dict with all statistics. Each statistic is dictionary with a metrics dict as '
        'value and quantity value as key. The possible metrics are code runs(calcs), %s. '
        'There is a pseudo quantity "total" with a single value "all" that contains the '
        ' metrics over all results. ' % ', '.join(datamodel.Domain.instance.metrics_names))),
    'datasets': fields.Raw(api.model('RepoDatasets', {
        'after': fields.String(description='The after value that can be used to retrieve the next datasets.'),
        'values': fields.Raw(description='A dict with names as key. The values are dicts with "total" and "examples" keys.')
    }), skip_none=True)
})


repo_calc_id_model = api.model('RepoCalculationId', {
    'upload_id': fields.String(), 'calc_id': fields.String()
})


def add_common_parameters(request_parser):
    request_parser.add_argument(
        'owner', type=str,
        help='Specify which calcs to return: ``all``, ``public``, ``user``, ``staging``, default is ``all``')
    request_parser.add_argument(
        'from_time', type=lambda x: rfc3339DateTime.parse(x),
        help='A yyyy-MM-ddTHH:mm:ss (RFC3339) minimum entry time (e.g. upload time)')
    request_parser.add_argument(
        'until_time', type=lambda x: rfc3339DateTime.parse(x),
        help='A yyyy-MM-ddTHH:mm:ss (RFC3339) maximum entry time (e.g. upload time)')

    for quantity in search.quantities.values():
        request_parser.add_argument(
            quantity.name, help=quantity.description,
            action=quantity.argparse_action if quantity.multi else None)


repo_request_parser = pagination_request_parser.copy()
add_common_parameters(repo_request_parser)
repo_request_parser.add_argument(
    'scroll', type=bool, help='Enable scrolling')
repo_request_parser.add_argument(
    'scroll_id', type=str, help='The id of the current scrolling window to use.')
repo_request_parser.add_argument(
    'date_histogram', type=bool, help='Add an additional aggregation over the upload time')
repo_request_parser.add_argument(
    'datasets_after', type=str, help='The last dataset id of the last scroll window for the dataset quantitiy')
repo_request_parser.add_argument(
    'metrics', type=str, action='append', help=(
        'Metrics to aggregate over all quantities and their values as comma separated list. '
        'Possible values are %s.' % ', '.join(datamodel.Domain.instance.metrics_names)))
repo_request_parser.add_argument(
    'datasets', type=bool, help=('Return dataset information.'))
repo_request_parser.add_argument(
    'statistics', type=bool, help=('Return statistics.'))


search_request_parser = api.parser()
add_common_parameters(search_request_parser)

query_model_parameters = {
    'owner': fields.String(description='Specify which calcs to return: ``all``, ``public``, ``user``, ``staging``, default is ``all``'),
    'from_time': RFC3339DateTime(description='A yyyy-MM-ddTHH:mm:ss (RFC3339) minimum entry time (e.g. upload time)'),
    'until_time': RFC3339DateTime(description='A yyyy-MM-ddTHH:mm:ss (RFC3339) maximum entry time (e.g. upload time)')
}

for quantity in search.quantities.values():
    if quantity.multi:
        def field(**kwargs):
            return fields.List(fields.String(**kwargs))
    else:
        field = fields.String
    query_model_parameters[quantity.name] = field(description=quantity.description)


repo_query_model = api.model('RepoQuery', query_model_parameters, skip_none=True)
repo_edit_model = api.model('RepoEdit', {
    'query': fields.Nested(repo_query_model, skip_none=True, description='New metadata will be applied to query results.'),
    'metadata': fields.Nested(metadata_model, skip_none=True, description='New metadata that should be used on all query results.')
})


def add_query(search_request: search.SearchRequest, args: Dict[str, Any]):
    """
    Help that adds query relevant request args to the given SearchRequest.
    """
    args = {key: value for key, value in args.items() if value is not None}

    # owner
    try:
        search_request.owner(
            args.get('owner', 'all'),
            g.user.user_id if g.user is not None else None)
    except ValueError as e:
        abort(401, getattr(e, 'message', 'Invalid owner parameter'))
    except Exception as e:
        abort(400, getattr(e, 'message', 'Invalid owner parameter'))

    # time range
    from_time_str = args.get('from_time', None)
    until_time_str = args.get('until_time', None)

    try:
        from_time = rfc3339DateTime.parse(from_time_str) if from_time_str is not None else None
        until_time = rfc3339DateTime.parse(until_time_str) if until_time_str is not None else None
        search_request.time_range(start=from_time, end=until_time)
    except Exception:
        abort(400, message='bad datetime format')

    # optimade
    try:
        optimade = args.get('optimade', None)
        if optimade is not None:
            q = filterparser.parse_filter(optimade)
            search_request.query(q)
    except filterparser.FilterException:
        abort(400, message='could not parse optimade query')

    # search parameter
    search_request.search_parameters(**{
        key: value for key, value in args.items()
        if key not in ['optimade'] and key in search.quantities})


@ns.route('/')
class RepoCalcsResource(Resource):
    @api.doc('search')
    @api.response(400, 'Invalid requests, e.g. wrong owner type or bad search parameters')
    @api.expect(repo_request_parser, validate=True)
    @api.marshal_with(repo_calcs_model, skip_none=True, code=200, description='Search results send')
    @authenticate()
    def get(self):
        """
        Search for calculations in the repository form, paginated.

        The ``owner`` parameter determines the overall entries to search through.
        Possible values are: ``all`` (show all entries visible to the current user), ``public``
        (show all publically visible entries), ``user`` (show all user entries, requires login),
        ``staging`` (show all user entries in staging area, requires login).

        You can use the various quantities to search/filter for. For some of the
        indexed quantities this endpoint returns aggregation information. This means
        you will be given a list of all possible values and the number of entries
        that have the certain value. You can also use these aggregations on an empty
        search to determine the possible values.

        The pagination parameters allows determine which page to return via the
        ``page`` and ``per_page`` parameters. Pagination however, is limited to the first
        100k (depending on ES configuration) hits.

        An alternative to pagination is to use ``scroll`` and ``scroll_id``. With ``scroll``
        you will get a ``scroll_id`` on the first request. Each call with ``scroll`` and
        the respective ``scroll_id`` will return the next ``per_page`` (here the default is 1000)
        results. Scroll however, ignores ordering and does not return aggregations.
        The scroll view used in the background will stay alive for 1 minute between requests.
        If the given ``scroll_id`` is not available anymore, a HTTP 400 is raised.

        The search will return aggregations on a predefined set of quantities. Aggregations
        will tell you what quantity values exist and how many entries match those values.

        Ordering is determined by ``order_by`` and ``order`` parameters.
        """

        search_request = search.SearchRequest()
        add_query(search_request, repo_request_parser.parse_args())

        try:
            scroll = bool(request.args.get('scroll', False))
            scroll_id = request.args.get('scroll_id', None)
            page = int(request.args.get('page', 1))
            per_page = int(request.args.get('per_page', 10 if not scroll else 1000))
            order = int(request.args.get('order', -1))
            order_by = request.args.get('order_by', 'formula')

            if bool(request.args.get('date_histogram', False)):
                search_request.date_histogram()
            metrics: List[str] = request.args.getlist('metrics')

            with_datasets = request.args.get('datasets', False)
            with_statistics = request.args.get('statistics', False)
        except Exception:
            abort(400, message='bad parameter types')

        try:
            assert page >= 1
            assert per_page > 0
        except AssertionError:
            abort(400, message='invalid pagination')

        if order not in [-1, 1]:
            abort(400, message='invalid pagination')

        for metric in metrics:
            if metric not in search.metrics_names:
                abort(400, message='there is no metric %s' % metric)

        if with_statistics:
            search_request.default_statistics(metrics_to_use=metrics)
            if 'datasets' not in metrics:
                total_metrics = metrics + ['datasets']
            else:
                total_metrics = metrics
            search_request.totals(metrics_to_use=total_metrics)
            search_request.statistic('authors', 1000)

        try:
            if scroll:
                results = search_request.execute_scrolled(scroll_id=scroll_id, size=per_page)

            else:
                if with_datasets:
                    search_request.quantity(
                        'dataset_id', size=per_page, examples=1,
                        after=request.args.get('datasets_after', None))

                results = search_request.execute_paginated(
                    per_page=per_page, page=page, order=order, order_by=order_by)

                # TODO just a work around to make things prettier
                if with_statistics:
                    statistics = results['statistics']
                    if 'code_name' in statistics and 'currupted mainfile' in statistics['code_name']:
                        del(statistics['code_name']['currupted mainfile'])

                if with_datasets:
                    datasets = results.pop('quantities')['dataset_id']
                    results['datasets'] = datasets

            return results, 200
        except search.ScrollIdNotFound:
            abort(400, 'The given scroll_id does not exist.')
        except KeyError as e:
            import traceback
            traceback.print_exc()
            abort(400, str(e))

    @api.doc('edit_repo')
    @api.response(400, 'Invalid requests, e.g. wrong owner type or bad search parameters')
    @api.expect(repo_edit_model)
    @api.response(code=200, description='Search results send')
    @authenticate()
    @with_logger
    def post(self, logger):
        """ Edit repository metadata. """
        json_data = request.get_json()
        if json_data is None:
            json_data = {}
        query = json_data.get('query', {})

        owner = query.get('owner', 'user')
        if owner not in ['user', 'staging']:
            abort(400, 'Not a valid owner for edit %s. Edit can only be performed in user or staging' % owner)
        query['owner'] = owner

        search_request = search.SearchRequest()
        add_query(search_request, query)

        if 'metadata' not in json_data:
            abort(400, 'Missing key metadata in edit repo payload')
        metadata = json_data['metadata']
        if metadata.get('with_embargo', False):
            abort(400, 'Cannot raise an embargo, you can only lift the embargo')

        if '_uploader' in metadata or '_upload_time' in metadata:
            if not g.user.is_admin():
                abort(400, 'Only the admin user can set uploader or upload_time.')

        mongo_update = {}
        for key in [
                'with_embargo', 'shared_with', 'coauthors', 'references', 'comment',
                'datasets', '_uploader', '_upload_time']:
            if key in metadata:
                mongo_update['metadata__%s' % key.lstrip('_')] = metadata[key]

        calc_ids = list(hit['calc_id'] for hit in search_request.execute_scan())

        n_updated = proc.Calc.objects(calc_id__in=calc_ids).update(multi=True, **mongo_update)
        if n_updated != len(calc_ids):
            logger.error('edit repo did not update all entries', payload=json_data)

        def elastic_updates():
            for calc in proc.Calc.objects(calc_id__in=calc_ids):
                entry = search.Entry.from_calc_with_metadata(
                    datamodel.CalcWithMetadata(**calc['metadata']))
                entry = entry.to_dict(include_meta=True)
                entry['_op_type'] = 'index'
                yield entry

        _, failed = elasticsearch.helpers.bulk(
            infrastructure.elastic_client, elastic_updates(), stats_only=True)
        search.refresh()
        if failed > 0:
            logger.error(
                'edit repo with failed elastic updates',
                payload=json_data, nfailed=len(failed))

        return 'metadata updated', 200


repo_quantity_values_model = api.model('RepoQuantityValues', {
    'quantity': fields.Nested(api.model('RepoQuantity', {
        'after': fields.String(description='The after value that can be used to retrieve the next set of values.'),
        'values': fields.Raw(description='A dict with values as key. Values are dicts with "total" and "examples" keys.')
    }), allow_null=True)
})

repo_quantity_search_request_parser = api.parser()
add_common_parameters(repo_quantity_search_request_parser)
repo_quantity_search_request_parser.add_argument(
    'after', type=str, help='The after value to use for "scrolling".')
repo_request_parser.add_argument(
    'size', type=int, help='The max size of the returned values.')


@ns.route('/<string:quantity>')
class RepoQuantityResource(Resource):
    @api.doc('quantity_search')
    @api.response(400, 'Invalid requests, e.g. wrong owner type, bad quantity, bad search parameters')
    @api.expect(repo_quantity_search_request_parser, validate=True)
    @api.marshal_with(repo_quantity_values_model, skip_none=True, code=200, description='Search results send')
    @authenticate()
    def get(self, quantity: str):
        """
        Retrieve quantity values from entries matching the search.

        You can use the various quantities to search/filter for. For some of the
        indexed quantities this endpoint returns aggregation information. This means
        you will be given a list of all possible values and the number of entries
        that have the certain value. You can also use these aggregations on an empty
        search to determine the possible values.

        There is no ordering and no pagination. Instead there is an 'after' key based
        scrolling. The result will contain an 'after' value, that can be specified
        for the next request. You can use the 'size' and 'after' parameters accordingly.

        The result will contain a 'quantity' key with quantity values and the "after"
        value. There will be upto 'size' many values. For the rest of the values use the
        "after" parameter in another request.
        """

        search_request = search.SearchRequest()
        add_query(search_request, repo_quantity_search_request_parser.parse_args())

        try:
            after = request.args.get('after', None)
            size = int(request.args.get('size', 100))
        except Exception:
            abort(400, message='bad parameter types')

        try:
            assert size >= 0
        except AssertionError:
            abort(400, message='invalid size')

        search_request.quantity(quantity, size=size, after=after)

        try:
            results = search_request.execute()
            quantities = results.pop('quantities')
            results['quantity'] = quantities[quantity]

            return results, 200
        except KeyError as e:
            import traceback
            traceback.print_exc()
            abort(400, 'Given quantity does not exist: %s' % str(e))


@ns.route('/pid/<int:pid>')
class RepoPidResource(Resource):
    @api.doc('resolve_pid')
    @api.response(404, 'Entry with PID does not exist')
    @api.marshal_with(repo_calc_id_model, skip_none=True, code=200, description='Entry resolved')
    @authenticate()
    def get(self, pid: int):
        search_request = search.SearchRequest()

        if g.user is not None:
            search_request.owner('all', user_id=g.user.user_id)
        else:
            search_request.owner('all')

        search_request.search_parameter('pid', pid)

        results = list(search_request.execute_scan())
        total = len(results)

        if total == 0:
            abort(404, 'Entry with PID %d does not exist' % pid)

        if total > 1:
            utils.get_logger(__name__).error('Two entries for the same pid', pid=pid)

        result = results[0]
        return dict(
            upload_id=result['upload_id'],
            calc_id=result['calc_id'])
