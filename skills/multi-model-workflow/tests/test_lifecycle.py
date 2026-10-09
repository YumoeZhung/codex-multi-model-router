import importlib.util
import json
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS=Path(__file__).resolve().parents[1]/'scripts'
def module(name):
 spec=importlib.util.spec_from_file_location(name,SCRIPTS/(name+'.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
m=module('manage');b=module('native_bridge')

class LifecycleTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.home=Path(self.tmp.name)/'home';self.home.mkdir()
  self.router=self.home/'model-router/router.py';self.router.parent.mkdir();self.router.write_text('original router')
  self.plist=self.home/'service.plist';self.original={'Label':'test.router','ProgramArguments':['python3',str(self.router),'--port','1234'],'EnvironmentVariables':{'PROXY':'original'}}
  self.plist.write_bytes(plistlib.dumps(self.original));self.calls=[]
  self.restart=lambda *a:self.calls.append(a)
 def test_install_remove_preserves_router_and_later_settings(self):
  state=m.install(self.home,self.plist,self.restart)
  self.assertTrue(state['active']);self.assertEqual(self.router.read_text(),'original router')
  d=plistlib.loads(self.plist.read_bytes());d['EnvironmentVariables']['PROXY']='later';self.plist.write_bytes(plistlib.dumps(d))
  result=m.remove_bridge(self.home,self.restart)
  d=plistlib.loads(self.plist.read_bytes());self.assertEqual(d['ProgramArguments'],self.original['ProgramArguments']);self.assertEqual(d['EnvironmentVariables']['PROXY'],'later')
  self.assertTrue(result['router_unchanged'])
 def test_conflicting_later_service_command_not_overwritten(self):
  m.install(self.home,self.plist,self.restart);d=plistlib.loads(self.plist.read_bytes());d['ProgramArguments']=['another'];self.plist.write_bytes(plistlib.dumps(d))
  with self.assertRaises(ValueError):m.remove_bridge(self.home,self.restart)
  self.assertEqual(plistlib.loads(self.plist.read_bytes())['ProgramArguments'],['another'])
 def test_unclosed_task_blocks_remove_even_expired(self):
  m.install(self.home,self.plist,self.restart)
  p=m.runtime(self.home)/'requests/a.json';p.write_text(json.dumps({'closed':False,'expires_at':0}))
  with self.assertRaises(ValueError):m.remove_bridge(self.home,self.restart)
 def test_install_rollback(self):
  def restart(*args):
   self.calls.append(args)
   if len(self.calls)==1:raise RuntimeError('failed start')
  with self.assertRaises(RuntimeError):m.install(self.home,self.plist,restart)
  self.assertEqual(plistlib.loads(self.plist.read_bytes()),self.original)
 def test_uninstall_archives_skill_not_router(self):
  skill=self.home/'skills/multi-model-workflow';skill.mkdir(parents=True);(skill/'SKILL.md').write_text('skill')
  (self.home/'config.toml').write_text('model_provider="local_router"')
  m.install(self.home,self.plist,self.restart)
  archive=self.home/'archives/test';m.uninstall(self.home,archive,self.restart,skill)
  self.assertFalse(skill.exists());self.assertTrue((archive/'multi-model-workflow/SKILL.md').exists())
  self.assertEqual(self.router.read_text(),'original router');self.assertEqual((self.home/'config.toml').read_text(),'model_provider="local_router"')
  self.assertEqual(plistlib.loads(self.plist.read_bytes())['ProgramArguments'],self.original['ProgramArguments'])
 def test_unfinished_run_blocks_uninstall(self):
  d=self.home/'multi-model-workflow-runs/run';d.mkdir(parents=True);(d/'state.json').write_text('{"status":"running"}')
  with self.assertRaises(ValueError):m.uninstall(self.home,self.home/'archive',self.restart,self.home/'skill')

 def test_keyboard_interrupt_rolls_back(self):
  def restart(*args):
   self.calls.append(args)
   if len(self.calls)==1:raise KeyboardInterrupt()
  with self.assertRaises(KeyboardInterrupt):m.install(self.home,self.plist,restart)
  self.assertEqual(plistlib.loads(self.plist.read_bytes()),self.original)
  self.assertEqual(m.read(m.runtime(self.home)/'installation.json')['phase'],'inactive')
 def test_crash_recovery_reconciles_actual_service_even_if_inactive(self):
  state=m.install(self.home,self.plist,self.restart)
  state.update(active=False,phase='installing');m.write(m.runtime(self.home)/'installation.json',state)
  m.remove_bridge(self.home,self.restart)
  self.assertEqual(plistlib.loads(self.plist.read_bytes())['ProgramArguments'],self.original['ProgramArguments'])
 def test_archive_inside_any_source_refused_before_mutation(self):
  m.install(self.home,self.plist,self.restart);before=self.plist.read_bytes()
  skill=self.home/'skill';skill.mkdir()
  for archive in (skill/'backup',m.runtime(self.home)/'backup',self.home/'multi-model-workflow-runs/backup',self.home/'skills/archive'):
   with self.assertRaises(ValueError):m.uninstall(self.home,archive,self.restart,skill)
   self.assertEqual(self.plist.read_bytes(),before);self.assertTrue(skill.exists())
 def test_unfinished_external_run_blocks_uninstall(self):
  rt=m.runtime(self.home);(rt/'runs').mkdir(parents=True)
  external=self.home/'outside';external.mkdir();m.write(external/'state.json',{'status':'awaiting_judgment'})
  m.write(rt/'runs/test.json',{'run_dir':str(external)})
  with self.assertRaises(ValueError):m.uninstall(self.home,self.home/'archive',self.restart,self.home/'skill')
 def test_policy_roundtrip_preserves_original_and_later_changes(self):
  p=self.home/'AGENTS.md';original='遵从第一性原理。\n';p.write_text(original)
  m.enable_policy(self.home);p.write_text(p.read_text()+'\nLater user instruction.\n')
  m.remove_policy(self.home)
  self.assertEqual(p.read_text(),original+'\nLater user instruction.\n')
 def test_policy_conflict_refuses_before_service_change(self):
  m.install(self.home,self.plist,self.restart);m.enable_policy(self.home)
  p=self.home/'AGENTS.md';p.write_text(p.read_text().replace('原生和 CLI','edited'))
  before=self.plist.read_bytes()
  with self.assertRaises(ValueError):m.uninstall(self.home,self.home/'archive',self.restart,self.home/'skill')
  self.assertEqual(self.plist.read_bytes(),before);self.assertIn('edited',p.read_text())
 def test_policy_new_file_removed_but_later_contents_preserved(self):
  p=self.home/'AGENTS.md';m.enable_policy(self.home);m.remove_policy(self.home);self.assertFalse(p.exists())
  m.enable_policy(self.home);p.write_text(p.read_text()+'later');m.remove_policy(self.home);self.assertEqual(p.read_text(),'later')
 def test_policy_interrupted_append_can_be_removed(self):
  p=self.home/'AGENTS.md';p.write_text('before');m.enable_policy(self.home);p.write_text('before')
  m.remove_policy(self.home);self.assertEqual(p.read_text(),'before')
 def test_runtime_recovery_after_manual_skill_removal(self):
  state=m.install(self.home,self.plist,self.restart);m.remove_bridge(self.home,self.restart)
  manifest=m.runtime(self.home)/'installation.json';state=m.read(manifest);state['skill_root']=str(self.home/'missing-skill');m.write(manifest,state)
  cmd=[sys.executable,str(m.runtime(self.home)/'remove_skill.py'),'uninstall','--home',str(self.home),'--archive',str(self.home/'archive')]
  result=subprocess.run(cmd,capture_output=True,text=True)
  self.assertEqual(result.returncode,0,result.stderr)
  self.assertTrue((self.home/'archive/multi-model-workflow-runtime/remove_skill.py').exists())
  self.assertTrue(self.router.exists())
 def test_uninstall_resumes_after_runs_moved_before_runtime(self):
  skill=self.home/'skills/multi-model-workflow';skill.mkdir(parents=True);(skill/'SKILL.md').write_text('skill')
  run=self.home/'multi-model-workflow-runs/example';run.mkdir(parents=True);m.write(run/'state.json',{'status':'complete'})
  rt=m.runtime(self.home);(rt/'runs').mkdir(parents=True);m.write(rt/'runs/example.json',{'run_dir':str(run)})
  archive=self.home/'archives/example';move=shutil.move
  def fail_final(source,dest):
   if Path(source)==rt.resolve():raise OSError('interrupted before runtime move')
   return move(source,dest)
  with patch.object(m.shutil,'move',side_effect=fail_final),self.assertRaises(OSError):m.uninstall(self.home,archive,self.restart,skill)
  self.assertFalse(run.exists());self.assertTrue((archive/'multi-model-workflow-runs/example/state.json').exists())
  result=m.uninstall(self.home,archive,self.restart,skill)
  self.assertTrue(result['router_preserved']);self.assertFalse(rt.exists())
  self.assertEqual(m.read(archive/'multi-model-workflow-runtime/uninstall.json')['phase'],'complete')

class BridgeTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  agent='/root/mmw_'+'a'*32
  self.record={'model':'aux','agent_path':agent,'author':'/root','expires_at':200,'prompt':'Read the synthetic fixture.','closed':False}
  self.path=b.record_path(self.root,agent);self.path.write_text(json.dumps(self.record))
  self.payload={'model':'aux','input':[{'type':'message','role':'user','content':[{'type':'input_text','text':'original user text'}]},{'type':'agent_message','id':'id1','author':'/root','recipient':agent,'content':[{'type':'encrypted_content','encrypted_content':'opaque'}]}]}
 def test_exact_registered_message_gets_own_plaintext(self):
  result=b.translate(self.payload,self.root,100)
  self.assertEqual(result['input'][0],self.payload['input'][0]);self.assertEqual(result['input'][1]['type'],'message');self.assertIn(self.record['prompt'],result['input'][1]['content'][0]['text'])
  self.assertEqual(self.payload['input'][1]['type'],'agent_message')
  self.assertEqual(json.loads(self.path.read_text())['message_id'],'id1')
 def test_unregistered_or_other_model_unchanged(self):
  self.payload['model']='gpt';self.assertEqual(b.translate(self.payload,self.root,100),self.payload)
  self.payload['model']='aux';self.payload['input'][1]['recipient']='/root/other';self.assertEqual(b.translate(self.payload,self.root,100),self.payload)
 def test_generic_cross_chat_agent_never_translated(self):
  self.record['agent_path']='/root/worker';b.record_path(self.root,'/root/worker').write_text(json.dumps(self.record))
  self.payload['input'][1]['recipient']='/root/worker'
  self.assertEqual(b.translate(self.payload,self.root,100),self.payload)
 def test_expired_closed_sender_mismatch_rejected(self):
  with self.assertRaises(ValueError):b.translate(self.payload,self.root,200)
  self.record['closed']=True;self.path.write_text(json.dumps(self.record))
  with self.assertRaises(ValueError):b.translate(self.payload,self.root,100)
  self.record['closed']=False;self.record['author']='/other';self.path.write_text(json.dumps(self.record))
  with self.assertRaises(ValueError):b.translate(self.payload,self.root,100)
 def test_second_assignment_same_agent_rejected(self):
  b.translate(self.payload,self.root,100);self.payload['input'][1]['id']='id2'
  with self.assertRaises(ValueError):b.translate(self.payload,self.root,100)
 def test_repeated_tool_requests_same_assignment_allowed(self):
  b.translate(self.payload,self.root,100);b.translate(self.payload,self.root,101)
  self.assertEqual(json.loads(self.path.read_text())['requests'],2)

if __name__=='__main__':unittest.main()
