"""导出：调用 logel2txt（不重复实现解码）。

两条路：
- plain（默认）：直接读抓取时 Logel 边收边写出的 traceview 缓存，快；
- playback：先用 Logel 命令行回放生成 `*_pb` 再导。R9.23 实测回放会弹一个**隐藏的模态
  警告框**（"No modem version found ... cannot download parser"）并因此挂着，点掉 OK 后
  它会写完 `*_pb` 再 0xC0000005；所以这里自己起回放、自动点框、只认 `*_pb` 是否写出来。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_COM_IN_NAME_RE = re.compile(r"\((COM\d+)\)\s*$", re.IGNORECASE)
_SPRD_DIAG_RE = re.compile(r"SPRD\s+DIAG\s*\(\s*COM\d+\s*\)", re.IGNORECASE)

_LINES_RE = re.compile(r"lines:\s*(\d+)")
_PATH_RE = re.compile(r"output:\s*(.+)")
_NEXT_INDEX_RE = re.compile(r"next_index:\s*(\d+)")

#: 抓取过程中供 Agent 轮询的增量文本文件名（写在会话目录内）
LIVE_TXT_NAME = "live.txt"


class ExportError(RuntimeError):
    """导出失败（找不到 logel2txt、0 行、进程超时等）。"""


def live_txt_path(session_dir: Path) -> Path:
    return Path(session_dir) / LIVE_TXT_NAME


@dataclass
class ExportResult:
    output: Path
    lines: int
    returncode: int
    stderr_tail: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "output": str(self.output),
            "lines": self.lines,
            "returncode": self.returncode,
            "stderr_tail": self.stderr_tail,
        }


def find_logel2txt(explicit: Optional[Path] = None) -> List[str]:
    """返回可直接拼接参数的命令前缀。

    不扫磁盘：显式 `--logel2txt` / `LOGEL2TXT`，或系统 PATH 里的 logel2txt。
    """
    if explicit is not None:
        return _command_for(Path(explicit))

    env = os.environ.get("LOGEL2TXT")
    if env:
        return _command_for(Path(env))

    exe = shutil.which("logel2txt")
    if exe:
        return [exe]
    raise ExportError(
        "logel2txt not found. Specify logel2txt.exe (or .py) via --logel2txt / LOGEL2TXT, "
        "or run `lcapt capture --no-export` and export manually afterwards."
    )


def _command_for(path: Path) -> List[str]:
    if not path.is_file():
        raise ExportError(f"logel2txt not found: {path}")
    if path.suffix.lower() == ".py":
        return [sys.executable, str(path)]
    return [str(path)]


def find_session_logel(session_dir: Path) -> Path:
    """会话目录里的 .logel（顶层优先，其次一层子目录）。"""
    top = sorted(session_dir.glob("*.logel"))
    if top:
        return top[0]
    nested = sorted(session_dir.glob("*/*.logel"))
    if nested:
        return nested[0]
    raise ExportError(f"no .logel in session: {session_dir}")


def _auto_start_disabled(exe: Path):
    """回放用的 profile 若开着 `Auto Start Logging=1`，回放会顺带真抓一份新日志。

    回放前把它临时置 0，结束后恢复；共用安装（本来就是 0）不受影响。
    """
    from contextlib import contextmanager

    from .logel import ini_get, ini_patch, read_ini, write_ini

    @contextmanager
    def _cm():
        ini = exe.parent / "logel.ini"
        original = None
        try:
            original = read_ini(ini)
            if ini_get(original, "Settings", "Auto Start Logging") == "1":
                patched, _ = ini_patch(original, {("Settings", "Auto Start Logging"): "0"})
                write_ini(ini, patched)
        except OSError:
            original = None
        try:
            yield
        finally:
            if original is not None:
                try:
                    write_ini(ini, original)
                except OSError:
                    pass

    return _cm()


_MIN_CACHE_BYTES = 1024


def _pb_cache_ok(pb: Path) -> bool:
    pbs = pb / "traceview.pbs"
    dat = pb / "traceview.dat"
    return pbs.is_file() and dat.is_file() and pbs.stat().st_size >= _MIN_CACHE_BYTES


def drop_tiny_pb(session_dir: Path) -> None:
    """删掉回放留下的空 `*_pb` 缓存，避免 logel2txt 优先选中它导致导出 0 行。"""
    for pb in session_dir.glob("*_pb"):
        if pb.is_dir() and not _pb_cache_ok(pb):
            shutil.rmtree(pb, ignore_errors=True)


def run_playback(
    logel_exe: Optional[Path],
    session_dir: Path,
    *,
    timeout_sec: float = 600.0,
    log=print,
) -> Path:
    """Logel 命令行回放生成 `*_pb`；自动点掉模态框，容忍写完缓存后的崩溃。"""
    from . import win32

    session_dir = session_dir.expanduser().resolve()
    logel_file = find_session_logel(session_dir)
    size = logel_file.stat().st_size
    if size < _MIN_CACHE_BYTES:
        raise ExportError(
            f"{logel_file.name} is only {size} bytes; skipping playback (no log data)"
        )
    pb = logel_file.parent / f"{logel_file.stem}_pb"
    if _pb_cache_ok(pb):
        log(f"[playback] {pb.name} already exists, skipping playback")
        return pb
    if logel_exe is None or not Path(logel_exe).is_file():
        raise ExportError(
            f"Playback requires Logel.exe (got {logel_exe}); "
            "specify the install dir or Logel.exe via --logel"
        )

    exe = Path(logel_exe)
    cmd = [str(exe), "-f", str(logel_file), "-q", "-w", "hide"]
    with _auto_start_disabled(exe):
        log("[playback] " + " ".join(cmd))
        proc = subprocess.Popen(cmd, cwd=str(exe.parent))
        deadline = time.time() + timeout_sec
        dialogs = 0
        try:
            while time.time() < deadline:
                if proc.poll() is not None:
                    break
                for dlg in win32.find_modal_dialogs(proc.pid):
                    text = win32.dialog_text(dlg)
                    if text and dialogs == 0:
                        log(f"[playback] modal dialog: {text}")
                    buttons = [
                        c
                        for c in win32.iter_child_windows(dlg.hwnd)
                        if c.title.strip().upper() == "OK"
                    ]
                    if buttons:
                        win32.click_button(buttons[0].hwnd)
                        dialogs += 1
                time.sleep(0.3)
            else:
                raise ExportError(
                    f"playback did not finish within {timeout_sec:.0f}s "
                    f"(dismissed {dialogs} modal dialogs)"
                )
        finally:
            if proc.poll() is None:
                proc.kill()
        code = proc.poll()

    if not ((pb / "traceview.pbs").is_file() and (pb / "traceview.dat").is_file()):
        raise ExportError(
            f"playback finished (exit={code}) but did not write "
            f"{pb.name}\\traceview.pbs/.dat; this .logel may lack a modem version, so the "
            "parser cannot be downloaded (the \\...\\Parser path in the ini must be reachable)"
        )
    if not _pb_cache_ok(pb):
        raise ExportError(
            f"playback produced an empty cache ({pb.name}\\traceview.pbs is "
            f"{(pb / 'traceview.pbs').stat().st_size} bytes)"
        )
    if code not in (0,):
        log(
            f"[playback][WARN] Logel exited with code {code}, but {pb.name}\\traceview.* "
            "was written; continuing export (R9.23 crashing after writing the cache is a "
            "known issue)"
        )
    return pb


def export_session(
    session_dir: Path,
    *,
    output: Optional[Path] = None,
    ue_base: Optional[str] = None,
    playback: bool = False,
    logel_exe: Optional[Path] = None,
    logel2txt: Optional[Path] = None,
    timeout_sec: float = 900.0,
    log=print,
) -> ExportResult:
    session_dir = session_dir.expanduser().resolve()
    if not session_dir.exists():
        raise ExportError(f"input not found: {session_dir}")
    out = output or (session_dir.parent / (session_dir.name + ".txt"))
    out.parent.mkdir(parents=True, exist_ok=True)

    cmd = find_logel2txt(logel2txt)
    used_pb = False
    if playback:
        # 自己起回放（要自动点掉模态框），生成 *_pb 后 logel2txt 会优先用它。
        # 回放失败/产出空缓存时自动退回 plain 缓存，不让「默认回放」降低成功率。
        try:
            run_playback(logel_exe, session_dir, timeout_sec=min(timeout_sec, 900.0), log=log)
            used_pb = True
        except ExportError as exc:
            log(f"[export][WARN] playback unavailable ({exc}); exporting from the plain cache")
            drop_tiny_pb(session_dir)
    base_cmd = cmd + [str(session_dir), "-o", str(out)]
    if ue_base:
        base_cmd += ["--ue-base", ue_base]

    def _run(extra: List[str]) -> subprocess.CompletedProcess:
        full = base_cmd + extra
        log("[export] " + " ".join(full))
        try:
            return subprocess.run(
                full,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_sec,
            )
        except subprocess.TimeoutExpired as exc:
            raise ExportError(f"logel2txt timed out ({timeout_sec:.0f}s): {exc}") from exc

    proc = _run([])
    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    m = _LINES_RE.search(stdout)
    lines = int(m.group(1)) if m else 0
    if not out.is_file():
        pm = _PATH_RE.search(stdout)
        if pm:
            out = Path(pm.group(1).strip())
    if (lines <= 0 or not out.is_file()) and used_pb:
        # _pb 解析不出内容时，删掉它退回 plain 缓存再试一次
        log("[export][WARN] _pb cache produced no data; retrying from the plain cache")
        for pb in session_dir.glob("*_pb"):
            shutil.rmtree(pb, ignore_errors=True)
        proc = _run([])
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        m = _LINES_RE.search(stdout)
        lines = int(m.group(1)) if m else 0
        if not out.is_file():
            pm = _PATH_RE.search(stdout)
            if pm:
                out = Path(pm.group(1).strip())
    if lines <= 0 or not out.is_file():
        tail = "\n".join(stderr.strip().splitlines()[-8:])
        raise ExportError(f"export produced no data (rc={proc.returncode}):\n{tail}")
    if proc.returncode not in (0, 1):
        log(f"[export][WARN] logel2txt exited with code {proc.returncode}, but produced {lines} lines")

    stale = "not under a *_pb directory" in stderr
    if stale:
        log("[export][WARN] using a non-_pb cache; line count may be lower than Logel Export Trace")
    log(f"[export] {lines} lines -> {out}")
    return ExportResult(
        output=out,
        lines=lines,
        returncode=proc.returncode,
        stderr_tail="\n".join(stderr.strip().splitlines()[-8:]),
    )


@dataclass
class LiveExportResult:
    output: Path
    added: int
    next_index: int
    returncode: int

    def to_dict(self) -> Dict[str, object]:
        return {
            "output": str(self.output),
            "added": self.added,
            "next_index": self.next_index,
            "returncode": self.returncode,
        }


def _ue_base_ms(ue_base: Optional[str]) -> int:
    if not ue_base:
        return 0
    try:
        from logel2txt.format import parse_hms_ms  # type: ignore

        return int(parse_hms_ms(ue_base))
    except Exception:
        # 延迟到 subprocess 路径再交给 logel2txt 解析
        return -1


def _import_logel2txt_exporters(logel2txt: Optional[Path] = None):
    """尽量 import logel2txt.exporters（源码旁路或已安装包）。"""
    try:
        from logel2txt import exporters as ex  # type: ignore

        return ex
    except ImportError:
        pass
    candidates: List[Path] = []
    if logel2txt is not None:
        p = Path(logel2txt)
        if p.is_file() and p.suffix.lower() == ".py":
            candidates.append(p.parent)
        elif p.is_dir():
            candidates.append(p)
    env = os.environ.get("LOGEL2TXT")
    if env:
        ep = Path(env)
        candidates.append(ep.parent if ep.is_file() else ep)
    for root in candidates:
        if not root.is_dir():
            continue
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        try:
            from logel2txt import exporters as ex  # type: ignore

            return ex
        except ImportError:
            continue
    return None


def _live_existing_lines(ex, out: Path) -> int:
    if not out.is_file():
        return 0
    try:
        return int(ex.count_data_lines(out))
    except Exception:
        return 0


def _live_append(ex, out: Path, data_lines: List[str], log=print) -> Optional[int]:
    if not data_lines:
        return 0
    write_header = not out.is_file() or out.stat().st_size == 0
    try:
        ex.append_data_lines(out, data_lines, write_header=write_header)
    except OSError as exc:
        log(f"[live][WARN] append live.txt failed: {exc}")
        return None
    return len(data_lines)


def _live_export_from_traceview(
    ex,
    session,
    out: Path,
    ue_ms: int,
    log=print,
) -> Optional[LiveExportResult]:
    cache = session.traceview_dir
    if cache is None:
        return None
    dat = cache / "traceview.dat"
    pbs = cache / "traceview.pbs"
    try:
        if not dat.is_file() or not pbs.is_file():
            return None
        if dat.stat().st_size <= 0 or pbs.stat().st_size <= 512:
            return None  # Logel 抓取中常只建空头，停抓才刷满
    except OSError:
        return None

    prev = _live_existing_lines(ex, out)
    try:
        slice_ = ex.export_traceview_incremental(
            dat,
            pbs,
            ue_ms,
            start_index=0,
            enrich_from_siblings=False,
        )
    except (OSError, ValueError) as exc:
        log(f"[live][WARN] incremental decode failed: {exc}")
        return None

    if not slice_.lines:
        return LiveExportResult(out, 0, 0, 0)

    # traceview 是更完整来源；若先前用 raw .logel 写过，这里整表覆盖
    try:
        from logel2txt.format import HDR  # type: ignore

        if prev > 0 and prev != len(slice_.lines):
            ex.write_lines(out, [HDR] + slice_.lines)
            log(
                f"[live] rewrote {len(slice_.lines)} lines -> {out} "
                f"(traceview replaced prior {prev}-line live)"
            )
            return LiveExportResult(out, len(slice_.lines), slice_.next_index, 0)
        if prev >= len(slice_.lines):
            return LiveExportResult(out, 0, slice_.next_index, 0)
        added = _live_append(ex, out, slice_.lines[prev:], log=log)
    except OSError as exc:
        log(f"[live][WARN] write live.txt failed: {exc}")
        return None
    if added is None:
        return None
    if added:
        log(f"[live] +{added} lines -> {out} (traceview next_index={slice_.next_index})")
    return LiveExportResult(out, added, slice_.next_index, 0)


def _live_export_from_logel_raw(
    ex,
    session,
    out: Path,
    ue_ms: int,
    log=print,
) -> Optional[LiveExportResult]:
    """抓取中 traceview 未刷盘时：从增长中的 .logel 抽明文（不完整，但可边抓边看）。"""
    logel = session.logel
    if logel is None or not logel.is_file():
        return None
    try:
        if logel.stat().st_size < 64:
            return LiveExportResult(out, 0, _live_existing_lines(ex, out), 0)
        all_lines = ex.export_from_logel_raw(logel, ue_ms)
    except (OSError, ValueError) as exc:
        log(f"[live][WARN] raw .logel extract failed: {exc}")
        return None

    data = all_lines[1:] if all_lines else []
    start = _live_existing_lines(ex, out)
    # 前缀稳定时按行下标续传；若扫描结果变短则整表重写
    if start > len(data):
        try:
            from logel2txt.format import HDR  # type: ignore

            ex.write_lines(out, [HDR] + data)
        except OSError as exc:
            log(f"[live][WARN] rewrite live.txt failed: {exc}")
            return None
        added = len(data)
        if added:
            log(f"[live] +{added} lines -> {out} (raw .logel rewrite)")
        return LiveExportResult(out, added, len(data), 0)

    new_lines = data[start:]
    added = _live_append(ex, out, new_lines, log=log)
    if added is None:
        return None
    if added:
        log(f"[live] +{added} lines -> {out} (raw .logel, total={start + added})")
    return LiveExportResult(out, added, start + added, 0)


def _live_export_inprocess(
    session_dir: Path,
    out: Path,
    *,
    ue_base: Optional[str],
    logel2txt: Optional[Path],
    log=print,
) -> Optional[LiveExportResult]:
    """进程内增量追加 live.txt。

    优先 plain traceview；抓取中 Logel 往往不刷 dat（0 字节），则回退 .logel 明文抽取。
    """
    ex = _import_logel2txt_exporters(logel2txt)
    if ex is None:
        return None
    from .logel import Session

    session = Session(session_dir)
    ue_ms = _ue_base_ms(ue_base)
    if ue_ms < 0:
        return None

    tv = _live_export_from_traceview(ex, session, out, ue_ms, log=log)
    if tv is not None and tv.added > 0:
        return tv

    raw = _live_export_from_logel_raw(ex, session, out, ue_ms, log=log)
    if raw is not None:
        return raw
    return tv if tv is not None else LiveExportResult(out, 0, 0, 0)


def live_export_once(
    session_dir: Path,
    *,
    output: Optional[Path] = None,
    ue_base: Optional[str] = None,
    logel2txt: Optional[Path] = None,
    timeout_sec: float = 60.0,
    log=print,
) -> Optional[LiveExportResult]:
    """追加 ``live.txt``：优先进程内增量解码，失败再调 ``logel2txt follow --once``。

    失败（无缓存、锁冲突、工具缺失）返回 ``None`` 或 ``added=0``，不抛——抓取主循环应跳过本轮。
    """
    session_dir = session_dir.expanduser().resolve()
    out = output or live_txt_path(session_dir)

    # 1) 进程内直读（与外部 follow 成功路径一致）
    try:
        inproc = _live_export_inprocess(
            session_dir, out, ue_base=ue_base, logel2txt=logel2txt, log=log
        )
        if inproc is not None and inproc.added > 0:
            return inproc
    except Exception as exc:  # noqa: BLE001
        log(f"[live][WARN] in-process live export failed: {exc}")
        inproc = None

    # 2) 回退 subprocess follow（显式指定 plain 缓存目录名）
    #    仅在进程内失败/抛错，或缓存已有数据却仍 0 行时尝试
    from .logel import Session

    tv = Session(session_dir).traceview_dir
    cache_nonempty = False
    if tv is not None:
        try:
            pbs = tv / "traceview.pbs"
            cache_nonempty = pbs.is_file() and pbs.stat().st_size > 512
        except OSError:
            cache_nonempty = False
    if inproc is not None and inproc.added == 0 and not cache_nonempty:
        return inproc

    try:
        cmd = find_logel2txt(logel2txt)
    except ExportError as exc:
        log(f"[live][WARN] {exc}")
        return inproc

    full = cmd + ["follow", str(session_dir), "-o", str(out), "--once"]
    if tv is not None:
        full += ["--traceview", tv.name]
    if ue_base:
        full += ["--ue-base", ue_base]
    try:
        proc = subprocess.run(
            full,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired:
        log(f"[live][WARN] logel2txt follow timed out ({timeout_sec:.0f}s)")
        return inproc
    except OSError as exc:
        log(f"[live][WARN] cannot run logel2txt follow: {exc}")
        return inproc

    stdout = (proc.stdout or "") + "\n" + (proc.stderr or "")
    m_lines = _LINES_RE.search(stdout)
    m_next = _NEXT_INDEX_RE.search(stdout)
    added = int(m_lines.group(1)) if m_lines else 0
    next_index = int(m_next.group(1)) if m_next else 0
    if proc.returncode not in (0, 1, 2):
        tail = "\n".join(stdout.strip().splitlines()[-6:])
        log(f"[live][WARN] follow exit={proc.returncode}: {tail}")
        return inproc
    if added:
        log(f"[live] +{added} lines -> {out} (next_index={next_index})")
    elif "[WARN]" in (proc.stderr or ""):
        warn = "\n".join(
            ln for ln in (proc.stderr or "").splitlines() if "[WARN]" in ln
        )[-300:]
        if warn:
            log(f"[live][WARN] {warn}")
    result = LiveExportResult(
        output=out,
        added=added,
        next_index=next_index,
        returncode=proc.returncode,
    )
    if added > 0 or inproc is None:
        return result
    return inproc


def list_com_ports() -> List[str]:
    """从注册表读当前存在的 COM 口（不依赖第三方库）。"""
    if os.name != "nt":
        return []
    try:
        import winreg
    except ImportError:  # pragma: no cover
        return []
    ports: List[str] = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DEVICEMAP\SERIALCOMM") as key:
            index = 0
            while True:
                try:
                    _, value, _ = winreg.EnumValue(key, index)
                except OSError:
                    break
                ports.append(str(value))
                index += 1
    except OSError:
        return []
    return sorted(set(ports), key=lambda s: int("".join(filter(str.isdigit, s)) or 0))


def list_com_devices() -> Dict[str, str]:
    """COM 口 -> 友好名（如 ``SPRD DIAG (COM31)``）。Windows 下用 PnP；失败则退回裸 COM 名。"""
    if os.name != "nt":
        return {}
    named = _list_com_devices_pnp()
    if named:
        return named
    return {p: p for p in list_com_ports()}


def _list_com_devices_pnp() -> Dict[str, str]:
    """Get-CimInstance Win32_PnPEntity，解析 ``Name (COMx)``。"""
    try:
        proc = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "[Console]::OutputEncoding = [Text.UTF8Encoding]::UTF8; "
                "Get-CimInstance Win32_PnPEntity | "
                "Where-Object { $_.Name -match 'COM\\d+' } | "
                "ForEach-Object { $_.Name }",
            ],
            capture_output=True,
            timeout=45,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    raw = proc.stdout or b""
    text = raw.decode("utf-8", errors="replace")
    if not text.strip():
        text = (proc.stderr or b"").decode("utf-8", errors="replace")
        # 有的机器 stdout 空时仍可能因编码问题落在 bytes；再试 gbk
        if not text.strip():
            text = raw.decode("gbk", errors="replace")
    devices: Dict[str, str] = {}
    for line in text.splitlines():
        name = line.strip()
        if not name:
            continue
        m = _COM_IN_NAME_RE.search(name)
        if not m:
            continue
        devices[m.group(1).upper()] = name
    return devices


def find_sprd_diag_ports(devices: Optional[Dict[str, str]] = None) -> List[str]:
    """当前像主 DIAG 的口（``SPRD DIAG (COMx)``，排除 SENSOR/AGDSP）。"""
    devices = devices if devices is not None else list_com_devices()
    found: List[str] = []
    for com, name in devices.items():
        upper = name.upper()
        if "SENSOR" in upper or "AGDSP" in upper:
            continue
        if _SPRD_DIAG_RE.search(name):
            found.append(com.upper() if com.upper().startswith("COM") else com)
    return sorted(set(found), key=lambda s: int("".join(filter(str.isdigit, s)) or 0))


def wait_for_sprd_diag(
    *,
    poll_sec: float = 1.0,
    log=None,
) -> List[str]:
    """阻塞直到出现至少一个 SPRD DIAG 口；返回 COM 名列表。"""
    last_note = 0.0
    while True:
        ports = find_sprd_diag_ports()
        if ports:
            return ports
        now = time.time()
        if log and (now - last_note) >= max(2.0, poll_sec):
            log("[watch] waiting for SPRD DIAG ...")
            last_note = now
        time.sleep(max(0.2, poll_sec))
