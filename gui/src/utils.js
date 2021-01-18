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
import { parse } from 'mathjs'
import { conversionMap, unitMap, unitSystems } from './units'
import { cloneDeep, merge } from 'lodash'

export const isEquivalent = (a, b) => {
  // Create arrays of property names
  var aProps = Object.getOwnPropertyNames(a)
  var bProps = Object.getOwnPropertyNames(b)

  // If number of properties is different,
  // objects are not equivalent
  if (aProps.length !== bProps.length) {
    return false
  }

  for (var i = 0; i < aProps.length; i++) {
    var propName = aProps[i]

    // If values of same property are not equal,
    // objects are not equivalent
    if (a[propName] !== b[propName]) {
      return false
    }
  }

  // If we made it this far, objects
  // are considered equivalent
  return true
}

export const capitalize = (s) => {
  if (typeof s !== 'string') {
    return ''
  }
  return s.charAt(0).toUpperCase() + s.slice(1)
}

/**
 * Used to scale numeric values. Works on n-dimensional arrays and implemented
 * as a relatively simple for loop for performance. If conversion times become
 * an issue, it might be worthwhile to look at vectorization with WebAssembly.
 *
 * @param {*} value The values to convert
 * @param {number} factor Scaling factor to apply.
 *
 * @return {*} A copy of the original data with numbers scaled.
 */
export function scale(value, factor) {
  // Convert arrays
  function scaleRecursive(list, newList) {
    let isScalarArray = !Array.isArray(list[0])
    if (isScalarArray) {
      for (let i = 0, size = list.length; i < size; ++i) {
        newList.push(list[i] * factor)
      }
    } else {
      for (let i = 0, size = list.length; i < size; ++i) {
        let iList = []
        newList.push(iList)
        scaleRecursive(list[i], iList)
      }
    }
  }
  let isArray = Array.isArray(value)
  let newValue
  if (!isArray) {
    newValue = value * factor
  } else {
    newValue = []
    scaleRecursive(value, newValue)
  }
  return newValue
}

/**
 * Used to add a single scalar value to an n-dimensional array.
 *
 * @param {*} value The values to convert
 * @param {number} addition Value to add.
 *
 * @return {*} A copy of the original data with numbers scaled.
 */
export function add(value, addition) {
  // Convert arrays
  function scaleRecursive(list, newList) {
    let isScalarArray = !Array.isArray(list[0])
    if (isScalarArray) {
      for (let i = 0, size = list.length; i < size; ++i) {
        newList.push(list[i] + addition)
      }
    } else {
      for (let i = 0, size = list.length; i < size; ++i) {
        let iList = []
        newList.push(iList)
        scaleRecursive(list[i], iList)
      }
    }
  }
  let isArray = Array.isArray(value)
  let newValue
  if (!isArray) {
    newValue = value + addition
  } else {
    newValue = []
    scaleRecursive(value, newValue)
  }
  return newValue
}

/**
 * Used to convert numeric values from SI units to the given unit system. Works
 * on n-dimensional arrays.
 *
 * @param {*} value The values to convert. Can be a scalar or an n-dimensional
 * array.
 * @param {string} from Original SI unit definition. Can be any algebraic
 * combination of SI units, e.g. "1 / meter^2". The unit names should follow
 * the definitions provided in the file units.js that is generated by the NOMAD
 * CLI.
 * @param {*} system Target unit system. A Javascript object where each
 * physical quantity (e.g. "length") acts as a key that corresponds to a target
 * unit (e.g. "angstrom"). The unit names should follow the definitions
 * provided in the file units.js that is generated by the NOMAD CLI.
 *
 * @return {*} A copy of the original data with units converted.
 */
