"""无头抓取的生命周期：起进程 → 等会话 → 监控 → WM_CLOSE 收尾 → 校验。

设计取舍：
- 不 kill 进程（kill 会丢尾包、.logel 可能没写完）；统一用 WM_CLOSE 走工具自己的 OnClose；
- 不做「改了原安装 logel.ini 再改回来」这种有副作用的做法，一律用独立 profile；
- 判据用 Application.log 里的 `ConnectByUART: Open Diag Port N`，不看 `result` 字段
  （实测 result 的 0/1 不是成功标志）。
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import win32
from .logel import AppLogTail, Session, analyze_connect_log, find_new_session


class CaptureError(RuntimeError):
    """抓取过程失败（端口没认到、进程提前退出、收尾超时等）。"""


@dataclass
class CapturePaths:
    profile_dir: Path
    out_dir: Path
    class_name: str = "LogelCapture"

    @property
    def bin_dir(self) -> Path:
        return self.profile_dir / "Bin"

    @property
    def exe(self) -> Path:
        return self.bin_dir / "Logel.exe"

    @property
    def app_log(self) -> Path:
        return self.bin_dir / "Log" / "Application.log"

    @property
    def lock_file(self) -> Path:
        return self.profile_dir / ".lcapt.lock"


@dataclass
class CaptureResult:
    session: Session
    diag_port: Optional[int]
    diag_name: Optional[str]
    duration_sec: float
    bytes_captured: int
    exit_code: Optional[int]
    stop_marker: bool
    tool_version: Optional[str] = None
    warnings: list = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        data = self.session.summary()
        data.update(
            {
                "diag_port": self.diag_port,
                "diag_name": self.diag_name,
                "duration_sec": round(self.duration_sec, 1),
                "bytes_captured": self.bytes_captured,
                "logel_exit_code": self.exit_code,
                "stop_marker": self.stop_marker,
                "tool_version": self.tool_version,
                "warnings": list(self.warnings),
            }
        )
        return data


def pid_alive(pid: int) -> bool:
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    import ctypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return False
    ctypes.windll.kernel32.CloseHandle(handle)
    return True


def _logel_pids() -> List[int]:
    """当前运行的 Logel.exe 进程 PID（tasklist；拿不到就返回空）。"""
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq Logel.exe", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    pids: List[int] = []
    for line in out.splitlines():
        cells = [c.strip().strip('"') for c in line.split(",")]
        if len(cells) >= 2 and cells[0].lower() == "logel.exe":
            try:
                pids.append(int(cells[1]))
            except ValueError:
                pass
    return pids


def other_logel_pids(exclude_pid: Optional[int] = None) -> List[int]:
    """除 exclude_pid 外仍在跑的 Logel.exe PID（升序去重）。"""
    pids = set(_logel_pids())
    if exclude_pid is not None:
        pids.discard(int(exclude_pid))
    return sorted(pids)


def other_logel_warning(exclude_pid: Optional[int] = None) -> str:
    """启动前软警告：已有其它 Logel（含 GUI）时 DIAG 可能被占。无则返回空串。"""
    others = other_logel_pids(exclude_pid)
    if not others:
        return ""
    listed = ", ".join(str(p) for p in others)
    return (
        f"Other Logel.exe process(es) already running: {listed}. "
        "They may hold the DIAG COM port (GUI capture or a leftover from a previous run). "
        "Close them before capture, or expect Open Diag Port fail."
    )


def port_busy_hint(log_text: str, own_pid: Optional[int] = None) -> str:
    """Logel 日志里出现 `Open Diag Port fail` 时，给出「串口被占用」的排查提示。"""
    if "Open Diag Port fail" not in log_text:
        return ""
    others = other_logel_pids(own_pid)
    hint = (
        "Logel reports 'Open Diag Port fail': the DIAG COM port is likely held by another "
        "Logel or serial tool (a stray process left by an earlier failed capture, or a "
        "colleague's GUI capture)."
    )
    if others:
        hint += f" Other Logel process(es) running: {', '.join(map(str, others))}."
    hint += " Close them and retry."
    return hint


class HeadlessCapture:
    def __init__(self, paths: CapturePaths, log=print, poll_sec: float = 0.5):
        self.paths = paths
        self._log = log
        self._poll = poll_sec
        self.proc: Optional[subprocess.Popen] = None
        self._tail: Optional[AppLogTail] = None
        self._log_text: str = ""
        self._lock_owned = False

    # ------------------------------------------------------------- 锁
    def acquire_lock(self) -> None:
        lock = self.paths.lock_file
        if lock.is_file():
            try:
                data = json.loads(lock.read_text(encoding="utf-8-sig"))
                pid = int(data.get("pid", 0))
            except (OSError, ValueError):
                pid = 0
            if pid and pid_alive(pid):
                raise CaptureError(
                    f"Capture already running (pid={pid}); run `lcapt stop` or wait for it"
                )
            lock.unlink(missing_ok=True)
        self.paths.profile_dir.mkdir(parents=True, exist_ok=True)
        lock.write_text(
            json.dumps({"pid": os.getpid(), "started_at": time.strftime("%Y-%m-%d %H:%M:%S")}),
            encoding="utf-8",
        )
        self._lock_owned = True

    def release_lock(self) -> None:
        # 只删自己写的那把锁：被别人拒绝启动时不能顺手删掉对方正在用的锁
        if self._lock_owned:
            self.paths.lock_file.unlink(missing_ok=True)
            self._lock_owned = False

    # ----------------------------------------------------------- 启动
    def start(self) -> None:
        if not self.paths.exe.is_file():
            raise CaptureError(f"Missing {self.paths.exe}; run `lcapt provision` first")
        self.paths.out_dir.mkdir(parents=True, exist_ok=True)
        self._tail = AppLogTail.from_end(self.paths.app_log)
        self._started_at = time.time()
        self._log(f"[capture] starting {self.paths.exe} -w hide -n {self.paths.class_name}")
        self.proc = subprocess.Popen(
            [str(self.paths.exe), "-w", "hide", "-n", self.paths.class_name],
            cwd=str(self.paths.bin_dir),
        )

    def _drain_log(self) -> str:
        if self._tail is None:
            return ""
        chunk = self._tail.read_since()
        if chunk:
            self._log_text = (self._log_text + chunk)[-20000:]
        return chunk

    def _conn(self) -> Dict[str, object]:
        return analyze_connect_log(self._log_text)

    # ------------------------------------------------- 等会话 + 等连上
    def wait_ready(self, timeout_sec: float = 60.0) -> Session:
        deadline = time.time() + timeout_sec
        session: Optional[Session] = None
        while time.time() < deadline:
            if self.proc is not None and self.proc.poll() is not None:
                raise CaptureError(
                    f"Logel exited early (exit={self.proc.returncode}); check the tail of "
                    f"{self.paths.app_log}"
                )
            self._drain_log()
            if session is None:
                session = find_new_session(self.paths.out_dir, self._started_at, timeout_sec=0.1)
            conn = self._conn()
            if conn["connected"]:
                if session is None:
                    session = find_new_session(self.paths.out_dir, self._started_at, timeout_sec=5)
                if session is not None:
                    self._log(
                        f"[capture] connected {conn['diag_name'] or 'DIAG'} port={conn['diag_port']}"
                        f", session {session.name}"
                    )
                    return session
            if conn["no_diag_port"] and int(conn.get("diag_port") or 0) == 0:
                raise CaptureError(
                    "SPRD DIAG port not detected. Check: (1) device is plugged in and the "
                    "driver enumerated (SPRD DIAG should appear in Device Manager); "
                    "(2) no other program is holding that COM port (a colleague's Logel/serial "
                    "tool); (3) logel.ini [MS] AutoIdentifyPort is 1."
                )
            time.sleep(self._poll)
        hint = port_busy_hint(self._log_text, self.proc.pid if self.proc else None)
        extra = f"\n{hint}" if hint else ""
        raise CaptureError(
            f"Timed out after {timeout_sec:.0f}s waiting for 'connected + session created'."
            f"{extra}\nLog: {self.paths.app_log}"
        )

    # ------------------------------------------------------------- 监控
    def _monitor(
        self,
        duration_sec: Optional[float],
        *,
        max_bytes: Optional[int] = None,
        progress_every: float = 5.0,
    ) -> bool:
        """抓取主循环。duration_sec=None 表示抓到 Ctrl+C / 进程结束 / 外部 `lcapt stop`。

        返回 True 表示到点/到量，由本进程负责收尾；False 表示 Logel 已经自己退出了
        （通常是另一个终端跑了 `lcapt stop`，发了 WM_CLOSE），此时不该再发一次关闭信号。
        """
        assert self.proc is not None
        started = time.time()
        last_report = 0.0
        while True:
            if self.proc.poll() is not None:
                return False
            self._drain_log()
            elapsed = time.time() - started
            size = self._session_size()
            if progress_every and elapsed - last_report >= progress_every:
                last_report = elapsed
                self._log(f"[capture] {int(elapsed)}s captured, .logel {size/1024:.0f} KB")
            if max_bytes and size >= max_bytes:
                self._log(f"[capture] reached --max-bytes ({max_bytes}), finishing early")
                return True
            if duration_sec is not None and elapsed >= duration_sec:
                return True
            sleep_for = self._poll
            if duration_sec is not None:
                sleep_for = min(self._poll, max(0.05, duration_sec - elapsed))
            time.sleep(sleep_for)

    def _session(self) -> Optional[Session]:
        sess = find_new_session(self.paths.out_dir, self._started_at, timeout_sec=0.1)
        return sess

    def _session_size(self) -> int:
        sess = self._session()
        return sess.size_bytes if sess else 0

    # ------------------------------------------------------------- 停止
    def stop(self, timeout_sec: float = 120.0) -> int:
        assert self.proc is not None
        pid = self.proc.pid
        win = win32.find_main_window(pid, self.paths.class_name)
        if win is None:
            # 兜底：按类名跨进程找（窗口可能在 launcher 之后重建过）
            win = win32.find_main_window(None, self.paths.class_name)
        if win is None:
            raise CaptureError(
                f"Logel main window not found (class {self.paths.class_name}); cannot stop "
                "safely. Do NOT taskkill; try `lcapt stop` first, then investigate"
            )
        self._log(f"[capture] sending WM_CLOSE (hwnd={win.hwnd})")
        win32.post_close(win.hwnd)
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            try:
                code = self.proc.wait(timeout=1)
                self._drain_log()
                return int(code)
            except subprocess.TimeoutExpired:
                continue
        raise CaptureError(
            f"Logel still running {timeout_sec:.0f}s after WM_CLOSE; a modal dialog may be "
            f"blocking it. Check {self.paths.app_log} or close the process manually"
        )

    def abort(self) -> None:
        """失败兜底：先 WM_CLOSE，短等后再 kill（避免直接 taskkill 撕坏 .logel）。"""
        if self.proc is None or self.proc.poll() is not None:
            return
        try:
            win = win32.find_main_window(self.proc.pid, self.paths.class_name)
            if win is None:
                win = win32.find_main_window(None, self.paths.class_name)
            if win is not None:
                win32.post_close(win.hwnd)
                try:
                    self.proc.wait(timeout=8)
                    return
                except subprocess.TimeoutExpired:
                    pass
        except Exception:
            pass
        if self.proc.poll() is None:
            self.proc.kill()

    def _install_console_close_hook(self) -> None:
        """控制台点 X / 注销时尽量 WM_CLOSE，减少孤儿 Logel 占 DIAG。"""
        if os.name != "nt":
            return
        import ctypes

        HandlerRoutine = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)
        # CTRL_CLOSE_EVENT=2, CTRL_LOGOFF=5, CTRL_SHUTDOWN=6
        close_kinds = {2, 5, 6}

        def _handler(ctrl_type: int) -> int:
            if int(ctrl_type) not in close_kinds:
                return 0
            try:
                self._log("[capture] console closing; sending WM_CLOSE to Logel")
                if self.proc is not None and self.proc.poll() is None:
                    win = win32.find_main_window(self.proc.pid, self.paths.class_name)
                    if win is None:
                        win = win32.find_main_window(None, self.paths.class_name)
                    if win is not None:
                        win32.post_close(win.hwnd)
                        try:
                            self.proc.wait(timeout=8)
                        except subprocess.TimeoutExpired:
                            pass
            except Exception:
                pass
            return 1

        self._console_handler = HandlerRoutine(_handler)
        ctypes.windll.kernel32.SetConsoleCtrlHandler(self._console_handler, True)

    # --------------------------------------------------------- 一次完整跑
    def run(
        self,
        duration_sec: Optional[float] = None,
        *,
        min_bytes: int = 0,
        max_bytes: Optional[int] = None,
        stop_timeout: float = 120.0,
    ) -> CaptureResult:
        warnings: List[str] = []
        started = time.time()
        session: Optional[Session] = None
        conn: Dict[str, object] = {}
        interrupted = False
        external_stop = False
        exit_code = 0
        try:
            self.acquire_lock()
            self.start()
            self._install_console_close_hook()
            session = self.wait_ready()
            conn = self._conn()
            try:
                finished = self._monitor(duration_sec, max_bytes=max_bytes)
            except KeyboardInterrupt:
                interrupted = True
                finished = True
                self._log("[capture] Ctrl+C received, stopping gracefully")
            if finished:
                exit_code = self.stop(stop_timeout)
            else:
                code = self.proc.returncode if self.proc else None
                if code not in (0, None):
                    raise CaptureError(f"Logel exited unexpectedly during capture (exit={code})")
                external_stop = True
                exit_code = int(code or 0)
                self._log("[capture] Logel was closed externally, treating as normal end")
        except BaseException:
            self.abort()
            raise
        finally:
            self.release_lock()

        assert session is not None
        elapsed = time.time() - started
        size = session.size_bytes
        if size == 0:
            warnings.append(".logel is empty: device may not have arm log enabled (AT+ARMLOG=1)")
        elif size < min_bytes:
            warnings.append(
                f".logel is only {size} bytes (minimum {min_bytes}); may not have received logs"
            )
        markers = session.markers()
        if not markers["stop"]:
            warnings.append(".lst has no `Stop Logging`; session may not have finished cleanly")
        if session.traceview_dir is None:
            warnings.append("No traceview cache; export falls back to plaintext extraction (incomplete)")
        if interrupted:
            warnings.append("Manually interrupted (Ctrl+C)")
        elif external_stop:
            warnings.append("Ended by external `lcapt stop` (WM_CLOSE still used; files intact)")

        result = CaptureResult(
            session=session,
            diag_port=conn.get("diag_port"),
            diag_name=conn.get("diag_name"),
            duration_sec=elapsed,
            bytes_captured=size,
            exit_code=exit_code,
            stop_marker=bool(markers["stop"]),
            tool_version=conn.get("tool_version"),
            warnings=warnings,
        )
        self._write_run_meta(result)
        return result

    def _write_run_meta(self, result: CaptureResult) -> None:
        meta = result.to_dict()
        try:
            (result.session.dir / "_lcapt.json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            pass


def stop_running(profile_dir: Path, class_name: str = "LogelCapture") -> Tuple[bool, str]:
    """给外部 `lcapt stop` 用：按窗口类名找隐藏窗口并发 WM_CLOSE。"""
    win = win32.find_main_window(None, class_name)
    if win is None:
        return False, f"Logel window with class {class_name} not found (capture may not be running)"
    win32.post_close(win.hwnd)
    lock = profile_dir / ".lcapt.lock"
    pid = 0
    if lock.is_file():
        try:
            pid = int(json.loads(lock.read_text(encoding="utf-8-sig")).get("pid", 0))
        except (OSError, ValueError):
            pid = 0
    deadline = time.time() + 120
    while time.time() < deadline:
        if pid and not pid_alive(pid):
            lock.unlink(missing_ok=True)
            return True, f"Stopped (launcher pid={pid} exited)"
        if win and not win32.enum_windows(class_name=class_name):
            lock.unlink(missing_ok=True)
            return True, "Stopped (window is gone)"
        time.sleep(0.5)
    return False, "WM_CLOSE sent but the window is still there after 120s; check manually"
