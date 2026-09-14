"""Read-only native LINE/closed polygonal LWPOLYLINE parent/detail face audit.

No snapping, buffering, union, or re-noding is performed before polygonization.
This checks an explicitly inventoried planar rectangular crop, not image semantics
or SketchUp behavior. Native curves require a separate curve-aware checker.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

import ezdxf
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import polygonize_full, unary_union
from shapely.strtree import STRtree

sys.path.insert(0, str(Path(__file__).resolve().parent))
from compare_dxf import shape, same


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate_config(c):
    required = {'schema_version', 'module', 'units', 'frame', 'tolerances',
                'expected_road_components', 'parents', 'frame_handles', 'details'}
    if not isinstance(c, dict) or not required <= c.keys() or c.keys() - required - {'baseline'}:
        raise ValueError('Missing or unknown face-check configuration fields')
    if c['schema_version'] != 1 or c['module'] not in ('outer', 'detail'):
        raise ValueError('Expected schema_version=1 and module outer/detail')
    if type(c['units']) is not int or not 0 <= c['units'] <= 24:
        raise ValueError('units must be a DXF INSUNITS integer (0 = relative)')
    f = c['frame']
    if not isinstance(f, list) or len(f) != 4 or not all(type(x) in (int,float) and math.isfinite(x) for x in f) or f[0] >= f[2] or f[1] >= f[3]:
        raise ValueError('frame must be finite [xmin,ymin,xmax,ymax]')
    t = c['tolerances']
    if not isinstance(t, dict) or set(t) != {'coordinate', 'area', 'min_edge', 'min_clearance'}:
        raise ValueError('Specify coordinate, area, min_edge and min_clearance tolerances')
    if not all(type(v) in (int,float) and math.isfinite(v) and v > 0 for v in t.values()):
        raise ValueError('Tolerances must be finite and positive')
    if t['coordinate'] >= min(t['min_edge'], t['min_clearance']):
        raise ValueError('Coordinate tolerance must be smaller than edge/clearance thresholds')
    if type(c['expected_road_components']) is not int or c['expected_road_components'] < 1:
        raise ValueError('Set expected_road_components from scene evidence, positive integer')
    for field, keys in [('parents', {'id','handle','clipped'}), ('details', {'id','handle','parent','role'})]:
        if not isinstance(c[field], list):
            raise ValueError(field + ' must be a list')
        for row in c[field]:
            if not isinstance(row, dict) or set(row) != keys:
                raise ValueError('Wrong inventory row fields in ' + field)
            for k in keys - {'clipped'}:
                if not isinstance(row[k], str) or not row[k].strip():
                    raise ValueError('Inventory identifiers/roles/handles must be nonempty strings')
            if field == 'parents' and type(row['clipped']) is not bool:
                raise ValueError('clipped is a source-derived boolean')
    if not isinstance(c['frame_handles'], list) or not all(isinstance(h,str) and h for h in c['frame_handles']):
        raise ValueError('frame_handles must be a list of native LINE handles')
    rows = c['parents'] + c['details']
    ids = [r['id'] for r in rows]
    handles = [r['handle'] for r in rows] + c['frame_handles']
    if len(set(ids)) != len(ids) or len(set(handles)) != len(handles):
        raise ValueError('Inventory IDs and handles must be unique')
    if any(r['parent'] not in {p['id'] for p in c['parents']} for r in c['details']):
        raise ValueError('Every detail must name a listed outer parent')
    if c['module'] == 'outer' and (c['details'] or 'baseline' in c):
        raise ValueError('outer has no detail rows or baseline')
    if c['module'] == 'detail':
        b = c.get('baseline', {})
        if set(b) != {'dxf','sha256'} or not isinstance(b['dxf'],str) or not b['dxf']:
            raise ValueError('detail requires baseline dxf and pinned sha256')
        if not isinstance(b['sha256'], str) or len(b['sha256']) != 64 or any(x not in '0123456789abcdefABCDEF' for x in b['sha256']):
            raise ValueError('baseline sha256 must contain 64 hex characters')


def analyze(doc, c):
    issues = []
    def issue(code, **data):
        issues.append(dict(code=code, **data))
    eps, area_tol, min_edge, gap = (c['tolerances'][k] for k in ('coordinate','area','min_edge','min_clearance'))
    frame = box(*c['frame'])
    rows = c['parents'] + c['details']
    by_handle = {r['handle']:r for r in rows}
    entities = {e.dxf.handle:e for e in doc.modelspace()}
    expected = set(by_handle) | set(c['frame_handles'])
    if set(entities) != expected or not c['parents']:
        issue('inventory_mismatch', missing=sorted(expected-set(entities)), unknown=sorted(set(entities)-expected))
    if doc.units != c['units']:
        issue('units_mismatch', actual=doc.units, expected=c['units'])
    rings, segments, owners, frame_edges = {}, [], [], []
    fatal = False
    for h, e in entities.items():
        kind = e.dxftype()
        if kind not in ('LINE','LWPOLYLINE'):
            issue('unsupported_entity', handle=h, type=kind)
            fatal = True
            continue
        if kind == 'LINE':
            xyz = [tuple(e.dxf.start), tuple(e.dxf.end)]
            xy = [p[:2] for p in xyz]
            z = [p[2] for p in xyz]
            if h not in c['frame_handles']:
                issue('line_not_registered_as_frame', handle=h)
        else:
            points = list(e.get_points('xyseb'))
            xy = [tuple(p[:2]) for p in points]
            z = [float(e.dxf.elevation)]
            if any(not math.isfinite(float(v)) for p in points for v in p):
                issue('nonfinite_geometry', handle=h)
                fatal = True
                continue
            if not e.closed:
                issue('native_not_closed', handle=h)
                fatal = True
            if e.dxf.const_width != 0 or any(v != 0 for p in points for v in p[2:]):
                issue('unsupported_curve_or_width', handle=h)
                fatal = True
            if h not in by_handle:
                issue('ring_not_registered', handle=h)
            if len(xy) < 3:
                issue('invalid_ring', handle=h)
                fatal = True
                continue
            poly = Polygon(xy)
            if not poly.is_valid or poly.area <= area_tol or not poly.exterior.is_simple:
                issue('invalid_ring', handle=h)
                fatal = True
            rings[h] = poly
        if any(not math.isfinite(float(v)) for p in xy for v in p) or any(not math.isfinite(v) for v in z):
            issue('nonfinite_geometry', handle=h)
            fatal = True
            continue
        extrusion = tuple(e.dxf.get('extrusion',(0,0,1)))
        thickness = float(e.dxf.get('thickness',0))
        if (any(not math.isfinite(v) for v in (*extrusion, thickness)) or
                any(abs(v) > eps for v in z) or extrusion != (0.,0.,1.) or abs(thickness) > eps):
            issue('nonplanar_or_extruded', handle=h)
            fatal = True
        pairs = zip(xy, xy[1:]+xy[:1]) if kind == 'LWPOLYLINE' else [(xy[0],xy[1])]
        for a,b in pairs:
            line = LineString([a,b])
            if line.length < min_edge:
                issue('short_native_edge', handle=h, length=line.length)
            if line.length == 0:
                fatal = True
                continue
            segments.append(line)
            owners.append(h)
            if not frame.covers(line):
                issue('outside_frame', handle=h)
            if frame.boundary.covers(line):
                frame_edges.append(line)
            elif kind == 'LINE':
                issue('frame_line_not_on_frame', handle=h)
    metrics = dict(entities=len(entities), parents=len(c['parents']), details=len(c['details']),
                   native_segments=len(segments), minimum_edge=min((s.length for s in segments),default=None))
    if fatal:
        return issues, metrics, []
    tree = STRtree(segments)
    for i,s in enumerate(segments):
        for index in tree.query(s, predicate='intersects'):
            j = int(index)
            if j <= i:
                continue
            q = segments[j]
            inter = s.intersection(q)
            if inter.length > eps:
                issue('overlapping_native_edges', handles=[owners[i],owners[j]], length=inter.length)
            elif inter.geom_type == 'Point':
                if any(min(inter.distance(Point(line.coords[0])), inter.distance(Point(line.coords[-1]))) > eps for line in (s,q)):
                    issue('intersection_without_native_endpoint', handles=[owners[i],owners[j]], point=list(inter.coords[0]))
    # union is used ONLY to measure frame coverage, never to repair the raw graph.
    covered_frame = unary_union(frame_edges)
    if frame.boundary.symmetric_difference(covered_frame).length > eps:
        issue('frame_coverage')
    parents = {r['id']:rings[r['handle']] for r in c['parents'] if r['handle'] in rings}
    for row in c['parents']:
        g = parents.get(row['id'])
        if g is None:
            continue
        contact = g.boundary.intersection(frame.boundary)
        if row['clipped'] and contact.length < min_edge:
            issue('clipped_parent_without_frame_edge', id=row['id'])
        if not row['clipped'] and not contact.is_empty:
            issue('unexpected_parent_frame_contact', id=row['id'])
    pitems = list(parents.items())
    ptree = STRtree([g for _,g in pitems])
    for i,(pid,g) in enumerate(pitems):
        for j0 in ptree.query(g, predicate='intersects'):
            j = int(j0)
            if j > i:
                issue('parents_overlap_or_touch', ids=[pid,pitems[j][0]])
    details = [(r,rings[r['handle']]) for r in c['details'] if r['handle'] in rings]
    for row,g in details:
        p = parents.get(row['parent'])
        if p is None or not p.covers(g):
            issue('detail_outside_parent', id=row['id'], parent=row['parent'])
        elif g.boundary.distance(p.boundary) < gap:
            issue('detail_parent_clearance', id=row['id'], distance=g.boundary.distance(p.boundary))
    dtree = STRtree([g for _,g in details])
    nested = 0
    for i,(row,g) in enumerate(details):
        x0,y0,x1,y1=g.bounds
        for j0 in dtree.query(box(x0-gap,y0-gap,x1+gap,y1+gap)):
            j=int(j0)
            if j <= i:
                continue
            other,q=details[j]
            crossing=g.intersects(q) and not (g.covers(q) or q.covers(g))
            if crossing or g.boundary.distance(q.boundary) < gap:
                issue('crossing_or_touching_details', ids=[row['id'],other['id']])
            elif g.covers(q) or q.covers(g):
                nested += 1
    faces,cuts,dangles,invalid=polygonize_full(segments)
    metrics.update(raw_faces=len(faces.geoms), raw_cuts=cuts.length, raw_dangles=dangles.length,
                   raw_invalid=invalid.length, nested_detail_pairs=nested)
    if not cuts.is_empty or not dangles.is_empty or not invalid.is_empty:
        issue('raw_linework_residue', cuts=cuts.length, dangles=dangles.length, invalid=invalid.length)
    if abs(sum(f.area for f in faces.geoms)-frame.area) > area_tol or any(not frame.covers(f) for f in faces.geoms):
        issue('face_coverage')
    ftree=STRtree(list(faces.geoms))
    claimed=[]
    def equal_poly(a,b):
        return a.symmetric_difference(b).area <= area_tol and a.boundary.hausdorff_distance(b.boundary) <= eps
    for row in rows:
        g=rings.get(row['handle'])
        if g is None:
            issue('missing_native_ring', id=row['id'])
            continue
        found=[int(j) for j in ftree.query(g) if equal_poly(g,Polygon(faces.geoms[int(j)].exterior))]
        if len(found)!=1:
            issue('native_ring_face_bijection', id=row['id'], matching_faces=found)
        else:
            claimed.append(found[0])
    if len(claimed)!=len(set(claimed)):
        issue('face_claimed_twice')
    road=[f for i,f in enumerate(faces.geoms) if i not in set(claimed)]
    metrics['road_components']=len(road)
    metrics['road_holes']=sum(len(f.interiors) for f in road)
    if len(road)!=c['expected_road_components']:
        issue('road_component_count', expected=c['expected_road_components'], actual=len(road))
    if len(faces.geoms)!=len(rows)+c['expected_road_components']:
        issue('face_count', expected=len(rows)+c['expected_road_components'], actual=len(faces.geoms))
    return issues,metrics,road


def inspect(dxf, config, config_dir=None):
    validate_config(config)
    path=Path(dxf).resolve()
    doc=ezdxf.readfile(path)
    issues,metrics,road=analyze(doc,config)
    baseline_sha=None
    if config['module']=='detail':
        bpath=(Path(config_dir or '.')/config['baseline']['dxf']).resolve()
        baseline_sha=digest(bpath)
        if baseline_sha.lower()!=config['baseline']['sha256'].lower():
            issues.append({'code':'baseline_hash_mismatch'})
        base=ezdxf.readfile(bpath)
        bc={k:v for k,v in config.items() if k!='baseline'}
        bc.update(module='outer',details=[])
        base_issues,_,base_road=analyze(base,bc)
        if base_issues:
            issues.append({'code':'invalid_outer_baseline','issues':base_issues})
        before={e.dxf.handle:e for e in base.modelspace()}
        after={e.dxf.handle:e for e in doc.modelspace()}
        for h,e in before.items():
            try:
                unchanged=h in after and same(shape(e),shape(after[h]),config['tolerances']['coordinate'])
            except (ValueError,TypeError):
                unchanged=False
            if not unchanged:
                issues.append({'code':'accepted_geometry_changed','handle':h})
        if base.units!=doc.units:
            issues.append({'code':'accepted_units_changed'})
        expected_new={r['handle'] for r in config['details']}
        if set(after)-set(before)!=expected_new:
            issues.append({'code':'detail_additions_mismatch'})
        eps=config['tolerances']['coordinate']; area=config['tolerances']['area']
        # Compare the road recovered from ALL current entities, not retained layers.
        same_road=(not base_issues and len(road)==len(base_road) and all(
            sum(a.symmetric_difference(b).area<=area and a.boundary.hausdorff_distance(b.boundary)<=eps for b in road)==1
            for a in base_road))
        metrics['road_unchanged']=same_road
        if not same_road:
            issues.append({'code':'road_changed_or_subdivided'})
    return dict(schema_version=1, module=config['module'], geometry_pass=not issues,
                dxf=str(path),sha256=digest(path),baseline_sha256=baseline_sha,
                no_precheck_repairs=True,metrics=metrics,issues=issues,
                limits='Native straight LINE/closed LWPOLYLINE, rectangular planar crop; inventory and thresholds need scene evidence. No image accuracy, curve, DWG-format or SU verdict.')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('dxf',type=Path)
    p.add_argument('--config',required=True,type=Path)
    p.add_argument('--report',required=True,type=Path)
    p.add_argument('--overwrite',action='store_true')
    a=p.parse_args()
    try:
        c=json.loads(a.config.read_text(encoding='utf-8-sig'))
        validate_config(c)
        inputs=[a.dxf.resolve(),a.config.resolve()]
        if 'baseline' in c:
            inputs.append((a.config.parent/c['baseline']['dxf']).resolve())
        if a.report.resolve() in inputs or (a.report.exists() and any(
                source.exists() and a.report.samefile(source) for source in inputs)):
            p.error('report must not replace an input or baseline')
        if a.report.exists() and not a.overwrite:
            p.error('report exists; choose another path or --overwrite')
        result=inspect(a.dxf,c,a.config.parent)
    except Exception as exc:
        p.exit(2,f'{type(exc).__name__}: {exc}\n')
    a.report.parent.mkdir(parents=True,exist_ok=True)
    result['config_sha256']=digest(a.config)
    a.report.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'geometry_pass':result['geometry_pass'],'issues':len(result['issues']),'metrics':result['metrics']}))
    return 0 if result['geometry_pass'] else 1


if __name__=='__main__':
    raise SystemExit(main())
