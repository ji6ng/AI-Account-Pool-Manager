# Linux 说明：一个安装包，两种运行模式

下载 `AI号池管理器-linux-<arch>.tar.gz`，同一可执行文件兼顾桌面和服务器。
Linux 模式按以下优先级解析（Windows/macOS 仍使用原生行为）：

1. `--server` / `--desktop`（互斥），最高优先级。
2. `AIPOOL_MODE=server|desktop`；其他非空值会记录警告并忽略。
3. 自动检测：有 `DISPLAY` 或 `WAYLAND_DISPLAY` 则桌面模式，否则服务器模式。

启动日志记录 `Linux mode=... source=flag|env|auto`。`--no-browser` 可在桌面模式禁用浏览器。
应用菜单入口不强制模式，仍遵循上述优先级；桌面自启固定 `--desktop`，systemd 固定 `--server`。
设置页切换自启立即生效：启用任一模式会移除另一模式的用户自启文件，关闭会移除两者。
桌面模式失去显示环境时只输出管理地址，不会崩溃。服务器模式从不启动浏览器、对话框或文件管理器；
日志同步到 stdout，告警和无响应提示到 stderr。桌面无响应时提示使用系统监视器。
`--set-password` 与非持久化 `--host` 在全部平台、全部模式可用。

## 桌面模式

适用于 GNOME/KDE 等 Linux 图形桌面。下载匹配架构的
`AI号池管理器-linux-<arch>.tar.gz`，用 `tar -xzf` 完整解压；
运行 `AI号池管理器-linux/AI号池管理器/AI号池管理器`，不要拆开 `_internal`。
桌面模式自动打开默认浏览器；没有 DISPLAY/WAYLAND_DISPLAY 时仅输出并记录管理地址。
关闭浏览器不会退出后台程序，请用「设置 → 退出程序」退出。

### 应用菜单与开机启动

在解压目录运行 `bash install-desktop.sh`，可选安装应用菜单入口和图标到
`${XDG_DATA_HOME:-~/.local/share}/applications` 与 `icons/hicolor/128x128/apps`。
不需要 sudo；移动程序目录后重新运行安装脚本。运行
`bash install-desktop.sh --uninstall` 仅移除菜单入口和图标，不删除程序或数据。

「设置 → 开机自动启动」为打包程序生成
`${XDG_CONFIG_HOME:-~/.config}/autostart/aipool-manager.desktop`，在桌面登录时启动。
源码运行不会添加自启，关闭选项始终可移除已有条目。移动程序前应关闭自启，
再在新位置启动并开启。菜单卸载与开机自启互相独立。

### 数据与代理

打包后优先使用可执行文件旁的 `data/`，不可写时回退到
`${XDG_DATA_HOME:-~/.local/share}/AIPool`。可用 `--data-dir /完整路径` 指定目录。
设置页显示实际数据目录；没有桌面或 xdg-open 时「打开数据目录」只显示路径。
升级前退出并备份实际数据目录，不要覆盖账号或设置文件。

在「设置」中填写出站代理地址（例如 `http://127.0.0.1:7890`），
不要把网关 API 地址当作出站代理；端口以本机代理软件配置为准。

### 构建与兼容性

使用 Linux Python 创建 venv，安装 `requirements.txt`，执行
`bash tools/fetch_cli_proxy_api.sh`，再执行 `python build.py`。
输出 `dist/AI号池管理器-linux/` 和保留执行权限、符号链接的 tar.gz。
默认下载 CLIProxyAPI v8.0.40 并校验 SHA-256；可通过 VERSION 和 DEST 覆盖版本与目标。
二进制只支持 glibc 不低于构建机的系统，并可能受内置代理自身的 glibc 要求限制；
不适用于 musl/Alpine。建议在计划支持的最旧 glibc 系统上构建，分架构发布。


## 服务器模式（无桌面 / VPS / SSH）

服务器模式在 Linux 上不打开浏览器或系统对话框。管理界面由远程浏览器访问，终端会打印实际 URL、监听地址、数据目录和号池端口。

### 解压、设置密码、启动

使用与服务器架构对应的 `AI号池管理器-linux-<arch>.tar.gz`：

```bash
tar -xzf AI号池管理器-linux-x86_64.tar.gz
cd AI号池管理器-linux
chmod 700 AI号池管理器
umask 077
./AI号池管理器/AI号池管理器 --data-dir "$HOME/aipool-data" --set-password
./AI号池管理器/AI号池管理器 --data-dir "$HOME/aipool-data" --server --host 0.0.0.0
```

`--set-password` 隐藏输入并保存后退出，也可从 `AIPOOL_ADMIN_PASSWORD` 环境变量读取；不要把密码直接写入 shell 历史。空密码会被拒绝。与网页设置使用同一存储方式，当前为 settings.json 中的明文，务必限制数据目录权限。若已有实例正在运行，修改后重启该实例。

`--host` 只覆盖本次运行的监听地址（包括内部号池），不会写入 settings.json；CLI 覆盖有效时，网页修改监听地址不会持久化。`--port 8320` 指定管理端口；端口被占用时可能自动调整，实际地址以启动输出为准。内部号池端口在设置中配置，默认 8318，管理端口默认 8320。使用 Ctrl+C 或 SIGTERM 停止；号池子进程和单实例锁会一起清理。失去响应时，按终端提示执行 `kill <pid>`，确认退出后再启动。

