"""
Mapping from Open Filament Database entities to Spoolman external-database records.

Spoolman (https://github.com/Donkie/Spoolman) fills its "add filament" dropdown
from two files, ``filaments.json`` and ``materials.json``, fetched from whatever
``EXTERNAL_DB_URL`` points at. Their shape is Spoolman's ``ExternalFilament`` and
``ExternalMaterial`` models (``spoolman/externaldb.py``).

OFD nests brand -> material -> filament -> variant -> size, while Spoolman wants
one flat record per purchasable spool, so every OFD **size** becomes one record.

Nothing is invented: where OFD has no value the field is ``None``. The one
aggregate — the per-material defaults in ``materials.json`` — is a median over
OFD's own filaments and is isolated in :func:`build_materials`.

This module is pure — no filesystem access, stdlib only — so the rules below are
directly unit-testable. See ``ofd/builder/exporters/spoolman_exporter.py`` for the
writer.
"""

import re
import statistics
from collections.abc import Iterable
from typing import Any

# Spoolman records link to a size by this id, so installs that picked an OFD
# filament keep working across rebuilds only while the scheme stays stable.
# Treat it as a published contract: never change it.
ID_PREFIX = "ofd_"

# Enum values accepted by Spoolman's ExternalFilament model.
SPOOLMAN_FINISHES = frozenset({"matte", "glossy"})
SPOOLMAN_PATTERNS = frozenset({"marble", "sparkle"})

# OFD trait -> Spoolman enum value, in precedence order: Spoolman allows a single
# finish and a single pattern, so when a variant carries several the first wins
# (marble over sparkle). OFD traits Spoolman has no equivalent for (silk, neon,
# iridescent, ...) are intentionally not represented.
FINISH_BY_TRAIT = {"matte": "matte"}
PATTERN_BY_TRAIT = {"imitates_marble": "marble", "glitter": "sparkle"}
TRANSLUCENT_TRAITS = frozenset({"translucent", "transparent"})
GLOW_TRAITS = frozenset({"glow"})

_HEX_RE = re.compile(r"^[0-9a-f]{6}([0-9a-f]{2})?$")


def norm(text: str) -> str:
    """Normalise a name for comparison: lowercase alphanumerics plus '+'.

    '+' is kept because it is meaningful in filament lines: PETG+ is not PETG.
    """
    return re.sub(r"[^a-z0-9+]", "", text.lower())


def norm_hex(value: str | None) -> str | None:
    """Return a Spoolman-style hex (lowercase, no '#') or None if malformed."""
    if not value or not isinstance(value, str):
        return None
    cleaned = value.strip().lstrip("#").lower()
    return cleaned if _HEX_RE.match(cleaned) else None


def pick_temp(low: int | None, high: int | None) -> int | None:
    """Collapse an OFD min/max range into Spoolman's single temperature.

    Uses the midpoint when both ends are real. OFD often records a bed minimum of
    0 meaning "unheated is fine", which would drag a midpoint to nonsense, so a
    zero end is ignored rather than averaged.
    """
    ends = [t for t in (low, high) if t]
    if not ends:
        return None
    return round(sum(ends) / len(ends))


def display_name(filament_name: str, brand_name: str, material: str, colour: str) -> str:
    """Build a Spoolman-style name: product line + colour, minus brand and bare material.

    OFD filament names often repeat the brand ("123-3D Filament PLA"); Spoolman shows
    manufacturer and material in their own columns, so a leading brand name is
    stripped — but only at a word boundary, so "Acme" is not cut out of "Acmeflex".
    The material is dropped only when it is the whole remaining line, so product
    lines like "PETG+" or "PLA Matte" survive intact.
    """
    line = filament_name.strip()
    if re.match(rf"{re.escape(brand_name)}(?=[\s_-]|$)", line, re.IGNORECASE):
        line = line[len(brand_name) :].strip(" -_")
    if norm(line) == norm(material):
        line = ""
    return f"{line} {colour}".strip() if line else colour


