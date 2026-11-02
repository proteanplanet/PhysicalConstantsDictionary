#!/usr/bin/env python3

"""
Unit and integration tests for tools/generate_source.py.

Run with:
    python3 -m unittest discover -s tools/tests -v
"""

import ast
import pathlib
import sys
import tempfile
import unittest

import yaml

this_dir = pathlib.Path(__file__).parent
tools_dir = this_dir.parent
sys.path.insert(0, str(tools_dir))

import generate_source as gs  # noqa: E402


###############################################################################
class TestParseFormula(unittest.TestCase):
###############################################################################

    def setUp(self):
        self.known = {'a', 'b', 'c'}

    def test_simple_arithmetic_is_accepted(self):
        for formula in ['a + b', 'a - b', 'a * b', 'a / b', '(a + b) / c',
                         '-a', '+a', '2 * a', 'a ** 2']:
            with self.subTest(formula=formula):
                tree, deps, _ = gs.parse_formula('result', formula, self.known)
                self.assertIsInstance(tree, ast.Expression)

    def test_dependencies_are_collected(self):
        _, deps, _ = gs.parse_formula('result', 'a * b + c', self.known)
        self.assertEqual(deps, {'a', 'b', 'c'})

    def test_pow_flag(self):
        _, _, has_pow = gs.parse_formula('result', 'a + b', self.known)
        self.assertFalse(has_pow)
        _, _, has_pow = gs.parse_formula('result', 'a ** b', self.known)
        self.assertTrue(has_pow)

    def test_unknown_name_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unknown constant 'z'"):
            gs.parse_formula('result', 'a + z', self.known)

    def test_self_reference_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'cannot reference itself'):
            gs.parse_formula('a', 'a + b', self.known)

    def test_syntax_error_is_rejected(self):
        with self.assertRaises(ValueError):
            gs.parse_formula('result', 'a +* b', self.known)

    def test_function_calls_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'unsupported syntax'):
            gs.parse_formula('result', "__import__('os').system('ls')", self.known)

    def test_boolean_and_comparison_ops_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'unsupported syntax'):
            gs.parse_formula('result', 'a and b', self.known)
        with self.assertRaisesRegex(ValueError, 'unsupported syntax'):
            gs.parse_formula('result', 'a < b', self.known)

    def test_string_literal_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'non-numeric literal'):
            gs.parse_formula('result', "'x' + a", self.known)


###############################################################################
class TestEmitExpr(unittest.TestCase):
###############################################################################

    def _emit(self, formula, lang, known=None):
        tree, _, _ = gs.parse_formula('result', formula, known or {'a', 'b'})
        return gs.emit_expr(tree, lang)

    def test_cxx_arithmetic(self):
        self.assertEqual(self._emit('a * b', 'cxx'), '(a * b)')
        self.assertEqual(self._emit('a + 2 * b', 'cxx'), '(a + (2.0 * b))')

    def test_f90_arithmetic_uses_dp_suffix(self):
        self.assertEqual(self._emit('a + 2 * b', 'f90'), '(a + (2.0_dp * b))')

    def test_pow_translation(self):
        self.assertEqual(self._emit('a ** b', 'cxx'), 'std::pow(a, b)')
        self.assertEqual(self._emit('a ** b', 'f90'), '(a**b)')

    def test_unary_minus(self):
        self.assertEqual(self._emit('-a', 'cxx'), '(-a)')


###############################################################################
class TestWrapF90Code(unittest.TestCase):
###############################################################################

    def test_short_code_is_not_wrapped(self):
        code = 'real(dp), parameter :: pi = 3.14_dp'
        self.assertEqual(gs.wrap_f90_code(code), ['    ' + code])

    def test_long_code_is_wrapped_under_free_form_limit(self):
        # A long formula, similar to the real dry_air_density_at_standard_
        # temperature_and_pressure / sea_ice_thermal_diffusivity_reference
        # entries that originally broke CI: their declaration alone (before
        # any trailing comment) exceeds Fortran's 132-column free-form
        # limit, and gfortran errors on that under -Wall -Werror (and
        # silently truncates -- corrupting the formula -- without it).
        code = ('real(dp), parameter :: dry_air_density_at_standard_temperature_and_pressure = '
                '(standard_atmosphere / (dry_air_specific_gas_constant_reference * '
                'pure_water_freezing_temperature_reference))')
        lines = gs.wrap_f90_code(code)
        self.assertGreater(len(lines), 1)
        for line in lines[:-1]:
            self.assertTrue(line.rstrip().endswith('&') is False)  # '&' added by the writer, not here
        for line in lines:
            self.assertLessEqual(len(line), gs._F90_MAX_LINE + 20)

    def test_wrapping_preserves_the_logical_statement(self):
        code = ('real(dp), parameter :: dry_air_density_at_standard_temperature_and_pressure = '
                '(standard_atmosphere / (dry_air_specific_gas_constant_reference * '
                'pure_water_freezing_temperature_reference))')
        lines = gs.wrap_f90_code(code)
        # Stripping the continuation marker/indentation and rejoining every
        # physical line must reconstruct the original logical statement,
        # i.e. the split never lands inside a token.
        rejoined = ' '.join(line.lstrip().lstrip('&') for line in lines)
        self.assertEqual(rejoined.split(), code.split())


