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
import React, { useContext, useRef, useState, useMemo, useCallback } from 'react'
import PropTypes from 'prop-types'
import clsx from 'clsx'
import { IconButton, makeStyles, lighten, TableHead, TableRow, TableCell, TableSortLabel,
  Checkbox, TableContainer, Table, TableBody, TablePagination, Box, Collapse, Toolbar, Typography,
  List, ListItem, ListItemText, Popover, CircularProgress, Button } from '@material-ui/core'
import TooltipButton from '../utils/TooltipButton'
import EditColumnsIcon from '@material-ui/icons/ViewColumn'
import InfiniteScroll from 'react-infinite-scroller'
import searchQuantities from '../../searchQuantities'

const DatatableContext = React.createContext({})
const StaticDatatableContext = React.createContext({})

/**
 * Combines a pagination request object with an reponse object. This allows to create
 * pagination object as they are expected by Datatable.
 *
 * @param {object} request The request pagination object (i.e. without total, next_page_afte_value, etc.)
 * @param {object} response The response pagination object (returned by NOMAD API)
 * @returns The combined pagination object.
 */
export function combinePagination(request, response) {
  const result = {
    page_size: request.page_size || response?.page_size,
    order_by: request.order_by || response?.order_by,
    order: request.order || response?.order,
    page: request.page || response?.page,
    total: response?.total
  }

  if (!result.page) {
    result.page_after_value = request.page_after_value || response?.page_after_value
    result.next_page_after_value = response?.next_page_after_value
  }

  return result
}

/**
 * Ensures that all given columns have default values for necessary keys.
 * @param {array} columns An array of columns to extend/check.
 */
export function addColumnDefaults(columns, moreDefaults) {
  columns.forEach(column => {
    if (moreDefaults) {
      Object.keys(moreDefaults).filter(key => !column[key]).forEach(key => { column[key] = moreDefaults[key] })
    }
    if (!column.label) {
      const keySegments = column.key.split('.')
      const name = keySegments[keySegments.length - 1]
      column.label = name.replace(/_/g, ' ')
      column.label = column.label[0].toUpperCase() + column.label.slice(1)
    }
    if (!column.render) {
      const segments = column.key.split('.')
      column.render = (row) => segments.reduce((current, segment) => current && current[segment], row)
    }
    if (column.sortable !== false) {
      column.sortable = true
    }
    if (!column.align) {
      column.align = 'center'
    }
    if (!column.description) {
      column.description = searchQuantities[column.key]?.description
    }
  })
}

function useDatatableContext() {
  const context = useContext(DatatableContext)
  return context
}

function useStaticDatatableContext() {
  const context = useContext(StaticDatatableContext)
  return context
}

/** The page based table pagination. Must be a child of DatatableTable component. */
export const DatatablePagePagination = React.memo(function DatatablePagePagination({pageSizeValues}) {
  const {pagination, onPaginationChanged} = useDatatableContext()

  const handleChangePage = (event, newPage) => {
    onPaginationChanged({
      ...pagination,
      page: newPage + 1
    })
  }

  const handleChangeRowsPerPage = (event) => {
    onPaginationChanged({
      ...pagination,
      page: 1,
      page_size: parseInt(event.target.value, 10)
    })
  }

  return <TablePagination
    rowsPerPageOptions={pageSizeValues || [5, 10, 50, 100]}
    component="div"
    count={pagination.total}
    rowsPerPage={pagination.page_size}
    page={pagination.page - 1}
    onPageChange={handleChangePage}
    onChangeRowsPerPage={handleChangeRowsPerPage}
  />
})
DatatablePagePagination.propTypes = {
  /** Optional array of selectable page size values. Default is [5, 10, 50, 100]. */
  pageSizeValues: PropTypes.arrayOf(PropTypes.number)
}

const useDatatableLoadMorePaginationStyles = makeStyles(theme => ({
  root: {
    padding: theme.spacing(1),
    display: 'flex',
    flexDirection: 'row',
    justifyContent: 'center',
    alignItems: 'center',
    flexWrap: 'nowrap'
  }
}))

