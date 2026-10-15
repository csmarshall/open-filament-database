"""Tests for the OFD -> Spoolman mapping.

Each case pins one rule of Spoolman's ``ExternalFilament`` / ``ExternalMaterial``
model or one decision about how OFD data folds into it, so a regression shows up
as a failing test rather than as a catalog entry that quietly lies.
"""

from ofd.builder.spoolman_mapping import (
    FINISH_BY_TRAIT,
    ID_PREFIX,
    PATTERN_BY_TRAIT,
    build_filament,
    build_materials,
    display_name,
    norm,
    norm_hex,
    pick_temp,
)

BRAND = {"id": "b1", "name": "Acme", "slug": "acme"}


def filament(**overrides) -> dict:
    base = {
        "id": "f1",
        "brand_id": "b1",
        "name": "Acme PLA",
        "material": "PLA",
        "density": 1.24,
        "min_print_temperature": 190,
        "max_print_temperature": 220,
        "min_bed_temperature": 50,
        "max_bed_temperature": 60,
    }
    base.update(overrides)
    return base


def variant(**overrides) -> dict:
    base = {"id": "v1", "filament_id": "f1", "name": "Black", "color_hex": "#000000"}
    base.update(overrides)
    return base


def size(**overrides) -> dict:
    base = {
        "id": "s1",
        "uuid": "11111111-1111-4111-8111-111111111111",
        "variant_id": "v1",
        "diameter": 1.75,
        "filament_weight": 1000,
    }
    base.update(overrides)
    return base


