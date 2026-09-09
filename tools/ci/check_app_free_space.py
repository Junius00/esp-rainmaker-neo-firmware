#!/usr/bin/env python
#
# SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
#
# SPDX-License-Identifier: Apache-2.0
"""Gate one finished ESP-IDF build directory on free space in its app partition.

This enforces an absolute (and/or relative) floor so an example cannot silently
creep up to the partition boundary.

Free space is the smallest app-type partition minus the app binary, matching what
the build prints as "Smallest app partition is ... N bytes (X%) free."

Exits 0 when the build is over the floor, or when the directory carries no build
metadata / no app partition (nothing to check). Exits 1 on a violation.
"""

import argparse
import json
import os
import sys

sys.path.insert(
    0, os.path.join(os.environ.get("IDF_PATH", ""), "components", "partition_table")
)
import gen_esp32part  # noqa: E402


def smallest_app_partition(table_bin):
    """Size in bytes of the smallest app-type partition, or None if there is none."""
    gen_esp32part.quiet = True
    with open(table_bin, "rb") as f:
        table, _ = gen_esp32part.PartitionTable.from_file(f)
    sizes = [p.size for p in table if p.type == gen_esp32part.APP_TYPE]
    return min(sizes) if sizes else None


def append_csv(path, label, used, part, free, pct):
    header = not os.path.exists(path)
    with open(path, "a") as f:
        if header:
            f.write("label,app_bytes,partition_bytes,free_bytes,free_pct\n")
        f.write(f"{label},{used},{part},{free},{pct:.2f}\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("build_dir", help="finished ESP-IDF build directory")
    parser.add_argument(
        "--min-free-bytes", type=int, default=0, help="absolute floor; 0 disables"
    )
    parser.add_argument(
        "--min-free-pct", type=float, default=0.0, help="relative floor; 0 disables"
    )
    parser.add_argument(
        "--label", default="", help="name used in output and in the CSV row"
    )
    parser.add_argument("--csv", help="append one row per build to this file")
    args = parser.parse_args()

    label = args.label or os.path.basename(os.path.normpath(args.build_dir))
    desc = os.path.join(args.build_dir, "project_description.json")
    table = os.path.join(args.build_dir, "partition_table", "partition-table.bin")
    if not (os.path.isfile(desc) and os.path.isfile(table)):
        return 0

    with open(desc) as f:
        app_bin = os.path.join(
            args.build_dir, os.path.basename(json.load(f)["app_bin"])
        )
    if not os.path.isfile(app_bin):
        print(f"WARN: {label}: no app binary at {app_bin}; skipping free-space check")
        return 0

    part = smallest_app_partition(table)
    if part is None:
        print(
            f"WARN: {label}: partition table has no app partition; skipping free-space check"
        )
        return 0

    used = os.path.getsize(app_bin)
    free = part - used
    pct = 100.0 * free / part
    print(
        f"free-space: {label}: app {used} B, partition {part} B, free {free} B ({pct:.1f}%)"
    )
    if args.csv:
        append_csv(args.csv, label, used, part, free, pct)

    if free < args.min_free_bytes or pct < args.min_free_pct:
        print(
            f"ERROR: {label}: {free} B ({pct:.1f}%) free is below the required "
            f"{args.min_free_bytes} B / {args.min_free_pct}%",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