### 远程管理与 SSH 隧道

未设置管理密码时，管理 API 只允许本机请求，不会因为绑定 0.0.0.0 而放宽权限。建议始终绑定 127.0.0.1，通过 SSH 管理：

```bash
ssh -N -L 8320:127.0.0.1:8320 user@server
# 本机浏览器打开 http://127.0.0.1:8320/
```

若实际管理端口不是 8320，修改隧道目标端口。直接远程访问需要先设置管理密码，再监听 `0.0.0.0`（或指定网卡地址），浏览器通过服务器地址登录。管理静态页面可能可加载，但未认证的远程管理 API 被拒绝。公网部署应使用可信 TLS 反向代理、VPN 或 SSH 隧道；HTTP 密码和会话不能安全地穿越不可信网络。不要把不可信反向代理接到本机免登录入口，否则代理后的请求可能被视为本机。

防火墙和云安全组仅向可信地址开放管理/客户端端口；内部号池端口不要向外开放。设置管理密码不等于开放网络端口，且客户端 API 仍需独立客户端 Key。SSH 隧道模式不需要对外开放管理端口。

### systemd 用户服务

打包程序中的「设置 → 开机自动启动」会写入 `${XDG_CONFIG_HOME:-~/.config}/systemd/user/aipool-manager.service`，调用 `systemctl --user daemon-reload` 和 enable/disable。仅打包程序允许启用，源码运行不生成服务。失败会记录日志；无 systemd 的容器中仍可生成 unit，但不会自动启动。关闭开关会删除 unit，不会停止正在运行的实例。

生成的服务使用当前打包可执行文件和 `--server`，不继承临时 `--host`、`--port`、`--data-dir` 参数。以 `--data-dir` 启动不会在启动阶段改动自启配置；如手动切换网页开关，仍会更新用户服务。使用自定义数据目录时请自行维护 unit 的 ExecStart，保证设密码和启动使用同一目录。

```bash
systemctl --user daemon-reload
systemctl --user enable aipool-manager.service
# 先退出手动运行的实例，再启动服务
systemctl --user start aipool-manager.service
journalctl --user -u aipool-manager.service -f
systemctl --user stop aipool-manager.service
systemctl --user disable aipool-manager.service
# 由有权限的管理员启用：无需登录即可在开机时启动用户服务
sudo loginctl enable-linger <user>
```

### systemd 系统服务（管理员安装）

包中 `packaging/linux/aipool-manager.service` 是系统服务模板。管理员需先创建专用非 root 用户 `aipool`、安装到 `/opt/aipool/AI号池管理器-linux` 并创建归该用户所有的 `/var/lib/aipool`（权限 700）。根据实际位置编辑 User、WorkingDirectory 和 ExecStart；模板默认仅监听回环地址。由同一服务用户执行 `--data-dir /var/lib/aipool --set-password` 后，再部署：

```bash
sudo cp packaging/linux/aipool-manager.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now aipool-manager.service
sudo journalctl -u aipool-manager.service -f
# 升级前先停止
sudo systemctl stop aipool-manager.service
```

不要同时启用用户服务和系统服务。无需以 root 运行程序；系统服务安装步骤才需要管理员权限。

### 数据、日志、代理与兼容性

- 打包 Linux 默认使用可执行文件所在目录的 `data/`；不可写时回退到 `${XDG_DATA_HOME:-~/.local/share}/AIPool`。`--data-dir` 优先。备份整个数据目录，升级时保留它，勿上传账号凭据、Key 或设置文件。
- 日志位于 `data/logs/app.log` 与 `data/logs/pool.log`（使用自定义目录时相应替换）；程序 `_log` 同时输出 stdout，systemd 可用 journalctl 查看。网页「打开数据目录」只显示服务器路径，不会启动 xdg-open。
- 通过管理页面的上游代理设置填写 `http://127.0.0.1:7894` 等 URL。该地址指服务器上的代理，不是远程浏览器电脑上的代理；不要假定桌面代理环境存在。
- 发布包需要 glibc Linux，不能在 Alpine/musl 上直接运行。glibc 版本必须不低于构建机器；例如 Ubuntu 22.04/glibc 2.35 构建的包需要 glibc ≥ 2.35。架构必须匹配。旧系统应在兼容的构建机器上重新构建。

### 从源码构建

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
bash tools/fetch_cli_proxy_api.sh
.venv/bin/python main.py --server --data-dir "$HOME/aipool-data" --host 127.0.0.1
.venv/bin/python build.py
```

下载脚本验证 CLIProxyAPI 校验和，支持 amd64/aarch64；`VERSION` 指定版本，`DEST` 可指定二进制下载路径。构建要求二进制位于 vendor/cli-proxy-api。输出为 `dist/AI号池管理器-linux/`（包含 `AI号池管理器/` 可执行文件及 `_internal`、文档、许可证与服务模板）和保留权限/符号链接的 tar.gz。`build.py --no-zip` 仅生成目录，`--zip <路径.tar.gz>` 自定义归档路径。
