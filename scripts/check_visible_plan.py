"""Audit ALL visible flat CAD lines against independent source observations.

No input geometry repair. Coordinate contract: x from left, y from top in CAD
units; native DXF x=x, y=height-y. Sources and expected probes must be frozen
before examining candidate outputs. This is not native SketchUp UI validation.
"""
from pathlib import Path
import json, hashlib, argparse, math, sys
import ezdxf
from shapely.geometry import LineString,Point,Polygon,box,shape
from shapely.ops import polygonize_full,unary_union
from shapely.strtree import STRtree

def validate_contract(cfg,base):
 required={'schema_version','width','height','units','sources','same_face_groups','distinct_groups','protected_regions','single_boundaries','width_probes','boundary_sawtooth','minimum_face_area','minimum_native_edge','tolerance'}
 if not required<=cfg.keys() or cfg['schema_version']!=1:raise ValueError('Incomplete source contract/schema_version')
 if not cfg['sources'] or not cfg['same_face_groups'] or not cfg['protected_regions']:raise ValueError('Sources, independent same-face observations and protected road footprints are required')
 for key in ('width','height','minimum_face_area','minimum_native_edge','tolerance'):
  if not isinstance(cfg[key],(int,float)) or not math.isfinite(cfg[key]) or cfg[key]<=0:raise ValueError('Invalid '+key)
 if cfg['minimum_native_edge']<=cfg['tolerance']:raise ValueError('Numeric tolerance must be smaller than minimum meaningful edge')
 for row in cfg['sources']:
  p=Path(row['path']);p=p if p.is_absolute() else base/p
  if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=row['sha256']:raise ValueError('Source file missing or hash mismatch: '+str(p))
 for key,pts in cfg['same_face_groups'].items():
  if len(pts)<2:raise ValueError('A same-face group needs at least two independent points: '+key)
  if any(len(p)!=2 or not all(isinstance(v,(int,float)) and math.isfinite(v) for v in p) for p in pts):raise ValueError('Invalid source point')
 if any(k not in cfg['same_face_groups'] for k in cfg['distinct_groups']):raise ValueError('Unknown distinct group')
 if not cfg['width_probes']:raise ValueError('At least one independent road-width probe is required')
 settings=cfg['boundary_sawtooth']
 if not isinstance(settings['roles'],list) or not settings['roles'] or any(not isinstance(v,str) or not v.strip() for v in settings['roles']):raise ValueError('Sawtooth role layer patterns must not be empty')
 for key in ('short_length','consecutive_alternating_turns','minimum_boundary_edges'):
  if not isinstance(settings[key],(int,float)) or not math.isfinite(settings[key]) or settings[key]<=0:raise ValueError('Invalid sawtooth setting '+key)
 for row in cfg['protected_regions']:
  p=shape(row['geometry'])
  if p.geom_type!='Polygon' or not p.is_valid or p.area<=0 or p.buffer(-row.get('inset',0)).is_empty:raise ValueError('Invalid protected source polygon')
  if not row.get('evidence'):raise ValueError('Protected corridor needs independent source evidence')
 for row in cfg['width_probes']:
  if not row.get('evidence'):raise ValueError('Width probes need source evidence')
  if not all(isinstance(row[k],(int,float)) and math.isfinite(row[k]) for k in ('expected','tolerance')) or row['expected']<=0 or row['tolerance']<0:raise ValueError('Invalid width expectation/tolerance')
  if any(len(p)!=2 or not all(isinstance(v,(int,float)) and math.isfinite(v) for v in p) for p in [row['point'],*row['segment']]):raise ValueError('Invalid width probe coordinates')
  ray=LineString(row['segment'])
  if not ray.is_valid or ray.length<=0:raise ValueError('Width probe must have positive length')
  if ray.distance(Point(row['point']))>cfg['tolerance']:raise ValueError('Width probe source point must lie on its segment')

