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
import React, { useCallback, useState, useMemo } from 'react'
import PropTypes from 'prop-types'
import clsx from 'clsx'
import Autocomplete from '@material-ui/lab/Autocomplete'
import { makeStyles } from '@material-ui/core/styles'
import SearchIcon from '@material-ui/icons/Search'
import CloseIcon from '@material-ui/icons/Close'
import {
  TextField,
  CircularProgress,
  Paper,
  Divider,
  Tooltip
} from '@material-ui/core'
import IconButton from '@material-ui/core/IconButton'
import { useApi } from '../apiV1'
import { useFiltersState } from './FilterContext'
import searchQuantities from '../../searchQuantities'

// Decides which options are shown
const filterOptions = (options, {inputValue}) => {
  const trimmed = inputValue.trim().toLowerCase()
  return options.filter(option => {
    // ES results do not need to be filtered at all
    const category = option.category
    if (category !== 'quantity name') {
      return true
    }
    // Underscore can be replaced by a whitespace
    const optionClean = option.value.trim().toLowerCase()
    const matchUnderscore = optionClean.includes(trimmed)
    const matchNoUnderscore = optionClean.replaceAll('_', ' ').includes(trimmed)
    return matchUnderscore || matchNoUnderscore
  })
}

const useStyles = makeStyles(theme => ({
  root: {
    display: 'flex',
    alignItems: 'center'
  },
  notchedOutline: {
    borderColor: 'rgba(0, 0, 0, 0.0)'
  },
  iconButton: {
    padding: 10
  },
  divider: {
    height: '2rem'
  },
  endAdornment: {
    position: 'static'
  }
}))

const quantityAbbreviations = new Map()
const quantityFullnames = new Map()
const nonUniques = new Set()
const quantityNames = Object.keys(searchQuantities).filter(q => q.startsWith('results'))
const abbreviationList = quantityNames.map(name => name.split('.').pop())
const abbreviationSet = new Set()
for (let abbr of abbreviationList) {
  if (abbreviationSet.has(abbr)) {
    nonUniques.add(abbr)
  }
  abbreviationSet.add(abbr)
}
for (let i = 0; i < quantityNames.length; ++i) {
  const abbr = abbreviationList[i]
  const name = quantityNames[i]
  const unique = !nonUniques.has(abbr)
  if (unique) {
    quantityAbbreviations.set(name, abbr)
    quantityFullnames.set(abbr, name)
  }
}

const opMap = {
  '<=': 'lte',
  '>=': 'gte',
  '>': 'gt',
  '<': 'lt'
}
const opMapReverse = {
  '<=': 'gte',
  '>=': 'lte',
  '>': 'lt',
  '<': 'gt'
}

/**
 * This searchbar component shows a searchbar with autocomplete functionality. The
 * searchbar also includes a status line about the current results. It uses the
 * search context to manipulate the current query and display results. It does its on
 * API calls to provide autocomplete suggestion options.
 */
