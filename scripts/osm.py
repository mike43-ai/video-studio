#!/usr/bin/env python3
"""osm.py — OpenStreetMap data for map videos (free public services, cached, polite rate).

  vs osm find "<place>" [--limit 5] [--json]                    Nominatim → lat, lon, bbox, osm id
  vs osm area "<place>" | --osm W123456 [-o site.geojson]       outline polygon + area m² + perimeter m
  vs osm query --around LAT,LON,RADIUS_M --tags amenity=school,shop=supermarket [-o amenities.json]
  vs osm query --ql '<Overpass QL>' [-o out.json]               raw Overpass (add [out:json])
  vs osm route LAT,LON LAT,LON [--mode walk|drive|bike] [-o route.geojson]
                                                                distance m, duration s, line geometry

Outputs are GeoJSON / JSON a composition can draw. Data © OpenStreetMap contributors (ODbL) —
put "© OpenStreetMap contributors" on the map. Routing: OSRM (FOSSGIS servers), fair-use only.
"""
from __future__ import annotations

import argparse
import json
import math
import time
import urllib.parse
from pathlib import Path

from vslib import CACHE, die, emit, http, http_json

NOMINATIM = "https://nominatim.openstreetmap.org"
OVERPASS = "https://overpass-api.de/api/interpreter"
OSRM = {"drive": "https://routing.openstreetmap.de/routed-car/route/v1/driving",
        "walk": "https://routing.openstreetmap.de/routed-foot/route/v1/foot",
        "bike": "https://routing.openstreetmap.de/routed-bike/route/v1/bike"}
STAMP = CACHE / "nominatim-last-call"


def polite() -> None:
    """Nominatim policy: at most 1 request per second."""
    STAMP.parent.mkdir(parents=True, exist_ok=True)
    if STAMP.exists():
        wait = 1.1 - (time.time() - STAMP.stat().st_mtime)
        if wait > 0:
            time.sleep(wait)
    STAMP.touch()


def nominatim(path: str, **params) -> list | dict:
    polite()
    return http_json(f"{NOMINATIM}/{path}", params={"format": "jsonv2", **params}, cache_ttl=30 * 86400)


def ring_metrics(ring: list) -> tuple[float, float]:
    """Area (m², spherical excess approx.) and perimeter (m) of a lon/lat ring."""
    R = 6371008.8
    area = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        area += math.radians(x2 - x1) * (2 + math.sin(math.radians(y1)) + math.sin(math.radians(y2)))
    per = sum(haversine(a, b) for a, b in zip(ring, ring[1:] + ring[:1]))
    return abs(area * R * R / 2), per


def haversine(a, b) -> float:
    (lon1, lat1), (lon2, lat2) = a, b
    p1, p2 = math.radians(lat1), math.radians(lat2)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371008.8 * math.asin(math.sqrt(h))


