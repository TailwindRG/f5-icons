"""
svgpath.py - Parse SVG path data into absolute move/line/curve/close ops,
and emit mxGraph stencil XML.

mxStencil understands <move>, <line>, <curve> (cubic), <quad>, <arc>, <close>.
We normalize everything to move/line/curve so there is exactly one code path
to get wrong, and arcs get converted with the standard endpoint-to-center
parameterization from the SVG 1.1 implementation notes (F.6.5).
"""

import math
import re

NUM = re.compile(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")
CMD = re.compile(r"[MmZzLlHhVvCcSsQqTtAa]")


def _tokenize(d: str):
    """Yield (command_letter, [floats]) with correct arity, handling implicit repeats."""
    arity = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4,
             "Q": 4, "T": 2, "A": 7, "Z": 0}
    i, n = 0, len(d)
    cmd = None
    while i < n:
        ch = d[i]
        if ch in " ,\t\r\n":
            i += 1
            continue
        if CMD.match(ch):
            cmd = ch
            i += 1
        elif cmd is None:
            raise ValueError(f"path data starts with a number: {d[:24]!r}")
        else:
            # Implicit repeat: M->L, m->l, everything else repeats itself.
            if cmd == "M":
                cmd = "L"
            elif cmd == "m":
                cmd = "l"

        k = arity[cmd.upper()]
        args = []
        while len(args) < k:
            while i < n and d[i] in " ,\t\r\n":
                i += 1
            # Arc flags are single characters and may be packed ("1 0 1" or "101").
            if cmd.upper() == "A" and len(args) in (3, 4):
                if i < n and d[i] in "01":
                    args.append(float(d[i]))
                    i += 1
                    continue
            m = NUM.match(d, i)
            if not m:
                if not args:
                    break
                raise ValueError(f"expected number at offset {i} in path data")
            args.append(float(m.group()))
            i = m.end()
        if len(args) < k:
            break
        yield cmd, args
        if k == 0:
            continue


