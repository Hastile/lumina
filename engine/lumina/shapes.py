"""Vector shape layers (Premiere's Essential Graphics rectangles, ellipses and
pen paths).

A path is a list of vertices ``[x, y, in_x, in_y, out_x, out_y]`` in layer
pixels, the same layout Premiere stores: the tangent handles are absolute
points, and a segment from vertex *i* to *i+1* is the cubic Bezier
``(p_i, out_i, in_{i+1}, p_{i+1})``. A corner vertex has both handles on the
point itself. ``app/src/renderer/src/util/shapes.ts`` is the TypeScript twin.
"""

from __future__ import annotations

import math

KAPPA = 4 * (math.sqrt(2) - 1) / 3  # cubic Bezier quarter-circle handle length


def rectangle(x0: float, y0: float, x1: float, y1: float) -> list[list[float]]:
    return [[x, y, x, y, x, y] for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]


def ellipse(x0: float, y0: float, x1: float, y1: float) -> list[list[float]]:
    """Four smooth vertices, top/right/bottom/left, clockwise (as Premiere draws them)."""
    cx, cy, rx, ry = (x0 + x1) / 2, (y0 + y1) / 2, abs(x1 - x0) / 2, abs(y1 - y0) / 2
    kx, ky = rx * KAPPA, ry * KAPPA
    return [
        [cx, cy - ry, cx - kx, cy - ry, cx + kx, cy - ry],
        [cx + rx, cy, cx + rx, cy - ky, cx + rx, cy + ky],
        [cx, cy + ry, cx + kx, cy + ry, cx - kx, cy + ry],
        [cx - rx, cy, cx - rx, cy + ky, cx - rx, cy - ky],
    ]


def make(kind: str, x0: float, y0: float, x1: float, y1: float) -> list[list[float]]:
    return ellipse(x0, y0, x1, y1) if kind == "ellipse" else rectangle(x0, y0, x1, y1)


def segments(path: list[list[float]], closed: bool):
    n = len(path)
    for i in range(n if closed else n - 1):
        a, b = path[i], path[(i + 1) % n]
        yield (a[0], a[1]), (a[4], a[5]), (b[2], b[3]), (b[0], b[1])


def flatten(path: list[list[float]], closed: bool, steps: int = 24) -> list[tuple[float, float]]:
    """Polyline through the path; straight segments stay a single edge."""
    if not path:
        return []
    out = [(path[0][0], path[0][1])]
    for p0, p1, p2, p3 in segments(path, closed):
        if p1 == p0 and p2 == p3:
            out.append(p3)
            continue
        for k in range(1, steps + 1):
            t = k / steps
            u = 1 - t
            out.append((
                u * u * u * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t * t * t * p3[0],
                u * u * u * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t * t * t * p3[1],
            ))
    if closed and len(out) > 1 and out[-1] == out[0]:
        out.pop()
    return out


def bounds(path: list[list[float]]) -> tuple[float, float, float, float]:
    """Bounding box of the vertices (the shape's size in Premiere's sense)."""
    if not path:
        return (0.0, 0.0, 0.0, 0.0)
    xs, ys = [v[0] for v in path], [v[1] for v in path]
    return (min(xs), min(ys), max(xs), max(ys))


def resize(path: list[list[float]], width: float | None, height: float | None) -> list[list[float]]:
    """Scale the path about its bounding-box center to the given size."""
    x0, y0, x1, y1 = bounds(path)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    fx = width / (x1 - x0) if width is not None and x1 > x0 else 1.0
    fy = height / (y1 - y0) if height is not None and y1 > y0 else 1.0
    return [[cx + (v[i] - cx) * fx if i % 2 == 0 else cy + (v[i] - cy) * fy for i in range(6)] for v in path]
