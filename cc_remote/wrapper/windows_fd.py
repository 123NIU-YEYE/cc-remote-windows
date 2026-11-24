"""Real Windows file handles; do not emulate dir_fd with mutable path strings."""
from __future__ import annotations

import os
import stat

# Virtual flags are consumed here and never passed to the Windows CRT.
NOFOLLOW = 1 << 28
DIRECTORY = 1 << 29


def _native_path(path):
    if isinstance(path, int):
        return path
    from cc_remote.wrapper.os_compat import _win32_long_path
    value = os.fsdecode(path)
    return _win32_long_path(value) if len(os.path.abspath(value)) >= 248 else path


def open_descriptor(path, flags: int, mode: int = 0o600, *, dir_fd=None) -> int:
    import ctypes
    import msvcrt
    import pywintypes
    import win32con as c
    import win32file

    if flags & os.O_RDWR:
        access, crt = c.GENERIC_READ | c.GENERIC_WRITE, os.O_RDWR
    elif flags & os.O_WRONLY:
        access, crt = c.GENERIC_WRITE, os.O_WRONLY
    else:
        access, crt = c.GENERIC_READ, os.O_RDONLY
    # Include READ_CONTROL so fchmod can verify the actual descriptor owner.
    access |= c.READ_CONTROL | 0x80
    if flags & os.O_CREAT:
        access |= c.WRITE_DAC
    if flags & os.O_CREAT:
        disposition = (c.CREATE_NEW if flags & os.O_EXCL else
                       c.OPEN_ALWAYS)
    else:
        disposition = c.OPEN_EXISTING
    attributes = c.FILE_FLAG_BACKUP_SEMANTICS
    if flags & NOFOLLOW:
        attributes |= 0x00200000
    if flags & getattr(os, "O_TEMPORARY", 0):
        attributes |= c.FILE_FLAG_DELETE_ON_CLOSE
        access |= c.DELETE
    handle = None
    try:
        if dir_fd is not None:
            handle = _relative_handle(path, dir_fd, access, disposition, flags)
        else:
            from cc_remote.windows_privacy import _private_attributes
            from cc_remote.wrapper.os_compat import _win32_long_path
            security = (_private_attributes() if flags & os.O_CREAT and not mode & 0o077 else None)
            handle = win32file.CreateFile(_win32_long_path(os.fsdecode(path)), access, 7, security,
                                          disposition, attributes, None)
        info = win32file.GetFileInformationByHandle(handle)
        if flags & NOFOLLOW and info[0] & c.FILE_ATTRIBUTE_REPARSE_POINT:
            raise PermissionError("Reparse points are not allowed")
        if flags & DIRECTORY and not info[0] & c.FILE_ATTRIBUTE_DIRECTORY:
            raise NotADirectoryError(os.fsdecode(path))
        descriptor = msvcrt.open_osfhandle(handle.Detach(), crt | os.O_BINARY)
        handle = None
        if flags & os.O_CREAT and not mode & 0o077:
            from cc_remote.windows_privacy import private_descriptor
            private_descriptor(descriptor)
        if flags & os.O_TRUNC:
            os.ftruncate(descriptor, 0)
        if flags & os.O_APPEND:
            os.lseek(descriptor, 0, os.SEEK_END)
        return descriptor
    except pywintypes.error as exc:
        raise ctypes.WinError(exc.winerror) from exc
    finally:
        if handle is not None:
            handle.Close()



def reopen_handle(fd: int, access: int):
    """Reopen the object behind ``fd`` itself with ``access``.

    ReOpenFile rejects directory handles; an empty NT name relative to the
    handle reopens the same file or directory without a path lookup.
    """
    return _relative_handle("", fd, access, 3, NOFOLLOW, directory=None, same_object=True)


