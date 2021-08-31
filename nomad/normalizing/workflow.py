#
# Copyright The NOMAD Authors.
#
# This file is part of NOMAD. See https://nomad-lab.eu for further info.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

import numpy as np

from nomad.normalizing.normalizer import Normalizer
from nomad.datamodel import EntryArchive
from nomad.datamodel.metainfo.workflow import (
    Workflow, SinglePoint, GeometryOptimization, MolecularDynamics, Phonon, Elastic,
    Thermodynamics)


def resolve_difference(values):
    delta_values = None

    values = [v for v in values if v is not None]
    for n in range(-1, -len(values), -1):
        delta_values = abs(values[n] - values[n - 1])
        if delta_values != 0.0:
            break

    return delta_values


class TaskNormalizer(Normalizer):
    def __init__(self, entry_archive, workflow_index):
        super().__init__(entry_archive)
        workflow_index = workflow_index if len(entry_archive.workflow) < workflow_index else -1
        self.workflow = entry_archive.workflow[workflow_index]
        run = self.workflow.run_ref
        self.run = run[-1].value if run else entry_archive.run[-1]


class SinglePointNormalizer(TaskNormalizer):
    def normalize(self):
        super().normalize()
        self.section = self.workflow.single_point
        if not self.section:
            self.section = self.workflow.m_create(SinglePoint)

        if not self.section.method:
            try:
                method = self.run.method[-1]
                self.section.method = method.electronic.method
            except Exception:
                pass

        scc = self.run.calculation
        if not scc:
            return

        if not self.section.n_scf_steps:
            self.section.n_scf_steps = len(scc[-1].scf_iteration)

        energies = [scf.energy.total.value for scf in scc[-1].scf_iteration if scf.energy is not None and scf.energy.total is not None]
        delta_energy = resolve_difference(energies)
        if not self.section.final_scf_energy_difference and delta_energy is not None:
            self.section.final_scf_energy_difference = delta_energy

        if not self.section.is_converged and delta_energy is not None:
            try:
                threshold = self.run.method[-1].scf.threshold_energy_change
                self.section.is_converged = bool(delta_energy <= threshold)
            except Exception:
                pass

        if not self.section.with_density_of_states:
            self.section.with_density_of_states = len(scc[-1].dos_electronic) > 0

        if not self.section.with_bandstructure:
            self.section.with_bandstructure = len(scc[-1].band_structure_electronic) > 0

        if not self.section.with_eigenvalues:
            self.section.with_eigenvalues = len(scc[-1].eigenvalues) > 0

        if not self.section.with_volumetric_data:
            self.section.with_volumetric_data = (
                len(scc[-1].potential) > 0 or len(scc[-1].density_charge) > 0)

        if not self.section.with_excited_states:
            self.section.with_excited_states = len(scc[-1].excited_states) > 0


