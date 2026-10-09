import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

spec = importlib.util.spec_from_file_location('workflow', Path(__file__).resolve().parents[1] / 'scripts/workflow.py')
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)

class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.codex_home = self.root / 'codex-home'
        self.codex_home.mkdir()
        home_patch = patch.object(w, 'home', return_value=self.codex_home)
        home_patch.start()
        self.addCleanup(home_patch.stop)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.repo)], check=True)
        self.g('config', 'user.name', 'Test')
        self.g('config', 'user.email', 'test@example.invalid')
        (self.repo / 'price.py').write_text('def price(value):\n    return value or 100\n')
        self.g('add', '.')
        self.g('commit', '-qm', 'fixture')
        self.packet = self.root / 'packet.json'
        w.save(self.packet, {'task': 'Review price zero semantics', 'questions': [{'id': 'F1', 'claim': 'Zero replaced'}], 'allowed_paths': ['price.py'], 'acceptance': ['Zero preserved']})
        self.args = argparse.Namespace(config=None, repo=str(self.repo), packet=str(self.packet), main_model='main-model', assistant_model='aux-model', mode='review', allow_write=False, base='HEAD', out=str(self.root / 'run'))
        self.cat = patch.object(w, 'catalog', return_value=['main-model', 'aux-model', 'future-flash'])
        self.cat.start()
        self.addCleanup(self.cat.stop)
        self.addCleanup(self.tmp.cleanup)

    def g(self, *args):
        return subprocess.check_output(['git', '-C', str(self.repo), *args])

    def start(self, **kw):
        for k,v in kw.items(): setattr(self.args,k,v)
        w.start(self.args)
        self.run = Path(self.args.out)
        return argparse.Namespace(run=str(self.run))

    def answer(self, quote='    return value or 100'):
        return {'findings': [{'id':'F1','title':'Zero fallback','stance':'support','priority':'P1','reason':'Falsy zero','trigger':'value=0','impact':'100 instead of 0','evidence':[{'path':'price.py','line':2,'quote':quote}]}], 'coverage':['price.py'], 'limitations':[]}

    def call(self, a, result=None, error=None):
        with patch.object(w, 'execute', side_effect=error, return_value=(result or self.answer(), {'input_tokens': 12}, ['aux-model'])):
            return w.ask(a)

    def decide(self, a, more=False, status='confirmed'):
        dec={'conclusions':[{'id':'F1','status':status,'reason':'Main verified','evidence':[{'path':'price.py','line':2,'quote':'return value or 100'}]}], 'continue':more,'feedback':'Check caller' if more else '', 'checks':['reproduced zero fallback']}
        p=self.root/'decision.json';w.save(p,dec)
        return w.judge(argparse.Namespace(run=a.run,decision=str(p)))

    def test_review_needs_main_judgment(self):
        a=self.start();self.assertEqual(self.call(a)['status'],'awaiting_judgment')
        with self.assertRaises(ValueError):w.ask(a)
        self.assertEqual(self.decide(a)['status'],'complete')
        self.assertIn('Main-agent conclusions',(self.run/'report.md').read_text())

    def test_repeated_evidence_stops(self):
        a=self.start();self.call(a);self.assertEqual(self.decide(a,True)['status'],'ready')
        self.call(a);self.assertEqual(self.decide(a,True,'unresolved')['status'],'no_new_evidence')
        self.assertEqual(w.read(self.run/'state.json')['conclusions'][0]['status'],'unresolved')

    def test_round_cap(self):
        a=self.start();s=w.read(self.run/'state.json');s['config']['max_rounds']=1;w.save(self.run/'state.json',s)
        self.call(a);self.assertEqual(self.decide(a,True)['status'],'round_limit')

    def test_time_cap_before_call(self):
        a=self.start();s=w.read(self.run/'state.json');s['started_at']-=2000;w.save(self.run/'state.json',s)
        with patch.object(w,'execute') as invoke:
            self.assertEqual(w.ask(a)['status'],'time_limit');invoke.assert_not_called()

    def test_timeout_no_fallback(self):
        a=self.start();self.assertEqual(self.call(a,error=TimeoutError('deadline'))['status'],'failed')
        with self.assertRaises(ValueError):w.ask(a)

    def test_invalid_evidence_fails(self):
        a=self.start();self.assertEqual(self.call(a,self.answer('not in source'))['status'],'failed')

    def test_missing_question_fails(self):
        a=self.start();r=self.answer();r['findings']=[];self.assertEqual(self.call(a,r)['status'],'failed')

    def test_changed_source_before_call(self):
        a=self.start();(self.repo/'price.py').write_text('changed')
        with patch.object(w,'execute') as invoke:
            self.assertEqual(w.ask(a)['status'],'source_changed');invoke.assert_not_called()

    def test_changed_source_before_judgment(self):
        a=self.start();self.call(a);(self.repo/'price.py').write_text('changed')
        self.assertEqual(self.decide(a)['status'],'source_changed')

    def test_alternate_model_selected_without_code_change(self):
        self.start(assistant_model='future-flash')
        s=w.read(self.run/'state.json');argv=w.child_argv(s,self.root)
        self.assertEqual(argv[argv.index('-m')+1],'future-flash')
        self.assertEqual(argv[argv.index('-s')+1],'read-only')

    def test_missing_model_fails_before_run(self):
        with self.assertRaises(ValueError):self.start(assistant_model='missing')
        self.assertFalse(Path(self.args.out).exists())

    def test_no_same_model_accident(self):
        with self.assertRaises(ValueError):self.start(assistant_model='main-model')

    def test_artifacts_inside_repo_rejected(self):
        with self.assertRaises(ValueError):self.start(out=str(self.repo/'artifacts'))

    def test_task_defaults_read_only(self):
        self.start(mode='task')
        s=w.read(self.run/'state.json');argv=w.child_argv(s,self.root)
        self.assertEqual(argv[argv.index('-s')+1],'read-only')
        self.assertFalse(s['write_enabled'])

    def test_task_dirty_checkout_rejected(self):
        (self.repo/'price.py').write_text('changed')
        with self.assertRaises(ValueError):self.start(mode='task',allow_write=True)

    def test_scope_escape_symlink_rejected(self):
        (self.repo/'escape').symlink_to(self.root)
        with self.assertRaises(ValueError):w.safe_path(self.repo,'escape/packet.json')
        with self.assertRaises(ValueError):w.safe_path(self.repo,'../packet.json')

    def test_task_scope_violation_retained(self):
        a=self.start(mode='task',allow_write=True)
        def execute(*_):
            (self.repo/'unwanted.py').write_text('bad')
            return {'summary':'done','changed_files':['unwanted.py'],'checks':[],'limitations':[]},None,[]
        with patch.object(w,'execute',side_effect=execute):self.assertEqual(w.ask(a)['status'],'scope_violation')
        self.assertTrue((self.repo/'unwanted.py').exists())

    def test_task_success_requires_main_checks(self):
        a=self.start(mode='task',allow_write=True)
        def execute(*_):
            (self.repo/'price.py').write_text('def price(value):\n    return 100 if value is None else value\n')
            return {'summary':'fixed','changed_files':['price.py'],'checks':[],'limitations':[]},None,[]
        with patch.object(w,'execute',side_effect=execute):self.assertEqual(w.ask(a)['status'],'awaiting_judgment')
        dec={'conclusions':[{'id':'TASK','status':'confirmed','reason':'verified','evidence':[{'path':'price.py','line':2,'quote':'return 100 if value is None else value'}]}],'continue':False,'feedback':'','checks':[]}
        p=self.root/'decision.json';w.save(p,dec);ja=argparse.Namespace(run=a.run,decision=str(p))
        with self.assertRaises(ValueError):w.judge(ja)
        dec['checks']=['Executed zero, None and nonzero cases'];w.save(p,dec)
        self.assertEqual(w.judge(ja)['status'],'complete')

    def test_main_cannot_drop_question(self):
        a=self.start();self.call(a)
        p=self.root/'dec.json';w.save(p,{'conclusions':[],'continue':False,'feedback':'','checks':[]})
        with self.assertRaises(ValueError):w.judge(argparse.Namespace(run=a.run,decision=str(p)))

    def test_new_findings_must_be_adjudicated(self):
        a=self.start();r=self.answer();f=dict(r['findings'][0]);f.update(id='R1-N1',stance='new');r['findings'].append(f)
        self.call(a,r)
        with self.assertRaises(ValueError):self.decide(a)

    def test_observed_model_mismatch(self):
        a=self.start()
        with patch.object(w,'execute',return_value=(self.answer(),None,['main-model'])):
            self.assertEqual(w.ask(a)['status'],'failed')

    def test_process_timeout_terminates(self):
        rd=self.root/'process';rd.mkdir()
        with self.assertRaises(TimeoutError):w.execute([__import__('sys').executable,'-c','import time;time.sleep(20)'],'',rd,.1)
        pid=w.read(rd/'process.json')['pid']
        with self.assertRaises(ProcessLookupError):__import__('os').kill(pid,0)

    def test_failed_event_even_zero_exit_rejected(self):
        rd=self.root/'process';rd.mkdir()
        with self.assertRaises(ValueError):w.execute([__import__('sys').executable,'-c','print(\'{"type":"turn.failed"}\')'],'',rd,3)

    def test_mode_only_change_detected(self):
        a=self.start()
        (self.repo/'price.py').chmod(0o755)
        self.assertEqual(w.ask(a)['status'],'source_changed')

    def test_quote_variation_is_not_new_evidence(self):
        a=self.start();self.call(a,self.answer('value'));self.decide(a,True)
        self.call(a,self.answer('value or 100'))
        self.assertEqual(self.decide(a,True,'unresolved')['status'],'no_new_evidence')

    def test_quoted_and_inline_mcp_disabled(self):
        a=self.start();s=w.read(self.run/'state.json')
        fake=self.root/'home';fake.mkdir()
        (fake/'config.toml').write_text('mcp_servers = { "external-service" = { command = "tool" } }')
        with patch.object(w,'home',return_value=fake):
            argv=w.child_argv(s,self.root)
        self.assertIn('mcp_servers.external-service.enabled=false',argv)

    def test_failure_snapshot_error_is_terminal(self):
        a=self.start();original=w.snapshot(self.repo)
        with patch.object(w,'execute',side_effect=ValueError('model failed')), patch.object(w,'snapshot',side_effect=[original,ValueError('disk error')]):
            self.assertEqual(w.ask(a)['status'],'failed')
        s=w.read(self.run/'state.json')
        self.assertEqual(s['status'],'failed')
        self.assertIn('snapshot_error',s['rounds'][0])

    def test_unsupported_mcp_name_fails_closed(self):
        a=self.start();s=w.read(self.run/'state.json')
        fake=self.root/'home';fake.mkdir()
        (fake/'config.toml').write_text('mcp_servers = { "external.service" = { command = "tool" } }')
        with patch.object(w,'home',return_value=fake), self.assertRaises(ValueError):w.child_argv(s,self.root)

    def test_mode_change_outside_task_scope_stops(self):
        other=self.repo/'other.py';other.write_text('pass\n');self.g('add','.');self.g('commit','-qm','other')
        a=self.start(mode='task',allow_write=True)
        def execute(*_):
            other.chmod(0o755)
            return {'summary':'done','changed_files':[],'checks':[],'limitations':[]},None,[]
        with patch.object(w,'execute',side_effect=execute):self.assertEqual(w.ask(a)['status'],'scope_violation')

    def test_main_can_override_priority(self):
        a=self.start();self.call(a)
        p=self.root/'dec.json';w.save(p,{'conclusions':[{'id':'F1','status':'confirmed','priority':'P2','reason':'main severity correction','evidence':[{'path':'price.py','line':2,'quote':'value or 100'}]}],'continue':False,'feedback':'','checks':['verified']})
        self.assertEqual(w.judge(argparse.Namespace(run=a.run,decision=str(p)))['status'],'complete')
        self.assertEqual(w.read(self.run/'state.json')['conclusions'][0]['priority'],'P2')

    def test_config_frozen_per_run(self):
        p=self.root/'config.json';w.save(p,{'assistant_model':'future-flash'})
        a=self.start(config=str(p),assistant_model=None)
        w.save(p,{'assistant_model':'main-model'})
        self.assertEqual(w.read(self.run/'state.json')['config']['assistant_model'],'future-flash')

    def test_dirty_submodule_content_change_detected(self):
        child=self.root/'child';child.mkdir()
        subprocess.run(['git','init','-q',str(child)],check=True)
        (child/'a.txt').write_text('initial')
        subprocess.run(['git','-C',str(child),'add','.'],check=True)
        subprocess.run(['git','-C',str(child),'-c','user.name=Test','-c','user.email=test@example.invalid','commit','-qm','child'],check=True)
        subprocess.run(['git','-C',str(self.repo),'-c','protocol.file.allow=always','submodule','add','-q',str(child),'sub'],check=True)
        self.g('commit','-qam','submodule')
        (self.repo/'sub/a.txt').write_text('dirty one')
        before=w.snapshot(self.repo)
        (self.repo/'sub/a.txt').write_text('dirty two')
        self.assertIn('sub',w.changes(before,w.snapshot(self.repo)))

    def test_readonly_task_write_is_violation(self):
        a=self.start(mode='task')
        def execute(*_):
            (self.repo/'price.py').write_text('unauthorized')
            return {'summary':'done','changed_files':['price.py'],'checks':[],'limitations':[]},None,[]
        with patch.object(w,'execute',side_effect=execute):self.assertEqual(w.ask(a)['status'],'scope_violation')

    def test_invalid_budget(self):
        p=self.root/'config.json';w.save(p,{'max_rounds':0});self.args.config=str(p)
        with self.assertRaises(ValueError):w.config(self.args)

    def prepare_native(self):
        rt = self.codex_home / 'multi-model-workflow-runtime'
        rt.mkdir(exist_ok=True)
        w.save(rt / 'installation.json', {'active': True})
        a = self.start(transport='native')
        with patch.object(w, 'execute') as execute:
            dispatch = w.ask(a)
            execute.assert_not_called()
        self.assertRegex(dispatch['agent_path'], r'^/root/mmw_[0-9a-f]{32}$')
        self.assertEqual(dispatch['model'], 'aux-model')
        self.assertEqual(dispatch['fork_turns'], 'none')
        return a, dispatch

    def native_intake(self, a, dispatch, delivered=True, **kw):
        s = w.read(self.run / 'state.json')
        path = Path(s['rounds'][-1]['registry_path'])
        if path.exists() and delivered:
            reg = w.read(path);reg['requests'] = 1;reg['message_id'] = 'test-message';w.save(path,reg)
        result = self.root / 'native-answer.json';w.save(result,self.answer())
        params = dict(run=a.run, agent_path=dispatch['agent_path'], terminal_confirmed=True, result=str(result), error=None)
        params.update(kw)
        return w.native_result(argparse.Namespace(**params))

    def test_native_dispatch_once_and_main_gate(self):
        a, dispatch = self.prepare_native()
        with self.assertRaises(ValueError):w.ask(a)
        self.assertEqual(self.native_intake(a,dispatch)['status'],'awaiting_judgment')
        s=w.read(self.run/'state.json');self.assertTrue(w.read(s['rounds'][0]['registry_path'])['closed'])
        with self.assertRaises(ValueError):w.ask(a)
        self.assertEqual(self.decide(a)['status'],'complete')

    def test_native_requires_exact_terminal_agent(self):
        a, dispatch = self.prepare_native()
        for kw in ({'terminal_confirmed':False},{'agent_path':'/root/wrong'}):
            with self.assertRaises(ValueError):self.native_intake(a,dispatch,**kw)
        s=w.read(self.run/'state.json');self.assertFalse(w.read(s['rounds'][0]['registry_path'])['closed'])

    def test_native_missing_delivery_fails_without_retry(self):
        a, dispatch = self.prepare_native()
        self.assertEqual(self.native_intake(a,dispatch,delivered=False)['status'],'failed')
        with self.assertRaises(ValueError):w.ask(a)

    def test_native_deadline_rejects_late_result_and_closes(self):
        a, dispatch = self.prepare_native()
        s=w.read(self.run/'state.json');s['rounds'][0]['deadline']=0;w.save(self.run/'state.json',s)
        self.assertEqual(self.native_intake(a,dispatch)['status'],'failed')
        self.assertTrue(w.read(s['rounds'][0]['registry_path'])['closed'])

    def test_native_missing_registry_still_persists_failure(self):
        a, dispatch = self.prepare_native()
        s=w.read(self.run/'state.json');Path(s['rounds'][0]['registry_path']).unlink()
        self.assertEqual(self.native_intake(a,dispatch)['status'],'failed')
        self.assertEqual(w.read(self.run/'state.json')['status'],'failed')

    def test_native_scope_audit_preserves_unauthorized_change(self):
        a, dispatch = self.prepare_native()
        (self.repo/'other.txt').write_text('unexpected')
        self.assertEqual(self.native_intake(a,dispatch)['status'],'scope_violation')
        self.assertTrue((self.repo/'other.txt').exists())

    def test_native_unique_rounds_and_same_evidence_stop(self):
        a, first = self.prepare_native();self.native_intake(a,first);self.decide(a,True)
        second=w.ask(a);self.assertNotEqual(first['agent_path'],second['agent_path'])
        self.native_intake(a,second)
        self.assertEqual(self.decide(a,True,'unresolved')['status'],'no_new_evidence')

    def test_native_round_cap_shared(self):
        a, dispatch = self.prepare_native()
        s=w.read(self.run/'state.json');s['config']['max_rounds']=1;w.save(self.run/'state.json',s)
        self.native_intake(a,dispatch);self.assertEqual(self.decide(a,True)['status'],'round_limit')

    def test_cli_cannot_accept_native_parameters(self):
        a=self.start();a.agent_parent='/root'
        with patch.object(w,'execute') as execute, self.assertRaises(ValueError):w.ask(a)
        execute.assert_not_called()

    def test_native_abandon_requires_host_confirmation(self):
        a, dispatch = self.prepare_native()
        args=argparse.Namespace(run=a.run,no_live_workers=False,reason='host confirmed interruption')
        with self.assertRaises(ValueError):w.abandon(args)
        args.no_live_workers=True;self.assertEqual(w.abandon(args)['status'],'failed')
        s=w.read(self.run/'state.json');self.assertTrue(w.read(s['rounds'][0]['registry_path'])['closed'])

if __name__=='__main__':unittest.main()
