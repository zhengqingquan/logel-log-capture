"""CLI 参数与 profile 逻辑的单测（不启动 Logel）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from lcapt.cli import (
    PathConstraintError,
    assert_ascii_path,
    build_parser,
    capture_seconds,
    default_out_dir,
)
from lcapt.profile import COPY_SUFFIXES, profile_patches


def test_zero_duration_means_indefinite():
    # -d 0 / 负数 = 不定长，不能当作「立刻到点」
    assert capture_seconds(0) is None
    assert capture_seconds(0.0) is None
    assert capture_seconds(-5) is None
    assert capture_seconds(60) == 60.0
    assert capture_seconds(1.5) == 1.5


def test_parser_capture_defaults():
    parser = build_parser()
    args = parser.parse_args(["capture"])
    assert args.duration == 60.0
    assert args.class_name
    assert args.export is False
    # 导出默认走回放
    assert args.playback is True


def test_no_playback_disables_replay():
    parser = build_parser()
    assert parser.parse_args(["capture", "--no-playback"]).playback is False
    assert parser.parse_args(["export", "--no-playback"]).playback is False
    # 旧脚本显式传 --playback 仍然可用
    assert parser.parse_args(["capture", "--playback"]).playback is True
    assert parser.parse_args(["export", "--playback"]).playback is True


def test_parser_common_options_after_subcommand():
    parser = build_parser()
    args = parser.parse_args(["capture", "-d", "5", "--profile", r"D:\p", "--json"])
    assert args.duration == 5.0
    assert args.profile == r"D:\p"
    assert args.json is True


def test_parser_global_options_before_subcommand():
    parser = build_parser()
    args = parser.parse_args(["--profile", r"D:\p", "list"])
    assert args.profile == r"D:\p"
    assert args.command == "list"


def test_duration_zero_means_until_stopped():
    parser = build_parser()
    args = parser.parse_args(["capture", "-d", "0"])
    assert args.duration == 0.0


def test_profile_patches_force_auto_logging(tmp_path: Path):
    patches = profile_patches(tmp_path, "LCAPT")
    assert patches[("Settings", "Auto Start Logging")] == "1"
    assert patches[("Settings", "TitleName")] == "LCAPT"
    assert patches[("MS", "LogOutputFolder")] == str(tmp_path)


def test_config_files_never_hardlinked():
    # 这些后缀必须复制，硬链接会改到原安装
    for suffix in (".ini", ".xml"):
        assert suffix in COPY_SUFFIXES


def test_default_out_dir_follows_profile(monkeypatch):
    for var in ("LCAPT_OUT", "LOGEL_EXE", "LOGEL_HOME", "LOGEL_ROOT"):
        monkeypatch.delenv(var, raising=False)
    assert default_out_dir(Path(r"D:\x")) == Path(r"D:\x") / "out"


def test_default_out_dir_uses_logel_history(monkeypatch, tmp_path):
    """给了 --logel 时，默认输出目录是安装目录同级的 Logel_History。"""
    for var in ("LCAPT_OUT", "LOGEL_EXE", "LOGEL_HOME", "LOGEL_ROOT"):
        monkeypatch.delenv(var, raising=False)
    install = tmp_path / "Unisoc_Tool" / "Logel"
    (install / "Bin").mkdir(parents=True)
    (install / "Bin" / "Logel.exe").write_bytes(b"stub")
    assert default_out_dir(Path(r"C:\p"), str(install)) == install.parent / "Logel_History"


def test_help_renders_for_every_command():
    """argparse 会用 % 格式化 help，help 文本里的 % 必须转义成 %%。"""
    parser = build_parser()
    parser.format_help()
    for action in parser._subparsers._group_actions:  # noqa: SLF001
        for choice in action.choices.values():
            choice.format_help()


def test_all_help_text_is_ascii():
    """面向用户的帮助文本必须是英文（ASCII），避免 GBK 控制台乱码。"""
    parser = build_parser()
    texts = [parser.description or ""]
    texts.extend(a.help or "" for a in parser._actions)  # noqa: SLF001
    for action in parser._subparsers._group_actions:  # noqa: SLF001
        for sub in action.choices.values():
            texts.append(sub.description or "")
            texts.extend(a.help or "" for a in sub._actions)  # noqa: SLF001
    for text in texts:
        text.encode("ascii")


def test_out_help_mentions_logel_history_and_ascii():
    parser = build_parser()
    helps = [a.help or "" for a in parser._actions]  # noqa: SLF001
    for action in parser._subparsers._group_actions:  # noqa: SLF001
        for sub in action.choices.values():
            helps.extend(a.help or "" for a in sub._actions)  # noqa: SLF001
    joined = "\n".join(helps)
    assert "Logel_History" in joined
    assert "ASCII" in joined


def test_assert_ascii_path_rejects_non_ascii():
    assert_ascii_path(Path(r"D:\Unisoc_Tool\Logel_History"), label="--out")
    with pytest.raises(PathConstraintError, match="ASCII"):
        assert_ascii_path(Path(r"D:\测试\log"), label="--out")