/** Pagination that uses a "load more button" if more data is available. */
export const DatatableLoadMorePagination = React.memo(function DatatableLoadMorePagination(props) {
  const classes = useDatatableLoadMorePaginationStyles()
  const {pagination, onPaginationChanged} = useDatatableContext()

  const handleClick = useCallback(() => {
    onPaginationChanged({
      ...pagination,
      page_after_value: pagination.next_page_after_value
    })
  }, [pagination, onPaginationChanged])

  if (pagination.next_page_after_value) {
    if (pagination.page_after_value === pagination.next_page_after_value) {
      return <div className={classes.root}>
        <CircularProgress size={36} />
      </div>
    }

    return <div className={classes.root}>
      <Button
        {...props}
        onClick={handleClick}
      />
    </div>
  }

  return ''
})
DatatableLoadMorePagination.propTypes = {}

const useDateatableScollPaginationStyles = makeStyles(theme => ({
  root: {
    padding: theme.spacing(2),
    display: 'flex',
    flexDirection: 'row',
    justifyContent: 'center',
    alignItems: 'center',
    flexWrap: 'nowrap',
    fontWeight: 'bold',
    '& > *': {
      paddingLeft: theme.spacing(1)
    }
  }
}))

/** The scroll based table pagination. Must be a child of DatatableTable component. */
export const DatatableScrollPagination = React.memo(function DatatablePagePagination({loadMessage}) {
  const {pagination} = useDatatableContext()
  const classes = useDateatableScollPaginationStyles()
  if (pagination.next_page_after_value && (pagination.next_page_after_value === pagination.page_after_value)) {
    return <div className={classes.root}>
      <CircularProgress size={20} />
      <Typography color="primary">{loadMessage || 'oading more items ...'}</Typography>
    </div>
  } else {
    return null
  }
})
DatatableScrollPagination.propTypes = {
  loadMessage: PropTypes.string
}

const useDatatableHeaderStyles = makeStyles(theme => ({
  root: {},
  visuallyHidden: {
    border: 0,
    clip: 'rect(0 0 0 0)',
    height: 1,
    margin: -1,
    overflow: 'hidden',
    padding: 0,
    position: 'absolute',
    top: 20,
    width: 1
  },
  stickyHeader: {
    background: '#fff'
  }
}))

const DatatableHeader = React.memo(function DatatableHeader({actions}) {
  const classes = useDatatableHeaderStyles()
  const {
    withSelectionFeature,
    selected,
    onSelectedChanged,
    shownColumns,
    sortingColumns,
    pagination,
    onPaginationChanged
  } = useDatatableContext()
  const columns = shownColumns
  const {order, order_by} = pagination
  const withSorting = !!onPaginationChanged

  const handleSelectAllChanged = () => {
    if (selected === 'all') {
      onSelectedChanged([])
    } else {
      onSelectedChanged('all')
    }
  }

  const createSortHandler = (column) => (event) => {
    const isAsc = order_by === column.key && order === 'asc'
    if (onPaginationChanged) {
      onPaginationChanged({
        ...pagination,
        order: isAsc ? 'desc' : 'asc',
        order_by: column.key
      })
    }
  }

  return <TableHead>
    <TableRow>
      {withSelectionFeature && <TableCell padding="checkbox" classes={{stickyHeader: classes.stickyHeader}}>
        <Checkbox
          indeterminate={selected.length > 0 && selected !== 'all'}
          checked={selected === 'all'}
          onChange={handleSelectAllChanged}
        />
      </TableCell>}
      {columns.map(column => (
        <TableCell
          classes={{stickyHeader: classes.stickyHeader}}
          key={column.key}
          align={column.align || 'right'}
          sortDirection={order_by === column.key ? order : false}
        >
          {withSorting && sortingColumns?.includes(column.key) ? <TableSortLabel
            active={order_by === column.key}
            direction={order_by === column.key ? order : 'asc'}
            onClick={createSortHandler(column)}
          >
            {column.label || column.key}
            {order_by === column.key ? (
              <span className={classes.visuallyHidden}>
                {order === 'desc' ? 'sorted descending' : 'sorted ascending'}
              </span>
            ) : null}
          </TableSortLabel> : column.label || column.key}
        </TableCell>
      ))}
      {actions && <TableCell classes={{stickyHeader: classes.stickyHeader}} />}
    </TableRow>
  </TableHead>
})
DatatableHeader.propTypes = {
  actions: PropTypes.oneOfType([
    PropTypes.elementType,
    PropTypes.func
  ])
}

