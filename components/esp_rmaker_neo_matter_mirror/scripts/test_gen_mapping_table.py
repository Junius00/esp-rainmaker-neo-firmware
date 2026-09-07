# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for the mapping generator's library merging and profile resolution."""

import copy
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from gen_mapping_table import (  # noqa: E402
    STRUCTS,
    TYPES_HEADER,
    _attr_match,
    emit_cluster_check,
    emit_header,
    emit_source,
    load_library,
    main,
    port_clusters,
    resolve,
    vocabulary_macros,
    validate,
)

LIB = Path(__file__).parent.parent / "mapping" / "lib" / "rmng_matter_mapping.json"
PROFILES = Path(__file__).parent.parent / "mapping" / "profiles"


@pytest.fixture
def library():
    lib, errors = load_library([str(LIB)])
    assert errors == []
    return lib


def names(entries, key):
    return [e[key] for e in entries]


def write(tmp_path, name, doc):
    path = tmp_path / name
    path.write_text(json.dumps(doc), encoding="utf-8")
    return str(path)


# Library merging ##############################################################


def test_shipped_library_validates(library):
    assert validate(library) == []
    assert library["mapping_version"]


def test_library_files_merge(tmp_path, library):
    extra = write(
        tmp_path,
        "vendor.json",
        {"device_types": [{"device_type": "esp.device.vendor", "rules": []}]},
    )
    merged, errors = load_library([str(LIB), extra])
    assert errors == []
    assert "esp.device.vendor" in names(merged["device_types"], "device_type")
    assert merged["mapping_version"] == library["mapping_version"]


def test_duplicate_entry_across_libraries_is_an_error(tmp_path, library):
    dup = write(tmp_path, "dup.json", {"capabilities": [library["capabilities"][0]]})
    _, errors = load_library([str(LIB), dup])
    assert any("already defined" in e for e in errors)


def test_disagreeing_mapping_version_is_an_error(tmp_path):
    other = write(tmp_path, "other.json", {"mapping_version": "9.9"})
    _, errors = load_library([str(LIB), other])
    assert any("disagrees" in e for e in errors)


def test_unknown_library_key_is_an_error(tmp_path):
    bad = write(tmp_path, "bad.json", {"mapping_version": "1.0", "caps": []})
    _, errors = load_library([bad])
    assert any("unknown library keys" in e for e in errors)


def test_missing_mapping_version_is_an_error(tmp_path):
    bad = write(tmp_path, "bad.json", {"capabilities": []})
    _, errors = load_library([bad])
    assert any("mapping_version" in e for e in errors)


# Profile resolution ###########################################################


def test_star_keeps_every_device_type(library):
    mapping, errors = resolve(library, {"name": "all", "device_types": "*"})
    assert errors == []
    assert mapping["device_types"] == library["device_types"]


def test_shipped_profiles_resolve_and_validate(library):
    for path in sorted(PROFILES.glob("*.json")):
        profile = json.loads(path.read_text(encoding="utf-8"))
        mapping, errors = resolve(library, profile)
        assert errors == [], f"{path.name}: {errors}"
        assert validate(mapping) == [], path.name


def test_unreferenced_capability_is_dropped(library):
    lib = dict(library)
    lib["capabilities"] = library["capabilities"] + [
        {
            "capability": "esp.param.unused",
            "matter_match": {
                "cluster": "0x0402",
                "attribute": "0x0000",
                "value_type": "i16",
            },
            "transform": {"type": "identity"},
        }
    ]
    mapping, errors = resolve(lib, {"name": "all", "device_types": "*"})
    assert errors == []
    assert "esp.param.unused" not in names(mapping["capabilities"], "capability")


def test_node_scoped_capability_survives_any_profile(library):
    node_scoped = [
        c["capability"]
        for c in library["capabilities"]
        if _attr_match(c).get("node_scoped")
    ]
    mapping, errors = resolve(library, {"name": "empty", "device_types": []})
    assert errors == []
    assert names(mapping["capabilities"], "capability") == node_scoped


def test_matter_match_must_be_a_location(library):
    lib = copy.deepcopy(library)
    lib["capabilities"][0]["matter_match"] = []
    assert any("must be a Matter location" in e for e in validate(lib))


def test_matter_match_must_parse(library):
    lib = copy.deepcopy(library)
    _attr_match(lib["capabilities"][0])["cluster"] = "0041"
    assert any("bad matter_match" in e for e in validate(lib))


