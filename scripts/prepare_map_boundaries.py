"""Build display-only NYC scope layers from the fixed official 26b snapshot.

No database connection, source rewrite, network request, or hull approximation.
Run with the project interpreter from the project root.
"""
import gzip
import hashlib
import json
from pathlib import Path

from pyproj import CRS, Transformer
from shapely.geometry import box, mapping, shape
from shapely.ops import transform, unary_union

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'data/raw/nyc_2021_2025_20261004/spatial'
OUTPUT = ROOT / 'static/data/nyc_research_boundary.geojson'
REGION = (-74.65, 40.35, -73.30, 41.06)
NAMES = {1: '曼哈顿', 2: '布朗克斯', 3: '布鲁克林', 4: '皇后区', 5: '斯塔滕岛'}


def build():
    water_path = SOURCE / 'boroughs_water_included.json.gz'
    land_path = SOURCE / 'boroughs_water_excluded.json.gz'
    water = json.loads(gzip.decompress(water_path.read_bytes()))
    land = json.loads(gzip.decompress(land_path.read_bytes()))
    boroughs = {int(row['boro_code'] if 'boro_code' in row else row['borocode']): shape(row['the_geom']) for row in water}
    lands = {int(row['boro_code'] if 'boro_code' in row else row['borocode']): shape(row['the_geom']) for row in land}
    if set(boroughs) != set(NAMES) or set(lands) != set(NAMES):
        raise ValueError('Expected the five official boroughs')
    if not all(g.is_valid and not g.is_empty for g in [*boroughs.values(), *lands.values()]):
        raise ValueError('Invalid source geometry; source is left unchanged')
    city = unary_union(list(boroughs.values()))
    internal = unary_union([g.boundary for g in boroughs.values()]).difference(city.boundary)
    # EPSG:2263 uses US survey feet. Convert the tolerance from metres explicitly.
    crs = CRS.from_epsg(2263)
    tolerance_m = 5
    forward = Transformer.from_crs(4326, crs, always_xy=True).transform
    backward = Transformer.from_crs(crs, 4326, always_xy=True).transform
    tolerance_units = tolerance_m / crs.axis_info[0].unit_conversion_factor

    def simplified(geometry):
        result = transform(backward, transform(forward, geometry).simplify(tolerance_units, preserve_topology=True))
        if not result.is_valid or result.is_empty:
            raise ValueError('Invalid derived geometry')
        return result

    display_city = simplified(city)
    if not box(*REGION).covers(city):
        raise ValueError('Boundary exceeds the local basemap region')
    features = []

    def add(role, geometry, **properties):
        features.append({'type': 'Feature', 'properties': {'role': role, **properties}, 'geometry': mapping(geometry)})

    add('city', display_city)
    add('internal', simplified(internal))
    add('outside', box(*REGION).difference(display_city))
    for code, geometry in sorted(boroughs.items()):
        add('borough', simplified(geometry), code=code, name=NAMES[code])
        land_geometry = lands[code]
        largest = max(land_geometry.geoms, key=lambda g: g.area) if hasattr(land_geometry, 'geoms') else land_geometry
        add('label', largest.representative_point(), code=code, name=NAMES[code])
    projected = transform(forward, city)
    difference = projected.symmetric_difference(transform(forward, display_city)).area / projected.area
    if difference > 0.001:
        raise ValueError('Display simplification changed too much area')
    result = {'type': 'FeatureCollection', 'bbox': list(city.bounds), 'metadata': {
        'source': 'NYC DCP / NYC Open Data', 'version': '26b', 'water_areas': 'included',
        'boundary_dataset': 'wh2p-dxnf', 'land_dataset': 'gthc-hcne',
        'source_url': 'https://data.cityofnewyork.us/resource/wh2p-dxnf.json',
        'display_only': True, 'crs': 'EPSG:4326', 'simplification_crs': 'EPSG:2263',
        'tolerance_metres': tolerance_m, 'symmetric_area_fraction': difference,
        'source_sha256': hashlib.sha256(water_path.read_bytes()).hexdigest(),
    }, 'features': features}
    encoded = json.dumps(result, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    if len(encoded) > 1024 * 1024:
        raise ValueError('Display resource exceeds 1 MiB budget')
    OUTPUT.write_bytes(encoded)
    print(f'Created {OUTPUT.relative_to(ROOT)}: {len(encoded):,} bytes; {len(features)} features; area difference {difference:.6%}')


if __name__ == '__main__':
    build()
