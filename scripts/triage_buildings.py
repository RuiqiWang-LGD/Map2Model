"""Read-only geometry triage for building review; never changes/classifies roofs.

Many turns, short edges and mixed directions prioritize SOURCE-IMAGE review.
A candidate can be a valid U/L form, roof group or curved building. A rectangle
can still be misclassified paving. No geometry metric authorizes rebuilding.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path

import ezdxf
from shapely.geometry import Polygon


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate(config, manifest):
    fields={'min_vertices','short_edge','short_fraction','axis_angle_degrees','off_axis_fraction'}
    if not isinstance(config,dict) or set(config)!=fields:
        raise ValueError('Specify exactly: '+', '.join(sorted(fields)))
    if type(config['min_vertices']) is not int or config['min_vertices']<4:
        raise ValueError('min_vertices must be an integer >=4')
    for k in fields-{'min_vertices'}:
        if type(config[k]) not in (int,float) or not math.isfinite(config[k]) or config[k]<=0:
            raise ValueError(k+' must be positive and finite')
    if any(config[k]>1 for k in ('short_fraction','off_axis_fraction')) or config['axis_angle_degrees']>=45:
        raise ValueError('Fractions must be <=1 and axis angle tolerance <45 degrees')
    if not isinstance(manifest,list):raise ValueError('manifest must be a list of object rows')
    for r in manifest:
        if not isinstance(r,dict) or any(not isinstance(r.get(k),str) or not r[k].strip() for k in ('id','handle','role')):
            raise ValueError('Every manifest row needs nonempty id, handle, role')
    for field in ('id','handle'):
        if len({r[field] for r in manifest})!=len(manifest):raise ValueError('Duplicate manifest '+field)


def triage(dxf, manifest, config):
    validate(config,manifest)
    path=Path(dxf);doc=ezdxf.readfile(path);entities={e.dxf.handle:e for e in doc.modelspace()}
    objects=[]
    for r in manifest:
        if not r['role'].startswith('BUILDING'):continue
        result={k:r[k] for k in ('id','handle','role')}
        if 'parent' in r:result['parent']=r['parent']
        result.update(status='geometry_deferred',reasons=[],metrics={})
        objects.append(result);e=entities.get(r['handle'])
        if e is None:
            result['reasons']=['missing_handle'];continue
        if e.dxftype()!='LWPOLYLINE' or not e.closed:
            result['reasons']=['requires_native_closed_polygon'];continue
        pts=list(e.get_points('xyseb'));z=float(e.dxf.elevation);thickness=float(e.dxf.get('thickness',0))
        ext=tuple(e.dxf.get('extrusion',(0,0,1)))
        if any(not math.isfinite(float(v)) for p in pts for v in p) or any(not math.isfinite(v) for v in (z,thickness,*ext)):
            result['reasons']=['nonfinite_geometry'];continue
        if z!=0 or thickness!=0 or ext!=(0.,0.,1.):
            result['reasons']=['nonplanar_geometry'];continue
        if e.dxf.const_width!=0 or any(v!=0 for p in pts for v in p[2:]):
            result['reasons']=['native_curve_or_width_keep_for_review'];continue
        if len(pts)<3:
            result['reasons']=['invalid_ring'];continue
        xy=[tuple(p[:2]) for p in pts];g=Polygon(xy)
        if not g.is_valid or g.area<=0:
            result['reasons']=['invalid_ring'];continue
        vec=[(b[0]-a[0],b[1]-a[1]) for a,b in zip(xy,xy[1:]+xy[:1])]
        lengths=[math.hypot(x,y) for x,y in vec]
        if min(lengths)<=0:
            result['reasons']=['zero_length_edge'];continue
        angles=[math.atan2(y,x) for x,y in vec];perimeter=sum(lengths)
        # Fourth harmonic estimates two perpendicular directions without fixing
        # them to world XY. It is a CAD descriptor, not a source roof-axis estimate.
        vx=sum(w*math.cos(4*a) for w,a in zip(lengths,angles))
        vy=sum(w*math.sin(4*a) for w,a in zip(lengths,angles))
        axis=math.atan2(vy,vx)/4
        residual=[abs((a-axis+math.pi/4)%(math.pi/2)-math.pi/4) for a in angles]
        off=sum(w for w,a in zip(lengths,residual) if a>math.radians(config['axis_angle_degrees']))/perimeter
        short=sum(l<config['short_edge'] for l in lengths)/len(lengths)
        result['metrics']=dict(vertices=len(xy),area=g.area,minimum_edge=min(lengths),short_edge_fraction=short,
                               dominant_axis_degrees=math.degrees(axis),axis_coherence=math.hypot(vx,vy)/perimeter,
                               off_axis_perimeter_fraction=off,rectangle_fill=g.area/g.minimum_rotated_rectangle.area,
                               bbox=list(g.bounds))
        reasons=[]
        if len(xy)>=config['min_vertices']:reasons.append('many_turns')
        if len(xy)>=8 and short>=config['short_fraction']:reasons.append('many_short_edges')
        if len(xy)>=12 and off>=config['off_axis_fraction']:reasons.append('mixed_directions')
        result['reasons']=reasons
        result['status']='review_candidate' if reasons else 'no_geometry_trigger'
    objects.sort(key=lambda r:(r['status']!='geometry_deferred',r['status']!='review_candidate',-len(r['reasons']),-r['metrics'].get('vertices',0)))
    return dict(schema_version=1,dxf=str(path.resolve()),dxf_sha256=digest(path),units=doc.units,
                building_count=len(objects),candidate_count=sum(r['status']=='review_candidate' for r in objects),
                deferred_count=sum(r['status']=='geometry_deferred' for r in objects),automatic_edits=0,
                objects=objects,config=config,limits='Manifest-selected building polygons only; geometry triage is neither source classification nor fit accuracy. Every edit requires image evidence and subsequent full topology/baseline checks.')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('dxf',type=Path);p.add_argument('--manifest',required=True,type=Path)
    p.add_argument('--config',required=True,type=Path);p.add_argument('--report',required=True,type=Path)
    p.add_argument('--overwrite',action='store_true');a=p.parse_args()
    try:
        inputs=[a.dxf,a.manifest,a.config]
        if any(a.report.resolve()==f.resolve() or (a.report.exists() and f.exists() and a.report.samefile(f)) for f in inputs):
            raise ValueError('report must not replace an input, including linked aliases')
        if a.report.exists() and not a.overwrite:raise ValueError('report exists; choose new path or --overwrite')
        config=json.loads(a.config.read_text(encoding='utf-8-sig'));manifest=json.loads(a.manifest.read_text(encoding='utf-8-sig'))
        report=triage(a.dxf,manifest,config)
        report.update(manifest_sha256=digest(a.manifest),config_sha256=digest(a.config))
        a.report.parent.mkdir(parents=True,exist_ok=True)
        a.report.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    except Exception as exc:p.exit(2,f'{type(exc).__name__}: {exc}\n')
    print(json.dumps({k:report[k] for k in ('building_count','candidate_count','deferred_count','automatic_edits')}))
    # Review candidates do not mean geometry is wrong; deferred objects do mean
    # this classifier did not evaluate that geometry. Neither is a CAD pass.
    return 1 if report['deferred_count'] else 0


if __name__=='__main__':raise SystemExit(main())
