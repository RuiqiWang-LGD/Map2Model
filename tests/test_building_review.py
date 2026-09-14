import copy,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from check_building_review import check
class CoverageTests(unittest.TestCase):
 def setUp(self):
  self.digest='a'*64;self.manifest=[{'id':'a','role':'BUILDING'},{'id':'b','role':'BUILDING'},{'id':'road','role':'ROAD'}]
  self.triage={'dxf_sha256':self.digest,'objects':[{'id':'a','status':'review_candidate'},{'id':'b','status':'no_geometry_trigger'}]}
  self.decisions={'dxf_sha256':self.digest,'objects':[{'id':i,'decision':'keep','evidence':'source block review','validation':'current version evidence'} for i in ['a','b']]}
 def result(self):return check(self.digest,self.manifest,self.triage,self.decisions)
 def test_complete_coverage(self):self.assertEqual(self.result()['status'],'pass')
 def test_only_known_candidates_is_incomplete(self):
  self.triage['objects'].pop();self.assertEqual(self.result()['status'],'fail')
 def test_nontriggered_still_needs_source_disposition(self):
  self.decisions['objects'].pop();self.assertEqual(self.result()['status'],'fail')
 def test_old_version_rejected(self):
  self.decisions['dxf_sha256']='b'*64;self.assertEqual(self.result()['status'],'fail')
 def test_deferred_not_approved(self):
  self.decisions['objects'][0]['decision']='defer';self.assertEqual(self.result()['status'],'fail')
 def test_unsupported_evidence_not_approved(self):
  self.decisions['objects'][0]['evidence']='';self.assertEqual(self.result()['status'],'fail')
 def test_duplicate_inventory_rejected(self):
  self.manifest.append(copy.deepcopy(self.manifest[0]));self.assertEqual(self.result()['status'],'fail')
if __name__=='__main__':unittest.main()
