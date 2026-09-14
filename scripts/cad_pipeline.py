"""Run configured CAD construction/check/review stages with immutable attempts.

Only bundled named operations run. Image interpretation and source acceptance
remain Agent work. A complete run does not claim visual or SketchUp validation.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
from shapely.errors import ShapelyError

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
from check_face_hierarchy import inspect
from compare_dxf import compare


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, data):
    Path(path).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')


def fields(value, required, optional=()):
    if not isinstance(value,dict) or not set(required)<=value.keys() or value.keys()-set(required)-set(optional):
        raise ValueError('missing or unknown configuration fields; required: '+', '.join(required))


def prepare(config_path):
    config_path=Path(config_path).resolve();c=load(config_path)
    fields(c,('schema_version','output_dir','outer'),('detail','triage','review','delivery'))
    if c['schema_version']!=1:raise ValueError('schema_version must be 1')
    def path(v):
        if not isinstance(v,str) or not v.strip():raise ValueError('nonempty path string required')
        return (config_path.parent/v).resolve()
    out=path(c['output_dir']);sources=[config_path]
    if not isinstance(c['outer'],dict):raise ValueError('outer must be a configuration object')
    fields(c['outer'],('recipe',) if 'recipe' in c['outer'] else ('dxf','faces'))
    for section in ('outer','detail','triage','review','delivery'):
        if section not in c:continue
        value=c[section]
        if section=='detail':fields(value,('recipe',))
        if section=='triage':fields(value,('config',))
        if section=='review':fields(value,('source','transform'),('selection','before_dxf'))
        if section=='delivery':
            fields(value,('core_console','tolerance'))
            if type(value['tolerance']) not in (int,float) or not math.isfinite(value['tolerance']) or value['tolerance']<=0:
                raise ValueError('conversion tolerance must be finite and positive')
        for k,v in list(value.items()):
            if k=='tolerance':continue
            value[k]=path(v);sources.append(value[k])
            if not value[k].is_file():raise ValueError(f'missing input: {value[k]}')
    if 'faces' in c['outer']:
        fc=load(c['outer']['faces'])
        if 'baseline' in fc:
            sources.append((c['outer']['faces'].parent/fc['baseline']['dxf']).resolve())
    if any(p==out or p.is_relative_to(out) for p in sources):raise ValueError('output directory contains project input')
    if any(not p.is_file() for p in sources):raise ValueError('missing external baseline')
    return config_path,c,out,sources


def fingerprint(paths, settings, scripts):
    payload={'inputs':{str(Path(p).resolve()):digest(p) for p in paths},'settings':settings,
             'tools':{s:digest(ROOT/s) for s in scripts}}
    return hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()


def input_baseline(face_path):
    c=load(face_path)
    return [(Path(face_path).parent/c['baseline']['dxf']).resolve()] if 'baseline' in c else []


def import_base(dxf,faces,directory):
    directory.mkdir();cfg=load(faces)
    if 'baseline' in cfg:
        cfg['baseline']['dxf']=str((Path(faces).parent/cfg['baseline']['dxf']).resolve())
    shutil.copyfile(dxf,directory/'cad.dxf');write(directory/'faces.json',cfg)
    rows=[dict(r,role='PARENT') for r in cfg['parents']]+cfg['details']
    write(directory/'manifest.json',rows)
    qa=inspect(directory/'cad.dxf',cfg,directory);write(directory/'validation.json',qa)
    write(directory/'construction.json',{'action':'import_verified_base','input_sha256':digest(dxf)})
    return dict(pass_=qa['geometry_pass'],dxf=str(directory/'cad.dxf'),faces=str(directory/'faces.json'),
                manifest=str(directory/'manifest.json'),validation=str(directory/'validation.json'),
                report=str(directory/'construction.json'),issues=qa['issues'])


def convert_delivery(current,options,directory):
    directory.mkdir()
    if os.name!='nt':raise ValueError('bundled DWG exporter requires Windows and existing AutoCAD Core Console')
    shell=shutil.which('pwsh.exe') or shutil.which('powershell.exe')
    if shell is None:raise ValueError('PowerShell is required for the bundled DWG exporter')
    cmd=[shell,'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(ROOT/'export_dwg.ps1'),
         '-InputDxf',current['dxf'],'-OutputDwg',str(directory/'drawing.dwg'),'-RoundTripDxf',str(directory/'roundtrip.dxf'),
         '-CoreConsolePath',str(options['core_console'])]
    proc=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=280,
                        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    (directory/'export.stdout.log').write_bytes(proc.stdout);(directory/'export.stderr.log').write_bytes(proc.stderr)
    if proc.returncode:raise ValueError('DWG conversion failed; see export.stdout.log and export.stderr.log')
    comparison=compare(current['dxf'],directory/'roundtrip.dxf',options['tolerance'])
    write(directory/'comparison.json',comparison)
    cfg=load(current['faces'])
    if 'baseline' in cfg:cfg['baseline']['dxf']=str((Path(current['faces']).parent/cfg['baseline']['dxf']).resolve())
    write(directory/'faces.json',cfg)
    qa=inspect(directory/'roundtrip.dxf',cfg,directory);write(directory/'validation.json',qa)
    signature=(directory/'drawing.dwg').read_bytes()[:6]==b'AC1032'
    return dict(pass_=comparison['geometry_equal'] and qa['geometry_pass'] and signature,
                dxf=str(directory/'roundtrip.dxf'),dwg=str(directory/'drawing.dwg'),
                faces=str(directory/'faces.json'),manifest=current['manifest'],validation=str(directory/'validation.json'),
                issues=comparison['issues']+qa['issues']+([] if signature else [{'code':'bad_dwg_signature'}]))


def run_pipeline(config_path,resume=False):
    config_path,c,out,sources=prepare(config_path)
    initial={str(p):digest(p) for p in sources}
    if out.exists():
        if not resume:raise ValueError('output exists; use --resume to verify checkpoints')
        if not (out/'state.json').is_file():raise ValueError('existing output has no pipeline state')
        state=load(out/'state.json')
        if state.get('schema_version')!=1 or state.get('project')!=str(config_path):raise ValueError('state belongs to another project')
    else:
        out.mkdir(parents=True);state={'schema_version':1,'project':str(config_path),'stages':{}}
    summary=dict(status='running',failed_stage=None,issues=[],stages=[],dxf=None,dwg=None,
                 validation=None,visual_validation='pending',su_validation='not_run')
    def save_state():
        state['status']=summary['status'];state['latest']=summary
        temp=out/'state.next.json';write(temp,state);os.replace(temp,out/'state.json')
    save_state()
    def stage(name,paths,settings,scripts,operation):
        key=fingerprint(paths,settings,['cad_pipeline.py']+scripts)
        prev=state['stages'].get(name)
        cached=False
        if prev and prev.get('status')=='passed' and prev.get('fingerprint')==key:
            outputs=prev.get('outputs',{})
            cached=bool(outputs) and all(Path(p).resolve().is_relative_to(out) and Path(p).is_file() and digest(p)==h for p,h in outputs.items())
        if cached:
            result=prev['result'];summary['stages'].append(dict(id=name,execution='cached',directory=prev['directory']))
            return result
        folder=out/name;folder.mkdir(exist_ok=True)
        attempt=1
        while (folder/f'attempt-{attempt:04}').exists():attempt+=1
        directory=folder/f'attempt-{attempt:04}'
        try:
            result=operation(directory)
            passed=result.get('pass',result.get('pass_',True)) is True
            if fingerprint(paths,settings,['cad_pipeline.py']+scripts)!=key:
                result['issues']=[{'code':'inputs_changed_during_stage'}];passed=False
        except (ValueError,OSError,RuntimeError,subprocess.SubprocessError,ShapelyError) as e:
            directory.mkdir(exist_ok=True);result={'pass':False,'issues':[{'code':'stage_error','type':type(e).__name__,'message':str(e)}]};passed=False
        write(directory/'stage-result.json',result)
        record=dict(status='passed' if passed else 'failed',fingerprint=key,directory=str(directory),result=result,
                    outputs={str(p):digest(p) for p in directory.rglob('*') if p.is_file()})
        state['stages'][name]=record
        summary['stages'].append(dict(id=name,execution='ran',directory=str(directory)))
        if not passed:
            issues=result.get('issues',[])
            if not issues and result.get('validation'):issues=load(result['validation']).get('issues',[])
            summary.update(status='failed',failed_stage=name,issues=issues or [{'code':'stage_failed','directory':str(directory)}])
        save_state()
        return result if passed else None
    tools_construct=['cad_construct.py','check_face_hierarchy.py','compare_dxf.py']
    if (ROOT/'cad_shapes.py').exists():tools_construct.append('cad_shapes.py')
    if 'recipe' in c['outer']:
        from cad_construct import construct
        current=stage('outer',[c['outer']['recipe']],{},tools_construct,lambda p:construct(c['outer']['recipe'],p))
    else:
        paths=[c['outer']['dxf'],c['outer']['faces']]+input_baseline(c['outer']['faces'])
        current=stage('outer',paths,{},['check_face_hierarchy.py','compare_dxf.py'],lambda p:import_base(c['outer']['dxf'],c['outer']['faces'],p))
    if current is None:return summary
    pre_detail_dxf=current['dxf']
    if 'detail' in c:
        from cad_construct import construct
        paths=[c['detail']['recipe'],current['dxf'],current['faces']]+input_baseline(current['faces'])
        previous=current
        current=stage('detail',paths,{},tools_construct,lambda p:construct(c['detail']['recipe'],p,previous['dxf'],previous['faces']))
        if current is None:return summary
    summary.update(dxf=current['dxf'],validation=current['validation'])
    if 'delivery' in c:
        paths=[current['dxf'],current['faces'],current['manifest'],c['delivery']['core_console']]+input_baseline(current['faces'])
        previous=current
        current=stage('delivery',paths,{'tolerance':c['delivery']['tolerance']},['export_dwg.ps1','compare_dxf.py','check_face_hierarchy.py'],
                      lambda p:convert_delivery(previous,c['delivery'],p))
        if current is None:return summary
        summary.update(dxf=current['dxf'],dwg=current['dwg'],validation=current['validation'])
    selection=None
    if 'triage' in c:
        from triage_buildings import triage
        def run_triage(p):
            p.mkdir();report=triage(current['dxf'],load(current['manifest']),load(c['triage']['config']))
            write(p/'candidates.json',report);return {'report':str(p/'candidates.json'),'candidate_count':report['candidate_count'],'deferred_count':report['deferred_count']}
        r=stage('triage',[current['dxf'],current['manifest'],c['triage']['config']],{},['triage_buildings.py'],run_triage)
        if r is None:return summary
        selection=r['report'];summary['triage']=r
    if 'review' in c:
        from cad_review import make_review
        review=dict(c['review']);review.setdefault('before_dxf',Path(pre_detail_dxf));selection=review.get('selection',selection)
        paths=[current['dxf'],current['manifest'],review['source'],review['transform'],review['before_dxf']]+([selection] if selection else [])
        def render(p):
            p.mkdir()
            cmd=[sys.executable,'-B',str(ROOT/'render_overlay.py'),'--dxf',current['dxf'],'--source',str(review['source']),
                 '--transform',str(review['transform']),'--output',str(p/'overlay.jpg'),'--color','#FFFF00']
            proc=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=180)
            if proc.returncode:raise ValueError('overlay failed: '+proc.stderr.decode('utf-8',errors='replace'))
            result=make_review(review['before_dxf'],current['dxf'],current['manifest'],review['source'],review['transform'],selection,p/'sheets')
            return dict(overlay=str(p/'overlay.jpg'),**result)
        r=stage('review',paths,{},['cad_review.py','render_overlay.py'],render)
        if r is None:return summary
        summary['review']=r
    if any(not Path(p).is_file() or digest(p)!=h for p,h in initial.items()):
        summary.update(status='failed',failed_stage='input_integrity',issues=[{'code':'project_input_changed'}])
    else:summary['status']='complete'
    save_state();return summary


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('config',type=Path);p.add_argument('--resume',action='store_true');a=p.parse_args()
    try:
        result=run_pipeline(a.config,a.resume);print(json.dumps(result,ensure_ascii=False,indent=2))
        if result['status']!='complete':raise SystemExit(1)
    except (ValueError,OSError) as e:p.exit(2,f'error: {e}\n')


if __name__=='__main__':main()
