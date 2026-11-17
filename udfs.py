"""UDFs for the brewery batch CLI. Pure Python, no table imports."""

from __future__ import annotations

import pixeltable as pxt

# Homebrew formula: ABV from original (OG) and final (FG) specific gravity.
ABV_FACTOR = 131.25


@pxt.expr_udf
def abv_pct(og: float, fg: float) -> float:
    """Alcohol by volume (%) from original and final gravity. An expression UDF: no Python runs per row."""
    return (og - fg) * ABV_FACTOR


@pxt.expr_udf
def attenuation_pct(og: float, fg: float) -> float:
    """Apparent attenuation (%): the share of the original extract the yeast consumed."""
    return (og - fg) / (og - 1.0) * 100.0


@pxt.expr_udf
def plato(sg: float) -> float:
    """Specific gravity to degrees Plato (polynomial approximation)."""
    return -616.868 + 1111.14 * sg - 630.272 * sg * sg + 135.997 * sg * sg * sg


@pxt.udf
def temp_corrected_sg(sg: float, temp_c: float) -> float:
    """Correct a hydrometer reading to the 20 C calibration temperature."""
    f = temp_c * 9 / 5 + 32
    cal = 68.0
    k = lambda t: 1.00130346 - 0.000134722124 * t + 0.00000204052596 * t * t - 0.00000000232820948 * t**3  # noqa: E731
    return round(sg * k(f) / k(cal), 4)


@pxt.udf
def stage(sg: float, og: float) -> str:
    """Fermentation stage from how much of the gravity drop (OG -> 1.000) has happened."""
    if og <= 1.0:
        return 'unknown'
    done = (og - sg) / (og - 1.0)
    if done < 0.1:
        return 'lag'
    if done < 0.6:
        return 'active'
    if done < 0.85:
        return 'slowing'
    return 'terminal'
