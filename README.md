# AI 号池管理器

在 Windows / macOS 上管理 ChatGPT / Gemini 账号、第三方提供商与客户端 Key，通过本地网关提供统一模型目录及 API。当前版本：**v1.2.18**。

本仓库包含完整 Python 后端、网页前端、构建脚本及离线测试。Windows 用户可直接下载绿色包，无需安装 Python。

## 下载与使用

1. 从 [Releases](https://github.com/ji6ng/AI-Account-Pool-Manager/releases) 下载 windows-x64-nocredentials.zip，完整解压。
2. 双击 AI号池管理器.exe，保留旁边的 _internal 文件夹。默认管理地址为 http://127.0.0.1:8320，端口占用时自动调整。
3. 添加自己的账号或提供商，在客户端密钥页选择允许调用的模型并复制接入配置。实际地址以页面为准。

关闭浏览器后服务仍会运行，在系统设置中退出。升级时先退出程序，备份并保留已有 data/，仅替换程序文件。

## 功能

- 管理 Plus、Pro、Gemini 账号及提供商，查看额度、重置倒计时和请求记录。
- 支持 OpenAI Responses / Chat Completions、Anthropic Messages 与 Gemini 接口，可用模型取决于上游账号和授权。
- 客户端 Key 按模型分组授权，同时控制调用权限和目录，提供 Codex 与 Claude 接入模板。
- Plus 支持账号顺序，优先使用有 5 小时窗口的账号，确认耗尽后进入后备组；恢复后新请求返回优先组。
- Plus 全部确认限额后，可按模型使用指定代理商模型；Key 必须同时授权原模型和备用模型。
- 普通网络错误、5xx 和未知额度不会触发账号优先级切换。

## 开发与构建

建议 Windows x64、Python 3.13：

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
# 按 vendor/README.md 准备 vendor/cli-proxy-api.exe
.\.venv\Scripts\python.exe main.py
.\.venv\Scripts\python.exe build.py
```

产物位于 dist/AI号池管理器。core/ 为后端，web/ 为前端，tools/ 为测试，docs/ 为说明。内部代理为独立第三方进程。

```powershell
.\.venv\Scripts\python.exe tools/test_regressions.py
.\.venv\Scripts\python.exe tools/test_plus_fallback.py
.\.venv\Scripts\python.exe tools/test_plus_priority.py
cd tools/ui-tests
npm ci
npm test
```

离线测试使用临时数据和模拟上游，不需账号凭据；前端测试需使用支持 JSDOM 30 的 Node.js。

## macOS

macOS 11 或更新版本，使用与本机架构一致的 Python（Apple Silicon 用 arm64，Intel 用 x86_64）：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
bash tools/fetch_cli_proxy_api.sh
.venv/bin/python main.py
# 构建 .app 和保留权限、符号链接的 zip
.venv/bin/python build.py
```

下载脚本默认获取 CLIProxyAPI v8.0.40，并验证发布校验和；可用 `VERSION=v8.0.40 bash tools/fetch_cli_proxy_api.sh` 指定版本。
构建产物为 `dist/AI号池管理器-macOS/AI号池管理器.app`，旁边包含说明与许可证；压缩包为 `dist/AI号池管理器-macos-<arch>.zip`。可将 `.app` 拖入「应用程序」后打开。

应用仅做临时签名，未经 Apple 公证。首次打开若被 Gatekeeper 拦截，确认来源可信后右键应用选择「打开」；或对实际安装路径执行：

```bash
xattr -dr com.apple.quarantine "/Applications/AI号池管理器.app"
```

打包应用的数据位于 `~/Library/Application Support/AIPool`，升级前备份此目录；开发运行仍使用仓库 `data/`，也可用 `--data-dir` 指定目录。应用在后台运行，没有 Dock 图标；再次打开应用只会打开已有的管理页面。关闭浏览器后服务继续运行，退出请用「设置 → 退出程序」。更多说明见 [macOS说明](docs/macOS说明.md)。

## Linux 服务器版

Linux 无桌面服务器/VPS 使用 `AI号池管理器-linux-server-<arch>.tar.gz`，解压后运行：

```bash
cd AI号池管理器-linux
./AI号池管理器/AI号池管理器 --set-password
./AI号池管理器/AI号池管理器 --no-browser --host 127.0.0.1
# 本地电脑上建立隧道后浏览 http://127.0.0.1:8320/
ssh -N -L 8320:127.0.0.1:8320 user@server
```

Linux 从不自动打开浏览器/对话框，启动地址和日志输出到终端。远程直接管理必须先设置管理密码，且需监听可访问地址；未设密码仅允许本机管理。`--set-password` 支持隐藏输入或 `AIPOOL_ADMIN_PASSWORD`，`--host` 仅本次生效，`--data-dir` 指定数据目录。支持 systemd 用户服务自启，开机免登录需管理员设置 `loginctl enable-linger <user>`；系统服务模板随包提供。

在 Linux 上运行 `bash tools/fetch_cli_proxy_api.sh` 后，`python build.py` 生成 `dist/AI号池管理器-linux/` 和 tar.gz。发布包要求 glibc ≥ 构建机器版本，不支持直接在 musl/Alpine 上运行。详细安装、数据/日志、代理、systemd 和安全说明见 [Linux服务器版说明](docs/Linux服务器版说明.md)。

## 使用限制与数据

额度默认约每 30 秒检查，切回发生在后续新请求，已输出回答不会重放。首次启用账号专属路由或新增账号后需重新启动一次号池。当前账号专属路由与备用通道均拒绝 previous_response_id，客户端需发送完整历史或开启新会话。未知额度会保留优先账号，可能暂时阻止使用后备组。

data/ 包含账号凭据、客户端 Key、提供商 Key 和日志，请自行备份且不要上传。发布包不含 data/、seed/ 或预置账号。程序未签名；管理密码留空时仅允许本机管理，远程管理需设置密码。

详情见 [使用说明](docs/使用说明.md) 和 [快速开始](docs/先看这里.txt)。

## 许可

本项目原创部分使用 [MIT](LICENSE)。第三方组件与参考项目见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

