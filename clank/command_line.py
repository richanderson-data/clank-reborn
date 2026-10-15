"""Parse the configured runner using the platform's command-line convention."""
from __future__ import annotations

import os
import shlex


def split_command(text):
    if os.name != "nt":
        return shlex.split(text)
    import ctypes
    from ctypes import wintypes

    # Windows' native parser preserves backslash paths and escaped quotes.
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    shell.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    shell.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    kernel.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel.LocalFree.restype = wintypes.HLOCAL
    count = ctypes.c_int()
    arguments = shell.CommandLineToArgvW("clank " + text, ctypes.byref(count))
    if not arguments:
        raise ValueError("Cannot parse command arguments")
    try:
        return [arguments[index] for index in range(1, count.value)]
    finally:
        kernel.LocalFree(arguments)
