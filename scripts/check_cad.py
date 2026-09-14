#!/usr/bin/env python3
"""Read-only DXF QA; geometric validity is separate from documented review."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import ezdxf
from ezdxf.path import make_path
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union, polygonize_full

SUPPORTED = {'LINE', 'LWPOLYLINE', 'POLYLINE', 'ARC', 'CIRCLE'}

def fingerprint(entity):
    # Raw DXF geometry retains bulges, OCS, widths and all other entity attributes.
    from ezdxf.lldxf.tagwriter import TagCollector
    tags = TagCollector.dxftags(entity)
    return [(t.code, str(t.value)) for t in tags if t.code not in {5, 330}]

def check(dxf_path, project_path):
    dxf_path, project_path = Path(dxf_path), Path(project_path)
    cfg = json.loads(project_path.read_text(encoding='utf-8-sig'))
    doc = ezdxf.readfile(dxf_path)
    digest = hashlib.sha256(dxf_path.read_bytes()).hexdigest()
    checks = []
    def record(id, status, message='', **detail):
        checks.append(dict(id=id, status=status, message=message, **detail))
    def result(id, failures):
        record(id, 'fail' if failures else 'pass', issues=failures)
    tol = float(cfg.get('tolerance', .001))
    ztol = float(cfg.get('z_tolerance', tol))
    if not math.isfinite(tol) or tol <= 0 or not math.isfinite(ztol) or ztol < 0:
        raise ValueError('tolerance must be finite positive; z_tolerance finite nonnegative')
    if cfg.get('schema_version') != 1:
        raise ValueError('schema_version must be 1')
    unit = cfg.get('units')
    expected = {'m': 6, 'mm': 4, 'relative': 0}.get(unit)
    result('units', [] if expected is not None and doc.units == expected else [dict(configured=unit, dxf_insunits=doc.units)])
    ents = list(doc.modelspace())
    result('nonempty_drawing', [] if ents else ['Empty modelspace'])
    by_handle = {e.dxf.handle: e for e in ents}
    used_layers = {e.dxf.layer for e in ents}
    result('required_layers', [x for x in cfg.get('required_layers', []) if x not in doc.layers or x not in used_layers])
    result('required_handles', [h for h in cfg.get('required_handles', []) if h not in by_handle])
    result('forbidden_layers', [x for x in cfg.get('forbidden_layers', []) if x in used_layers])
    layers = cfg.get('layers', {})
    building_layers = layers.get('building', [])
    road_layers = layers.get('road', [])
    unsupported, invalid, zbad, open_buildings, open_roads = [], [], [], [], []
    lines, buildings, roads = [], [], []
    polygons = {}
    segments, duplicates, cross_overlaps = [], [], []
    for e in ents:
        handle, layer, typ = e.dxf.handle, e.dxf.layer, e.dxftype()
        if typ not in SUPPORTED or (typ == 'POLYLINE' and not e.is_2d_polyline):
            unsupported.append(dict(handle=handle, type=typ)); continue
        try:
            if typ == 'LINE':
                pts = [e.dxf.start, e.dxf.end]; closed = False
            else:
                path = make_path(e)
                pts = list(path.flattening(distance=tol / 4, segments=8))
                closed = typ == 'CIRCLE' or (typ in {'LWPOLYLINE','POLYLINE'} and e.is_closed)
            if any(abs(p.z) > ztol for p in pts): zbad.append(handle)
            coords = [(float(p.x), float(p.y)) for p in pts]
            if len(coords) < 2: raise ValueError('fewer than 2 points')
            line = LineString(coords)
            if not line.is_valid or not line.is_simple or line.length <= tol:
                invalid.append(dict(handle=handle, reason='degenerate or self-intersecting line'))
            lines.append((handle, layer, line))
            polygon = Polygon(coords) if closed and len(coords) >= 4 else None
            if closed and (polygon is None or not polygon.is_valid or polygon.area <= tol * tol):
                invalid.append(dict(handle=handle, reason='invalid closed polygon')); polygon = None
            if polygon is not None: polygons[handle] = polygon
            if layer in building_layers:
                if not closed: open_buildings.append(handle)
                elif polygon is not None: buildings.append((handle, polygon))
            if layer in road_layers:
                if not closed: open_roads.append(handle)
                elif polygon is not None: roads.append(polygon)
            for a, b in zip(coords, coords[1:]):
                seg = LineString([a,b])
                if seg.length <= tol / 10: continue
                segments.append((handle, seg))
        except Exception as exc:
            invalid.append(dict(handle=handle, reason=str(exc)))
    # Explicit hole associations are validated before constructing semantic areas.
    hole_map = cfg.get('hole_map', {})
    hole_errors, owners = [], {}
    composed = dict(polygons)
    def role(h):
        if h not in by_handle: return None
        assigned = [name for name in ('building','road') if by_handle[h].dxf.layer in layers.get(name,[])]
        return assigned[0] if len(assigned) == 1 else None
    if not isinstance(hole_map, dict):
        hole_errors.append('hole_map must be an object'); hole_map = {}
    for outer, inners in hole_map.items():
        if not isinstance(inners,list) or not inners:
            hole_errors.append(dict(outer=outer,reason='Expected nonempty inner-handle array')); continue
        holes=[]
        for inner in inners:
            if not isinstance(inner,str):
                hole_errors.append(dict(outer=outer,reason='Inner handle must be string')); continue
            if inner in owners:
                hole_errors.append(dict(inner=inner,reason='Duplicate or multiple parents'))
            owners[inner]=outer
            if outer not in polygons or inner not in polygons or role(outer) is None or role(outer) != role(inner):
                hole_errors.append(dict(outer=outer,inner=inner,reason='Missing, unclosed, invalid, or incompatible semantic role')); continue
            shell, hole = polygons[outer],polygons[inner]
            if inner in hole_map or outer == inner:
                hole_errors.append(dict(inner=inner,reason='Nested hole ownership is unsupported'))
            if not shell.contains(hole) or shell.boundary.intersects(hole):
                hole_errors.append(dict(outer=outer,inner=inner,reason='Hole must be strictly contained without boundary contact'))
            if any(hole.intersects(other) for other in holes):
                hole_errors.append(dict(inner=inner,reason='Holes must not overlap or touch'))
            holes.append(hole)
        if outer in polygons:
            candidate=Polygon(polygons[outer].exterior.coords,[h.exterior.coords for h in holes])
            if not candidate.is_valid or candidate.area <= tol*tol:
                hole_errors.append(dict(outer=outer,reason='Invalid composite polygon'))
            else: composed[outer]=candidate
    result('hole_map',hole_errors)
    # Do not use a partly invalid association to fabricate empty road areas.
    if not hole_errors:
        buildings=[(h,composed[h]) for h,_ in buildings if h not in owners]
        roads=[composed[h] for h in polygons if role(h)=='road' and h not in owners]
    if layers.get('frame'):
        frame_lines=[line for _,layer,line in lines if layer in layers['frame']]
        frame_issues=[]
        if not frame_lines:
            frame_issues.append('No frame linework')
        else:
            faces,cuts,dangles,invalid_rings=polygonize_full(unary_union(frame_lines))
            parts=list(faces.geoms)
            if len(parts)!=1 or not cuts.is_empty or not dangles.is_empty or not invalid_rings.is_empty:
                frame_issues.append('Frame must form one polygon without extra unclosed or internal lines')
            else:
                frame=parts[0]
                rectangle=frame.minimum_rotated_rectangle
                if not frame.is_valid or frame.area <= tol*tol or len(frame.interiors) or frame.symmetric_difference(rectangle).area > tol*frame.length:
                    frame_issues.append('Frame is not a valid rectangle within tolerance')
        result('frame_rectangle',frame_issues)
    else: record('frame_rectangle','not_run','No frame layers configured')
    result('supported_entities', unsupported)
    result('valid_2d', invalid)
    result('planar_z', zbad)
    result('building_closed', open_buildings)
    if road_layers: result('road_closed', open_roads)
    # Spatial index avoids quadratic work on flattened curves. Positive length
    # overlaps are duplicate portions even where vertex segmentation differs.
    from shapely.strtree import STRtree
    if segments:
        tree = STRtree([s for _,s in segments])
        for i,(h,s) in enumerate(segments):
            for j in tree.query(s):
                if j <= i: continue
                overlap = s.intersection(segments[j][1]).length
                if overlap > tol:
                    same_layer = by_handle[h].dxf.layer == by_handle[segments[j][0]].dxf.layer
                    target = duplicates if same_layer else cross_overlaps
                    if len(target) < 100: target.append(dict(handles=[h,segments[j][0]], layers=[by_handle[h].dxf.layer,by_handle[segments[j][0]].dxf.layer], overlap=overlap))
                    if len(duplicates) >= 100: break
            if len(duplicates) >= 100: break
    result('duplicate_segments', duplicates)
    record('cross_layer_overlap','warning' if cross_overlaps else 'pass','Cross-layer overlapping segments require visual review; not automatically removed.',issues=cross_overlaps)
    building_conflicts=[]
    if buildings:
        bt=STRtree([p for _,p in buildings])
        for i,(h,p) in enumerate(buildings):
            for j in bt.query(p):
                if j <= i: continue
                area=p.intersection(buildings[j][1]).area
                if area > tol*tol: building_conflicts.append(dict(handles=[h,buildings[j][0]],overlap_area=area))
    result('building_overlap',building_conflicts)
    road = unary_union(roads) if roads else None
    if road is None:
        record('road_intrusion','not_run','No configured valid closed road surfaces; no road network coverage.')
    else:
        result('road_intrusion', [dict(handle=h, overlap_area=p.intersection(road).area) for h,p in buildings if p.intersection(road).area > tol*tol])
    for c in cfg.get('road_connections',[]):
        if road is None:
            record('connection:'+c['id'],'pending','No road surfaces'); continue
        components = [road] if road.geom_type == 'Polygon' else list(road.geoms)
        snap = float(c['max_snap'])
        if not math.isfinite(snap) or snap < 0: raise ValueError('max_snap must be finite nonnegative')
        ok = any(p.area > tol*tol and p.distance(Point(c['point_a'])) <= snap and p.distance(Point(c['point_b'])) <= snap for p in components)
        record('connection:'+c['id'],'pass' if ok else 'fail','Same positive-area polygon component; point-only contacts do not connect.')
    for w in cfg.get('width_probes',[]):
        if road is None:
            record('width:'+w['id'],'pending','No road surfaces'); continue
        probe = LineString(w['segment'])
        measured = probe.intersection(road).length
        wt = float(w['tolerance']); expected_width = float(w['expected'])
        if not math.isfinite(wt) or wt < 0 or not math.isfinite(expected_width) or expected_width <= 0 or probe.length <= tol: raise ValueError('Invalid width probe')
        record('width:'+w['id'], 'pass' if abs(measured-expected_width) <= wt else 'fail', 'Constructed road cross-section length, not an independent survey width.', measured=measured)
    for c in cfg.get('control_points',[]):
        choices = [l for _,layer,l in lines if layer == c['layer']]
        limit = float(c['max_distance'])
        if not math.isfinite(limit) or limit < 0: raise ValueError('max_distance must be finite nonnegative')
        distance = min((Point(c['point']).distance(l) for l in choices), default=None)
        ok = bool(c.get('source')) and distance is not None and distance <= limit
        record('control:'+c['id'],'pass' if ok else 'fail', 'Independent observation distance to CAD line; source provenance is declarative.', measured=distance, source=c.get('source'))
    frozen = cfg.get('frozen_handles',[])
    if frozen:
        failures=[]
        baseline_path = cfg.get('baseline')
        if not baseline_path or not (project_path.parent / baseline_path).is_file(): failures.append('Missing baseline')
        else:
            baseline=ezdxf.readfile(project_path.parent / baseline_path)
            originals={e.dxf.handle:e for e in baseline.modelspace()}
            if baseline.units != doc.units: failures.append('INSUNITS changed')
            for h in frozen:
                if h not in originals or h not in by_handle or fingerprint(originals[h]) != fingerprint(by_handle[h]): failures.append(h)
        result('frozen_geometry', failures)
    else: record('frozen_geometry','not_run','No frozen_handles configured')
    geometry_pass = not any(c['status'] in {'fail','pending'} for c in checks)
    required_reviews = sorted(set(cfg.get('required_reviews',[])) | {'cad_visual','road_semantics'})
    review = cfg.get('review',{})
    for stage in required_reviews:
        r = review.get(stage,{})
        evidence = r.get('evidence')
        exists = isinstance(evidence,str) and bool(evidence) and (project_path.parent/evidence).is_file()
        ok = r.get('status') == 'pass' and exists and r.get('dxf_sha256') == digest and isinstance(r.get('reviewer'),str) and bool(r['reviewer'].strip())
        record('review:'+stage, 'pass' if ok else ('fail' if r.get('status')=='fail' else 'pending'), 'Documented review declaration; file existence and current DXF hash checked, contents not automatically authenticated.', evidence=evidence)
    delivery_ready = geometry_pass and all(c['status']=='pass' for c in checks if c['id'].startswith('review:'))
    return dict(schema_version=1, input=str(dxf_path.resolve()), dxf_sha256=digest, geometry_pass=geometry_pass, delivery_ready=delivery_ready, checks=checks, issues=[c for c in checks if c['status'] in {'fail','pending','warning'}], coverage=dict(modelspace_entities=len(ents), supported_types=sorted(SUPPORTED), road_surfaces=len(roads), road_network='configured probes only' if roads else 'not_run', curve_flattening_distance=tol/4, duplicate_results_capped_at=100), parameters=cfg)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input',type=Path)
    parser.add_argument('--project',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--geometry-only',action='store_true')
    args=parser.parse_args()
    protected = {args.input.resolve(), args.project.resolve()}
    try:
        config = json.loads(args.project.read_text(encoding='utf-8-sig'))
        if config.get('baseline'): protected.add((args.project.parent/config['baseline']).resolve())
        for review in config.get('review',{}).values():
            if review.get('evidence'): protected.add((args.project.parent/review['evidence']).resolve())
    except (OSError, ValueError, TypeError):
        pass
    if args.report.resolve() in protected or args.report.suffix.lower() in {'.dxf','.dwg'}:
        parser.error('Report must not overwrite CAD or project input')
    try: report=check(args.input,args.project)
    except Exception as exc:
        report=dict(schema_version=1,geometry_pass=False,delivery_ready=False,checks=[dict(id='input_error',status='fail',message=str(exc))])
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('geometry_pass','delivery_ready')}))
    return 0 if report['geometry_pass' if args.geometry_only else 'delivery_ready'] else 1

if __name__ == '__main__': raise SystemExit(main())
