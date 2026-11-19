"""并发锁行为：别人的锁不能抢、也不能顺手删；陈旧锁可以接管。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from lcapt.capture import (
    CaptureError,
    CapturePaths,
    HeadlessCapture,
    other_logel_pids,
    other_logel_warning,
    port_busy_hint,
)


def _paths(tmp_path: Path) -> CapturePaths:
    return CapturePaths(profile_dir=tmp_path, out_dir=tmp_path / "out")


def test_refuses_when_other_capture_alive(tmp_path: Path):
    paths = _paths(tmp_path)
    paths.profile_dir.mkdir(parents=True, exist_ok=True)
    paths.lock_file.write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")

    cap = HeadlessCapture(paths, log=lambda _: None)
    with pytest.raises(CaptureError):
        cap.acquire_lock()
    # 拒绝启动时不能把对方正在用的锁删掉
    assert paths.lock_file.is_file()

    # 没拿到锁的实例 release 也是空操作
    cap.release_lock()
    assert paths.lock_file.is_file()


def test_takes_over_stale_lock_and_releases_own(tmp_path: Path):
    paths = _paths(tmp_path)
    paths.profile_dir.mkdir(parents=True, exist_ok=True)
    paths.lock_file.write_text(json.dumps({"pid": 999999}), encoding="utf-8")

    cap = HeadlessCapture(paths, log=lambda _: None)
    cap.acquire_lock()
    assert json.loads(paths.lock_file.read_text(encoding="utf-8"))["pid"] == os.getpid()
    cap.release_lock()
    assert not paths.lock_file.exists()


def test_tolerates_bom_in_lock_file(tmp_path: Path):
    paths = _paths(tmp_path)
    paths.profile_dir.mkdir(parents=True, exist_ok=True)
    paths.lock_file.write_text(json.dumps({"pid": 999999}), encoding="utf-8-sig")

    cap = HeadlessCapture(paths, log=lambda _: None)
    cap.acquire_lock()  # 有 BOM 也应按陈旧锁处理，而不是崩掉
    assert json.loads(paths.lock_file.read_text(encoding="utf-8"))["pid"] == os.getpid()
    cap.release_lock()


def test_port_busy_hint_only_when_port_open_failed():
    assert port_busy_hint("") == ""
    assert port_busy_hint("ConnectByUART: Open Diag Port 22") == ""
    hint = port_busy_hint("... Open Diag Port fail ...", own_pid=os.getpid())
    assert "Open Diag Port fail" in hint
    assert "COM port" in hint
    assert "Close them and retry" in hint


def test_other_logel_warning_empty_when_no_pids(monkeypatch):
    monkeypatch.setattr("lcapt.capture._logel_pids", lambda: [])
    assert other_logel_pids() == []
    assert other_logel_warning() == ""


def test_other_logel_warning_lists_pids_and_excludes_own(monkeypatch):
    monkeypatch.setattr("lcapt.capture._logel_pids", lambda: [111, 222, 333])
    assert other_logel_pids(exclude_pid=222) == [111, 333]
    warn = other_logel_warning(exclude_pid=222)
    assert "111" in warn and "333" in warn and "222" not in warn
    assert "DIAG" in warn
    assert "GUI" in warn
