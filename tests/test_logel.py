"""纯逻辑单测：ini 读写、Application.log 判据、会话发现。不碰真设备。"""

from __future__ import annotations

import os
import time
from pathlib import Path

from lcapt.logel import (
    Session,
    analyze_connect_log,
    find_new_session,
    ini_get,
    ini_patch,
    list_sessions,
)


def test_ini_patch_replaces_only_target_section():
    text = "\n".join(
        [
            "[Settings]",
            "Auto Start Logging=0",
            "TitleName=",
            "[MS]",
            "LogOutputFolder=D:\\old",
            "AutoIdentifyPort=1",
        ]
    )
    new, missing = ini_patch(
        text,
        {
            ("Settings", "Auto Start Logging"): "1",
            ("Settings", "TitleName"): "LCAPT",
            ("MS", "LogOutputFolder"): "D:\\new",
        },
    )
    assert missing == []
    assert ini_get(new, "Settings", "Auto Start Logging") == "1"
    assert ini_get(new, "Settings", "TitleName") == "LCAPT"
    assert ini_get(new, "MS", "LogOutputFolder") == "D:\\new"
    assert ini_get(new, "MS", "AutoIdentifyPort") == "1"


def test_ini_patch_appends_missing_key_into_its_section():
    text = "[Settings]\nPlaySound=0\n\n[MS]\nType=1\n"
    new, missing = ini_patch(text, {("Settings", "Auto Start Logging"): "1"})
    assert missing == []
    assert ini_get(new, "Settings", "Auto Start Logging") == "1"
    # 追加的键仍应落在 [Settings] 段内
    settings_block = new.split("[MS]")[0]
    assert "Auto Start Logging=1" in settings_block


def test_analyze_connect_log_success():
    text = (
        "[STEP]: Diag Port matched [SPRD DIAG (COM22) 22]\n"
        "CCommOpr::ConnectByUART: Open Diag Port 22\n"
        "CCommOpr::Connect, type: UART, result: 1\n"
        "Logel tool version: R9.23.1102\n"
    )
    conn = analyze_connect_log(text)
    assert conn["connected"] is True
    assert conn["diag_port"] == 22
    assert conn["diag_name"] == "SPRD DIAG (COM22)"
    assert conn["tool_version"] == "R9.23.1102"


def test_analyze_connect_log_no_device():
    text = "[STEP]: No Diag Port matched! Use diag port [0] in logel.ini\n[STEP]: No Log Port found!\n"
    conn = analyze_connect_log(text)
    assert conn["connected"] is False
    assert conn["no_diag_port"] is True
    assert conn["diag_port"] is None


def _fake_session(root: Path, name: str, *, logel_bytes: int = 1024, stop: bool = True) -> Path:
    sess = root / name
    sess.mkdir(parents=True)
    stem = name.replace("_armlog", "")
    (sess / f"{stem}.logel").write_bytes(b"\0" * logel_bytes)
    marker = "Stop Logging" if stop else ""
    (sess / f"{stem}.lst").write_text(
        f"Start Logging[LittleEndian]\nTool Version: R9.23.1102.0\n{marker}\n", encoding="utf-8"
    )
    (sess / stem).mkdir()
    return sess


def test_find_new_session_ignores_old(tmp_path: Path):
    old = _fake_session(tmp_path, "2026_01_01_00_00_00_000_armlog")
    old_time = time.time() - 3600
    os.utime(old, (old_time, old_time))

    since = time.time()
    assert find_new_session(tmp_path, since, timeout_sec=0.5) is None

    new = _fake_session(tmp_path, "2026_02_02_00_00_00_000_armlog")
    sess = find_new_session(tmp_path, since, timeout_sec=0.5)
    assert sess is not None
    assert sess.dir == new


def test_session_summary_and_markers(tmp_path: Path):
    path = _fake_session(tmp_path, "2026_03_03_00_00_00_000_armlog", logel_bytes=2048)
    sess = Session(path)
    assert sess.markers() == {"start": True, "stop": True}
    summary = sess.summary()
    assert summary["size_bytes"] == 2048
    assert summary["has_stop_marker"] is True
    assert summary["traceview_cached"] is True


def test_list_sessions_newest_first(tmp_path: Path):
    _fake_session(tmp_path, "2026_01_01_00_00_00_000_armlog")
    _fake_session(tmp_path, "2026_05_05_00_00_00_000_armlog")
    names = [s.name for s in list_sessions(tmp_path)]
    assert names[0].startswith("2026_05_05")
    assert list_sessions(tmp_path, limit=1)[0].name.startswith("2026_05_05")