class GeometryOptimizationNormalizer(TaskNormalizer):

    def _get_geometry_optimization_type(self):
        sec_system = self.run.system
        if not sec_system:
            return

        def compare_cell(cell1, cell2):
            if (cell1 == cell2).all():
                return None
            else:
                cell1_normed = cell1 / np.linalg.norm(cell1)
                cell2_normed = cell2 / np.linalg.norm(cell2)
                if (cell1_normed == cell2_normed).all():
                    return 'cell_volume'
                else:
                    return 'cell_shape'

        if len(sec_system) < 2:
            return 'static'

        else:
            if sec_system[0].atoms is None or sec_system[-1].atoms is None:
                return 'static'

            cell_init = sec_system[0].atoms.lattice_vectors
            cell_final = sec_system[-1].atoms.lattice_vectors
            if cell_init is None or cell_final is None:
                return 'static'

            cell_relaxation = compare_cell(cell_init.magnitude, cell_final.magnitude)

            if cell_relaxation is not None:
                return cell_relaxation

            atom_pos_init = sec_system[0].atoms.positions
            atom_pos_final = sec_system[-1].atoms.positions

            if atom_pos_init is None or atom_pos_final is None:
                return 'static'

            if (atom_pos_init.magnitude == atom_pos_final.magnitude).all():
                return 'static'

            return 'ionic'

    def normalize(self):
        super().normalize()
        self.section = self.workflow.geometry_optimization
        if self.section is None:
            self.section = self.workflow.m_create(GeometryOptimization)

        if not self.section.type:
            try:
                geometry_optimization_type = self._get_geometry_optimization_type()
                self.section.type = geometry_optimization_type
            except Exception:
                pass

        if not self.section.optimization_steps:
            scc = self.run.calculation
            self.section.optimization_steps = len(scc)

        if not self.section.final_energy_difference:
            energies = []
            for scc in self.run.calculation:
                if scc.energy is not None and scc.energy.total is not None:
                    energies.append(scc.energy.total.value)

            delta_energy = resolve_difference(energies)
            if delta_energy is not None:
                self.section.final_energy_difference = delta_energy

        if not self.section.final_force_maximum:
            scc = self.run.calculation
            if len(scc) > 0:
                if scc[-1].forces is not None and scc[-1].forces.total is not None:
                    forces = scc[-1].forces.total.value
                    if forces is not None:
                        max_force = np.max(np.linalg.norm(forces.magnitude, axis=1))
                        self.section.final_force_maximum = max_force * forces.units

        # Store the energies as an explicit list. If a step within the
        # trajectory does not contain an energy the rest of the energies in the
        # trajectory are not included.
        trajectory = self.workflow.calculations_ref
        if trajectory:
            n_steps = len(trajectory)
            energies = []
            invalid = False
            for step in range(n_steps):
                try:
                    energy = trajectory[step].energy.total.value
                except (IndexError, AttributeError):
                    invalid = True
                    break
                energies.append(energy.magnitude)
            if invalid:
                self.logger.warning("energy not reported for an scc that is part of a geometry optimization")
            if energies:
                self.section.energies = energies

        if not self.section.final_displacement_maximum:
            try:
                system = self.run.system
                displacements = [np.max(np.abs(
                    system[n].atoms.positions - system[n - 1].atoms.positions)) for n in range(1, len(system))]
                self.section.final_displacement_maximum = resolve_difference(displacements)
            except Exception:
                pass

        if not self.section.is_converged_geometry:
            # we can have several criteria for convergence: energy, force, displacement
            criteria = []
            try:
                criteria.append(self.section.final_energy_difference <= self.section.convergence_tolerance_energy_difference)
            except Exception:
                pass

            try:
                criteria.append(self.section.final_force_maximum <= self.section.convergence_tolerance_force_maximum)
            except Exception:
                pass

            try:
                criteria.append(self.section.final_displacement_maximum <= self.section.convergence_tolerance_displacement_maximum)
            except Exception:
                pass

            # converged when either criterion is met
            if criteria:
                self.section.is_converged_geometry = True in criteria


class PhononNormalizer(TaskNormalizer):
    def _get_n_imaginary_frequencies(self):
        scc = self.run.calculation
        if not scc:
            return
        sec_band = scc[0].band_structure_phonon
        if not sec_band:
            return
        result = 0
        for band_segment in sec_band[0].segment:
            freq = band_segment.value
            result += np.count_nonzero(np.array(freq) < 0)
        return result

    def normalize(self):
        super().normalize()
        self.section = self.workflow.phonon

        if not self.section:
            self.section = self.workflow.m_create(Phonon)

        if not self.section.n_imaginary_frequencies:
            # get number from bands (not complete as this is not the whole mesh)
            self.section.n_imaginary_frequencies = self._get_n_imaginary_frequencies()