def _first_match(traits: set[str], mapping: dict[str, str]) -> str | None:
    """The mapped value of the first trait in ``mapping`` (precedence order) that is set."""
    return next((value for trait, value in mapping.items() if trait in traits), None)


def is_discontinued(filament: dict, variant: dict, size: dict) -> bool:
    """A size is discontinued if it, its variant or its filament is."""
    return bool(
        size.get("discontinued") or variant.get("discontinued") or filament.get("discontinued")
    )


def build_filament(
    brand: dict,
    filament: dict,
    variant: dict,
    size: dict,
    include_discontinued: bool = False,
) -> dict[str, Any] | None:
    """
    Build one Spoolman ``ExternalFilament`` record from an OFD size row.

    Returns None when the row cannot be represented honestly: discontinued
    (unless asked for), or missing density, net weight or diameter — all three
    are required by Spoolman and none may be guessed.
    """
    if not include_discontinued and is_discontinued(filament, variant, size):
        return None
    if not filament.get("density") or not size.get("filament_weight") or not size.get("diameter"):
        return None

    traits = {key for key, enabled in (variant.get("traits") or {}).items() if enabled}

    name = display_name(filament["name"], brand["name"], filament["material"], variant["name"])
    if size.get("spool_refill"):
        name += " (refill)"

    empty_spool_weight = size.get("empty_spool_weight")

    return {
        "id": f"{ID_PREFIX}{size['id']}",
        "manufacturer": brand["name"],
        "name": name,
        "material": filament["material"],
        "density": float(filament["density"]),
        "weight": float(size["filament_weight"]),
        "spool_weight": float(empty_spool_weight) if empty_spool_weight else None,
        "spool_type": None,
        "diameter": float(size["diameter"]),
        "color_hex": norm_hex(variant.get("color_hex")),
        "color_hexes": None,
        "multi_color_direction": None,
        "extruder_temp": pick_temp(
            filament.get("min_print_temperature"), filament.get("max_print_temperature")
        ),
        "bed_temp": pick_temp(
            filament.get("min_bed_temperature"), filament.get("max_bed_temperature")
        ),
        "finish": _first_match(traits, FINISH_BY_TRAIT),
        "pattern": _first_match(traits, PATTERN_BY_TRAIT),
        "translucent": bool(traits & TRANSLUCENT_TRAITS),
        "glow": bool(traits & GLOW_TRAITS),
    }


def _median_int(values: list[int]) -> int | None:
    return round(statistics.median(values)) if values else None


def build_materials(filaments: Iterable[dict]) -> list[dict[str, Any]]:
    """
    Build Spoolman ``ExternalMaterial`` records, one per material type.

    OFD's material entities carry no density or temperatures, so the per-material
    defaults Spoolman wants are the **median across OFD's own filaments** of that
    material type: density, and the midpoint temperatures from :func:`pick_temp`.
    A temperature no filament of the material records stays None. Spoolman only
    uses these to pre-fill a form for a material that has no filament picked, so
    a statistical default is acceptable there; change the policy here and nowhere
    else.

    ``filaments`` should be the OFD filaments that actually produced records, so
    materials.json never describes a material filaments.json does not contain.
    Only filaments with a density are considered (Spoolman requires it).
    """
    by_material: dict[str, list[dict]] = {}
    for filament in filaments:
        if filament.get("density"):
            by_material.setdefault(filament["material"], []).append(filament)

    materials = []
    for material, group in sorted(by_material.items()):
        extruder = [
            t
            for f in group
            if (t := pick_temp(f.get("min_print_temperature"), f.get("max_print_temperature")))
        ]
        bed = [
            t
            for f in group
            if (t := pick_temp(f.get("min_bed_temperature"), f.get("max_bed_temperature")))
        ]
        materials.append(
            {
                "material": material,
                "density": round(statistics.median(float(f["density"]) for f in group), 3),
                "extruder_temp": _median_int(extruder),
                "bed_temp": _median_int(bed),
            }
        )
    return materials
