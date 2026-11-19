"""live.txt / follow 相关单测（不启动 Logel）。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from lcapt.cli import build_parser
from lcapt.export import LIVE_TXT_NAME, LiveExportResult, live_export_once, live_txt_path
from lcapt.logel import Session


def test_parser_live_export_flags():
    parser = build_parser()
    args = parser.parse_args(["capture", "--live-export", "--live-interval", "1.5"])
    assert args.live_export is True
    assert args.live_interval == 1.5
    watch = parser.parse_args(["watch", "--live-export"])
    assert watch.live_export is True
    follow = parser.parse_args(["follow", "--once", "--interval", "3"])
    assert follow.once is True
    assert follow.interval == 3.0
    assert follow.command == "follow"


def test_session_summary_live_txt(tmp_path: Path):
    sess_dir = tmp_path / "2026_01_01_00_00_00_000_armlog"
    sess_dir.mkdir()
    (sess_dir / "x.logel").write_bytes(b"\0" * 32)
    summary = Session(sess_dir).summary()
    assert summary["live_txt"] is None
    live = sess_dir / LIVE_TXT_NAME
    live.write_text("SN\theader\n", encoding="utf-8")
    summary2 = Session(sess_dir).summary()
    assert summary2["live_txt"] == str(live)


def test_live_txt_path(tmp_path: Path):
    assert live_txt_path(tmp_path) == tmp_path / "live.txt"


def test_live_export_once_invokes_follow(tmp_path: Path, monkeypatch):
    sess = tmp_path / "sess_armlog"
    sess.mkdir()
    calls = {}

    def fake_run(cmd, **kwargs):
        calls["cmd"] = cmd
        out = MagicMock()
        out.returncode = 0
        out.stdout = "lines: 3\nnext_index: 3\noutput: x\n"
        out.stderr = ""
        return out

    monkeypatch.setattr("lcapt.export._import_logel2txt_exporters", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "lcapt.export.find_logel2txt",
        lambda explicit=None: ["logel2txt"],
    )
    monkeypatch.setattr("lcapt.export.subprocess.run", fake_run)
    result = live_export_once(sess, log=lambda *_: None)
    assert result is not None
    assert result.added == 3
    assert result.next_index == 3
    assert "follow" in calls["cmd"]
    assert "--once" in calls["cmd"]


def test_live_export_once_missing_tool_returns_none(tmp_path: Path, monkeypatch):
    from lcapt.export import ExportError

    monkeypatch.setattr("lcapt.export._import_logel2txt_exporters", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "lcapt.export.find_logel2txt",
        lambda explicit=None: (_ for _ in ()).throw(ExportError("missing")),
    )
    assert live_export_once(tmp_path, log=lambda *_: None) is None


def test_live_export_once_prefers_inprocess(tmp_path: Path, monkeypatch):
    sess = tmp_path / "sess_armlog"
    sess.mkdir()
    monkeypatch.setattr(
        "lcapt.export._live_export_inprocess",
        lambda *_a, **_k: LiveExportResult(sess / "live.txt", 7, 7, 0),
    )
    called = {"run": False}

    def boom(*_a, **_k):
        called["run"] = True
        raise AssertionError("subprocess should not run")

    monkeypatch.setattr("lcapt.export.subprocess.run", boom)
    result = live_export_once(sess, log=lambda *_: None)
    assert result is not None
    assert result.added == 7
    assert called["run"] is False
