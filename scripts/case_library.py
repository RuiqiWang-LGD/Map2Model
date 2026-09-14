"""Read-only retrieval, dedup proposals and portable case-library validation."""
import argparse,hashlib,json,re
from pathlib import Path
DEFAULT=Path(__file__).resolve().parents[1]/'references'/'error-library'/'catalog.json'
KEYS=('cause_key','remedy_key','check_key','condition_key')
STATES={'geometry_verified','historical_record','pending','superseded'}
def search(data,query,limit=3):
 tokens=[x.casefold() for x in re.split(r'\s+',query) if x];rows=[]
 for f in data['families']:
  hay=' '.join([f['id'],f['title'],*f['tags']]).casefold();score=sum(t in hay for t in tokens)
  if score:rows.append((score,{'id':f['id'],'title':f['title'],'detail':f['detail'],'variants':len(f['variants'])}))
 return [row for _,row in sorted(rows,key=lambda v:(-v[0],v[1]['id']))[:limit]]
def propose(data,incoming):
 for key in ('family_id',*KEYS):
  if not isinstance(incoming.get(key),str) or not incoming[key].strip():raise ValueError('Missing '+key)
 evidence=incoming.get('evidence_hashes',[])
 if not isinstance(evidence,list) or any(not re.fullmatch('[a-fA-F0-9]{64}',h) for h in evidence):raise ValueError('Invalid evidence hashes')
 f=next((f for f in data['families'] if f['id']==incoming['family_id']),None)
 if f is None:return {'action':'new_family','reason':'No matching problem family; source review required.'}
 matches=[v for v in f['variants'] if all(v[k]==incoming[k] for k in KEYS)]
 for v in matches:
  if evidence and set(h.lower() for h in evidence)==set(h.lower() for h in v['evidence_hashes']) and not incoming.get('new_information','').strip():
   return {'action':'exact_duplicate','variant':v['id'],'reason':'Same semantic keys and evidence set; do not increment occurrence.'}
 if not matches or incoming.get('new_information','').strip():
  return {'action':'new_variant','reason':'Different structure/method or explicit new information; review before adding.'}
 return {'action':'repeat_occurrence','variant':matches[0]['id'],'reason':'Same known condition with no stated new information; reuse representative figures. Count only if an independent occurrence is evidenced.'}
def safe_path(base,value):
 if not isinstance(value,str) or not value or '\\' in value or re.match(r'^[a-zA-Z]:',value):raise ValueError('Nonportable path: '+str(value))
 rel=Path(value)
 if rel.is_absolute() or '..' in rel.parts:raise ValueError('Path must stay inside library: '+value)
 path=(base/rel).resolve()
 if not path.is_relative_to(base.resolve()) or not path.is_file():raise ValueError('Missing/escaping path: '+value)
 return path
def validate(data,base):
 errors=[];ids=set();variants=set();images={}
 if data.get('schema_version')!=1:errors.append('schema_version must be 1')
 if not data.get('families'):errors.append('Empty library')
 for f in data.get('families',[]):
  try:
   if f['id'] in ids:raise ValueError('Duplicate family '+f['id'])
   ids.add(f['id']);safe_path(base,f['detail'])
   for v in f['variants']:
    if v['id'] in variants:raise ValueError('Duplicate variant '+v['id'])
    variants.add(v['id'])
    if v['status'] not in STATES:raise ValueError('Unknown status '+v['id'])
    if not all(isinstance(v.get(k),str) and v[k].strip() for k in KEYS):raise ValueError('Missing semantic key '+v['id'])
    if type(v['occurrence_count']) is not int or v['occurrence_count']<1:raise ValueError('Invalid occurrence count')
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',v['last_seen']):raise ValueError('Invalid date')
    if v['status']=='pending' and any(x['kind']=='actual_cad_before_after' for x in v['figures']):raise ValueError('Pending case must not claim its own verified AFTER')
    for im in v['figures']:
     file=safe_path(base,im['path']);digest=hashlib.sha256(file.read_bytes()).hexdigest()
     if digest!=im['sha256']:raise ValueError('Figure hash mismatch '+im['path'])
     if digest in images and images[digest]!=im['path']:raise ValueError('Duplicate image content in different files')
     images[digest]=im['path']
  except (KeyError,ValueError,TypeError) as e:errors.append(str(e))
 serialized=json.dumps(data,ensure_ascii=False)
 if re.search(r'[A-Za-z]:[\\/]|(?:/Users/|/home/)|wxid_|Administrator',serialized):errors.append('Private/local path identifier in catalog')
 return {'status':'fail' if errors else 'pass','families':len(ids),'variants':len(variants),'unique_figures':len(images),'errors':errors,'limits':'Structural checks only; source truth, image publication rights and semantic validation need review.'}
def main():
 ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--catalog',type=Path,default=DEFAULT);sub=ap.add_subparsers(dest='command',required=True)
 q=sub.add_parser('search');q.add_argument('--query',required=True);q.add_argument('--limit',type=int,default=3)
 q=sub.add_parser('propose');q.add_argument('--input',type=Path,required=True);sub.add_parser('validate')
 a=ap.parse_args();data=json.loads(a.catalog.read_text(encoding='utf-8-sig'))
 if a.command=='search':
  if not 1<=a.limit<=20:ap.error('limit must be 1..20')
  result=search(data,a.query,a.limit)
 elif a.command=='propose':result=propose(data,json.loads(a.input.read_text(encoding='utf-8-sig')))
 else:result=validate(data,a.catalog.parent)
 print(json.dumps(result,ensure_ascii=False,indent=2));return int(isinstance(result,dict) and result.get('status')=='fail')
if __name__=='__main__':raise SystemExit(main())
