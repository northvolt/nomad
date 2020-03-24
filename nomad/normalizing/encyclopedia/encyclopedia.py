# Copyright 2018 Markus Scheidgen
#
# Licensed under the Apache License, Version 2.0 (the 'License');
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an'AS IS' BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from typing import Any

from nomad.normalizing.normalizer import (
    Normalizer,
    s_run,
    s_scc,
    s_system,
    s_method,
    s_frame_sequence,
    r_frame_sequence_to_sampling,
    s_sampling_method,
    r_frame_sequence_local_frames,
)
from nomad.metainfo.encyclopedia import (
    Encyclopedia,
    Material,
    Method,
    Properties,
    Calculation,
)
from nomad.parsing.backend import LocalBackend
from nomad.normalizing.encyclopedia.context import Context
from nomad.normalizing.encyclopedia.material import MaterialBulkNormalizer, Material2DNormalizer, Material1DNormalizer
from nomad.normalizing.encyclopedia.method import MethodDFTNormalizer, MethodGWNormalizer
from nomad.normalizing.encyclopedia.properties import PropertiesNormalizer
from nomad import config

J_to_Ry = 4.587425e+17


class EncyclopediaNormalizer(Normalizer):
    """
    This normalizer emulates the functionality of the old Encyclopedia backend.
    The data used by the encyclopedia have been assigned under new metainfo
    within a new section called "Encyclopedia". In the future these separate
    metainfos could be absorbed into the existing metainfo hiearchy.
    """
    def __init__(self, backend: LocalBackend):
        super().__init__(backend)
        self.backend: LocalBackend = backend

    def calc_type(self, calc: Calculation) -> str:
        """Decides what type of calculation this is: single_point, md,
        geometry_optimization, etc.
        """
        calc_enums = Calculation.calculation_type.type
        calc_type = calc_enums.unavailable

        try:
            sccs = self._backend[s_scc]
        except Exception:
            sccs = []
        try:
            frame_sequences = self._backend[s_frame_sequence]
        except Exception:
            frame_sequences = []

        n_scc = len(sccs)
        n_frame_seq = len(frame_sequences)

        # No sequences, only a few calculations
        if n_scc <= 3 and n_frame_seq == 0:
            program_name = self._backend["program_name"]
            if program_name == "elastic":
                # TODO move to taylor expansion as soon as data is correct in archive
                calc_type = calc_enums.elastic_constants
            else:
                calc_type = calc_enums.single_point

        # One sequence. Currently calculations with multiple sequences are
        # unsupported.
        elif n_frame_seq == 1:
            frame_seq = frame_sequences[0]

            # See if sampling_method is present
            try:
                i_sampling_method = frame_seq[r_frame_sequence_to_sampling]
            except KeyError:
                self.logger.info(
                    "Cannot determine encyclopedia run type because missing "
                    "value for frame_sequence_to_sampling_ref."
                )
                return calc_type

            # See if local frames are present
            try:
                frames = frame_seq[r_frame_sequence_local_frames]
            except KeyError:
                self.logger.info(
                    "section_frame_sequence_local_frames not found although a "
                    "frame_sequence exists."
                )
                return calc_type
            if len(frames) == 0:
                self.logger.info("No frames referenced in section_frame_sequence_local_frames.")
                return calc_type

            section_sampling_method = self._backend[s_sampling_method][i_sampling_method]
            sampling_method = section_sampling_method["sampling_method"]

            if sampling_method == "molecular_dynamics":
                calc_type = calc_enums.molecular_dynamics
            if sampling_method == "geometry_optimization":
                calc_type = calc_enums.geometry_optimization
            if sampling_method == "taylor_expansion":
                calc_type = calc_enums.phonon_calculation

        calc.calculation_type = calc_type
        return calc_type

    def material_type(self, material: Material) -> tuple:
        # Try to fetch representative system
        system = None
        material_type = config.services.unavailable_value
        material_enums = Material.material_type.type
        system_idx = self._backend["section_run"][0].tmp["representative_system_idx"]
        if system_idx is not None:
            # Try to find system type information from backend for the selected system.
            try:
                system = self._backend[s_system][system_idx]
                stype = system["system_type"]
            except KeyError:
                pass
            else:
                if stype == material_enums.one_d or stype == material_enums.two_d:
                    material_type = stype
                # For bulk systems we also ensure that the symmetry information is available
                if stype == material_enums.bulk:
                    try:
                        system["section_symmetry"][0]
                    except (KeyError, IndexError):
                        self.logger.info("Symmetry information is not available for a bulk system. No Encylopedia entry created.")
                    else:
                        material_type = stype

        material.material_type = material_type
        return system, material_type

    def method_type(self, method: Method) -> tuple:
        repr_method = None
        method_id = config.services.unavailable_value
        methods = self._backend[s_method]
        n_methods = len(methods)

        if n_methods == 1:
            repr_method = methods[0]
            method_id = repr_method.get("electronic_structure_method", config.services.unavailable_value)
        elif n_methods > 1:
            for sec_method in self._backend[s_method]:
                # GW
                electronic_structure_method = sec_method.get("electronic_structure_method", None)
                if electronic_structure_method in {"G0W0", "scGW"}:
                    repr_method = sec_method
                    method_id = "GW"
                    break

                # Methods linked to each other through references. Get all
                # linked methods, try to get electronic_structure_method from
                # each.
                try:
                    refs = sec_method["section_method_to_method_refs"]
                except KeyError:
                    pass
                else:
                    linked_methods = [sec_method]
                    for ref in refs:
                        method_to_method_kind = ref["method_to_method_kind"]
                        method_to_method_ref = ref["method_to_method_ref"]
                        if method_to_method_kind == "core_settings":
                            linked_methods.append(methods[method_to_method_ref])

                    for i_method in linked_methods:
                        try:
                            electronic_structure_method = i_method["electronic_structure_method"]
                        except KeyError:
                            pass
                        else:
                            repr_method = sec_method
                            method_id = electronic_structure_method

        method.method_type = method_id
        return repr_method, method_id

    def fill(self, context: Context):
        # Fill structure related metainfo
        struct: Any = None
        if context.material_type == Material.material_type.type.bulk:
            struct = MaterialBulkNormalizer(self.backend, self.logger)
        elif context.material_type == Material.material_type.type.two_d:
            struct = Material2DNormalizer(self.backend, self.logger)
        elif context.material_type == Material.material_type.type.one_d:
            struct = Material1DNormalizer(self.backend, self.logger)
        if struct is not None:
            struct.normalize(context)

        # Fill method related metainfo
        method = None
        if context.method_type == Method.method_type.type.DFT or context.method_type == Method.method_type.type.DFTU:
            method = MethodDFTNormalizer(self._backend, self.logger)
        elif context.method_type == Method.method_type.type.GW:
            method = MethodGWNormalizer(self._backend, self.logger)
        if method is not None:
            method.normalize(context)

        # Fill properties related metainfo
        properties = PropertiesNormalizer(self.backend, self.logger)
        properties.normalize(context)

    def normalize(self, logger=None) -> None:
        """The caller will automatically log if the normalizer succeeds or ends
        up with an exception.
        """
        try:
            super().normalize(logger)

            # Initialise metainfo structure
            sec_enc = Encyclopedia()
            material = sec_enc.m_create(Material)
            method = sec_enc.m_create(Method)
            sec_enc.m_create(Properties)
            calc = sec_enc.m_create(Calculation)

            # Determine run type, stop if unknown
            calc_type = self.calc_type(calc)
            if calc_type == config.services.unavailable_value:
                self.logger.info(
                    "Unsupported run type for encyclopedia, encyclopedia metainfo not created.",
                    enc_status="unsupported_calc_type",
                )
                return

            # Get the system type, stop if unknown
            material_enums = Material.material_type.type
            representative_system, material_type = self.material_type(material)
            if material_type != material_enums.bulk and material_type != material_enums.two_d and material_type != material_enums.one_d:
                self.logger.info(
                    "Unsupported system type for encyclopedia, encyclopedia metainfo not created.",
                    enc_status="unsupported_material_type",
                )
                return

            # Get the method type, stop if unknown
            representative_method, method_type = self.method_type(method)
            if method_type == config.services.unavailable_value:
                self.logger.info(
                    "Unsupported method type for encyclopedia, encyclopedia metainfo not created.",
                    enc_status="unsupported_method_type",
                )
                return

            # Get representative scc
            try:
                representative_scc_idx = self._backend[s_run][0].tmp["representative_scc_idx"]
                representative_scc = self._backend[s_scc][representative_scc_idx]
            except (KeyError, IndexError):
                representative_scc = None
                representative_scc_idx = None

            # Create one context that holds all details
            context = Context(
                material_type=material_type,
                method_type=method_type,
                calc_type=calc_type,
                representative_system=representative_system,
                representative_method=representative_method,
                representative_scc=representative_scc,
                representative_scc_idx=representative_scc_idx,
            )

            # Put the encyclopedia section into backend
            self._backend.add_mi2_section(sec_enc)
            self.fill(context)
        except Exception:
            self.logger.error(
                "Failed to create an Encyclopedia entry due to an unhandlable exception.",
                enc_status="failure",
            )
            raise  # Reraise for the caller to log the exception as well
        else:
            self.logger.info(
                "Successfully created metainfo for Encyclopedia.",
                enc_status="success",
            )
