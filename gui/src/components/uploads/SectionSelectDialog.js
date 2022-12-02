/*
 * Copyright The NOMAD Authors.
 *
 * This file is part of NOMAD. See https://nomad-lab.eu for further info.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
import React, {useCallback, useContext, useEffect, useMemo, useState} from 'react'
import {
  makeStyles, DialogContent, Dialog, Typography, Checkbox, FormControlLabel,
  Box, MenuItem, ListItemIcon, ListItemText, MenuList, Chip
} from '@material-ui/core'
import Button from '@material-ui/core/Button'
import DialogActions from '@material-ui/core/DialogActions'
import PropTypes from 'prop-types'
import {SearchContext, useSearchContext} from "../search/SearchContext"
import {ui} from "../../config"
import SearchBar from '../search/SearchBar'
import {useApi} from '../api'
import {useUploadPageContext} from './UploadPageContext'
import {useEntryPageContext} from '../entry/EntryPageContext'
import {traverse, useGlobalMetainfo} from '../archive/metainfo'
import { filterGroups } from '../search/FilterRegistry'
import SearchResults from '../search/SearchResults'
import {useDataStore} from '../DataStore'
import {pluralize, resolveNomadUrlNoThrow} from "../../utils"
import {Check} from '@material-ui/icons'

const searchDialogContext = React.createContext()
const context = ui?.search_contexts?.options?.entries

const allFilters = new Set(filterGroups && context?.filter_menus?.include
      .map(filter => {
        const group = filterGroups?.[filter]
        return group ? Array.from(group) : []
      }).flat())

const useStyles = makeStyles(theme => ({
  dialog: {
    width: '100%',
    minWidth: 1100,
    minHeight: 400
  },
  resultsTable: {
    overflowY: 'scroll',
    maxHeight: 700
  },
  searchBar: {
    display: 'flex',
    flexGrow: 0,
    zIndex: 1
  },
  filters: {
    marginTop: 10
  }
}))

const shownColumns = [
  'entry_name',
  'entry_type',
  'authors',
  'upload_create_time'
]

const Details = React.memo(({data}) => {
  const {api} = useApi()
  const entry_id = data?.entry_id
  const globalMetainfo = useGlobalMetainfo()
  const dataStore = useDataStore()
  const {useFiltersLockedState} = useSearchContext()
  const filtersLocked = useFiltersLockedState(['sections', 'entry_type'])
  const {selected, onSelectedChanged} = useContext(searchDialogContext)
  const [sections, setSections] = useState()
  const {url} = useEntryPageContext() || {}

  useEffect(() => {
    if (entry_id && globalMetainfo) {
      fetch()
    }
    async function fetch() {
      if (filtersLocked.entry_type?.has('Schema')) {
        const customMetainfo = await globalMetainfo.fetchAllCustomMetainfos(true, {entry_id: entry_id})
        const schemaArchive = customMetainfo[0]?._data
        const sectionDefs = schemaArchive?.definitions?.section_definitions
        setSections(sectionDefs.map(sectionDef => {
          return {
            label: sectionDef.name,
            entry_id: schemaArchive.metadata.entry_id,
            value: sectionDef.name,
            data: {sectionDef: sectionDef, archive: schemaArchive}
          }
        }))
      } else {
        const response = await api.post(`entries/archive/query`, {
          owner: 'visible',
          query: {
            entry_id: entry_id,
            processed: true
          }
        })
        const dataArchive = response?.data?.[0].archive
        const m_def = dataArchive?.data?.m_def
        const referencedSubSections = []
        if (m_def) {
          const dataMetainfoDefUrl = resolveNomadUrlNoThrow(m_def, url)
          const sectionDef = await dataStore.getMetainfoDefAsync(dataMetainfoDefUrl)
          traverse(dataArchive?.data, sectionDef, 'data', (section, sectionDef, path) => {
            if (filtersLocked.sections && sectionDef._qualifiedName === [...filtersLocked.sections][0]) {
              referencedSubSections.push({name: `./${path}`, upload_id: response?.data?.[0]?.upload_id, entry_id: response?.data?.[0]?.entry_id, path: path})
            }
          })
        } else {
          referencedSubSections.push({name: `./data`, upload_id: response?.data?.[0]?.upload_id, entry_id: response?.data?.[0]?.entry_id, path: '/data'})
        }
        const references = referencedSubSections || []
        setSections(references.map(reference => ({label: reference.name, entry_id: reference.entry_id, value: reference.path, data: reference})))
      }
    }
  }, [api, dataStore, entry_id, filtersLocked.entry_type, filtersLocked.sections, globalMetainfo, url])

  if (!sections) {
    return ''
  }

  return (
    <MenuList dense>
      {sections.map((section, index) => {
        const isSelected = selected && section.entry_id === selected.entry_id && section.value === selected.value
        return (
          <MenuItem key={`${entry_id}-${index + 1}`} value={section.label} onClick={() => onSelectedChanged(section)}>
            {isSelected && <ListItemIcon>
              <Check data-testid='check-icon'/>
            </ListItemIcon>}
            <ListItemText inset={!isSelected}>
              {section.label}
            </ListItemText>
          </MenuItem>
        )
      })}
    </MenuList>
  )
})
Details.propTypes = {
  data: PropTypes.object.isRequired
}

function SearchBox({open, onCancel, onSelectedChanged, selected}) {
  const classes = useStyles()
  const {user} = useApi()
  const [onlyMine, setOnlyMine] = useState(!!user?.sub)
  const [onlyThisUpload, setOnlyThisUpload] = useState(false)
  const {useFilterState, useFiltersState, useFiltersLocked} = useSearchContext()
  const filtersLocked = useFiltersLocked()
  const [filters, setFilters] = useFiltersState([...allFilters].filter(filter => filter !== 'visibility' && filter !== 'upload_id' && !filtersLocked[filter]))
  const uploadContext = useUploadPageContext()
  const entryContext = useEntryPageContext()
  const {uploadId} = uploadContext || entryContext
  const [, setVisibilityFilter] = useFilterState('visibility')
  const [, setUploadIdFilter] = useFilterState('upload_id')

  const handleCancel = useCallback(() => {
    onCancel()
  }, [onCancel])

  useEffect(() => {
    setVisibilityFilter(onlyMine ? 'user' : undefined)
    setUploadIdFilter(onlyThisUpload ? uploadId : undefined)
  }, [onlyMine, onlyThisUpload, setUploadIdFilter, setVisibilityFilter, uploadId, user])

  const contextValue = useMemo(() => ({
    selected: selected,
    onSelectedChanged: onSelectedChanged
  }), [onSelectedChanged, selected])

  const definedFilters = useMemo(() => {
    return Object.values(filters).filter(value => {
      return value !== undefined
    })
  }, [filters])

  const handleResetSearch = () => {
    return Object.keys(filters).forEach(key => {
      setFilters([key, undefined])
    })
  }

  return <searchDialogContext.Provider value={contextValue}>
    <Dialog classes={{paper: classes.dialog}} open={open} data-testid='section-select-dialog'>
      <DialogContent>
        <SearchBar className={classes.searchBar} />
        <Typography className={classes.filters} variant="body1">
          Filters
        </Typography>
        <Box display='flex'>
          <FormControlLabel
              control={<Checkbox
                  onChange={event => setOnlyMine(event.target.checked)}
                  color='primary'
                  checked={onlyMine}
                  name='Only mine'
                  disabled={!user || onlyThisUpload}
              />}
              label='Only mine'
          />
          <FormControlLabel
              control={<Checkbox
                  onChange={event => setOnlyThisUpload(event.target.checked)}
                  color='primary'
                  checked={onlyThisUpload}
                  name='Only this upload'
                  disabled={!user}
              />}
              label='Only this upload'
          />
          {definedFilters.length > 0 && <Chip label={`and ${definedFilters.length} more ${pluralize('filter', definedFilters.length, false)}`} color="primary" onDelete={() => handleResetSearch()}/>}
        </Box>
        <div className={classes.resultsTable}>
          <SearchResults
            defaultUncollapsedEntryID={selected?.entry_id}
            multiSelect={false}
            noAction
          />
        </div>
      </DialogContent>
      <DialogActions>
        <span style={{flexGrow: 1}} />
        <Button onClick={handleCancel} color="secondary">
          Cancel
        </Button>
      </DialogActions>
    </Dialog>
  </searchDialogContext.Provider>
}
SearchBox.propTypes = {
  open: PropTypes.bool,
  onCancel: PropTypes.func,
  onSelectedChanged: PropTypes.func,
  selected: PropTypes.object
}

function SectionSelectDialog(props) {
  const {open, onSelectedChanged, selected, onCancel, filtersLocked} = props

  const columns = context?.columns
  const rows = context?.rows
  columns['enable'] = shownColumns
  rows['details'] = {enable: true, render: Details}
  rows['actions'] = {enable: false}

  return <React.Fragment>
    {open && <SearchContext
      resource={context?.resource}
      initialPagination={context?.pagination}
      initialColumns={columns}
      initialRows={rows}
      initialFilterMenus={context?.filter_menus}
      initialFiltersLocked={filtersLocked}
    >
      <SearchBox open={open} onCancel={onCancel} onSelectedChanged={onSelectedChanged} selected={selected}/>
    </SearchContext>}
  </React.Fragment>
}
SectionSelectDialog.propTypes = {
  open: PropTypes.bool,
  onSelectedChanged: PropTypes.func,
  selected: PropTypes.object,
  onCancel: PropTypes.func,
  filtersLocked: PropTypes.object
}

export default SectionSelectDialog
