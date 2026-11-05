"""Bounded Windows writable-file ownership using actual kernel handles."""
from __future__ import annotations

import ctypes
import os
from typing import Mapping

from cc_remote.wrapper.process_scan import ProcessIdentity, process_identity

MAX_HANDLE_BYTES = 64 * 1024 * 1024
MAX_PROCESS_HANDLES = 8192


def writable_file_holders(
    paths: Mapping[str, str], processes: Mapping[int, ProcessIdentity],
) -> tuple[dict[str, set[ProcessIdentity]], bool]:
    """Match file IDs and granted write access; never inspect file contents.

    The caller selects in-scope process owners and excludes exact managed
    generations. Unreadable live processes or incomplete handle tables make
    the result incomplete. psutil.open_files cannot provide this guarantee on
    Windows and does not report the granted file access there.
    """
    result: dict[str, set[ProcessIdentity]] = {sid: set() for sid in paths}
    if os.name != "nt":
        return result, False
    from ctypes import wintypes as w

    class FileId(ctypes.Structure):
        _fields_ = [("volume", ctypes.c_ulonglong), ("id", ctypes.c_ubyte * 16)]

    class Entry(ctypes.Structure):
        _fields_ = [("object", ctypes.c_void_p), ("pid", ctypes.c_size_t),
                    ("handle", ctypes.c_size_t), ("access", w.ULONG),
                    ("creator", w.USHORT), ("type", w.USHORT),
                    ("attributes", w.ULONG), ("reserved", w.ULONG)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    native = ctypes.WinDLL("ntdll")
    kernel.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p,
                                  w.DWORD, w.DWORD, w.HANDLE]
    kernel.CreateFileW.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    kernel.OpenProcess.restype = w.HANDLE
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.GetFileType.argtypes = [w.HANDLE]
    kernel.GetFileType.restype = w.DWORD
    kernel.GetFileInformationByHandleEx.argtypes = [w.HANDLE, ctypes.c_int,
                                                   ctypes.c_void_p, w.DWORD]
    kernel.GetFileInformationByHandleEx.restype = w.BOOL
    kernel.DuplicateHandle.argtypes = [w.HANDLE, w.HANDLE, w.HANDLE,
                                      ctypes.POINTER(w.HANDLE), w.DWORD, w.BOOL, w.DWORD]
    kernel.DuplicateHandle.restype = w.BOOL
    native.NtQuerySystemInformation.argtypes = [w.ULONG, ctypes.c_void_p, w.ULONG,
                                               ctypes.POINTER(w.ULONG)]
    native.NtQuerySystemInformation.restype = ctypes.c_long
    watched = []
    process_handles = {}
    complete = True

    def file_id(handle):
        info = FileId()
        if not kernel.GetFileInformationByHandleEx(handle, 18, ctypes.byref(info), ctypes.sizeof(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        return info.volume, bytes(info.id)

    try:
        identities: dict[tuple[int, bytes], set[str]] = {}
        for sid, path in paths.items():
            if not os.path.isfile(path):
                continue
            handle = kernel.CreateFileW(path, 0x80, 7, None, 3, 0, None)
            if handle == ctypes.c_void_p(-1).value:
                return result, False
            watched.append(handle)
            identities.setdefault(file_id(handle), set()).add(sid)
        if not identities:
            return result, True
        size = 1024 * 1024
        while True:
            buffer = ctypes.create_string_buffer(size)
            required = w.ULONG()
            status = native.NtQuerySystemInformation(64, buffer, size, ctypes.byref(required))
            if status == 0:
                break
            if status & 0xFFFFFFFF != 0xC0000004:
                return result, False
            size = max(size * 2, required.value + 65536)
            if size > MAX_HANDLE_BYTES:
                return result, False
        count = ctypes.c_size_t.from_buffer(buffer).value
        offset = 2 * ctypes.sizeof(ctypes.c_size_t)
        if offset + count * ctypes.sizeof(Entry) > size:
            return result, False
        entries = (Entry * count).from_buffer(buffer, offset)
        file_types = {entry.type for entry in entries
                      if entry.pid == os.getpid() and entry.handle in watched}
        if not file_types:
            return result, False
        inspected: dict[int, int] = {}
        stable = {}
        current = kernel.GetCurrentProcess()
        for entry in entries:
            identity = processes.get(entry.pid)
            if identity is None or entry.type not in file_types or not entry.access & 6:
                continue
            inspected[entry.pid] = inspected.get(entry.pid, 0) + 1
            if inspected[entry.pid] > MAX_PROCESS_HANDLES:
                complete = False
                continue
            if entry.pid not in stable:
                stable[entry.pid] = process_identity(entry.pid) == identity
            if not stable[entry.pid]:
                continue
            if entry.pid not in process_handles:
                process_handles[entry.pid] = kernel.OpenProcess(0x40, False, entry.pid)
            process_handle = process_handles[entry.pid]
            if not process_handle:
                complete = False
                continue
            duplicate = w.HANDLE()
            if not kernel.DuplicateHandle(process_handle, entry.handle, current,
                                          ctypes.byref(duplicate), 0, False, 2):
                if ctypes.get_last_error() != 6:  # closed/reused source handle
                    complete = False
                continue
            try:
                if kernel.GetFileType(duplicate) != 1:  # local/disk file, not pipe/console
                    continue
                try:
                    matches = identities.get(file_id(duplicate), ())
                except OSError:
                    complete = False
                    continue
                if matches and process_identity(entry.pid) == identity:
                    for sid in matches:
                        result[sid].add(identity)
            finally:
                kernel.CloseHandle(duplicate)
        return result, complete
    except (OSError, ValueError, OverflowError):
        return result, False
    finally:
        for handle in [*watched, *process_handles.values()]:
            if handle:
                kernel.CloseHandle(handle)
