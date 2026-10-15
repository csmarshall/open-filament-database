"""
Spoolman external filament database exporter.

Spoolman can populate its filament dropdown from a remote catalog: it fetches
``filaments.json`` and ``materials.json`` from the URL in its ``EXTERNAL_DB_URL``
environment variable. This exporter writes exactly those two files into a static
tree published alongside the API:

  spoolman/
    filaments.json
    materials.json
    README.txt

so ``EXTERNAL_DB_URL=https://<site>/spoolman/`` works with no further setup.

The OFD -> Spoolman field mapping lives in ``ofd/builder/spoolman_mapping.py``.
"""

import json
from pathlib import Path

from ..models import Database
from ..spoolman_mapping import build_filament, build_materials

README_TEXT = """\
Spoolman external filament catalog from the Open Filament Database
===================================================================

filaments.json and materials.json in this directory are in the format Spoolman
reads from its external database URL, generated from
https://openfilamentdatabase.org/

USE
---
Point Spoolman at this directory. The trailing slash matters:

  EXTERNAL_DB_URL=https://<this site>/spoolman/
  EXTERNAL_DB_NAME=Open Filament Database      (optional, labels it in the UI)

then restart Spoolman. It re-syncs on an hourly interval.

WHAT THIS IS
------------
One record per purchasable spool (every size of every colour). Ids are
"ofd_<size uuid>", where the uuid is the size's canonical Open Filament Database
uuid, so they stay stable across rebuilds and edits. Spoolman has no alias
concept: if a uuid is ever retired (recorded as "moved_from" on its survivor),
links to the old id will not follow; api/v1/uuid-index.json maps old to current.
Values the database does not record (spool weight, colour, temperatures, ...) are
null rather than guessed. Multi-colour filaments carry color_hexes and a null
color_hex; multi_color_direction is coaxial for coextruded, longitudinal for
gradual colour change, and null when the database does not say. Discontinued or incomplete products (no density,
weight or diameter) are left out. Extruder and bed temperature are the midpoint
of the recommended range, ignoring a bound of 0. materials.json holds, per
material type, the median density and temperatures across the exported
(non-discontinued) filaments of that material that record the value.

Found a wrong number? Corrections are welcome at
https://openfilamentdatabase.org/
"""


def export_spoolman(
    db: Database,
    output_dir: str,
    version: str,
    generated_at: str,
    **kwargs,
) -> int:
    """
    Export the Spoolman external database files. Returns the number of filament records.
    """
    spoolman_path = Path(output_dir) / "spoolman"
    spoolman_path.mkdir(parents=True, exist_ok=True)

    brands = {brand["id"]: brand for brand in db.brands}
    filaments = {filament["id"]: filament for filament in db.filaments}
    variants = {variant["id"]: variant for variant in db.variants}

    records: list[dict] = []
    exported_filament_ids: set[str] = set()
    dangling = 0
    no_uuid = 0

    for size in db.sizes:
        variant = variants.get(size["variant_id"])
        filament = filaments.get(variant["filament_id"]) if variant else None
        brand = brands.get(filament["brand_id"]) if filament else None
        if not (variant and filament and brand):
            dangling += 1
            continue

        if not size.get("uuid"):
            no_uuid += 1
            continue

        record = build_filament(brand, filament, variant, size)
        if record is None:
            continue
        records.append(record)
        exported_filament_ids.add(filament["id"])

    records.sort(key=lambda record: record["id"])
    materials = build_materials(filaments[fid] for fid in sorted(exported_filament_ids))

    # Compact separators: Spoolman downloads these on every sync.
    for name, payload in (("filaments.json", records), ("materials.json", materials)):
        with open(spoolman_path / name, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
            f.write("\n")

    (spoolman_path / "README.txt").write_text(README_TEXT, encoding="utf-8")

    skipped = len(db.sizes) - len(records)
    print(f"  Written: {len(records)} Spoolman filaments, {len(materials)} materials")
    if skipped:
        print(
            f"  Skipped: {skipped} sizes (discontinued, or missing density/weight/diameter"
            + (f"; {dangling} dangling references" if dangling else "")
            + (f"; {no_uuid} without a uuid" if no_uuid else "")
            + ")"
        )

    return len(records)
