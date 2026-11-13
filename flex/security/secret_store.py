from __future__ import annotations

import base64
import ctypes
import os
from ctypes import wintypes

_PREFIX = "dpapi:v1:"
_CRYPTPROTECT_UI_FORBIDDEN = 0x1


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


def protect_secret(value: str) -> str:
    if not value:
        return ""
    if os.name != "nt":
        raise OSError("Windows DPAPI is required to protect saved credentials.")

    raw = value.encode("utf-8")
    input_buffer = ctypes.create_string_buffer(raw, len(raw))
    input_blob = _DataBlob(
        len(raw),
        ctypes.cast(input_buffer, ctypes.POINTER(ctypes.c_byte)),
    )
    output_blob = _DataBlob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    protect = crypt32.CryptProtectData
    protect.argtypes = [
        ctypes.POINTER(_DataBlob),
        wintypes.LPCWSTR,
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    protect.restype = wintypes.BOOL
    if not protect(
        ctypes.byref(input_blob),
        "FLEX Discord bot token",
        None,
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(output_blob),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        protected = ctypes.string_at(output_blob.pbData, output_blob.cbData)
        return _PREFIX + base64.b64encode(protected).decode("ascii")
    finally:
        ctypes.WinDLL("kernel32", use_last_error=True).LocalFree(output_blob.pbData)


def unprotect_secret(value: str) -> str:
    if not value:
        return ""
    if not value.startswith(_PREFIX):
        return value
    if os.name != "nt":
        raise OSError("Windows DPAPI is required to unprotect saved credentials.")

    protected = base64.b64decode(value[len(_PREFIX):], validate=True)
    input_buffer = ctypes.create_string_buffer(protected, len(protected))
    input_blob = _DataBlob(
        len(protected),
        ctypes.cast(input_buffer, ctypes.POINTER(ctypes.c_byte)),
    )
    output_blob = _DataBlob()
    description = ctypes.c_wchar_p()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    unprotect = crypt32.CryptUnprotectData
    unprotect.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.POINTER(ctypes.c_wchar_p),
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    unprotect.restype = wintypes.BOOL
    if not unprotect(
        ctypes.byref(input_blob),
        ctypes.byref(description),
        None,
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(output_blob),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData).decode("utf-8")
    finally:
        ctypes.WinDLL("kernel32", use_last_error=True).LocalFree(output_blob.pbData)
