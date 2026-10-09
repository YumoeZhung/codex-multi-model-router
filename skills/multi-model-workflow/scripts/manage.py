#!/usr/bin/env python3
"""Install/remove only this skill's optional wrapper; never edit router/auth/model configs."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def digest(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def runtime(home):
    return Path(home) / 'multi-model-workflow-runtime'


def read(p):
    return json.loads(Path(p).read_text())


def write(p, value):
    p = Path(p)
    tmp = p.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    tmp.replace(p)


def restart(plist, label):
    domain = 'gui/' + str(os.getuid())
    result = subprocess.run(['launchctl', 'bootout', domain + '/' + label], capture_output=True, text=True)
    if result.returncode and 'No such process' not in result.stderr and 'Could not find service' not in result.stderr:
        raise RuntimeError('Service unload failed: ' + result.stderr.strip())
    # launchd may return from bootout before the label is reusable.
    for attempt in range(5):
        result = subprocess.run(['launchctl', 'bootstrap', domain, str(plist)], capture_output=True, text=True)
        if not result.returncode:
            break
        if result.returncode != 5 or attempt == 4:
            raise RuntimeError('Service start failed: ' + result.stderr.strip())
        time.sleep(.3)
    argv = plistlib.loads(Path(plist).read_bytes())['ProgramArguments']
    if '--port' not in argv:
        raise ValueError('Cannot verify router health without an explicit port')
    url = 'http://127.0.0.1:' + str(int(argv[argv.index('--port') + 1])) + '/health'
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for attempt in range(20):
        try:
            with opener.open(url, timeout=.5) as response:
                if json.load(response) == {'status': 'ok', 'service': 'codex-model-router'}:
                    return
        except (OSError, ValueError):
            pass
        time.sleep(.25)
    raise RuntimeError('Router did not become healthy after service restart')


def install(home, plist_path, restart_fn=restart):
    rt = runtime(home)
    manifest = rt / 'installation.json'
    if manifest.exists():
        state = read(manifest)
        if state.get('active') or state.get('phase', 'inactive') != 'inactive':
            raise ValueError('Existing installation requires remove-bridge before reinstall')
    plist_path = Path(plist_path).resolve()
    plist = plistlib.loads(plist_path.read_bytes())
    argv = plist.get('ProgramArguments', [])
    if len(argv) < 2 or Path(argv[1]).name != 'router.py' or '--port' not in argv:
        raise ValueError('Expected existing Python router.py service; inspect custom services manually')
    router = Path(argv[1]).resolve()
    rt.mkdir(parents=True, exist_ok=True, mode=0o700)
    (rt / 'requests').mkdir(exist_ok=True, mode=0o700)
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    backup = rt / ('service-before-' + stamp + '.plist')
    shutil.copy2(plist_path, backup)
    shutil.copy2(ROOT / 'scripts/native_bridge.py', rt / 'native_bridge.py')
    shutil.copy2(Path(__file__), rt / 'remove_skill.py')
    new_argv = [argv[0], str(rt / 'native_bridge.py'), '--router', str(router), '--registry', str(rt / 'requests')] + argv[2:]
    state = {'version': 1, 'active': False, 'phase': 'installing', 'plist': str(plist_path), 'label': plist['Label'], 'backup': str(backup),
             'skill_root': str(ROOT), 'original_argv': argv, 'installed_argv': new_argv, 'router': str(router), 'router_sha256': digest(router)}
    write(manifest, state)
    plist['ProgramArguments'] = new_argv
    try:
        plist_path.write_bytes(plistlib.dumps(plist))
        restart_fn(plist_path, plist['Label'])
        state['active'] = True
        state['phase'] = 'active'
        write(manifest, state)
    except BaseException:
        # Re-read so unrelated edits made during startup are not overwritten.
        current = plistlib.loads(plist_path.read_bytes())
        if current.get('ProgramArguments') not in (argv, new_argv):
            raise RuntimeError('Service command changed during install; recovery records retained')
        current['ProgramArguments'] = argv
        plist_path.write_bytes(plistlib.dumps(current))
        restart_fn(plist_path, plist['Label'])
        state.update(active=False, phase='inactive')
        write(manifest, state)
        raise
    return state


def active_registrations(rt):
    import time
    return [p for p in (rt / 'requests').glob('*.json') if not read(p).get('closed')]


def remove_bridge(home, restart_fn=restart):
    rt = runtime(home)
    p = rt / 'installation.json'
    if not p.exists():
        return {'active': False, 'changed': False}
    state = read(p)
    if active_registrations(rt):
        raise ValueError('Live native task registrations exist; stop and account for agents before uninstall')
    plist_path = Path(state['plist'])
    plist = plistlib.loads(plist_path.read_bytes())
    actual = plist.get('ProgramArguments')
    if actual not in (state['installed_argv'], state['original_argv']):
        raise ValueError('Service command changed since install; preserve it and resolve manually')
    if actual == state['original_argv'] and not state.get('active') and state.get('phase', 'inactive') == 'inactive':
        return {'active': False, 'changed': False}
    # Restore only our owned field. Later environment/proxy/other service edits survive.
    plist['ProgramArguments'] = state['original_argv']
    installed_bytes = plist_path.read_bytes()
    state['phase'] = 'removing'
    write(p, state)
    try:
        plist_path.write_bytes(plistlib.dumps(plist))
        restart_fn(plist_path, state['label'])
    except BaseException:
        current = plistlib.loads(plist_path.read_bytes())
        if current.get('ProgramArguments') not in (state['original_argv'], actual):
            raise RuntimeError('Service command changed during removal; recovery records retained')
        current['ProgramArguments'] = plistlib.loads(installed_bytes)['ProgramArguments']
        plist_path.write_bytes(plistlib.dumps(current))
        restart_fn(plist_path, state['label'])
        raise
    state.update(active=False, phase='inactive')
    write(p, state)
    return {'active': False, 'changed': True, 'router_unchanged': digest(state['router']) == state['router_sha256']}


POLICY = """<!-- multi-model-workflow:begin -->
在主 Agent 准备委派子任务时，先读取个人技能目录中的 multi-model-workflow/SKILL.md，统一判断任务是否适合委派、明确指定模型并验收；原生和 CLI 通道遵守同一规则，同一子任务只走一条通道。优先使用已验证的原生派发，不把所有子 Agent 固定为某个模型。需要强推理或特定工具能力时保留强模型；能可靠脚本化的工作优先脚本。辅助 Agent 已收到具体任务时不要递归启动本技能或再派发。
<!-- multi-model-workflow:end -->"""


def policy_path(home):
    return runtime(home) / 'policy-installation.json'


def enable_policy(home):
    rt = runtime(home);rt.mkdir(parents=True, exist_ok=True, mode=0o700)
    if policy_path(home).exists() and read(policy_path(home)).get('active'):
        raise ValueError('Delegation policy already installed')
    p = Path(home) / 'AGENTS.md'
    original = p.read_text() if p.exists() else ''
    if 'multi-model-workflow:begin' in original:
        raise ValueError('Existing managed policy must be inspected first')
    addition = '\n\n' + POLICY + '\n'
    backup = rt / ('AGENTS-before-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.md')
    backup.write_text(original)
    shutil.copy2(Path(__file__), rt / 'remove_skill.py')
    write(policy_path(home), {'active': True, 'path': str(p), 'backup': str(backup), 'addition': addition, 'previously_existed': p.exists(), 'skill_root': str(ROOT)})
    p.write_text(original + addition)
    return {'policy_installed': True, 'backup': str(backup)}


def remove_policy(home):
    state_path = policy_path(home)
    if not state_path.exists() or not read(state_path).get('active'):
        return
    state = read(state_path);p = Path(state['path']);current = p.read_text() if p.exists() else ''
    if state['addition'] not in current and 'multi-model-workflow:begin' not in current:
        state['active'] = False;write(state_path, state)
        return
    if current.count(state['addition']) != 1:
        raise ValueError('Managed policy changed; preserve user edits and resolve manually')
    restored = current.replace(state['addition'], '', 1)
    if not restored and not state['previously_existed']:
        p.unlink()
    else:
        p.write_text(restored)
    state['active'] = False;write(state_path, state)


def uninstall(home, archive, restart_fn=restart, skill_root=ROOT):
    home = Path(home).resolve()
    archive = Path(archive).resolve()
    skill_root = Path(skill_root).resolve()
    sources = [skill_root, home / 'multi-model-workflow.json', home / 'multi-model-workflow-runs', runtime(home)]
    # Validate every destination before restoring service or moving any artifact.
    for forbidden in sources + [home / 'skills', Path.home() / '.agents/skills']:
        forbidden = forbidden.resolve()
        if archive == forbidden or forbidden in archive.parents or archive in forbidden.parents:
            raise ValueError('Archive must be separate from all skill/runtime/run/discovery paths')
    if archive.exists():
        journal_path = runtime(home) / 'uninstall.json'
        if not journal_path.exists() or read(journal_path).get('archive') != str(archive):
            raise ValueError('Archive already exists without a matching uninstall journal')
    journal_path = runtime(home) / 'uninstall.json'
    if journal_path.exists():
        journal = read(journal_path)
        if journal.get('archive') != str(archive):
            raise ValueError('Resume the unfinished uninstall in its original archive: ' + journal['archive'])
        if journal.get('sources') != [str(p) for p in sources]:
            raise ValueError('Uninstall source paths changed; inspect recovery journal')
    else:
        journal = {'archive': str(archive), 'sources': [str(p) for p in sources], 'phase': 'prepared'}
    destinations = [archive / 'multi-model-workflow'] + [archive / p.name for p in sources[1:]]
    for source, dest in zip(sources, destinations):
        if source.exists() and dest.exists():
            raise ValueError('Source and archive copy both exist; preserve both and inspect: ' + str(source))
    if active_registrations(runtime(home)):
        raise ValueError('Live native task registrations exist; stop and account for agents before uninstall')
    # Refuse unfinished ledger state, even if its clock budget has expired.
    runs = set((home / 'multi-model-workflow-runs').glob('*/state.json'))
    for item in (runtime(home) / 'runs').glob('*.json'):
        runs.add(Path(read(item)['run_dir']) / 'state.json')
    for p in runs:
        p = p.resolve()
        if not p.exists() and sources[2] in p.parents:
            # A prior attempt may have moved default ledgers before runtime.
            moved = destinations[2] / p.relative_to(sources[2])
            if journal_path.exists() and moved.exists():
                p = moved
        if not p.exists():
            raise ValueError('Registered run ledger missing; account for its workers before uninstall: ' + str(p))
        if read(p).get('status') in ('running', 'awaiting_dispatch', 'awaiting_judgment', 'ready'):
            raise ValueError('Unfinished workflow: ' + str(p.parent) + '; finish or explicitly abandon it first')
    # Check policy ownership before changing service state.
    if policy_path(home).exists() and read(policy_path(home)).get('active'):
        ps = read(policy_path(home))
        current = Path(ps['path']).read_text() if Path(ps['path']).exists() else ''
        if ps['addition'] not in current and 'multi-model-workflow:begin' not in current:
            pass  # Already removed or installation interrupted before the append.
        elif current.count(ps['addition']) != 1:
            raise ValueError('Managed policy changed; preserve it and resolve manually')
    archive.mkdir(parents=True, exist_ok=True)
    runtime(home).mkdir(parents=True, exist_ok=True, mode=0o700)
    write(journal_path, journal)
    restored = remove_bridge(home, restart_fn)
    remove_policy(home)
    # Preserve Git history, host config, runtime records and evidence instead of deleting.
    # Move recovery runtime last. The journal and exact destinations permit
    # resuming after a partial move, without guessing that a missing ledger ended.
    for source, dest in zip(sources, destinations):
        if source.exists():
            shutil.move(str(source), str(dest))
    journal['phase'] = 'complete'
    write(destinations[-1] / 'uninstall.json', journal)
    return {'archive': str(archive), 'router_preserved': True, 'bridge': restored, 'new_session_required': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['status', 'install-bridge', 'remove-bridge', 'enable-policy', 'remove-policy', 'uninstall'])
    parser.add_argument('--home', default=os.environ.get('CODEX_HOME', str(Path.home() / '.codex')))
    parser.add_argument('--service')
    parser.add_argument('--archive')
    args = parser.parse_args()
    try:
        if args.action == 'install-bridge':
            if not args.service:raise ValueError('--service required')
            s = install(args.home, args.service)
            result = {'active': s['active'], 'backup': s['backup'], 'router_source_modified': False}
        elif args.action == 'remove-bridge':result = remove_bridge(args.home)
        elif args.action == 'enable-policy':result = enable_policy(args.home)
        elif args.action == 'remove-policy':
            remove_policy(args.home);result = {'policy_installed': False}
        elif args.action == 'uninstall':
            journal = runtime(args.home) / 'uninstall.json'
            pending_archive = read(journal)['archive'] if journal.exists() else None
            archive = args.archive or pending_archive or str(Path(args.home) / 'skill-archives' / ('multi-model-workflow-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S')))
            manifest = runtime(args.home) / 'installation.json'
            policy = policy_path(args.home)
            owner = read(manifest) if manifest.exists() else read(policy) if policy.exists() else {}
            skill_root = owner.get('skill_root', str(ROOT))
            result = uninstall(args.home, archive, skill_root=skill_root)
        else:
            p = runtime(args.home) / 'installation.json'
            result = {'bridge_active': read(p)['active'] if p.exists() else False, 'active_registrations': len(active_registrations(runtime(args.home)))}
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as e:
        print(json.dumps({'error': str(e)}, ensure_ascii=False), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
