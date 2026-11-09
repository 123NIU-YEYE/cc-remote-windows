"""Claude transcript timestamps must survive a non-UTF-8 service locale."""
import json
import os
from pathlib import Path
import subprocess
import sys


def test_transcript_timestamps_read_unicode_records_without_utf8_mode(tmp_path):
    sid = "a83fa433-86e2-44ea-9c6d-07dfc3240a3f"
    transcript = tmp_path / f"{sid}.jsonl"
    rows = [
        {"type": "user", "uuid": "first-user", "timestamp": "2026-11-09T01:18:29.336Z",
         "message": {"role": "user", "content": "你好吗"}},
        {"type": "assistant", "uuid": "first-reply", "timestamp": "2026-11-09T01:18:41.412Z",
         "message": {"role": "assistant", "content": [{"type": "text", "text": "项目/🌏 好"}]}},
    ]
    transcript.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    env = dict(os.environ, PYTHONUTF8="0", PYTHONCOERCECLOCALE="0", LC_ALL="C", LANG="C")
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    # A missing entry makes History stamp that turn with the read time, and the
    # browser orders turns by that time.
    code = (
        "import sys; from cc_remote.wrapper.stream import transcript_timestamps; "
        "assert not sys.flags.utf8_mode; "
        "stamps = transcript_timestamps(sys.argv[1], path=sys.argv[2]); "
        "assert stamps == {'first-user': 1794187109.336, 'first-reply': 1794187121.412}, stamps"
    )
    result = subprocess.run(
        [sys.executable, "-X", "utf8=0", "-c", code, sid, str(transcript)],
        env=env, capture_output=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
