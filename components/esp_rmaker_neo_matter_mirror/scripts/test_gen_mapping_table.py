# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for the C emitters: the tables, the port cross-check and the RM_MIRROR_USES_* gates.

The mapping language itself is tested in the canonical repo (esp-rainmaker-neo-matter-mapping);
what is left here is the lowering into C and the two seams with the hand-written port.
"""

import copy
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from gen_mapping_table import (  # noqa: E402
    ARG_TYPES,
    ATTR_TYPES,
    COMPOSITE_CONVERSIONS,
    CMD_KEYS,
    STARTUP_POLICIES,
    STRUCTS,
    TYPES_HEADER,
    VAL_TYPES,
    XFORM_TYPES,
    _check_widths,
    emit_cluster_check,
    emit_header,
    emit_source,
    main,
    port_clusters,
    vocabulary_macros,
)
from mapping import vocabulary  # noqa: E402
from mapping.validate import attr_match, load_library, resolve, validate  # noqa: E402

LIB = Path(__file__).parent.parent / "mapping" / "lib" / "rmng_matter_mapping.json"
PROFILES = Path(__file__).parent.parent / "mapping" / "profiles"
VECTORS = (
    Path(__file__).parent.parent
    / "mapping"
    / "vectors"
    / "rmng_matter_mapping.vectors.json"
)


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


# The committed copy ###########################################################


def test_shipped_library_validates(library):
    """The synced copy is sound, and every value fits the field this port gives it."""
    assert validate(library, [_check_widths]) == []
    assert library["mapping_version"]


def test_shipped_profiles_resolve_and_validate(library):
    for path in sorted(PROFILES.glob("*.json")):
        profile = json.loads(path.read_text(encoding="utf-8"))
        mapping, errors = resolve(library, profile)
        assert errors == [], f"{path.name}: {errors}"
        assert validate(mapping, [_check_widths]) == [], path.name


# The seam with the shared vocabulary ##########################################


def test_symbol_tables_cover_the_shared_vocabulary():
    """Every primitive the mapping language declares has a C symbol here. Importing the
    generator already asserts this; the test names what breaks when it does not."""
    assert set(XFORM_TYPES) == set(vocabulary.XFORM_TYPES)
    assert set(VAL_TYPES) == set(vocabulary.VAL_TYPES)
    assert set(ARG_TYPES) == set(vocabulary.ARG_TYPES)
    assert set(ATTR_TYPES) == set(vocabulary.ATTR_TYPES)
    assert set(COMPOSITE_CONVERSIONS) == set(vocabulary.COMPOSITE_CONVERSIONS)
    assert set(STARTUP_POLICIES) == set(vocabulary.STARTUP_POLICIES)
    assert {k for k, _p, _s in CMD_KEYS} == set(vocabulary.CMD_KEYS)


def test_list_lengths_must_fit_a_uint8(library):
    """The tables address every list with a uint8, so the vocabulary cannot outgrow one."""
    lib = copy.deepcopy(library)
    cap = next(c for c in lib["capabilities"] if c["transform"]["type"] == "enum_map")
    cap["transform"]["map"] = [{"rmng": i, "matter": i} for i in range(300)]
    assert any("more than 255" in e for e in validate(lib, [_check_widths]))


def test_field_widths_are_this_ports_check_alone(library):
    """A value too wide for a generated field is an error here and nowhere else: the shared
    validator knows nothing about this port's table layout."""
    lib = copy.deepcopy(library)
    lib["device_types"][0]["rules"][0]["matter_device_type_version"] = 300
    assert validate(lib) == []
    assert any("does not fit a uint8" in e for e in validate(lib, [_check_widths]))


# Port cross-check ############################################################


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
        c for c in library["capabilities"] if attr_match(c).get("node_scoped")
    ]
    assert node_scoped, "expected a node-scoped capability in the library"
    for cap in node_scoped:
        assert int(attr_match(cap)["cluster"], 16) not in port_clusters(mapping)


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


def test_cluster_check_is_inert_when_external_clusters_are_allowed(library):
    mapping, _ = resolve(library, {"name": "all", "device_types": "*"})
    assert "#error" not in emit_cluster_check(mapping, "test", allow_external=True)


# Vocabulary gates ############################################################


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


# Seed commands ###############################################################


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


# Table descriptors ###########################################################


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


# The command line ############################################################


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


# Golden vectors ##############################################################


def test_vectors_header_lowers_the_committed_json(tmp_path):
    """The header carries the committed vectors, not a fresh computation of them."""
    out = tmp_path / "vec.h"
    rc = main(
        [
            "--lib",
            str(LIB),
            "--vectors-in",
            str(VECTORS),
            "--output-vectors-c",
            str(out),
        ]
    )
    assert rc == 0
    header = out.read_text(encoding="utf-8")
    committed = json.loads(VECTORS.read_text(encoding="utf-8"))
    assert (
        f'#define RM_MIRROR_VECTORS_MAPPING_VERSION "{committed["mapping_version"]}"'
        in header
    )
    for t in committed["transforms"]:
        assert f'"{t["capability"]}", "{t["transform"]["type"]}"' in header
    first_in, first_out = committed["transforms"][0]["to_matter"][0]
    assert f"{{{first_in}, {first_out}}}" in header
