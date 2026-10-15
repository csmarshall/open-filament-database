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

# Spoolman installs link to a record by its id, so the id must survive rebuilds
# and data edits. It is "ofd_" + the size's stored canonical ``uuid`` — never the
# derived ``id`` (a uuid5 of names, weight, gtin, array index, ...), which changes
# when a typo is fixed. Treat the scheme as a published contract: never change it.
# Spoolman has no alias concept, so if a size's uuid is ever retired and recorded
# in a survivor's ``moved_from``, links to the old id will not follow; consumers
# can resolve it through api/v1/uuid-index.json.
ID_PREFIX = "ofd_"

# OFD trait -> Spoolman enum value, in precedence order: Spoolman allows a single
# finish and a single pattern, so when a variant carries several the first wins
# (marble over sparkle). OFD traits Spoolman has no equivalent for (silk, neon,
# iridescent, ...) are intentionally not represented.
FINISH_BY_TRAIT = {"matte": "matte"}
PATTERN_BY_TRAIT = {"imitates_marble": "marble", "glitter": "sparkle"}
# Multi-colour direction. Both traits set is ambiguous, so it yields None rather
# than a guess (see _multi_color_direction).
DIRECTION_BY_TRAIT = {"coextruded": "coaxial", "gradual_color_change": "longitudinal"}
TRANSLUCENT_TRAITS = frozenset({"translucent", "transparent"})
GLOW_TRAITS = frozenset({"glow"})

# Spoolman's maximum filament name length: FilamentParameters.name max_length=64 in
# spoolman/api/v1/filament.py and Filament.name String(64) in spoolman/database/models.py.
NAME_MAX_LENGTH = 64
ELLIPSIS = "…"
# Share of the budget reserved for the end of an elided name, so a trailing colour survives.
_ELIDED_TAIL_BUDGET = NAME_MAX_LENGTH // 3
_TRAILING_PARENTHETICAL_RE = re.compile(r"\s*\([^()]*\)\s*$")

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


def _multi_color_direction(traits: set[str]) -> str | None:
    """coextruded -> coaxial, gradual_color_change -> longitudinal; neither or both -> None."""
    directions = {value for trait, value in DIRECTION_BY_TRAIT.items() if trait in traits}
    return directions.pop() if len(directions) == 1 else None


def _colours(variant: dict, traits: set[str]) -> tuple[str | None, list[str] | None, str | None]:
    """
    Resolve a variant's colours into Spoolman's (color_hex, color_hexes, direction).

    A multi-colour record (SpoolmanDB's convention: ``color_hexes`` and a null
    ``color_hex``) is emitted only when the variant lists two or more valid colours
    AND a direction is known, because Spoolman rejects ``multi_color_hexes`` without
    a ``multi_color_direction``. OFD also stores colour lists for glow, temperature-
    change and angle-shift colours, so without a ``coextruded`` or
    ``gradual_color_change`` trait the list cannot be told apart from those and the
    variant is exported as single-colour using its first valid colour.
    """
    valid = [h for h in map(norm_hex, variant.get("color_hexes") or []) if h]
    direction = _multi_color_direction(traits)
    if len(valid) >= 2 and direction:
        return None, valid, direction
    return (valid[0] if valid else norm_hex(variant.get("color_hex"))), None, None


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
    (unless asked for), or missing the size's canonical uuid (the id is never
    derived from anything else), density, net weight or diameter — the last three
    are required by Spoolman and none may be guessed.
    """
    if not include_discontinued and is_discontinued(filament, variant, size):
        return None
    if not size.get("uuid"):
        return None
    if not filament.get("density") or not size.get("filament_weight") or not size.get("diameter"):
        return None

    traits = {key for key, enabled in (variant.get("traits") or {}).items() if enabled}

    name = display_name(filament["name"], brand["name"], filament["material"], variant["name"])
    if size.get("spool_refill"):
        name += " (refill)"

    empty_spool_weight = size.get("empty_spool_weight")
    color_hex, color_hexes, multi_color_direction = _colours(variant, traits)

    return {
        "id": f"{ID_PREFIX}{size['uuid']}",
        "manufacturer": brand["name"],
        "name": name,
        "material": filament["material"],
        "density": float(filament["density"]),
        "weight": float(size["filament_weight"]),
        "spool_weight": float(empty_spool_weight) if empty_spool_weight else None,
        "spool_type": None,
        "diameter": float(size["diameter"]),
        "color_hex": color_hex,
        "color_hexes": color_hexes,
        "multi_color_direction": multi_color_direction,
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
    Temperature medians ignore zero bounds (see :func:`pick_temp`) and use Python's
    round-half-even; a temperature no filament of the material records stays None. Spoolman only
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


def shorten_name(name: str) -> str:
    """
    Deterministically elide the middle of a name so it fits Spoolman's length limit.

    Keeps whole words from the start and from the end, joined by one "… ", so a
    trailing colour such as "Neon Yellow" survives. A name already within the limit
    is returned unchanged, which makes the function idempotent. A single word longer
    than its share is cut by characters.
    """
    if len(name) <= NAME_MAX_LENGTH:
        return name

    words = name.split()
    tail_words: list[str] = []
    while words and len(" ".join([words[-1], *tail_words])) <= _ELIDED_TAIL_BUDGET:
        tail_words.insert(0, words.pop())
    tail = " ".join(tail_words)
    if not tail:
        tail = words.pop()[-_ELIDED_TAIL_BUDGET:]

    separator = ELLIPSIS + " "
    head_budget = NAME_MAX_LENGTH - len(separator) - len(tail)
    head_words: list[str] = []
    for word in words:
        if len(" ".join([*head_words, word])) > head_budget:
            break
        head_words.append(word)
    head = " ".join(head_words) or (words[0][:head_budget] if words else "")
    return f"{head}{separator}{tail}"


def shorten_long_names(records: list[dict[str, Any]]) -> list[tuple[str, str, str, str]]:
    """
    Bring every record's name within Spoolman's limit, in place, and report what changed.

    Per over-long name: (a) drop a trailing parenthetical if the remainder fits and
    does not collide with another record's name for the same (manufacturer, material,
    weight, diameter); otherwise (b) elide the middle (:func:`shorten_name`). Collisions
    are judged against every other record's name and every other candidate, so the
    result does not depend on record order.

    Returns ``(id, original, shortened, step)`` tuples, step being "parenthetical" or
    "elided".
    """

    def key(record: dict[str, Any], name: str) -> tuple:
        return (
            record["manufacturer"],
            record["material"],
            record["weight"],
            record["diameter"],
            name,
        )

    def stripped(name: str) -> str:
        return _TRAILING_PARENTHETICAL_RE.sub("", name)

    in_use: dict[tuple, int] = {}
    for record in records:
        name = record["name"]
        candidate = stripped(name) if len(name) > NAME_MAX_LENGTH else name
        if len(name) > NAME_MAX_LENGTH and len(candidate) > NAME_MAX_LENGTH:
            continue
        in_use[key(record, candidate)] = in_use.get(key(record, candidate), 0) + 1

    changes = []
    for record in records:
        original = record["name"]
        if len(original) <= NAME_MAX_LENGTH:
            continue
        candidate = stripped(original)
        if (
            candidate
            and candidate != original
            and len(candidate) <= NAME_MAX_LENGTH
            and in_use[key(record, candidate)] == 1
        ):
            record["name"], step = candidate, "parenthetical"
        else:
            record["name"], step = shorten_name(original), "elided"
        changes.append((record["id"], original, record["name"], step))
    return changes
