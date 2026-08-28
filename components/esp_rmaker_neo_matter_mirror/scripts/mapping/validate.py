# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0

"""Validate a RainMaker Neo <-> Matter mapping library, and resolve it against a profile.

Nothing here knows how a consumer represents the mapping. The checks are the ones the JSON
Schema cannot express: cross-entry references, rule ordering, arity, and the numeric limits
of the mapping language. A consumer with limits of its own (the field widths of a generated
table, for one) passes them to validate() as extra_checks.
"""

import json
import sys

from .vocabulary import (
    ARG_INDEXED_PLACEHOLDER,
    ARG_TYPES,
    ARG_VALUE_PLACEHOLDER,
    ATTR_TYPES,
    CMD_KEYS,
    COMPOSITE_CONVERSIONS,
    LIBRARY_KEYS,
    LIBRARY_SECTIONS,
    PROFILE_KEYS,
    STARTUP_POLICIES,
    VAL_TYPES,
    XFORM_TYPES,
    check_i32,
    parse_hex,
)


def report(errors, prog):
    """Print @p errors to stderr, prefixed with the calling program's name. Returns 1, so a
    caller can `return report(...)` from main()."""
    for err in errors:
        print(f"{prog}: {err}", file=sys.stderr)
    return 1


def attr_match(cap):
    """The Matter location a capability maps to, empty when it declares none."""
    match = cap.get("matter_match")
    return match if isinstance(match, dict) else {}


def feature_map(match):
    """The cluster FeatureMap bits a matter_match asks for; absent means none."""
    return parse_hex(match.get("feature_map", "0x00"))


def bounds_attr(spec):
    """Parse a bounds_from source like 'attr:0x400B' into an attribute id."""
    if not spec.startswith("attr:"):
        raise ValueError(f"unsupported bounds_from source {spec!r}")
    return parse_hex(spec[len("attr:") :])


def _validate_commands(name, key, block, errors, n_values=None):
    """Shared shape checks for write_as and seed_as. A seed runs once, with no value to
    substitute and nothing to select on, so it takes neither 'when' nor a placeholder.
    A composite's write_as (n_values set) substitutes the conversion's i-th output with
    '$value[i]'; a scalar capability has the single '$value'."""
    seeds = key == "seed_as"
    commands = block.get("commands")
    if not isinstance(commands, list) or not commands:
        errors.append(f"{name}: {key} needs a non-empty commands list")
        return
    unconditional = 0
    for i, cmd in enumerate(commands):
        where = f"{name}: {key} command {i}"
        try:
            parse_hex(cmd["command"])
        except (KeyError, ValueError) as exc:
            errors.append(f"{where}: bad command id: {exc}")
        if "when" in cmd:
            if seeds:
                errors.append(f"{where}: a seed command cannot carry 'when'")
            elif not isinstance(cmd["when"], int):
                errors.append(f"{where}: 'when' must be an integer Matter-domain value")
        else:
            unconditional += 1
        args = cmd.get("args", [])
        if not isinstance(args, list):
            errors.append(f"{where}: args must be a list")
            continue
        fields = set()
        for arg in args:
            if not isinstance(arg.get("field"), int):
                errors.append(f"{where}: arg missing integer field id")
                continue
            if arg["field"] in fields:
                errors.append(f"{where}: duplicate field id {arg['field']}")
            fields.add(arg["field"])
            if arg.get("type") not in ARG_TYPES:
                errors.append(f"{where}: unknown arg type {arg.get('type')!r}")
            value = arg.get("value")
            if seeds:
                if not isinstance(value, int):
                    errors.append(
                        f"{where}: a seed command's args must be integer literals, "
                        f"got {value!r}"
                    )
            elif n_values is not None:
                idx = (
                    ARG_INDEXED_PLACEHOLDER.match(value)
                    if isinstance(value, str)
                    else None
                )
                if isinstance(value, int) or value == ARG_VALUE_PLACEHOLDER:
                    pass
                elif idx is None or int(idx.group(1)) >= n_values:
                    errors.append(
                        f"{where}: arg value must be an integer literal, "
                        f"'{ARG_VALUE_PLACEHOLDER}' or '$value[i]' with i < {n_values}, "
                        f"got {value!r}"
                    )
            elif not isinstance(value, int) and value != ARG_VALUE_PLACEHOLDER:
                errors.append(
                    f"{where}: arg value must be an integer literal or "
                    f"'{ARG_VALUE_PLACEHOLDER}', got {value!r}"
                )
    if not seeds and unconditional > 1:
        errors.append(f"{name}: write_as has more than one unconditional command")


