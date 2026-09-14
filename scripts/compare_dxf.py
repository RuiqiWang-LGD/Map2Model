"""Compare DXF modelspace geometry before/after a format roundtrip, read-only."""
import argparse
import hashlib
import json
import math
from pathlib import Path

import ezdxf


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def shape(entity):
    """Keep arc parameters and OCS attributes, rather than flattening them away."""
    kind = entity.dxftype()
    common = {'type': kind, 'layer': entity.dxf.layer}
    if kind == 'LINE':
        values = [list(entity.dxf.start), list(entity.dxf.end)]
    elif kind == 'LWPOLYLINE':
        values = [bool(entity.closed), float(entity.dxf.elevation),
                  [list(p) for p in entity.get_points('xyseb')],
                  float(entity.dxf.const_width)]
    elif kind == 'POLYLINE':
        if entity.is_polygon_mesh or entity.is_poly_face_mesh:
            raise ValueError('Mesh POLYLINE is not a supported planar base entity')
        values = [bool(entity.is_closed), bool(entity.is_3d_polyline),
                  list(entity.dxf.elevation),
                  [[*v.dxf.location, v.dxf.bulge, v.dxf.start_width, v.dxf.end_width]
                   for v in entity.vertices]]
    elif kind in ('ARC', 'CIRCLE'):
        values = [list(entity.dxf.center), entity.dxf.radius]
        if kind == 'ARC':
            values.extend([entity.dxf.start_angle, entity.dxf.end_angle])
    else:
        raise ValueError(f'Unsupported entity: {kind}')
    common['geometry'] = values
    common['extrusion'] = list(entity.dxf.get('extrusion', (0, 0, 1)))
    common['thickness'] = entity.dxf.get('thickness', 0)
    return common


def same(a, b, tolerance):
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isfinite(a) and math.isfinite(b) and abs(a - b) <= tolerance
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k], tolerance) for k in a)
    if isinstance(a, (tuple, list)) and isinstance(b, (tuple, list)):
        return len(a) == len(b) and all(same(x, y, tolerance) for x, y in zip(a, b))
    return a == b


def compare(before, after, tolerance=1e-6):
    if not math.isfinite(tolerance) or tolerance <= 0:
        raise ValueError('tolerance must be positive and finite, in drawing units')
    docs = [ezdxf.readfile(before), ezdxf.readfile(after)]
    issues, maps = [], []
    for label, doc in zip(('before', 'after'), docs):
        objects = {}
        for e in doc.modelspace():
            try:
                objects[e.dxf.handle] = shape(e)
            except (ValueError, TypeError) as exc:
                issues.append({'kind': 'unsupported', 'file': label,
                               'handle': e.dxf.handle, 'detail': str(exc)})
        maps.append(objects)
    if docs[0].units != docs[1].units:
        issues.append({'kind': 'units_changed', 'before': docs[0].units, 'after': docs[1].units})
    missing = sorted(set(maps[0]) - set(maps[1]))
    added = sorted(set(maps[1]) - set(maps[0]))
    if missing or added:
        issues.append({'kind': 'entity_identity_changed', 'missing': missing, 'added': added})
    for handle in sorted(set(maps[0]) & set(maps[1])):
        if not same(maps[0][handle], maps[1][handle], tolerance):
            issues.append({'kind': 'geometry_changed', 'handle': handle,
                           'before': maps[0][handle], 'after': maps[1][handle]})
    if not maps[0]:
        issues.append({'kind': 'empty_input'})
    return {'schema_version': 1, 'geometry_equal': not issues,
            'before': str(Path(before).resolve()), 'after': str(Path(after).resolve()),
            'before_sha256': sha256(before), 'after_sha256': sha256(after),
            'tolerance': tolerance, 'units_before': docs[0].units, 'units_after': docs[1].units,
            'count_before': len(docs[0].modelspace()), 'count_after': len(docs[1].modelspace()),
            'issues': issues,
            'limits': 'Requires stable entity handles/order within polylines. Checks geometry, not source-image accuracy or DWG signature.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('before', type=Path)
    parser.add_argument('after', type=Path)
    parser.add_argument('--tolerance', type=float, default=1e-6, help='drawing units; also absolute tolerance for stored arc parameters')
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--overwrite', action='store_true')
    args = parser.parse_args()
    if args.report.resolve() in (args.before.resolve(), args.after.resolve()):
        parser.error('report must not replace an input')
    if args.report.exists() and not args.overwrite:
        parser.error('report exists; choose a new path or --overwrite')
    try:
        report = compare(args.before, args.after, args.tolerance)
    except Exception as exc:
        parser.exit(2, f'{type(exc).__name__}: {exc}\n')
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'geometry_equal': report['geometry_equal'], 'issues': len(report['issues'])}))
    return 0 if report['geometry_equal'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
