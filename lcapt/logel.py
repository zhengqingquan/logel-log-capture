"""Logel 安装发现、ini 读写、Application.log 解析、会话枚举。

这里刻意不调用 Logel 的解析库（闭源、无稳定 API），只用它的命令行与文件产物。
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

EXE_NAME = "Logel.exe"
INI_NAME = "logel.ini"
APP_LOG_REL = Path("Log") / "Application.log"

#: 自动识别端口时，Logel 会打印这些内容；有 Diag Port matched / Open Diag Port 才算连上设备
_PORT_MATCH_RE = re.compile(r"Diag Port matched \[(.+?)\s+(\d+)\]")
_OPEN_DIAG_RE = re.compile(r"ConnectByUART: Open Diag Port (\d+)")
_NO_DIAG_RE = re.compile(r"No Diag Port matched")
_NO_LOG_RE = re.compile(r"No Log Port found")
_VERSION_RE = re.compile(r"Logel tool version:\s*(\S+)")


class LogelLayoutError(RuntimeError):
    """给定的路径不是一份 Logel 安装。"""


@dataclass(frozen=True)
class LogelInstall:
    """一份 Logel 安装（含 Bin/Logel.exe）。"""

    root: Path

    @property
    def bin_dir(self) -> Path:
        return self.root / "Bin"

    @property
    def exe(self) -> Path:
        return self.bin_dir / EXE_NAME

    @property
    def ini(self) -> Path:
        return self.bin_dir / INI_NAME

    @property
    def app_log(self) -> Path:
        return self.bin_dir / APP_LOG_REL

    def __str__(self) -> str:  # pragma: no cover - 仅展示
        return str(self.root)


def as_install(path: Path) -> LogelInstall:
    """把「安装目录 / Bin 目录 / Logel.exe」统一归一为安装目录。"""
    path = path.expanduser().resolve()
    if path.is_file() and path.name.lower() == EXE_NAME.lower():
        path = path.parent.parent if path.parent.name.lower() == "bin" else path.parent
    elif (path / "Bin").is_dir():
        pass
    elif path.name.lower() == "bin" and (path / EXE_NAME).is_file():
        path = path.parent
    inst = LogelInstall(path)
    if not inst.exe.is_file():
        raise LogelLayoutError(f"{path} does not contain Bin\\{EXE_NAME}")
    return inst


def find_install(
    explicit: Optional[Path] = None,
    roots: Optional[Sequence[Path]] = None,
    prefer: Optional[str] = None,
) -> LogelInstall:
    """按 显式参数 → 环境变量 → 用户指定的根目录 找 Logel 安装。

    不做任何磁盘扫描：不传 `--logel` 也没配环境变量就报错，由用户明确指定。
    环境变量：LOGEL_EXE / LOGEL_HOME / LOGEL_ROOT（与 logel2txt 保持一致）。
    `prefer` 用于挑版本目录名（如 "R9.21"），默认优先不带版本后缀的 Logel。
    """
    if explicit is not None:
        return as_install(Path(explicit))

    for env in ("LOGEL_EXE", "LOGEL_HOME"):
        val = os.environ.get(env)
        if val:
            try:
                return as_install(Path(val))
            except LogelLayoutError:
                pass

    candidates: List[Path] = []
    root_env = os.environ.get("LOGEL_ROOT")
    if root_env:
        candidates.extend(_scan_root(Path(root_env)))
    for root in roots or ():
        candidates.extend(_scan_root(Path(root)))
    if not candidates:
        raise LogelLayoutError(
            "Logel install not specified. Use --logel (a dir containing Bin\\Logel.exe, or "
            "Logel.exe itself), or set LOGEL_EXE / LOGEL_HOME / LOGEL_ROOT."
        )

    if prefer:
        for cand in candidates:
            if prefer.lower() in str(cand).lower():
                return as_install(cand)

    # 优先无版本后缀的 Logel（团队日常使用的那份），其次按 R9.xx 数字倒序
    def _rank(p: Path) -> Tuple[int, str]:
        name = p.name
        m = re.search(r"[Rr]9[._](\d+)", name)
        return (0 if m is None else 1, name)

    for cand in sorted(candidates, key=_rank):
        try:
            return as_install(cand)
        except LogelLayoutError:
            continue
    raise LogelLayoutError("no usable Logel under the specified directory (missing Bin\\Logel.exe)")


def _scan_root(root: Path) -> Iterable[Path]:
    if not root.is_dir():
        return []
    found: List[Path] = []
    if (root / "Bin" / EXE_NAME).is_file():
        found.append(root)
    plain = root / "Logel"
    if (plain / "Bin" / EXE_NAME).is_file():
        found.append(plain)
    for child in sorted(root.glob("Logel_R*")):
        if (child / "Bin" / EXE_NAME).is_file():
            found.append(child)
    return found


# ---------------------------------------------------------------- ini 读写


def read_ini(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    return raw.decode("utf-8", "replace")


def write_ini(path: Path, text: str, newline: str = "\r\n") -> None:
    data = newline.join(text.splitlines())
    if not data.endswith(newline):
        data += newline
    tmp = path.with_suffix(path.suffix + ".lcapt.tmp")
    tmp.write_bytes(data.encode("utf-8"))
    os.replace(tmp, path)


def ini_get(text: str, section: str, key: str) -> Optional[str]:
    cur = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            cur = stripped[1:-1].strip()
            continue
        if cur != section or "=" not in stripped:
            continue
        k, _, v = stripped.partition("=")
        if k.strip().lower() == key.lower():
            return v.strip()
    return None


def ini_patch(text: str, patches: Dict[Tuple[str, str], str]) -> Tuple[str, List[str]]:
    """按 (section, key) 精准改值；不存在的键追加到对应 section 末尾。

    返回 (新文本, 未找到 section 而追加到文件末尾的 key 列表)。
    """
    wanted = {k: v for k, v in patches.items()}
    done = set()
    out: List[str] = []
    cur: Optional[str] = None

    def flush_section_end(section: Optional[str]) -> None:
        if section is None:
            return
        for (sec, key), val in list(wanted.items()):
            if sec == section and (sec, key) not in done:
                out.append(f"{key}={val}")
                done.add((sec, key))

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            flush_section_end(cur)
            cur = stripped[1:-1].strip()
            out.append(line)
            continue
        if cur is not None and "=" in stripped:
            k = stripped.partition("=")[0].strip()
            hit = None
            for (sec, key) in wanted:
                if sec == cur and key.lower() == k.lower() and (sec, key) not in done:
                    hit = (sec, key)
                    break
            if hit is not None:
                out.append(f"{hit[1]}={wanted[hit]}")
                done.add(hit)
                continue
        out.append(line)

    flush_section_end(cur)
    missing = [f"[{sec}] {key}" for (sec, key) in wanted if (sec, key) not in done]
    for (sec, key), val in wanted.items():
        if (sec, key) not in done:
            out.extend([f"[{sec}]", f"{key}={val}"])
            done.add((sec, key))
    return "\n".join(out), missing


# ------------------------------------------------------- Application.log


@dataclass
class AppLogTail:
    """Application.log 的增量读取器，用于判断「连上了 / 没连上」。"""

    path: Path
    offset: int = 0

    @classmethod
    def from_end(cls, path: Path) -> "AppLogTail":
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        return cls(path=path, offset=size)

    def read_since(self) -> str:
        try:
            size = self.path.stat().st_size
        except OSError:
            return ""
        if size < self.offset:  # 日志被轮转/清空
            self.offset = 0
        if size == self.offset:
            return ""
        with self.path.open("rb") as fh:
            fh.seek(self.offset)
            data = fh.read(size - self.offset)
        self.offset = size
        return data.decode("utf-8", "replace")


def analyze_connect_log(text: str) -> Dict[str, object]:
    """从 Application.log 片段里抽取连接状态。

    注意：`Connect, type: UART, result: N` 里的 N 不是 0/1 成功标志，
    真正可靠的判据是 `ConnectByUART: Open Diag Port N` 与 `Diag Port matched [...]`。
    """
    diag = _OPEN_DIAG_RE.search(text)
    matched = _PORT_MATCH_RE.search(text)
    version = _VERSION_RE.search(text)
    return {
        "connected": bool(diag),
        "diag_port": int(diag.group(1)) if diag else None,
        "diag_name": matched.group(1).strip() if matched else None,
        "no_diag_port": bool(_NO_DIAG_RE.search(text)),
        "no_log_port": bool(_NO_LOG_RE.search(text)),
        "tool_version": version.group(1) if version else None,
    }


# --------------------------------------------------------------- 会话


@dataclass
class Session:
    """一次抓取会话目录 `<时间戳>_armlog`。"""

    dir: Path

    @property
    def name(self) -> str:
        return self.dir.name

    def _single(self, pattern: str) -> Optional[Path]:
        hits = sorted(self.dir.glob(pattern))
        return hits[0] if hits else None

    @property
    def logel(self) -> Optional[Path]:
        return self._single("*.logel")

    @property
    def lst(self) -> Optional[Path]:
        return self._single("*.lst")

    @property
    def size_bytes(self) -> int:
        lg = self.logel
        return lg.stat().st_size if lg else 0

    @property
    def traceview_dir(self) -> Optional[Path]:
        """Logel 抓取时边收边解码，缓存目录名与 .logel 同名。"""
        lg = self.logel
        if lg is None:
            return None
        cand = self.dir / lg.stem
        return cand if cand.is_dir() else None

    def markers(self) -> Dict[str, bool]:
        txt = ""
        lst = self.lst
        if lst is not None:
            txt = lst.read_text(encoding="utf-8", errors="ignore")
        return {
            "start": "Start Logging" in txt,
            "stop": "Stop Logging" in txt,
        }

    def summary(self) -> Dict[str, object]:
        m = self.markers()
        return {
            "session": str(self.dir),
            "logel": str(self.logel) if self.logel else None,
            "size_bytes": self.size_bytes,
            "has_stop_marker": m["stop"],
            "traceview_cached": self.traceview_dir is not None,
        }


def find_new_session(out_dir: Path, since_ts: float, timeout_sec: float = 60.0) -> Optional[Session]:
    """等待 `since_ts` 之后新建、且已经出现 .logel 的会话目录。"""
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        best: Optional[Path] = None
        for cand in out_dir.glob("*_armlog"):
            try:
                mtime = cand.stat().st_mtime
            except OSError:
                continue
            if mtime + 2 < since_ts:
                continue
            if best is None or mtime > best.stat().st_mtime:
                best = cand
        if best is not None:
            sess = Session(best)
            if sess.logel is not None:
                return sess
        if timeout_sec <= 0.1:  # 单次扫描模式，供监控循环高频调用
            return None
        time.sleep(0.5)
    return None


def list_sessions(out_dir: Path, limit: Optional[int] = None) -> List[Session]:
    if not out_dir.is_dir():
        return []
    sessions = [Session(p) for p in out_dir.glob("*_armlog") if p.is_dir()]
    sessions.sort(key=lambda s: s.dir.name, reverse=True)
    return sessions[:limit] if limit else sessions
