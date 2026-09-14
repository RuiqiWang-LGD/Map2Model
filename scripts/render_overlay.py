"""Render actual DXF modelspace geometry with an explicit world-to-image affine."""
import argparse
import hashlib
import json
import math
from pathlib import Path

import ezdxf
from ezdxf.path import make_path
import numpy as np
from PIL import Image, ImageDraw, ImageColor

SUPPORTED = {'LINE', 'LWPOLYLINE', 'POLYLINE', 'ARC', 'CIRCLE'}


def main():
    p = argparse.ArgumentParser(description=__doc__, epilog='No automatic fit-to-extents. Supports LINE, LWPOLYLINE (bulges), POLYLINE (bulges), ARC and CIRCLE. Curves are flattened within pixel tolerance. Any unsupported entity aborts, including INSERT/HATCH/TEXT. Geometry is projected to world XY; this is not a 3D perspective renderer.')
    p.add_argument('--dxf', required=True, type=Path, help='Actual final/read-back DXF to draw')
    p.add_argument('--transform', required=True, type=Path, help='JSON with finite invertible 3x3 world_to_image affine; vectorizer metadata is accepted')
    p.add_argument('--source', type=Path, help='Original image or analysis image; output retains its exact dimensions')
    p.add_argument('--width', type=int, help='Canvas width required without source; if supplied with source must match')
    p.add_argument('--height', type=int, help='Canvas height required without source; if supplied with source must match')
    p.add_argument('--output', required=True, type=Path, help='JPG destination; metadata written beside it as .metadata.json')
    p.add_argument('--source-kind', choices=['original','analysis','blank'], default='original', help='Evidence label; no source forces blank')
    p.add_argument('--color', default='#E02020', help='Overlay line color, default #E02020')
    p.add_argument('--opacity', type=float, default=0.8, help='Line alpha from 0 to 1')
    p.add_argument('--line-width', type=int, default=1, help='Positive line width in pixels')
    p.add_argument('--curve-tolerance-px', type=float, default=0.25, help='Positive curve flattening tolerance in image pixels')
    p.add_argument('--overwrite', action='store_true', help='Explicitly replace output JPG and metadata')
    a = p.parse_args()
    try:
        meta_path = a.output.with_suffix('.metadata.json')
        if a.output.suffix.lower() not in {'.jpg','.jpeg'}:
            raise ValueError('output must be JPG/JPEG')
        if not a.overwrite and (a.output.exists() or meta_path.exists()):
            raise ValueError('output exists; use --overwrite')
        if {a.output.resolve(), meta_path.resolve()} & {x.resolve() for x in [a.dxf,a.transform,a.source] if x}:
            raise ValueError('outputs must not overwrite inputs')
        cfg = json.loads(a.transform.read_text(encoding='utf-8'))
        matrix = np.asarray(cfg.get('world_to_image'), dtype=float)
        if matrix.shape != (3,3) or not np.isfinite(matrix).all() or not np.array_equal(matrix[2], [0,0,1]):
            raise ValueError('world_to_image must be a finite 3x3 affine with last row [0,0,1]')
        if np.linalg.det(matrix[:2,:2]) == 0:
            raise ValueError('world_to_image must be invertible')
        if not math.isfinite(a.opacity) or not 0 <= a.opacity <= 1 or a.line_width <= 0 or not math.isfinite(a.curve_tolerance_px) or a.curve_tolerance_px <= 0:
            raise ValueError('invalid opacity, line width or curve tolerance')
        if a.source:
            base = Image.open(a.source).convert('RGBA')
            if (a.width is not None and a.width != base.width) or (a.height is not None and a.height != base.height):
                raise ValueError('explicit dimensions must match source dimensions')
        else:
            if not a.width or not a.height or min(a.width,a.height) <= 0:
                raise ValueError('positive --width and --height required without source')
            base = Image.new('RGBA', (a.width,a.height), 'white')
        if ('width' in cfg and cfg['width'] != base.width) or ('height' in cfg and cfg['height'] != base.height):
            raise ValueError('transform dimensions do not match canvas/source; explicitly register a transform for this image size')
        doc = ezdxf.readfile(a.dxf)
        entities = list(doc.modelspace())
        if not entities:
            raise ValueError('no modelspace entities to render')
        unsupported = [{'handle':e.dxf.handle,'type':e.dxftype()} for e in entities if e.dxftype() not in SUPPORTED or (e.dxftype() == 'POLYLINE' and (e.is_polygon_mesh or e.is_poly_face_mesh))]
        if unsupported:
            raise ValueError('unsupported entities (none silently skipped): ' + json.dumps(unsupported))
        overlay = Image.new('RGBA', base.size, (0,0,0,0))
        draw = ImageDraw.Draw(overlay)
        color = ImageColor.getrgb(a.color)[:3] + (round(255*a.opacity),)
        tolerance = a.curve_tolerance_px / np.linalg.norm(matrix[:2,:2], ord=2)
        counts = {}
        for entity in entities:
            path = make_path(entity)
            points = []
            for vertex in path.flattening(distance=tolerance, segments=8):
                point = matrix @ [vertex.x, vertex.y, 1]
                if not np.isfinite(point).all():
                    raise ValueError('nonfinite transformed geometry')
                points.append((float(point[0]),float(point[1])))
            if len(points) < 2:
                raise ValueError(f'degenerate entity {entity.dxf.handle}: cannot render')
            draw.line(points, fill=color, width=a.line_width)
            counts[entity.dxftype()] = counts.get(entity.dxftype(),0) + 1
        metadata = {'schema_version':1, 'dxf':str(a.dxf.resolve()), 'dxf_sha256':hashlib.sha256(a.dxf.read_bytes()).hexdigest(), 'source':str(a.source.resolve()) if a.source else None, 'source_sha256':hashlib.sha256(a.source.read_bytes()).hexdigest() if a.source else None, 'transform_sha256':hashlib.sha256(a.transform.read_bytes()).hexdigest(), 'source_kind':a.source_kind if a.source else 'blank', 'width':base.width, 'height':base.height, 'world_to_image':matrix.tolist(), 'rendered_entities':len(entities), 'entity_counts':counts, 'skipped_entities':[], 'projection':'world XY', 'curve_tolerance_px':a.curve_tolerance_px, 'visual_validation':'pending'}
        a.output.parent.mkdir(parents=True, exist_ok=True)
        Image.alpha_composite(base, overlay).convert('RGB').save(a.output, quality=95, subsampling=0)
        meta_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'output':str(a.output), 'metadata':str(meta_path)}))
    except (ValueError, OSError, ezdxf.DXFError, np.linalg.LinAlgError) as exc:
        p.exit(2, f'error: {exc}\n')


if __name__ == '__main__':
    main()
