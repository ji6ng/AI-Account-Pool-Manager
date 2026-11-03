# Linux 桌面版

适用于 GNOME/KDE 等 Linux 图形桌面。下载匹配架构的
`AI号池管理器-linux-gui-<arch>.tar.gz`，用 `tar -xzf` 完整解压；
运行 `AI号池管理器-linux/AI号池管理器/AI号池管理器`，不要拆开 `_internal`。
程序自动打开默认浏览器；没有 DISPLAY/WAYLAND_DISPLAY 时仅输出并记录管理地址。
关闭浏览器不会退出后台程序，请用「设置 → 退出程序」退出。

## 应用菜单与开机启动

在解压目录运行 `bash install-desktop.sh`，可选安装应用菜单入口和图标到
`${XDG_DATA_HOME:-~/.local/share}/applications` 与 `icons/hicolor/128x128/apps`。
不需要 sudo；移动程序目录后重新运行安装脚本。运行
`bash install-desktop.sh --uninstall` 仅移除菜单入口和图标，不删除程序或数据。

「设置 → 开机自动启动」为打包程序生成
`${XDG_CONFIG_HOME:-~/.config}/autostart/aipool-manager.desktop`，在桌面登录时启动。
源码运行不会添加自启，关闭选项始终可移除已有条目。移动程序前应关闭自启，
再在新位置启动并开启。菜单卸载与开机自启互相独立。

## 数据与代理

打包后优先使用可执行文件旁的 `data/`，不可写时回退到
`${XDG_DATA_HOME:-~/.local/share}/AIPool`。可用 `--data-dir /完整路径` 指定目录。
设置页显示实际数据目录；没有桌面或 xdg-open 时「打开数据目录」只显示路径。
升级前退出并备份实际数据目录，不要覆盖账号或设置文件。

在「设置」中填写出站代理地址（例如 `http://127.0.0.1:7890`），
不要把网关 API 地址当作出站代理；端口以本机代理软件配置为准。

## 构建与兼容性

使用 Linux Python 创建 venv，安装 `requirements.txt`，执行
`bash tools/fetch_cli_proxy_api.sh`，再执行 `python build.py`。
输出 `dist/AI号池管理器-linux/` 和保留执行权限、符号链接的 tar.gz。
默认下载 CLIProxyAPI v8.0.40 并校验 SHA-256；可通过 VERSION 和 DEST 覆盖版本与目标。
二进制只支持 glibc 不低于构建机的系统，并可能受内置代理自身的 glibc 要求限制；
不适用于 musl/Alpine。建议在计划支持的最旧 glibc 系统上构建，分架构发布。
