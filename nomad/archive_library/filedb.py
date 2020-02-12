# Copyright 2019  Alvin Noe Ladines, Markus Scheidgen
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
Module for storage of archive data using the msgpack module.

In module ``ArchiveFileDB, the data are fragmented and saved as a list
in a msgpack file.
A component can be retrieved by giving an offset to the msgpack file to
be unpacked. The database can then be queried by giving a schema similar
to the archive data.

To build the database,

.. code-block:: python
    db = ArchiveFileDB("db.msg", mode='w', max_lfragment=3)
    db.add_data(["archive1.json", "archive2.json"])
    db.close()

To query the database,

.. code-block:: python
    db = ArchiveFileDB("db.msg", mode='r')
    db.query({'idX':{'sectionX':{'propertyX':None}}})
    db.close()
"""
import msgpack
import json
import os
import re
from io import StringIO, BufferedWriter, BufferedReader, BytesIO


_PLACEHOLDER = '*'


class JSONdata:
    """
    Provides a graphQL-style query for a given json data and query schema.
    Arguments:
        data: The json data to be queried
    """
    def __init__(self, data):
        self.data = self._merge_list(data)

    def _merge_list(self, data):
        if not isinstance(data, dict):
            return data
        merged = {}
        main_keys = []
        for key, val in data.items():
            bracket = key.find('[')
            val = self._merge_list(val)
            if bracket > 0:
                index = key[bracket + 1:]
                index = int(index.strip().rstrip(']'))
                main_key = key[:bracket]
                if main_key not in merged:
                    main_keys.append(main_key)
                    merged[main_key] = {}
                merged[main_key][index] = val
            else:
                merged[key] = val

        for key in main_keys:
            merged[key] = [merged[key][n] for n in sorted(merged[key].keys())]
        return merged

    def _get_index(self, str_index, nval_ref):
        str_index = str_index.strip().lstrip('[').rstrip(']')
        if ':' in str_index:
            lo, hi = str_index.split(':')
            lo = int(lo) if lo else 0
            hi = int(hi) if hi else 0
            lo = nval_ref + lo if lo < 0 else lo
            hi = nval_ref + hi if hi <= 0 else hi
            if hi > nval_ref:
                hi = nval_ref
            if lo > hi or lo < 0 or hi < 0:
                return
            if lo > hi or lo < 0 or hi < 0:
                return
            index = list(range(lo, hi))
        else:
            index = [int(str_index)]
        return index

    def get_data(self, entry, ref=None):
        out_data = {}
        if ref is None:
            ref = self.data

        if not isinstance(entry, dict):
            return ref

        for key, val in entry.items():
            index = None
            bracket = key.find('[')
            if bracket > 0:
                str_index = key[bracket:]
                key = key[:bracket]
                index = self._get_index(str_index, len(ref[key]))

            if key not in ref:
                continue

            if index is None:
                out_data[key] = self.get_data(val, ref[key])
            else:
                try:
                    out_data[key] = [self.get_data(val, ref[key][n]) for n in index]
                except Exception:
                    continue

        out_data = self._merge_list(out_data)

        return out_data


class ArchiveFileDB:
    """
    An interface to the messagepack module to provide an searchable
    container of archive data.
    Arguments:
        fileio: can be a string or file object to read/write the msgpack file
        mode: r/w to indicate read or write the msgpack file
        max_lfragment: the maximum level for which the archive data will
            be fragmented for more efficient unpacking of msgpack components
    """
    def __init__(self, fileio, mode='r', max_lfragment=None):
        self._filename = None
        self._fileobj = None
        if isinstance(fileio, str):
            self._filename = fileio
            self._mode = mode
        elif isinstance(fileio, BufferedReader):
            self._fileobj = fileio
            self._mode = 'rb'
        elif isinstance(fileio, BufferedWriter):
            self._fileobj = fileio
            self._mode = 'wb'
        elif isinstance(fileio, BytesIO):
            self._fileobj = fileio
            self._mode = mode
        else:
            raise TypeError
        self._max_lfragment = max_lfragment
        if 'w' in self._mode and self._max_lfragment is None:
            self._max_lfragment = 2
        if '+' in self._mode:
            self._max_lfragment = None
        self._sep = 'MSG_ENTRY'
        self._ids = None
        self._data = {}

    @property
    def max_lfragment(self):
        if self._max_lfragment is None:
            orig_mode = self.mode
            self.mode = 'rb'
            self._max_lfragment = self.get_docs('MAX_LFRAGMENT')
            self.mode = orig_mode
        return self._max_lfragment

    def _fragment_json(self, data, key='', cur_lfragment=0):
        if cur_lfragment >= self.max_lfragment:
            pass
        elif isinstance(data, list):
            res = []
            main = dict(path=key, data=[])
            for i in range(len(data)):
                if not isinstance(data[i], dict):
                    break
                p = '%s[%d]' % (key, i)
                res += self._fragment_json(data[i], p, cur_lfragment)
                main['data'].append(p)
            res += [main]
            return res
        elif isinstance(data, dict):
            res = []
            cur_lfragment += 1
            main = dict(path=key, data=[])
            for k, v in data.items():
                p = os.path.join(key, k)
                res += self._fragment_json(v, p, cur_lfragment)
                main['data'].append(p)
            res += [main]
            return res
        return [dict(path=key, data={os.path.basename(key): data})]

    def write(self, abspath, relpath):
        """
        Mimic the zipfile function to write files to database.
        Arguments:
            abspath: The absolute path to the file to be read
            relpath: For compatibility with zipfile
        """
        self.add_data(abspath)

    def close(self, save=True):
        """
        Mimic the zipfile function to close the msgpack file.
        Will trigger the creation of the database when in write mode.
        Arguments:
            save: If True will add the current data in memory to database
        """
        if 'w' in self._mode:
            self.create_db()
        if self._fileobj:
            self._fileobj.close()
            self._fileobj = None

    def save(self):
        """
        Commits current data in memory to database
        """
        self.create_db()

    def add_data(self, data):
        """
        Add data to the msgpack database.
        Arguments:
            data: Can be a filename or dictionary or list of both
        """
        if isinstance(data, str):
            key = os.path.basename(data)
            if data.endswith('json'):
                key = key.split('.')[0]
                val = json.load(open(data))
                if val:
                    self._data[key] = val
            else:
                key = key.replace('.', '_')
                val = open(data).read()
                if val:
                    self._data[key] = val
        elif isinstance(data, dict):
            for key, val in data.items():
                if val:
                    self._data[key] = val
        elif isinstance(data, list):
            for i in range(len(data)):
                self.add_data(data[i])
        else:
            raise NotImplementedError

    # TODO
    # def edit_data(self, data):
    #     if self._data:
    #         print("Memory not empty. Commit existing data first")
    #         return
    #     self.add_data(data)
    #     entries = self._fragment_json(self._data)
    #     data_to_write = self._load_data()
    #     raise NotImplementedError

    def _load_data(self):
        orig_mode = self.mode
        self.mode = 'rb'
        self.fileobj.seek(0)
        data_loaded = msgpack.load(self.fileobj)
        self.mode = orig_mode
        return data_loaded

    def create_db(self):
        """
        Creates the database and writes it to the msgpack file.
        The database consists of the list of the fragmented data
        and the list of footers such as the ids of the data.
        """
        # data to be added in memory
        data = self._data
        # segment the data, each entry is a dict with 'path' and 'data' values
        entries = self._fragment_json(data)

        # save the data in a separate list everytime function is called
        # last list reserved for IDs
        if '+' in self._mode:
            data_to_write = self._load_data()
            data_to_write.insert(-1, [])
        else:
            data_to_write = [[], []]

        # get last entry in database
        last_index = 0
        for i in range(len(data_to_write) - 1):
            last_index += len(data_to_write[i]) / 2
        last_index = int(last_index)

        # add data in list, second to the last
        for i in range(len(entries)):
            sep = '%s_%d' % (self._sep, i + last_index)
            data_to_write[-2].append(sep)
            data_to_write[-2].append(entries[i]['data'])
        data_str = msgpack.dumps(data_to_write)
        if not data_to_write[-1]:
            head = {}
        else:
            sep = '%s_IDS' % (self._sep)
            hi = data_to_write[-1].index(sep.encode()) + 1
            head = data_to_write[-1][hi]
            data_to_write[-1] = []

        # add fragmentation info
        sep = '%s_MAX_LFRAGMENT' % (self._sep)
        data_to_write[-1].append(sep)
        data_to_write[-1].append(self.max_lfragment)
        # add pointers to entries
        start_index = 0
        for i in range(len(entries)):
            sep = '%s_%d' % (self._sep, i + last_index)
            index = data_str.index(sep.encode(), start_index) + len(sep)
            pre_index = head.get(('%s' % entries[i]['path']).encode(), [])
            head[(entries[i]['path']).encode()] = pre_index + [index]
            start_index = index
        sep = '%s_IDS' % (self._sep)
        data_to_write[-1].append(sep)
        data_to_write[-1].append(head)
        data_str = msgpack.dumps(data_to_write)
        self.fileobj.write(data_str)
        self._data = {}

    def _reduce_to_section(self, entry, cur_lfragment=0):
        if not isinstance(entry, dict):
            return entry
        cur_lfragment += 1
        if cur_lfragment > self.max_lfragment:
            return _PLACEHOLDER
        new_dict = {}
        for key, val in entry.items():
            v = self._reduce_to_section(val, cur_lfragment)
            new_dict[key] = v
        return new_dict

    @staticmethod
    def to_list_path_str(entries, root='', paths=[]):
        if not isinstance(entries, dict):
            return
        if len(paths) > 0:
            paths.remove(root)
        for key, val in entries.items():
            p = os.path.join(root, key)
            paths.append(p)
            ArchiveFileDB.to_list_path_str(val, p, paths)
        return list(paths)

    @staticmethod
    def to_nested_dict(path_str):
        if isinstance(path_str, str):
            path_str = path_str.split('/')
        if len(path_str) == 1:
            return {path_str[0]: _PLACEHOLDER}
        else:
            pdict = {}
            pdict[path_str[0]] = ArchiveFileDB.to_nested_dict(path_str[1:])
            return pdict

    @staticmethod
    def append_data(entry, val):
        for k, v in entry.items():
            if v == _PLACEHOLDER or v is None:
                entry[k] = val
            else:
                entry[k] = ArchiveFileDB.append_data(v, val)
        return entry

    @staticmethod
    def merge_dict(dict0, dict1):
        if not isinstance(dict1, dict) or not isinstance(dict0, dict):
            return dict1
        for k, v in dict1.items():
            if k in dict0:
                dict0[k] = ArchiveFileDB.merge_dict(dict0[k], v)
            else:
                dict0[k] = v
        return dict0

    @property
    def mode(self):
        if 'b' not in self._mode:
            self._mode += 'b'
        return self._mode

    @mode.setter
    def mode(self, m):
        if self.mode == m:
            return
        self._mode = m
        if self._fileobj and not isinstance(self._fileobj, BytesIO):
            self._fileobj.close()
            self._fileobj = None

    @property
    def fileobj(self):
        if self._fileobj is None:
            mode = self.mode
            self._fileobj = open(self._filename, mode)
        return self._fileobj

    def get_docs(self, offset):
        """
        Provides an entry in the database.
        Arguments:
            offset: str to indicate the offset for unpacking the
                msgpack file or a string corresponding to the id of the entry
        """
        if isinstance(offset, str):
            self.fileobj.seek(0)
            sep = '%s_%s' % (self._sep, offset)
            offset = self.fileobj.read().find(sep.encode())
            if offset < 0:
                return
            offset += len(sep)
        self.fileobj.seek(offset)
        unpacker = msgpack.Unpacker(self.fileobj, raw=False)
        res = unpacker.unpack()
        return res

    @property
    def ids(self):
        if self._ids is None:
            self._ids = self.get_docs('IDS')
        return self._ids

    def _query_path(self, path_str, path_index):
        data = self.get_docs(path_index)
        if isinstance(data, dict):
            entry = ArchiveFileDB.to_nested_dict(path_str)
            data = ArchiveFileDB.append_data(entry, list(data.values())[0])
            return data
        elif isinstance(data, list):
            d = {}
            for p in data:
                d = ArchiveFileDB.merge_dict(d, self._query(p))
            return d

    def _query(self, path_str):
        data = {}
        if "[:]" in path_str:
            # if path_str contains a wildcard, get all
            path_re = path_str.replace('[:]', '...')
            for path in self.ids:
                if not re.search(path_re, path):
                    continue
                data = ArchiveFileDB.merge_dict(data, self._query(path))
            return data
        else:
            path_indexes = self.ids.get(path_str, [])
        if len(path_indexes) == 0:
            return
        if isinstance(path_indexes, int):
            path_indexes = [path_indexes]
        for path_index in path_indexes:
            datai = self._query_path(path_str, path_index)
            data = ArchiveFileDB.merge_dict(data, datai)
        return data

    def query(self, entries, dtype='dict'):
        """
        Queries the database given a schema.
        Arguments:
            entries: A dictionary with a schema similar to the database
                entries but containing null values corresponding to the
                desired quantity.
            dtype: format of the outfile can be file, string, dict or metainfo
        """
        reduced_entries = self._reduce_to_section(entries)
        path_strs = ArchiveFileDB.to_list_path_str(reduced_entries, root='', paths=[])
        data_to_query = {}
        for path_str in path_strs:
            data_entry = self._query(path_str)
            if data_entry:
                data_to_query = ArchiveFileDB.merge_dict(data_to_query, data_entry)
        result = {}
        if data_to_query:
            jdata = JSONdata(data_to_query)
            result = jdata.get_data(entries)
        if dtype == 'file':
            result = StringIO(json.dumps(result, indent=4))
        elif dtype == 'string':
            result = json.dumps(result)
        else:
            pass
        return result
