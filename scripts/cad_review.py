"""Generate source / before / after evidence pages from actual CAD and a fixed affine.

No registration, image editing model, or automatic visual verdict. Output directory
must be new; original inputs and native CAD geometry are read-only.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile

import ezdxf
from ezdxf.path import make_path
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from shapely.geometry import LineString, box

SUPPORTED = {'LINE','LWPOLYLINE','POLYLINE','ARC','CIRCLE'}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def selected_ids(selection, rows, before_dxf, after_dxf):
    if selection is None:
        return [r['id'] for r in rows]
    data=load(selection)
    if not isinstance(data,dict):raise ValueError('selection must be an object')
    if 'dxf_sha256' in data and data['dxf_sha256'] not in (digest(before_dxf),digest(after_dxf)):
        raise ValueError('selection refers to a different CAD revision')
    if 'ids' in data:
        ids=data['ids']
    elif 'objects' in data and isinstance(data['objects'],list):
        if any(not isinstance(r,dict) or r.get('status') not in
               ('review_candidate','geometry_deferred','no_geometry_trigger') for r in data['objects']):
            raise ValueError('invalid triage selection records')
        ids=[r.get('id') for r in data['objects'] if r['status']!='no_geometry_trigger']
    else:raise ValueError('selection requires ids or triage objects')
    if not isinstance(ids,list) or any(not isinstance(v,str) for v in ids) or len(set(ids))!=len(ids):
        raise ValueError('selection IDs must be unique strings')
    if set(ids)-{r['id'] for r in rows}:raise ValueError('selection contains unknown IDs')
    return ids


def entity_points(entities, handle, matrix):
    e=entities.get(handle)
    if e is None:raise ValueError(f'missing actual modelspace handle: {handle}')
    if e.dxftype() not in SUPPORTED or (e.dxftype()=='POLYLINE' and (e.is_polygon_mesh or e.is_poly_face_mesh)):
        raise ValueError(f'unsupported review geometry: {handle} {e.dxftype()}')
    tol=.25/np.linalg.norm(matrix[:2,:2],ord=2)
    points=[]
    for v in make_path(e).flattening(distance=tol,segments=8):
        p=matrix @ [v.x,v.y,1]
        if not np.isfinite(p).all():raise ValueError('nonfinite transformed CAD')
        points.append((float(p[0]),float(p[1])))
    if len(points)<2:raise ValueError(f'degenerate review geometry: {handle}')
    return points


def make_review(before_dxf, after_dxf, manifest_path, source, transform_path,
                selection_path, output_dir, padding=24):
    """Create fresh JPEG pages and index; return index/pages/count paths and count.

    Manifest rows require id/handle/role and may declare before_handle or
    before_handles (empty for additions). Selection is None, an IDs object, or a
    triage report tied to either input DXF. Transform is a source-sized affine;
    padding is image pixels. Invalid/missing selectors raise ValueError before
    publishing. Inputs are read-only; no image accuracy verdict is assigned.
    """
    output_dir=Path(output_dir).resolve()
    if output_dir.exists():raise ValueError('review output directory already exists')
    if type(padding) not in (int,float) or not math.isfinite(padding) or padding<0:
        raise ValueError('padding must be finite nonnegative image pixels')
    inputs={k:Path(v).resolve() for k,v in dict(before_dxf=before_dxf,after_dxf=after_dxf,
             manifest=manifest_path,source=source,transform=transform_path,selection=selection_path).items() if v is not None}
    if any(p.is_relative_to(output_dir) for p in inputs.values()):raise ValueError('output contains input')
    rows=load(manifest_path)
    if not isinstance(rows,list) or any(not isinstance(r,dict) or
        any(not isinstance(r.get(k),str) or not r[k].strip() for k in ('id','handle','role')) for r in rows):
        raise ValueError('manifest needs id, handle, role records')
    if len({r['id'] for r in rows})!=len(rows):raise ValueError('duplicate manifest IDs')
    ids=selected_ids(selection_path,rows,before_dxf,after_dxf)
    matrix_cfg=load(transform_path);matrix=np.asarray(matrix_cfg.get('world_to_image'),dtype=float)
    if matrix.shape!=(3,3) or not np.isfinite(matrix).all() or not np.array_equal(matrix[2],[0,0,1]) or np.linalg.det(matrix[:2,:2])==0:
        raise ValueError('finite invertible world_to_image affine required')
    with Image.open(source) as opened:base=opened.convert('RGB')
    if any(k in matrix_cfg and matrix_cfg[k]!=v for k,v in [('width',base.width),('height',base.height)]):
        raise ValueError('transform dimensions do not match source')
    maps=[{e.dxf.handle:e for e in ezdxf.readfile(p).modelspace()} for p in (before_dxf,after_dxf)]
    by_id={r['id']:r for r in rows};objects=[]
    for key in ids:
        row=by_id[key]
        if 'before_handles' in row:old=row['before_handles']
        elif 'before_handle' in row:old=[] if row['before_handle'] is None else [row['before_handle']]
        else:old=[row['handle']]
        if not isinstance(old,list) or any(not isinstance(h,str) or not h for h in old) or len(set(old))!=len(old):
            raise ValueError(f'invalid before selectors: {key}')
        lines=[[entity_points(maps[0],h,matrix) for h in old], [entity_points(maps[1],row['handle'],matrix)]]
        points=[p for ls in lines for line in ls for p in line]
        x0=max(0,math.floor(min(p[0] for p in points)-padding));y0=max(0,math.floor(min(p[1] for p in points)-padding))
        x1=min(base.width,math.ceil(max(p[0] for p in points)+padding));y1=min(base.height,math.ceil(max(p[1] for p in points)+padding))
        if x0>=x1 or y0>=y1:raise ValueError(f'object outside source image: {key}')
        objects.append(dict(id=key,role=row['role'],handle=row['handle'],before_handles=old,
                            pixel_bounds=[x0,y0,x1,y1],lines=lines,
                            clipped_by_image=any(x<0 or y<0 or x>=base.width or y>=base.height for x,y in points)))
    hashes={k:digest(p) for k,p in inputs.items()}
    output_dir.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.cad-review-',dir=output_dir.parent) as tmp:
        stage=Path(tmp)/'result';stage.mkdir();pages=[]
        try:font=ImageFont.load_default(size=19)
        except TypeError:font=ImageFont.load_default()
        for start in range(0,len(objects),6):
            batch=objects[start:start+6]
            page=Image.new('RGB',(1260,55+len(batch)*305),'#EEF0EA');d=ImageDraw.Draw(page)
            d.text((20,14),'CAD review | Source / Before (yellow) / After (cyan)',font=font,fill='#243D37')
            for row_index,obj in enumerate(batch):
                x0,y0,x1,y1=obj['pixel_bounds'];cropped=base.crop((x0,y0,x1,y1))
                for panel in range(3):
                    tile=cropped.copy();dr=ImageDraw.Draw(tile)
                    if panel:
                        for line in obj['lines'][panel-1]:
                            # Clip actual segments first; clamping un-clipped vertices
                            # would invent an edge along the image boundary.
                            clipped=LineString(line).intersection(box(x0,y0,x1,y1))
                            parts=list(clipped.geoms) if hasattr(clipped,'geoms') else [clipped]
                            for segment in parts:
                                if segment.geom_type not in ('LineString','LinearRing') or segment.is_empty:continue
                                points=[(min(tile.width-1,max(0,x-x0)),min(tile.height-1,max(0,y-y0))) for x,y in segment.coords]
                                dr.line(points,fill='#FFFF00' if panel==1 else '#00FFFF',width=2)
                    scale=min(400/tile.width,258/tile.height)
                    tile=tile.resize((max(1,round(tile.width*scale)),max(1,round(tile.height*scale))),Image.Resampling.LANCZOS)
                    tx=20+panel*420+(400-tile.width)//2;ty=88+row_index*305+(258-tile.height)//2
                    page.paste(tile,(tx,ty));d.text((20+panel*420,59+row_index*305),obj['id'],font=font,fill='#243D37')
                obj['page']=f'review-{start//6+1:03}.jpg';obj['row']=row_index+1
            name=f'review-{start//6+1:03}.jpg';page.save(stage/name,quality=94,subsampling=0);pages.append(str(output_dir/name))
        index=dict(schema_version=1,count=len(objects),objects=[{k:v for k,v in o.items() if k!='lines'} for o in objects],
                   pages=pages,inputs={k:str(p) for k,p in inputs.items()},input_sha256=hashes,
                   world_to_image=matrix.tolist(),curve_tolerance_px=.25,visual_validation='pending')
        (stage/'index.json').write_text(json.dumps(index,ensure_ascii=False,indent=2),encoding='utf-8')
        if any(digest(inputs[k])!=v for k,v in hashes.items()):raise ValueError('input changed during review rendering')
        if output_dir.exists():raise ValueError('output appeared while rendering')
        os.rename(stage,output_dir)
    return dict(index=str(output_dir/'index.json'),pages=pages,count=len(objects))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('before-dxf','after-dxf','manifest','source','transform','output-dir'):
        p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--selection',type=Path);p.add_argument('--padding',type=float,default=24)
    a=p.parse_args()
    try:print(json.dumps(make_review(a.before_dxf,a.after_dxf,a.manifest,a.source,a.transform,a.selection,a.output_dir,a.padding),ensure_ascii=False))
    except (ValueError,OSError,ezdxf.DXFError) as e:p.exit(2,f'error: {e}\n')


if __name__=='__main__':main()