def test_transform_constants_must_fit_an_int32(library):
    lib = copy.deepcopy(library)
    cap = next(c for c in lib["capabilities"] if c["transform"]["type"] == "linear")
    cap["transform"]["out_max"] = 1 << 31
    assert any("does not fit an int32" in e for e in validate(lib))


def test_ids_must_fit_a_matter_32_bit_id(library):
    lib = copy.deepcopy(library)
    _attr_match(lib["capabilities"][0])["cluster"] = "0x1FFFFFFFF"
    assert any("32-bit Matter id" in e for e in validate(lib))


def test_list_lengths_must_fit_a_uint8(library):
    lib = copy.deepcopy(library)
    cap = next(c for c in lib["capabilities"] if c["transform"]["type"] == "enum_map")
    cap["transform"]["map"] = [{"rmng": i, "matter": i} for i in range(300)]
    assert any("more than 255" in e for e in validate(lib))


def test_composite_dropped_when_a_member_is(library):
    hue = "esp.param.hue"
    assert any(hue in c["params"] for c in library["composites"])
    mapping, _ = resolve(
        library,
        {"name": "x", "device_types": "*", "exclude_capabilities": [hue]},
    )
    assert mapping["composites"] == []


def test_unknown_device_type_is_an_error(library):
    _, errors = resolve(library, {"name": "x", "device_types": ["esp.device.nope"]})
    assert any("not in the library" in e for e in errors)


def test_unknown_profile_key_is_an_error(library):
    _, errors = resolve(library, {"name": "x", "devicetypes": []})
    assert any("unknown profile keys" in e for e in errors)


def test_device_types_must_be_a_list_or_star(library):
    _, errors = resolve(library, {"name": "x", "device_types": "esp.device.lightbulb"})
    assert any("must be a list" in e for e in errors)


def test_excluding_a_required_param_is_an_error(library):
    _, errors = resolve(
        library,
        {
            "name": "x",
            "device_types": "*",
            "exclude_capabilities": ["esp.param.power"],
        },
    )
    assert any("required by a kept device rule" in e for e in errors)


def test_excluding_an_optional_param_clears_it_from_the_rules(library):
    optional = {
        p
        for entry in library["device_types"]
        for rule in entry["rules"]
        for p in rule.get("optional_params", [])
    }
    victim = sorted(optional)[0]
    mapping, errors = resolve(
        library,
        {"name": "x", "device_types": "*", "exclude_capabilities": [victim]},
    )
    assert errors == []
    assert validate(mapping) == []
    assert victim not in names(mapping["capabilities"], "capability")
    for entry in mapping["device_types"]:
        for rule in entry["rules"]:
            assert victim not in rule.get("optional_params", [])


def test_resolve_does_not_mutate_the_library(library):
    before = json.dumps(library, sort_keys=True)
    optional = next(
        p
        for entry in library["device_types"]
        for rule in entry["rules"]
        for p in rule.get("optional_params", [])
    )
    resolve(
        library,
        {"name": "x", "device_types": "*", "exclude_capabilities": [optional]},
    )
    assert json.dumps(library, sort_keys=True) == before


def test_extra_capability_is_kept(library):
    victim = "esp.param.light-mode"
    mapping, errors = resolve(
        library, {"name": "x", "device_types": [], "capabilities": [victim]}
    )
    assert errors == []
    assert victim in names(mapping["capabilities"], "capability")


def test_unknown_capability_name_is_an_error(library):
    _, errors = resolve(
        library, {"name": "x", "device_types": "*", "capabilities": ["esp.param.nope"]}
    )
    assert any("capability not in the library" in e for e in errors)


# Port cluster set #############################################################


def test_port_clusters_covers_capabilities_composites_and_mandatory(library):
    mapping, _ = resolve(library, {"name": "all", "device_types": "*"})
    clusters = port_clusters(mapping)
    assert 0x0006 in clusters  # esp.param.power
    assert 0x0003 in clusters  # mandatory cluster, no bound param
    assert clusters[0x0300] == 0x19  # HS | XY | CT: capabilities plus the composite


def test_node_scoped_cluster_is_not_the_ports_job(library):
    """BasicInformation lives on endpoint 0, which the stack builds, not the factory."""
    mapping, _ = resolve(library, {"name": "all", "device_types": "*"})
    node_scoped = [
        c for c in library["capabilities"] if _attr_match(c).get("node_scoped")
    ]
    assert node_scoped, "expected a node-scoped capability in the library"
    for cap in node_scoped:
        assert int(_attr_match(cap)["cluster"], 16) not in port_clusters(mapping)