class ElasticNormalizer(TaskNormalizer):
    def _resolve_mechanical_stability(self):
        spacegroup, c = None, None
        try:
            spacegroup = self.run.system[-1].symmetry[-1].space_group_number
            c = self.section.elastic_constants_matrix_second_order
        except Exception:
            return False

        if c is None or spacegroup is None:
            return False

        # see Phys. Rev B 90, 224104 (2014)
        res = False
        if spacegroup <= 2:  # Triclinic
            res = np.count_nonzero(c < 0)
        elif spacegroup <= 15:  # Monoclinic
            res = np.count_nonzero(c < 0)
        elif spacegroup <= 74:  # Orthorhombic
            res =\
                c[0][0] > 0 and c[0][0] * c[1][1] > c[0][1] ** 2 and\
                c[0][0] * c[1][1] * c[2][2] + 2 * c[0][1] * c[0][2] * c[1][2] -\
                c[0][0] * c[1][2] ** 2 - c[1][1] * c[0][2] ** 2 - c[2][2] * c[0][1] ** 2 > 0 and\
                c[3][3] > 0 and c[4][4] > 0 and c[5][5] > 0
        elif spacegroup <= 88:  # Tetragonal II
            res =\
                c[0][0] > abs(c[0][1]) and\
                2 * c[0][2] ** 2 < c[2][2] * (c[0][0] + c[0][1])
        elif spacegroup <= 142:  # Tetragonal I
            res =\
                c[0][0] > abs(c[0][1]) and\
                2 * c[0][2] ** 2 < c[2][2] * (c[0][0] + c[0][1]) and\
                c[3][3] > 0 and c[5][5] > 0
        elif spacegroup <= 148:  # rhombohedral II
            res =\
                c[0][0] > abs(c[0][1]) and c[3][3] > 0 and\
                c[0][2] ** 2 < (0.5 * c[2][2] * (c[0][0] + c[0][1])) and\
                c[0][3] ** 2 + c[0][4] ** 2 < 0.5 * c[3][3] * (c[0][0] - c[0][1])
        elif spacegroup <= 167:  # rhombohedral I
            res =\
                c[0][0] > abs(c[0][1]) and c[3][3] > 0 and\
                c[0][2] ** 2 < 0.5 * c[2][2] * (c[0][0] + c[0][1]) and\
                c[0][3] ** 2 < 0.5 * c[3][3] * (c[0][0] - c[0][1])
        elif spacegroup <= 194:  # hexagonal I
            res =\
                c[0][0] > abs(c[0][1]) and\
                2 * c[0][2] ** 2 < c[2][2] * (c[0][0] + c[0][1]) and\
                c[3][3] > 0 and c[5][5] > 0
        else:  # cubic
            res = c[0][0] - c[0][1] > 0 and c[0][0] + 2 * c[0][1] > 0 and c[3][3] > 0

        return res

    def _get_maximum_fit_error(self):
        max_error = 0.0
        if len(self.run.calculation) == 0:
            return max_error

        for diagram in self.section.strain_diagrams:
            if diagram.type == 'cross-validation':
                error = np.amax(diagram.value)
                max_error = error if error > max_error else max_error

        return max_error

    def normalize(self):
        super().normalize()
        self.section = self.workflow.elastic

        if not self.section:
            self.section = self.workflow.m_create(Elastic)

        if self.section.is_mechanically_stable is None:
            self.section.is_mechanically_stable = bool(self._resolve_mechanical_stability())

        if self.section.fitting_error_maximum is None:
            self.section.fitting_error_maximum = self._get_maximum_fit_error()


class MolecularDynamicsNormalizer(TaskNormalizer):
    def _is_with_thermodynamics(self):
        try:
            return len(self.run.calculation[-1].thermodynamics[-1].values()) > 0
        except Exception:
            return False

    def _is_with_trajectory(self):
        try:
            return self.run.system[-1].atoms.positions is not None
        except Exception:
            return False

    def normalize(self):
        super().normalize()
        self.section = self.workflow.molecular_dynamics

        if not self.section:
            self.section = self.workflow.m_create(MolecularDynamics)

        if self.section.with_thermodynamics is None:
            self.section.with_thermodynamics = self._is_with_thermodynamics()

        if self.section.with_trajectory is None:
            self.section.with_trajectory = self._is_with_trajectory()


