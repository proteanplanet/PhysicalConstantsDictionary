# PhysicalConstantsDictionary
YAML dictionary of physical constants and tools to create consistent constant sets for Earth System Models

To learn mode about this project, please read the available [documentation](https://github.com/ESCOMP/PhysicalConstantsDictionary/wiki). 

## Derived constants

An entry in `pcd.yaml` must define exactly one of `value` or `formula`:

- `value`: a plain numerical value, for constants that are not derived from
  other constants in the dictionary.
- `formula`: an arithmetic expression (`+`, `-`, `*`, `/`, `**`, unary `+`/`-`,
  and parentheses) referencing the `name` of one or more other constants in
  the dictionary, for constants that are derived from them. For example:

  ```yaml
  - name: molar_gas_constant
    formula: avogadro_constant * boltzmann_constant
  ```

An entry's `type` should be `formula` when it has a `formula`, and `strict`
(or another appropriate value) when it has a plain `value`.

A derived constant's value isn't taken from a single cited source, so a
`formula` entry has no `reference` field: omit it from the `pcd.yaml` entry
entirely. Anyone who wants to know where the constants a formula uses came
from can follow the formula to their own `reference` entries. The generated
file's trailing comment for a `formula` entry correspondingly shows only its
units (when it has any), with no reference.

`generate_source.py` never evaluates a `formula` itself: it validates that the
expression only uses arithmetic operators and known constant names, translates
it into the equivalent C++/Fortran expression, and lets the compiler compute
the actual value as a `constexpr`/`parameter`. This keeps derived constants
numerically consistent (to full double precision) with the constants they are
derived from, rather than relying on a separately hand-copied numerical value
that can drift out of sync.

Generated C++ headers require C++20.

## Atomic-weight reference sets and provenance

The existing `carbon_molar_mass_reference` and `oxygen_molar_mass_reference`
names remain compatibility aliases for the explicitly named E3SM legacy set:
`carbon_molar_mass_e3sm_legacy` (0.0120107 kg mol-1) and
`oxygen_molar_mass_e3sm_legacy` (0.0159994 kg mol-1). Their historical
`IUPAC1997` source is retained. `carbon_dioxide_molar_mass_reference` continues
to use these legacy values; no existing numerical default changes.

The separately named `carbon_molar_mass_ciaaw2024_abridged` and
`oxygen_molar_mass_ciaaw2024_abridged` constants are opt-in model values of
0.012011 and 0.015999 kg mol-1, respectively. They use the
[CIAAW 2024 abridged table](https://ciaaw.org/abridged-atomic-weights.htm),
not midpoints of the full standard atomic-weight intervals. The full
dimensionless intervals are [12.0096, 12.0116] for carbon and
[15.99903, 15.99977] for oxygen. These describe natural isotopic variation,
not statistical measurement-error bars. The entries' uncertainty fields
carry the abridged table's bounds scaled to kg mol-1, not standard uncertainties.

These model molar masses use the approximate factor 0.001 kg mol-1 per unit
atomic weight. Since the 2019 SI revision, the molar-mass constant is not
exactly 0.001 kg mol-1; these entries are not precision determinations of
sample-specific molar masses. Components must assess the numerical and
scientific impact before switching references, including recomputing derived
compound molar masses consistently. The generator does not switch sets
automatically; both use distinct names in the same chemical group.

`SI2026` cites the 2026 update of the ninth-edition SI Brochure, not a new
edition or a change to numerical defaults. Standard gravity and the
standard atmosphere retain their exact conventional values, with citations
to the defining CGPM declaration (1901) and resolution (1954), rather than
to the CODATA adjustment.

The generator automatically orders every constant so that a derived
constant's `formula` only ever references constants declared earlier in the
generated file, regardless of the order groups/entries happen to appear in
`pcd.yaml`; a circular dependency between formulas is reported as an error.

`tools/validate_generated_sources.py` generates the C++ header and Fortran
module and attempts to compile a small driver program that references every
constant, as an extra safety net that generated formulas are valid, correctly
ordered code in both languages (it skips a language's check if that
language's compiler isn't available).

## Automated E3SM Updates

This repository includes a GitHub Actions workflow that automatically updates the E3SM repository when `pcd.yaml` is modified. 

When changes are pushed to `pcd.yaml` on the `main` branch, the workflow:
1. Generates updated source files (`pcd_const.f90` and `pcd_const.h`)
2. Creates a pull request in the [e3sm-project/e3sm](https://github.com/e3sm-project/e3sm) repository with:
   - Updated `share/util/pcd_const.f90` (Fortran module)
   - Updated `share/util_cxx/pcd_const.h` (C++ header)
   - Updated `share/pcd.yaml` (source YAML file)

**Note**: The workflow requires a GitHub personal access token (PAT) stored as a secret named `E3SM_PAT` with permissions to create pull requests in the e3sm-project/e3sm repository.