def build(fil=None, var=None, siz=None, **kwargs):
    return build_filament(
        BRAND,
        fil if fil is not None else filament(),
        var if var is not None else variant(),
        siz if siz is not None else size(),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------


def test_norm_keeps_plus():
    """Regression: stripping '+' made 'PETG+' indistinguishable from bare 'PETG'."""
    assert norm("PETG+") == "petg+"
    assert norm("PETG+") != norm("PETG")
    assert norm("Hello, World-1!") == "helloworld1"


def test_norm_hex_lowercases_and_strips_hash():
    assert norm_hex("#FF5733") == "ff5733"
    assert norm_hex("  #aabbccdd ") == "aabbccdd"


def test_norm_hex_discards_malformed():
    assert norm_hex("#12345") is None
    assert norm_hex("#GGGGGG") is None
    assert norm_hex("red") is None
    assert norm_hex("") is None
    assert norm_hex(None) is None


def test_pick_temp_midpoint():
    assert pick_temp(190, 220) == 205
    assert pick_temp(200, 200) == 200


def test_pick_temp_ignores_zero_bound():
    """A bed minimum of 0 means 'unheated is fine'; averaging it would give nonsense."""
    assert pick_temp(0, 60) == 60
    assert pick_temp(50, 0) == 50
    assert pick_temp(0, 0) is None
    assert pick_temp(None, None) is None
    assert pick_temp(None, 70) == 70


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------


def test_name_strips_brand_and_bare_material():
    assert display_name("Acme PLA", "Acme", "PLA", "Red") == "Red"


def test_name_keeps_product_line():
    assert display_name("Acme PLA Matte", "Acme", "PLA", "Red") == "PLA Matte Red"


def test_petg_plus_survives():
    assert display_name("PETG+", "Acme", "PETG", "Black") == "PETG+ Black"
    assert display_name("Acme PETG+", "Acme", "PETG", "Black") == "PETG+ Black"


def test_brand_stripped_only_at_word_boundary():
    """'Acme' must not be cut out of 'Acmeflex'."""
    assert display_name("Acmeflex PLA", "Acme", "PLA", "Red") == "Acmeflex PLA Red"


def test_brand_with_digits_and_hyphen_stripped():
    assert display_name("123-3D Filament PLA", "123-3D", "PLA", "Red") == "Filament PLA Red"


def test_brand_separators_stripped():
    assert display_name("Brand - PLA", "Brand", "PLA", "Red") == "Red"
    assert display_name("Brand-PLA", "Brand", "PLA", "Red") == "Red"
    assert display_name("brand_PLA", "Brand", "PLA", "Red") == "Red"
    assert display_name("Brand", "Brand", "PLA", "Red") == "Red"


def test_bare_material_without_brand_dropped():
    assert display_name("PETG", "Acme", "PETG", "Black") == "Black"


# ---------------------------------------------------------------------------
# Whole-record mapping
# ---------------------------------------------------------------------------


def test_record_shape_and_id():
    record = build()
    assert record["id"] == f"{ID_PREFIX}11111111-1111-4111-8111-111111111111"
    assert record["manufacturer"] == "Acme"
    assert record["name"] == "Black"
    assert record["material"] == "PLA"
    assert record["density"] == 1.24
    assert record["weight"] == 1000.0
    assert record["diameter"] == 1.75
    assert record["color_hex"] == "000000"
    assert record["extruder_temp"] == 205
    assert record["bed_temp"] == 55
    assert set(record) == {
        "id",
        "manufacturer",
        "name",
        "material",
        "density",
        "weight",
        "spool_weight",
        "spool_type",
        "diameter",
        "color_hex",
        "color_hexes",
        "multi_color_direction",
        "extruder_temp",
        "bed_temp",
        "finish",
        "pattern",
        "translucent",
        "glow",
    }


def test_id_uses_canonical_uuid_not_derived_id():
    """The derived ``id`` changes when a name is fixed; the stored ``uuid`` does not."""
    record = build(siz=size(id="derived-id", uuid="canonical-uuid"))
    assert record["id"] == "ofd_canonical-uuid"
    assert "derived-id" not in record["id"]


def test_size_without_uuid_is_skipped_not_given_a_derived_id():
    assert build(siz=size(uuid=None)) is None
    assert build(siz=size(uuid="")) is None


def test_mapped_enum_values_are_valid_spoolman_enums():
    # Hard-coded from spoolman/externaldb.py (Finish, Pattern, MultiColorDirection).
    spoolman_finish = {"matte", "glossy"}
    spoolman_pattern = {"marble", "sparkle"}
    spoolman_direction = {"coaxial", "longitudinal"}
    assert set(FINISH_BY_TRAIT.values()) <= spoolman_finish
    assert set(PATTERN_BY_TRAIT.values()) <= spoolman_pattern
    everything = dict.fromkeys((*FINISH_BY_TRAIT, *PATTERN_BY_TRAIT), True)
    record = build(var=variant(traits=everything))
    assert record["finish"] in spoolman_finish
    assert record["pattern"] in spoolman_pattern
    assert record["multi_color_direction"] in spoolman_direction | {None}


def test_no_invented_values():
    """Where OFD has no value the record carries null, never a guess."""
    record = build(
        fil=filament(
            min_print_temperature=None,
            max_print_temperature=None,
            min_bed_temperature=0,
            max_bed_temperature=0,
        ),
        var=variant(color_hex="not-a-colour"),
    )
    assert record["spool_weight"] is None
    assert record["spool_type"] is None
    assert record["color_hex"] is None
    assert record["color_hexes"] is None
    assert record["multi_color_direction"] is None
    assert record["extruder_temp"] is None
    assert record["bed_temp"] is None
    assert record["finish"] is None
    assert record["pattern"] is None
    assert record["translucent"] is False
    assert record["glow"] is False


def test_spool_weight_from_empty_spool_weight():
    assert build(siz=size(empty_spool_weight=216))["spool_weight"] == 216.0
    assert build(siz=size(empty_spool_weight=0))["spool_weight"] is None


def test_refill_suffix():
    assert build(siz=size(spool_refill=True))["name"] == "Black (refill)"
    assert build(siz=size(spool_refill=False))["name"] == "Black"


def test_density_required():
    assert build(fil=filament(density=None)) is None
    assert build(fil=filament(density=0)) is None


def test_weight_and_diameter_required():
    assert build(siz=size(filament_weight=None)) is None
    assert build(siz=size(diameter=None)) is None


def test_discontinued_skipped_by_default_at_every_level():
    assert build(siz=size(discontinued=True)) is None
    assert build(var=variant(discontinued=True)) is None
    assert build(fil=filament(discontinued=True)) is None


def test_discontinued_kept_on_request():
    assert build(siz=size(discontinued=True), include_discontinued=True) is not None


def test_traits_map_to_spoolman_enums():
    assert build(var=variant(traits={"matte": True}))["finish"] == "matte"
    assert build(var=variant(traits={"matte": False}))["finish"] is None
    assert build(var=variant(traits={"imitates_marble": True}))["pattern"] == "marble"
    assert build(var=variant(traits={"glitter": True}))["pattern"] == "sparkle"
    both = {"glitter": True, "imitates_marble": True}
    assert build(var=variant(traits=both))["pattern"] == "marble"
    assert build(var=variant(traits={"translucent": True}))["translucent"] is True
    assert build(var=variant(traits={"transparent": True}))["translucent"] is True
    assert build(var=variant(traits={"glow": True}))["glow"] is True
    # Traits Spoolman has no equivalent for are simply not represented.
    assert build(var=variant(traits={"silk": True}))["finish"] is None


# ---------------------------------------------------------------------------
# materials.json
# ---------------------------------------------------------------------------


def test_materials_are_medians_of_ofd_filaments():
    filaments = [
        filament(id="a", density=1.20, min_print_temperature=200, max_print_temperature=200),
        filament(id="b", density=1.24, min_print_temperature=210, max_print_temperature=210),
        filament(id="c", density=1.30, min_print_temperature=220, max_print_temperature=220),
        filament(
            id="d", material="PETG", density=1.27, min_print_temperature=0, max_print_temperature=0
        ),
    ]
    materials = build_materials(filaments)
    assert [m["material"] for m in materials] == ["PETG", "PLA"]
    pla = materials[1]
    assert pla["density"] == 1.24
    assert pla["extruder_temp"] == 210
    assert pla["bed_temp"] == 55
    # A material with no temperatures anywhere gets null, not a made-up default.
    assert materials[0]["extruder_temp"] is None
    assert materials[0]["bed_temp"] == 55


def test_materials_empty():
    assert build_materials([]) == []