def _arc_to_curves(x0, y0, rx, ry, phi_deg, large_arc, sweep, x, y):
    """SVG elliptical arc -> list of cubic bezier segments (SVG 1.1 F.6.5)."""
    if x0 == x and y0 == y:
        return []
    if rx == 0 or ry == 0:
        return [("L", [x, y])]

    rx, ry = abs(rx), abs(ry)
    phi = math.radians(phi_deg % 360)
    cos_p, sin_p = math.cos(phi), math.sin(phi)

    dx2, dy2 = (x0 - x) / 2.0, (y0 - y) / 2.0
    x1p = cos_p * dx2 + sin_p * dy2
    y1p = -sin_p * dx2 + cos_p * dy2

    # F.6.6: scale up radii if they are too small to span the endpoints.
    lam = (x1p * x1p) / (rx * rx) + (y1p * y1p) / (ry * ry)
    if lam > 1:
        s = math.sqrt(lam)
        rx, ry = rx * s, ry * s

    num = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p
    den = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    co = math.sqrt(max(0.0, num / den)) if den else 0.0
    if large_arc == sweep:
        co = -co
    cxp = co * rx * y1p / ry
    cyp = -co * ry * x1p / rx

    cx = cos_p * cxp - sin_p * cyp + (x0 + x) / 2.0
    cy = sin_p * cxp + cos_p * cyp + (y0 + y) / 2.0

    def angle(ux, uy, vx, vy):
        dot = ux * vx + uy * vy
        n = math.hypot(ux, uy) * math.hypot(vx, vy)
        if n == 0:
            return 0.0
        a = math.acos(max(-1.0, min(1.0, dot / n)))
        return -a if (ux * vy - uy * vx) < 0 else a

    theta1 = angle(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    dtheta = angle((x1p - cxp) / rx, (y1p - cyp) / ry,
                   (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if not sweep and dtheta > 0:
        dtheta -= 2 * math.pi
    elif sweep and dtheta < 0:
        dtheta += 2 * math.pi

    segs = max(1, int(math.ceil(abs(dtheta) / (math.pi / 2))))
    delta = dtheta / segs
    t = 4.0 / 3.0 * math.tan(delta / 4.0)

    out = []
    th = theta1
    px, py = x0, y0
    for _ in range(segs):
        th2 = th + delta
        cos1, sin1 = math.cos(th), math.sin(th)
        cos2, sin2 = math.cos(th2), math.sin(th2)

        def pt(c, s):
            return (cx + rx * c * cos_p - ry * s * sin_p,
                    cy + rx * c * sin_p + ry * s * cos_p)

        ex, ey = pt(cos2, sin2)
        # Derivative at the parametric endpoints, scaled by t.
        d1x = -rx * sin1 * cos_p - ry * cos1 * sin_p
        d1y = -rx * sin1 * sin_p + ry * cos1 * cos_p
        d2x = -rx * sin2 * cos_p - ry * cos2 * sin_p
        d2y = -rx * sin2 * sin_p + ry * cos2 * cos_p

        out.append(("C", [px + t * d1x, py + t * d1y,
                          ex - t * d2x, ey - t * d2y, ex, ey]))
        px, py, th = ex, ey, th2
    return out


def parse_path(d: str):
    """Return a flat list of ('M'|'L'|'C'|'Z', [absolute coords])."""
    ops = []
    cx = cy = 0.0          # current point
    sx = sy = 0.0          # subpath start
    prev_c2 = None         # last cubic control point, for S/s
    prev_q1 = None         # last quadratic control point, for T/t
    last = None

    for cmd, a in _tokenize(d):
        up = cmd.upper()
        rel = cmd.islower()

        if up == "M":
            x, y = (cx + a[0], cy + a[1]) if rel else (a[0], a[1])
            ops.append(("M", [x, y]))
            cx = cy = 0
            cx, cy = x, y
            sx, sy = x, y
            prev_c2 = prev_q1 = None

        elif up == "L":
            x, y = (cx + a[0], cy + a[1]) if rel else (a[0], a[1])
            ops.append(("L", [x, y]))
            cx, cy = x, y
            prev_c2 = prev_q1 = None

        elif up == "H":
            x = cx + a[0] if rel else a[0]
            ops.append(("L", [x, cy]))
            cx = x
            prev_c2 = prev_q1 = None

        elif up == "V":
            y = cy + a[0] if rel else a[0]
            ops.append(("L", [cx, y]))
            cy = y
            prev_c2 = prev_q1 = None

        elif up == "C":
            c = [cx + a[i] if rel and i % 2 == 0 else
                 cy + a[i] if rel else a[i] for i in range(6)]
            ops.append(("C", c))
            prev_c2 = (c[2], c[3])
            cx, cy = c[4], c[5]
            prev_q1 = None

        elif up == "S":
            r = [cx + a[i] if rel and i % 2 == 0 else
                 cy + a[i] if rel else a[i] for i in range(4)]
            if last in ("C", "S") and prev_c2:
                c1x, c1y = 2 * cx - prev_c2[0], 2 * cy - prev_c2[1]
            else:
                c1x, c1y = cx, cy
            c = [c1x, c1y, r[0], r[1], r[2], r[3]]
            ops.append(("C", c))
            prev_c2 = (r[0], r[1])
            cx, cy = r[2], r[3]
            prev_q1 = None

        elif up == "Q":
            r = [cx + a[i] if rel and i % 2 == 0 else
                 cy + a[i] if rel else a[i] for i in range(4)]
            ops.append(("C", _quad_to_cubic(cx, cy, r[0], r[1], r[2], r[3])))
            prev_q1 = (r[0], r[1])
            cx, cy = r[2], r[3]
            prev_c2 = None

        elif up == "T":
            x, y = (cx + a[0], cy + a[1]) if rel else (a[0], a[1])
            if last in ("Q", "T") and prev_q1:
                qx, qy = 2 * cx - prev_q1[0], 2 * cy - prev_q1[1]
            else:
                qx, qy = cx, cy
            ops.append(("C", _quad_to_cubic(cx, cy, qx, qy, x, y)))
            prev_q1 = (qx, qy)
            cx, cy = x, y
            prev_c2 = None

        elif up == "A":
            x, y = (cx + a[5], cy + a[6]) if rel else (a[5], a[6])
            for kind, args in _arc_to_curves(cx, cy, a[0], a[1], a[2],
                                             int(a[3]), int(a[4]), x, y):
                ops.append((kind, args))
            cx, cy = x, y
            prev_c2 = prev_q1 = None

        elif up == "Z":
            ops.append(("Z", []))
            cx, cy = sx, sy
            prev_c2 = prev_q1 = None

        last = up
    return ops


def _quad_to_cubic(x0, y0, qx, qy, x, y):
    return [x0 + 2.0 / 3.0 * (qx - x0), y0 + 2.0 / 3.0 * (qy - y0),
            x + 2.0 / 3.0 * (qx - x), y + 2.0 / 3.0 * (qy - y),
            x, y]


def ops_to_svg_d(ops, prec=3) -> str:
    """Rebuild an SVG 'd' string. Used by the verification harness."""
    def f(v):
        return f"{round(v, prec):g}"
    out = []
    for kind, a in ops:
        if kind == "M":
            out.append("M" + " ".join(f(v) for v in a))
        elif kind == "L":
            out.append("L" + " ".join(f(v) for v in a))
        elif kind == "C":
            out.append("C" + " ".join(f(v) for v in a))
        elif kind == "Z":
            out.append("Z")
    return " ".join(out)


def ops_to_stencil_path(ops, dx=0.0, dy=0.0, prec=3) -> str:
    """Emit the <move>/<line>/<curve>/<close> body of an mxStencil <path>."""
    def f(v):
        return f"{round(v, prec):g}"
    parts = []
    for kind, a in ops:
        if kind == "M":
            parts.append(f'<move x="{f(a[0]-dx)}" y="{f(a[1]-dy)}"/>')
        elif kind == "L":
            parts.append(f'<line x="{f(a[0]-dx)}" y="{f(a[1]-dy)}"/>')
        elif kind == "C":
            parts.append(
                f'<curve x1="{f(a[0]-dx)}" y1="{f(a[1]-dy)}"'
                f' x2="{f(a[2]-dx)}" y2="{f(a[3]-dy)}"'
                f' x3="{f(a[4]-dx)}" y3="{f(a[5]-dy)}"/>')
        elif kind == "Z":
            parts.append("<close/>")
    return "".join(parts)
