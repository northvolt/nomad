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
import userEvent from '@testing-library/user-event'
import { waitFor } from '@testing-library/dom'
import { startAPI, closeAPI, screen } from '../../conftest.spec'
import { renderSearchEntry, expectInputPeriodicTable, expectInputPeriodicTableItems } from '../conftest.spec'
import InputPeriodicTable from './InputPeriodicTable'

const quantity = 'results.material.elements'
const stateName = 'tests.states.search.search'

describe('', () => {
  beforeEach(async () => {
    await startAPI(stateName, 'tests/data/search/inputperiodictable')
    renderSearchEntry(<InputPeriodicTable
      quantity={quantity}
      visible
    />
    )
  })
  afterEach(() => closeAPI())

  test('initial state is loaded correctly', async () => {
    await expectInputPeriodicTable(quantity, false, ['H', 'C', 'N', 'Ti', 'Zr', 'Nb', 'I', 'Hf', 'Ta', 'Pb'])
  })
})

describe('', () => {
  beforeEach(async () => {
    await startAPI(stateName, 'tests/data/search/inputperiodictable-edit')
    renderSearchEntry(<InputPeriodicTable
      quantity={quantity}
      visible
    />
    )
  })
  afterEach(() => closeAPI())

  test('selecting an element in both non-exclusive and exclusive mode correctly updates the table', async () => {
    // Wait for hydrogen to become selectable
    await waitFor(() => { expect(screen.getByText('H').closest('button')).not.toHaveAttribute('disabled') })

    // Test that after selecting C, only H and C are selectable.
    const cButton = screen.getByText('C')
    await userEvent.click(cButton)
    await expectInputPeriodicTableItems(['C', 'H', 'N', 'I', 'Pb'])

    // Test that after enabling exclusive search, only C is selectable
    const exclusiveCheckbox = screen.getByRole('checkbox')
    await userEvent.click(exclusiveCheckbox)
    await expectInputPeriodicTableItems(['C'])
  })
})