const useDatatableRowStyles = makeStyles(theme => ({
  rowWithDetails: {
    '& > *': {
      borderBottom: 'unset'
    }
  },
  rowWithUncollapsedDetails: {
    backgroundColor: `${theme.palette.primary.main} !important`,
    '& *': {
      fontWeight: 'bold',
      color: `${theme.palette.primary.contrastText} !important`
    }
  },
  rowClickable: {
    cursor: 'pointer'
  },
  rowActionsCell: {
    textAlign: 'right',
    width: 1,
    whiteSpace: 'nowrap',
    paddingTop: 0,
    paddingBottom: 0
  },
  detailsCell: {
    paddingTop: 0,
    paddingBottom: 0
  }
}))

const DatatableRow = React.memo(function DatatableRow({data, selected, uncollapsed, onRowUncollapsed, actions, details}) {
  const classes = useDatatableRowStyles()
  const {withSelectionFeature, shownColumns, onSelectedChanged} = useStaticDatatableContext()
  const columns = shownColumns
  const row = data
  const numberOfColumns = columns.length + (withSelectionFeature ? 1 : 0) + (actions ? 1 : 0)

  const handleRowCollapseChange = (event) => {
    event.stopPropagation()
    onRowUncollapsed(uncollapsed ? null : row)
  }

  const handleSelect = onSelectedChanged ? (event) => {
    event.stopPropagation()
    onSelectedChanged(selected => {
      if (selected === 'all') {
        return [row]
      }
      const index = selected.map(selectedRow => selectedRow.entry_id).indexOf(row.entry_id)
      if (index > -1) {
        return [...selected.slice(0, index), ...selected.slice(index + 1)]
      } else {
        return [...selected, row]
      }
    })
  } : null

  return <>
    <TableRow
      className={clsx({
        [classes.rowWithUncollapsedDetails]: uncollapsed,
        [classes.rowWithDetails]: details,
        [classes.rowClickable]: details || withSelectionFeature
      })}
      hover
      onClick={details ? handleRowCollapseChange : handleSelect}
      role="checkbox"
      tabIndex={-1}
      selected={selected}
    >
      {withSelectionFeature && <TableCell padding="checkbox">
        <Checkbox
          checked={selected}
          onClick={handleSelect}
        />
      </TableCell>}
      {columns.map(column => <TableCell key={column.key} align={column.align || 'right'} style={column.style}>
        {(column?.render && column?.render(row)) || row[column.key] || ''}
      </TableCell>)}
      {actions && <TableCell
        align="right" size="small" className={classes.rowActionsCell}
        onClick={(event) => event.stopPropagation()}
      >
        {React.createElement(actions, {data: row})}
      </TableCell>}
    </TableRow>
    {details && <TableRow selected={selected}>
      <TableCell className={classes.detailsCell} colSpan={numberOfColumns}>
        <Collapse in={uncollapsed} timeout="auto" unmountOnExit>
          <Box margin={1}>
            {React.createElement(details, {data: row})}
          </Box>
        </Collapse>
      </TableCell>
    </TableRow>}
  </>
})
DatatableRow.propTypes = {
  data: PropTypes.object.isRequired,
  selected: PropTypes.bool,
  uncollapsed: PropTypes.bool,
  onRowUncollapsed: PropTypes.func.isRequired,
  actions: PropTypes.elementType,
  details: PropTypes.elementType
}

