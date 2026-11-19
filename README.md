# logel-log-capture

**不开 GUI 抓展锐（Unisoc / SPRD）Logel 日志**：隐藏窗口自动连机抓取 → 用窗口消息正常收尾 → 可选转 txt。

源码仓库：<https://github.com/zhengqingquan/logel-log-capture>

```
lcapt capture -d 60 --export
```

产物与手动点工具栏抓出来的完全同构：`<时间戳>_armlog\` 目录，里面有 `.logel`、`.lst`、以及 Logel 边收边解码的 `traceview.*` 缓存；转文本交给 [logel2txt](../logel2txt)。

## 为什么是这个方案

Logel **没有"开始抓取"的命令行参数**。官方 User Guide 第 14 章的 `-f/-d/-r/-t/-n/-p/-w/-q/-m/-l/-o` 全部只作用于"打开/回放一个已有文件"。真正能自动抓取的是它给产测用的 AutoTest 路径，本工具把它包装成一条命令：

| 环节 | 做法 | 实测证据（R9.23.1102.0，UIS8910） |
|------|------|-----------------------------------|
| 开始抓取 | profile 的 `logel.ini` 写 `[Settings] Auto Start Logging=1`，启动即自动识别端口并开抓 | `Application.log`: `OnConnect ... AutoTest: 1` → `SetLogelFilePath ...<ts>_armlog\<ts>.logel` → `ConnectByUART: Open Diag Port 22` |
| 不弹窗 | `Logel.exe -w hide -n <窗口类名>` | 主窗口 `IsWindowVisible=False`，类名可控 |
| 停止 | 向该窗口 `PostMessage(WM_CLOSE)`（等价于点右上角 X） | `OnClose → OnStopLogging → CloseFile Write Header Done → ExitInstance`，退出码 0，`.lst` 出现 `Stop Logging` |
| 不改团队配置 | 整份复制 `Bin` 做独立 profile | Logel **只读 exe 同目录** 的 `logel.ini`（不读当前工作目录） |
| 转文本 | 调 `logel2txt` 读抓取时已生成的缓存 | 20s 抓取 → 1000 行；60s 抓取 → 数千行 |

三个必须知道的坑：

1. **别改共用安装的 `logel.ini`**——它是全团队 GUI 在用的那份；本工具永远只改 `--profile` 目录里的副本。
2. **配置文件绝不能硬链接**。硬链接 `logel.ini` 等于直接改原安装（本项目第一版就踩过，已还原）。`.ini/.xml/.dat/.log` 一律复制，见 `lcapt/profile.py`。
3. **别 `taskkill`**。强杀会丢尾包、`.logel` 可能不完整；停止一律走 `WM_CLOSE`，本工具不会 kill 进程。

## 安装

```powershell
cd <本项目目录>
pip install -e .
lcapt -V
```

不装也能跑：`python lcapt.py <子命令>`。要求 Windows + Python ≥ 3.10，无第三方依赖。

## 快速开始

```powershell
# 先指定 Logel 安装位置（也可以设 LOGEL_EXE / LOGEL_HOME / LOGEL_ROOT）
lcapt doctor --logel "D:\Unisoc_Tool\Logel"          # 自检：Logel / profile / 串口 / logel2txt
lcapt provision --logel "D:\Unisoc_Tool\Logel"       # 建独立无头 profile（复制 Bin，约 300MB，一次性）
lcapt capture -d 60 --export --logel "D:\Unisoc_Tool\Logel"   # 抓 60s，并转成 txt
lcapt list                                         # 看历史会话
lcapt export                                       # 把最新会话再导一次（可换 --ue-base）
```

长期/手动场景：

```powershell
lcapt capture -d 0                 # 一直抓到 Ctrl+C，或另开终端 lcapt stop
lcapt stop                         # 发 WM_CLOSE 收尾（不会 kill）
```

## 子命令

| 命令 | 说明 |
|------|------|
| `doctor` | 环境自检，硬性检查不过返回 2 |
| `provision [--link] [--force] [--max-size-mb N]` | 建/刷新 profile。`--link` 用硬链接镜像（快、不占空间；配置类文件仍复制） |
| `capture [-d SEC] [--export] [--no-playback] [--ue-base T] [--min-bytes N] [--max-bytes N]` | 抓取。`-d 0` = 抓到 Ctrl+C/`stop`；`--max-bytes` 达到上限提前收尾 |
| `stop` | 按窗口类名找隐藏窗口发 `WM_CLOSE` |
| `list [--limit N]` | 会话列表（大小 / 是否完整收尾 / 是否有解码缓存） |
| `export [SESSION] [-o FILE] [--ue-base T] [--no-playback]` | 调 logel2txt 导出；省略 SESSION 取最新 |
| `version` | 版本 |

通用参数：`--logel PATH`、`--profile DIR`、`--out DIR`、`--json`、`-q`。

**Logel 路径必须由用户指定**：`--logel <安装目录或 Logel.exe>`，或设 `LOGEL_EXE` / `LOGEL_HOME` / `LOGEL_ROOT`。本工具**不扫磁盘、不猜安装位置**，没指定就直接报错。

默认值：profile 为 `%LOCALAPPDATA%\logel-log-capture\profile`（可用 `LCAPT_PROFILE` 覆盖）；
输出目录默认是 **`<Logel 安装目录>\..\Logel_History`**（即 `D:\Unisoc_Tool\Logel` → `D:\Unisoc_Tool\Logel_History`），
和 GUI 抓出来的日志放一起；可用 `--out DIR` 或 `LCAPT_OUT` 覆盖。没给 `--logel` 也没环境变量时退回 `<profile>\out`。

**硬约束：`--out` / `LCAPT_OUT` 必须是纯 ASCII 路径**（禁止中文等非 ASCII 字符）。
实测把输出指到含中文的目录（如桌面 `测试日志\...`）时，DIAG 可能连上但会话目录建不起来 / 超时无 `.logel`。
正确做法：先抓到 `D:\Unisoc_Tool\Logel_History\...`，再把 `.txt` / `.logel` 拷到中文工作区。

导出默认走 **`--playback`**（Logel 重新解析 `.logel`，补全停止瞬间的最后几秒，行数与 Export Trace 一致）；`--no-playback` 直接读抓取缓存，快但会少最后几秒。

环境变量：`LCAPT_PROFILE`、`LCAPT_OUT`、`LCAPT_WINDOW_CLASS`、`LOGEL2TXT`、`LOGEL_HOME` / `LOGEL_EXE` / `LOGEL_ROOT`（与 logel2txt 一致）。

## 退出码

| 码 | 含义 |
|----|------|
| 0 | 成功 |
| 2 | 前置检查失败（找不到 Logel / profile / logel2txt、串口不可用等） |
| 3 | 抓取失败（没认到 DIAG 口、Logel 提前退出等） |
| 4 | 停止失败（`WM_CLOSE` 后超时，可能弹了模态框） |
| 5 | 抓取成功但导出失败 |
| 130 | Ctrl+C |

## 一次抓取实际发生了什么

```
provision → 起 Logel.exe -w hide -n LogelCapture（cwd=profile\Bin）
         → 轮询 <out>\*_armlog 与 Application.log，等 `ConnectByUART: Open Diag Port N`
         → 按 -d / --max-bytes 计时计流量（每 5s 打一行进度到 stderr）
         → PostMessage(WM_CLOSE) → 等进程退出（默认 120s）
         → 校验 .lst 有 `Stop Logging`、.logel 非空、缓存目录在
         → 写 <会话>\_lcapt.json（时长、DIAG 口、版本、警告）
         → 可选：logel2txt 导出 txt
