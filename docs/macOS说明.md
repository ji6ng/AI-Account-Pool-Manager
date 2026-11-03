# macOS 使用说明

支持 macOS 11 及更新版本；Apple Silicon 与 Intel 应分别使用对应架构构建的应用。

1. 解压后将「AI号池管理器.app」拖入「应用程序」，双击启动。
2. 浏览器自动打开本机管理界面，默认 http://127.0.0.1:8320；实际端口以页面或日志为准。
3. 首次打开若 Gatekeeper 阻止，确认来源可信后右键选择「打开」，或在终端执行 `xattr -dr com.apple.quarantine "/Applications/AI号池管理器.app"`。应用使用临时签名，未经 Apple 公证。
4. 应用在后台运行，没有 Dock 图标；再次打开应用只会打开已有的管理页面。关闭浏览器不会退出服务，请使用「设置 → 退出程序」。进程无响应时，在「活动监视器」按提示的进程号结束进程。

数据在 `~/Library/Application Support/AIPool`，日志在其 `logs/app.log`。账号凭据在其 `pool/auths`，等同于密码，请勿外发。升级或迁移电脑时单独备份并迁移整个数据目录，替换应用不会迁移数据。不要在 `.app` 内添加或修改数据，也无需寻找 Windows 的 `_internal` 文件夹。

开发运行使用仓库 `data/`；`--data-dir` 可以指定独立目录。构建方法见项目 README。开启登录启动时使用 `~/Library/LaunchAgents/com.aipool.manager.plist`，可在系统设置里关闭。