const useDatatableTableStyles = makeStyles(theme => ({
  tableContainer: {
    flexGrow: 1
  }
}))

/** The actional table, including pagination. Must be child of a Datatable component. */
export const DatatableTable = React.memo(function DatatableTable({children, actions, details}) {
  const classes = useDatatableTableStyles()
  const {shownColumns, data, pagination, onPaginationChanged, selected, withSelectionFeature} = useDatatableContext()
  const {page_size} = pagination
  const emptyRows = Math.max(0, page_size - data.length)
  const columns = shownColumns
  const numberOfColumns = columns.length + (withSelectionFeature ? 1 : 0) + (actions ? 1 : 0)
  const isScrolling = children?.type === DatatableScrollPagination
  const isExtending = children?.type === DatatableScrollPagination || children?.type === DatatableLoadMorePagination
  const scrollParentRef = useRef(null)

  const [uncollapsedRow, setUncollapsedRow] = useState(null)

  const handleLoadMore = useCallback(() => {
    onPaginationChanged(pagination => {
      const {next_page_after_value, page_after_value} = pagination
      if (next_page_after_value && page_after_value !== next_page_after_value) {
        return {
          ...pagination,
          page_after_value: next_page_after_value
        }
      } else {
        return pagination
      }
    })
  }, [onPaginationChanged])

  let dataToShow
  if (isExtending) {
    // If list extends, all data is rendered regardless of pagination.
    dataToShow = data
  } else {
    // If used correctly, data should give given to Datatable already correctly sliced.
    // We simply enforce that data is not longer than page_size, just in case.
    dataToShow = data.slice(data.length < pagination.page_size ? 0 : data.length - pagination.page_size)
  }

  const table = <Table size="medium" stickyHeader={isScrolling}>
    <DatatableHeader actions={actions}/>
    <TableBody>
      {dataToShow.map((row, index) => (
        <DatatableRow
          actions={actions}
          details={details}
          key={index}
          selected={selected === 'all' || selected?.map(selectedRow => selectedRow.entry_id).includes(row.entry_id)}
          uncollapsed={row === uncollapsedRow}
          data={row}
          onRowUncollapsed={setUncollapsedRow}
        />
      ))}
      {!isExtending && (emptyRows > 0) && (
        <TableRow style={{ height: 53 * emptyRows }}>
          <TableCell colSpan={numberOfColumns} />
        </TableRow>
      )}
      <TableRow style={{ height: '1px' }}/>
    </TableBody>
  </Table>

  return <TableContainer ref={scrollParentRef} className={classes.tableContainer}>
    {isScrolling ? (
      <InfiniteScroll
        pageStart={0}
        loadMore={handleLoadMore}
        hasMore={!!pagination.next_page_after_value}
        useWindow={false}
        getScrollParent={() => scrollParentRef.current}
      >
        {table}
      </InfiniteScroll>
    ) : (
      table
    )}
    {children}
  </TableContainer>
})
DatatableTable.propTypes = {
  /** Objectal render function or component for row details. Function and component
   * get row object as "data" prop. */
  details: PropTypes.elementType,
  /** Objectal render function or component for row actions. Function and component
   * get row object as "data" prop. */
  actions: PropTypes.elementType,
  /** Optional pagination component, e.g. DatatablePagePagination. */
  children: PropTypes.node
}

/** Actions displayed in a DatatableToolbar. Must be child of a DatatableToolbar. */
export const DatatableToolbarActions = React.memo(function DatatableToolbarActions({children, selection}) {
  const {selected} = useDatatableContext()

  const hasSelection = selected?.length > 0
  if ((hasSelection && selection) || (!hasSelection && !selection)) {
    return <React.Fragment>
      {children}
    </React.Fragment>
  }

  return ''
})
DatatableToolbarActions.propTypes = {
  /** If true, this component is only shown if the surrounding table as an active selection. */
  selection: PropTypes.bool,
  children: PropTypes.oneOfType([
    PropTypes.arrayOf(PropTypes.node),
    PropTypes.node
  ])
}

