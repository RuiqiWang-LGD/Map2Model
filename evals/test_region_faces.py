"""Synthetic regional gate regressions: valid face, thin neck, tiny face."""
from pathlib import Path
import sys,tempfile,hashlib
import ezdxf
from shapely.geometry import box,Polygon
from shapely.ops import unary_union
sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
from check_region_faces import audit
def drawing(p,width,triangle=False):
 d=ezdxf.new();m=d.modelspace()
 road=unary_union([box(10,10,35,50),box(65,10,90,50),box(35,30-width/2,65,30+width/2)])
 for poly in [box(0,0,100,80),road]+([Polygon([(45,60),(46,60),(45,61)])] if triangle else []):
  m.add_lwpolyline([(x,80-y) for x,y in list(poly.exterior.coords)[:-1]],close=True)
 d.saveas(p)
with tempfile.TemporaryDirectory() as t:
 p=Path(t);(p/'observation.txt').write_text('Independent synthetic corridor specification')
 cfg={'height':80,'sources':[{'path':'observation.txt','sha256':hashlib.sha256((p/'observation.txt').read_bytes()).hexdigest()}],'regions':[{'id':'test','bounds':[0,0,100,80],'source':'Synthetic source specifies full-width corridor, no tiny islands','min_face_area':2,'min_edge':.5,'groups':[{'id':'road','points':[[20,30],[80,30]],'clearance':2}],'distinct':['road'],'exceptions':[]}]}
 for name,width,tri,expected in [('wide',8,False,'pass'),('neck',2,False,'fail'),('sliver',8,True,'fail')]:
  f=p/(name+'.dxf');drawing(f,width,tri);r=audit(f,cfg,p);assert r['status']==expected,(name,r)
 print('3 regional regressions passed')
