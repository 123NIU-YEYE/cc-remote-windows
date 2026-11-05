import os
import subprocess
import sys

import pytest

from cc_remote.wrapper import codex_external as external
from cc_remote.wrapper.process_scan import ProcessIdentity


def test_windows_holder_scan_keeps_unknown_process_ownership_closed(monkeypatch):
    pid = ProcessIdentity(100, 1234)
    monkeypatch.setattr('cc_remote.wrapper.process_scan.process_owner_uid', lambda _: None)
    monkeypatch.setattr('cc_remote.wrapper.windows_files.writable_file_holders', lambda paths, selected: ({'s': set()}, True))
    scan = external._windows_writable_rollout_holders(
        {'s': 'rollout.jsonl'}, (), ([(pid, 1, 0, (b'C:\\bin\\codex.exe', b'app-server'))], True))
    assert not scan.complete


def test_windows_holders_keep_private_app_server_separate_and_exclude_owned(monkeypatch):
    owned, foreign = ProcessIdentity(10, 100), ProcessIdentity(20, 200)
    monkeypatch.setattr('cc_remote.wrapper.process_scan.process_owner_uid', lambda _: os.getuid())
    def scan(paths, selected):
        assert selected == {20: foreign}
        return {'s': {foreign}}, True
    monkeypatch.setattr('cc_remote.wrapper.windows_files.writable_file_holders', scan)
    result = external._windows_writable_rollout_holders({'s': 'rollout'}, (owned,), ([
        (owned, 1, 0, (b'codex.exe', b'app-server')),
        (foreign, 1, 0, (b'codex.exe', b'app-server')),
    ], True))
    assert result.complete and result.holders['s'] == {foreign}
    assert result.passive_holders['s'] == result.private_holders['s'] == {foreign}


def test_windows_sandbox_service_is_not_a_conversation_writer(monkeypatch):
    pid = ProcessIdentity(100, 1234)
    monkeypatch.setattr('cc_remote.wrapper.process_scan.process_owner_uid', lambda _: None)
    def scan(paths, selected):
        assert not selected
        return {'s': set()}, True
    monkeypatch.setattr('cc_remote.wrapper.windows_files.writable_file_holders', scan)
    result = external._windows_writable_rollout_holders({'s': 'rollout'}, (), ([
        (pid, 1, 0, (b'codex-windows-sandbox-service.exe',)),
    ], True))
    assert result.complete


def test_windows_incomplete_kernel_table_stays_read_only(monkeypatch):
    monkeypatch.setattr('cc_remote.wrapper.windows_files.writable_file_holders', lambda *args: ({'s': set()}, False))
    assert not external._windows_writable_rollout_holders({'s': 'rollout'}, (), ([], True)).complete


def test_targeted_snapshot_filters_helpers_before_reading_process_memory(monkeypatch):
    from cc_remote.wrapper import process_scan
    class Unreadable:
        info = {'pid': 1, 'name': 'codex-windows-sandbox-service.exe'}
        def ppid(self):
            pytest.fail('Unrelated helper process memory must not be read')
    class FakePsutil:
        NoSuchProcess = ProcessLookupError
        AccessDenied = PermissionError
        @staticmethod
        def process_iter(fields):
            return iter([Unreadable()])
    monkeypatch.setattr(process_scan, '_windows_psutil', lambda: FakePsutil)
    assert process_scan.windows_process_snapshot(names={'codex.exe'}) == ([], True)


def test_targeted_snapshot_keeps_unreadable_codex_metadata_incomplete(monkeypatch):
    from cc_remote.wrapper import process_scan
    class Unreadable:
        info = {'pid': 1, 'name': 'codex.exe'}
        def ppid(self):
            raise PermissionError()
    class FakePsutil:
        NoSuchProcess = ProcessLookupError
        AccessDenied = PermissionError
        @staticmethod
        def process_iter(fields):
            return iter([Unreadable()])
    monkeypatch.setattr(process_scan, '_windows_psutil', lambda: FakePsutil)
    assert process_scan.windows_process_snapshot(names={'codex.exe'}) == ([], False)


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows kernel handle access')
@pytest.mark.parametrize('mode,writable', [('rb', False), ('r+b', True)])
def test_kernel_scan_matches_actual_file_id_and_granted_write_access(tmp_path, mode, writable):
    from cc_remote.wrapper.process_scan import process_identity
    from cc_remote.wrapper.windows_files import writable_file_holders
    target = tmp_path / 'original.jsonl'
    target.write_bytes(b'{}\n')
    code = 'import sys; f=open(sys.argv[1],sys.argv[2]); print("ready",flush=True); sys.stdin.readline()'
    child = subprocess.Popen([sys.executable, '-c', code, str(target), mode], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == 'ready'
        identity = process_identity(child.pid)
        assert identity is not None
        holders, complete = writable_file_holders({'s': str(target)}, {child.pid: identity})
        assert complete
        assert holders['s'] == ({identity} if writable else set())
    finally:
        child.communicate('\n', timeout=10)
    assert child.returncode == 0


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows kernel handle access')
def test_kernel_scan_matches_a_hardlink_instead_of_filename_text(tmp_path):
    from cc_remote.wrapper.process_scan import process_identity
    from cc_remote.wrapper.windows_files import writable_file_holders
    target, alias = tmp_path / 'original.jsonl', tmp_path / 'alias.jsonl'
    target.write_bytes(b'{}\n')
    os.link(target, alias)
    code = 'import sys; f=open(sys.argv[1],"r+b"); print("ready",flush=True); sys.stdin.readline()'
    child = subprocess.Popen([sys.executable, '-c', code, str(target)], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == 'ready'
        identity = process_identity(child.pid)
        holders, complete = writable_file_holders({'s': str(alias)}, {child.pid: identity})
        assert complete and holders['s'] == {identity}
    finally:
        child.communicate('\n', timeout=10)