###############################################################################
class TestTopologicalOrder(unittest.TestCase):
###############################################################################

    def test_preserves_order_when_no_reordering_needed(self):
        items = [{'name': 'a', 'deps': set()},
                 {'name': 'b', 'deps': {'a'}},
                 {'name': 'c', 'deps': {'a', 'b'}}]
        ordered = [it['name'] for it in gs.topological_order(items)]
        self.assertEqual(ordered, ['a', 'b', 'c'])

    def test_reorders_dependencies_before_dependents(self):
        # 'c' is listed FIRST even though it depends on 'a' and 'b', which
        # are listed after it -- this is exactly the situation the generator
        # must handle so that a derived constant's formula only ever
        # references already-declared constants.
        items = [{'name': 'c', 'deps': {'a', 'b'}},
                 {'name': 'a', 'deps': set()},
                 {'name': 'b', 'deps': set()}]
        ordered = [it['name'] for it in gs.topological_order(items)]
        self.assertLess(ordered.index('a'), ordered.index('c'))
        self.assertLess(ordered.index('b'), ordered.index('c'))

    def test_circular_dependency_is_detected(self):
        items = [{'name': 'a', 'deps': {'b'}},
                 {'name': 'b', 'deps': {'a'}}]
        with self.assertRaisesRegex(ValueError, 'Circular dependency'):
            gs.topological_order(items)