const useDatatableToolbarStyles = makeStyles(theme => ({
  root: {
    paddingLeft: theme.spacing(2),
    paddingRight: theme.spacing(1)
  },
  highlight: {
    color: theme.palette.secondary.main,
    backgroundColor: lighten(theme.palette.secondary.light, 0.85),
    '& *': {
      color: theme.palette.secondary.main
    }
  },
  title: {
    flex: '1 1 100%'
  }
}))

/** A toolbar shown on top of tables. It shows a title, general table actions, and actions
 * on selected rows. Must be child of a Datatable */
export const DatatableToolbar = React.memo(function DatatableToolbar({children, title, hideColumns}) {
  const classes = useDatatableToolbarStyles()
  const {selected} = useDatatableContext()
  return (
    <Toolbar
      className={clsx(classes.root, {
        [classes.highlight]: selected?.length > 0
      })}
    >
      {selected?.length > 0 ? (
        <Typography className={classes.title} color="inherit" variant="subtitle1" component="div">
          {selected === 'all' ? 'All' : selected.length} selected
        </Typography>
      ) : (
        <Typography className={classes.title} variant="h6" id="tableTitle" component="div">
          {title || ''}
        </Typography>
      )}
      {children}
      {!hideColumns && !(selected?.length > 0) && <DatatableColumnSelector />}
    </Toolbar>
  )
})
DatatableToolbar.propTypes = {
  /** Optional table title */
  title: PropTypes.string,
  hideColumns: PropTypes.bool,
  /** Children, e.g. DatatableToolbarActions for general and selection actions. */
  children: PropTypes.oneOfType([
    PropTypes.arrayOf(PropTypes.node),
    PropTypes.node
  ])
}

const DatatableColumnSelector = React.memo(function DatatableColumnSelector({children}) {
  const [anchorEl, setAnchorEl] = useState(null)
  const {columns, shownColumns, onShownColumnsChanged} = useDatatableContext()

  const handleToggle = (column) => {
    const index = shownColumns.indexOf(column)
    if (index > -1) {
      onShownColumnsChanged([...shownColumns.slice(0, index), ...shownColumns.slice(index + 1)])
    } else {
      onShownColumnsChanged([...shownColumns, column])
    }
  }
  return <React.Fragment>
    <TooltipButton
      title="Change the shown columns."
      component={IconButton}
      onClick={(event) => setAnchorEl(anchorEl ? null : event.currentTarget)}
    >
      <EditColumnsIcon/>
    </TooltipButton>
    <Popover
      open={anchorEl !== null}
      anchorEl={anchorEl}
      onClose={() => setAnchorEl(null)}
      anchorOrigin={{
        vertical: 'bottom',
        horizontal: 'center'
      }}
      transformOrigin={{
        vertical: 'top',
        horizontal: 'center'
      }}
    >
      <List>
        {columns.map(column => <ListItem
          key={column.key} role={undefined} dense button onClick={() => handleToggle(column)}
        >
          <Checkbox
            checked={shownColumns.includes(column)}
            tabIndex={-1}
            disableRipple
          />
          <ListItemText primary={column.label || column.key} />
        </ListItem>)}
      </List>
    </Popover>
  </React.Fragment>
})
DatatableColumnSelector.propTypes = {
  children: PropTypes.oneOfType([
    PropTypes.arrayOf(PropTypes.node),
    PropTypes.node
  ])
}

/** A table with optional header, selectable rows, actions on table, selection, or rows,
 * foldable row details, sortable coluns, editable columns, and pagination.
 * Table is based on array data with row objects and matching speficiation of columns.
 */
