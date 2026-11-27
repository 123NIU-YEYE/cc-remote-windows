"""Runtime text I/O must not depend on the host's locale encoding.

The Windows Wrapper runs with a cp936 locale and Python UTF-8 mode off. Text
opened without an explicit encoding is then decoded as GBK, which silently broke
Claude history timestamps. Every text-mode file or subprocess stream in the
runtime package therefore names its encoding.
"""
import ast
from pathlib import Path


PACKAGE = Path(__file__).resolve().parents[1] / "cc_remote"
SUBPROCESS_CALLS = {"run", "check_output", "check_call", "Popen"}


def _constant(node):
    return node.value if isinstance(node, ast.Constant) else None


def _text_mode(mode) -> bool:
    return not isinstance(mode, str) or "b" not in mode


def _locale_dependent(node: ast.Call) -> bool:
    keywords = {keyword.arg: keyword.value for keyword in node.keywords}
    if "encoding" in keywords:
        return False
    func = node.func
    name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
    receiver = ast.unparse(func.value) if isinstance(func, ast.Attribute) else ""
    if (name == "open" and isinstance(func, ast.Name)) or (
            name == "fdopen" and receiver == "os") or (
            name == "open" and receiver == "io"):
        mode = (_constant(node.args[1]) if len(node.args) > 1
                else _constant(keywords.get("mode")) if "mode" in keywords else "r")
        return _text_mode(mode) and len(node.args) < 4
    if name == "open" and isinstance(func, ast.Attribute) and receiver != "os":
        # Path.open(mode=...): its first positional argument is the mode. Calls
        # such as Image.open(stream) or opener.open(request) are not file text I/O.
        if node.args and not isinstance(node.args[0], ast.Constant):
            return False
        mode = (_constant(node.args[0]) if node.args
                else _constant(keywords.get("mode")) if "mode" in keywords else "r")
        return _text_mode(mode) and len(node.args) < 3
    if name in ("read_text", "write_text") and isinstance(func, ast.Attribute):
        return len(node.args) < (1 if name == "read_text" else 2)
    if name in SUBPROCESS_CALLS:
        flag = keywords.get("text", keywords.get("universal_newlines"))
        return flag is not None and _constant(flag) is not False
    return False


def test_runtime_text_io_names_its_encoding():
    offenders = []
    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _locale_dependent(node):
                offenders.append(
                    f"{path.relative_to(PACKAGE.parent)}:{node.lineno}: "
                    f"{ast.unparse(node)[:100]}")
    assert not offenders, "text I/O without encoding=:\n" + "\n".join(offenders)


def test_guard_flags_locale_dependent_calls():
    def flagged(source: str) -> bool:
        return _locale_dependent(ast.parse(source, mode="eval").body)

    assert flagged("open(path)")
    assert flagged("open(path, 'w')")
    assert flagged("os.fdopen(fd, 'w')")
    assert flagged("path.open()")
    assert flagged("path.read_text()")
    assert flagged("tmp.write_text(payload)")
    assert flagged("subprocess.run(argv, text=True)")
    assert not flagged("open(path, 'rb')")
    assert not flagged("open(path, encoding='utf-8')")
    assert not flagged("path.read_text('utf-8')")
    assert not flagged("Image.open(io.BytesIO(raw))")
    assert not flagged("os.open(name, flags)")
    assert not flagged("subprocess.run(argv, capture_output=True)")