###############################################################################
class TestGenerateFileIntegration(unittest.TestCase):
###############################################################################
    """
    End-to-end tests that run against the real pcd.yaml.
    """

    def test_cxx_generation_orders_derived_constants_after_dependencies(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp) / 'pcd_const.h'
            gs.generate_file('cxx', None, str(out))
            text = out.read_text(encoding='utf-8')

        def line_index(name):
            marker = f'double {name} ='
            for i, line in enumerate(text.splitlines()):
                if marker in line:
                    return i
            self.fail(f"constant '{name}' not found in generated file")

        self.assertLess(line_index('avogadro_constant'), line_index('molar_gas_constant'))
        self.assertLess(line_index('boltzmann_constant'), line_index('molar_gas_constant'))
        self.assertIn('molar_gas_constant = (avogadro_constant * boltzmann_constant);', text)

    def test_f90_generation_orders_derived_constants_after_dependencies(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp) / 'pcd_const.F90'
            gs.generate_file('f90', None, str(out))
            text = out.read_text(encoding='utf-8')

        def line_index(name):
            marker = f':: {name} ='
            for i, line in enumerate(text.splitlines()):
                if marker in line:
                    return i
            self.fail(f"constant '{name}' not found in generated file")

        self.assertLess(line_index('avogadro_constant'), line_index('molar_gas_constant'))
        self.assertLess(line_index('boltzmann_constant'), line_index('molar_gas_constant'))

    def test_f90_output_stays_within_fortran_line_length_limit(self):
        # Regression test: a real derived-constant formula (e.g.
        # dry_air_density_at_standard_temperature_and_pressure) previously
        # produced a declaration longer than Fortran's 132-column free-form
        # line limit, which gfortran rejects under -Wall -Werror (see CI
        # failure on PR #21).
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp) / 'pcd_const.F90'
            gs.generate_file('f90', None, str(out))
            text = out.read_text(encoding='utf-8')

        for line in text.splitlines():
            self.assertLessEqual(len(line), 132,
                                 f"line exceeds Fortran's free-form limit: {line!r}")

    def test_groups_filter_rejects_missing_cross_group_dependency(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp) / 'pcd_const.h'
            with self.assertRaisesRegex(ValueError, "not included in the selected groups"):
                gs.generate_file('cxx', ['earth_atmosphere'], str(out))

    def test_derived_constants_have_no_reference_in_generated_comment(self):
        # A derived constant's value isn't taken from a single cited source,
        # so its generated comment should show units (if any) but never the
        # 'reference' field; 'reference' is still kept on the pcd.yaml entry
        # itself, just not echoed into the generated file.
        with tempfile.TemporaryDirectory() as tmp:
            cxx_out = pathlib.Path(tmp) / 'pcd_const.h'
            gs.generate_file('cxx', None, str(cxx_out))
            cxx_text = cxx_out.read_text(encoding='utf-8')

        for line in cxx_text.splitlines():
            if 'molar_gas_constant = (avogadro_constant' in line:
                self.assertIn('// [J mol-1 K-1]', line)
                self.assertNotIn('NIST_CODATA2022', line)
                break
        else:
            self.fail('molar_gas_constant declaration not found')

        # flattening has no units either, so it should have no trailing
        # comment at all.
        for line in cxx_text.splitlines():
            if 'double flattening = ' in line:
                self.assertNotIn('//', line)
                break
        else:
            self.fail('flattening declaration not found')

    def test_non_derived_constants_keep_their_reference_in_generated_comment(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp) / 'pcd_const.h'
            gs.generate_file('cxx', None, str(out))
            text = out.read_text(encoding='utf-8')

        for line in text.splitlines():
            if 'double avogadro_constant = ' in line:
                self.assertIn('NIST_CODATA2022', line)
                break
        else:
            self.fail('avogadro_constant declaration not found')

    def test_format_constant_reference_is_none_only_for_formula_entries(self):
        _, _, _, all_items, _ = gs.load_constants(None)
        for it in all_items:
            _, _, r = gs.format_constant('cxx', it)
            if it['tree'] is not None:
                self.assertIsNone(r, f"formula entry '{it['name']}' should have no reference")
            else:
                self.assertIsNotNone(r, f"value entry '{it['name']}' should keep its reference")

    def test_formula_entries_are_tagged_type_formula_in_yaml(self):
        _, _, _, all_items, _ = gs.load_constants(None)
        for it in all_items:
            if it['tree'] is not None:
                self.assertEqual(it['entry'].get('type'), 'formula',
                                 f"derived constant '{it['name']}' should have type: formula")

    def test_formula_entries_have_no_reference_field_in_yaml(self):
        # A derived constant's value isn't taken from a single cited
        # source, so its pcd.yaml entry shouldn't carry a 'reference'
        # field at all; a value entry still must.
        _, _, _, all_items, _ = gs.load_constants(None)
        for it in all_items:
            if it['tree'] is not None:
                self.assertNotIn('reference', it['entry'],
                                 f"derived constant '{it['name']}' should have no reference field")
            else:
                self.assertIn('reference', it['entry'],
                              f"value constant '{it['name']}' should keep its reference field")

    def test_every_constant_defines_exactly_one_of_value_or_formula(self):
        _, _, _, all_items, _ = gs.load_constants(None)
        self.assertGreater(len(all_items), 0)
        for it in all_items:
            has_value = 'value' in it['entry']
            has_formula = 'formula' in it['entry']
            self.assertNotEqual(has_value, has_formula,
                                f"Constant '{it['name']}' must define exactly one "
                                "of 'value' or 'formula'.")

    def test_e3sm_legacy_molar_masses_and_aliases_are_preserved(self):
        _, _, _, all_items, _ = gs.load_constants(None)
        entries = {item['name']: item['entry'] for item in all_items}
        for element, value in [('carbon', 0.0120107), ('oxygen', 0.0159994)]:
            with self.subTest(element=element):
                legacy_name = f'{element}_molar_mass_e3sm_legacy'
                self.assertEqual(entries[legacy_name]['value'], value)
                self.assertEqual(entries[legacy_name]['reference'], 'IUPAC1997')
                self.assertEqual(entries[f'{element}_molar_mass_reference']['formula'],
                                 legacy_name)
        self.assertEqual(
            entries['carbon_dioxide_molar_mass_reference']['formula'],
            'carbon_molar_mass_reference + 2 * oxygen_molar_mass_reference')

    def test_ciaaw_abridged_molar_masses_are_separate_opt_in_values(self):
        _, _, _, all_items, _ = gs.load_constants(None)
        entries = {item['name']: item['entry'] for item in all_items}
        for element, value, uncertainty in [('carbon', 0.012011, 2.0E-06),
                                            ('oxygen', 0.015999, 1.0E-06)]:
            with self.subTest(element=element):
                entry = entries[f'{element}_molar_mass_ciaaw2024_abridged']
                self.assertEqual(entry['value'], value)
                self.assertEqual(entry['units'], 'kg mol-1')
                self.assertEqual(entry['uncertainty'], uncertainty)
                self.assertEqual(entry['reference'], 'CIAAW2024_abridged')

    def test_conventional_values_and_si_provenance(self):
        _, _, _, all_items, _ = gs.load_constants(None)
        entries = {item['name']: item['entry'] for item in all_items}
        for name, value, reference in [
                ('standard_acceleration_of_gravity', 9.80665, 'CGPM1901'),
            ('standard_atmosphere', 101325, 'CGPM1954')]:
            with self.subTest(name=name):
                self.assertEqual(entries[name]['value'], value)
                self.assertEqual(entries[name]['reference'], reference)
                self.assertEqual(entries[name]['uncertainty'], 'exact')

    def test_all_references_resolve(self):
        dictionary = yaml.safe_load(
            (tools_dir.parent / 'pcd.yaml').read_text(encoding='utf-8')
        )['physical_constants_dictionary']
        self.assertIn('10.59161/AUEZ1291', dictionary['references']['SI2026']['citation'])
        _, _, _, all_items, _ = gs.load_constants(None)
        for item in all_items:
            entry = item['entry']
            if 'reference' in entry:
                with self.subTest(name=item['name']):
                    self.assertIn(entry['reference'], dictionary['references'])


if __name__ == '__main__':
    unittest.main()
