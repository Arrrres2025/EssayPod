# EssayPod macOS 应用

这个目录包含 EssayPod 的轻量 macOS 桌面窗口壳。生成的 `EssayPod.app`
会连接当前 EssayPod 源码目录，不会复制体积较大的 Python 环境、模型和历史任务。

## 构建

```bash
cd EssayPod
./desktop/build-macos-app.sh
```

输出位置：

```text
EssayPod/dist/EssayPod.app
```

## 使用方式

双击 `EssayPod.app` 后，应用会：

1. 启动 FastAPI 后端；
2. 重新构建 Web UI，以包含最新的前端源码；
3. 启动 Web UI 服务；
4. 在独立的 macOS WebKit 窗口中打开 EssayPod；
5. 退出应用时关闭由它启动的后台服务。

应用日志保存在：

```text
~/Library/Logs/EssayPod/
```

## 后续更新

- 修改 Python 后端后，退出并重新打开应用即可生效。
- 修改 React 前端后，退出并重新打开应用；启动过程会自动重新构建界面。
- 修改 `desktop/` 中的窗口壳代码后，重新运行打包脚本。
- 项目目录移动后，需要重新运行打包脚本，让应用记录新的源码位置。

当前版本面向本机开发使用。它采用本机临时签名，可以复制到这台 Mac 的
“应用程序”目录；若要分发给其他用户，还需要独立打包运行环境、Developer ID
签名和 Apple 公证。