class ThermodynamicsNormalizer(TaskNormalizer):
    def normalize(self):
        super().normalize()
        self.section = self.workflow.thermodynamics

        if not self.run.calculation or not self.run.calculation[0].thermodynamics:
            return

        if not self.section:
            self.section = self.workflow.m_create(Thermodynamics)

        def set_thermo_property(name):
            values = []
            quantity = None
            for scc in self.run.calculation:
                try:
                    for thermo in scc.thermodynamics:
                        quantity = thermo[name]
                        values.append(quantity.magnitude if hasattr(quantity, 'magnitude') else quantity)
                except Exception:
                    pass
            unit = quantity.magnitude if hasattr(quantity, 'magnitude') else 1.0
            setattr(self.section, name, np.array(values) * unit)

        if not self.section.temperature:
            set_thermo_property('temperature')

        if not self.section.pressure:
            set_thermo_property('pressure')

        if not self.section.helmholz_free_energy:
            set_thermo_property('helmholz_free_energy')

        if not self.section.vibrational_free_energy_at_constant_volume:
            set_thermo_property('vibrational_free_energy_at_constant_volume')

        if not self.section.heat_capacity_c_v:
            set_thermo_property('heat_capacity_c_v')

        # TODO add values for specific energy


class WorkflowNormalizer(Normalizer):
    '''
    This normalizer produces information specific to a workflow.
    '''
    def __init__(self, entry_archive):
        super().__init__(entry_archive)
        self._elastic_programs = ['elastic']
        self._phonon_programs = ['phonopy']
        self._molecular_dynamics_programs = ['lammps']

    def _resolve_workflow_type(self, run):
        # resolve it from parser
        workflow_type = None
        program_name = run.program.name
        if program_name:
            program_name = program_name.lower()

        if program_name in self._elastic_programs:
            workflow_type = 'elastic'

        elif program_name in self._molecular_dynamics_programs:
            workflow_type = 'molecular_dynamics'

        elif program_name in self._phonon_programs:
            workflow_type = 'phonon'

        # resolve if from scc
        if workflow_type is None:
            if len(run.calculation) == 1:
                workflow_type = 'single_point'
            else:
                workflow_type = 'geometry_optimization'

        return workflow_type

    def normalize(self, logger=None) -> None:
        super().normalize()

        # Do nothing if section_run is not present
        if not self.entry_archive.run:
            return

        if not self.entry_archive.workflow:
            self.entry_archive.m_create(Workflow)

        for n, sec_workflow in enumerate(self.entry_archive.workflow):
            # we get reference the referenced run from which information can be extracted
            sec_run = sec_workflow.run_ref
            sec_run = sec_run[-1].value if sec_run else self.entry_archive.run[-1]

            scc = sec_run.calculation
            if not sec_workflow.calculation_result_ref:
                if scc:
                    sec_workflow.calculation_result_ref = scc[-1]

            if not sec_workflow.calculations_ref:
                if scc:
                    sec_workflow.calculations_ref = scc

            if sec_workflow.type is None:
                workflow_type = self._resolve_workflow_type(sec_run)
                sec_workflow.type = workflow_type

            if sec_workflow.type == 'geometry_optimization':
                GeometryOptimizationNormalizer(self.entry_archive, n).normalize()

            elif sec_workflow.type == 'phonon':
                PhononNormalizer(self.entry_archive, n).normalize()

            elif sec_workflow.type == 'elastic':
                ElasticNormalizer(self.entry_archive, n).normalize()

            elif sec_workflow.type == 'molecular_dynamics':
                MolecularDynamicsNormalizer(self.entry_archive, n).normalize()

            elif sec_workflow.type == 'single_point':
                SinglePointNormalizer(self.entry_archive, n).normalize()

            # add thermodynamics data
            ThermodynamicsNormalizer(self.entry_archive, n).normalize()

            # remove the section workflow again, if the parser/normalizer could not produce a result
            if sec_workflow.calculation_result_ref is None:
                self.entry_archive.m_remove_sub_section(EntryArchive.workflow, n)