def validate(mapping, extra_checks=()):
    """Semantic errors in @p mapping, as a list of strings; empty when it is sound.

    @note Each callable in @p extra_checks runs last as check(mapping, errors) and appends
    the consumer-specific errors its own representation implies.
    """
    errors = []
    cap_types = set()
    for cap in mapping.get("capabilities", []):
        name = cap.get("capability", "<missing>")
        for key in CMD_KEYS:
            if key in cap:
                _validate_commands(name, key, cap[key], errors)
        if name in cap_types:
            errors.append(f"duplicate capability entry: {name}")
        cap_types.add(name)
        matched_attr = None
        match = attr_match(cap)
        if not match:
            errors.append(f"{name}: matter_match must be a Matter location")
        else:
            try:
                parse_hex(match["cluster"])
                feature_map(match)
                matched_attr = parse_hex(match["attribute"])
            except (KeyError, ValueError) as exc:
                errors.append(f"{name}: bad matter_match: {exc}")
        xform = cap.get("transform", {}).get("type")
        if xform not in XFORM_TYPES:
            errors.append(f"{name}: unknown transform type {xform!r}")
        value_type = attr_match(cap).get("value_type")
        if value_type not in VAL_TYPES or value_type == "none":
            errors.append(
                f"{name}: matter_match needs a value_type, got {value_type!r}"
            )
        elif (value_type == "string") != (xform == "string"):
            errors.append(
                f"{name}: value_type 'string' and the string transform imply each other"
            )
        bounds = cap.get("bounds_from", {})
        if ("min" in bounds or "max" in bounds) and bounds.get(
            "value_type"
        ) not in ATTR_TYPES:
            errors.append(
                f"{name}: bounds_from needs a value_type, got {bounds.get('value_type')!r}"
            )
        for shadow in cap.get("shadow_attributes", []):
            try:
                attr = parse_hex(shadow["attribute"])
            except (KeyError, ValueError) as exc:
                errors.append(f"{name}: bad shadow_attributes entry: {exc}")
                continue
            if shadow.get("value_type") not in ATTR_TYPES:
                errors.append(
                    f"{name}: shadow attribute 0x{attr:04X} needs a value_type, "
                    f"got {shadow.get('value_type')!r}"
                )
            if attr == matched_attr:
                errors.append(f"{name}: shadow attribute repeats the matched attribute")
        if "startup" in cap:
            startup = cap["startup"]
            try:
                parse_hex(startup["attribute"])
            except (KeyError, ValueError) as exc:
                errors.append(f"{name}: bad startup attribute: {exc}")
            policy = startup.get("policy")
            if policy not in STARTUP_POLICIES:
                errors.append(f"{name}: unknown startup policy {policy!r}")
            if policy == "min_clamp" and not isinstance(startup.get("min"), int):
                errors.append(f"{name}: min_clamp startup policy missing integer min")
        if not isinstance(cap.get("deferred_persistence", False), bool):
            errors.append(f"{name}: deferred_persistence must be a boolean")
        if "report_as" in cap:
            kind = cap["report_as"].get("kind")
            if not isinstance(kind, str) or not kind:
                errors.append(f"{name}: report_as needs a non-empty kind")
            if "write_as" in cap:
                errors.append(
                    f"{name}: report_as is for values Matter does not expose as a "
                    f"writable attribute, so it cannot carry write_as commands"
                )
        if xform == "string":
            if not isinstance(cap["transform"].get("max_len"), int):
                errors.append(f"{name}: string transform missing integer max_len")
            check_i32(name, "string max_len", cap["transform"].get("max_len"), errors)
        default_id = cap.get("defaults", {}).get("id")
        if default_id is not None and (
            not isinstance(default_id, str) or not default_id.strip()
        ):
            errors.append(f"{name}: defaults.id must be a non-empty string")
        bounds = cap.get("defaults", {}).get("bounds")
        if bounds is not None:
            if not all(isinstance(bounds.get(k), int) for k in ("min", "max", "step")):
                errors.append(
                    f"{name}: defaults.bounds needs integer min, max and step"
                )
            elif bounds["min"] > bounds["max"] or bounds["step"] < 1:
                errors.append(f"{name}: defaults.bounds needs min <= max and step >= 1")
        if xform == "scale":
            factor = cap["transform"].get("factor")
            if not isinstance(factor, int) or factor == 0:
                errors.append(
                    f"{name}: scale transform needs a non-zero integer factor"
                )
            check_i32(name, "scale factor", factor, errors)
        if xform == "linear":
            for key in ("in_min", "in_max", "out_min", "out_max"):
                if not isinstance(cap["transform"].get(key), int):
                    errors.append(f"{name}: linear transform missing integer {key}")
                check_i32(name, f"linear {key}", cap["transform"].get(key), errors)
        if xform == "enum_map":
            pairs = cap["transform"].get("map", [])
            aliases = cap["transform"].get("matter_aliases", [])
            if not pairs:
                errors.append(f"{name}: enum_map transform with empty map")
            seen_rmng, seen_matter = set(), set()
            for pair in pairs + aliases:
                if not isinstance(pair.get("rmng"), int) or not isinstance(
                    pair.get("matter"), int
                ):
                    errors.append(f"{name}: enum_map pair members must be integers")
                check_i32(name, "enum_map rmng value", pair.get("rmng"), errors)
                check_i32(name, "enum_map matter value", pair.get("matter"), errors)
            for pair in pairs:
                if pair.get("rmng") in seen_rmng:
                    errors.append(f"{name}: duplicate rmng key in enum_map")
                if pair.get("matter") in seen_matter:
                    errors.append(f"{name}: duplicate matter key in enum_map")
                seen_rmng.add(pair.get("rmng"))
                seen_matter.add(pair.get("matter"))

    for comp in mapping.get("composites", []):
        cid = comp.get("id", "<missing>")
        conv = comp.get("conversion", {}).get("type")
        if conv not in COMPOSITE_CONVERSIONS:
            errors.append(f"composite {cid}: unknown conversion type {conv!r}")
        if not isinstance(comp.get("deferred_persistence", False), bool):
            errors.append(f"composite {cid}: deferred_persistence must be a boolean")
        params = comp.get("params", [])
        attrs = comp.get("matter_match", {}).get("attributes", [])
        value_types = comp.get("matter_match", {}).get("value_types", [])
        if len(value_types) != len(attrs) or any(
            v not in ATTR_TYPES for v in value_types
        ):
            errors.append(
                f"composite {cid}: matter_match needs one value_type per attribute"
            )
        unknown = set(params) - cap_types
        if unknown:
            errors.append(
                f"composite {cid}: params not in capabilities: {sorted(unknown)}"
            )
        if conv == "hsv_xy" and (len(params) != 2 or len(attrs) != 2):
            errors.append(
                f"composite {cid}: hsv_xy requires exactly 2 params (hue, saturation) "
                f"and 2 attributes (CurrentX, CurrentY)"
            )
        try:
            parse_hex(comp["matter_match"]["cluster"])
            for attr in attrs:
                parse_hex(attr)
            feature_map(comp["matter_match"])
        except (KeyError, ValueError) as exc:
            errors.append(f"composite {cid}: bad matter_match: {exc}")
        if "write_as" in comp:
            _validate_commands(
                f"composite {cid}", "write_as", comp["write_as"], errors, len(attrs)
            )

    for entry in mapping.get("device_types", []):
        dev = entry.get("device_type", "<missing>")
        rules = entry.get("rules", [])
        if not rules:
            errors.append(f"{dev}: no rules")
        dev_defaults = entry.get("defaults")
        if dev_defaults is not None:
            dev_id = dev_defaults.get("id")
            if not isinstance(dev_id, str) or not dev_id.strip():
                errors.append(f"{dev}: defaults.id must be a non-empty string")
            primary = dev_defaults.get("primary")
            if primary is not None and primary not in cap_types:
                errors.append(
                    f"{dev}: defaults.primary {primary!r} is not a capability"
                )
        for i, rule in enumerate(rules):
            params = set(rule.get("params", []))
            if not params:
                errors.append(f"{dev} rule {i}: empty params")
            unknown = (params | set(rule.get("optional_params", []))) - cap_types
            if unknown:
                errors.append(
                    f"{dev} rule {i}: params not in capabilities: {sorted(unknown)}"
                )
            try:
                parse_hex(rule["matter_device_type"])
            except (KeyError, ValueError) as exc:
                errors.append(f"{dev} rule {i}: bad matter_device_type: {exc}")
            for dt in rule.get("additional_device_types", []):
                try:
                    parse_hex(dt["id"])
                    for cluster in dt.get("mandatory_clusters", []):
                        parse_hex(cluster)
                except (KeyError, ValueError) as exc:
                    errors.append(
                        f"{dev} rule {i}: bad additional_device_types entry: {exc}"
                    )
                if not isinstance(dt.get("version"), int):
                    errors.append(
                        f"{dev} rule {i}: additional_device_types entry needs an integer version"
                    )
            # Most-specific-first: a later rule must not shadow-proof an earlier one
            for j in range(i):
                earlier = set(rules[j].get("params", []))
                if earlier <= params:
                    errors.append(
                        f"{dev} rule {i} is unreachable: its param set is a superset "
                        f"of earlier rule {j} (order rules most-specific-first)"
                    )
    for check in extra_checks:
        check(mapping, errors)
    return errors


