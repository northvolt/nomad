import repo from '@material-ui/core/colors/deepPurple'
import archive from '@material-ui/core/colors/teal'
import enc from '@material-ui/core/colors/amber'
import analytics from '@material-ui/core/colors/lightGreen'
import secondary from '@material-ui/core/colors/blueGrey'
import { createMuiTheme } from '@material-ui/core'

window.nomadEnv = window.nomadEnv || {}
export const apiBase = process.env.REACT_APP_API_BASE || window.nomadEnv.apiBase
export const appBase = process.env.REACT_APP_APP_BASE || window.nomadEnv.appBase
export const kibanaBase = process.env.REACT_KIBANA_BASE || window.nomadEnv.kibanaBase
export const appStaticBase = process.env.REACT_APP_APP_STATIC_BASE || window.nomadEnv.appStaticBase
export const debug = process.env.REACT_APP_DEBUG ? process.env.REACT_APP_DEBUG === 'true' : window.nomadEnv.debug

export const genTheme = createMuiTheme({
  palette: {
    primary: secondary,
    secondary: secondary
  }
})

export const repoTheme = createMuiTheme({
  palette: {
    primary: repo,
    secondary: repo
  }
})

export const archiveTheme = createMuiTheme({
  palette: {
    primary: archive,
    secondary: repo
  }
})

export const encTheme = createMuiTheme({
  palette: {
    primary: enc,
    secondary: repo
  }
})

export const analyticsTheme = createMuiTheme({
  palette: {
    primary: analytics,
    secondary: repo
  }
})
