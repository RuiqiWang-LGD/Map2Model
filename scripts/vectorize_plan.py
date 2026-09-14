"""Exact-color masks to draft polygon candidates; no semantic inference."""
import argparse
import hashlib
import json
import math
from pathlib import Path

import ezdxf
import numpy as np
from PIL import Image, ImageColor
from shapely.affinity import affine_transform
from shapely.geometry import box, mapping
from shapely.ops import unary_union

PALETTE = {'road': '#666666', 'building': '#EEEEEE', 'water': '#A6D8F0', 'green': '#BBD6A7', 'background': '#FFFFFF'}


def main():
    p = argparse.ArgumentParser(description=__doc__, epilog='Outputs PREFIX.dxf, PREFIX.geojson, PREFIX.metadata.json. Shared class boundaries remain duplicate draft rings requiring CAD postprocessing. Pixel edges use x=px, y=height-py. No small-object filtering; holes and frame clipping are preserved. A colored internal outline cannot be distinguished from a road.')
    p.add_argument('--input', required=True, type=Path, help='Accepted flat-color analysis image or manually corrected classification mask; not a raw photograph')
    p.add_argument('--output-prefix', required=True, type=Path, help='Output path prefix (without extension)')
    p.add_argument('--palette', type=Path, help='JSON object of class name to #RRGGBB; background is excluded; FRAME is reserved; default matching is exact RGB')
    p.add_argument('--color-tolerance', type=float, default=0, help='Maximum Euclidean RGB distance to nearest palette color (0..441.673), default 0 exact match; does not infer semantics')
    p.add_argument('--simplify-px', type=float, default=0, help='Topology-preserving pixel tolerance, default 0; nonzero can change shared-boundary alignment and must be reviewed')
    p.add_argument('--units', choices=['unknown', 'm', 'mm'], default='unknown', help='Unknown uses unitless pixels and INSUNITS=0')
    p.add_argument('--units-per-pixel', type=float, help='Required positive scale for m/mm; forbidden with unknown')
    p.add_argument('--overwrite', action='store_true', help='Explicitly replace existing outputs')
    a = p.parse_args()
    try:
        if not math.isfinite(a.simplify_px) or a.simplify_px < 0:
            raise ValueError('simplify-px must be finite and nonnegative')
        if not math.isfinite(a.color_tolerance) or not 0 <= a.color_tolerance <= math.sqrt(3*255**2):
            raise ValueError('color-tolerance must be finite in RGB distance range')
        if a.units == 'unknown' and a.units_per_pixel is not None:
            raise ValueError('unknown units require pixel coordinates; omit units-per-pixel')
        s = 1 if a.units == 'unknown' else a.units_per_pixel
        if s is None or not math.isfinite(s) or s <= 0:
            raise ValueError('m/mm require positive finite units-per-pixel')
        paths = [Path(str(a.output_prefix) + suffix) for suffix in ['.dxf', '.geojson', '.metadata.json']]
        if len({x.resolve() for x in [a.input, *paths]}) != 4 or (a.palette and a.palette.resolve() in {x.resolve() for x in paths}):
            raise ValueError('output paths must not overwrite inputs')
        if not a.overwrite and any(x.exists() for x in paths):
            raise ValueError('output exists; use --overwrite')
        palette = json.loads(a.palette.read_text(encoding='utf-8')) if a.palette else PALETTE
        if not isinstance(palette, dict) or not palette:
            raise ValueError('palette must be a nonempty JSON object')
        if any(name.upper() == 'FRAME' for name in palette):
            raise ValueError('FRAME is reserved for the image boundary')
        colors = {name: ImageColor.getrgb(color) for name, color in palette.items()}
        if any(len(rgb) != 3 for rgb in colors.values()) or len(set(colors.values())) != len(colors):
            raise ValueError('palette must use distinct RGB colors')
        image = Image.open(a.input).convert('RGBA')
        arr = np.asarray(image)
        if np.any(arr[:, :, 3] != 255):
            raise ValueError('mask must be opaque; flatten/correct transparency before conversion')
        rgb = arr[:, :, :3]
        w, h = image.size
        best = np.full((h,w), np.inf)
        labels = np.full((h,w), -1, dtype=np.int32)
        ties = np.zeros((h,w), dtype=bool)
        for index, color in enumerate(colors.values()):
            distance = np.sum((rgb.astype(np.float64) - color)**2, axis=2)
            closer = distance < best
            ties = np.where(closer, False, ties | (distance == best))
            labels[closer] = index
            best = np.minimum(best, distance)
        unmatched = best > a.color_tolerance**2
        if unmatched.any() or ties.any():
            raise ValueError(f'{int(unmatched.sum())} pixels outside palette tolerance; {int(ties.sum())} ambiguous nearest-color pixels; correct the classification mask first')
        masks = {name: labels == index for index, name in enumerate(colors)}
        approximate_count = int((best > 0).sum())
        doc = ezdxf.new('R2010')
        doc.header['$INSUNITS'] = {'unknown':0, 'm':6, 'mm':4}[a.units]
        features = []
        for name, mask in masks.items():
            if name == 'background' or not mask.any():
                continue
            doc.layers.new(name)
            rects = []
            for y, row in enumerate(mask):
                changes = np.diff(np.r_[False, row, False].astype(np.int8))
                rects.extend(box(int(start), y, int(end), y+1) for start, end in zip(np.where(changes == 1)[0], np.where(changes == -1)[0]))
            geom = unary_union(rects)
            if a.simplify_px:
                geom = geom.simplify(a.simplify_px, preserve_topology=True)
            geom = affine_transform(geom, [s,0,0,-s,0,h*s])
            polygons = [geom] if geom.geom_type == 'Polygon' else list(geom.geoms)
            for poly in polygons:
                handles = []
                for ring in [poly.exterior, *poly.interiors]:
                    entity = doc.modelspace().add_lwpolyline(list(ring.coords)[:-1], close=True, dxfattribs={'layer':name})
                    handles.append(entity.dxf.handle)
                features.append({'type':'Feature', 'id':len(features)+1, 'properties':{'class':name, 'dxf_handles':handles, 'hole_count':len(poly.interiors), 'status':'draft'}, 'geometry':mapping(poly)})
        metadata = {'schema_version':1, 'status':'draft', 'input':str(a.input.resolve()), 'input_sha256':hashlib.sha256(a.input.read_bytes()).hexdigest(), 'width':w, 'height':h, 'units':a.units, 'units_per_pixel':s if a.units != 'unknown' else None, 'image_to_world':[[s,0,0],[0,-s,h*s],[0,0,1]], 'world_to_image':[[1/s,0,0],[0,-1/s,h],[0,0,1]], 'palette':palette, 'simplify_px':a.simplify_px, 'feature_count':len(features), 'shared_boundaries':'per-polygon rings may duplicate shared boundaries; postprocess and validate before modeling', 'semantic_validation':'pending: cannot distinguish road-colored internal outlines from roads', 'visual_validation':'pending', 'small_object_filter':False, 'geojson_coordinates':'local planar coordinates, not RFC7946 WGS84 longitude/latitude'}
        if not features:
            raise ValueError('no candidate features: mask contains only background')
        doc.layers.new('FRAME')
        frame = doc.modelspace().add_lwpolyline([(0,0),(w*s,0),(w*s,h*s),(0,h*s)], close=True, dxfattribs={'layer':'FRAME'})
        metadata['frame'] = {'layer':'FRAME', 'dxf_handle':frame.dxf.handle, 'bounds':[0,0,w*s,h*s], 'status':'draft: frame may overlap clipped polygon boundaries'}
        metadata.update(color_tolerance=a.color_tolerance, approximate_match_pixels=approximate_count, class_pixel_counts={name:int(mask.sum()) for name,mask in masks.items()})
        a.output_prefix.parent.mkdir(parents=True, exist_ok=True)
        doc.saveas(paths[0])
        paths[1].write_text(json.dumps({'type':'FeatureCollection','features':features}, ensure_ascii=False, indent=2), encoding='utf-8')
        paths[2].write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'status':'draft','outputs':[str(x) for x in paths]}))
    except (ValueError, OSError, ezdxf.DXFError) as exc:
        p.exit(2, f'error: {exc}\n')


if __name__ == '__main__':
    main()
