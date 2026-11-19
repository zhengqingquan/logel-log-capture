"""无头 profile 的建/改。

要点（实测得出的硬约束）：
- Logel 只读 **exe 同目录** 的 logel.ini，不读当前工作目录；
- 因此必须整份复制 Bin 做独立 profile，不能改团队共用的那份；
- 配置文件（*.ini / *.xml）绝不能硬链接，否则会连带改到原安装。
"""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Tuple

from .logel import LogelInstall, ini_patch, read_ini, write_ini

MANIFEST_NAME = "lcapt-profile.json"

#: 这些后缀必须复制（工具运行时会写回），不能硬链接
COPY_SUFFIXES = {".ini", ".xml", ".dat", ".log"}
#: 运行期日志目录，镜像时跳过
SKIP_DIRS = {"log"}

#: 写进 profile 的 logel.ini 关键项
def profile_patches(out_dir: Path, title: str) -> Dict[Tuple[str, str], str]:
    return {
        ("Settings", "Auto Start Logging"): "1",
        ("Settings", "TitleName"): title,
        ("Settings", "VersionCheck"): "0",
        ("Settings", "ShowException"): "0",
        ("Settings", "PlaySound"): "0",
        ("MS", "LogOutputFolder"): str(out_dir),
    }


@dataclass
class ProvisionResult:
    profile_dir: Path
    bin_dir: Path
    ini: Path
    created: bool
    files_linked: int
    files_copied: int
    missing_keys: Tuple[str, ...]

    def to_dict(self) -> Dict[str, object]:
        return {
            "profile_dir": str(self.profile_dir),
            "bin_dir": str(self.bin_dir),
            "ini": str(self.ini),
            "created": self.created,
            "files_linked": self.files_linked,
            "files_copied": self.files_copied,
            "missing_keys": list(self.missing_keys),
        }


def provision(
    install: LogelInstall,
    profile_dir: Path,
    out_dir: Path,
    *,
    link: bool = False,
    title: str = "LCAPT",
    force_mirror: bool = False,
    log=print,
) -> ProvisionResult:
    """建/更新独立无头 profile。已存在时只刷新 ini（除非 force_mirror）。

    不写 MaxSize/MaxTime：官方单位未证实，分段请用 ``lcapt capture -d`` / ``--max-bytes``。
    """
    profile_dir = profile_dir.expanduser().resolve()
    bin_dir = profile_dir / "Bin"
    out_dir.mkdir(parents=True, exist_ok=True)

    created = not (bin_dir / "Logel.exe").is_file()
    linked = copied = 0
    if created or force_mirror:
        log(
            f"[provision] mirroring {install.bin_dir} -> {bin_dir} "
            f"({'hardlink' if link else 'copy'} mode)"
        )
        linked, copied = _mirror(install.bin_dir, bin_dir, link=link)

    ini = bin_dir / "logel.ini"
    if not ini.is_file():
        raise RuntimeError(f"logel.ini missing from profile: {ini}")

    patches = profile_patches(out_dir, title)

    text = read_ini(ini)
    new_text, missing = ini_patch(text, patches)
    write_ini(ini, new_text)

    manifest = {
        "source_install": str(install.root),
        "source_exe": str(install.exe),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "link_mode": bool(link),
        "out_dir": str(out_dir),
        "title": title,
        "logel_ini_keys": {f"{sec}.{key}": val for (sec, key), val in patches.items()},
    }
    (profile_dir / MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    log(
        f"[provision] done: {profile_dir} "
        f"(created={created}, linked {linked} files, copied {copied} files)"
    )
    if missing:
        log(f"[provision][WARN] keys missing from the original logel.ini, appended: {', '.join(missing)}")
    return ProvisionResult(
        profile_dir=profile_dir,
        bin_dir=bin_dir,
        ini=ini,
        created=created,
        files_linked=linked,
        files_copied=copied,
        missing_keys=tuple(missing),
    )


def _mirror(src: Path, dst: Path, *, link: bool) -> Tuple[int, int]:
    """复制/硬链接整棵 Bin。配置类与 Log 目录走复制，避免连带改到原安装。"""
    dst.mkdir(parents=True, exist_ok=True)
    linked = copied = 0
    for root, dirs, files in os.walk(src):
        rel = Path(root).relative_to(src)
        if rel.parts and rel.parts[0].lower() in SKIP_DIRS:
            dirs[:] = []
            continue
        (dst / rel).mkdir(parents=True, exist_ok=True)
        for name in files:
            s = Path(root) / name
            d = dst / rel / name
            if d.exists():
                continue
            must_copy = s.suffix.lower() in COPY_SUFFIXES
            if link and not must_copy:
                try:
                    os.link(s, d)
                    linked += 1
                    continue
                except OSError:
                    pass
            shutil.copy2(s, d)
            copied += 1
    return linked, copied
