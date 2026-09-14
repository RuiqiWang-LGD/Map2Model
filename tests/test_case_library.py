import copy,json,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import case_library as lib
class LibraryTests(unittest.TestCase):
 def setUp(self):
  self.data=json.loads(lib.DEFAULT.read_text(encoding='utf-8'))
  f=next(f for f in self.data['families'] if f['id']=='EF005');v=f['variants'][0]
  self.row={'family_id':f['id'],**{k:v[k] for k in lib.KEYS},'evidence_hashes':list(v['evidence_hashes']),'new_information':''}
 def test_shipped_library(self):self.assertEqual(lib.validate(self.data,lib.DEFAULT.parent)['status'],'pass')
 def test_search_retrieves_abnormal_buildings(self):self.assertEqual(lib.search(self.data,'建筑 拖尾')[0]['id'],'EF005')
 def test_exact_duplicate(self):self.assertEqual(lib.propose(self.data,self.row)['action'],'exact_duplicate')
 def test_new_evidence_same_condition_repeats(self):
  self.row['evidence_hashes']=['a'*64];self.assertEqual(lib.propose(self.data,self.row)['action'],'repeat_occurrence')
 def test_similar_image_new_topology_retained(self):
  self.row['condition_key']='different_courtyard_connection';self.assertEqual(lib.propose(self.data,self.row)['action'],'new_variant')
 def test_identical_image_new_finding_retained(self):
  self.row['new_information']='Same image now reveals real covered connector';self.assertEqual(lib.propose(self.data,self.row)['action'],'new_variant')
 def test_new_family(self):
  self.row['family_id']='EF999';self.assertEqual(lib.propose(self.data,self.row)['action'],'new_family')
 def test_pending_cannot_claim_verified_after(self):
  self.data['families'][0]['variants'][0]['status']='pending';self.assertEqual(lib.validate(self.data,lib.DEFAULT.parent)['status'],'fail')
 def test_escape_and_absolute_paths_rejected(self):
  for name in ['../private.png','C:/private.png','/tmp/private.png']:
   with self.assertRaises(ValueError):lib.safe_path(lib.DEFAULT.parent,name)
 def test_bad_image_hash_rejected(self):
  self.data['families'][0]['variants'][0]['figures'][0]['sha256']='0'*64;self.assertEqual(lib.validate(self.data,lib.DEFAULT.parent)['status'],'fail')
 def test_duplicate_ids_rejected(self):
  self.data['families'].append(copy.deepcopy(self.data['families'][0]));self.assertEqual(lib.validate(self.data,lib.DEFAULT.parent)['status'],'fail')
if __name__=='__main__':unittest.main()