const NewSearchBar = React.memo(({
  quantities,
  className
}) => {
  const styles = useStyles()
  const [suggestions, setSuggestions] = useState([])
  const [loading, setLoading] = useState(false)
  const [inputValue, setInputValue] = useState('')
  const [highlighted, setHighlighted] = useState({value: ''})
  const [open, setOpen] = useState(false)
  const [error, setError] = useState(false)
  // const [value, setValue] = useState({value: 'moi', category: 'moi'})
  const api = useApi()
  const setFilter = useFiltersState(quantities)[1]
  const quantitySet = useMemo(() => new Set(quantities), [quantities])
  const quantitySuggestions = useMemo(() => {
    return quantities.map(q => ({
      value: quantityAbbreviations.get(q) || q,
      category: 'quantity name'
    }))
  }, [quantities])

  // Triggered when a value is submitted by pressing enter or clicking the
  // search icon.
  const handleSubmit = useCallback(() => {
    if (inputValue.trim().length === 0) {
      return
    }
    const reString = '\\S(?:.*\\S)?'
    let valid = false
    let quantityFullname
    let queryValue

    // Equality query
    const equals = inputValue.match(new RegExp(`^\\s*(${reString})\\s*=\\s*(${reString})\\s*$`))
    if (equals) {
      const quantityName = equals[1]
      queryValue = equals[2]
      quantityFullname = quantityFullnames.get(quantityName)
      if (!quantitySet.has(quantityFullname)) {
        setError(`Unknown quantity name`)
        return
      }
      valid = true
    }

    // Simple LTE/GTE query
    if (!valid) {
      const ltegte = inputValue.match(new RegExp(`^\\s*(${reString})\\s*(<|>=?)\\s*(${reString})\\s*$`))
      if (ltegte) {
        const a = ltegte[1]
        const op = ltegte[2]
        const b = ltegte[3]
        const aFullname = quantityFullnames.get(a)
        const bFullname = quantityFullnames.get(b)
        const isAQuantity = quantitySet.has(aFullname)
        const isBQuantity = quantitySet.has(bFullname)
        if (!isAQuantity && !isBQuantity) {
          setError(`Unknown quantity name`)
          return
        }
        quantityFullname = isAQuantity ? aFullname : bFullname
        const quantityValue = Number.parseFloat(isAQuantity ? b : a)
        if (isNaN(quantityValue)) {
          setError(`Invalid number`)
          return
        }
        const type = searchQuantities[quantityFullname].type
        if (!type.type_data.startsWith('int') && !type.type_data.startsWith('float')) {
          setError(`Cannot perform range query for a non-numeric quantity.`)
          return
        }
        queryValue = {}
        queryValue[opMap[op]] = quantityValue
        valid = true
      }
    }

    // Sandwiched LTE/GTE query
    if (!valid) {
      const ltegteSandwich = inputValue.match(new RegExp(`^\\s*(${reString})\\s*(<|>=?)\\s*(${reString})\\s*(<|>=?)\\s*(${reString})\\s*$`))
      if (ltegteSandwich) {
        const a = ltegteSandwich[1]
        const op1 = ltegteSandwich[2]
        const b = ltegteSandwich[3]
        const op2 = ltegteSandwich[4]
        const c = ltegteSandwich[5]
        quantityFullname = quantityFullnames.get(b)
        const isBQuantity = quantitySet.has(quantityFullname)
        if (!isBQuantity) {
          setError(`Unknown quantity name`)
          return
        }
        const aValue = Number.parseFloat(a)
        const cValue = Number.parseFloat(c)
        if (isNaN(aValue) || isNaN(cValue)) {
          setError(`Invalid number`)
          return
        }
        const type = searchQuantities[quantityFullname].type
        if (!type.type_data.startsWith('int') && !type.type_data.startsWith('float')) {
          setError(`Cannot perform range query for a non-numeric quantity.`)
          return
        }
        queryValue = {}
        queryValue[opMapReverse[op1]] = aValue
        queryValue[opMap[op2]] = cValue
        valid = true
      }
    }

    if (valid) {
      // Submit to search context on successful validation.
      setFilter([quantityFullname, queryValue])
      setInputValue('')
      setOpen(false)
    } else {
      setError(`Invalid query`)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inputValue, quantitySet])

  // Handle clear button
  const handleClose = useCallback(() => {
    setInputValue('')
  }, [])

  const handleHighlight = useCallback((event, value, reason) => {
    setHighlighted(value)
  }, [])

  // When enter is pressed, select currently highlighted value and close menu,
  // or if menu is not open submit the value.
  const handleEnter = useCallback((event) => {
    if (event.key === 'Enter') {
      if (open && highlighted?.value) {
        setInputValue(highlighted.value)
        setOpen(false)
      } else {
        handleSubmit()
      }
      event.stopPropagation()
      event.preventDefault()
    }
  }, [open, highlighted, handleSubmit])

  // Handle typing events. After a debounce time has expired, a list of
  // suggestion will be retrieved if they are available for this metainfo and
  // the input is deemed meaningful.
  const handleInputChange = useCallback((event, value, reason) => {
    setError(error => error ? undefined : null)
    setInputValue(value)
    value = value?.trim()
    if (!value || reason !== 'input') {
      setSuggestions([])
      return
    }
    // If the input is prefixed with a proper quantity name and an equals-sign,
    // we extract the quantity name and the typed input
    const split = value.split('=', 2)
    let quantityList = quantities
    if (split.length === 2) {
      const quantityName = split[0].trim()
      const quantityFullname = quantityFullnames.get(quantityName)
      if (quantitySet.has(quantityName)) {
        quantityList = [quantityName]
        value = split[1].trim()
      } else if (quantitySet.has(quantityFullname)) {
        quantityList = [quantityFullname]
        value = split[1].trim()
      }
    }

    setLoading(true)
    // If some input is given, and the quantity supports suggestions, we use
    // input suggester to suggest values
    if (value.length > 0) {
      const filteredList = quantityList.filter(q => searchQuantities[q].suggestion)
      api.suggestions(filteredList, value)
        .then(data => {
          let res = []
          for (let q of filteredList) {
            const name = quantityAbbreviations.get(q) || q
            const esSuggestions = data[q]
            if (esSuggestions) {
              res = res.concat(esSuggestions.map(suggestion => ({
                value: `${name}=${suggestion.value}`,
                category: name
              })))
            }
          }
          setSuggestions(res)
        })
        .finally(() => setLoading(false))
    // If no input is given, we suggest Enum values, or for non-enum quantities
    // use terms aggregation.
    } else {
    }
  }, [quantities, api, quantitySet])

  return <Paper className={clsx(className, styles.root)}>
    <Autocomplete
      className={styles.input}
      freeSolo
      clearOnBlur={false}
      inputValue={inputValue}
      value={null}
      open={open}
      onOpen={() => setOpen(true)}
      onClose={() => setOpen(false)}
      fullWidth
      disableClearable
      classes={{endAdornment: styles.endAdornment}}
      groupBy={(option) => option.category}
      filterOptions={filterOptions}
      options={quantitySuggestions.concat(suggestions)}
      onInputChange={handleInputChange}
      onHighlightChange={handleHighlight}
      getOptionLabel={option => option.value}
      getOptionSelected={(option, value) => false}
      renderInput={(params) => (
        <TextField
          {...params}
          className={styles.textField}
          variant="outlined"
          placeholder=""
          label={error || undefined}
          error={!!error}
          onKeyDown={handleEnter}
          InputLabelProps={{ shrink: true }}
          InputProps={{
            ...params.InputProps,
            classes: {
              notchedOutline: styles.notchedOutline
            },
            endAdornment: (<>
              {loading ? <CircularProgress color="inherit" size={20} /> : null}
              {(inputValue?.length || null) && <>
                <Tooltip title="Clear">
                  <IconButton onClick={handleClose} className={styles.iconButton} aria-label="clear">
                    <CloseIcon />
                  </IconButton>
                </Tooltip>
                <Divider className={styles.divider} orientation="vertical"/>
              </>}
              <Tooltip title="Add filter">
                <IconButton onClick={handleSubmit} className={styles.iconButton} aria-label="search">
                  <SearchIcon />
                </IconButton>
              </Tooltip>
            </>)
          }}
        />
      )}
    />
  </Paper>
})

NewSearchBar.propTypes = {
  quantities: PropTypes.arrayOf(PropTypes.string), // The set of search quantities over which the suggestions are served.
  className: PropTypes.string
}

export default NewSearchBar
