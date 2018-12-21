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
This module comprises a set of persistent document classes that hold all user related
data. These are information about users, their uploads and datasets, the associated
calculations, and files


.. autoclass:: Calc
    :members:
.. autoclass:: Upload
    :members:
"""

from typing import List, Any, ContextManager, Tuple, Generator
from elasticsearch.exceptions import NotFoundError
from mongoengine import StringField, BooleanField, DateTimeField, DictField, IntField
import logging
import base64
import time
from structlog import wrap_logger
from contextlib import contextmanager

from nomad import config, utils, coe_repo
from nomad.files import UploadFile, ArchiveFile, ArchiveLogFile, File
from nomad.repo import RepoCalc
from nomad.processing.base import Proc, Chord, process, task, PENDING, SUCCESS, FAILURE, RUNNING
from nomad.parsing import parsers, parser_dict
from nomad.normalizing import normalizers
from nomad.utils import lnr


class NotAllowedDuringProcessing(Exception): pass


class Calc(Proc):
    """
    Instances of this class represent calculations. This class manages the elastic
    search index entry, files, and archive for the respective calculation.

    It also contains the calculations processing and its state.

    The attribute list, does not include the various repository properties generated
    while parsing, including ``program_name``, ``program_version``, etc.

    Attributes:
        archive_id: the hash based archive id of the calc
        parser: the name of the parser used to process this calc
        upload_id: the id of the upload used to create this calculation
        mainfile: the mainfile (including path in upload) that was used to create this calc
        mainfile_tmp_path: path to the mainfile extracted for processing
    """
    archive_id = StringField(primary_key=True)
    upload_id = StringField()
    mainfile = StringField()
    parser = StringField()
    mainfile_tmp_path = StringField()

    meta: Any = {
        'indices': [
            'upload_id', 'mainfile', 'code', 'parser', 'status'
        ]
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._parser_backend = None
        self._upload = None
        self._calc_proc_logwriter = None
        self._calc_proc_logfile = None
        self._calc_proc_logwriter_ctx: ContextManager = None

    @classmethod
    def get(cls, id):
        return cls.get_by_id(id, 'archive_id')

    @property
    def mainfile_file(self) -> File:
        return File(self.mainfile_tmp_path)

    @property
    def upload(self) -> 'Upload':
        if not self._upload:
            self._upload = Upload.get(self.upload_id)
        return self._upload

    def delete(self):
        """
        Delete this calculation and all associated data. This includes all files,
        the archive, and this search index entry.
        TODO is this needed? Or do we always delete hole uploads in bulk.
        """
        # delete the archive
        if self.archive_id is not None:
            ArchiveFile(self.archive_id).delete()

        # delete the search index entry
        try:
            elastic_entry = RepoCalc.get(self.archive_id)
            if elastic_entry is not None:
                elastic_entry.delete()
        except NotFoundError:
            pass

        # delete this mongo document
        super().delete()

    def get_logger(self, **kwargs):
        upload_hash, calc_hash = self.archive_id.split('/')
        logger = super().get_logger()
        logger = logger.bind(
            upload_id=self.upload_id, mainfile=self.mainfile,
            upload_hash=upload_hash, calc_hash=calc_hash,
            archive_id=self.archive_id, **kwargs)

        return logger

    def get_calc_logger(self, **kwargs):
        """
        Returns a wrapped logger that additionally saves all entries to the calculation
        processing log in the archive.
        """
        logger = self.get_logger(**kwargs)

        if self._calc_proc_logwriter is None:
            self._calc_proc_logfile = ArchiveLogFile(self.archive_id)
            self._calc_proc_logwriter_ctx = self._calc_proc_logfile.open('wt')
            self._calc_proc_logwriter = self._calc_proc_logwriter_ctx.__enter__()  # pylint: disable=E1101

        def save_to_calc_log(logger, method_name, event_dict):
            program = event_dict.get('normalizer', 'parser')
            event = event_dict.get('event', '')
            entry = '[%s] %s: %s' % (method_name, program, event)
            if len(entry) > 120:
                self._calc_proc_logwriter.write(entry[:120])
                self._calc_proc_logwriter.write('...')
            else:
                self._calc_proc_logwriter.write(entry)
            self._calc_proc_logwriter.write('\n')
            return event_dict

        return wrap_logger(logger, processors=[save_to_calc_log])

    @process
    def process(self):
        logger = self.get_logger()
        if self.upload is None:
            logger.error('calculation upload does not exist')

        try:
            self.parsing()
            self.normalizing()
            self.archiving()
        finally:
            # close loghandler that was not closed due to failures
            try:
                if self._calc_proc_logwriter is not None:
                    self._calc_proc_logwriter.close()
                    self._calc_proc_logwriter = None
            except Exception as e:
                logger.error('could not close calculation proc log', exc_info=e)

            # inform parent proc about completion
            self.upload.completed_child()

    @task
    def parsing(self):
        context = dict(parser=self.parser, step=self.parser)
        logger = self.get_calc_logger(**context)
        parser = parser_dict[self.parser]

        with utils.timer(logger, 'parser executed', input_size=self.mainfile_file.size):
            self._parser_backend = parser.run(self.mainfile_tmp_path, logger=logger)

        self._parser_backend.openNonOverlappingSection('section_calculation_info')
        self._parser_backend.addValue('upload_id', self.upload_id)
        self._parser_backend.addValue('archive_id', self.archive_id)
        self._parser_backend.addValue('main_file', self.mainfile)
        self._parser_backend.addValue('parser_name', self.parser)

        if self._parser_backend.status[0] != 'ParseSuccess':
            logger.error(self._parser_backend.status[1])
            error = self._parser_backend.status[1]
            self._parser_backend.addValue('parse_status', 'ParseFailure')
            self.fail(error, level=logging.DEBUG, **context)
        else:
            self._parser_backend.addValue('parse_status', 'ParseSuccess')

        self._parser_backend.closeNonOverlappingSection('section_calculation_info')

        self.add_processor_info(self.parser)

    @contextmanager
    def use_parser_backend(self, processor_name):
        self._parser_backend.reset_status()
        yield self._parser_backend
        self.add_processor_info(processor_name)

    def add_processor_info(self, processor_name: str) -> None:
        self._parser_backend.openContext('/section_calculation_info/0')
        self._parser_backend.openNonOverlappingSection('section_archive_processing_info')
        self._parser_backend.addValue('archive_processor_name', processor_name)

        if self._parser_backend.status[0] == 'ParseSuccess':
            warnings = getattr(self._parser_backend, '_warnings', [])
            if len(warnings) > 0:
                self._parser_backend.addValue('archive_processor_status', 'WithWarnings')
                self._parser_backend.addValue('archive_processor_warning_number', len(warnings))
                self._parser_backend.addArrayValues('archive_processor_warnings', [str(warning) for warning in warnings])
            else:
                self._parser_backend.addValue('archive_processor_status', 'Success')
        else:
            errors = self._parser_backend.status[1]
            self._parser_backend.addValue('archive_processor_error', str(errors))

        self._parser_backend.closeNonOverlappingSection('section_archive_processing_info')
        self._parser_backend.closeContext('/section_calculation_info/0')

    @task
    def normalizing(self):
        for normalizer in normalizers:
            normalizer_name = normalizer.__name__
            context = dict(normalizer=normalizer_name, step=normalizer_name)
            logger = self.get_calc_logger(**context)

            with utils.timer(
                    logger, 'normalizer executed', input_size=self.mainfile_file.size):
                with self.use_parser_backend(normalizer_name) as backend:
                    normalizer(backend).normalize(logger=logger)

            failed = self._parser_backend.status[0] != 'ParseSuccess'
            if failed:
                logger.error(self._parser_backend.status[1])
                error = self._parser_backend.status[1]
                self.fail(error, level=logging.WARNING, **context)
                break
            else:
                logger.debug(
                    'completed normalizer successfully', normalizer=normalizer_name)

    @task
    def archiving(self):
        logger = self.get_logger()

        upload_hash, calc_hash = self.archive_id.split('/')
        additional = dict(
            mainfile=self.mainfile,
            upload_time=self.upload.upload_time,
            staging=True,
            restricted=False,
            user_id=self.upload.user_id,
            aux_files=list(self.upload.upload_file.get_siblings(self.mainfile)))

        with utils.timer(logger, 'indexed', step='index'):
            # persist to elastic search
            RepoCalc.create_from_backend(
                self._parser_backend,
                additional=additional,
                upload_hash=upload_hash,
                calc_hash=calc_hash,
                upload_id=self.upload_id).persist()

        with utils.timer(
                logger, 'archived', step='archive',
                input_size=self.mainfile_file.size) as log_data:

            # persist the archive
            archive_file = ArchiveFile(self.archive_id)
            with archive_file.write_archive_json() as out:
                self._parser_backend.write_json(out, pretty=True)

            log_data.update(archive_size=archive_file.size)

        # close loghandler
        if self._calc_proc_logwriter is not None:
            with utils.timer(
                    logger, 'archived log', step='archive_log',
                    input_size=self.mainfile_file.size) as log_data:
                self._calc_proc_logwriter_ctx.__exit__(None, None, None)  # pylint: disable=E1101
                self._calc_proc_logwriter = None

                log_data.update(log_size=self._calc_proc_logfile.size)


class Upload(Chord):
    """
    Represents uploads in the databases. Provides persistence access to the files storage,
    and processing state.

    Attributes:
        name: optional user provided upload name
        local_path: optional local path, e.g. for files that are already somewhere on the server
        additional_metadata: optional user provided additional meta data
        upload_id: the upload id generated by the database
        in_staging: true if the upload is still in staging and can be edited by the uploader
        is_private: true if the upload and its derivitaves are only visible to the uploader
        upload_time: the timestamp when the system realised the upload
        upload_hash: the hash of the uploaded file
        user_id: the id of the user that created this upload
    """
    id_field = 'upload_id'

    upload_id = StringField(primary_key=True)

    name = StringField(default=None)
    local_path = StringField(default=None)
    additional_metadata = DictField(default=None)

    in_staging = BooleanField(default=True)
    is_private = BooleanField(default=False)

    upload_time = DateTimeField()
    upload_hash = StringField(default=None)

    user_id = StringField(required=True)
    upload_url = StringField(default=None)
    upload_command = StringField(default=None)

    coe_repo_upload_id = IntField(default=None)

    _initiated_parsers = IntField(default=-1)

    meta: Any = {
        'indexes': [
            'upload_hash', 'user_id', 'status'
        ]
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._upload_file = None

    @classmethod
    def get(cls, id):
        return cls.get_by_id(id, 'upload_id')

    @classmethod
    def user_uploads(cls, user: coe_repo.User) -> List['Upload']:
        """ Returns all uploads for the given user. Currently returns all uploads. """
        return cls.objects(user_id=str(user.user_id), in_staging=True)

    def get_logger(self, **kwargs):
        logger = super().get_logger()
        logger = logger.bind(upload_id=self.upload_id, **kwargs)
        return logger

    def delete(self):
        logger = self.get_logger(task='delete')

        if not (self.completed or self.current_task == 'uploading'):
            raise NotAllowedDuringProcessing()

        with lnr(logger, 'delete upload file'):
            try:
                UploadFile(self.upload_id, local_path=self.local_path).delete()
            except KeyError:
                if self.current_task == 'uploading':
                    logger.debug(
                        'Upload exist, but file does not exist. '
                        'It was probably aborted and deleted.')
                else:
                    logger.debug('Upload exist, but uploaded file does not exist.')

        with lnr(logger, 'deleting calcs'):
            # delete archive files
            ArchiveFile.delete_archives(upload_hash=self.upload_hash)

            # delete repo entries
            RepoCalc.delete_upload(upload_id=self.upload_id)

            # delete calc processings
            Calc.objects(upload_id=self.upload_id).delete()

        with lnr(logger, 'deleting upload'):
            super().delete()

    @classmethod
    def _external_objects_url(cls, url):
        """ Replaces the given internal object storage url with an URL that allows
            external access.
        """
        return 'http://%s:%s%s%s' % (config.services.api_host, config.services.api_port, config.services.api_base_path, url)

    @classmethod
    def create(cls, **kwargs) -> 'Upload':
        """
        Creates a new upload for the given user, a user given name is optional.
        It will populate the record with a signed url and pending :class:`UploadProc`.
        The upload will be already saved to the database.

        Arguments:
            user (coe_repo.User): The user that created the upload.
        """
        user: coe_repo.User = kwargs['user']
        del(kwargs['user'])
        if 'upload_id' not in kwargs:
            kwargs.update(upload_id=utils.create_uuid())
        kwargs.update(user_id=str(user.user_id))
        self = super().create(**kwargs)

        basic_auth_token = base64.b64encode(b'%s:' % user.get_auth_token()).decode('utf-8')

        self.upload_url = cls._external_objects_url('/uploads/%s/file' % self.upload_id)
        self.upload_command = 'curl -H "Authorization: Basic %s" "%s" --upload-file local_file' % (
            basic_auth_token, self.upload_url)

        self._continue_with('uploading')

        return self

    def unstage(self):
        self.get_logger().info('unstage')

        if not (self.completed or self.current_task == 'uploading'):
            raise NotAllowedDuringProcessing()

        self.in_staging = False
        RepoCalc.unstage(upload_id=self.upload_id)
        coe_repo.add_upload(self, restricted=False)  # TODO allow users to choose restricted
        self.save()

    @process
    def process(self):
        self.extracting()
        self.parse_all()

    @task
    def uploading(self):
        pass

    @property
    def upload_file(self):
        """ The :class:`UploadFile` instance that represents the uploaded file of this upload. """
        if not self._upload_file:
            self._upload_file = UploadFile(self.upload_id, local_path=self.local_path)
        return self._upload_file

    @task
    def extracting(self):
        """
        Task performed before the actual parsing/normalizing. Extracting and bagging
        the uploaded files, computing all keys, create an *upload* entry in the NOMAD-coe
        repository db, etc.
        """
        # extract the uploaded file, this will also create a bagit bag.
        logger = self.get_logger()
        try:
            with utils.timer(
                    logger, 'upload extracted', step='extracting',
                    upload_size=self.upload_file.size):
                self.upload_file.extract()
        except KeyError as e:
            self.fail('process request for non existing upload', level=logging.INFO)
            return

        # create and save a hash for the upload
        try:
            self.upload_hash = self.upload_file.upload_hash()
        except Exception as e:
            self.fail('could not create upload hash', e)
            return

        # check if the file was already uploaded and processed before
        if RepoCalc.upload_exists(self.upload_hash):
            self.fail('The same file was already uploaded and processed.', level=logging.INFO)
            return

    def match_mainfiles(self) -> Generator[Tuple[File, str, object], None, None]:
        """
        Generator function that matches all files in the upload to all parsers to
        determine the upload's mainfiles.

        Returns:
            Tuples of mainfile, filename, and parsers
        """
        for filename in self.upload_file.filelist:
            potential_mainfile = self.upload_file.get_file(filename)
            for parser in parsers:
                try:
                    with potential_mainfile.open('r') as mainfile_f:
                        if parser.is_mainfile(filename, lambda fn: mainfile_f):
                            yield potential_mainfile, filename, parser
                except Exception as e:
                    self.get_logger().error(
                        'exception while matching pot. mainfile',
                        mainfile=filename, exc_info=e)

    @task
    def parse_all(self):
        """
        Identified mainfile/parser combinations among the upload's files, creates
        respective :class:`Calc` instances, and triggers their processing.
        """
        logger = self.get_logger()

        # TODO: deal with multiple possible parser specs
        with utils.timer(
                logger, 'upload extracted', step='matching',
                upload_size=self.upload_file.size,
                upload_filecount=len(self.upload_file.filelist)):
            total_calcs = 0
            for mainfile, filename, parser in self.match_mainfiles():
                calc = Calc.create(
                    archive_id='%s/%s' % (self.upload_hash, utils.hash(filename)),
                    mainfile=filename, parser=parser.name,
                    mainfile_tmp_path=mainfile.os_path,
                    upload_id=self.upload_id)

                calc.process()
                total_calcs += 1

        # have to save the total_calcs information for chord management
        self.spwaned_childred(total_calcs)

    def join(self):
        self.cleanup()

    @task
    def cleanup(self):
        try:
            upload = UploadFile(self.upload_id, local_path=self.local_path)
            with utils.timer(
                    self.get_logger(), 'upload persisted', step='cleaning',
                    upload_size=upload.size):
                upload.persist()

            with utils.timer(
                    self.get_logger(), 'processing cleaned up', step='cleaning',
                    upload_size=upload.size):
                upload.remove_extract()
        except KeyError as e:
            self.fail('Upload does not exist', exc_info=e)
            return

        self.get_logger().debug('closed upload')

    @property
    def processed_calcs(self):
        return Calc.objects(upload_id=self.upload_id, status__in=[SUCCESS, FAILURE]).count()

    @property
    def total_calcs(self):
        return Calc.objects(upload_id=self.upload_id).count()

    @property
    def failed_calcs(self):
        return Calc.objects(upload_id=self.upload_id, status=FAILURE).count()

    @property
    def pending_calcs(self):
        return Calc.objects(upload_id=self.upload_id, status=PENDING).count()

    def all_calcs(self, start, end, order_by='mainfile'):
        return Calc.objects(upload_id=self.upload_id)[start:end].order_by(order_by)

    @staticmethod
    def repair_all():
        """
        Utitlity function that will look for suspiciously looking conditions in
        all uncompleted downloads. It ain't a perfect world.
        """
        # TODO this was added as a quick fix to #37.
        # Even though it might be strictly necessary, there should be a tested backup
        # solution for it Chords to not work properly due to failed to fail processings
        uploads = Upload.objects(status__in=[PENDING, RUNNING])
        for upload in uploads:
            completed = upload.processed_calcs
            total = upload.total
            pending = upload.pending_calcs

            if completed + pending == total:
                time.sleep(2)
                if pending == upload.pending_calcs:
                    Calc.objects(upload_id=upload.upload_id, status=PENDING).delete()