def load_library(paths):
    """Merge the library files into one mapping, in the order given.

    Entries are keyed by capability / composite id / device type; a key defined
    twice is an error rather than a silent last-one-wins, so a product's own
    library file cannot quietly redefine the shared vocabulary.
    """
    merged = {section: [] for section, _ in LIBRARY_SECTIONS}
    seen = {section: {} for section, _ in LIBRARY_SECTIONS}
    version = None
    errors = []
    for path in paths:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        where = path.rsplit("/", 1)[-1]
        unknown = set(doc) - LIBRARY_KEYS
        if unknown:
            errors.append(f"{where}: unknown library keys {sorted(unknown)}")
        if "mapping_version" in doc:
            if version is not None and doc["mapping_version"] != version:
                errors.append(
                    f"{where}: mapping_version {doc['mapping_version']!r} disagrees "
                    f"with {version!r} from an earlier library file"
                )
            version = doc["mapping_version"]
        for section, id_key in LIBRARY_SECTIONS:
            for entry in doc.get(section, []):
                key = entry.get(id_key)
                if key in seen[section]:
                    errors.append(
                        f"{where}: {section} entry {key!r} already defined in "
                        f"{seen[section][key]}"
                    )
                    continue
                seen[section][key] = where
                merged[section].append(entry)
    if version is None:
        errors.append("no library file declares a mapping_version")
    merged["mapping_version"] = version
    return merged, errors


