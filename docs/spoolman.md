# Spoolman External Filament Catalog

The Open Filament Database is published in the format [Spoolman](https://github.com/Donkie/Spoolman) reads for its **external filament database**. Point Spoolman at it and every filament in the database appears in Spoolman's "add filament" dropdown, with density, diameter, weight, temperatures and colour already filled in.

---

## Use it

Set `EXTERNAL_DB_URL` on the Spoolman container to the `spoolman/` directory of the published site, **with the trailing slash**, and restart Spoolman:

```yaml
services:
  spoolman:
    image: ghcr.io/donkie/spoolman:latest
    environment:
      - EXTERNAL_DB_URL=https://api.openfilamentdatabase.org/spoolman/
      - EXTERNAL_DB_NAME=Open Filament Database   # optional, labels the library in the UI
```

Spoolman fetches `filaments.json` and `materials.json` from that URL and re-syncs hourly. Setting `EXTERNAL_DB_URL` replaces Spoolman's default [SpoolmanDB](https://donkie.github.io/SpoolmanDB/) catalog rather than adding to it.

---

## What you get

One record per purchasable spool: every size of every colour of every filament becomes one entry.

| Spoolman field | Source in the database |
| --- | --- |
| `id` | `ofd_` + the size's `id`. Stable across rebuilds: Spoolman installs link to these ids, so the scheme will not change. |
| `manufacturer` | `brand.json` → `name` |
| `name` | The filament name plus the variant (colour) name, with a leading brand name and a bare material name removed. `PETG+` and `PLA Matte` are product lines and are kept. Refill sizes get a ` (refill)` suffix. |
| `material` | `filament.json` → `material` |
| `density` | `filament.json` → `density` |
| `weight` | `sizes.json` → `filament_weight` |
| `diameter` | `sizes.json` → `diameter` |
| `spool_weight` | `sizes.json` → `empty_spool_weight`, otherwise `null` |
| `color_hex` | `variant.json` → `color_hex`, lowercased without `#`; `null` if malformed |
| `extruder_temp`, `bed_temp` | The midpoint of the recommended min/max range. A bound of `0` is ignored rather than averaged. |
| `finish` | `matte` trait → `matte` |
| `pattern` | `imitates_marble` trait → `marble`, `glitter` trait → `sparkle` |
| `translucent` | `translucent` or `transparent` trait |
| `glow` | `glow` trait |

Where the database has no value the field is `null`; nothing is guessed. `spool_type`, `color_hexes` and `multi_color_direction` are always `null` today, because the database does not carry them in a form Spoolman understands.

**Left out:** discontinued products, and any size without a density, net weight or diameter, since Spoolman requires all three.

**`materials.json`** holds one entry per material type. The database does not record per-material defaults, so density and temperatures are the **median across the database's own filaments of that material**, and are `null` where no filament records a temperature.

Output is sorted by `id`, so rebuilding unchanged data gives byte-identical files.

---

## Build it yourself

```bash
python -m ofd build               # writes dist/spoolman/
python -m ofd build --skip-spoolman
```

The mapping lives in [`ofd/builder/spoolman_mapping.py`](../ofd/builder/spoolman_mapping.py), the writer in [`ofd/builder/exporters/spoolman_exporter.py`](../ofd/builder/exporters/spoolman_exporter.py), and both are covered by [`tests/test_spoolman_mapping.py`](../tests/test_spoolman_mapping.py) and [`tests/test_spoolman_exporter.py`](../tests/test_spoolman_exporter.py).

---

## See also

- [OrcaSlicer presets](orcaslicer.md)
- [Spoolman external database format](https://github.com/Donkie/Spoolman/blob/master/spoolman/externaldb.py)