def test_cluster_check_errors_on_every_used_cluster(library):
    mapping, _ = resolve(library, {"name": "all", "device_types": "*"})
    check = emit_cluster_check(mapping, "test", allow_external=False)
    for cid in port_clusters(mapping):
        assert f"#if !defined(RM_MIRROR_PORT_HAS_CLUSTER_{cid:04X})" in check
    assert check.count("#error") == len(port_clusters(mapping))


def _lib_with_extra_device_type(library, entry):
    """The library plus a metering-plug-shaped rule carrying @p entry."""
    lib = dict(library)
    lib["device_types"] = library["device_types"] + [
        {
            "device_type": "esp.device.plug",
            "rules": [
                {
                    "params": ["esp.param.power"],
                    "matter_device_type": "0x010A",
                    "matter_device_type_version": 4,
                    "mandatory_clusters": ["0x0003"],
                    "additional_device_types": [entry],
                }
            ],
        }
    ]
    return lib


ELECTRICAL_SENSOR = {
    "id": "0x0510",
    "version": 1,
    "mandatory_clusters": ["0x009C"],
}


def test_additional_device_type_clusters_reach_the_port(library):
    """A cluster mandated by an additional device type still needs a factory."""
    lib = _lib_with_extra_device_type(library, ELECTRICAL_SENSOR)
    mapping, errors = resolve(
        lib, {"name": "plug", "device_types": ["esp.device.plug"]}
    )
    assert errors == []
    assert validate(mapping) == []
    assert 0x009C in port_clusters(mapping)
    check = emit_cluster_check(mapping, "test", allow_external=False)
    assert "#if !defined(RM_MIRROR_PORT_HAS_CLUSTER_009C)" in check


def test_additional_device_type_needs_an_integer_version(library):
    lib = _lib_with_extra_device_type(library, {"id": "0x0510"})
    mapping, _ = resolve(lib, {"name": "plug", "device_types": ["esp.device.plug"]})
    assert any("integer version" in e for e in validate(mapping))


def test_additional_device_type_id_must_be_hex(library):
    lib = _lib_with_extra_device_type(library, {"id": "1296", "version": 1})
    mapping, _ = resolve(lib, {"name": "plug", "device_types": ["esp.device.plug"]})
    assert any("additional_device_types" in e for e in validate(mapping))


def test_cluster_check_is_inert_when_external_clusters_are_allowed(library):
    mapping, _ = resolve(library, {"name": "all", "device_types": "*"})
    assert "#error" not in emit_cluster_check(mapping, "test", allow_external=True)


# Engine vocabulary ############################################################


def test_vocabulary_macros_cover_the_whole_mapping(library):
    mapping, _ = resolve(library, {"name": "all", "device_types": "*"})
    macros = vocabulary_macros(mapping)
    assert "RM_MIRROR_USES_XFORM_KELVIN_MIREDS" in macros  # esp.param.cct
    assert "RM_MIRROR_USES_XFORM_ENUM_MAP" in macros  # esp.param.light-mode
    assert "RM_MIRROR_USES_COMPOSITES" in macros
    assert "RM_MIRROR_USES_CONV_HSV_XY" in macros
    assert "RM_MIRROR_USES_STARTUP_MIN_CLAMP" in macros  # esp.param.brightness


def test_vocabulary_shrinks_with_the_profile(library):
    """A power-only device carries no colour maths, mireds or enum tables."""
    lib = dict(library)
    lib["device_types"] = library["device_types"] + [
        {
            "device_type": "esp.device.switch",
            "rules": [
                {
                    "params": ["esp.param.power"],
                    "matter_device_type": "0x0100",
                    "matter_device_type_version": 3,
                    "mandatory_clusters": ["0x0003"],
                }
            ],
        }
    ]
    mapping, errors = resolve(
        lib, {"name": "onoff", "device_types": ["esp.device.switch"]}
    )
    assert errors == []
    macros = vocabulary_macros(mapping)
    for gone in (
        "RM_MIRROR_USES_XFORM_LINEAR",
        "RM_MIRROR_USES_XFORM_KELVIN_MIREDS",
        "RM_MIRROR_USES_XFORM_ENUM_MAP",
        "RM_MIRROR_USES_COMPOSITES",
        "RM_MIRROR_USES_CONV_HSV_XY",
        "RM_MIRROR_USES_STARTUP_MIN_CLAMP",
        "RM_MIRROR_USES_STARTUP_PASSTHROUGH",
    ):
        assert gone not in macros
    assert "RM_MIRROR_USES_STARTUP_TOGGLE_PREVIOUS" in macros  # esp.param.power


