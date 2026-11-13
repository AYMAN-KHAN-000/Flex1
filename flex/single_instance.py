from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

_MUTEX_NAME = "Local\\FLEX-Desktop-Application-0C82E8F7-9B9B-4CC6-9E49-5D62B6E9AC31"
_ERROR_ALREADY_EXISTS = 183


class WindowsSingleInstance:
    def __init__(self) -> None:
        self._kernel32 = None
        self._handle = None

    def acquire(self) -> bool:
        if sys.platform != "win32":
            return True

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_mutex = kernel32.CreateMutexW
        create_mutex.argtypes = (
            wintypes.LPVOID,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        )
        create_mutex.restype = wintypes.HANDLE
        ctypes.set_last_error(0)
        handle = create_mutex(None, False, _MUTEX_NAME)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return False

        self._kernel32 = kernel32
        self._handle = handle
        return True

    def close(self) -> None:
        if self._handle is None or self._kernel32 is None:
            return

        handle = self._handle
        self._handle = None
        if not self._kernel32.CloseHandle(handle):
            raise ctypes.WinError(ctypes.get_last_error())
