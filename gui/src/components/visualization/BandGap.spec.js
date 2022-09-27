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
import { render } from '../conftest.spec'
import { expectVisualization, VisualizationState } from './conftest.spec'
import BandGap, { bandGapError } from './BandGap'

test.each([
  ['no data', VisualizationState.NoData, false],
  ['loading', VisualizationState.Loading, undefined],
  ['error: invalid data', VisualizationState.Error, "invalid data"]
])('band gap: %s', async (id, state, data) => {
  render(<BandGap data={data} />)
  await expectVisualization(state, 'band-gap-placeholder', bandGapError)
})
