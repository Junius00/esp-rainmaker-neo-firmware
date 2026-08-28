# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""The mapping language's enumerated vocabulary and its numeric limits.

Every name here is data the mapping JSON may use. A consumer that lowers the mapping into
its own representation keeps its own table from these names to whatever it emits (the
firmware generator maps them to its own enum symbols) and asserts the two agree, so a primitive
added here fails the consumer's sync rather than its build.
"""

import re

# Transform kinds. Adding one is a code change for every consumer (see the README's
# data-vs-code table), which is why the set is closed.
XFORM_TYPES = frozenset(
    {"identity", "linear", "kelvin_mireds", "enum_map", "string", "scale"}
)

# Matter-domain value types: the wire type of a mapped attribute, and the TLV width of a
# write_as command argument. "none" is the zero value, for unset optional slots.
VAL_TYPES = frozenset(
    {
        "none",
        "bool",
        "u8",
        "u16",
        "u32",
        "i8",
        "i16",
        "i32",
        "i64",
        "enum8",
        "nullable_u8",
        "nullable_u16",
        "nullable_i64",
        "string",
    }
)

# The subset a write_as command argument may use: TLV-encodable widths only
ARG_TYPES = frozenset({"u8", "u16", "u32", "i8", "i16", "i32", "bool"})

# The subset a numeric attribute may use (strings take a consumer's separate string path)
ATTR_TYPES = VAL_TYPES - {"none", "string"}

# Capability keys that carry commands
CMD_KEYS = ("write_as", "seed_as")

# Composite conversions are enumerated (no scripting in the mapping language); the
# semantics live in each consumer's engine, keyed by this name.
COMPOSITE_CONVERSIONS = frozenset({"hsv_xy"})

# How a StartUp* attribute's stored value becomes the boot state. Enumerated for the
# same reason as composite conversions.
STARTUP_POLICIES = frozenset({"passthrough", "min_clamp", "toggle_previous"})

# Numeric limits of the mapping language. Every value crossing a transform is a 32-bit signed
# integer, and the arithmetic is spelled out rather than left to a host language - truncating
# division, halves away from zero - which the golden vectors pin for every port (see
# scripts/reference/). A consumer whose own representation is narrower checks that itself,
# through validate(extra_checks=...).

U32_MAX = 0xFFFFFFFF
I32_MIN = -(1 << 31)
I32_MAX = (1 << 31) - 1

# Placeholder for the transformed Matter-domain value in a write_as argument
ARG_VALUE_PLACEHOLDER = "$value"

ARG_INDEXED_PLACEHOLDER = re.compile(r"^\$value\[(\d+)\]$")

# The three library sections, and the field naming each entry within them
LIBRARY_SECTIONS = (
    ("capabilities", "capability"),
    ("composites", "id"),
    ("device_types", "device_type"),
)

LIBRARY_KEYS = {"mapping_version", "description"} | {s for s, _ in LIBRARY_SECTIONS}

PROFILE_KEYS = {
    "profile_version",
    "name",
    "description",
    "device_types",
    "capabilities",
    "exclude_capabilities",
}


def parse_hex(value):
    """Parse a JSON hex-string id (e.g. "0x0300") into an int.

    Matter ids are 32-bit: a manufacturer extension carries the vendor in the upper
    half, so the tables keep the full width rather than assuming standard ids.
    """
    if not isinstance(value, str) or not value.startswith("0x"):
        raise ValueError(f"expected hex string like '0x0300', got {value!r}")
    parsed = int(value, 16)
    if not 0 <= parsed <= U32_MAX:
        raise ValueError(f"id {value} does not fit a 32-bit Matter id")
    return parsed



def check_i32(where, what, value, errors):
    """Transform arithmetic is 32-bit signed end to end for every consumer, so a mapping constant
    must fit that. The golden vectors pin the semantics; see scripts/reference/."""
    if not isinstance(value, int):
        return
    if not I32_MIN <= value <= I32_MAX:
        errors.append(f"{where}: {what} {value} does not fit an int32")