def _relative_handle(path, root_fd, access, disposition, flags, *, directory=False, same_object=False):
    """Open one child via NT RootDirectory, pinned to the actual parent handle."""
    import ctypes as ct
    import msvcrt
    from ctypes import wintypes as w

    name = os.fsdecode(path)
    if same_object:
        if name:
            raise ValueError("Reopening a descriptor takes no path component")
    elif not name or name in {".", ".."} or any(c in name for c in "\\/:\0"):
        raise ValueError("A descriptor-relative child must be one literal path component")

    class UnicodeString(ct.Structure):
        _fields_ = [("length", w.USHORT), ("maximum", w.USHORT), ("buffer", w.LPWSTR)]
    class ObjectAttributes(ct.Structure):
        _fields_ = [("length", w.ULONG), ("root", w.HANDLE),
                    ("name", ct.POINTER(UnicodeString)), ("attributes", w.ULONG),
                    ("security", ct.c_void_p), ("qos", ct.c_void_p)]
    class IoStatus(ct.Structure):
        _fields_ = [("status", ct.c_void_p), ("information", ct.c_size_t)]

    buffer = ct.create_unicode_buffer(name)
    size = len(name.encode("utf-16-le"))
    string = UnicodeString(size, size + 2, ct.cast(buffer, w.LPWSTR))
    attributes = ObjectAttributes(ct.sizeof(ObjectAttributes), msvcrt.get_osfhandle(root_fd),
                                  ct.pointer(string), 0x40, None, None)
    io, output = IoStatus(), w.HANDLE()
    native = ct.WinDLL("ntdll")
    create = native.NtCreateFile
    create.argtypes = [ct.POINTER(w.HANDLE), w.ULONG, ct.POINTER(ObjectAttributes),
                       ct.POINTER(IoStatus), ct.c_void_p, w.ULONG, w.ULONG,
                       w.ULONG, w.ULONG, ct.c_void_p, w.ULONG]
    create.restype = ct.c_long
    native.RtlNtStatusToDosError.argtypes = [ct.c_long]
    native.RtlNtStatusToDosError.restype = w.ULONG
    # FILE_SYNCHRONOUS_IO_NONALERT, FILE_OPEN_REPARSE_POINT, directory constraint.
    options = 0x20 | 0x00200000 | (1 if directory or flags & DIRECTORY else 0 if directory is None else 0x40)
    status = create(ct.byref(output), access | 0x100000, ct.byref(attributes),
                    ct.byref(io), None, 0x80, 7, {1: 2, 3: 1, 4: 3}[disposition],
                    options, None, 0)
    if status < 0:
        raise ct.WinError(native.RtlNtStatusToDosError(status))
    from pywintypes import HANDLE
    return HANDLE(output.value)


def install() -> None:
    if getattr(os.open, "_cc_remote_windows", False):
        return
    native_open, native_fsync = os.open, os.fsync
    native_stat, native_lstat, native_scandir = os.stat, os.lstat, os.scandir
    native_listdir = os.listdir
    native_mkdir, native_unlink, native_replace, native_rename = os.mkdir, os.unlink, os.replace, os.rename
    os.O_NOFOLLOW = NOFOLLOW
    os.O_DIRECTORY = DIRECTORY

    def binary_open(path, flags, mode=0o777, *, dir_fd=None):
        if dir_fd is None and not flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT) and os.path.isdir(path):
            flags |= DIRECTORY | NOFOLLOW
        if dir_fd is not None or flags & (NOFOLLOW | DIRECTORY) or flags & os.O_CREAT and not mode & 0o077:
            return open_descriptor(path, flags, mode, dir_fd=dir_fd)
        return native_open(_native_path(path), flags | os.O_BINARY, mode)

    def fsync(fd):
        # Only directory handles have no Windows fsync equivalent. Never swallow
        # an invalid-descriptor or disk error on an ordinary file.
        if not stat.S_ISDIR(os.fstat(fd).st_mode):
            native_fsync(fd)

    def relative_stat(path, *, dir_fd=None, follow_symlinks=True):
        if dir_fd is None:
            return native_stat(_native_path(path), follow_symlinks=follow_symlinks)
        handle = _relative_handle(path, dir_fd, 0x80000000, 3, NOFOLLOW, directory=None)
        import msvcrt
        descriptor = msvcrt.open_osfhandle(handle.Detach(), os.O_RDONLY | os.O_BINARY)
        try:
            return os.fstat(descriptor)
        finally:
            os.close(descriptor)

    def lstat(path, *, dir_fd=None):
        return relative_stat(path, dir_fd=dir_fd, follow_symlinks=False) if dir_fd is not None else native_lstat(_native_path(path))

    def mkdir(path, mode=0o777, *, dir_fd=None):
        if dir_fd is None:
            return native_mkdir(_native_path(path), mode)
        handle = _relative_handle(path, dir_fd, 0x80000000, 1, DIRECTORY, directory=True)
        handle.Close()

    def unlink(path, *, dir_fd=None):
        if dir_fd is None:
            return native_unlink(_native_path(path))
        _relative_mutation(path, dir_fd, delete=True)

    def replace(source, target, *, src_dir_fd=None, dst_dir_fd=None):
        if src_dir_fd is None and dst_dir_fd is None:
            return native_replace(_native_path(source), _native_path(target))
        if src_dir_fd is None or dst_dir_fd is None:
            raise NotImplementedError("Both Windows rename directory handles are required")
        _relative_mutation(source, src_dir_fd, target=target, target_fd=dst_dir_fd)

    def rename(source, target, *, src_dir_fd=None, dst_dir_fd=None):
        if src_dir_fd is None and dst_dir_fd is None:
            return native_rename(_native_path(source), _native_path(target))
        raise NotImplementedError("Descriptor-relative non-replacing rename is not supported")

    binary_open._cc_remote_windows = True
    os.open, os.fsync = binary_open, fsync
    os.stat, os.lstat = relative_stat, lstat
    os.scandir = lambda path=".": _directory_entries(path) if isinstance(path, int) else native_scandir(_native_path(path))
    os.listdir = lambda path=".": [entry.name for entry in _directory_entries(path)] if isinstance(path, int) else native_listdir(_native_path(path))
    os.mkdir, os.unlink, os.replace, os.rename = mkdir, unlink, replace, rename


