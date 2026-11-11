# AI 号池管理器

在 Windows / macOS / Linux 上管理 ChatGPT / Gemini 账号、第三方提供商与客户端 Key，通过本地网关提供统一模型目录及 API。当前源码版本：**v1.2.19**。

本仓库包含完整 Python 后端、网页前端、构建脚本及离线测试。Windows 用户可直接下载绿色包，无需安装 Python。

## 下载与使用

> **Mac 用户**请看下方 [macOS → 下载与安装](#下载与安装普通用户)，Linux 用户请看 [Linux](#linux桌面与服务器模式)。以下为 Windows 步骤。

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
- 可为选定的 Plus / Pro 账号建立多个额度激活任务，选择每日重复或一次性执行。
- 上游明确返回额度 429 时，刷新额度后最多重试一次到下一个可用账号或已配置的备用模型。

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

### 下载与安装（普通用户）

不需要安装 Python 或任何开发工具，按下面几步操作即可。

**0. 确认电脑型号**

目前只提供 Apple 芯片版本。点屏幕左上角的苹果图标  →「关于本机」，「芯片」一栏显示
**Apple M1 / M2 / M3 / M4…** 即可使用；如果显示的是 Intel，暂时请从源码运行（见下文）。
系统需要 macOS 11 或更新版本。

**1. 下载**

1. 打开 [Releases 页面](https://github.com/ji6ng/AI-Account-Pool-Manager/releases)。
2. 从上往下找到第一个带有 `macos-arm64.zip` 文件的版本（最新版本有时只更新了 Windows 包，往下翻一个即可）。
3. 展开该版本下方的「Assets」，点击 `AI-Cliproxy-API-版本号-macos-arm64.zip` 下载。
   同名的 `.sha256` 文件是校验值，普通用户不用下载。

**2. 安装**

1. 在「访达」→「下载」里双击下载好的 zip，系统会自动解压出一个 `AI号池管理器-macOS` 文件夹。
2. 打开这个文件夹，把里面的 **`AI号池管理器.app`** 拖到左侧栏的「应用程序」里。
3. 文件夹里的其它文件是说明书和许可证，可以留着参考，也可以删掉。

**3. 第一次打开**

本应用没有经过 Apple 公证，第一次打开时 macOS 会提示「无法验证开发者」或「Apple 无法检查其是否包含恶意软件」，
这是正常现象，按你的系统版本选一种方法放行（只需操作一次）：

- **macOS 15 Sequoia 及更新版本**：先双击打开一次，在弹窗中点「完成」；然后打开「系统设置」→「隐私与安全性」，
  滚动到底部「安全性」一栏，在「已阻止使用 AI号池管理器」旁边点 **「仍要打开」**，输入开机密码确认，再点「仍要打开」。
- **macOS 14 及更早版本**：在「应用程序」里 **按住 Control 点击**（或右键）`AI号池管理器`，选「打开」，在弹窗中再点「打开」。
- 以上方法都不行时，可打开「终端」，粘贴下面这行并回车（可能要求输入开机密码），然后正常双击打开：

  ```bash
  xattr -dr com.apple.quarantine "/Applications/AI号池管理器.app"
  ```

**4. 开始使用**

- 打开后会自动在浏览器里显示管理页面，地址一般是 http://127.0.0.1:8320 （端口被占用时会自动换一个，以浏览器打开的为准）。
- 应用在后台运行，**没有 Dock 图标，也没有窗口**，这是正常的。关掉浏览器不会退出；想再打开管理页面，再双击一次应用即可。
- 访问 ChatGPT 账号一般需要代理：在「系统设置 → 出网代理」填写你的代理地址（例如 Clash 默认是 `http://127.0.0.1:7890`，以代理软件里显示的端口为准），点「测试」通过后保存。从「应用程序」打开的程序不会读取终端里设置的代理。
- 账号、Key 等配置方法与 Windows 版相同，见 [使用说明](docs/使用说明.md)。
- **退出**：管理页面左侧「系统设置」→「退出程序」。

**5. 升级与卸载**

- **升级**：先在「系统设置」→「退出程序」，再按第 1、2 步下载新版本，把新的 `AI号池管理器.app` 拖进「应用程序」并选择「替换」。
  账号和设置保存在 `~/Library/Application Support/AIPool`，替换应用不会丢失；新版本第一次打开时可能需要再按第 3 步放行一次。
- **卸载**：先退出程序，把「应用程序」里的 `AI号池管理器` 拖到废纸篓。如果还想删除账号凭据和设置，在访达中按
  `Command + Shift + G`，输入 `~/Library/Application Support/AIPool` 删除该文件夹；开启过「开机自动启动」的，
  再删除 `~/Library/LaunchAgents/com.aipool.manager.plist`。

### 从源码运行与构建（开发者 / Intel Mac）

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

应用仅做临时签名，未经 Apple 公证，首次打开的放行方法同上文第 3 步。本机构建的应用没有下载隔离标记，通常可以直接打开。

打包应用的数据位于 `~/Library/Application Support/AIPool`，升级前备份此目录；开发运行仍使用仓库 `data/`，也可用 `--data-dir` 指定目录。应用在后台运行，没有 Dock 图标；再次打开应用只会打开已有的管理页面。关闭浏览器后服务继续运行，退出请用「设置 → 退出程序」。更多说明见 [macOS说明](docs/macOS说明.md)。

## Linux：桌面与服务器模式

统一下载 `AI号池管理器-linux-<arch>.tar.gz`，完整解压后运行
`AI号池管理器-linux/AI号池管理器/AI号池管理器`。
Linux 优先使用互斥的 `--desktop` / `--server`，其次使用 `AIPOOL_MODE=desktop|server`，
最后自动检测：有 DISPLAY/WAYLAND_DISPLAY 则桌面模式，否则服务器模式。无效环境值会警告并忽略。
桌面模式打开浏览器并支持应用菜单安装（包内 `bash install-desktop.sh`；`--uninstall` 卸载），
服务器模式不启动浏览器/对话框，打印 URL 和终端日志。桌面失去显示环境时只打印 URL。

```bash
cd AI号池管理器-linux
./AI号池管理器/AI号池管理器 --set-password
./AI号池管理器/AI号池管理器 --server --host 127.0.0.1
# 在本地电脑建立隧道，再浏览 http://127.0.0.1:8320/
ssh -N -L 8320:127.0.0.1:8320 user@server
```

`--set-password` 支持隐藏输入或 `AIPOOL_ADMIN_PASSWORD`；`--host` 仅本次有效。
未设密码仅允许本机管理。设置页自启立即生效：桌面使用 XDG 条目，服务器使用 systemd 用户服务，
模式切换会移除另一条目。系统服务模板随包提供；开机免登录需管理员启用 linger。
Linux 构建先执行 `bash tools/fetch_cli_proxy_api.sh` 再执行 `python build.py`，
生成 `dist/AI号池管理器-linux/` 和单一 tar.gz。需要 glibc ≥ 构建机，不适用于 musl/Alpine。
密码、远程访问、防火墙、systemd、数据目录、代理及构建细节见 [Linux说明](docs/Linux说明.md)。

## 使用限制与数据

额度默认约每 30 秒检查，切回发生在后续新请求，已输出回答不会重放。首次启用账号专属路由或新增账号后需重新启动一次号池。当前账号专属路由与备用通道均拒绝 previous_response_id，客户端需发送完整历史或开启新会话。未知额度会保留优先账号，可能暂时阻止使用后备组。

data/ 包含账号凭据、客户端 Key、提供商 Key 和日志，请自行备份且不要上传。发布包不含 data/、seed/ 或预置账号。程序未签名；管理密码留空时仅允许本机管理，远程管理需设置密码。

详情见 [使用说明](docs/使用说明.md) 和 [快速开始](docs/先看这里.txt)。

## 许可

本项目原创部分使用 [MIT](LICENSE)。第三方组件与参考项目见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

