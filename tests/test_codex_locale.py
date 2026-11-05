"""Native rollout discovery must survive a non-UTF-8 service locale."""
import json
import os
from pathlib import Path
import subprocess
import sys


def test_cold_rollout_discovery_reads_unicode_metadata_without_utf8_mode(tmp_path):
    home = tmp_path / "codex"
    directory = home / "sessions" / "2026" / "11" / "05"
    directory.mkdir(parents=True)
    sid = "native-unicode-metadata"
    rollout = directory / f"rollout-{sid}.jsonl"
    rollout.write_text(json.dumps({
        "type": "session_meta",
        "payload": {"id": sid, "cwd": "项目/🌏", "source": "cli"},
    }, ensure_ascii=False) + "\n", encoding="utf-8")
    env = dict(os.environ, PYTHONUTF8="0", PYTHONCOERCECLOCALE="0", LC_ALL="C", LANG="C")
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    code = (
        "import sys; from cc_remote.wrapper.codex_sessions import codex_rollout_path; "
        "assert not sys.flags.utf8_mode; "
        "assert codex_rollout_path(sys.argv[1], codex_home=sys.argv[2]) == sys.argv[3]"
    )
    result = subprocess.run(
        [sys.executable, "-X", "utf8=0", "-c", code, sid, str(home), str(rollout)],
        env=env, capture_output=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
