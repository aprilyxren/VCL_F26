"""Build the small base map the analysis page draws its map on.

Map tiles cannot be loaded inside the published page, so the land and rivers
are drawn from data embedded in it. Two frames:

* ``virginia``: the Chesapeake and the tidal James, York, and Potomac. Land is
  the US Census 1:500,000 state boundaries, which follow the shoreline of the
  Bay and its estuaries; river courses are OpenStreetMap ``waterway=river``
  ways (fetched with Overpass), since the narrow upper James is below the
  Census file's resolution.
* ``england``: southern Britain and the near Continent, from Natural Earth
  10m land.

Shapes are clipped to the frame (Sutherland-Hodgman for land rings), thinned
with Douglas-Peucker, and rounded to 4 decimals (about 10 m). Sources and
licences are listed in ``data/geo/map_sources.csv``.
"""

from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

FRAMES = {
    "virginia": (-77.75, 36.55, -75.55, 38.45),
    "england": (-6.2, 49.8, 2.6, 54.2),
}
TOLERANCE = {"virginia": 0.002, "england": 0.01}


def read_shp_polygons(path: Path) -> list[list[tuple[float, float]]]:
    """Rings of every polygon in an ESRI shapefile (shape type 5)."""
    data = path.read_bytes()
    rings, offset = [], 100
    while offset < len(data):
        _, length = struct.unpack(">ii", data[offset:offset + 8])
        content = data[offset + 8: offset + 8 + length * 2]
        offset += 8 + length * 2
        if struct.unpack("<i", content[:4])[0] != 5:
            continue
        num_parts, num_points = struct.unpack("<ii", content[36:44])
        parts = list(struct.unpack(f"<{num_parts}i", content[44:44 + 4 * num_parts]))
        base = 44 + 4 * num_parts
        points = [struct.unpack("<dd", content[base + 16 * i: base + 16 * i + 16]) for i in range(num_points)]
        for i, start in enumerate(parts):
            rings.append(points[start: parts[i + 1] if i + 1 < num_parts else num_points])
    return rings


def geojson_rings(path: Path) -> list[list[tuple[float, float]]]:
    rings = []
    for feature in json.loads(path.read_text(encoding="utf-8"))["features"]:
        geometry = feature["geometry"]
        polygons = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
        rings.extend(tuple(map(tuple, ring)) for polygon in polygons for ring in polygon)
    return rings


def clip_ring(ring, box):
    """Sutherland-Hodgman clip of a ring to an axis-aligned box."""
    x0, y0, x1, y1 = box
    edges = [(lambda p: p[0] >= x0, lambda a, b: cross_x(a, b, x0)),
             (lambda p: p[0] <= x1, lambda a, b: cross_x(a, b, x1)),
             (lambda p: p[1] >= y0, lambda a, b: cross_y(a, b, y0)),
             (lambda p: p[1] <= y1, lambda a, b: cross_y(a, b, y1))]
    points = list(ring)
    for inside, cross in edges:
        if not points:
            break
        out, prev = [], points[-1]
        for cur in points:
            if inside(cur):
                if not inside(prev):
                    out.append(cross(prev, cur))
                out.append(cur)
            elif inside(prev):
                out.append(cross(prev, cur))
            prev = cur
        points = out
    return points


def cross_x(a, b, x):
    t = (x - a[0]) / (b[0] - a[0])
    return (x, a[1] + t * (b[1] - a[1]))


def cross_y(a, b, y):
    t = (y - a[1]) / (b[1] - a[1])
    return (a[0] + t * (b[0] - a[0]), y)


def clip_line(line, box):
    """Pieces of a polyline inside the box (a piece ends where the line leaves)."""
    x0, y0, x1, y1 = box
    pieces, current = [], []
    for p in line:
        if x0 <= p[0] <= x1 and y0 <= p[1] <= y1:
            current.append(p)
        elif current:
            pieces.append(current)
            current = []
    if current:
        pieces.append(current)
    return [piece for piece in pieces if len(piece) > 1]


def simplify(points, tolerance):
    """Douglas-Peucker, iterative. A closed ring is split at its farthest point
    from the start, since its first and last points coincide."""
    if len(points) < 3:
        return list(points)
    if tuple(points[0]) == tuple(points[-1]):
        x0, y0 = points[0]
        far = max(range(len(points)), key=lambda i: (points[i][0] - x0) ** 2 + (points[i][1] - y0) ** 2)
        if 0 < far < len(points) - 1:
            return simplify(points[:far + 1], tolerance)[:-1] + simplify(points[far:], tolerance)
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        (ax, ay), (bx, by) = points[first], points[last]
        dx, dy = bx - ax, by - ay
        norm = (dx * dx + dy * dy) ** 0.5 or 1e-12
        index, worst = None, tolerance
        for i in range(first + 1, last):
            px, py = points[i]
            d = abs(dy * (px - ax) - dx * (py - ay)) / norm
            if d > worst:
                index, worst = i, d
        if index is not None:
            keep[index] = True
            stack += [(first, index), (index, last)]
    return [p for p, k in zip(points, keep) if k]


def rounded(points):
    return [[round(x, 4), round(y, 4)] for x, y in points]


def land(rings, frame):
    box, tolerance = FRAMES[frame], TOLERANCE[frame]
    out = []
    for ring in rings:
        xs, ys = [p[0] for p in ring], [p[1] for p in ring]
        if max(xs) < box[0] or min(xs) > box[2] or max(ys) < box[1] or min(ys) > box[3]:
            continue
        clipped = simplify(clip_ring(ring, box), tolerance)
        if len(clipped) >= 4:
            out.append(rounded(clipped))
    return out


def rivers(overpass: Path, frame: str) -> list[dict]:
    box, tolerance = FRAMES[frame], TOLERANCE[frame]
    by_name: dict[str, list] = {}
    for element in json.loads(overpass.read_text(encoding="utf-8"))["elements"]:
        line = [(node["lon"], node["lat"]) for node in element.get("geometry", [])]
        for piece in clip_line(line, box):
            by_name.setdefault(element["tags"]["name"], []).append(rounded(simplify(piece, tolerance / 2)))
    return [{"name": name, "lines": lines} for name, lines in sorted(by_name.items())]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--census-states", type=Path, required=True, help="cb_2018_us_state_500k.shp")
    parser.add_argument("--rivers", type=Path, required=True, help="Overpass JSON of waterway=river ways")
    parser.add_argument("--world-land", type=Path, required=True, help="ne_10m_land.geojson")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    basemap = {
        "virginia": {"bbox": FRAMES["virginia"], "land": land(read_shp_polygons(args.census_states), "virginia"),
                     "rivers": rivers(args.rivers, "virginia")},
        "england": {"bbox": FRAMES["england"], "land": land(geojson_rings(args.world_land), "england")},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(basemap, separators=(",", ":")), encoding="utf-8")
    points = sum(len(r) for frame in basemap.values() for r in frame["land"])
    print(f"wrote {args.output} ({args.output.stat().st_size / 1e3:.0f} kB; {points} land points, "
          f"{len(basemap['virginia']['rivers'])} rivers)")


if __name__ == "__main__":
    main()
