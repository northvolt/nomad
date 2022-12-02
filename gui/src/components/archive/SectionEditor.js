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

import React, { useCallback, useMemo, useRef, useState } from 'react'
import PropTypes from 'prop-types'
import { Box, makeStyles, TextField } from '@material-ui/core'
import { useEntryPageContext } from '../entry/EntryPageContext'
import _ from 'lodash'
import ListEditQuantity from '../editQuantity/ListEditQuantity'
import { editQuantityComponents } from '../editQuantity/EditQuantity'
import { QuantityMDef } from './metainfo'

export const JsonEditor = React.memo(function JsonEditor({data, onChange}) {
  const [json, setJson] = useState(JSON.stringify(data, null, 2))
  const [error, setError] = useState(null)

  const handleChange = useCallback((event) => {
    const value = event.target.value
    setJson(value)
    try {
      const data = JSON.parse(value)
      if (onChange) {
        onChange(data)
      }
      setError(null)
    } catch (e) {
      setError('This is not JSON: ' + e)
    }
  }, [onChange, setJson])

  return (
    <TextField
      fullWidth label="JSON" error={!!error}
      helperText={error}
      variant="filled" multiline maxRows={20}
      value={json} onChange={handleChange}
    />
  )
})
JsonEditor.propTypes = {
  data: PropTypes.object.isRequired,
  onChange: PropTypes.func
}

const PropertyEditor = React.memo(function PropertyEditor({quantityDef, value, onChange}) {
  const editAnnotations = quantityDef.m_annotations?.eln || []
  const editAnnotation = editAnnotations[0] || {}
  const {component, props, ...moreProps} = editAnnotation
  const editComponent = component && editQuantityComponents[component]
  if (!editComponent) {
    return null
  }
  const editComponentProps = {
    quantityDef: quantityDef,
    value: value === undefined ? quantityDef.default : value,
    onChange: onChange,
    ...moreProps,
    ...(props || {})
  }

  const shape = quantityDef.shape || []
  if (shape.length === 0) {
    return React.createElement(editComponent, editComponentProps)
  } else if (shape.length === 1) {
    return <ListEditQuantity
      component={editComponent}
      {...editComponentProps}
    />
  } else {
    console.log('Unsupported quantity shape ', shape)
    return null
  }
})
PropertyEditor.propTypes = {
  quantityDef: PropTypes.object.isRequired,
  value: PropTypes.any,
  onChange: PropTypes.func.isRequired
}

const useSectionEditorStyles = makeStyles(theme => ({
  root: {
    minWidth: 600
  }
}))
const SectionEditor = React.memo(function SectionEditor({sectionDef, section, onChange, showJson}) {
  const classes = useSectionEditorStyles()
  const {handleArchiveChanged} = useEntryPageContext()
  const rootRef = useRef()

  const handleChange = useCallback((property, value) => {
    if (section[property.name] === value) {
      return
    }
    section[property.name] = value
    if (onChange) {
      onChange(section)
    }
    handleArchiveChanged()
  }, [section, onChange, handleArchiveChanged])

  const handleJsonChange = useCallback((data) => {
    _.extend(section, data)
    if (onChange) {
      onChange(section)
    }
    handleArchiveChanged()
  }, [handleArchiveChanged, onChange, section])

  const filterHiddenProperties = useCallback((property) => {
    const hiddenPropertyNames = sectionDef?.m_annotations?.eln?.[0]?.hide || []
    return !hiddenPropertyNames.includes(property.name)
  }, [sectionDef])

  const jsonData = useMemo(() => {
    if (!showJson) {
      return null
    }
    const jsonData = {}
    sectionDef._allProperties
      .filter(property => property.m_def === QuantityMDef && property.m_annotations?.eln)
      .filter(property => {
        // TODO this is just a hack to avoid large values, e.g. rich text with images
        const value = section[property.name]
        return !value || typeof value !== 'string' || value.length <= 1e3
      })
      .forEach(property => {
        jsonData[property.name] = section[property.name]
      })
    return jsonData
  }, [showJson, section, sectionDef])

  return (
    <div className={classes.root} ref={rootRef}>
      {showJson
        ? (
          <Box height={rootRef.current?.clientHeight} marginY={1}>
            <JsonEditor data={jsonData} onChange={handleJsonChange} />
          </Box>
        ) : (
          sectionDef._allProperties.filter(filterHiddenProperties).map(property => (
            <Box marginBottom={1} key={property.name}>
              <PropertyEditor
                quantityDef={property}
                value={section?.[property.name]} onChange={value => handleChange(property, value)}
              />
            </Box>
          ))
        )
      }
    </div>
  )
})
SectionEditor.propTypes = {
  sectionDef: PropTypes.object.isRequired,
  section: PropTypes.object,
  onChange: PropTypes.func,
  showJson: PropTypes.bool,
  children: PropTypes.oneOfType([
    PropTypes.arrayOf(PropTypes.node),
    PropTypes.node
  ])
}

export default SectionEditor
