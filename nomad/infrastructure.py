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
This module provides function to establish connections to the database, searchengine, etc.
infrastructure services.
"""

import shutil
from mongoengine import connect
from elasticsearch_dsl import connections
from elasticsearch.exceptions import RequestError

from nomad import config, utils

logger = utils.get_logger(__name__)

elastic_client = None
""" The elastic search client. """

mongo_client = None
""" The pymongo mongodb client. """


repository_db = None
""" The repository postgres db sqlalchemy client. """


def setup():
    """ Creates connections to mongodb and elastic search. """
    global elastic_client
    setup_logging()
    setup_mongo()
    setup_elastic()
    setup_repository_db()


def setup_logging():
    utils.configure_logging()
    logger.info(
        'setup logging',
        logstash=config.logstash.enabled,
        logstash_host=config.logstash.host,
        logstash_port=config.logstash.tcp_port,
        logstash_level=config.logstash.level)


def setup_mongo():
    """ Creates connection to mongodb. """
    global mongo_client
    mongo_client = connect(db=config.mongo.users_db, host=config.mongo.host, port=config.mongo.port)
    logger.info('setup mongo connection')


def setup_elastic():
    """ Creates connection to elastic search. """
    global elastic_client
    elastic_client = connections.create_connection(hosts=[config.elastic.host])
    logger.info('setup elastic connection')

    try:
        from nomad.repo import RepoCalc
        RepoCalc.init()
    except RequestError as e:
        if e.status_code == 400 and 'resource_already_exists_exception' in e.error:
            pass  # happens if two services try this at the same time
        else:
            raise e
    else:
        logger.info('init elastic index')


def setup_repository_db():
    """ Creates a sqlalchemy session for the NOMAD-coe repository postgres db. """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    global repository_db
    url = 'postgresql://%s:%s@%s:%d/%s' % (
        config.repository_db.user,
        config.repository_db.password,
        config.repository_db.host,
        config.repository_db.port,
        config.repository_db.dbname)
    engine = create_engine(url, echo=False, isolation_level='AUTOCOMMIT')
    repository_db = sessionmaker(bind=engine)()
    logger.info('setup repository db')


def reset():
    """ Resets the databases mongo and elastic/calcs. Be careful. """
    logger.info('reset mongodb')
    mongo_client.drop_database(config.mongo.users_db)

    logger.info('reset elastic search')
    elastic_client.indices.delete(index=config.elastic.calc_index)
    from nomad.repo import RepoCalc
    RepoCalc.init()

    logger.info('reset repository db')
    reset_repository_db()

    logger.info('reset files')
    shutil.rmtree(config.fs.objects, ignore_errors=True)
    shutil.rmtree(config.fs.tmp, ignore_errors=True)


def reset_repository_db():
    import psycopg2
    logger.info('reset repository db 1')
    conn_str = "host='%s' port=%d dbname='%s' user='%s' password='%s'" % (
        config.repository_db.host,
        config.repository_db.port,
        config.repository_db.dbname,
        config.repository_db.user,
        config.repository_db.password)

    logger.info('reset repository db 2')
    conn = psycopg2.connect(conn_str)
    logger.info('reset repository db 3')
    with conn.cursor() as cur:
        logger.info('reset repository db 4')
        cur.execute(
            "DROP SCHEMA public CASCADE;"
            "CREATE SCHEMA public;"
            "GRANT ALL ON SCHEMA public TO postgres;"
            "GRANT ALL ON SCHEMA public TO public;")
        logger.info('reset repository db 5')
        cur.execute(open('nomad/empty_repository_db.sql', 'r').read())
        logger.info('reset repository db 6')
    conn.close()
    logger.info('reset repository db 7')


if __name__ == '__main__':
    setup()
    reset()
