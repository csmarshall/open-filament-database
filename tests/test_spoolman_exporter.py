"""Tests for the Spoolman external-database exporter."""

import json

from ofd.builder.exporters.spoolman_exporter import export_spoolman
from ofd.builder.models import Database


def make_db() -> Database:
    db = Database()
    db.brands = [{"id": "b1", "name": "Acme", "slug": "acme"}]
    db.materials = [{"id": "m1", "brand_id": "b1", "material": "PLA", "slug": "PLA"}]
    db.filaments = [
        {
            "id": "f1",
            "brand_id": "b1",
            "material_id": "m1",
            "name": "Acme PLA",
            "material": "PLA",
            "density": 1.24,
            "min_print_temperature": 190,
            "max_print_temperature": 220,
            "min_bed_temperature": 0,
            "max_bed_temperature": 60,
        },
        {
            "id": "f2",
            "brand_id": "b1",
            "material_id": "m1",
            "name": "No Density PLA",
            "material": "PLA",
        },
    ]
    db.variants = [
        {"id": "v1", "filament_id": "f1", "name": "Black", "color_hex": "#000000"},
        {
            "id": "v2",
            "filament_id": "f1",
            "name": "Old",
            "color_hex": "#ffffff",
            "discontinued": True,
        },
        {"id": "v3", "filament_id": "f2", "name": "Red", "color_hex": "#ff0000"},
    ]
    db.sizes = [
        {"id": "zzz", "variant_id": "v1", "diameter": 1.75, "filament_weight": 1000},
        {
            "id": "aaa",
            "variant_id": "v1",
            "diameter": 2.85,
            "filament_weight": 750,
            "spool_refill": True,
        },
        {"id": "ddd", "variant_id": "v2", "diameter": 1.75, "filament_weight": 1000},
        {"id": "nod", "variant_id": "v3", "diameter": 1.75, "filament_weight": 1000},
        {"id": "dangling", "variant_id": "missing", "diameter": 1.75, "filament_weight": 1000},
    ]
    return db


def run(tmp_path, db=None, **kwargs):
    count = export_spoolman(
        db or make_db(), str(tmp_path), "test", "2026-01-01T00:00:00Z", **kwargs
    )
    out = tmp_path / "spoolman"
    return (
        count,
        json.loads((out / "filaments.json").read_text(encoding="utf-8")),
        json.loads((out / "materials.json").read_text(encoding="utf-8")),
    )


def test_writes_the_files_spoolman_fetches(tmp_path):
    run(tmp_path)
    assert sorted(p.name for p in (tmp_path / "spoolman").iterdir()) == [
        "README.txt",
        "filaments.json",
        "materials.json",
    ]


def test_skips_discontinued_missing_density_and_dangling(tmp_path):
    count, filaments, _ = run(tmp_path)
    assert count == len(filaments) == 2
    assert {f["id"] for f in filaments} == {"ofd_zzz", "ofd_aaa"}


def test_include_discontinued(tmp_path):
    _, filaments, _ = run(tmp_path, include_discontinued=True)
    assert "ofd_ddd" in {f["id"] for f in filaments}


def test_sorted_by_id(tmp_path):
    _, filaments, _ = run(tmp_path)
    assert [f["id"] for f in filaments] == ["ofd_aaa", "ofd_zzz"]


def test_output_is_deterministic(tmp_path):
    db = make_db()
    run(tmp_path / "one", db)
    db.sizes.reverse()
    run(tmp_path / "two", db)
    for name in ("filaments.json", "materials.json"):
        assert (tmp_path / "one" / "spoolman" / name).read_bytes() == (
            tmp_path / "two" / "spoolman" / name
        ).read_bytes()


def test_materials_derived_from_exported_filaments_only(tmp_path):
    _, _, materials = run(tmp_path)
    assert materials == [{"material": "PLA", "density": 1.24, "extruder_temp": 205, "bed_temp": 60}]


def test_refill_and_ids_in_output(tmp_path):
    _, filaments, _ = run(tmp_path)
    by_id = {f["id"]: f for f in filaments}
    assert by_id["ofd_aaa"]["name"] == "Black (refill)"
    assert by_id["ofd_aaa"]["diameter"] == 2.85
    assert by_id["ofd_zzz"]["name"] == "Black"
