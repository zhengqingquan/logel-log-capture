"""Win32 窗口工具：找 Logel 主窗口、发 WM_CLOSE。

只有 Windows 才能抓日志；非 Windows 导入本模块不应报错（测试里会 import）。
"""

from __future__ import annotations

import ctypes
import sys
from dataclasses import dataclass
from typing import List, Optional

IS_WINDOWS = sys.platform == "win32"

WM_CLOSE = 0x0010


@dataclass
class Window:
    hwnd: int
    cls: str
    title: str
    visible: bool


def _user32():
    if not IS_WINDOWS:
        raise RuntimeError("Win32 API is only available on Windows")
    return ctypes.windll.user32


def enum_windows(
    pid: Optional[int] = None,
    class_name: Optional[str] = None,
    timeout_sec: float = 30.0,
) -> List[Window]:
    """枚举顶层窗口，可按进程 / 类名过滤。"""
    from ctypes import wintypes

    user32 = _user32()
    rows: List[Window] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def _cb(hwnd, _lparam):  # noqa: ANN001
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if pid is not None and owner.value != pid:
            return True
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        if class_name is not None and cls.value != class_name:
            return True
        title = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, title, 512)
        rows.append(
            Window(
                hwnd=hwnd,
                cls=cls.value,
                title=title.value,
                visible=bool(user32.IsWindowVisible(hwnd)),
            )
        )
        return True

    user32.EnumWindows(_cb, 0)
    return rows


def find_main_window(pid: Optional[int], class_name: str) -> Optional[Window]:
    """按进程 + 类名找主窗口；找不到再退回按类名找（跨进程 stop 用）。"""
    for win in enum_windows(pid=pid, class_name=class_name):
        return win
    for win in enum_windows(pid=None, class_name=class_name):
        return win
    return None


def post_close(hwnd: int) -> None:
    """PostMessage(WM_CLOSE)：等价于点窗口右上角 X，触发工具正常收尾。"""
    _user32().PostMessageW(hwnd, WM_CLOSE, 0, 0)


# --------------------------------------------------- 模态框（回放/导出用）


def iter_child_windows(hwnd: int) -> "list[Window]":
    """枚举某个窗口的全部子控件（对话框里的 Static / Button）。"""
    from ctypes import wintypes

    user32 = _user32()
    rows: "list[Window]" = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def _cb(child, _lparam):  # noqa: ANN001
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(child, cls, 256)
        title = ctypes.create_unicode_buffer(2048)
        user32.GetWindowTextW(child, title, 2048)
        rows.append(
            Window(
                hwnd=child,
                cls=cls.value,
                title=title.value,
                visible=bool(user32.IsWindowVisible(child)),
            )
        )
        return True

    user32.EnumChildWindows(hwnd, _cb, 0)
    return rows


def find_modal_dialogs(pid: Optional[int]) -> "list[Window]":
    """找 #32770（标准对话框）：Logel 的警告框就是这个类。"""
    return [w for w in enum_windows(pid=pid) if w.cls == "#32770"]


def click_button(hwnd: int) -> None:
    """BM_CLICK，等价于用鼠标点一下按钮。"""
    _user32().SendMessageW(hwnd, 0x00F5, 0, 0)


def dialog_text(dialog: "Window") -> str:
    """把对话框里的说明性文字拼起来（用于日志/告警）。"""
    parts = [c.title.strip() for c in iter_child_windows(dialog.hwnd)]
    return " ".join(p for p in parts if len(p) > 15)
