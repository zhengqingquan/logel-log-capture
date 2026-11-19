"""lcapt 命令行入口。"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

from . import __version__
from .capture import (
    CaptureError,
    CapturePaths,
    HeadlessCapture,
    other_logel_pids,
    other_logel_warning,
    stop_running,
)
from .export import ExportError, export_session, find_logel2txt, list_com_ports
from .logel import LogelLayoutError, find_install, list_sessions, read_ini, ini_get
from .profile import provision

EXIT_OK = 0
EXIT_PREFLIGHT = 2
EXIT_CAPTURE = 3
EXIT_STOP = 4
EXIT_EXPORT = 5

DEFAULT_CLASS = os.environ.get("LCAPT_WINDOW_CLASS", "LogelCapture")


def capture_seconds(value: Optional[float]) -> Optional[float]:
    """`-d 0` / 负数 = 不定长：一直抓到 Ctrl+C 或外部 `lcapt stop`。"""
    return value if value is not None and value > 0 else None


# --------------------------------------------------------------- 路径默认值


def default_profile_dir() -> Path:
    env = os.environ.get("LCAPT_PROFILE")
    if env:
        return Path(env)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "logel-log-capture" / "profile"


def default_out_dir(profile_dir: Path, logel: Optional[str] = None) -> Path:
    env = os.environ.get("LCAPT_OUT")
    if env:
        return Path(env)
    # 默认落到 <Logel 安装目录>\..\Logel_History（与 GUI 抓取同级）
    try:
        install = find_install(logel)
    except LogelLayoutError:
        return profile_dir / "out"
    return install.root.parent / "Logel_History"


class PathConstraintError(RuntimeError):
    """输出路径等硬约束不满足（例如含中文）。"""


def assert_ascii_path(path: Path, *, label: str = "--out") -> None:
    """Logel 对非 ASCII 输出路径会建会话失败；启动前直接拒绝。"""
    text = os.fspath(path)
    if not text.isascii():
        raise PathConstraintError(
            f"{label} must be a pure ASCII path (no CJK/non-ASCII characters). "
            f"Got: {path}. Write to an ASCII directory first, then copy elsewhere."
        )


# ------------------------------------------------------------------- 日志


class Logger:
    def __init__(self, quiet: bool = False, json_mode: bool = False):
        self.quiet = quiet
        self.json_mode = json_mode

    def __call__(self, message: str) -> None:
        if self.quiet:
            return
        print(message, file=sys.stderr, flush=True)


def _emit(data: Dict[str, object], json_mode: bool, human: List[str]) -> None:
    if json_mode:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        for line in human:
            print(line)


# ------------------------------------------------------------------- 各命令


def cmd_provision(args, log: Logger) -> int:
    try:
        install = find_install(args.logel)
    except LogelLayoutError as exc:
        print(f"[lcapt] {exc}", file=sys.stderr)
        return EXIT_PREFLIGHT
    profile_dir, out_dir = _paths(args)
    try:
        result = provision(
            install,
            profile_dir,
            out_dir,
            link=args.link,
            force_mirror=args.force,
            max_size_mb=args.max_size_mb,
            log=log,
        )
    except (OSError, RuntimeError) as exc:
        print(f"[lcapt] provision failed: {exc}", file=sys.stderr)
        return EXIT_PREFLIGHT
    human = [
        f"profile: {result.profile_dir}",
        f"logel  : {install.exe}",
        f"output : {out_dir}",
        f"status : {'created' if result.created else 'existing (logel.ini refreshed)'}",
        "",
        "next   : lcapt doctor && lcapt capture --duration 60",
    ]
    _emit(result.to_dict(), args.json, human)
    return EXIT_OK


def _playback_exe(args, profile_dir: Path) -> Path:
    """回放优先用共用安装（ini 里 Auto Start Logging=0，不会顺带真抓一份日志）。"""
    try:
        return find_install(getattr(args, "logel", None)).exe
    except LogelLayoutError:
        return profile_dir / "Bin" / "Logel.exe"


def cmd_capture(args, log: Logger) -> int:
    profile_dir, out_dir = _paths(args)
    try:
        install = find_install(args.logel)
    except LogelLayoutError as exc:
        if args.no_provision:
            print(f"[lcapt] {exc}", file=sys.stderr)
            return EXIT_PREFLIGHT
        print(f"[lcapt] {exc}", file=sys.stderr)
        return EXIT_PREFLIGHT

    need_provision = not (profile_dir / "Bin" / "Logel.exe").is_file()
    if need_provision and args.no_provision:
        print(
            f"[lcapt] profile not found: {profile_dir} (add --provision or run `lcapt provision` first)",
            file=sys.stderr,
        )
        return EXIT_PREFLIGHT
    if not args.no_provision:
        provision(install, profile_dir, out_dir, link=args.link, log=log)

    paths = CapturePaths(profile_dir=profile_dir, out_dir=out_dir, class_name=args.class_name)
    warn = other_logel_warning()
    if warn:
        log(f"[capture][WARN] {warn}")
    cap = HeadlessCapture(paths, log=log)
    started = time.time()
    try:
        result = cap.run(
            capture_seconds(args.duration),
            min_bytes=args.min_bytes,
            max_bytes=args.max_bytes,
            stop_timeout=args.stop_timeout,
        )
    except CaptureError as exc:
        print(f"[lcapt] capture failed: {exc}", file=sys.stderr)
        # Only real stop failures (window missing / close timed out), not "stopped with WM_CLOSE" reasons
        msg = str(exc)
        return EXIT_STOP if ("after WM_CLOSE" in msg or "cannot stop safely" in msg) else EXIT_CAPTURE

    except OSError as exc:
        print(f"[lcapt] capture failed: {exc}", file=sys.stderr)
        return EXIT_CAPTURE

    data = result.to_dict()
    data["elapsed_sec"] = round(time.time() - started, 1)

    if args.export:
        try:
            exp = export_session(
                result.session.dir,
                ue_base=args.ue_base,
                playback=args.playback,
                logel_exe=_playback_exe(args, profile_dir),
                logel2txt=args.logel2txt,
                log=log,
            )
            data["export"] = exp.to_dict()
        except ExportError as exc:
            print(f"[lcapt] export failed (capture itself succeeded): {exc}", file=sys.stderr)
            _emit(data, args.json, _human_capture(result))
            return EXIT_EXPORT

    human = _human_capture(result)
    if "export" in data:
        human.append(f"text   : {data['export']['output']} ({data['export']['lines']} lines)")
    _emit(data, args.json, human)
    return EXIT_OK


def _human_capture(result) -> List[str]:
    lines = [
        f"session: {result.session.dir}",
        f"duration: {result.duration_sec:.1f}s, .logel {result.bytes_captured/1024:.0f} KB",
        f"DIAG   : {result.diag_name or '-'} port={result.diag_port}, Logel exit={result.exit_code}",
        f"finish : {'clean (Stop Logging present)' if result.stop_marker else 'incomplete (missing Stop Logging)'}",
    ]
    for warn in result.warnings:
        lines.append(f"warning: {warn}")
    return lines


def cmd_stop(args, log: Logger) -> int:
    profile_dir, _ = _paths(args)
    ok, message = stop_running(profile_dir, args.class_name)
    _emit({"stopped": ok, "message": message}, args.json, [message])
    return EXIT_OK if ok else EXIT_STOP


def cmd_list(args, log: Logger) -> int:
    _, out_dir = _paths(args)
    sessions = list_sessions(out_dir, limit=args.limit)
    rows = [s.summary() for s in sessions]
    human = [f"output dir: {out_dir}", ""]
    for row in rows:
        human.append(
            f"{Path(row['session']).name:<40} {row['size_bytes']/1024:>9.0f} KB  "
            f"{'complete' if row['has_stop_marker'] else 'no stop marker'}  "
            f"{'cached' if row['traceview_cached'] else 'no cache'}"
        )
    if not rows:
        human.append("(empty)")
    _emit({"out_dir": str(out_dir), "sessions": rows}, args.json, human)
    return EXIT_OK


def cmd_export(args, log: Logger) -> int:
    profile_dir, out_dir = _paths(args)
    target = Path(args.session) if args.session else _latest_session(out_dir)
    if target is None:
        print(f"[lcapt] no sessions to export in {out_dir}", file=sys.stderr)
        return EXIT_PREFLIGHT
    output = Path(args.output) if args.output else None
    try:
        result = export_session(
            target,
            output=output,
            ue_base=args.ue_base,
            playback=args.playback,
            logel_exe=_playback_exe(args, profile_dir),
            logel2txt=args.logel2txt,
            log=log,
        )
    except ExportError as exc:
        print(f"[lcapt] export failed: {exc}", file=sys.stderr)
        return EXIT_EXPORT
    _emit(
        result.to_dict(),
        args.json,
        [f"{result.lines} lines -> {result.output}"],
    )
    return EXIT_OK


def cmd_doctor(args, log: Logger) -> int:
    profile_dir, out_dir = _paths(args)
    checks: List[Dict[str, object]] = []
    problems = 0

    def add(name: str, ok: bool, detail: str, hard: bool = True) -> None:
        nonlocal problems
        checks.append({"check": name, "ok": ok, "detail": detail, "hard": hard})
        if not ok and hard:
            problems += 1

    try:
        install = find_install(args.logel)
        add("logel", True, str(install.exe))
    except LogelLayoutError as exc:
        add("logel", False, str(exc))
        install = None

    exe = profile_dir / "Bin" / "Logel.exe"
    add("profile", exe.is_file(), str(profile_dir))
    if exe.is_file():
        ini = profile_dir / "Bin" / "logel.ini"
        try:
            text = read_ini(ini)
            auto = ini_get(text, "Settings", "Auto Start Logging")
            log_dir = ini_get(text, "MS", "LogOutputFolder")
            add("logel.ini auto logging", auto == "1", f"Auto Start Logging={auto}")
            add(
                "logel.ini output dir",
                Path(log_dir or "") == out_dir,
                f"LogOutputFolder={log_dir} (expected {out_dir})",
                hard=False,
            )
        except OSError as exc:
            add("logel.ini", False, f"cannot read {ini}: {exc}")

    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        probe = out_dir / ".lcapt_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        add("output dir writable", True, str(out_dir))
    except OSError as exc:
        add("output dir writable", False, f"{out_dir}: {exc}")

    try:
        cmd = find_logel2txt(Path(args.logel2txt) if args.logel2txt else None)
        add("logel2txt", True, " ".join(cmd))
    except ExportError as exc:
        add("logel2txt", False, str(exc), hard=False)

    ports = list_com_ports()
    add(
        "serial ports",
        bool(ports),
        ", ".join(ports) if ports else "no COM ports found (device may be unplugged)",
        hard=False,
    )

    others = other_logel_pids()
    add(
        "other Logel processes",
        not others,
        (
            "none"
            if not others
            else (
                f"running PID(s): {', '.join(map(str, others))}; "
                "DIAG may be held — close GUI/stray Logel before capture"
            )
        ),
        hard=False,
    )

    lock = profile_dir / ".lcapt.lock"
    add(
        "concurrent capture",
        not lock.is_file(),
        "no capture running" if not lock.is_file() else f"lock present: {lock}",
    )

    def _mark(c: Dict[str, object]) -> str:
        if c["ok"]:
            return "OK  "
        return "ERR " if c.get("hard", True) else "WARN"

    human = [f"{_mark(c)} {c['check']:<20} {c['detail']}" for c in checks]
    if problems:
        human.append("")
        human.append("Hard checks failed; fix them before capturing.")
    _emit({"ok": problems == 0, "checks": checks}, args.json, human)
    return EXIT_OK if problems == 0 else EXIT_PREFLIGHT


def _latest_session(out_dir: Path):
    sessions = list_sessions(out_dir, limit=1)
    return sessions[0].dir if sessions else None


def cmd_version(args, log: Logger) -> int:
    _emit({"version": __version__}, args.json, [f"lcapt {__version__}"])
    return EXIT_OK


# --------------------------------------------------------------- 参数解析


def _paths(args) -> "tuple[Path, Path]":
    profile_dir = Path(args.profile).expanduser() if args.profile else default_profile_dir()
    if args.out:
        out_dir = Path(args.out).expanduser()
        assert_ascii_path(out_dir, label="--out")
    else:
        out_dir = default_out_dir(profile_dir, getattr(args, "logel", None))
        if os.environ.get("LCAPT_OUT"):
            assert_ascii_path(out_dir, label="LCAPT_OUT")
        else:
            assert_ascii_path(out_dir, label="output dir")
    return profile_dir, out_dir


def _add_common(p: argparse.ArgumentParser, *, suppress: bool) -> None:
    d = (lambda v: argparse.SUPPRESS) if suppress else (lambda v: v)
    p.add_argument(
        "--logel",
        default=d(None),
        help=(
            "Logel install dir or Logel.exe (required, or set "
            "LOGEL_EXE / LOGEL_HOME / LOGEL_ROOT)"
        ),
    )
    p.add_argument(
        "--profile",
        default=d(None),
        help=(
            "Headless profile dir (default: LCAPT_PROFILE, else "
            "%%LOCALAPPDATA%%\\logel-log-capture\\profile)"
        ),
    )
    p.add_argument(
        "--out",
        default=d(None),
        help=(
            "Capture output dir (default: <Logel>\\..\\Logel_History, else <profile>\\out; "
            "must be pure ASCII)"
        ),
    )
    p.add_argument("--json", action="store_true", default=d(False), help="Output JSON")
    p.add_argument("-q", "--quiet", action="store_true", default=d(False), help="Only output results")
    if not suppress:
        p.add_argument("-V", "--version", action="version", version=f"lcapt {__version__}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lcapt",
        description=(
            "Unisoc Logel headless capture: hidden window auto-connect -> WM_CLOSE finish "
            "-> optional txt export"
        ),
    )
    _add_common(parser, suppress=False)
    sub = parser.add_subparsers(dest="command", required=True)

    def new(name: str, help_text: str) -> argparse.ArgumentParser:
        sp = sub.add_parser(name, help=help_text, parents=[_common_parent()])
        return sp

    p = new(
        "doctor",
        "Environment self-check (Logel / profile / serial / other Logel / logel2txt)",
    )
    p.add_argument("--logel2txt", default=None)
    p.set_defaults(func=cmd_doctor)

    p = new("provision", "Create/refresh an independent headless profile")
    p.add_argument("--force", action="store_true", help="Force re-mirror of Bin")
    p.add_argument("--link", action="store_true", help="Mirror with hard links (fast, no extra space)")
    p.add_argument("--max-size-mb", type=int, default=None, help="Max single-file size written to logel.ini")
    p.set_defaults(func=cmd_provision)

    p = new("capture", "Headless capture (default 60s)")
    p.add_argument("-d", "--duration", type=float, default=60.0,
                   help="Capture seconds; 0 = until Ctrl+C or `lcapt stop`")
    p.add_argument("--min-bytes", type=int, default=1024, help="Warn when .logel is smaller than this")
    p.add_argument("--max-bytes", type=int, default=None, help="Finish early when .logel exceeds this")
    p.add_argument("--stop-timeout", type=float, default=120.0)
    p.add_argument("--class-name", default=DEFAULT_CLASS, help="Hidden window class name (-n)")
    p.add_argument("--no-provision", action="store_true", help="Do not auto-create the profile")
    p.add_argument("--link", action="store_true", help="Use hard links when auto-creating the profile")
    p.add_argument("--export", action="store_true", help="Export to txt right after capture")
    p.add_argument("--ue-base", default=None, help="UE base time for export, HH:MM:SS.mmm")
    p.add_argument(
        "--no-playback",
        dest="playback",
        action="store_false",
        help="Export from the plain capture cache instead of replaying the .logel "
        "(faster, but misses the last few seconds written at stop time)",
    )
    p.add_argument("--playback", dest="playback", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--logel2txt", default=None)
    p.set_defaults(func=cmd_capture, playback=True)

    p = new("stop", "Stop the running capture (sends WM_CLOSE)")
    p.add_argument("--class-name", default=DEFAULT_CLASS)
    p.set_defaults(func=cmd_stop)

    p = new("list", "List capture sessions")
    p.add_argument("--limit", type=int, default=10)
    p.set_defaults(func=cmd_list)

    p = new("export", "Export a session to txt (via logel2txt)")
    p.add_argument("session", nargs="?", help="Session dir; defaults to the latest")
    p.add_argument("-o", "--output", default=None)
    p.add_argument("--ue-base", default=None)
    p.add_argument(
        "--no-playback",
        dest="playback",
        action="store_false",
        help="Export from the plain capture cache instead of replaying the .logel "
        "(faster, but misses the last few seconds written at stop time)",
    )
    p.add_argument("--playback", dest="playback", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--logel2txt", default=None)
    p.set_defaults(func=cmd_export, playback=True)

    p = new("version", "Version")
    p.set_defaults(func=cmd_version)
    return parser


def _common_parent() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(add_help=False)
    _add_common(p, suppress=True)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    log = Logger(quiet=getattr(args, "quiet", False), json_mode=getattr(args, "json", False))
    try:
        return int(args.func(args, log))
    except KeyboardInterrupt:
        print("[lcapt] interrupted", file=sys.stderr)
        return 130
    except (LogelLayoutError, ExportError, CaptureError, PathConstraintError) as exc:
        print(f"[lcapt] {exc}", file=sys.stderr)
        return EXIT_PREFLIGHT