def audit(path,manifest=None,contract=None):
 cfg=contract
 if cfg is None:raise ValueError("An independent source contract is required")
 H=cfg['height']
 eps=cfg['tolerance'];area_eps=cfg.get('area_tolerance',eps*10)
 doc=ezdxf.readfile(path);lines=[];owners=[];entitypts={};issue=[];closed=0
 if doc.units!=cfg['units']:issue.append({'code':'units_mismatch','expected':cfg['units'],'actual':doc.units})
 for e in doc.modelspace():
  z=[]
  if e.dxftype()=='LINE':
   pts=[(float(v.x),H-float(v.y)) for v in [e.dxf.start,e.dxf.end]];z=[float(e.dxf.start.z),float(e.dxf.end.z)]
  elif e.dxftype()=='LWPOLYLINE':
   pts=[(float(p[0]),H-float(p[1])) for p in e.get_points()]
   if any(p[4]!=0 for p in e.get_points()):issue.append({'code':'unsupported_bulge','handle':e.dxf.handle})
   if e.closed:pts.append(pts[0]);closed+=1
   z=[float(e.dxf.elevation)]
   if float(e.dxf.const_width)!=0 or any(p[2]!=0 or p[3]!=0 for p in e.get_points()):issue.append({'code':'unsupported_visible_width','handle':e.dxf.handle})
  else:issue.append({'code':'unsupported_entity','handle':e.dxf.handle});continue
  ext=tuple(e.dxf.get('extrusion',(0,0,1)));thick=float(e.dxf.get('thickness',0))
  if any(not math.isfinite(v) for v in [*z,*ext,thick,*[v for p in pts for v in p]]):
   issue.append({'code':'nonfinite_geometry','handle':e.dxf.handle});continue
  if any(abs(v)>1e-8 for v in z) or ext!=(0.,0.,1.) or abs(thick)>1e-8:issue.append({'code':'nonplanar','handle':e.dxf.handle})
  entitypts[e.dxf.handle]=(pts,e.dxf.layer)
  for a,b in zip(pts,pts[1:]):
   if a==b:issue.append({'code':'zero_length','handle':e.dxf.handle});continue
   lines.append(LineString([a,b]));owners.append(e.dxf.handle)
 for pattern in cfg['boundary_sawtooth']['roles']:
  if not any(pattern in lay for _,lay in entitypts.values()):issue.append({'code':'configured_boundary_role_not_found','pattern':pattern})
 tree=STRtree(lines);crossings=[];duplicates=[]
 for i,l in enumerate(lines):
  for j0 in tree.query(l,predicate='intersects'):
   j=int(j0)
   if j<=i:continue
   inter=l.intersection(lines[j])
   if inter.length>eps:duplicates.append([owners[i],owners[j],inter.length])
   elif inter.geom_type=='Point':
    if any(min(Point(s.coords[0]).distance(inter),Point(s.coords[-1]).distance(inter))>eps for s in [l,lines[j]]):crossings.append([owners[i],owners[j],list(inter.coords[0])])
 if duplicates:issue.append({'code':'duplicate_visible_boundaries','count':len(duplicates),'examples':duplicates[:12]})
 if crossings:issue.append({'code':'crossing_without_native_endpoint','count':len(crossings),'examples':crossings[:12]})
 fs,cuts,dangles,invalid=polygonize_full(lines);faces=list(fs.geoms)
 if cuts.length+dangles.length+invalid.length>eps:issue.append({'code':'raw_linework_residue','cuts':cuts.length,'dangles':dangles.length,'invalid':invalid.length})
 ftree=STRtree(faces)
 def face_at(pt):return [int(i) for i in ftree.query(Point(pt),predicate='within')]
 # STRtree predicate is predicate(input, tree geometry): point within polygon.
 groups={}
 for ident,pts in cfg['same_face_groups'].items():
  found=[face_at(p) for p in pts];ids=[v[0] for v in found if len(v)==1]
  ok=len(ids)==len(pts) and len(set(ids))==1
  groups[ident]={'pass':ok,'faces':found,'points':pts}
  if not ok:issue.append({'code':'source_same_face_group_split','id':ident,'faces':found})
 selected=[groups[n]['faces'][0][0] for n in cfg['distinct_groups'] if len(groups[n]['faces'][0])==1]
 if len(selected)!=len(cfg['distinct_groups']) or len(set(selected))!=len(selected):issue.append({'code':'source_regions_leak_or_merge'})
 # Independent deck geometries are old source-derived guide envelopes, not new
 # output edges. Exclude a 3 px uncertainty band but NEVER ignore lower layers.
 if 'protected_regions' in cfg:decks=[shape(f['geometry']).buffer(-f.get('inset',0)) for f in cfg['protected_regions']]
 else:
  src=json.loads(Path(cfg['protected_deck_source']).read_text(encoding='utf-8'))
  decks=[shape(f['geometry']).buffer(-cfg['protected_deck_inset']) for f in src['features'] if f['class']==5]
 intrusion=[]
 for n,deck in enumerate(decks,1):
  hit=[(owners[i],s.intersection(deck).length) for i,s in enumerate(lines) if s.intersects(deck) and s.intersection(deck).length>eps]
  intrusion.append({'deck':n,'total_length':sum(x[1] for x in hit),'segments':len(hit),'examples':hit[:10]})
 if any(x['total_length']>eps for x in intrusion):issue.append({'code':'primary_road_interior_lines','decks':intrusion})
 boundary=[]
 for probe in cfg['single_boundaries']:
  ray=LineString(probe['segment']);points=[]
  for i in tree.query(ray,predicate='intersects'):
   g=ray.intersection(lines[int(i)])
   if g.geom_type=='Point':
    t=ray.project(g)
    if not any(abs(t-q)<eps for q in points):points.append(t)
  ok=len(points)==probe['expected'];boundary.append(dict(id=probe['id'],count=len(points),pass_check=ok))
  if not ok:issue.append({'code':'shore_boundary_multiplicity','id':probe['id'],'count':len(points),'expected':probe['expected']})
 # Run-based oscillation, not a ban on every legitimate short footprint wing.
 width_results=[]
 for probe in cfg.get('width_probes',[]):
  ray=LineString(probe['segment']);point=Point(probe['point']);ids=face_at(probe['point']);measured=None;reason=None
  if ray.distance(point)>eps:reason='source_point_off_probe_segment'
  elif len(ids)!=1:reason='source_point_not_in_unique_face'
  else:
   # A U-shaped face can intersect the ray in several separate intervals.
   # Measure the one containing the source point, never their summed length.
   pending=[faces[ids[0]].intersection(ray)];intervals=[]
   while pending:
    part=pending.pop()
    if part.geom_type=='LineString' and part.length>eps:intervals.append(part)
    elif hasattr(part,'geoms'):pending.extend(part.geoms)
   selected=[part for part in intervals if part.distance(point)<=eps]
   if len(selected)==1:measured=selected[0].length
   else:reason='source_point_not_in_unique_width_interval'
  ok=measured is not None and abs(measured-probe['expected'])<=probe['tolerance']
  width_results.append(dict(id=probe['id'],expected=probe['expected'],measured=measured,pass_check=ok,reason=reason))
  if not ok:issue.append({'code':'source_road_width','id':probe['id'],'measured':measured,'expected':probe['expected'],'reason':reason})
 saw=[];short=sum(s.length<cfg['minimum_native_edge'] for s in lines);settings=cfg['boundary_sawtooth']
 # Follow the rings from the unmodified raw polygonization, rather than DXF
 # entity boundaries: splitting a toothed edge into LINEs must not hide it.
 # Exact endpoint keys retain each ring segment's actual exported layer role.
 def edge_key(a,b):return tuple(sorted((tuple(a),tuple(b))))
 edge_owners={}
 for s,h in zip(lines,owners):edge_owners.setdefault(edge_key(s.coords[0],s.coords[-1]),set()).add(h)
 seen_rings=set()
 for face_id,face in enumerate(faces):
  for ring_id,ring in enumerate([face.exterior,*face.interiors]):
   pts=list(ring.coords)[:-1];n=len(pts)
   keys=[edge_key(pts[i],pts[(i+1)%n]) for i in range(n)]
   signature=frozenset(keys)
   if signature in seen_rings:continue
   seen_rings.add(signature)
   if any(k not in edge_owners for k in keys):
    issue.append({'code':'unmatched_raw_face_boundary','face':face_id,'ring':ring_id});continue
   role_edges=[any(pattern in entitypts[h][1] for h in edge_owners[k] for pattern in settings['roles']) for k in keys]
   if n<settings['minimum_boundary_edges'] or not any(role_edges):continue
   run=0;maxrun=0;last_sign=0
   # Two traversals cover a run across the arbitrary start of a closed ring;
   # cap it at the number of actual vertices so no turn is counted twice.
   for step in range(n*2):
    i=step%n;a,b,c=pts[(i-1)%n],pts[i],pts[(i+1)%n]
    u=(b[0]-a[0],b[1]-a[1]);v=(c[0]-b[0],c[1]-b[1])
    angle=math.degrees(math.atan2(u[0]*v[1]-u[1]*v[0],u[0]*v[0]+u[1]*v[1]));sign=1 if angle>0 else -1
    if role_edges[(i-1)%n] and role_edges[i] and max(math.hypot(*u),math.hypot(*v))<settings['short_length'] and 15<abs(angle)<165:
     run=min(run+1,n) if sign!=last_sign else 1;last_sign=sign
    else:run=0;last_sign=0
    maxrun=max(maxrun,run)
   if maxrun>=settings['consecutive_alternating_turns']:
    handles=sorted({h for k in keys for h in edge_owners[k]})
    saw.append({'face':face_id,'ring':ring_id,'handles':handles,'layers':sorted({entitypts[h][1] for h in handles}),'alternating_turn_run':maxrun})
 if saw:issue.append({'code':'visible_boundary_sawtooth','count':len(saw),'examples':saw[:20]})
 if short:issue.append({'code':'sub_resolution_native_edges','count':short,'threshold':cfg.get('minimum_native_edge',.15)})
 slivers=[p.area for p in faces if p.area<cfg['minimum_face_area']]
 if slivers:issue.append({'code':'sub_resolution_faces','count':len(slivers),'areas':slivers[:20]})
 # Coverage only measures the graph after raw polygonization, without repairing
 # that graph. Expected semantic surface comparison is supplemental, not proof.
 coverage=unary_union(faces).symmetric_difference(box(0,0,cfg['width'],H)).area
 if coverage>area_eps:issue.append({'code':'frame_face_coverage','area':coverage})
 correspondence=[]
 if manifest:
  expected=json.loads(Path(manifest).read_text(encoding='utf-8'))['surfaces']
  for row in expected:
   p=shape(row['geometry']);ids=face_at(p.representative_point());match=ids[0] if len(ids)==1 else None
   err=p.symmetric_difference(faces[match]).area if match is not None else None
   correspondence.append({'id':row['id'],'role':row['role'],'face':match,'error_area':err})
  bad=[r for r in correspondence if r['error_area'] is None or r['error_area']>area_eps]
  if bad:issue.append({'code':'supplemental_face_correspondence','count':len(bad),'examples':bad[:12]})
 report={'dxf':str(path),'sha256':hashlib.sha256(Path(path).read_bytes()).hexdigest(),'status':'pass' if not issue else 'fail','issues':issue,'metrics':{'entities':len(entitypts),'native_closed_entities':closed,'segments':len(lines),'raw_faces':len(faces),'raw_cuts':cuts.length,'raw_dangles':dangles.length,'raw_invalid':invalid.length,'coverage_error_area':coverage,'min_edge':min((s.length for s in lines),default=None)},'source_contract':cfg,'source_face_groups':groups,'primary_deck_intrusion':intrusion,'shore_crossings':boundary,'width_probes':width_results,'correspondence':correspondence,'native_sketchup':'not_run'}
 return report,faces