def latlon(s: str) -> tuple[float, float]:
    try:
        lat, lon = (float(v) for v in s.split(",")[:2])
        return lat, lon
    except ValueError:
        die(f"expected LAT,LON, got {s!r}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["find", "area", "query", "route"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--osm", help="N123 / W123 / R123")
    ap.add_argument("--around")
    ap.add_argument("--tags")
    ap.add_argument("--ql")
    ap.add_argument("--mode", default="walk", choices=list(OSRM))
    ap.add_argument("-o", "--out", type=Path)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    def save(obj, default_name: str) -> Path:
        out = a.out or Path(default_name)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
        return out

    if a.cmd == "find":
        if not a.args:
            die("usage: vs osm find \"<place>\"")
        hits = nominatim("search", q=" ".join(a.args), limit=a.limit, addressdetails=0)
        rows = [{"name": h["display_name"], "lat": float(h["lat"]), "lon": float(h["lon"]),
                 "osm": f"{h['osm_type'][0].upper()}{h['osm_id']}", "type": f"{h.get('category')}/{h.get('type')}",
                 "bbox": [float(v) for v in h["boundingbox"]]} for h in hits]

        def human(rows):
            for r in rows:
                print(f"  {r['lat']:.6f},{r['lon']:.6f}  {r['osm']:<12} {r['type']:<24} {r['name'][:90]}")
        emit(rows, a.json, human)

    elif a.cmd == "area":
        if a.osm:
            hits = nominatim("lookup", osm_ids=a.osm, polygon_geojson=1)
        elif a.args:
            hits = nominatim("search", q=" ".join(a.args), limit=1, polygon_geojson=1)
        else:
            die("usage: vs osm area \"<place>\" | --osm W123")
        if not hits:
            die("place not found")
        h = hits[0]
        geom = h.get("geojson", {})
        if geom.get("type") not in ("Polygon", "MultiPolygon"):
            die(f"{h['display_name']} has no outline (it is a {geom.get('type')}); try --osm with a way/relation id")
        polys = [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]
        area = sum(ring_metrics(p[0])[0] - sum(ring_metrics(hole)[0] for hole in p[1:]) for p in polys)
        per = sum(ring_metrics(p[0])[1] for p in polys)
        feat = {"type": "Feature", "geometry": geom,
                "properties": {"name": h["display_name"], "area_m2": round(area), "perimeter_m": round(per),
                               "osm": f"{h['osm_type'][0].upper()}{h['osm_id']}", "center": [float(h["lon"]), float(h["lat"])],
                               "attribution": "© OpenStreetMap contributors"}}
        out = save(feat, "site.geojson")
        print(f"✓ {out} · {h['display_name'][:70]} · {area:,.0f} m² ({area / 1e4:.2f} ha) · perimeter {per:,.0f} m")

    elif a.cmd == "query":
        if a.ql:
            ql = a.ql
        elif a.around and a.tags:
            lat, lon, rad = (float(v) for v in a.around.split(","))
            parts = []
            for t in a.tags.split(","):
                k, _, v = t.partition("=")
                sel = f'["{k}"="{v}"]' if v else f'["{k}"]'
                parts.append(f"nwr{sel}(around:{rad:.0f},{lat},{lon});")
            ql = f"[out:json][timeout:60];({''.join(parts)});out center tags;"
        else:
            die("usage: vs osm query --around LAT,LON,R --tags k=v,… | --ql '<QL>'")
        body = http(OVERPASS, data=f"data={urllib.parse.quote(ql)}".encode(), timeout=120)
        res = json.loads(body)
        els = res.get("elements", [])
        rows = []
        for e in els:
            c = e.get("center") or {"lat": e.get("lat"), "lon": e.get("lon")}
            tags = e.get("tags", {})
            kind = next((f"{k}={tags[k]}" for k in ("amenity", "shop", "leisure", "public_transport", "railway",
                                                    "highway", "tourism", "healthcare", "office") if k in tags), "")
            rows.append({"name": tags.get("name", ""), "kind": kind, "lat": c["lat"], "lon": c["lon"],
                         "osm": f"{e['type'][0].upper()}{e['id']}"})
        counts: dict = {}
        for r in rows:
            counts[r["kind"]] = counts.get(r["kind"], 0) + 1
        out = save({"query": ql, "counts": counts, "elements": rows, "attribution": "© OpenStreetMap contributors"},
                   "osm-query.json")
        print(f"✓ {out} · {len(rows)} elements · " + ", ".join(f"{k or '?'}: {n}" for k, n in sorted(counts.items())))

    elif a.cmd == "route":
        if len(a.args) < 2:
            die("usage: vs osm route LAT,LON LAT,LON [--mode walk|drive|bike]")
        pts = [latlon(s) for s in a.args]
        coords = ";".join(f"{lon},{lat}" for lat, lon in pts)
        r = http_json(f"{OSRM[a.mode]}/{coords}", params={"overview": "full", "geometries": "geojson", "steps": "false"},
                      cache_ttl=30 * 86400)
        if r.get("code") != "Ok" or not r.get("routes"):
            die(f"no route: {r.get('message', r.get('code'))}")
        rt = r["routes"][0]
        feat = {"type": "Feature", "geometry": rt["geometry"],
                "properties": {"mode": a.mode, "distance_m": round(rt["distance"]), "duration_s": round(rt["duration"]),
                               "legs": [{"distance_m": round(l["distance"]), "duration_s": round(l["duration"])} for l in rt["legs"]],
                               "attribution": "© OpenStreetMap contributors, OSRM"}}
        out = save(feat, f"route-{a.mode}.geojson")
        print(f"✓ {out} · {a.mode} {rt['distance'] / 1000:.2f} km · {rt['duration'] / 60:.0f} min "
              f"· {len(rt['geometry']['coordinates'])} points")


if __name__ == "__main__":
    main()
