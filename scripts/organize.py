#!/usr/bin/env python3
"""
organize.py - Stage the flat F5 icon export into draw.io library groups.

The vendor ships one directory of ~800 SVGs whose filenames carry a coarse
prefix: ai-, delivery-, deployment-, industry-, other-, platformadsp-,
security-, xops-. Six of those are usable palettes as-is. `other-` is a
319-icon grab bag that nobody can scan, and two of them are too small to
deserve their own palette.

This script rewrites the prefixes according to taxonomy.json and copies the
result into a staging directory. svg2drawio.py then picks the new prefixes up
as categories with no changes of its own:

    python3 scripts/organize.py "source/Icons ..." -o build/staged
    python3 scripts/svg2drawio.py build/staged -o libraries --name f5 \\
        --per-category

Standard library only.

Usage:
    python3 organize.py <src-dir-or-zip> -o build/staged
    python3 organize.py <src-dir-or-zip> --report     # assignments only
"""

import argparse
import json
import re
import shutil
import sys
import zipfile
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_TAXONOMY = HERE / "taxonomy.json"


def load_taxonomy(path: Path):
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    groups = data["groups"]
    for g in groups:
        g["_rx"] = [re.compile(p, re.I) for p in g.get("match", [])]
    fallback = data.get("fallback")
    slugs = {g["slug"] for g in groups}
    if fallback and fallback not in slugs:
        sys.exit(f"error: fallback '{fallback}' is not a declared group")
    return groups, fallback


def collect(src: Path):
    """Return [(stem, suffix, bytes)] for every SVG in a directory or zip."""
    out = []
    if src.is_file() and src.suffix.lower() == ".zip":
        with zipfile.ZipFile(src) as z:
            for info in z.infolist():
                p = Path(info.filename)
                if (info.is_dir() or p.name.startswith("._")
                        or p.suffix.lower() != ".svg"
                        or "__MACOSX" in p.parts):
                    continue
                out.append((p.stem, p.suffix.lower(), z.read(info)))
    elif src.is_dir():
        for p in sorted(src.rglob("*.svg")):
            if p.name.startswith("._"):
                continue
            out.append((p.stem, p.suffix.lower(), p.read_bytes()))
    else:
        sys.exit(f"error: {src} is not a directory or .zip")
    return sorted(out)


def split_prefix(stem: str):
    """('other-user-admin') -> ('other', 'user-admin'); no dash -> (None, stem)."""
    head, _, rest = stem.partition("-")
    return (head.lower(), rest) if rest else (None, stem)


def assign(stem: str, groups, fallback):
    """Return (group_slug, remainder) for one icon, or (None, remainder)."""
    vendor, rest = split_prefix(stem)
    for g in groups:
        sources = [s.lower() for s in g.get("from", [])]
        if sources and vendor not in sources:
            continue
        rx = g["_rx"]
        if not rx:
            return g["slug"], rest
        if any(r.search(rest) for r in rx):
            return g["slug"], rest
    return fallback, rest


def main():
    ap = argparse.ArgumentParser(
        description="Re-prefix F5 icon SVGs into draw.io library groups.")
    ap.add_argument("source", type=Path,
                    help="Vendor icon directory or .zip")
    ap.add_argument("-o", "--out", type=Path, default=Path("build/staged"))
    ap.add_argument("--taxonomy", type=Path, default=DEFAULT_TAXONOMY)
    ap.add_argument("--report", action="store_true",
                    help="Print the assignment table, write nothing")
    args = ap.parse_args()

    groups, fallback = load_taxonomy(args.taxonomy)
    assets = collect(args.source)
    if not assets:
        sys.exit("error: no SVGs found")

    display = {g["slug"]: g.get("display", g["slug"]) for g in groups}
    rows, tally, unmatched = [], Counter(), []
    for stem, suffix, raw in assets:
        slug, rest = assign(stem, groups, fallback)
        if slug is None:
            unmatched.append(stem)
            continue
        tally[slug] += 1
        rows.append((slug, f"{slug}-{rest}{suffix}", stem, raw))

    if args.report:
        for slug, newname, stem, _ in rows:
            print(f"{slug:<14} {newname:<52} {stem}")
        print()
        for slug, n in sorted(tally.items(), key=lambda kv: -kv[1]):
            print(f"{n:>4}  {slug:<14} {display.get(slug, '')}")
        print(f"\n{len(rows)} icons in {len(tally)} groups")
        if unmatched:
            print(f"\nunmatched ({len(unmatched)}):", file=sys.stderr)
            for s in unmatched:
                print(f"  {s}", file=sys.stderr)
        return

    if unmatched:
        sys.exit(f"error: {len(unmatched)} icons matched no group and no "
                 f"fallback is set; first: {unmatched[0]}")

    if args.out.exists():
        shutil.rmtree(args.out)
    args.out.mkdir(parents=True)

    collisions = Counter(n for _, n, _, _ in rows)
    dupes = [n for n, c in collisions.items() if c > 1]
    if dupes:
        sys.exit(f"error: staged filename collision: {dupes[:5]}")

    for _, newname, _, raw in rows:
        (args.out / newname).write_bytes(raw)

    for slug, n in sorted(tally.items(), key=lambda kv: -kv[1]):
        print(f"{n:>4}  {slug:<14} {display.get(slug, '')}")
    print(f"\nstaged {len(rows)} icons in {len(tally)} groups -> {args.out}")


if __name__ == "__main__":
    main()