# End to end ###################################################################


def test_main_emits_both_files(tmp_path):
    out_c = tmp_path / "gen.c"
    out_h = tmp_path / "gen.h"
    rc = main(
        [
            "--lib",
            str(LIB),
            "--profile",
            str(PROFILES / "all.json"),
            "--output-c",
            str(out_c),
            "--output-h",
            str(out_h),
            "--output-check",
            str(tmp_path / "check.h"),
        ]
    )
    assert rc == 0
    assert "rm_mirror_capabilities[]" in out_c.read_text(encoding="utf-8")
    assert "rm_mirror_capability_t" in out_h.read_text(encoding="utf-8")


def test_main_fails_on_a_bad_profile(tmp_path):
    bad = write(tmp_path, "bad.json", {"name": "bad", "device_types": ["esp.device.x"]})
    rc = main(
        [
            "--lib",
            str(LIB),
            "--profile",
            bad,
            "--output-c",
            str(tmp_path / "gen.c"),
            "--output-h",
            str(tmp_path / "gen.h"),
            "--output-check",
            str(tmp_path / "check.h"),
        ]
    )
    assert rc == 1
    assert not (tmp_path / "gen.c").exists()


# seed_as #####################################################################


def test_seed_macro_follows_the_mapping(library):
    """Both branches are emitted, so the port never tests an undefined identifier."""
    assert "#define RM_MIRROR_SEEDS_SHADOWS 1" in emit_header(library, "test")
    bare = copy.deepcopy(library)
    for cap in bare["capabilities"]:
        cap.pop("seed_as", None)
    assert "#define RM_MIRROR_SEEDS_SHADOWS 0" in emit_header(bare, "test")


def test_seed_commands_emit_their_own_arrays(library):
    """A capability carrying both keys must not have its arg arrays collide."""
    source = emit_source(library, "test", "gen.h")
    assert "__seed_cmds_esp_param_hue[]" in source
    assert "__cmd_args_esp_param_hue_seed_0[]" in source
    assert "__cmd_args_esp_param_hue_0[]" in source  # write_as, distinct ident
    assert ".seed_cmds = __seed_cmds_esp_param_hue" in source
    assert ".args = __cmd_args_esp_param_hue_seed_0" in source


def test_seed_command_rejects_a_value_placeholder(library):
    """A seed runs once, with no param value to substitute."""
    bad = copy.deepcopy(library)
    cap = next(c for c in bad["capabilities"] if c["capability"] == "esp.param.hue")
    cap["seed_as"]["commands"][0]["args"][0]["value"] = "$value"
    errors = validate(bad)
    assert any("integer literals" in e for e in errors), errors


def test_seed_command_rejects_a_when_selector(library):
    bad = copy.deepcopy(library)
    cap = next(c for c in bad["capabilities"] if c["capability"] == "esp.param.hue")
    cap["seed_as"]["commands"][0]["when"] = 1
    errors = validate(bad)
    assert any("cannot carry 'when'" in e for e in errors), errors


def test_seed_command_needs_commands(library):
    bad = copy.deepcopy(library)
    cap = next(c for c in bad["capabilities"] if c["capability"] == "esp.param.hue")
    cap["seed_as"] = {"note": "empty"}
    errors = validate(bad)
    assert any("seed_as needs a non-empty commands list" in e for e in errors), errors


# Table descriptors ############################################################


def test_generated_header_asserts_every_declared_width(library):
    """The generator's assumed widths are checked against the hand-written types."""
    header = emit_header(library, "test")
    assert f'#include "{TYPES_HEADER}"' in header
    for struct in STRUCTS.values():
        for line in struct.assertions():
            assert line in header
    assert (
        "RM_MIRROR_STATIC_ASSERT(sizeof(((rm_mirror_capability_t *)0)->startup_min) == 4"
        in header
    )


def test_asserted_fields_exist_in_the_types_header():
    """Every field the generator fills is one rm_mirror_mapping.h declares."""
    types = (Path(__file__).parent.parent / "priv_include" / TYPES_HEADER).read_text()
    for struct in STRUCTS.values():
        body = types[
            types.index("typedef struct {", types.find(struct.c_name) - 4000) :
        ]
        body = body[: body.index(f"}} {struct.c_name};")]
        for name in struct.names():
            assert re.search(rf"\b{name}\b", body), f"{struct.c_name}.{name}"


def test_source_rows_cover_every_entry(library):
    source = emit_source(library, "test", "gen.h")
    for cap in library["capabilities"]:
        assert f'.param_type = "{cap["capability"]}",' in source
