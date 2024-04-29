"""Various utilities for handling colors."""

from __future__ import annotations

from typing import Tuple

# Define usable ranges as bulbs either ignore or behave unexpectedly
# when it is sent a value is outside of the range.
TemperatureRange = Tuple[int, int]
TEMPERATURE_PROFILES: dict[str, TemperatureRange] = dict(
    (model, temp)
    for models, temp in (
        # Lightify RGBW, 1900-6500K
        (["LIGHTIFY A19 RGBW"], (151, 555)),
    )
    for model in models
)

ColorXY = Tuple[float, float]
ColorGamut = Tuple[ColorXY, ColorXY, ColorXY]
COLOR_PROFILES: dict[str, ColorGamut] = dict(
    (model, gamut)
    for models, gamut in (
        # Lightify RGBW, 1900-6500K
        # https://flow-morewithless.blogspot.com/2015/01/osram-lightify-color-gamut-and-spectrum.html
        (
            ["LIGHTIFY A19 RGBW"],
            ((0.683924, 0.315904), (0.391678, 0.501414), (0.136990, 0.051035)),
        ),
    )
    for model in models
)


def get_profiles(model: str) -> tuple[TemperatureRange, ColorGamut]:
    """Return the temperature and color profiles for a given model."""
    return (
        TEMPERATURE_PROFILES.get(model, (150, 600)),
        COLOR_PROFILES.get(model, ((1.0, 0.0), (0.0, 1.0), (0.0, 0.0))),
    )


def is_same_side(p1: ColorXY, p2: ColorXY, a: ColorXY, b: ColorXY) -> bool:
    """Test if points p1 and p2 lie on the same side of line a-b."""
    # pylint: disable=invalid-name
    vector_ab = [y - x for x, y in zip(a, b)]
    vector_ap1 = [y - x for x, y in zip(a, p1)]
    vector_ap2 = [y - x for x, y in zip(a, p2)]
    cross_vab_ap1 = vector_ab[0] * vector_ap1[1] - vector_ab[1] * vector_ap1[0]
    cross_vab_ap2 = vector_ab[0] * vector_ap2[1] - vector_ab[1] * vector_ap2[0]
    return (cross_vab_ap1 * cross_vab_ap2) >= 0


def closest_point(p: ColorXY, a: ColorXY, b: ColorXY) -> ColorXY:
    """Test if points p1 and p2 lie on the same side of line a-b."""
    # pylint: disable=invalid-name
    vector_ab = [y - x for x, y in zip(a, b)]
    vector_ap = [y - x for x, y in zip(a, p)]
    dot_ap_ab = sum(x * y for x, y in zip(vector_ap, vector_ab))
    dot_ab_ab = sum(x * y for x, y in zip(vector_ab, vector_ab))
    t = max(0.0, min(dot_ap_ab / dot_ab_ab, 1.0))
    return a[0] + vector_ab[0] * t, a[1] + vector_ab[1] * t


def limit_to_gamut(xy: ColorXY, gamut: ColorGamut) -> ColorXY:
    """Return the closest point within the gamut triangle for colorxy."""
    # pylint: disable=invalid-name
    r, g, b = gamut

    # http://www.blackpawn.com/texts/pointinpoly/
    if not is_same_side(xy, r, g, b):
        xy = closest_point(xy, g, b)

    if not is_same_side(xy, g, b, r):
        xy = closest_point(xy, b, r)

    if not is_same_side(xy, b, r, g):
        xy = closest_point(xy, r, g)

    return xy