def main():
 ap=argparse.ArgumentParser(description=__doc__)
 ap.add_argument('dxf',type=Path);ap.add_argument('--contract',type=Path,required=True)
 ap.add_argument('--manifest',type=Path,help='Supplemental expected surfaces, never a substitute for source contract')
 ap.add_argument('--report',type=Path,required=True);args=ap.parse_args()
 try:
  inputs=[args.dxf,args.contract]+([args.manifest] if args.manifest else [])
  cfg=json.loads(args.contract.read_text(encoding='utf-8-sig'));validate_contract(cfg,args.contract.parent)
  sources=[(Path(r['path']) if Path(r['path']).is_absolute() else args.contract.parent/Path(r['path'])) for r in cfg['sources']]
  derived=[args.dxf]+([args.manifest] if args.manifest else [])
  if any(s.resolve()==p.resolve() or (s.exists() and p.exists() and s.samefile(p)) for s in sources for p in derived):raise ValueError('Independent sources must not alias candidate DXF or derived manifest')
  inputs += sources
  if args.report.resolve() in [p.resolve() for p in inputs] or (args.report.exists() and any(p.exists() and args.report.samefile(p) for p in inputs)):raise ValueError('Report must not overwrite an input or source, including a hard-link alias')
  report,_=audit(args.dxf,args.manifest,cfg)
  report['contract_sha256']=hashlib.sha256(args.contract.read_bytes()).hexdigest()
  report['checker_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
  report['manifest_sha256']=hashlib.sha256(args.manifest.read_bytes()).hexdigest() if args.manifest else None
  args.report.parent.mkdir(parents=True,exist_ok=True);args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
  print(json.dumps({'status':report['status'],'metrics':report['metrics'],'issue_codes':[v['code'] for v in report['issues']]},ensure_ascii=False))
  return 0 if report['status']=='pass' else 1
 except (OSError,ValueError,KeyError,TypeError) as exc:
  print('Input/check failure: '+str(exc),file=sys.stderr);return 2
if __name__=='__main__':raise SystemExit(main())