export function convertSI(value, unit, system, units = true) {
  // Modify syntax to comply with math.js evaluation
  const from = unit.replace('**', '^')

  // Temperatures require special handling due to the fact that Celsius and
  // Fahrenheit are not absolute units and are non-multiplicative. Two kinds of
  // temperature conversions are supported: ones with a single temperature unit
  // and ones where temperature is used as a part of an expression. If a single
  // temperature unit is specified, they are converted normally taking the
  // offset into account. If they are used as a part of an expression, they are
  // interpreted as ranges and the offset is ignored.
  if (from === 'kelvin') {
    const unitTo = system['temperature']
    const multiplier = conversionMap['temperature'].multipliers['kelvin'][unitTo]
    const constant = conversionMap['temperature'].constants['kelvin'][unitTo]
    const label = unitMap['kelvin'].label
    let newValues = value
    if (multiplier !== 1) {
      newValues = scale(newValues, multiplier)
    }
    if (constant !== undefined) {
      newValues = add(newValues, constant)
    }
    return [newValues, label]
  }

  // Gather all units present
  const variables = new Set()
  const rootNode = parse(from)
  rootNode.traverse((node, path, parent) => {
    if (node.isSymbolNode) {
      variables.add(node.name)
    }
  })

  // Check if conversion is required. The unit definition string is standardized
  // even if no conversion took place.
  let isSI = true
  for (const unit of variables) {
    const dimension = unitMap[unit].dimension
    const unitSI = unitSystems['SI'][dimension]
    isSI = unit === unitSI
    if (isSI) {
      break
    }
  }
  if (isSI) {
    if (units) {
      const newUnit = convertSILabel(from, system)
      return [value, newUnit]
    }
    return value
  }

  // Gather conversion values for each present SI unit
  const scope = {}
  for (const unitFrom of variables) {
    const dimension = unitMap[unitFrom].dimension
    const unitTo = system[dimension]
    scope[unitFrom] = conversionMap[dimension].multipliers[unitFrom][unitTo]
  }

  // Compute the scaling factor by evaluating the unit definition with the
  // SI units converted to target system
  const code = rootNode.compile()
  const factor = code.evaluate(scope)

  // Scale values to new units
  let newValues = scale(value, factor)

  // Form new unit definition string by replacing the SI units with the target
  if (units) {
    const newUnit = convertSILabel(from, system)
    return [newValues, newUnit]
  }
  return newValues
}

export function convertSILabel(label, system) {
  // Form new unit definition string by replacing the SI units with the target
  const rootNode = parse(label)
  const newRoot = rootNode.transform((node, path, parent) => {
    if (node.isSymbolNode) {
      const unitFromInfo = unitMap[node.name]
      if (unitFromInfo !== undefined) {
        const dimension = unitFromInfo.dimension
        const unitTo = system[dimension]
        const unitToInfo = unitMap[unitTo]
        const label = unitToInfo.abbreviation
        node.name = label
      }
    }
    return node
  })
  return newRoot.toString()
}

/**
 * Used to calculate the distance between two n-dimensional points,
 *
 * @param {*} a First point
 * @param {*} b Second point
 *
 * @return {*} Euclidean distance between the given two points.
 */
export function distance(a, b) {
  return a
    .map((x, i) => Math.abs(x - b[i]) ** 2) // square the difference
    .reduce((sum, now) => sum + now) ** // sum
    (1 / 2)
}

/**
 * Used to merge two Javascript objects into a new third object by recursively
 * overwriting and extending the target object with properties from the source
 * object.
 *
 * @param {*} target The values to convert
 * @param {*} source Original unit.
 *
 * @return {*} A copy of the original data with units converted.
 */
export function mergeObjects(source, target, copy = false) {
  // First create a deep clone that will be used as the returned object
  let cloned = cloneDeep(target)
  let val = merge(cloned, source)
  return val
}

export function arraysEqual(_arr1, _arr2) {
  if (!Array.isArray(_arr1) || !Array.isArray(_arr2) || _arr1.length !== _arr2.length) {
    return false
  }

  var arr1 = _arr1.concat().sort()
  var arr2 = _arr2.concat().sort()

  for (var i = 0; i < arr1.length; i++) {
    if (arr1[i] !== arr2[i]) { return false }
  }

  return true
}

export function onlyUnique(value, index, self) {
  return self.indexOf(value) === index
}

export function objectFilter(obj, predicate) {
  return Object.keys(obj)
    .filter(key => predicate(key))
    .reduce((res, key) => {
      res[key] = obj[key]
      return res
    }, {})
}

export function titleCase(str) {
  var splitStr = str.toLowerCase().split(' ')
  for (var i = 0; i < splitStr.length; i++) {
    // You do not need to check if i is larger than splitStr length, as your for does that for you
    // Assign it back to the array
    splitStr[i] = splitStr[i].charAt(0).toUpperCase() + splitStr[i].substring(1)
  }
  // Directly return the joined string
  return splitStr.join(' ')
}

export function nameList(users, expanded) {
  const names = users.map(user => titleCase(user.name)).filter(name => name !== '')
  if (names.length > 3 && !expanded) {
    return [names[0], names[names.length - 1]].join(', ') + ' et al'
  } else {
    return names.join(', ')
  }
}

export function authorList(entry, expanded) {
  if (!entry) {
    return ''
  }

  if (entry.external_db) {
    if (entry.authors?.length > 1 && expanded) {
      return `${entry.external_db} (${nameList(entry.authors)})`
    }
    return entry.external_db
  } else {
    return nameList(entry.authors || [], expanded)
  }
}
