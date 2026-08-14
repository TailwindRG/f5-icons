#!/usr/bin/env python3
"""
svg2drawio.py - Build draw.io shape libraries (.xml) from a folder or zip of SVGs.

Two conversion modes, chosen per icon automatically:

  stencil  Pure-geometry icons become true mxGraph stencils. They are real
           vectors: recolorable with draw.io's fill picker, restyleable, and
           resolution independent. Note they are NOT smaller on disk. Vendor
           SVG is exported with compact relative coordinates, while mxStencil
           needs verbose absolute-coordinate tags, so expect roughly 1.4x the
           size of the image form. You are buying editability, not bytes.

  image    Icons using gradients, masks, clip paths, live text, filters, or
           the even-odd fill rule are embedded as base64 SVG data URIs, which
           preserves them exactly at the cost of recolorability.

Verified against F5 brand SVGs by rebuilding geometry from the emitted stencil
and pixel-diffing against the source at 512px: 1 differing pixel in 262,144 at
the default precision of 2.

Requires svgpath.py alongside it. Standard library only.

Usage:
    python3 svg2drawio.py assets.zip -o ./libraries --name f5 --per-category
    python3 svg2drawio.py assets.zip --report          # inspect names first
"""

import argparse
import base64
import json
import math
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
import zlib
from collections import Counter
from pathlib import Path
from urllib.parse import quote
from xml.sax.saxutils import escape

from svgpath import parse_path, ops_to_stencil_path

RASTER_MIME = {".png": "image/png", ".jpg": "image/jpeg",
               ".jpeg": "image/jpeg", ".gif": "image/gif"}
SKIP_DIR_PARTS = {"__MACOSX", ".git", "node_modules"}

# Elements whose presence forces the image fallback.
UNSUPPORTED = {"lineargradient", "radialgradient", "pattern", "mask",
               "clippath", "filter", "text", "tspan", "image", "use",
               "foreignobject", "style", "switch", "marker", "symbol"}

SHAPE_TAGS = {"path", "rect", "circle", "ellipse", "polygon", "polyline", "line"}
CONTAINER_TAGS = {"g", "svg", "defs", "title", "desc", "metadata"}

ACRONYMS = {
    "f5": "F5", "bigip": "BIG-IP", "nginx": "NGINX", "waf": "WAF", "ltm": "LTM",
    "gtm": "GTM", "apm": "APM", "asm": "ASM", "afm": "AFM", "dns": "DNS",
    "ssl": "SSL", "tls": "TLS", "vpn": "VPN", "api": "API", "apis": "APIs",
    "aws": "AWS", "gcp": "GCP", "cdn": "CDN", "ddos": "DDoS", "xc": "XC",
    "saas": "SaaS", "iot": "IoT", "http": "HTTP", "https": "HTTPS", "ip": "IP",
    "vm": "VM", "ha": "HA", "sso": "SSO", "mfa": "MFA", "ztna": "ZTNA",
    "sase": "SASE", "cnf": "CNF", "cis": "CIS", "adc": "ADC", "wan": "WAN",
    "lan": "LAN", "nat": "NAT", "ai": "AI", "ml": "ML", "k8s": "K8s",
    "adsp": "ADSP", "nap": "NAP", "irule": "iRule", "irules": "iRules",
    "os": "OS", "ui": "UI", "ux": "UX", "sdwan": "SD-WAN", "mcn": "MCN",
    "cve": "CVE", "siem": "SIEM", "soc": "SOC", "pki": "PKI", "ztp": "ZTP",
    # mobile generations
    "4g": "4G", "5g": "5G", "6g": "6G",
    # silicon and platform
    "cpu": "CPU", "gpu": "GPU", "dpu": "DPU", "fpga": "FPGA", "bnk": "BNK",
    "tmos": "TMOS", "iapp": "iApp", "openshift": "OpenShift",
    "openstack": "OpenStack",
    # ops disciplines
    "aiops": "AIOps", "devops": "DevOps", "netops": "NetOps",
    "secops": "SecOps", "xops": "XOps",
    # protocols and addressing
    "ipv4": "IPv4", "ipv6": "IPv6", "wifi": "Wi-Fi", "sim": "SIM",
    # security and AI
    "edr": "EDR", "rag": "RAG", "ttft": "TTFT", "ebpf": "eBPF",
    # general
    "atm": "ATM", "b2b": "B2B", "id": "ID", "it": "IT", "php": "PHP",
    "sql": "SQL", "us": "US", "vr": "VR",
}
PHRASE_FIXUPS = [
    # branding
    (r"\bBig[- ]?IP\b", "BIG-IP"),
    (r"\bSd Wan\b", "SD-WAN"),
    (r"\bHw\b", "Hardware"),
    (r"\bAb Testing\b", "A/B Testing"),
    (r"\bCi Cd\b", "CI/CD"),
    (r"\bSSL TLS\b", "SSL/TLS"),
    (r"\bTLS SSL\b", "TLS/SSL"),
    (r"\bFirewall WAF\b", "Firewall (WAF)"),
    (r"\bQuestionmark\b", "Question Mark"),
    # vendor filename typos
    (r"\bIntegretion\b", "Integration"),
    (r"\bMicroservoces\b", "Microservices"),
    (r"\bOptimzation\b", "Optimization"),
    (r"\bClippboard\b", "Clipboard"),
    (r"\bCompetetive\b", "Competitive"),
    (r"\bCinsolidate\b", "Consolidate"),
    (r"\bSheild\b", "Shield"),
    (r"\bLoad0\b", "Load"),
    # vendor filename asides
    (r"\(Usually Use For VELOS\)", "(VELOS)"),
    (r"\(Usually Use For R Series\)", "(rSeries)"),
]
STRIP_PREFIXES = ("icon-", "icon_", "ico-", "ico_")

