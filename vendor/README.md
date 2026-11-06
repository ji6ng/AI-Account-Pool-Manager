# 构建资源

源码仓库不提交 cli-proxy-api.exe。请从 [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI/releases) 获取兼容的 Windows x64 核心，命名为 vendor/cli-proxy-api.exe 后启动或构建。

v1.2.19 使用 v8.0.40 核心测试。其它版本需验证管理接口、OAuth 登录与每账号 model_aliases 的兼容性。

modeltrace/unified_bank.json 来自 ModelTrace，见 ../licenses/ModelTrace-LICENSE。


## macOS

运行 `bash tools/fetch_cli_proxy_api.sh`，自动按本机架构下载 v8.0.40 的 `darwin_aarch64`（Apple Silicon）或 `darwin_amd64`（Intel）压缩包，验证 SHA-256 后安装为 `vendor/cli-proxy-api`，设置执行权限并移除 quarantine。可通过 `VERSION` 环境变量覆盖版本。下载的二进制由 `.gitignore` 排除，不提交到仓库。
