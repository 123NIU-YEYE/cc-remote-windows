"""cc-remote: self-hosted remote control for Claude Code.

Two independent links:
  - model link:  cc -> local proxy (127.0.0.1:19191) -> z.AI GLM  (untouched)
  - control link: client <-> relay(WS) <-> wrapper <-> ClaudeSDKClient <-> cc
"""

__version__ = "4.0.29"

# Define the POSIX-only ``os`` attributes that Windows lacks before any wrapper
# module reads them. No-op on Unix. See cc_remote.wrapper.os_compat.
from cc_remote.wrapper.os_compat import install_windows_shims as _install_windows_shims

_install_windows_shims()
del _install_windows_shims