XML_DECL = re.compile(rb"<\?xml[^>]*\?>", re.I)
DOCTYPE = re.compile(rb"<!DOCTYPE[^>]*>", re.I)
COMMENT = re.compile(rb"<!--.*?-->", re.S)
SCRIPT = re.compile(rb"<script\b.*?</script\s*>", re.S | re.I)
WS = re.compile(rb">\s+<")
NUMRE = re.compile(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")


# ----------------------------------------------------------------- encoding

def drawio_compress(xml: str) -> str:
    """Mirror draw.io's Graph.compress(): encodeURIComponent -> deflateRaw -> base64."""
    co = zlib.compressobj(9, zlib.DEFLATED, -15)
    raw = co.compress(quote(xml, safe="!~*'()").encode("utf-8")) + co.flush()
    return base64.b64encode(raw).decode("ascii")


def xesc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;").replace("'", "&#39;"))


# ---------------------------------------------------------------- transforms

def mat_mul(m, n):
    a1, b1, c1, d1, e1, f1 = m
    a2, b2, c2, d2, e2, f2 = n
    return (a1 * a2 + c1 * b2, b1 * a2 + d1 * b2,
            a1 * c2 + c1 * d2, b1 * c2 + d1 * d2,
            a1 * e2 + c1 * f2 + e1, b1 * e2 + d1 * f2 + f1)


def parse_transform(s: str):
    """Return an affine matrix (a, b, c, d, e, f) for an SVG transform list."""
    m = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    for name, argstr in re.findall(r"([A-Za-z]+)\s*\(([^)]*)\)", s or ""):
        v = [float(x) for x in NUMRE.findall(argstr)]
        if not v:
            continue
        if name == "translate":
            t = (1, 0, 0, 1, v[0], v[1] if len(v) > 1 else 0)
        elif name == "scale":
            sx = v[0]
            sy = v[1] if len(v) > 1 else sx
            t = (sx, 0, 0, sy, 0, 0)
        elif name == "rotate":
            a = math.radians(v[0])
            cos, sin = math.cos(a), math.sin(a)
            t = (cos, sin, -sin, cos, 0, 0)
            if len(v) == 3:
                t = mat_mul(mat_mul((1, 0, 0, 1, v[1], v[2]), t),
                            (1, 0, 0, 1, -v[1], -v[2]))
        elif name == "matrix" and len(v) == 6:
            t = tuple(v)
        elif name == "skewX":
            t = (1, 0, math.tan(math.radians(v[0])), 1, 0, 0)
        elif name == "skewY":
            t = (1, math.tan(math.radians(v[0])), 0, 1, 0, 0)
        else:
            continue
        m = mat_mul(m, t)
    return m


def apply_matrix(ops, m):
    a, b, c, d, e, f = m
    if (a, b, c, d, e, f) == (1, 0, 0, 1, 0, 0):
        return ops
    out = []
    for kind, arr in ops:
        pts = []
        for i in range(0, len(arr), 2):
            x, y = arr[i], arr[i + 1]
            pts += [a * x + c * y + e, b * x + d * y + f]
        out.append((kind, pts))
    return out


# ------------------------------------------------------------ shape -> path

def shape_to_ops(tag: str, at: dict):
    """Convert an SVG basic shape into path ops, or return None if unknown."""
    def g(k, dflt=0.0):
        try:
            return float(at.get(k, dflt) or 0)
        except ValueError:
            return 0.0

    if tag == "path":
        return parse_path(at.get("d", "")) if at.get("d") else []

    if tag == "rect":
        x, y, w, h = g("x"), g("y"), g("width"), g("height")
        if w <= 0 or h <= 0:
            return []
        rxa, rya = at.get("rx"), at.get("ry")
        rx = float(rxa) if rxa not in (None, "") else (float(rya) if rya else 0.0)
        ry = float(rya) if rya not in (None, "") else rx
        rx, ry = min(rx, w / 2), min(ry, h / 2)
        if rx <= 0 or ry <= 0:
            d = f"M{x} {y}H{x+w}V{y+h}H{x}Z"
        else:
            d = (f"M{x+rx} {y}H{x+w-rx}A{rx} {ry} 0 0 1 {x+w} {y+ry}"
                 f"V{y+h-ry}A{rx} {ry} 0 0 1 {x+w-rx} {y+h}"
                 f"H{x+rx}A{rx} {ry} 0 0 1 {x} {y+h-ry}"
                 f"V{y+ry}A{rx} {ry} 0 0 1 {x+rx} {y}Z")
        return parse_path(d)

    if tag in ("circle", "ellipse"):
        cx, cy = g("cx"), g("cy")
        rx = ry = g("r") if tag == "circle" else 0.0
        if tag == "ellipse":
            rx, ry = g("rx"), g("ry")
        if rx <= 0 or ry <= 0:
            return []
        return parse_path(f"M{cx-rx} {cy}A{rx} {ry} 0 1 0 {cx+rx} {cy}"
                          f"A{rx} {ry} 0 1 0 {cx-rx} {cy}Z")

    if tag in ("polygon", "polyline"):
        v = [float(n) for n in NUMRE.findall(at.get("points", ""))]
        if len(v) < 4:
            return []
        d = f"M{v[0]} {v[1]}" + "".join(f"L{v[i]} {v[i+1]}"
                                       for i in range(2, len(v) - 1, 2))
        return parse_path(d + ("Z" if tag == "polygon" else ""))

    if tag == "line":
        return parse_path(f"M{g('x1')} {g('y1')}L{g('x2')} {g('y2')}")

    return None


# ------------------------------------------------------------ svg analysis

class Unsupported(Exception):
    pass


def local(tag: str) -> str:
    return tag.split("}")[-1].lower() if "}" in tag else str(tag).lower()


def parse_len(v: str):
    m = re.match(r"^\s*(-?[\d.]+)\s*(px|pt|mm|cm|in|pc)?\s*$", v or "")
    if not m:
        return None
    f = {"px": 1, "pt": 4 / 3, "pc": 16, "in": 96,
         "cm": 96 / 2.54, "mm": 96 / 25.4}[(m.group(2) or "px").lower()]
    return float(m.group(1)) * f


def viewbox(root):
    vb = root.get("viewBox") or root.get("viewbox")
    if vb:
        p = re.split(r"[\s,]+", vb.strip())
        if len(p) == 4:
            try:
                x, y, w, h = (float(v) for v in p)
                if w > 0 and h > 0:
                    return x, y, w, h
            except ValueError:
                pass
    w = parse_len(root.get("width", ""))
    h = parse_len(root.get("height", ""))
    return (0.0, 0.0, w, h) if w and h else None


def norm_color(c, default):
    """Return a hex color, or None for an explicit no-fill."""
    if c is None or str(c).strip() == "":
        return default
    c = str(c).strip().lower()
    if c in ("none", "transparent"):
        return None
    if c == "currentcolor":
        return default
    if re.fullmatch(r"#[0-9a-f]{3}", c):
        return "#" + "".join(ch * 2 for ch in c[1:])
    if re.fullmatch(r"#[0-9a-f]{6}", c):
        return c
    m = re.fullmatch(r"rgba?\(([^)]*)\)", c)
    if m:
        p = [x for x in re.split(r"[\s,/]+", m.group(1)) if x]
        try:
            vals = []
            for x in p[:3]:
                vals.append(int(round(float(x[:-1]) * 2.55)) if x.endswith("%")
                            else int(float(x)))
            r, g, b = vals
            return f"#{r:02x}{g:02x}{b:02x}"
        except (ValueError, IndexError):
            return default
    return {"black": "#000000", "white": "#ffffff", "red": "#ff0000",
            "gray": "#808080", "grey": "#808080",
            "silver": "#c0c0c0"}.get(c, default)


def style_dict(el) -> dict:
    """Presentation attributes merged with inline style, style winning."""
    d = dict(el.attrib)
    for decl in (el.get("style") or "").split(";"):
        if ":" in decl:
            k, v = decl.split(":", 1)
            d[k.strip().lower()] = v.strip()
    return d


def extract_geometry(root, default_color):
    """Walk the tree, returning [(ops, fill_hex)] in paint order."""
    out = []

    def walk(el, mat, inherited_fill, inherited_rule):
        tag = local(el.tag)
        if tag in UNSUPPORTED:
            raise Unsupported(tag)
        if not isinstance(el.tag, str):      # comment / processing instruction
            return

        st = style_dict(el)
        if str(st.get("fill", "")).strip().lower().startswith("url("):
            raise Unsupported("fill-url")
        if str(st.get("stroke") or "none").strip().lower() not in ("none", ""):
            raise Unsupported("stroke")
        if st.get("clip-path") or st.get("mask") or st.get("filter"):
            raise Unsupported("clip-mask-filter")
        op = str(st.get("opacity", "")).strip()
        if op and op not in ("1", "1.0"):
            raise Unsupported("opacity")
        fo = str(st.get("fill-opacity", "")).strip()
        if fo and fo not in ("1", "1.0"):
            raise Unsupported("fill-opacity")

        rule = str(st.get("fill-rule") or inherited_rule).strip().lower()
        if rule == "evenodd":
            raise Unsupported("evenodd")

        mat = mat_mul(mat, parse_transform(el.get("transform", "")))
        fill = norm_color(st.get("fill"), inherited_fill)

        if tag in SHAPE_TAGS:
            ops = shape_to_ops(tag, st)
            if ops is None:
                raise Unsupported(tag)
            if ops and fill:
                out.append((apply_matrix(ops, mat), fill))
            return

        if tag not in CONTAINER_TAGS:
            raise Unsupported(tag)

        for child in el:
            walk(child, mat, fill if fill else inherited_fill, rule)

    walk(root, (1.0, 0.0, 0.0, 1.0, 0.0, 0.0), default_color, "nonzero")
    return out


# --------------------------------------------------------------- generation

def build_stencil(name, geom, vb, prec):
    """Return (stencil_xml, primary_color)."""
    dx, dy, w, h = vb
    colors = [c for _, c in geom]
    primary = Counter(colors).most_common(1)[0][0]
    multi = len(set(colors)) > 1

    body = []
    for ops, color in geom:
        if multi:
            body.append(f'<fillcolor color="{color}"/>')
        body.append("<path>" + ops_to_stencil_path(ops, dx, dy, prec) + "</path>")
        body.append("<fill/>")

    return (f'<shape name="{xesc(name)}" w="{w:g}" h="{h:g}"'
            f' aspect="fixed" strokewidth="inherit">'
            f'<connections/><background/>'
            f'<foreground>{"".join(body)}</foreground></shape>'), primary


def clean_svg(data: bytes, color):
    data = XML_DECL.sub(b"", data)
    data = DOCTYPE.sub(b"", data)
    data = COMMENT.sub(b"", data)
    data = SCRIPT.sub(b"", data)
    data = WS.sub(b"><", data)
    if color:
        data = re.sub(rb"currentColor", color.encode(), data, flags=re.I)
    return data.strip()


def make_entry(title, style, w, h, compress):
    model = ("<mxGraphModel><root>"
             '<mxCell id="0"/><mxCell id="1" parent="0"/>'
             f'<mxCell id="2" value="" style="{xesc(style)}" vertex="1" parent="1">'
             f'<mxGeometry x="0" y="0" width="{w}" height="{h}" as="geometry"/>'
             "</mxCell></root></mxGraphModel>")
    return {"xml": drawio_compress(model) if compress else model,
            "w": w, "h": h, "title": title, "aspect": "fixed"}


def scaled(w, h, target):
    if w >= h:
        return target, max(1, round(target * h / w))
    return max(1, round(target * w / h)), target


# ------------------------------------------------------------------ naming

def prettify(stem: str) -> str:
    s = stem
    low = s.lower()
    for p in STRIP_PREFIXES:
        if low.startswith(p):
            s = s[len(p):]
            break
    s = s.replace("-_-", "\x00").replace("_-_", "\x00")
    s = re.sub(r"[-_]+", " ", s)
    s = s.replace("\x00", " / ")
    s = s.replace("&", " & ")
    s = re.sub(r"(?<=[^\s(])\(", " (", s)   # response(edr) -> response (edr)
    s = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\s*(@\d+x|copy|\d+\s*px)$", "", s, flags=re.I).strip()

    out = []
    for word in s.split(" "):
        # Peel surrounding brackets so "(edr)" still hits the acronym table.
        m = re.fullmatch(r"([(\[]*)([^\s()\[\]]*)([)\]]*)", word)
        pre, core, post = m.groups() if m else ("", word, "")
        key = core.lower()
        if key in ACRONYMS:
            fixed = ACRONYMS[key]
        elif core in ("/", "&"):
            fixed = core
        elif core.isupper() and len(core) > 1:
            fixed = core
        elif len(core) > 1 and any(c.isupper() for c in core[1:]):
            fixed = core
        else:
            fixed = core.capitalize()
        out.append(pre + fixed + post)
    res = " ".join(out) or stem
    for pat, rep in PHRASE_FIXUPS:
        res = re.sub(pat, rep, res)
    return res


# -------------------------------------------------------------- collection

def gather(src: Path, include_raster: bool):
    exts = {".svg"} | (set(RASTER_MIME) if include_raster else set())
    found = []
    if src.is_file() and src.suffix.lower() == ".zip":
        with zipfile.ZipFile(src) as z:
            for info in z.infolist():
                p = Path(info.filename)
                if (info.is_dir() or p.name.startswith("._")
                        or p.suffix.lower() not in exts
                        or any(x in SKIP_DIR_PARTS for x in p.parts)):
                    continue
                found.append((p.as_posix(), p.suffix.lower(), z.read(info)))
    elif src.is_dir():
        for p in sorted(src.rglob("*")):
            if (not p.is_file() or p.suffix.lower() not in exts
                    or p.name.startswith("._")
                    or any(x in SKIP_DIR_PARTS for x in p.parts)):
                continue
            found.append((p.relative_to(src).as_posix(), p.suffix.lower(),
                          p.read_bytes()))
    else:
        sys.exit(f"error: {src} is not a directory or .zip")
    return sorted(found)


def write_library(entries, path):
    entries = sorted(entries, key=lambda e: e["title"].lower())
    payload = json.dumps(entries, separators=(",", ":"))
    # The JSON sits in an XML text node, so & < > must be escaped or the file
    # is not well-formed and draw.io refuses the import. Titles are the usual
    # culprit ("Load & Queue Awareness", "IPv4 & IPv6").
    payload = escape(payload)
    path.write_text(f"<mxlibrary>{payload}</mxlibrary>", encoding="utf-8")


# -------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="Build draw.io libraries from SVGs.")
    ap.add_argument("source", type=Path)
    ap.add_argument("-o", "--out", type=Path, default=Path("./libraries"))
    ap.add_argument("--name", default="icons")
    ap.add_argument("--size", type=int, default=64)
    ap.add_argument("--fill", default="#000000",
                    help="Default fillColor for stencil shapes (default #000000)")
    ap.add_argument("--per-category", action="store_true",
                    help="Also emit one library per detected category")
    ap.add_argument("--min-category", type=int, default=3,
                    help="Filename prefix must repeat this often to count")
    ap.add_argument("--precision", type=int, default=2,
                    help="Decimal places for stencil coordinates (default 2)")
    ap.add_argument("--force-image", action="store_true",
                    help="Skip stencil conversion, embed everything as images")
    ap.add_argument("--include-raster", action="store_true")
    ap.add_argument("--no-compress", action="store_true")
    ap.add_argument("--report", action="store_true",
                    help="Print names and conversion modes, write nothing")
    args = ap.parse_args()

    assets = gather(args.source, args.include_raster)
    if not assets:
        sys.exit("error: no assets found")

    stems = [Path(r).stem for r, _, _ in assets]
    heads = Counter(s.split("-", 1)[0].lower() for s in stems if "-" in s)
    known = {h for h, n in heads.items() if n >= args.min_category}

    meta = []
    for (rel, ext, raw), stem in zip(assets, stems):
        head = stem.split("-", 1)
        if len(head) == 2 and head[0].lower() in known:
            cat, rest = head[0], head[1]
        elif len(Path(rel).parts) > 1:
            cat, rest = Path(rel).parts[0], stem
        else:
            cat, rest = None, stem
        meta.append((rel, ext, raw, cat, prettify(rest)))

    base = [m[4] for m in meta]
    dupes = {t for t in base if base.count(t) > 1}
    tally, titles = Counter(), []
    for (_, _, _, cat, t) in meta:
        title = f"{t} ({prettify(cat)})" if t in dupes and cat else t
        tally[title] += 1
        titles.append(f"{title} {tally[title]}" if tally[title] > 1 else title)

    compress = not args.no_compress
    all_entries, by_cat = [], {}
    stats, fallbacks = Counter(), []

    for (rel, ext, raw, cat, _), title in zip(meta, titles):
        style = None
        if ext != ".svg":
            b64 = base64.b64encode(raw).decode()
            w = h = args.size
            style = ("shape=image;html=1;verticalLabelPosition=bottom;"
                     "verticalAlign=top;imageAspect=1;aspect=fixed;"
                     f"image=data:{RASTER_MIME[ext]},{b64};")
            stats["raster"] += 1
        else:
            if not args.force_image:
                try:
                    root = ET.fromstring(raw.decode("utf-8", "replace"))
                    vb = viewbox(root)
                    if vb is None:
                        raise Unsupported("no-viewbox")
                    geom = extract_geometry(root, args.fill)
                    if not geom:
                        raise Unsupported("no-geometry")
                    stencil, primary = build_stencil(title, geom, vb,
                                                     args.precision)
                    w, h = scaled(vb[2], vb[3], args.size)
                    style = (f"shape=stencil({drawio_compress(stencil)});"
                             "html=1;verticalLabelPosition=bottom;"
                             "verticalAlign=top;align=center;aspect=fixed;"
                             f"strokeColor=none;fillColor={primary};")
                    stats["stencil"] += 1
                except (Unsupported, ET.ParseError, ValueError,
                        ZeroDivisionError) as e:
                    fallbacks.append((rel, type(e).__name__ + ":" + str(e)[:30]))
            if style is None:
                payload = clean_svg(raw, args.fill)
                b64 = base64.b64encode(payload).decode()
                try:
                    vb = viewbox(ET.fromstring(payload.decode("utf-8", "replace")))
                except ET.ParseError:
                    vb = None
                w, h = scaled(vb[2], vb[3], args.size) if vb else (args.size,
                                                                   args.size)
                style = ("shape=image;html=1;verticalLabelPosition=bottom;"
                         "verticalAlign=top;imageAspect=1;aspect=fixed;"
                         f"image=data:image/svg+xml,{b64};")
                stats["image"] += 1

        entry = make_entry(title, style, w, h, compress)
        all_entries.append(entry)
        by_cat.setdefault(cat or "_uncategorized", []).append(entry)

    if args.report:
        for (rel, _, _, cat, _), title in zip(meta, titles):
            print(f"{(cat or '-'):<16} {title:<44} {rel}")
        print(f"\ncategories detected ({len(known)}): {sorted(known)}")
        print(f"modes: {dict(stats)}")
        if fallbacks:
            print(f"\nimage fallbacks ({len(fallbacks)}):")
            for r, why in fallbacks[:30]:
                print(f"  {why:<34} {r}")
            if len(fallbacks) > 30:
                print(f"  ... and {len(fallbacks) - 30} more")
        return

    args.out.mkdir(parents=True, exist_ok=True)
    combined = args.out / f"{args.name}.xml"
    write_library(all_entries, combined)
    print(f"{combined}  {len(all_entries)} shapes, "
          f"{combined.stat().st_size/1024:.0f} KB")

    if args.per_category:
        for cat, entries in sorted(by_cat.items()):
            slug = re.sub(r"[^a-z0-9]+", "-", cat.lower()).strip("-") or "misc"
            p = args.out / f"{args.name}-{slug}.xml"
            write_library(entries, p)
            print(f"{p}  {len(entries)} shapes, {p.stat().st_size/1024:.0f} KB")

    print(f"\nmodes: {dict(stats)}")
    if fallbacks:
        print(f"{len(fallbacks)} image fallbacks (run --report for detail)",
              file=sys.stderr)


if __name__ == "__main__":
    main()
