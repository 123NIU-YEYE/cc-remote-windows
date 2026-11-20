"""Reviewed Work startup failures, without exposing native diagnostics."""

WORK_SANDBOX_UNAVAILABLE = "work_sandbox_unavailable"
WORK_SANDBOX_UNAVAILABLE_MESSAGE = (
    "当前设备无法启动 Claude Work 的隔离环境。"
    "可切换到 Code 继续使用；Windows 用户需在 WSL2 中运行 Work。"
)


class ClaudeWorkSandboxUnavailable(RuntimeError):
    def __init__(self):
        super().__init__(WORK_SANDBOX_UNAVAILABLE_MESSAGE)


def sandbox_unavailable(error: Exception) -> bool:
    # This explicit native failure proves initialization stopped before query.
    # Do not infer sandbox availability from unrelated permission/auth errors.
    return "Sandbox required but unavailable:" in str(error)
