# Linux 服务器版（无桌面 / VPS / SSH）

本版本在 Linux 上不打开浏览器或系统对话框。管理界面由远程浏览器访问，终端会打印实际 URL、监听地址、数据目录和号池端口。

## 解压、设置密码、启动

使用与服务器架构对应的 `AI号池管理器-linux-server-<arch>.tar.gz`：

```bash
tar -xzf AI号池管理器-linux-server-x86_64.tar.gz
cd AI号池管理器-linux
chmod 700 AI号池管理器
umask 077
./AI号池管理器/AI号池管理器 --data-dir "$HOME/aipool-data" --set-password
./AI号池管理器/AI号池管理器 --data-dir "$HOME/aipool-data" --no-browser --host 0.0.0.0
```

`--set-password` 隐藏输入并保存后退出，也可从 `AIPOOL_ADMIN_PASSWORD` 环境变量读取；不要把密码直接写入 shell 历史。空密码会被拒绝。与网页设置使用同一存储方式，当前为 settings.json 中的明文，务必限制数据目录权限。若已有实例正在运行，修改后重启该实例。

`--host` 只覆盖本次运行的监听地址（包括内部号池），不会写入 settings.json；CLI 覆盖有效时，网页修改监听地址不会持久化。`--port 8320` 指定管理端口；端口被占用时可能自动调整，实际地址以启动输出为准。内部号池端口在设置中配置，默认 8318，管理端口默认 8320。使用 Ctrl+C 或 SIGTERM 停止；号池子进程和单实例锁会一起清理。失去响应时，按终端提示执行 `kill <pid>`，确认退出后再启动。

## 远程管理与 SSH 隧道

未设置管理密码时，管理 API 只允许本机请求，不会因为绑定 0.0.0.0 而放宽权限。建议始终绑定 127.0.0.1，通过 SSH 管理：

```bash
ssh -N -L 8320:127.0.0.1:8320 user@server
# 本机浏览器打开 http://127.0.0.1:8320/
```

若实际管理端口不是 8320，修改隧道目标端口。直接远程访问需要先设置管理密码，再监听 `0.0.0.0`（或指定网卡地址），浏览器通过服务器地址登录。管理静态页面可能可加载，但未认证的远程管理 API 被拒绝。公网部署应使用可信 TLS 反向代理、VPN 或 SSH 隧道；HTTP 密码和会话不能安全地穿越不可信网络。不要把不可信反向代理接到本机免登录入口，否则代理后的请求可能被视为本机。

防火墙和云安全组仅向可信地址开放管理/客户端端口；内部号池端口不要向外开放。设置管理密码不等于开放网络端口，且客户端 API 仍需独立客户端 Key。SSH 隧道模式不需要对外开放管理端口。

## systemd 用户服务

打包程序中的「设置 → 开机自动启动」会写入 `${XDG_CONFIG_HOME:-~/.config}/systemd/user/aipool-manager.service`，调用 `systemctl --user daemon-reload` 和 enable/disable。仅打包程序允许启用，源码运行不生成服务。失败会记录日志；无 systemd 的容器中仍可生成 unit，但不会自动启动。关闭开关会删除 unit，不会停止正在运行的实例。

生成的服务使用当前打包可执行文件和 `--no-browser`，不继承临时 `--host`、`--port`、`--data-dir` 参数。以 `--data-dir` 启动不会在启动阶段改动自启配置；如手动切换网页开关，仍会更新用户服务。使用自定义数据目录时请自行维护 unit 的 ExecStart，保证设密码和启动使用同一目录。

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

## systemd 系统服务（管理员安装）

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

## 数据、日志、代理与兼容性

- 打包 Linux 默认使用可执行文件所在目录的 `data/`；不可写时回退到 `${XDG_DATA_HOME:-~/.local/share}/AIPool`。`--data-dir` 优先。备份整个数据目录，升级时保留它，勿上传账号凭据、Key 或设置文件。
- 日志位于 `data/logs/app.log` 与 `data/logs/pool.log`（使用自定义目录时相应替换）；程序 `_log` 同时输出 stdout，systemd 可用 journalctl 查看。网页「打开数据目录」只显示服务器路径，不会启动 xdg-open。
- 通过管理页面的上游代理设置填写 `http://127.0.0.1:7894` 等 URL。该地址指服务器上的代理，不是远程浏览器电脑上的代理；不要假定桌面代理环境存在。
- 发布包需要 glibc Linux，不能在 Alpine/musl 上直接运行。glibc 版本必须不低于构建机器；例如 Ubuntu 22.04/glibc 2.35 构建的包需要 glibc ≥ 2.35。架构必须匹配。旧系统应在兼容的构建机器上重新构建。

## 从源码构建

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
bash tools/fetch_cli_proxy_api.sh
.venv/bin/python main.py --no-browser --data-dir "$HOME/aipool-data" --host 127.0.0.1
.venv/bin/python build.py
```

下载脚本验证 CLIProxyAPI 校验和，支持 amd64/aarch64；`VERSION` 指定版本，`DEST` 可指定二进制下载路径。构建要求二进制位于 vendor/cli-proxy-api。输出为 `dist/AI号池管理器-linux/`（包含 `AI号池管理器/` 可执行文件及 `_internal`、文档、许可证与服务模板）和保留权限/符号链接的 tar.gz。`build.py --no-zip` 仅生成目录，`--zip <路径.tar.gz>` 自定义归档路径。
