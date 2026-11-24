"""Exact-generation argv reads on Windows (the platform has no procfs)."""
import os
import subprocess
import sys

import pytest

from cc_remote.wrapper.process_scan import ProcessIdentity, process_command, process_identity

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="native Windows processes")


def test_reads_argv_of_the_exact_process_generation():
    marker = "cc-remote-argv-probe"
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", marker])
    try:
        identity = process_identity(child.pid)
        assert identity is not None
        args = process_command(identity)
        assert args is not None and args[-1] == marker.encode()
        # A recycled PID is a different generation and must not be read.
        stale = ProcessIdentity(identity.pid, identity.start_ticks - 1000)
        assert process_command(stale) is None
    finally:
        child.kill()
        child.wait()
    assert process_command(identity) is None


def test_current_process_argv_round_trips():
    identity = process_identity(os.getpid())
    assert identity is not None
    args = process_command(identity)
    assert args is not None and args[0]
