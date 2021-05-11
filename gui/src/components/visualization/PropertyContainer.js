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
import React from 'react'
import { Typography } from '@material-ui/core'
import { makeStyles } from '@material-ui/core/styles'
import PropTypes from 'prop-types'
import clsx from 'clsx'

const useStyles = makeStyles(theme => ({
  root: {
  },
  title: {
    marginBottom: theme.spacing(0.5)
  }
}))
export default function PropertyContainer({title, className, classes, children}) {
  const styles = useStyles({classes: classes})
  return <div className={clsx(className, styles.root)}>
    <Typography variant="subtitle1" align='center' className={styles.title}>{title}</Typography>
    {children}
  </div>
}

PropertyContainer.propTypes = {
  title: PropTypes.string,
  className: PropTypes.string,
  classes: PropTypes.object,
  children: PropTypes.any
}