def _relative_mutation(name, root_fd, *, delete=False, target=None, target_fd=None):
    import ctypes as ct
    import msvcrt
    import win32file
    from ctypes import wintypes as w
    handle = _relative_handle(name, root_fd, 0x10000 | 0x80, 3, NOFOLLOW)
    try:
        info = win32file.GetFileInformationByHandle(handle)
        if info[0] & 0x400:
            raise PermissionError("Reparse points are not allowed")
        class IoStatus(ct.Structure):
            _fields_ = [("status", ct.c_void_p), ("information", ct.c_size_t)]
        native = ct.WinDLL("ntdll")
        setter = native.NtSetInformationFile
        setter.argtypes = [w.HANDLE, ct.POINTER(IoStatus), ct.c_void_p, w.ULONG, ct.c_int]
        setter.restype = ct.c_long
        native.RtlNtStatusToDosError.argtypes = [ct.c_long]
        native.RtlNtStatusToDosError.restype = w.ULONG
        if delete:
            buffer, kind = ct.c_ubyte(True), 13
            pointer, size = ct.byref(buffer), ct.sizeof(buffer)
        else:
            _literal_child(target)
            encoded = target.encode("utf-16-le")
            class Rename(ct.Structure):
                _fields_ = [("replace", w.DWORD), ("root", w.HANDLE),
                            ("length", w.DWORD), ("name", w.WCHAR * (len(encoded)//2 + 1))]
            buffer = Rename(1, msvcrt.get_osfhandle(target_fd), len(encoded), target)
            pointer, size, kind = ct.byref(buffer), Rename.name.offset + len(encoded), 10
        io = IoStatus()
        status = setter(int(handle), ct.byref(io), pointer, size, kind)
        if status < 0:
            raise ct.WinError(native.RtlNtStatusToDosError(status))
    finally:
        handle.Close()


def _literal_child(name):
    name = os.fsdecode(name)
    if not name or name in {".", ".."} or any(char in name for char in "\\/:\0"):
        raise ValueError("A descriptor-relative child must be one literal path component")
    return name


class _DirectoryIterator:
    def __init__(self, entries):
        self.entries = iter(entries)
    def __iter__(self): return self
    def __next__(self): return next(self.entries)
    def __enter__(self): return self
    def __exit__(self, *_): self.close()
    def close(self): self.entries = iter(())


class _DirectoryEntry:
    def __init__(self, name, attributes, descriptor):
        self.name, self.path, self._attributes, self._descriptor = name, name, attributes, descriptor
    def is_symlink(self): return bool(self._attributes & 0x400)
    def is_dir(self, *, follow_symlinks=True):
        return not self.is_symlink() and bool(self._attributes & 0x10)
    def is_file(self, *, follow_symlinks=True):
        return not self.is_symlink() and not self._attributes & 0x10
    def stat(self, *, follow_symlinks=True):
        return os.stat(self.name, dir_fd=self._descriptor, follow_symlinks=follow_symlinks)


def _directory_entries(descriptor):
    import ctypes as ct
    import msvcrt
    from ctypes import wintypes as w
    kernel = ct.WinDLL("kernel32", use_last_error=True)
    query = kernel.GetFileInformationByHandleEx
    query.argtypes = [w.HANDLE, ct.c_int, ct.c_void_p, w.DWORD]
    query.restype = w.BOOL
    handle, first, entries = msvcrt.get_osfhandle(descriptor), True, []
    while True:
        buffer = ct.create_string_buffer(64*1024)
        if not query(handle, 11 if first else 10, buffer, len(buffer)):
            error = ct.get_last_error()
            if error == 18:
                break
            raise ct.WinError(error)
        first, offset = False, 0
        while True:
            following = int.from_bytes(buffer.raw[offset:offset+4], "little")
            attributes = int.from_bytes(buffer.raw[offset+56:offset+60], "little")
            size = int.from_bytes(buffer.raw[offset+60:offset+64], "little")
            if size > len(buffer)-offset-104 or size % 2:
                raise OSError("Invalid Windows directory entry")
            name = buffer.raw[offset+104:offset+104+size].decode("utf-16-le")
            if name not in {".", ".."}:
                entries.append(_DirectoryEntry(name, attributes, descriptor))
            if len(entries) > 20000:
                raise ValueError("Directory entry limit exceeded")
            if not following:
                break
            if following < 104 or offset + following >= len(buffer):
                raise OSError("Invalid Windows directory continuation")
            offset += following
    return _DirectoryIterator(entries)
