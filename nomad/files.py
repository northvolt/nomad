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
Uploads contains classes and functions to create and maintain file structures
for uploads.

There are two different structures for uploads in two different states: *staging* and *public*.
Possible operations on uploads differ based on this state. Staging is used for
processing, heavily editing, creating hashes, etc. Public is supposed to be a
almost readonly (beside metadata) storage.

.. code-block:: sh

    fs/staging/<upload>/raw/**
                       /archive/<calc>.json
    fs/public/<upload>/raw-public.plain.zip
                      /raw-restricted.plain.zip
                      /archive-public.json.zip
                      /archive-restricted.json.zip

There is an implicit relationship between files, based on them being in the same
directory. Each directory with at least one *mainfile* is a *calculation directory*
and all the files are *aux* files to that *mainfile*. This is independent of the
respective files actually contributing data or not. A *calculation directory* might
contain multiple *mainfile*. E.g., user simulated multiple states of the same system, have
one calculation based on the other, etc. In this case the other *mainfile* is an *aux*
file to the original *mainfile* and vice versa.

Published files are kept in pairs of public and restricted files. Here the multiple *mainfiles*
per directory provides a dilemma. If on *mainfile* is restricted, all its *aux* files
should be restricted too. But if one of the *aux* files is actually a *mainfile* it
might be published!

There are multiple ways to solve this. Due to the rarity of the case, we take the
most simple solution: if one file is public, all files are made public, execpt those
being other mainfiles. Therefore, the aux files of a restricted calc might become public!
"""

from abc import ABCMeta
import sys
from typing import IO, Generator, Dict, Iterable, Callable, List, Tuple
import os.path
import os
import shutil
import tarfile
import hashlib
import io
import pickle
from contextlib import contextmanager

from nomad import config, utils
from nomad.datamodel import UploadWithMetadata


# TODO this should become obsolete, once we are going beyong python 3.6. For now
# python 3.6's zipfile does not allow to seek/tell within a file-like opened from a
# file in a zipfile.
if sys.version_info >= (3, 7):
    import zipfile
else:
    import zipfile37 as zipfile

user_metadata_filename = 'user_metadata.pickle'


def always_restricted(path: str):
    """
    Used to put general restrictions on files, e.g. due to licensing issues. Will be
    called during packing and while accessing public files.
    """
    basename = os.path.basename(path)
    if basename.startswith('POTCAR') and not basename.endswith('.stripped'):
        return True


def copytree(src, dst):
    """
    A close on ``shutils.copytree`` that does not try to copy the stats on all files.
    This is unecessary for our usecase and also causes permission denies for unknown
    reasons.
    """
    os.makedirs(dst, exist_ok=False)

    for item in os.listdir(src):
        s = os.path.join(src, item)
        d = os.path.join(dst, item)
        if os.path.isdir(s):
            copytree(s, d)
        else:
            shutil.copyfile(s, d)


class PathObject:
    """
    Object storage-like abstraction for paths in general.
    Arguments:
        bucket: The bucket to store this object in
        object_id: The object id (i.e. directory path)
        os_path: Override the "object storage" path with the given path.
        prefix: Add a x-digit prefix directory, e.g. foo/test/ -> foo/tes/test
        create_prefix: Create the prefix right away
    """
    def __init__(
            self, bucket: str, object_id: str, os_path: str = None,
            prefix: bool = False, create_prefix: bool = False) -> None:
        if os_path:
            self.os_path = os_path
        else:
            self.os_path = os.path.join(bucket, object_id)

        if prefix and config.fs.prefix_size > 0:
            segments = list(os.path.split(self.os_path))
            last = segments[-1]
            segments[-1] = last[:config.fs.prefix_size]
            segments.append(last)
            self.os_path = os.path.join(*segments)

            if create_prefix:
                os.makedirs(os.path.dirname(self.os_path), exist_ok=True)

    def delete(self) -> None:
        basename = os.path.basename(self.os_path)
        parent_directory = os.path.dirname(self.os_path)
        parent_name = os.path.basename(parent_directory)

        shutil.rmtree(self.os_path)

        if len(parent_name) == config.fs.prefix_size and basename.startswith(parent_name):
            try:
                if not os.listdir(parent_directory):
                    os.rmdir(parent_directory)
            except Exception as e:
                utils.get_logger(__name__).error(
                    'could not remove empty prefix dir', directory=parent_directory, exc_info=e)

    def exists(self) -> bool:
        return os.path.exists(self.os_path)

    @property
    def size(self) -> int:
        """ The os determined file size. """
        return os.stat(self.os_path).st_size

    def __repr__(self) -> str:
        return self.os_path


class DirectoryObject(PathObject):
    """
    Object storage-like abstraction for directories.
    Arguments:
        bucket: The bucket to store this object in
        object_id: The object id (i.e. directory path)
        create: True if the directory structure should be created. Default is False.
    """
    def __init__(self, bucket: str, object_id: str, create: bool = False, **kwargs) -> None:
        super().__init__(bucket, object_id, **kwargs)
        self._create = create
        if create and not os.path.isdir(self.os_path):
            os.makedirs(self.os_path)

    def join_dir(self, path, create: bool = None) -> 'DirectoryObject':
        if create is None:
            create = self._create
        return DirectoryObject(None, None, create=create, os_path=os.path.join(self.os_path, path))

    def join_file(self, path) -> PathObject:
        dirname = os.path.dirname(path)
        if dirname != '':
            return self.join_dir(dirname).join_file(os.path.basename(path))
        else:
            return PathObject(None, None, os_path=os.path.join(self.os_path, path))

    def exists(self) -> bool:
        return os.path.isdir(self.os_path)


class ExtractError(Exception):
    pass


class Restricted(Exception):
    pass


class UploadFiles(DirectoryObject, metaclass=ABCMeta):

    _archive_ext = 'json'

    def __init__(
            self, bucket: str, upload_id: str,
            is_authorized: Callable[[], bool] = lambda: False,
            create: bool = False) -> None:
        self.logger = utils.get_logger(__name__, upload_id=upload_id)

        super().__init__(bucket, upload_id, create=create, prefix=True)

        if not create and not self.exists():
            raise KeyError()

        self.upload_id = upload_id
        self._is_authorized = is_authorized

    @property
    def _user_metadata_file(self):
        return self.join_file('user_metadata.pickle')

    @property
    def user_metadata(self) -> dict:
        if self._user_metadata_file.exists():
            with open(self._user_metadata_file.os_path, 'rb') as f:
                return pickle.load(f)
        else:
            return {}

    @user_metadata.setter
    def user_metadata(self, data: dict) -> None:
        with open(self._user_metadata_file.os_path, 'wb') as f:
            pickle.dump(data, f)

    def to_staging_upload_files(self, create: bool = False) -> 'StagingUploadFiles':
        """ Casts to or creates corresponding staging upload files or returns None. """
        raise NotImplementedError()

    @staticmethod
    def get(upload_id: str, *args, **kwargs) -> 'UploadFiles':
        if DirectoryObject(config.fs.staging, upload_id, prefix=True).exists():
            return StagingUploadFiles(upload_id, *args, **kwargs)
        elif DirectoryObject(config.fs.public, upload_id, prefix=True).exists():
            return PublicUploadFiles(upload_id, *args, **kwargs)
        else:
            return None

    def raw_file(self, file_path: str, *args, **kwargs) -> IO:
        """
        Opens a raw file and returns a file-like object. Additional args, kwargs are
        delegated to the respective `open` call.
        Arguments:
            file_path: The path to the file relative to the upload.
        Raises:
            KeyError: If the file does not exist.
            Restricted: If the file is restricted and upload access evaluated to False.
        """
        raise NotImplementedError()

    def raw_file_size(self, file_path: str) -> int:
        """
        Returns:
            The size of the given raw file.
        """
        raise NotImplementedError()

    def raw_file_manifest(self, path_prefix: str = None) -> Generator[str, None, None]:
        """
        Returns the path for all raw files in the archive (with a given prefix).
        Arguments:
            path_prefix: An optional prefix; only returns those files that have the prefix.
        Returns:
            An iterable over all (matching) raw files.
        """
        raise NotImplementedError()

    def raw_file_list(self, directory: str) -> List[Tuple[str, int]]:
        """
        Gives a list of directory contents and its size.
        Arguments:
            directory: The directory to list
        Returns:
            A list of tuples with file name and size.
        """
        raise NotImplementedError()

    def archive_file(self, calc_id: str, *args, **kwargs) -> IO:
        """
        Opens a archive file and returns a file-like objects. Additional args, kwargs are
        delegated to the respective `open` call.
        Arguments:
            calc_id: The id identifying the calculation.
        Raises:
            KeyError: If the calc does not exist.
            Restricted: If the file is restricted and upload access evaluated to False.
        """
        raise NotImplementedError()

    def archive_log_file(self, calc_id: str, *args, **kwargs) -> IO:
        """
        Opens a archive log file and returns a file-like objects. Additional args, kwargs are
        delegated to the respective `open` call.
        Arguments:
            calc_id: The id identifying the calculation.
        Raises:
            KeyError: If the calc does not exist.
            Restricted: If the file is restricted and upload access evaluated to False.
        """
        raise NotImplementedError()


class StagingUploadFiles(UploadFiles):
    def __init__(
            self, upload_id: str, is_authorized: Callable[[], bool] = lambda: False,
            create: bool = False) -> None:
        super().__init__(config.fs.staging, upload_id, is_authorized, create)

        self._raw_dir = self.join_dir('raw')
        self._archive_dir = self.join_dir('archive')
        self._frozen_file = self.join_file('.frozen')

        self._size = 0
        self._shared = DirectoryObject(config.fs.public, upload_id, create=create)

    def to_staging_upload_files(self, create: bool = False) -> 'StagingUploadFiles':
        return self

    @property
    def _user_metadata_file(self):
        return self._shared.join_file('user_metadata.pickle')

    @property
    def size(self) -> int:
        return self._size

    def _file(self, path_object: PathObject, *args, **kwargs) -> IO:
        try:
            return open(path_object.os_path, *args, **kwargs)
        except FileNotFoundError:
            raise KeyError()
        except IsADirectoryError:
            raise KeyError()

    def raw_file(self, file_path: str, *args, **kwargs) -> IO:
        if not self._is_authorized():
            raise Restricted
        return self._file(self.raw_file_object(file_path), *args, **kwargs)

    def raw_file_size(self, file_path: str) -> int:
        if not self._is_authorized():
            raise Restricted
        return self.raw_file_object(file_path).size

    def raw_file_object(self, file_path: str) -> PathObject:
        return self._raw_dir.join_file(file_path)

    def archive_file(self, calc_id: str, *args, **kwargs) -> IO:
        if not self._is_authorized():
            raise Restricted
        return self._file(self.archive_file_object(calc_id), *args, **kwargs)

    def archive_log_file(self, calc_id: str, *args, **kwargs) -> IO:
        if not self._is_authorized():
            raise Restricted
        return self._file(self.archive_log_file_object(calc_id), *args, **kwargs)

    def archive_file_object(self, calc_id: str) -> PathObject:
        return self._archive_dir.join_file('%s.%s' % (calc_id, self._archive_ext))

    def archive_log_file_object(self, calc_id: str) -> PathObject:
        return self._archive_dir.join_file('%s.log' % calc_id)

    def add_rawfiles(self, path: str, move: bool = False, prefix: str = None, force_archive: bool = False) -> None:
        """
        Add rawfiles to the upload. The given file will be copied, moved, or extracted.

        Arguments:
            path: Path to a directory, file, or zip file. Zip files will be extracted.
            move: Whether the file should be moved instead of copied. Zips will be extracted and then deleted.
            prefix: Optional path prefix for the added files.
            force_archive: Expect the file to be a zip or other support archive file.
                Usually those files are only extracted if they can be extracted and copied instead.
        """
        assert not self.is_frozen
        assert os.path.exists(path)
        self._size += os.stat(path).st_size
        target_dir = self._raw_dir
        if prefix is not None:
            target_dir = target_dir.join_dir(prefix, create=True)
        ext = os.path.splitext(path)[1]
        if force_archive or ext == '.zip':
            try:
                with zipfile.ZipFile(path) as zf:
                    zf.extractall(target_dir.os_path)
                if move:
                    os.remove(path)
                return
            except zipfile.BadZipFile:
                pass

        if force_archive or ext in ['.tgz', '.tar.gz', '.tar.bz2', '.tar']:
            try:
                with tarfile.open(path) as tf:
                    tf.extractall(target_dir.os_path)
                if move:
                    os.remove(path)
                return
            except tarfile.TarError:
                pass

        if force_archive:
            raise ExtractError

        if move:
            shutil.move(path, target_dir.os_path)
        else:
            if os.path.isdir(path):
                shutil.copytree(path, os.path.join(target_dir.os_path, os.path.dirname(path)))
            else:
                shutil.copy(path, target_dir.os_path)

    @property
    def is_frozen(self) -> bool:
        """ Returns True if this upload is already *bagged*. """
        return self._frozen_file.exists()

    def create_extracted_copy(self) -> None:
        """
        Copies all raw-file to the extracted bucket to mimic the behavior of the old
        CoE python API. TODO: should be removed after migration.
        """
        copytree(self._raw_dir.os_path, os.path.join(config.fs.coe_extracted, self.upload_id))

    def pack(
            self, upload: UploadWithMetadata, target_dir: DirectoryObject = None,
            skip_raw: bool = False) -> None:
        """
        Replaces the staging upload data with a public upload record by packing all
        data into files. It is only available if upload *is_bag*.
        This is potentially a long running operation.
        Arguments:
            upload: The upload with all calcs and  calculation metadata of the upload
                used to determine what files to pack and what the embargo situation is.
            target_dir: optional DirectoryObject to override where to put the files. Default
                is the corresponding public upload files directory.
            skip_raw: determine to not pack the raw data, only archive and user metadata
        """
        self.logger.info('started to pack upload')

        # freeze the upload
        assert not self.is_frozen, "Cannot pack an upload that is packed, or packing."
        with open(self._frozen_file.os_path, 'wt') as f:
            f.write('frozen')

        # create a target dir in the public bucket
        if target_dir is None:
            target_dir = DirectoryObject(
                config.fs.public, self.upload_id, create=True, prefix=True,
                create_prefix=True)
        assert target_dir.exists()

        # copy user metadata
        target_metadata_file = target_dir.join_file(user_metadata_filename)
        if self._user_metadata_file.exists() and not target_metadata_file.exists():
            shutil.copyfile(
                self._user_metadata_file.os_path,
                target_metadata_file.os_path)

        # In prior versions we used bagit on raw files. There was not much purpose for
        # it, so it was removed. Check 0.3.x for the implementation
        def create_zipfile(kind: str, prefix: str, ext: str) -> zipfile.ZipFile:
            file = target_dir.join_file('%s-%s.%s.zip' % (kind, prefix, ext))
            return zipfile.ZipFile(file.os_path, mode='w')

        # zip archives
        archive_public_zip = create_zipfile('archive', 'public', self._archive_ext)
        archive_restricted_zip = create_zipfile('archive', 'restricted', self._archive_ext)

        try:
            for calc in upload.calcs:
                archive_zip = archive_restricted_zip if calc.with_embargo else archive_public_zip

                archive_filename = '%s.%s' % (calc.calc_id, self._archive_ext)
                archive_file = self._archive_dir.join_file(archive_filename)
                if archive_file.exists():
                    archive_zip.write(archive_file.os_path, archive_filename)

                archive_log_filename = '%s.%s' % (calc.calc_id, 'log')
                log_file = self._archive_dir.join_file(archive_log_filename)
                if log_file.exists():
                    archive_zip.write(log_file.os_path, archive_log_filename)

        except Exception as e:
            self.logger.error('exception during packing archives', exc_info=e)

        finally:
            archive_restricted_zip.close()
            archive_public_zip.close()

        self.logger.info('packed archives')

        if skip_raw:
            return

        # zip raw files
        raw_public_zip = create_zipfile('raw', 'public', 'plain')
        raw_restricted_zip = create_zipfile('raw', 'restricted', 'plain')

        try:
            # 1. add all public raw files
            # 1.1 collect all public mainfiles and aux files
            public_files: Dict[str, str] = {}
            for calc in upload.calcs:
                if not calc.with_embargo:
                    mainfile = calc.mainfile
                    assert mainfile is not None
                    # mainfile might already have been added due to being a auxfile to another calc
                    if mainfile not in public_files:
                        for filepath in self.calc_files(mainfile, with_cutoff=False):
                            if not always_restricted(filepath):
                                public_files[filepath] = None
            # 1.2 remove the non public mainfiles that have been added as auxfiles of public mainfiles
            for calc in upload.calcs:
                if calc.with_embargo:
                    mainfile = calc.mainfile
                    assert mainfile is not None
                    if mainfile in public_files:
                        del(public_files[mainfile])
            # 1.3 zip all remaining public
            for filepath in public_files.keys():
                raw_public_zip.write(self._raw_dir.join_file(filepath).os_path, filepath)

            # 2. everything else becomes restricted
            for filepath in self.raw_file_manifest():
                if filepath not in public_files:
                    raw_restricted_zip.write(self._raw_dir.join_file(filepath).os_path, filepath)

        except Exception as e:
            self.logger.error('exception during packing raw files', exc_info=e)

        finally:
            raw_restricted_zip.close()
            raw_public_zip.close()

        self.logger.info('packed raw files')

    def raw_file_manifest(self, path_prefix: str = None) -> Generator[str, None, None]:
        upload_prefix_len = len(self._raw_dir.os_path) + 1
        for root, _, files in os.walk(self._raw_dir.os_path):
            for file in files:
                path = os.path.join(root, file)[upload_prefix_len:]
                if path_prefix is None or path.startswith(path_prefix):
                    yield path

    def raw_file_list(self, directory: str) -> List[Tuple[str, int]]:
        if not self._is_authorized():
            raise Restricted

        if directory is None or directory == '':
            prefix = self._raw_dir.os_path
        else:
            prefix = os.path.join(self._raw_dir.os_path, directory)

        results: List[Tuple[str, int]] = []
        if not os.path.isdir(prefix):
            return results

        for file in os.listdir(prefix):
            path = os.path.join(prefix, file)
            if os.path.isfile(path):
                results.append((file, os.path.getsize(path)))

        return results

    def calc_files(self, mainfile: str, with_mainfile: bool = True, with_cutoff: bool = True) -> Iterable[str]:
        """
        Returns all the auxfiles and mainfile for a given mainfile. This implements
        nomad's logic about what is part of a calculation and what not. The mainfile
        is first entry, the rest is sorted.
        Arguments:
            mainfile: The mainfile relative to upload
            with_mainfile: Do include the mainfile, default is True
        """
        mainfile_object = self._raw_dir.join_file(mainfile)
        if not mainfile_object.exists():
            raise KeyError()

        mainfile_basename = os.path.basename(mainfile)
        calc_dir = os.path.dirname(mainfile_object.os_path)
        calc_relative_dir = calc_dir[len(self._raw_dir.os_path) + 1:]

        file_count = 0
        aux_files: List[str] = []
        for filename in os.listdir(calc_dir):
            if filename != mainfile_basename and os.path.isfile(os.path.join(calc_dir, filename)):
                aux_files.append(os.path.join(calc_relative_dir, filename))
                file_count += 1

            if with_cutoff and file_count > config.auxfile_cutoff:
                # If there are two many of them, its probably just a directory with lots of
                # calculations. In this case it does not make any sense to provide thousands of
                # aux files.
                break

        aux_files = sorted(aux_files)

        if with_mainfile:
            return [mainfile] + aux_files
        else:
            return aux_files

    def calc_id(self, mainfile: str) -> str:
        """
        Calculates a id for the given calc.
        Arguments:
            mainfile: The mainfile path relative to the upload that identifies the calc in the folder structure.
        Returns:
            The calc id
        Raises:
            KeyError: If the mainfile does not exist.
        """
        return utils.hash(self.upload_id, mainfile)

    def calc_hash(self, mainfile: str) -> str:
        """
        Calculates a hash for the given calc based on file contents and aux file contents.
        Arguments:
            mainfile: The mainfile path relative to the upload that identifies the calc in the folder structure.
        Returns:
            The calculated hash
        Raises:
            KeyError: If the mainfile does not exist.
        """
        hash = hashlib.sha512()
        for filepath in self.calc_files(mainfile):
            with open(self._raw_dir.join_file(filepath).os_path, 'rb') as f:
                for data in iter(lambda: f.read(65536), b''):
                    hash.update(data)

        return utils.make_websave(hash)

    def delete(self) -> None:
        super().delete()
        if self._shared.exists():
            self._shared.delete()


class ArchiveBasedStagingUploadFiles(StagingUploadFiles):
    """
    :class:`StagingUploadFiles` based on a single uploaded archive file (.zip)

    Arguments:
        upload_path: The path to the uploaded file.
    """

    def __init__(
            self, upload_id: str, upload_path: str, *args, **kwargs) -> None:
        super().__init__(upload_id, *args, **kwargs)
        self.upload_path = upload_path

    @property
    def is_valid(self) -> bool:
        if self.upload_path is None:
            return False
        if not os.path.exists(self.upload_path):
            return False
        elif not os.path.isfile(self.upload_path):
            return False
        else:
            return True

    def extract(self) -> None:
        assert next(self.raw_file_manifest(), None) is None, 'can only extract once'
        super().add_rawfiles(self.upload_path, force_archive=True)

    def add_rawfiles(self, path: str, move: bool = False, prefix: str = None, force_archive: bool = False) -> None:
        assert False, 'do not add_rawfiles to a %s' % self.__class__.__name__


class PublicUploadFilesBasedStagingUploadFiles(StagingUploadFiles):
    """
    :class:`StagingUploadFiles` based on a single uploaded archive file (.zip)

    Arguments:
        upload_path: The path to the uploaded file.
    """

    def __init__(
            self, public_upload_files: 'PublicUploadFiles', *args, **kwargs) -> None:
        super().__init__(public_upload_files.upload_id, *args, **kwargs)
        self.public_upload_files = public_upload_files

    def extract(self) -> None:
        assert next(self.raw_file_manifest(), None) is None, 'can only extract once'
        for access in ['public', 'restricted']:
            super().add_rawfiles(
                self.public_upload_files.get_zip_file('raw', access, 'plain').os_path,
                force_archive=True)

    def add_rawfiles(self, path: str, move: bool = False, prefix: str = None, force_archive: bool = False) -> None:
        assert False, 'do not add_rawfiles to a %s' % self.__class__.__name__

    def pack(self, upload: UploadWithMetadata, *args, **kwargs) -> None:
        """ Packs only the archive contents and stores it in the existing public upload files. """
        super().pack(upload, target_dir=self.public_upload_files, skip_raw=True)


class PublicUploadFiles(UploadFiles):

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(config.fs.public, *args, **kwargs)
        self._zipfile_cache: Dict[str, zipfile.ZipFile] = None

    def get_zip_file(self, prefix: str, access: str, ext: str) -> PathObject:
        return self.join_file('%s-%s.%s.zip' % (prefix, access, ext))

    def open_zip_file(self, prefix: str, access: str, ext: str) -> zipfile.ZipFile:
        zip_path = self.get_zip_file(prefix, access, ext).os_path
        if self._zipfile_cache is None:
            return zipfile.ZipFile(zip_path)
        else:
            if zip_path in self._zipfile_cache:
                f = self._zipfile_cache[zip_path]
            else:
                f = zipfile.ZipFile(zip_path)
                self._zipfile_cache[zip_path] = f

            return f

    def _file(self, prefix: str, ext: str, path: str, *args, **kwargs) -> IO:
        mode = kwargs.get('mode') if len(args) == 0 else args[0]
        if 'mode' in kwargs:
            del(kwargs['mode'])
        mode = mode if mode else 'rb'

        for access in ['public', 'restricted']:
            try:
                zf = self.open_zip_file(prefix, access, ext)

                f = zf.open(path, 'r', **kwargs)
                if (access == 'restricted' or always_restricted(path)) and not self._is_authorized():
                    raise Restricted
                if 't' in mode:
                    return io.TextIOWrapper(f)
                else:
                    return f
            except FileNotFoundError:
                pass
            except IsADirectoryError:
                pass
            except KeyError:
                pass

        raise KeyError()

    def to_staging_upload_files(self, create: bool = False) -> 'StagingUploadFiles':
        try:
            staging_upload_files = PublicUploadFilesBasedStagingUploadFiles(self)
        except KeyError:
            if not create:
                return None

            staging_upload_files = PublicUploadFilesBasedStagingUploadFiles(self, create=True)
            staging_upload_files.extract()

        return staging_upload_files

    def raw_file(self, file_path: str, *args, **kwargs) -> IO:
        return self._file('raw', 'plain', file_path, *args, *kwargs)

    def raw_file_size(self, file_path: str) -> int:
        for access in ['public', 'restricted']:
            try:
                zf = self.open_zip_file('raw', access, 'plain')
                info = zf.getinfo(file_path)
                if (access == 'restricted' or always_restricted(file_path)) and not self._is_authorized():
                    raise Restricted

                return info.file_size
            except FileNotFoundError:
                pass
            except KeyError:
                pass

        raise KeyError()

    def raw_file_manifest(self, path_prefix: str = None) -> Generator[str, None, None]:
        for access in ['public', 'restricted']:
            try:
                zf = self.open_zip_file('raw', access, 'plain')
                for path in zf.namelist():
                    if path_prefix is None or path.startswith(path_prefix):
                        yield path
            except FileNotFoundError:
                pass

    def raw_file_list(self, directory: str) -> List[Tuple[str, int]]:
        if directory is None:
            directory = ''
        directory_len = len(directory)

        results = []
        for access in ['public', 'restricted']:
            if access == 'restricted' and not self._is_authorized():
                continue

            try:
                zf = self.open_zip_file('raw', access, 'plain')
                for path in zf.namelist():
                    content_path = path[directory_len + (0 if directory_len == 0 else 1):]
                    if path.startswith(directory) and '/' not in content_path:
                        if '/' not in content_path:
                            results.append((content_path, zf.getinfo(path).file_size))
                        else:
                            # this asserts that sub directories are always behind their
                            # parents and file siblings
                            break

            except FileNotFoundError:
                pass

        return results

    def archive_file(self, calc_id: str, *args, **kwargs) -> IO:
        return self._file('archive', self._archive_ext, '%s.%s' % (calc_id, self._archive_ext), *args, **kwargs)

    def archive_log_file(self, calc_id: str, *args, **kwargs) -> IO:
        return self._file('archive', self._archive_ext, '%s.log' % calc_id, *args, **kwargs)

    def repack(self) -> None:
        """
        Replaces the existing public/restricted data file pairs with new ones, based
        on current restricted information in the metadata. Should be used after updating
        the restrictions on calculations. This is potentially a long running operation.
        """
        raise NotImplementedError()

    @contextmanager
    def zipfile_cache(self):
        """
        Context that allows to read files while caching zipfiles without reopening them
        all the time.
        """
        if self._zipfile_cache is None:
            self._zipfile_cache = {}
        try:
            yield
        finally:
            for zip_file in self._zipfile_cache.values():
                zip_file.close()
