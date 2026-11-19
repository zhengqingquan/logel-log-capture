# logel-log-capture 更新记录

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

CLI 版本以 `lcapt version` / `pyproject.toml` / `lcapt.__version__` 为准。

## [Unreleased]

### 新增

- **`doctor` / `capture` 启动前检测其它 `Logel.exe`**：若已有 GUI 或残留进程，给出软警告
  （列出 PID，提示 DIAG 可能被占）。`doctor` 该项为非硬性检查（不因此 exit 2）；
  `capture` 仍继续启动，真正占口时仍靠原有 `Open Diag Port fail` 提示。
- **`--out` / `LCAPT_OUT` / 解析后的输出目录启动前校验纯 ASCII**：含中文等非 ASCII 时立即
  exit 2，不再等到会话超时。
- 控制台关闭（点 X）时尽力向隐藏 Logel 发 `WM_CLOSE`，减少孤儿进程占 DIAG。

### 变更

- `doctor` 软检查失败显示 **`WARN`**（不再与硬失败同用 `ERR`）；JSON 增加 `hard` 字段。
- `serial ports` 改为软检查（未插机也可 doctor 通过）。
- `--out` 帮助文案与默认一致：`<Logel>\..\Logel_History`，并注明须纯 ASCII。
- `abort()` 失败兜底改为先 `WM_CLOSE` 再短等，最后才 `kill`。

## [1.0.0] - 2026-11-19

首发版本：不开 GUI 抓展锐（Unisoc / SPRD）Logel 日志。

### 新增

- `lcapt capture`：独立无头 profile + `-w hide` 隐藏窗口 + AutoTest 自动连机抓取；
  `-d` / `--max-bytes` 控制时长与容量；`-d 0` 与负数均表示不定长，抓到 Ctrl+C 或外部
  `lcapt stop`。
- `lcapt stop`：向隐藏窗口 `PostMessage(WM_CLOSE)` 正常收尾，不 kill 进程；支持跨终端停止
  正在跑的抓取（抓取侧识别为「外部结束」，仍按完整会话校验）。
- `lcapt provision`：整份镜像 `Bin` 建独立 profile；`--link` 用硬链接加速，但
  `.ini` / `.xml` / `.dat` / `.log` 强制复制——硬链接 `logel.ini` 会改到团队共用安装。
- `lcapt doctor`：Logel / profile / `Auto Start Logging` / 输出目录可写 / 串口 / logel2txt 自检。
- `lcapt export`：对接 logel2txt 导出 txt；**默认走 playback**（Logel 回放 `.logel` 重新解析，
  补全停止瞬间的最后几秒；实测同一会话约 855 → 995 行）。回放时自动点掉隐藏模态警告框
  （"No modem version found … cannot download parser"），容忍写完 `*_pb` 后的 `0xC0000005`；
  回放前临时把 profile 的 `Auto Start Logging` 置 0。`--playback` 保留为兼容别名；
  `--no-playback` 直接读抓取缓存（快，但少最后几秒）。
- 失败判据：未识别到 DIAG 口、Logel 提前退出、`WM_CLOSE` 超时、`.lst` 缺 `Stop Logging`
  都会显式报错/告警，而不是静默产出空日志。
- 抓取超时时若出现 `Open Diag Port fail`，直接提示 DIAG 串口可能被其他 Logel / 串口工具占用，
  并列出残留 Logel 进程 PID。
- 每次会话写 `_lcapt.json` 记录 DIAG 口、时长、字节数、工具版本与警告。
- 并发锁：同一 profile 第二个抓取会被拒绝（exit 3）且不会误删对方的锁；陈旧锁自动接管；兼容 BOM。

### 变更

- **Logel 路径必须显式指定**：`--logel`，或 `LOGEL_EXE` / `LOGEL_HOME` / `LOGEL_ROOT`；
  不扫盘、不猜测 `C:/D:/E:\Unisoc_Tool`。未指定直接报错（exit 2）。
  profile 默认 `%LOCALAPPDATA%\logel-log-capture\profile`。
- **抓取输出默认** `<Logel 安装目录>\..\Logel_History`（与 GUI 日志同级）；可用 `--out` /
  `LCAPT_OUT` 覆盖；未给 `--logel` 且无环境变量时退回 `<profile>\out`。
- `logel2txt` 只认 `--logel2txt` / `LOGEL2TXT` 或系统 PATH，不探测同级源码目录。
- 所有面向用户的打印（错误 / 警告 / 提示 / argparse help）为英文。

### 文档

- **禁止 `--out` / `LCAPT_OUT` 使用中文等非 ASCII 路径**：实测指向含中文目录时 DIAG 可能连上但
  会话建失败 / 超时无 `.logel`。应先写到 ASCII 目录（如 `D:\Unisoc_Tool\Logel_History\`），
  再拷贝到中文工作区。已写入本仓库 `README.md`。

### 已知限制

- `Module` 列恒为空（缓存里没有模块名，只有 Logel 现场解析的内存里有）。
