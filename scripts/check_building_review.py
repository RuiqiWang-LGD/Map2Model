"""Gate declared building inventory, triage and review coverage for one DXF version.
Decisions: {dxf_sha256, objects:[{id,decision,evidence,validation}]}.
All inventory BUILDING* IDs must be triaged. Candidate/deferred/user-flagged
objects need explicit dispositions; nontriggered objects need a source-reviewed
keep disposition too (which can share a block-level evidence document).
This checks declared coverage, not whether the inventory truly includes every roof.
"""
import argparse,json,hashlib
from pathlib import Path
def check(digest,manifest,triage,decisions):
 issues=[]
 expected=[r['id'] for r in manifest if r.get('role','').startswith('BUILDING')]
 objects=triage.get('objects',[]);actual=[r['id'] for r in objects];dec=decisions.get('objects',[]);reviewed=[r['id'] for r in dec]
 for label,ids in [('inventory',expected),('triage',actual),('decisions',reviewed)]:
  if len(ids)!=len(set(ids)):issues.append({'code':'duplicate_ids','where':label})
 if triage.get('dxf_sha256')!=digest or decisions.get('dxf_sha256')!=digest:issues.append({'code':'stale_version'})
 if set(expected)!=set(actual):issues.append({'code':'incomplete_triage','missing':sorted(set(expected)-set(actual)),'extra':sorted(set(actual)-set(expected))})
 if set(expected)!=set(reviewed):issues.append({'code':'incomplete_decisions','missing':sorted(set(expected)-set(reviewed)),'extra':sorted(set(reviewed)-set(expected))})
 by={r['id']:r for r in dec};pending=[]
 for ident in expected:
  r=by.get(ident)
  if not r:continue
  if r.get('decision') not in {'keep','regularize','separate_ground','defer'} or not r.get('evidence') or not r.get('validation'):issues.append({'code':'unsupported_disposition','id':ident})
  if r.get('decision')=='defer':pending.append(ident)
 if pending:issues.append({'code':'unresolved_buildings','ids':pending})
 return {'status':'fail' if issues else 'pass','inventory_count':len(expected),'triaged_count':len(actual),'reviewed_count':len(reviewed),'pending':pending,'issues':issues,'limits':'Coverage of declared inventory only. Evidence descriptions are declarations, not automatic visual verification.'}
def main():
 ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('dxf',type=Path)
 for name in ('manifest','triage','decisions','report'):ap.add_argument('--'+name,required=True,type=Path)
 a=ap.parse_args();inputs=[a.dxf,a.manifest,a.triage,a.decisions]
 if any(a.report.resolve()==p.resolve() or (a.report.exists() and a.report.samefile(p)) for p in inputs):raise ValueError('Report must not replace inputs')
 load=lambda p:json.loads(p.read_text(encoding='utf-8-sig'))
 r=check(hashlib.sha256(a.dxf.read_bytes()).hexdigest(),load(a.manifest),load(a.triage),load(a.decisions))
 r['input_sha256']={k:hashlib.sha256(v.read_bytes()).hexdigest() for k,v in [('dxf',a.dxf),('manifest',a.manifest),('triage',a.triage),('decisions',a.decisions)]}
 a.report.parent.mkdir(parents=True,exist_ok=True);a.report.write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(r,ensure_ascii=False));return r['status']!='pass'
if __name__=='__main__':raise SystemExit(main())