export const Datatable = React.memo(function Datatable(props) {
  const {children, ...contextProps} = props
  const {data, columns} = contextProps

  const [shownColumns, setShownColumns] = useState(
    props.shownColumns ? columns.filter(column => props.shownColumns.includes(column.key)) : columns
  )
  const withSelectionFeature = !!contextProps.selected
  const shownColumnsObjects = useMemo(() => (
    columns.filter(column => shownColumns.map(shownColumn => shownColumn.key).includes(column.key))
  ), [columns, shownColumns])

  const context = {
    withSelectionFeature: withSelectionFeature,
    pagination: {
      page: 1,
      page_size: data.length,
      total: data.length
    },
    ...contextProps,
    shownColumns: shownColumnsObjects,
    onShownColumnsChanged: setShownColumns
  }

  const staticContext = useMemo(() => ({
    withSelectionFeature: withSelectionFeature,
    shownColumns: shownColumnsObjects,
    onSelectedChanged: props.onSelectedChanged
  }), [
    shownColumnsObjects,
    withSelectionFeature,
    props.onSelectedChanged])

  return <StaticDatatableContext.Provider value={staticContext}>
    <DatatableContext.Provider value={context}>
      <Box display="flex" flexDirection="column" height="100%">
        {children || <DatatableTable/>}
      </Box>
    </DatatableContext.Provider>
  </StaticDatatableContext.Provider>
})
const paginationBaseProps = {
  page_size: PropTypes.number,
  total: PropTypes.number,
  order: PropTypes.oneOf(['asc', 'desc']),
  order_by: PropTypes.string
}
Datatable.propTypes = {
  /** Specification for all possible column. */
  columns: PropTypes.arrayOf(PropTypes.shape({
    /** Unique key for this column. Should be the row object key for this columns value.
     * Also used as order_by value. */
    key: PropTypes.string.isRequired,
    /** Optional human readible label. Default is key. */
    label: PropTypes.string,
    /** Optional human readible description. */
    description: PropTypes.string,
    /** If this columns should be sortable. */
    sortable: PropTypes.bool,
    /** The alignment of header and values. Default is right. */
    align: PropTypes.oneOf(['right', 'left', 'center']),
    /** Optional value render function. Default will render value taken from row object by
     * columns key. */
    render: PropTypes.func
  })),
  /** Optional array of column keys. Only those columns will be visible. Default is to
   * show all columns */
  shownColumns: PropTypes.arrayOf(PropTypes.string),
  sortingColumns: PropTypes.arrayOf(PropTypes.string),
  /** Optional table data as array of objects. Default is empty table. */
  data: PropTypes.arrayOf(PropTypes.object),
  /** Optional pagination object (e.g. from NOMAD API). Used to display current pagination
   * and ordering information. Either based on page (if using DatatablePagePagination) or
   * page_after_value (if using DatatableScrollPagination). */
  pagination: PropTypes.oneOfType([
    PropTypes.shape({
      ...paginationBaseProps,
      next_page_after_value: PropTypes.number,
      page_after_value: PropTypes.number
    }),
    PropTypes.shape({
      ...paginationBaseProps,
      page: PropTypes.number
    })
  ]),
  /** Optional callback to on pagination changes (page, page size, order, ...). Function
   * that takes new pagination object as parameter. */
  onPaginationChanged: PropTypes.func,
  /** Optional value for selected rows to show. It is either the string "all" or
   * an array of selected row objects. If same object, row is shown as
   * selected. If no value is given, the table will not show any selection boxes. */
  selected: PropTypes.oneOfType([
    PropTypes.oneOf(['all']),
    PropTypes.arrayOf(PropTypes.object)
  ]),
  /** Optional callback for selection changes. Takes either "all" or new array of
   * selected row objects as parameter. */
  onSelectedChanged: PropTypes.func,
  /** Children, e.g. DatatableToolbar and DatatableTable */
  children: PropTypes.oneOfType([
    PropTypes.arrayOf(PropTypes.node),
    PropTypes.node
  ])
}