def _rule_without(rule, excluded):
    """Copy @p rule with the excluded capabilities dropped from optional_params."""
    optional = [p for p in rule.get("optional_params", []) if p not in excluded]
    out = dict(rule)
    if optional:
        out["optional_params"] = optional
    else:
        out.pop("optional_params", None)
    return out


def resolve(library, profile):
    """Restrict the library to what @p profile selects.

    Keeps a capability when it is (a) node-scoped, (b) referenced by a kept
    rule's params/optional_params, (c) targeting a cluster listed as mandatory
    by a kept rule (e.g. identify), or (d) named by the profile outright. A
    composite is kept when all its member params are. Everything else is
    dropped, which is what makes unused library entries cost no flash.
    """
    errors = []
    unknown = set(profile) - PROFILE_KEYS
    if unknown:
        errors.append(f"unknown profile keys {sorted(unknown)}")

    known_devices = {e.get("device_type") for e in library["device_types"]}
    wanted = profile.get("device_types", "*")
    if wanted == "*":
        kept_devices = list(library["device_types"])
    elif isinstance(wanted, list):
        missing = [d for d in wanted if d not in known_devices]
        if missing:
            errors.append(f"device types not in the library: {sorted(missing)}")
        kept_devices = [
            e for e in library["device_types"] if e.get("device_type") in set(wanted)
        ]
    else:
        errors.append('device_types must be a list or "*"')
        kept_devices = []

    required = set()
    referenced = set()
    mandatory_clusters = set()
    for entry in kept_devices:
        for rule in entry.get("rules", []):
            required.update(rule.get("params", []))
            referenced.update(rule.get("params", []))
            referenced.update(rule.get("optional_params", []))
            mandatory_clusters.update(
                parse_hex(c) for c in rule.get("mandatory_clusters", [])
            )
            for dt in rule.get("additional_device_types", []):
                mandatory_clusters.update(
                    parse_hex(c) for c in dt.get("mandatory_clusters", [])
                )

    known_caps = {c.get("capability") for c in library["capabilities"]}
    extra = profile.get("capabilities", [])
    excluded = set(profile.get("exclude_capabilities", []))
    for name in sorted(set(extra) | excluded):
        if name not in known_caps:
            errors.append(f"capability not in the library: {name}")
    # A rule cannot match without its required params, so excluding one would
    # leave the device type unmatchable. Optional params are fair game.
    for name in sorted(excluded & required):
        errors.append(
            f"{name} is required by a kept device rule and cannot be excluded"
        )
    referenced.update(extra)

    kept_caps = [
        c
        for c in library["capabilities"]
        if (
            attr_match(c).get("node_scoped", False)
            or c.get("capability") in referenced
            or parse_hex(attr_match(c)["cluster"]) in mandatory_clusters
        )
        and c.get("capability") not in excluded
    ]
    kept_names = {c.get("capability") for c in kept_caps}
    kept_composites = [
        c for c in library["composites"] if set(c.get("params", [])) <= kept_names
    ]
    # A rule must not gate-list a capability that is no longer compiled in, so
    # drop excluded names from optional_params (required ones errored above).
    if excluded:
        kept_devices = [
            dict(entry, rules=[_rule_without(r, excluded) for r in entry["rules"]])
            for entry in kept_devices
        ]

    dropped = (
        len(library["capabilities"])
        - len(kept_caps)
        + len(library["composites"])
        - len(kept_composites)
        + len(library["device_types"])
        - len(kept_devices)
    )
    if dropped and not errors:
        print(
            f"resolve: profile {profile.get('name', '?')!r} keeps "
            f"{len(kept_devices)} device type(s), {len(kept_caps)} capability(ies), "
            f"{len(kept_composites)} composite(s); {dropped} library entry(ies) dropped",
            file=sys.stderr,
        )

    return {
        "mapping_version": library["mapping_version"],
        "capabilities": kept_caps,
        "composites": kept_composites,
        "device_types": kept_devices,
    }, errors


def requires(mapping):
    """Enumerated primitives a consumer must implement to interpret @p mapping completely: the
    transform kinds and composite conversions it references. Consumers compare this against what
    they support (a generator's compiled-in set, a runtime interpreter's supported set) - a
    cloud-delivered JSON that needs more than a controller has is detected before use."""
    kinds = sorted({c["transform"]["type"] for c in mapping.get("capabilities", [])})
    convs = sorted({c["conversion"]["type"] for c in mapping.get("composites", [])})
    return {"transforms": kinds, "conversions": convs}
