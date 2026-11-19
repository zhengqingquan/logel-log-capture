# logel-log-capture

**不开 GUI 抓展锐（Unisoc / SPRD）Logel 日志**：隐藏窗口自动连机抓取 → 用窗口消息正常收尾 → 可选转 txt。

- 源码：<https://github.com/zhengqingquan/logel-log-capture>
- 发版（含 `lcapt.exe` 压缩包）：<https://github.com/zhengqingquan/logel-log-capture/releases>
- 转文本工具：[logel2txt](https://github.com/zhengqingquan/logel2txt)（可选，仅 `--export` / `export` 需要）

```
lcapt capture -d 60 --export --logel "<Logel 安装目录>"
```

产物与手动点工具栏抓出来的同构：`<时间戳>_armlog\` 目录，内含 `.logel`、`.lst`、以及 Logel 边收边解码的 `traceview.*` 缓存。

## 为什么是这个方案

Logel **没有「开始抓取」的命令行参数**。官方 User Guide 第 14 章的 `-f/-d/-r/-t/-n/-p/-w/-q/-m/-l/-o` 全部只作用于「打开/回放一个已有文件」。真正能自动抓取的是产测用的 AutoTest 路径，本工具把它包装成一条命令：

| 环节 | 做法 | 实测证据（R9.23.1102.0，UIS8910） |
|------|------|-----------------------------------|
| 开始抓取 | profile 的 `logel.ini` 写 `[Settings] Auto Start Logging=1`，启动即自动识别端口并开抓 | `Application.log`: `OnConnect ... AutoTest: 1` → `SetLogelFilePath ...<ts>_armlog\<ts>.logel` → `ConnectByUART: Open Diag Port 22` |
| 不弹窗 | `Logel.exe -w hide -n <窗口类名>` | 主窗口 `IsWindowVisible=False`，类名可控 |
| 停止 | 向该窗口 `PostMessage(WM_CLOSE)`（等价于点右上角 X） | `OnClose → OnStopLogging → CloseFile Write Header Done → ExitInstance`，退出码 0，`.lst` 出现 `Stop Logging` |
| 不改团队配置 | 整份复制 `Bin` 做独立 profile | Logel **只读 exe 同目录** 的 `logel.ini`（不读当前工作目录） |
| 转文本 | 调 [logel2txt](https://github.com/zhengqingquan/logel2txt) 读抓取/回放缓存 | 20s 抓取 → 约千行；60s → 数千行 |

三个必须知道的坑：

1. **别改共用安装的 `logel.ini`**——它是全团队 GUI 在用的那份；本工具永远只改 `--profile` 目录里的副本。
2. **配置文件绝不能硬链接**。硬链接 `logel.ini` 等于直接改原安装。`.ini` / `.xml` / `.dat` / `.log` 一律复制，见 `lcapt/profile.py`。
3. **别 `taskkill`**。强杀会丢尾包、`.logel` 可能不完整；停止一律走 `WM_CLOSE`，本工具不会 kill 进程。

## 安装

**推荐**：从 [Releases](https://github.com/zhengqingquan/logel-log-capture/releases) 下载 `logel-log-capture-*-windows-x64.zip`，解压后使用其中的 `lcapt.exe`（目标机无需 Python）。

从源码运行（Windows + Python ≥ 3.10，无第三方依赖）：

```powershell
cd <本项目目录>
pip install -e .
lcapt -V
# 或不安装：python lcapt.py <子命令>
```

依赖本机已安装 **Logel**；若要用 `--export` / `export`，还需 [logel2txt](https://github.com/zhengqingquan/logel2txt) 在 PATH 中，或通过 `--logel2txt` / `LOGEL2TXT` 指定。

## 快速开始

```powershell
# 将 <Logel> 换成本机安装目录（也可设 LOGEL_EXE / LOGEL_HOME / LOGEL_ROOT）
lcapt doctor --logel "<Logel>"          # 自检：Logel / profile / 串口 / logel2txt
lcapt provision --logel "<Logel>"       # 建独立无头 profile（复制 Bin，约 300MB，一次性）
lcapt capture -d 60 --export --logel "<Logel>"   # 抓 60s，并转成 txt
lcapt list
lcapt export                            # 把最新会话再导一次（可换 --ue-base）
```

长期 / 手动场景：

```powershell
lcapt capture -d 0 --logel "<Logel>"   # 一直抓到 Ctrl+C，或另开终端 lcapt stop
lcapt stop                             # 发 WM_CLOSE 收尾（不会 kill）
```

烧写后自动抓（常驻等 DIAG，口出现就开抓；掉线停干净再等下一轮）：

```powershell
lcapt watch --logel "<Logel>"          # 默认每轮抓到掉线；Ctrl+C 结束 watch
# 另开终端烧写：dloader flash ...
# 开机枚举出 SPRD DIAG 后会自动开始新一轮 capture
```

## 子命令

| 命令 | 说明 |
|------|------|
| `doctor` | 环境自检（含其它 Logel 软检查）；硬性检查不过返回 2 |
| `provision [--link] [--force] [--max-size-mb N]` | 建/刷新 profile。`--link` 用硬链接镜像（配置类文件仍复制） |
| `capture [-d SEC] [--export] [--no-playback] [--ue-base T] [--min-bytes N] [--max-bytes N]` | 抓取。`-d 0` / 负数 = 抓到 Ctrl+C/`stop`；`--max-bytes` 达上限提前收尾 |
| `watch [-d SEC] [--settle SEC] [--poll SEC] [--once]` | 等到 **SPRD DIAG** 出现后自动 `capture`；掉线释放后再等。适合烧写后开机抓 log |
| `stop` | 按窗口类名找隐藏窗口发 `WM_CLOSE` |
| `list [--limit N]` | 会话列表（大小 / 是否完整收尾 / 是否有解码缓存） |
| `export [SESSION] [-o FILE] [--ue-base T] [--no-playback]` | 调 logel2txt 导出；省略 SESSION 取最新 |
| `version` | 版本 |

通用参数：`--logel PATH`、`--profile DIR`、`--out DIR`、`--json`、`-q`。

**Logel 路径必须由用户指定**：`--logel <安装目录或 Logel.exe>`，或设 `LOGEL_EXE` / `LOGEL_HOME` / `LOGEL_ROOT`。本工具**不扫磁盘、不猜安装位置**，没指定就直接报错。

默认值：

- profile：`%LOCALAPPDATA%\logel-log-capture\profile`（可用 `LCAPT_PROFILE` 覆盖）
- 输出目录：`<Logel 安装目录>\..\Logel_History`（与 GUI 抓取同级）；可用 `--out` / `LCAPT_OUT` 覆盖；未解析到 Logel 时退回 `<profile>\out`

**硬约束：`--out` / `LCAPT_OUT` 必须是纯 ASCII 路径**（禁止中文等非 ASCII 字符）。指向含中文目录时，DIAG 可能连上但会话建失败 / 超时无 `.logel`。应先写到 ASCII 目录，再把产物拷到中文工作区。

导出默认走 **playback**（Logel 重新解析 `.logel`，补全停止瞬间的最后几秒）；`--no-playback` 直接读抓取缓存（快，但少最后几秒）。`--playback` 保留为兼容别名。

环境变量：`LCAPT_PROFILE`、`LCAPT_OUT`、`LCAPT_WINDOW_CLASS`、`LOGEL2TXT`、`LOGEL_HOME` / `LOGEL_EXE` / `LOGEL_ROOT`。

## 退出码

| 码 | 含义 |
|----|------|
| 0 | 成功（`doctor` 仅有软警告 `WARN` 时也是 0） |
| 2 | 硬性前置失败（找不到 Logel / profile、输出目录不可写、`--out` 非 ASCII 等） |
| 3 | 抓取失败（没认到 DIAG 口、Logel 提前退出等） |
| 4 | 停止失败（`WM_CLOSE` 后超时，可能弹了模态框） |
| 5 | 抓取成功但导出失败 |
| 130 | Ctrl+C |

说明：`logel2txt` 缺失、未插机无串口、已有其它 Logel 进程等为 **软检查**（`WARN`），不单独导致 exit 2。

## 一次抓取实际发生了什么

```
provision → 起 Logel.exe -w hide -n LogelCapture（cwd=profile\Bin）
         → 轮询 <out>\*_armlog 与 Application.log，等 ConnectByUART: Open Diag Port N
         → 按 -d / --max-bytes 计时计流量（每 5s 打一行进度到 stderr）
         → PostMessage(WM_CLOSE) → 等进程退出（默认 120s）
         → 校验 .lst 有 Stop Logging、.logel 非空、缓存目录在
         → 写 <会话>\_lcapt.json（时长、DIAG 口、版本、警告）
         → 可选：logel2txt 导出 txt
```

失败当场给判据：没认出 DIAG 口会直接报错并列出排查方向，不会干等到超时。

## 已知限制

- **`Module` 列恒为空**：logel2txt 从 `traceview.dat/.pbs` 取的是已解码文本，缓存里没有模块名；模块名只存在于 Logel 现场解析的内存里。要模块列只能用 Logel GUI「Export To Text File」。
- **回放/CLI 导出可能被解析库卡住**：若 `.lst` 里 `Modem Version` 为空，且 ini 里配置的外部/内部 Parser 路径本机不可达，Logel 会弹隐藏模态框「No modem version found…」并挂住。本工具默认 playback 导出时会自动点掉该框，并容忍写完 `*_pb` 后的 `0xC0000005`；`--no-playback` 可退回读抓取缓存。
- 同一台机器同时只能跑一个抓取（`<profile>\.lcapt.lock`）；同事 GUI **正在连 DIAG** 时会抢口。只开 GUI、未抓取时通常不占 DIAG。
- **抓取期间可发 AT**（AT 口与 DIAG 分离；实测可行）。烧写 / `dloader` 软触发前请先 `lcapt stop` 并关掉正在抓的 Logel。
- **DIAG 掉线会自动停抓并释放端口**（约 3 秒无重连）：不会在空挂状态占口；口回来后请重新开一轮 `capture`（不自动续抓）。
- 工具报错弹窗仍可能卡住无人值守抓取（profile 已设 `ShowException=0`、`PlaySound=0`，不能 100% 免疫）。
- `MaxTime` / `MaxSize` 的单位官方未写死，本工具不依赖它们做分段；需要分段请用 `-d` 多次调用。
- 直接点控制台窗口 X 仍可能来不及收尾；优先 Ctrl+C 或 `lcapt stop`。

## 故障排查

| 现象 | 处理 |
|------|------|
| `SPRD DIAG port not detected` | 设备管理器确认有 `SPRD DIAG (COMxx)`；关掉占用该口的其它程序；确认 `[MS] AutoIdentifyPort=1` |
| `Open Diag Port fail` / 超时 | DIAG 口多半被其它 Logel / 串口工具占用；看报错里列出的残留 PID |
| `DIAG port N disconnected` | 抓取中设备拔线/口消失，已自动 `WM_CLOSE` 释放；插回后重新 `capture` |
| `doctor` 报 `other Logel processes` / capture 启动前 `[WARN]` | 已有 GUI 或残留 `Logel.exe`；先关掉再抓（软提示，不阻止启动） |
| Logel 提前退出 | 看 `<profile>\Bin\Log\Application.log` 末尾；多为版本/解析库不匹配 |
| `WM_CLOSE` 后仍不退出 | 大概率弹了模态框；人工结束该进程，`Application.log` 能看到卡在哪 |
| `.logel` 为空 | 设备侧未开 armlog：`AT+ARMLOG=1`（DSP log 用 `AT+SPDSPOP=2`） |
| 导出 0 行 | 会话里没有可用 `traceview` 缓存；确认抓取时 `.logel` 在增长；检查 logel2txt 是否可用 |
| 控制台中文乱码 | `set PYTHONUTF8=1` 或 `chcp 65001`（仅控制台；**不能**给 `--out` 开中文路径） |
| `--out` 含中文 / 超时无会话 | 改用纯 ASCII 输出目录，抓完再拷贝 |

## 自行打包 exe

官方包见 [Releases](https://github.com/zhengqingquan/logel-log-capture/releases)。若需本地重打：

```powershell
pip install pyinstaller
pyinstaller --onefile --name lcapt --clean --noconfirm lcapt.py
# 产物：dist\lcapt.exe
```

`build/`、`dist/`、`*.spec` 已在 `.gitignore` 中。

## 开发

```powershell
python -m pytest tests -q
python lcapt.py doctor --logel "<Logel>"
python lcapt.py capture -d 10 --logel "<Logel>"
```

模块分工：`logel.py`（安装发现 / ini / Application.log 解析 / 会话）、`profile.py`（无头 profile 镜像与 ini 补丁）、`win32.py`（窗口与 `WM_CLOSE`）、`capture.py`（生命周期与校验）、`export.py`（logel2txt 适配）、`cli.py`（argparse）。
