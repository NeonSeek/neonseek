"""the windows recycle bin: the only way neonseek deletes a file (PLAN.md sections 0 and 8).

nothing is ever deleted for good. before a file goes, the drive is checked for a recycle bin,
and a drive without one (usb stick, network share, subst drive, bin turned off, file bigger
than the bin) is refused with a message instead. tests use a fake bin, never this one.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from neonseek.errors import NeonSeekError
from neonseek.i18n import N_, tr


class RecycleError(NeonSeekError):
    pass


class UnconfirmedError(RecycleError):
    """the file left the library, but its recycle bin record could not be found."""


class RecycleBin(Protocol):
    def problem(self, path: Path) -> str | None:
        """why this file cannot go to the recycle bin, none when it can."""
        ...

    def recycle(self, path: Path) -> None:
        """moves one file to the recycle bin, raises RecycleError when it did not."""
        ...


def no_bin_message(path: Path, why: str) -> str:
    return tr(
        "NeonSeek only deletes into the Windows Recycle Bin, and {path} is on a drive without "
        "one ({why}). Nothing was deleted. Delete the file yourself in Explorer if you really "
        "want it gone, or move the library to an internal drive in Settings."
    ).format(path=path, why=why)


# windows constants
DRIVE_FIXED = 3
DRIVE_NAMES = {
    0: N_("unknown drive"),
    1: N_("drive not found"),
    2: N_("removable drive, like a USB stick"),
    4: N_("network drive"),
    5: N_("CD or DVD drive"),
    6: N_("RAM disk"),
}
FO_DELETE = 3
FOF_SILENT = 0x0004
FOF_NOCONFIRMATION = 0x0010
FOF_ALLOWUNDO = 0x0040
FOF_NOERRORUI = 0x0400
# asks before a file would be deleted for good instead of recycled, the last guard
FOF_WANTNUKEWARNING = 0x4000
BITBUCKET = r"Software\Microsoft\Windows\CurrentVersion\Explorer\BitBucket\Volume"
# SHFileOperationW takes paths up to MAX_PATH (260) with the closing zero
MAX_PATH_CHARS = 259
GIB = 1024**3
# windows records a deletion in <drive>\$Recycle.Bin\<user>\$I... within moments
BIN_CLOCK_SLACK_S = 5.0
POLICIES = r"Software\Microsoft\Windows\CurrentVersion\Policies\Explorer"


@dataclass(frozen=True)
class VolumeFacts:
    """what windows says about the drive of a file. read only, nothing is changed."""

    root: str
    drive_type: int
    subst: bool
    bin_off: bool  # NoRecycleFiles policy or "remove files immediately" for this drive
    max_bytes: int | None  # size of the bin set for this drive, none when never set
    total_bytes: int | None = None  # size of the drive, for the default size of its bin


def default_capacity(total_bytes: int) -> int:
    """the bin size windows uses until the user sets one: 10 % of the first 40 GB of the
    drive and 5 % of the rest. checked against the registry of a real drive."""
    first = min(total_bytes, 40 * GIB)
    return first // 10 + max(0, total_bytes - 40 * GIB) // 20


def bin_capacity(facts: VolumeFacts) -> int | None:
    if facts.max_bytes is not None:
        return facts.max_bytes
    if facts.total_bytes:
        return default_capacity(facts.total_bytes)
    return None


def bin_problem(facts: VolumeFacts, size: int) -> str | None:
    """the decision on top of the facts, kept apart so tests can feed any drive."""
    if facts.drive_type != DRIVE_FIXED:
        if facts.drive_type in DRIVE_NAMES:
            return tr(DRIVE_NAMES[facts.drive_type])
        return tr("drive without a Recycle Bin")
    if facts.subst:
        return tr("a drive made with subst")
    if facts.bin_off:
        return tr("the Recycle Bin is turned off for this drive")
    capacity = bin_capacity(facts)
    if capacity is None:
        # without a size there is no way to know that windows would not delete it for good
        return tr("Windows does not tell the size of the Recycle Bin of this drive")
    if size > capacity:
        # windows deletes a file bigger than the bin for good instead of recycling it
        return tr("the file is bigger than the Recycle Bin of this drive")
    return None


def path_problem(path: Path) -> str | None:
    """a path the recycle bin call cannot take, before anything is touched."""
    if len(os.path.abspath(str(path))) > MAX_PATH_CHARS:
        return tr(
            "NeonSeek only deletes into the Windows Recycle Bin, and the path of {path} is "
            "longer than {limit} characters, which the Recycle Bin cannot take. Nothing was "
            "deleted. Move the library to a folder with a shorter path in Settings, or delete "
            "the file yourself in Explorer."
        ).format(path=path, limit=MAX_PATH_CHARS)
    return None


@dataclass(frozen=True)
class BinRecord:
    """one $I file of the recycle bin: where the file was, its size, when it was deleted."""

    path: str
    size: int
    deleted: float  # unix time


def read_bin_record(data: bytes) -> BinRecord | None:
    """windows vista to 8.1 write version 1 (fixed 260 characters), windows 10 and 11
    version 2 (length, then the path). anything else is not ours to read."""
    if len(data) < 24:
        return None
    version = int.from_bytes(data[0:8], "little")
    size = int.from_bytes(data[8:16], "little")
    filetime = int.from_bytes(data[16:24], "little")
    if version == 1:
        raw = data[24 : 24 + 520]
    elif version == 2 and len(data) >= 28:
        length = int.from_bytes(data[24:28], "little")
        raw = data[28 : 28 + length * 2]
    else:
        return None
    path = raw.decode("utf-16-le", errors="replace").split("\0", 1)[0]
    # filetime counts 100 ns steps since 1601
    return BinRecord(path, size, filetime / 10_000_000 - 11_644_473_600)


def _record_files(folder: Path) -> list[Path]:
    """the $I files of a recycle bin. ntfs keeps one folder per user (only the own one can be
    read), fat32 and exfat drives have no users and keep them right in $Recycle.Bin."""
    found: list[Path] = []
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return found
    for entry in entries:
        try:
            if entry.name.startswith("$I") and entry.is_file():
                found.append(Path(entry.path))
            elif entry.is_dir():
                names = os.listdir(entry.path)
                found += [Path(entry.path) / n for n in names if n.startswith("$I")]
        except OSError:
            continue  # another user's folder
    return found


def find_in_bin(root: Path, path: Path, size: int, since: float) -> bool:
    """the recycle bin of this drive holds a deletion of exactly this file, made after
    since."""
    wanted = os.path.normcase(os.path.abspath(str(path)))
    for record_file in _record_files(Path(root) / "$Recycle.Bin"):
        try:
            record = read_bin_record(record_file.read_bytes())
        except OSError:
            continue
        if (
            record is not None
            and record.deleted >= since
            and record.size == size
            and os.path.normcase(record.path) == wanted
        ):
            return True
    return False


class WindowsRecycleBin:
    """the real recycle bin through shell32. used by the app, never by the tests."""

    def __init__(
        self,
        facts: Callable[[Path], VolumeFacts] | None = None,
        delete: Callable[[Path], None] | None = None,
    ) -> None:
        self._facts = facts or volume_facts
        # tests pass a stand in, the app always calls shell_delete
        self._delete = delete

    def problem(self, path: Path) -> str | None:
        if sys.platform != "win32":
            return no_bin_message(path, tr("not Windows"))
        too_long = path_problem(path)
        if too_long:
            return too_long
        try:
            size = path.stat().st_size
            why = bin_problem(self._facts(path), size)
        except OSError as exc:
            return tr("Could not check the drive of {path} ({error}). Nothing was deleted.").format(
                path=path, error=exc.strerror or exc
            )
        return no_bin_message(path, why) if why else None

    def recycle(self, path: Path) -> None:
        """moves the file and checks that it really is in the recycle bin: the call worked,
        the file left the library, and the bin of its drive has a record of exactly this path
        and size made just now. only then the caller may mark the track."""
        path = Path(path)
        problem = self.problem(path)
        if problem:
            raise RecycleError(problem)
        facts = self._facts(path)
        size = path.stat().st_size
        started = time.time() - BIN_CLOCK_SLACK_S
        (self._delete or shell_delete)(path)
        if path.exists():
            raise RecycleError(
                tr("{path} did not go to the Recycle Bin. It is still in the library.").format(
                    path=path
                )
            )
        if not find_in_bin(Path(facts.root), path, size, started):
            raise UnconfirmedError(
                tr(
                    "{path} left the library, but NeonSeek could not confirm it in the Recycle "
                    "Bin. Look for it in the Recycle Bin."
                ).format(path=path)
            )


# shell32 through ctypes, no extra dependency


def _wide_buffer(size: int):
    return ctypes.create_unicode_buffer(size)


def volume_facts(path: Path) -> VolumeFacts:
    kernel32 = ctypes.windll.kernel32
    full = str(Path(path).resolve())
    root_buf = _wide_buffer(1024)
    if not kernel32.GetVolumePathNameW(full, root_buf, len(root_buf)):
        raise OSError(ctypes.GetLastError(), "GetVolumePathNameW failed")
    root = root_buf.value
    drive_type = kernel32.GetDriveTypeW(root)
    subst = False
    if len(root) <= 3 and root[1:2] == ":":
        target = _wide_buffer(1024)
        if kernel32.QueryDosDeviceW(root[:2], target, len(target)):
            # a subst drive points at a folder: \??\C:\some\folder
            subst = target.value.startswith("\\??\\")
    bin_off, max_bytes = _bin_settings(root)
    try:
        total = shutil.disk_usage(root).total
    except OSError:
        total = None
    return VolumeFacts(root, drive_type, subst, bin_off, max_bytes, total)


def _bin_settings(root: str) -> tuple[bool, int | None]:
    import winreg

    def read(hive, key: str, name: str):
        try:
            with winreg.OpenKey(hive, key) as handle:
                return winreg.QueryValueEx(handle, name)[0]
        except OSError:
            return None

    off = any(
        read(hive, POLICIES, "NoRecycleFiles") == 1
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE)
    )
    max_bytes = None
    guid = _volume_guid(root)
    if guid:
        key = f"{BITBUCKET}\\{guid}"
        off = off or read(winreg.HKEY_CURRENT_USER, key, "NukeOnDelete") == 1
        capacity = read(winreg.HKEY_CURRENT_USER, key, "MaxCapacity")
        if isinstance(capacity, int) and capacity > 0:
            max_bytes = capacity * 1024 * 1024
    return off, max_bytes


def _volume_guid(root: str) -> str | None:
    name = _wide_buffer(64)
    mount = root if root.endswith("\\") else root + "\\"
    if not ctypes.windll.kernel32.GetVolumeNameForVolumeMountPointW(mount, name, len(name)):
        return None
    # \\?\Volume{1234-...}\ -> {1234-...}
    value = name.value
    start, end = value.find("{"), value.find("}")
    return value[start : end + 1] if start >= 0 and end > start else None


class _ShFileOp(ctypes.Structure):
    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("wFunc", ctypes.c_uint),
        ("pFrom", ctypes.c_void_p),
        ("pTo", ctypes.c_void_p),
        ("fFlags", ctypes.c_ushort),
        ("fAnyOperationsAborted", ctypes.c_int),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", ctypes.c_wchar_p),
    ]
    # shellapi.h packs this structure to one byte on 32 bit windows only
    if ctypes.sizeof(ctypes.c_void_p) == 4:
        _pack_ = 1


def shell_delete(path: Path) -> None:
    """one file into the recycle bin with SHFileOperationW. the checks come before."""
    full = os.path.abspath(str(path))
    # the list of files ends with two zero characters. python strings cannot hold one, so
    # the buffer is two utf-16 units longer than the path and starts zeroed
    units = len(full.encode("utf-16-le")) // 2
    buffer = ctypes.create_unicode_buffer(units + 2)
    buffer.value = full
    op = _ShFileOp()
    op.wFunc = FO_DELETE
    op.pFrom = ctypes.addressof(buffer)
    op.fFlags = (
        FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI | FOF_WANTNUKEWARNING
    )
    ole32 = ctypes.windll.ole32
    initialized = ole32.CoInitializeEx(None, 0x2) in (0, 1)  # apartment threaded
    try:
        result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    finally:
        if initialized:
            ole32.CoUninitialize()
    if result != 0 or op.fAnyOperationsAborted:
        raise RecycleError(
            tr(
                "Windows could not move {path} to the Recycle Bin (code {code}). Close programs "
                "that use the file and try again."
            ).format(path=path, code=result if result else "cancelled")
        )


def default_bin() -> RecycleBin:
    return WindowsRecycleBin()
