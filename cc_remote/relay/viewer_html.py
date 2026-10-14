"""Insert the trusted keyboard helper before page policies, without buffering HTML."""
from __future__ import annotations

import re


async def preview_html(stream, helper: bytes):
    """Keep the BOM, leading comments and doctype ahead of the helper.

    Inserting before the doctype would put standards-mode pages into quirks
    mode. Only scan the prologue; after its first content token, pass bytes
    through unchanged. Even a huge leading comment/doctype is streamed with
    at most a partial marker retained between resource chunks.
    """
    pending = b""
    state = "bom"
    quote = 0
    async for chunk in stream:
        pending += chunk
        while pending:
            if state == "done":
                yield pending
                pending = b""
            elif state == "bom":
                if len(pending) < 3:
                    break
                if pending.startswith(b"\xef\xbb\xbf"):
                    yield pending[:3]
                    pending = pending[3:]
                state = "before"
            elif state == "before":
                whitespace = re.match(rb"[\t\n\f\r ]+", pending)
                if whitespace:
                    yield pending[:whitespace.end()]
                    pending = pending[whitespace.end():]
                    continue
                if any(marker.startswith(pending.lower()) for marker in (b"<!--", b"<!doctype", b"<?")):
                    break
                if pending.startswith(b"<!--"):
                    yield pending[:4]
                    pending = pending[4:]
                    state = "comment-start"
                elif pending.lower().startswith(b"<!doctype"):
                    yield pending[:9]
                    pending = pending[9:]
                    state = "doctype"
                elif pending.startswith((b"<!", b"<?")):
                    # HTML treats other declarations/processing instructions
                    # as bogus comments; they need not change document mode.
                    state = "bogus-comment"
                else:
                    yield helper
                    state = "done"
            elif state == "comment-start":
                if len(pending) < 2:
                    break
                end = 1 if pending.startswith(b">") else 2 if pending.startswith(b"->") else 0
                if end:  # HTML's abruptly closed empty comments.
                    yield pending[:end]
                    pending = pending[end:]
                    state = "before"
                else:
                    state = "comment"
            elif state in {"comment", "bogus-comment"}:
                end = re.search(rb"--!?>" if state == "comment" else rb">", pending)
                if end:
                    yield pending[:end.end()]
                    pending = pending[end.end():]
                    state = "before"
                else:
                    # Keep only enough bytes to recognize a split terminator.
                    keep = 3 if state == "comment" else 0
                    size = max(0, len(pending) - keep)
                    if size:
                        yield pending[:size]
                        pending = pending[size:]
                    break
            else:  # doctype: quoted public/system identifiers may contain '>'.
                end = len(pending)
                for index, char in enumerate(pending):
                    if quote:
                        if char == quote:
                            quote = 0
                    elif char in (34, 39):
                        quote = char
                    elif char == 62:
                        end = index + 1
                        state = "before"
                        break
                yield pending[:end]
                pending = pending[end:]
    if pending:
        yield pending
    if state != "done":
        yield helper