```

失败当场给判据：没认出 DIAG 口会直接报「没识别到 SPRD DIAG 端口」并列出三条排查方向，不会干等到超时。

## 已知限制

- **`Module` 列恒为空**：`logel2txt` 从 `traceview.dat/.pbs` 取的是"已解码文本"，缓存里没有模块名；
  模块名只存在于 Logel 现场解析的内存里，所以生成 `*_pb` 也不会补上。要模块列只能用 Logel 自己的
  GUI「Export To Text File」，或修好解析库后用 `Logel.exe -f <logel> -o mt`（见下条）。
- **回放/CLI 导出被解析库卡住**：`.lst` 里 `Modem Version:` 是空的，而 `logel.ini` 的
  `[iLog] External_Parser=\\shextnas1\Parser`、`Internal_Parser=\\shrdnas2\Parser` 在本机不可达，
  Logel 会弹一个**隐藏的模态框**「WARNING: No modem version found. Logel cannot download parser…」
  并挂住；点掉 OK 后它写完（有时不写）`*_pb` 就 `0xC0000005`。导出**默认走 playback**，已能自动
  点掉这个框并容忍崩溃后的 `*_pb`；`--no-playback` 可退回读缓存。
  实测同一会话：普通缓存 855 行 → `_pb` 缓存 995 行（多 140 行 / +16%，多的是停止前最后几秒）。
- 同一台机器同时只能跑一个抓取（本工具用 `<profile>\.lcapt.lock` 挡并发）；同事开着 GUI 抓同一个 DIAG 口会抢串口。
- 工具报错弹窗会卡住无人值守抓取（profile 里已设 `ShowException=0`、`PlaySound=0`，仍不能 100% 免疫）。
- `MaxTime` / `MaxSize` 的单位官方没写死，本工具不依赖它们做分段；需要分段请用 `-d` 多次调用。

## 故障排查

| 现象 | 处理 |
|------|------|
| `没识别到 SPRD DIAG 端口` | 设备管理器确认有 `SPRD DIAG (COMxx)`；关掉占用该口的其它程序；确认 `[MS] AutoIdentifyPort=1` |
| `Logel 提前退出` | 看 `<profile>\Bin\Log\Application.log` 末尾；多为版本/解析库不匹配 |
| `WM_CLOSE 后仍不退出` | 大概率弹了模态框；人工关掉该进程，`Application.log` 里能看到卡在哪 |
| `.logel` 为空 | 设备侧没开 armlog：`AT+ARMLOG=1`（DSP log 用 `AT+SPDSPOP=2`） |
| 导出 0 行 | 会话里没有 `traceview` 缓存；确认抓取时 `.logel` 在增长 |
| 中文乱码（重定向到文件时） | `set PYTHONUTF8=1` 或 `chcp 65001`（仅控制台；**不能**给 `--out` 开中文路径） |
| `--out` 含中文 / 超时无会话 | 改用 ASCII 目录（如 `D:\Unisoc_Tool\Logel_History\`），抓完再拷贝 |

## 打包 exe

```powershell
pip install pyinstaller
pyinstaller --onefile --name lcapt --clean --noconfirm lcapt.py
# 产物 dist\lcapt.exe：单文件，目标机不需要 Python
```

`build/`、`dist/`、`*.spec` 已在 `.gitignore` 里，不会进仓库。给 skill 用时把 `dist\lcapt.exe`
放到 skill 目录即可。

## 开发

```powershell
python -m pytest tests -q
python lcapt.py doctor
python lcapt.py capture -d 10
```

模块分工：`logel.py`（安装发现 / ini / Application.log 解析 / 会话）、`profile.py`（无头 profile 镜像与 ini 补丁）、`win32.py`（窗口与 `WM_CLOSE`）、`capture.py`（生命周期与校验）、`export.py`（logel2txt 适配）、`cli.py`（argparse）。

MIT License.
