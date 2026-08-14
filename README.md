# F5 Icons for draw.io

Converts the official [F5 brand icon set][f5brand] into
[draw.io](https://www.drawio.com/) shape libraries — 802 icons across 13
themed palettes, 797 of them as true recolorable vector stencils.

> **Attribution.** The icon artwork is the property of F5, Inc. and comes from
> the F5 brand portal: <https://brand.f5.com/document/186>. This project is a
> community conversion tool, not affiliated with, endorsed by, or sponsored by
> F5. The original SVG files are not committed here, but the generated
> libraries in `libraries/` do embed F5's icon geometry and are therefore
> derivative works — use them under F5's brand terms, not under this repo's
> MIT license. See [Licensing](#licensing).

## Why stencils and not images

Most icon-to-draw.io converters embed each SVG as a base64 image. That works,
but the result is inert: you cannot restyle it, and it does not respond to
draw.io's fill picker.

This converter parses the SVG geometry and emits real `mxGraph` stencils
instead. The practical difference:

| | Stencil | Embedded image |
| --- | --- | --- |
| Recolor with draw.io's fill picker | Yes | No |
| Scales without artefacts | Yes | Yes |
| Respects diagram themes | Yes | No |
| Editable outline / stroke | Yes | No |

797 of the 802 icons convert to stencils. The remaining 5 use SVG features
that have no stencil equivalent (a non-`none` stroke, or partial opacity) and
fall back to embedded images so they still look exactly right:

- AI Finance 2
- Hybrid Multicloud Complexity
- Multi Device Compatibility
- Blog
- Megaphone

## The libraries

The vendor ships one flat folder of ~800 SVGs. Six coarse prefixes are usable
as palettes as-is; `other-` is a 319-icon grab bag that is impossible to scan,
and two prefixes are too small to deserve their own palette. This repo
regroups them into 13 libraries of a browsable size:

| Library | Icons | Contents |
| --- | --- | --- |
| `f5-security.xml` | 130 | WAF, bot defence, DDoS, API security, zero trust, threat actors |
| `f5-xops.xml` | 105 | DevOps, SecOps, NetOps, observability, pipelines, orchestration |
| `f5-deployment.xml` | 91 | Cloud, multicloud, data centre, hardware and virtual form factors |
| `f5-delivery.xml` | 85 | Load balancing, DNS, traffic management, Kubernetes, platform value |
| `f5-people.xml` | 81 | User roles, personas, support, training, professional services |
| `f5-ai.xml` | 64 | AI factories, gateways, guardrails, inference, model operations |
| `f5-devices.xml` | 51 | Laptops, desktops, phones, tablets, wearables, endpoints |
| `f5-business.xml` | 48 | Industry verticals, currency, commerce, buildings, transport |
| `f5-symbols.xml` | 37 | Status marks, arrows, metaphors, general diagram symbols |
| `f5-docs.xml` | 36 | Documents, guides, files, messaging, email, time, notifications |
| `f5-apps.xml` | 33 | Application lifecycle, tiers, modernisation, migration, code |
| `f5-data.xml` | 21 | Graphs, dashboards, databases, metrics, targets, planning |
| `f5-network.xml` | 20 | Wi-Fi, cellular, addressing, satellite, fibre, internet reach |
| `f5.xml` | 802 | Everything, in one library |

Load the themed libraries you need rather than `f5.xml` — an 802-shape palette
is slow to scan and slow to search.

## Installing a library

The generated files are draw.io shape libraries (`.xml`). In either the
desktop app or <https://app.diagrams.net>:

1. **File → Open Library from → Device…**
2. Pick one or more `.xml` files from `libraries/`.

Each library appears as its own collapsible section in the left-hand shape
panel and persists across sessions.

To load straight from a URL instead, use **File → Open Library from → URL…**
and paste the raw file address:

```text
https://raw.githubusercontent.com/TailwindRG/f5-icons/main/libraries/f5-security.xml
```

### Recoloring

Select a placed stencil and use the **Fill** colour in the Style panel. The
icons are generated black (`#000000`) so they inherit cleanly. To bake a
different default into the libraries — F5 red, for instance — pass `--fill`
at build time:

```bash
python3 scripts/svg2drawio.py build/staged -o libraries --name f5 \
    --per-category --fill "#E4002B"
```

## Building from source

Pre-built libraries are in `libraries/`, so you only need this if you want to
change the grouping, the naming, the default fill, or to pick up a newer icon
release from F5. The original SVG export is not committed — you supply it.

### Prerequisites

- Python 3.9 or newer. No third-party packages — the scripts are standard
  library only.

### Steps

1. Download the icon set from the F5 brand portal:
   <https://brand.f5.com/document/186>. Take the SVG export.

2. Unpack it under `source/` (the directory is gitignored). You should end up
   with a folder of flat `*.svg` files, e.g.
   `source/Icons 2026-08-14 10_43_49/`.

3. Stage the icons into groups. This rewrites each filename's vendor prefix
   according to `scripts/taxonomy.json` and copies the result to
   `build/staged/`:

   ```bash
   python3 scripts/organize.py "source/Icons 2026-08-14 10_43_49" -o build/staged
   ```

4. Build the libraries:

   ```bash
   python3 scripts/svg2drawio.py build/staged -o libraries --name f5 --per-category
   ```

You now have `libraries/f5.xml` plus one file per group.

### Inspecting before you commit

Both scripts take `--report`, which prints what they would do and writes
nothing:

```bash
# Which group does each icon land in?
python3 scripts/organize.py "source/Icons 2026-08-14 10_43_49" --report

# What will each shape be called, and will it be a stencil or an image?
python3 scripts/svg2drawio.py build/staged --report
```

## Scripts

| File | Purpose |
| --- | --- |
| `scripts/organize.py` | Regroups the flat vendor export using `taxonomy.json` |
| `scripts/taxonomy.json` | The group definitions and filename-matching rules |
| `scripts/svg2drawio.py` | Converts staged SVGs into draw.io shape libraries |
| `scripts/svgpath.py` | SVG path parser and `mxStencil` path emitter |

### `svg2drawio.py` options

| Flag | Default | Effect |
| --- | --- | --- |
| `-o`, `--out` | `./libraries` | Output directory |
| `--name` | `icons` | Base filename for generated libraries |
| `--size` | `64` | Longest edge of each shape, in points |
| `--fill` | `#000000` | Default `fillColor` for stencil shapes |
| `--per-category` | off | Also emit one library per detected group |
| `--min-category` | `3` | How often a prefix must repeat to count as a group |
| `--precision` | `2` | Decimal places for stencil coordinates |
| `--force-image` | off | Skip stencil conversion, embed everything as images |
| `--include-raster` | off | Also ingest PNG/JPG/GIF assets |
| `--no-compress` | off | Emit uncompressed mxGraph XML (large, but readable) |
| `--report` | off | Print names and conversion modes, write nothing |

## Adjusting the grouping

Group membership is data, not code. Edit `scripts/taxonomy.json` and re-run
`organize.py`.

Each group entry looks like this:

```json
{
  "slug": "network",
  "display": "F5 Network & Connectivity",
  "description": "Wi-Fi, cellular, addressing, satellite, fibre and internet reach",
  "from": ["other"],
  "match": ["wifi", "^satellite$", "^ipv[46]$"]
}
```

- `from` — which vendor prefixes this group may claim from.
- `match` — Python regexes, case-insensitive, tested with `re.search` against
  the filename **with the vendor prefix already stripped**. So a rule for
  `other-user-admin.svg` is tested against `user-admin`.
- An empty `match` claims every icon carrying one of its `from` prefixes.

Groups are evaluated top to bottom and the first hit wins, so order your rules
from most specific to most general. Anything matching nothing lands in the
group named by the top-level `fallback` key. `organize.py --report` lists the
assignments and refuses to stage if an icon matches no group and no fallback
is set.

## Accuracy

Stencil geometry was checked by rebuilding the emitted paths and pixel-diffing
against the source SVG at 512 px: 1 differing pixel in 262,144 at the default
precision of 2. Raise `--precision` if you need tighter, at the cost of file
size.

## Licensing

The scripts and documentation in this repository are MIT licensed — see
[LICENSE](LICENSE).

The MIT grant does **not** extend to the F5 icon artwork, to any shape library
generated from it, or to the F5 name and logo. Those remain the property of
F5, Inc. and are governed by F5's brand guidelines at
<https://brand.f5.com/document/186>. Obtain the artwork from F5 and use it on
F5's terms.

[f5brand]: https://brand.f5.com/document/186
