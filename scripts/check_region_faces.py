"""Read-only regional face/clearance gate, supplemental to check_visible_plan.
Contract: {height,sources:[{path,sha256}],regions:[{id,bounds:[x0,y0,x1,y1],
source,min_face_area,min_edge,groups:[{id,points:[[x,y],...],clearance:radius}],
distinct:[group IDs],exceptions:[{kind:'small_face'|'short_edge',point:[x,y],
max_distance:number,reason:string}]}]}. Coordinates image-down in drawing units.
Clearance is erosion radius, i.e. half the minimum usable corridor width.
No default dimensional thresholds and no geometry repair are performed.
"""
import argparse,json,hashlib,math
from pathlib import Path
import ezdxf
from shapely.geometry import LineString,Point,box
from shapely.ops import polygonize_full
from shapely.strtree import STRtree
def read(path,height):
 segs=[]
 for e in ezdxf.readfile(path).modelspace():
  if e.dxftype()=='LINE':pts=[(v.x,height-v.y) for v in [e.dxf.start,e.dxf.end]]
  elif e.dxftype()=='LWPOLYLINE':
   if any(v[4]!=0 for v in e.get_points()):raise ValueError('Bulges unsupported; use separate appropriate curve audit')
   pts=[(v[0],height-v[1]) for v in e.get_points()]
   if e.closed:pts.append(pts[0])
  else:raise ValueError('Unsupported entity '+e.dxftype())
  segs.extend(LineString([a,b]) for a,b in zip(pts,pts[1:]))
 faces,cuts,dangles,invalid=polygonize_full(segs)
 return segs,list(faces.geoms),cuts.length+dangles.length+invalid.length
def audit(dxf,contract,base):
 for row in contract['sources']:
  src=base/row['path']
  if hashlib.sha256(src.read_bytes()).hexdigest()!=row['sha256']:raise ValueError('Source hash mismatch')
 if not contract['sources'] or not contract['regions']:raise ValueError('Independent source and regions required')
 segs,faces,residue=read(dxf,contract['height']);tree=STRtree(faces);stree=STRtree(segs);rows=[];issues=[]
 if residue>1e-7:issues.append({'code':'raw_residue','length':residue})
 for r in contract['regions']:
  if not r.get('source'):raise ValueError('Region source evidence required')
  for k in ('min_face_area','min_edge'):
   if not math.isfinite(r[k]) or r[k]<=0:raise ValueError('Positive finite '+k+' required')
  roi=box(*r['bounds']);groups={};bad=[]
  for g in r['groups']:
   if len(g['points'])<2 or not math.isfinite(g['clearance']) or g['clearance']<0:raise ValueError('Invalid group')
   pts=[Point(v) for v in g['points']]
   ids=[[int(i) for i in tree.query(pt,predicate='within')] for pt in pts]
   same=all(len(v)==1 for v in ids) and len({v[0] for v in ids})==1
   clear=False
   if same:
    core=faces[ids[0][0]].buffer(-g['clearance'])
    parts=[core] if core.geom_type=='Polygon' else list(getattr(core,'geoms',[]))
    clear=any(all(q.covers(pt) for pt in pts) for q in parts)
   groups[g['id']]={'faces':ids,'same_face':same,'clearance_pass':clear,'radius':g['clearance']}
   if not same or not clear:bad.append({'code':'region_clearance','group':g['id'],'same_face':same,'clearance_pass':clear})
  distinct=[groups[k]['faces'][0] for k in r['distinct']]
  if any(len(x)!=1 for x in distinct) or len({x[0] for x in distinct if x})!=len(distinct):bad.append({'code':'region_leak'})
  candidates=[]
  for i in tree.query(roi):
   f=faces[i]
   if roi.covers(f.representative_point()) and f.area<r['min_face_area']:
    candidates.append({'kind':'small_face','point':list(f.representative_point().coords[0]),'area':f.area,'face':int(i)})
  for i in stree.query(roi):
   s=segs[i]
   if roi.contains(s.interpolate(.5,normalized=True)) and s.length<r['min_edge']:
    candidates.append({'kind':'short_edge','point':list(s.interpolate(.5,normalized=True).coords[0]),'length':s.length})
  for c in candidates:
   hits=[e for e in r.get('exceptions',[]) if e['kind']==c['kind'] and e.get('reason') and Point(c['point']).distance(Point(e['point']))<=e['max_distance']]
   c['disposition']='reviewed_exception' if hits else 'unresolved'
   if hits:c['reason']=hits[0]['reason']
  unresolved=[c for c in candidates if c['disposition']=='unresolved']
  if unresolved:bad.append({'code':'unresolved_regional_fragments','count':len(unresolved)})
  rows.append({'id':r['id'],'groups':groups,'candidates':candidates,'issues':bad})
  issues.extend(dict(region=r['id'],**v) for v in bad)
 return {'status':'fail' if issues else 'pass','dxf_sha256':hashlib.sha256(Path(dxf).read_bytes()).hexdigest(),'regions':rows,'issues':issues,'native_sketchup':'not_run','limits':'Configured regions and source probes only. Eroded-face connectivity is a planar clearance proxy, not native SU behavior.'}
def main():
 ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('dxf',type=Path);ap.add_argument('--contract',type=Path,required=True);ap.add_argument('--report',type=Path,required=True);a=ap.parse_args()
 cfg=json.loads(a.contract.read_text(encoding='utf-8-sig'))
 inputs=[a.dxf,a.contract,*[a.contract.parent/r['path'] for r in cfg['sources']]]
 if a.report.resolve() in [p.resolve() for p in inputs] or (a.report.exists() and any(a.report.samefile(p) for p in inputs)):raise ValueError('Report may not overwrite inputs')
 report=audit(a.dxf,cfg,a.contract.parent);report['contract_sha256']=hashlib.sha256(a.contract.read_bytes()).hexdigest()
 a.report.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8');print(json.dumps({'status':report['status'],'issues':report['issues']}));return report['status']!='pass'
if __name__=='__main__':raise SystemExit(main())
