# EssayPod macOS App 技术开发记录

> 文档日期：2026-07-29  
> 当前版本：本机开发版（Local Development App）  
> 目标平台：Apple Silicon Mac，macOS 13 及以上  
> App Bundle ID：`com.essaypod.desktop`

## 1. 文档目的

本文记录如何在不重写 EssayPod 现有 Python 后端和 React Web UI 的前提下，
将项目封装为可从 macOS“应用程序”目录双击启动、带独立窗口和 Dock 图标的
`EssayPod.app`。

文档包含：

- 技术方案和选型依据；
- macOS App 的目录结构；
- 桌面窗口、前后端服务和进程生命周期的实现；
- App 图标、签名、安装与更新方式；
- 开发过程中实际遇到的问题、根因和解决方案；
- 当前方案的限制和未来产品化建议。

## 2. 原项目结构

EssayPod 原本由两个独立服务组成：

```text
EssayPod/
├── app.py              # FastAPI 后端
├── tasks/              # 论文、音频、翻译、视频等任务数据
├── static/
├── translation_block/
├── video_generator/
└── web_ui/             # React 19 + vinext/Vite Web UI
```

本地开发时需要分别运行：

```bash
# 后端
cd EssayPod
../.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8010

# 前端
cd EssayPod/web_ui
npm run dev
```

用户还需要手动打开浏览器访问 Web UI。桌面化的核心目标，是把上述操作放进
一个原生 macOS 应用的生命周期中。

## 3. 技术方案选择

### 3.1 评估过的方案

最初评估了以下几类桌面封装方案：

| 方案 | 优点 | 不足 |
|---|---|---|
| Tauri | 体积小，原生感强，支持自动更新 | 本机没有可用 Rust 工具链；仍需处理 Python sidecar |
| Electron | 生态成熟，Node 集成方便 | 应用体积和内存占用较大；还需额外打包 Python |
| PyWebView | Python 集成直接，开发较快 | 产品化、窗口控制和后续分发能力相对有限 |
| Swift/AppKit | 原生窗口体验好 | 本机 Swift 工具链存在版本问题 |
| Objective-C/AppKit | 原生、无需第三方依赖，可直接使用系统 Clang | 代码比 Swift 冗长 |

### 3.2 最终方案

最终采用：

```text
Objective-C + AppKit + WKWebView + NSTask
```

具体分工：

- AppKit 创建真正的 macOS 应用和独立窗口；
- WKWebView 在窗口中加载现有 React Web UI；
- NSTask 启动和管理 FastAPI、前端构建及前端生产服务；
- Objective-C 只负责桌面壳，不重写业务功能；
- 原有 Web UI、FastAPI API、任务数据结构均保持不变。

### 3.3 为什么当前版本没有把整个项目嵌入 `.app`

开发时项目体积约为：

```text
EssayPod/                  约 6.3 GB
.venv/                     约 2.2 GB
web_ui/node_modules/       约 789 MB
```

如果直接复制进 `.app`：

- 每次修改都需要重新复制数 GB 内容；
- 构建和安装速度会明显下降；
- 现有 `tasks/` 数据可能被错误地当成应用资源覆盖；
- 更新 App 时更容易误删或替换用户数据。

因此当前版本采用“轻量桌面壳 + 当前源码目录”的模式：

```text
/Applications/EssayPod.app
            │
            ├── 原生窗口和启动器
            │
            └── 指向开发目录中的 EssayPod 源码、.venv 和 node_modules
```

这非常适合当前持续开发阶段。后端代码修改后重启 App 即可生效；前端代码会在
App 启动时重新构建。

## 4. 新增文件

桌面化相关文件位于：

```text
EssayPod/desktop/
├── main.m                  # 原生窗口、服务启动和进程生命周期
├── AppIcon.m               # 生成 AppIcon.icns
├── Info.plist              # macOS App Bundle 元数据
├── build-macos-app.sh      # 可重复执行的打包脚本
└── README.md               # 简明使用说明
```

构建产物位于：

```text
EssayPod/dist/
├── EssayPod.app
└── EssayPod.previous.app   # 最近一次构建的备份
```

## 5. `.app` Bundle 结构

打包脚本生成标准 macOS App Bundle：

```text
EssayPod.app/
└── Contents/
    ├── Info.plist
    ├── MacOS/
    │   └── EssayPod
    └── Resources/
        └── AppIcon.icns
```

其中：

- `Contents/MacOS/EssayPod` 是 Clang 编译出的 arm64 原生可执行文件；
- `Info.plist` 定义应用名称、Bundle ID、最低系统版本和源码目录；
- `AppIcon.icns` 包含 16～1024 像素的多尺寸图标。

## 6. App 启动流程

### 6.1 总体时序

```text
用户双击 EssayPod.app
        │
        ▼
创建 NSApplication、菜单和 NSWindow
        │
        ▼
WKWebView 显示“正在启动 EssayPod”
        │
        ▼
解析 EssayPod 源码目录
        │
        ▼
检查/启动 FastAPI（127.0.0.1:8010）
        │
        ▼
npm run build 构建最新 Web UI
        │
        ▼
npm run start 启动前端（127.0.0.1:3000）
        │
        ▼
再次确认前后端健康状态
        │
        ▼
WKWebView 加载 http://127.0.0.1:3000/
```

### 6.2 源码目录解析

App 按以下优先级定位 EssayPod：

1. 环境变量 `ESSAYPOD_PROJECT_ROOT`；
2. `Info.plist` 中的 `EssayPodProjectRoot`；
3. 根据 `.app` 所在位置推断。

打包时，脚本通过 `/usr/libexec/PlistBuddy` 将当前绝对路径写入：

```xml
<key>EssayPodProjectRoot</key>
<string>/实际路径/EssayPod</string>
```

因此，如果以后移动 EssayPod 项目目录，需要重新运行打包脚本。

### 6.3 Finder 启动环境

从 Finder 启动的应用不会继承终端完整的 `PATH`。如果直接调用 `npm` 或
Python，可能出现“终端里能运行，App 里找不到命令”的问题。

App 显式构造：

```text
../.venv/bin
/opt/homebrew/bin
/usr/local/bin
/usr/bin
/bin
/usr/sbin
/sbin
```

同时设置：

```text
PYTHONUNBUFFERED=1
NEXT_PUBLIC_API_BASE=http://127.0.0.1:8010
```

### 6.4 后端启动

后端由 NSTask 执行：

```text
../.venv/bin/python
  -m uvicorn
  app:app
  --host 127.0.0.1
  --port 8010
```

工作目录设置为 `EssayPod/`，所以原有相对路径、`.env` 查找逻辑和
`tasks/` 数据位置保持不变。

### 6.5 前端构建与启动

App 每次启动时执行：

```bash
NEXT_PUBLIC_API_BASE=http://127.0.0.1:8010 npm run build
npm run start -- --port 3000
```

选择生产服务而不是开发服务的原因：

- 日常使用更稳定；
- 不需要 HMR 和调试端口；
- 资源结构与最终部署构建一致；
- 启动时构建只需数秒，同时可以自动包含最新前端代码。

### 6.6 健康检查

启动器分别检查：

```text
http://127.0.0.1:8010/
http://127.0.0.1:3000/
```

判定条件：

- HTTP 状态码为 `200`；
- 响应内容包含 `EssayPod`；
- 最多等待 60 秒，每 0.25 秒检查一次。

健康检查使用：

- 唯一查询参数 `desktop_health=<UUID>`；
- `NSURLSessionConfiguration.ephemeralSessionConfiguration`；
- 禁用 `URLCache`；
- `Cache-Control: no-cache, no-store`。

前端构建完成后还会再次检查前后端，防止复用一个正处于关闭阶段的旧进程。

## 7. 原生窗口实现

### 7.1 NSWindow

窗口尺寸和能力：

```text
初始尺寸：1440 × 920
最小尺寸：1040 × 680
支持：关闭、最小化、缩放、全屏
```

使用的窗口样式：

```objc
NSWindowStyleMaskTitled
NSWindowStyleMaskClosable
NSWindowStyleMaskMiniaturizable
NSWindowStyleMaskResizable
```

没有启用 `NSWindowStyleMaskFullSizeContentView`，因此 Web UI 位于真正的
macOS 标题栏下方。

这样解决了两个体验问题：

- 顶部标题栏可以直接拖动窗口；
- 红黄绿窗口按钮不再覆盖左侧导航和页面内容。

标题文字被隐藏，但保留完整原生标题栏：

```objc
self.window.titlebarAppearsTransparent = NO;
self.window.titleVisibility = NSWindowTitleHidden;
```

### 7.2 WKWebView

WKWebView 使用默认持久数据存储，因此：

- `localStorage` 中的侧边栏、视频模板等设置可以保存；
- Cookie 和站内状态可以跨启动保留；
- 音频允许直接播放；
- JavaScript 保持启用。

外部链接和 `window.open` 链接交给 macOS 默认应用打开，主窗口只允许
`localhost` 和 `127.0.0.1` 导航。

### 7.3 macOS 菜单

应用提供：

- 关于 EssayPod；
- 退出 EssayPod；
- 撤销、重做；
- 剪切、复制、粘贴、全选；
- 重新载入；
- 进入全屏幕。

这保证 WKWebView 中的文本编辑快捷键符合普通 Mac 应用行为。

## 8. 子进程生命周期管理

### 8.1 为什么不能只终止 `npm`

`npm run start` 会继续产生 Node 子进程。如果仅调用：

```objc
[task terminate];
```

可能只终止 npm 包装进程，实际监听 3000 端口的 Node 进程仍然存在。

### 8.2 进程组方案

每个服务启动后调用：

```c
setpgid(processID, processID);
```

退出 App 时向负 PID 发送信号：

```c
kill(-processID, SIGTERM);
```

负 PID 表示终止整个进程组，从而一起清理 npm、Node 及其子进程。

App 在 `applicationWillTerminate:` 中按逆序停止自己启动的服务。

### 8.3 已存在服务

如果 8010 或 3000 上已经运行有效的 EssayPod 服务，App 会复用它，而不会
占用同一个端口重复启动。

App 只终止自己创建和记录的进程，不会盲目结束其他程序。

## 9. 日志与错误处理

日志保存到：

```text
~/Library/Logs/EssayPod/
├── backend.log
├── frontend-build.log
└── frontend.log
```

每次启动都会在日志末尾加入日期分隔符，不会直接覆盖历史日志。

如果出现以下问题，App 会显示原生错误对话框：

- 找不到源码目录；
- 找不到 `.venv`；
- 找不到 npm；
- 未安装前端依赖；
- 前端构建失败；
- 服务在 60 秒内未能启动。

错误对话框可以直接打开日志目录。

## 10. App 图标实现

图标使用 AppKit 绘制，视觉元素包括：

- 蓝色渐变圆角底板；
- 白色论文页面；
- 论文标题和正文线条；
- 播客麦克风标识。

图标生成器会绘制：

```text
16 × 16
32 × 32
64 × 64
128 × 128
256 × 256
512 × 512
1024 × 1024
```

然后按 ICNS 文件格式写入以下块：

```text
icp4, icp5, icp6, ic07, ic08, ic09, ic10
```

最终生成：

```text
Contents/Resources/AppIcon.icns
```

## 11. 打包脚本

执行：

```bash
cd EssayPod
./desktop/build-macos-app.sh
```

脚本步骤：

1. 检查项目父目录的 `.venv/bin/python`；
2. 检查 `web_ui/node_modules/.bin/vinext`；
3. 执行 `npm run build`；
4. 在临时目录创建完整 `.app`；
5. 写入实际源码路径；
6. 使用 Clang 编译 Objective-C 窗口壳；
7. 编译并运行图标生成器；
8. 清理扩展属性；
9. 对 App 进行本机临时签名；
10. 成功后才替换 `dist/EssayPod.app`；
11. 将上一版本保存为 `dist/EssayPod.previous.app`。

使用临时目录构建的意义是：如果编译失败，现有可用版本不会被半成品覆盖。

## 12. 签名和安装

### 12.1 本机临时签名

当前使用 ad-hoc 签名：

```bash
codesign --force --deep --sign - EssayPod.app
```

验证命令：

```bash
codesign --verify --deep --strict --verbose=2 EssayPod.app
```

该签名适合本机开发和自用，但不适合公开分发。

### 12.2 安装到“应用程序”

```bash
ditto \
  "$(pwd)/dist/EssayPod.app" \
  /Applications/EssayPod.app

xattr -cr /Applications/EssayPod.app
codesign --force --deep --sign - /Applications/EssayPod.app
```

安装完成后可从 Finder、Launchpad 或 Spotlight 打开。

## 13. 开发中遇到的问题及解决过程

### 13.1 Swift 编译器与 SDK 版本不匹配

#### 现象

最初计划使用 Swift/AppKit，但编译时出现：

```text
unable to open output file ~/.cache/clang/ModuleCache/...:
Operation not permitted
```

将模块缓存改到可写目录后，又出现：

```text
this SDK is not supported by the compiler
SDK: Apple Swift 6.0.3 (...1.5)
Compiler: Apple Swift 6.0.3 (...1.10)
```

尝试显式选择 macOS 14.5 SDK 后，还出现：

```text
redefinition of module 'SwiftBridging'
could not build module 'Foundation'
could not build module 'AppKit'
```

#### 根因

本机 Command Line Tools 中 Swift 编译器、系统 SDK 和 Swift module interface
并非同一完整工具链版本。即使版本号都显示 6.0.3，内部构建版本仍不一致。

#### 解决

不要求用户重新安装 Xcode，直接改用 Objective-C：

```bash
clang -fobjc-arc -framework AppKit -framework WebKit main.m
```

Objective-C 使用 Clang 和系统 Framework 头文件，不依赖 Swift Module ABI，
因此可以在当前环境稳定编译，同时仍然获得原生 AppKit 窗口。

### 13.2 Codex 沙箱阻止前端调试端口

#### 现象

在受限开发环境执行 `npm run dev` 时出现：

```text
listen EPERM: operation not permitted 0.0.0.0:9229
```

生产服务也会绑定：

```text
0.0.0.0:3000
```

#### 根因

Cloudflare/Vite 插件需要调试端口或监听所有本地网卡，而开发沙箱只允许部分
本地监听方式。这不是 EssayPod 前端本身的错误。

#### 解决

- 构建阶段使用可以正常运行的 `npm run build`；
- 实际服务由用户启动的 `.app` 在普通 macOS 用户环境中运行；
- 日常桌面版使用 `npm run start`，不依赖 HMR 调试端口；
- 最终通过监听端口和真实 API 请求验证运行状态。

### 13.3 `iconutil` 报 `Invalid Iconset`

#### 现象

生成的 PNG 文件尺寸和格式均正确：

```text
16, 32, 64, 128, 256, 512, 1024
8-bit RGBA PNG
```

但执行：

```bash
iconutil -c icns AppIcon.iconset
```

仍返回：

```text
Invalid Iconset
```

#### 根因

本机 `iconutil` 对生成文件的元数据或 iconset 结构校验不兼容。继续调整文件名、
DPI 和 PNG 格式仍无法稳定解决。

#### 解决

绕过 `iconutil`，由 `AppIcon.m` 直接构造 ICNS 容器：

1. 用 AppKit 绘制不同尺寸 PNG；
2. 为每个 PNG 写入对应 ICNS 类型；
3. 按大端序写入块长度；
4. 生成合法的 `AppIcon.icns`。

最终通过 `file` 验证为：

```text
Mac OS X icon
```

### 13.4 `codesign` 被 Finder 扩展属性阻止

#### 现象

签名或严格验证时出现：

```text
resource fork, Finder information, or similar detritus not allowed
Disallowed xattr com.apple.FinderInfo
```

还观察到：

```text
com.apple.fileprovider.fpfs#P
```

#### 根因

工作区位于 Desktop。Finder 或文件提供程序会给 `.app` 写入扩展属性，而
`codesign --strict` 不接受这些附加数据。

#### 解决

在签名前执行：

```bash
xattr -cr EssayPod.app
```

并将构建过程移到 `mktemp` 创建的临时目录。只有完成清理和签名后，才把
`.app` 移入 `dist/`。

复制到 `/Applications` 后再次清理并签名，最终验证结果为：

```text
valid on disk
satisfies its Designated Requirement
```

### 13.5 健康检查错误复用旧响应

#### 现象

一次测试中：

- 前端 3000 端口正常；
- 后端 8010 已经退出；
- App 却认为后端仍然健康，因此没有重新启动后端。

#### 根因

最初使用 `NSURLSession.sharedSession` 请求固定 URL。系统缓存了上一个
`127.0.0.1:8010/` 的 HTTP 200 响应。即使服务已经停止，新 App 进程仍可能
读到缓存结果。

仅加入 `Cache-Control` 和 `reloadIgnoringLocalCacheData` 仍不足以完全避免
共享缓存层命中。

#### 解决

最终同时采用：

```text
每次请求添加随机 UUID 查询参数
使用 ephemeral NSURLSession
禁用 URLCache
设置 no-cache, no-store
前端构建后再次检查两个服务
```

这样每次健康检查都是唯一请求，不可能命中旧的固定 URL 响应。

### 13.6 使用 `open -n` 测试造成多实例混乱

#### 现象

为了反复测试，曾使用：

```bash
open -n /Applications/EssayPod.app
```

`-n` 会强制创建多个相同 Bundle ID 的 App 实例，导致：

- 不同实例分别拥有前端或后端进程；
- 一个实例退出后，另一个实例的服务可能仍在；
- 通过 AppleScript 退出时不容易判断目标实例；
- 端口归属看起来不一致。

#### 根因

这是测试命令人为绕过 macOS 单实例行为造成的，不是普通用户双击时会出现的
问题。

#### 解决

清理所有测试实例后，改用：

```bash
open /Applications/EssayPod.app
```

正常启动路径下：

- 第一次点击启动一个 App；
- 第二次点击只激活现有窗口；
- 8010 和 3000 的 PID 保持不变；
- 不会重复创建服务。

### 13.7 App 退出后 Node 子进程残留

#### 风险

如果只记录 npm 的 NSTask，npm 退出后实际 Node 服务可能继续监听 3000。

#### 解决

启动后建立独立进程组，退出时对整个进程组发送 `SIGTERM`。验证中关闭 App 后：

```text
8010 不再监听
3000 不再监听
Uvicorn 完成 application shutdown
```

### 13.8 Finder 中找不到 Python 或 npm

#### 风险

Finder 启动的 App 不会读取 `.zshrc`，所以 Homebrew 和虚拟环境命令可能不在
`PATH` 中。

#### 解决

- Python 使用已知绝对路径查找；
- npm 依次检查 `/opt/homebrew/bin`、`/usr/local/bin` 和 `/usr/bin`；
- 为所有子进程构造明确的 `PATH`；
- 启动前检查依赖并提供中文错误提示。

### 13.9 原始标题栏不能拖动且窗口按钮压住内容

#### 现象

初始窗口使用：

```objc
NSWindowStyleMaskFullSizeContentView
titlebarAppearsTransparent = YES
```

这会让 WKWebView 延伸到标题栏。结果：

- 红黄绿按钮覆盖左侧导航；
- 网页元素接收鼠标事件；
- 顶部没有可靠的窗口拖动区域；
- 内容与窗口按钮距离太近。

#### 解决

移除 `NSWindowStyleMaskFullSizeContentView`，使用独立的原生标题栏：

```objc
self.window.titlebarAppearsTransparent = NO;
self.window.titleVisibility = NSWindowTitleHidden;
```

网页内容从标题栏下方开始，标题栏整条区域可用于拖动。

### 13.10 失败构建产生半成品和多个归档

#### 现象

早期脚本会先移动旧 App，再直接在最终输出位置编译。如果中途失败：

- `dist/EssayPod.app` 可能成为半成品；
- 多次失败会产生多个带时间戳的归档。

#### 解决

改为 staging 构建：

```text
临时目录完整构建
      ↓
图标成功
      ↓
签名成功
      ↓
替换最终 App
```

最终只保留一个 `EssayPod.previous.app`，用于最近版本回退。

### 13.11 App 内点击和拖拽 PDF 均无响应

#### 现象

“导入一篇论文”弹窗中：

- 点击“浏览文件”没有弹出文件选择器；
- 从 Finder 将 PDF 拖到“拖入或选择 PDF”区域也没有反应；
- 相同的 `<input type="file">` 在普通浏览器中可以工作。

#### 根因

这是两个相互独立的问题：

1. 桌面壳将自身设置为 `WKUIDelegate`，但没有实现 WKWebView 的文件选择面板
   回调。因此 Web 页面请求打开 `<input type="file">` 时，没有原生组件负责
   显示 `NSOpenPanel`。
2. Web UI 虽然显示“拖入或选择 PDF”，但 Dropzone 只是包裹隐藏 file input
   的 `<label>`，没有注册 `dragenter`、`dragover`、`dragleave` 和 `drop`
   事件。界面文案描述了拖拽功能，代码却尚未实现。

#### 解决

桌面壳实现：

```objc
webView:
runOpenPanelWithParameters:
initiatedByFrame:
completionHandler:
```

回调中创建 `NSOpenPanel`，并通过 `UniformTypeIdentifiers` 限制为：

```objc
panel.allowedContentTypes = @[UTTypePDF];
```

用户选择后将 `panel.URLs` 交回 WKWebView，取消时返回 `nil`。

Web UI 同时加入完整拖拽处理：

- `dragover` 中调用 `preventDefault()`，允许 Finder 文件落下；
- `drop` 中读取 `event.dataTransfer.files[0]`；
- 接受 MIME 为 `application/pdf` 或文件名以 `.pdf` 结尾的文件；
- 在前端验证 50 MB 大小限制；
- 拖入时显示紫色边框和背景反馈；
- 支持 Enter/空格键打开文件选择器；
- 清空 input 的 value，使用户可以连续选择同一个 PDF。

原生 App 构建时新增链接：

```text
UniformTypeIdentifiers.framework
```

最终二进制中确认存在：

```text
webView:runOpenPanelWithParameters:initiatedByFrame:completionHandler:
```

## 14. 验证记录

完成的验证包括：

### 14.1 编译和构建

```text
npm run build                     通过
Clang Objective-C 编译            通过
AppIcon.icns 生成                 通过
Info.plist 检查                   通过
```

### 14.2 签名

```text
codesign --verify --deep --strict 通过
```

### 14.3 运行

```text
FastAPI 127.0.0.1:8010           正常监听
Web UI 0.0.0.0:3000              正常监听
/api/tasks                       持续返回 200
翻译、图片、聊天状态 API          持续返回 200
```

### 14.4 生命周期

```text
双击启动                         通过
独立窗口                         通过
再次点击激活现有窗口              通过
关闭窗口退出 App                  通过
退出后关闭前后端服务              通过
```

### 14.5 原有数据

App 继续读取：

```text
EssayPod/tasks/
```

打包过程没有移动、复制、删除或重置历史任务数据。

## 15. 日常开发和更新方式

### 15.1 修改 Python 后端

修改完成后：

1. 退出 EssayPod；
2. 重新打开 EssayPod。

不需要重新打包桌面壳。

### 15.2 修改 React Web UI

修改完成后：

1. 退出 EssayPod；
2. 重新打开 EssayPod；
3. App 会自动执行 `npm run build`。

不需要重新编译 Objective-C 窗口壳。

### 15.3 修改桌面窗口或启动逻辑

修改 `desktop/main.m`、`Info.plist` 或图标代码后：

```bash
cd EssayPod
./desktop/build-macos-app.sh
```

然后更新安装版本：

```bash
ditto dist/EssayPod.app /Applications/EssayPod.app
xattr -cr /Applications/EssayPod.app
codesign --force --deep --sign - /Applications/EssayPod.app
```

### 15.4 移动项目目录

由于 `Info.plist` 保存了绝对源码路径，移动项目后必须重新运行打包脚本。

也可以临时指定：

```bash
ESSAYPOD_PROJECT_ROOT=/新的/EssayPod/路径 \
  /Applications/EssayPod.app/Contents/MacOS/EssayPod
```

## 16. 当前方案限制

当前版本是为本机持续开发设计的，不是可直接发送给其他用户的独立安装包。

它仍依赖：

- 当前 EssayPod 源码目录；
- 工作区中的 `.venv`；
- `web_ui/node_modules`；
- 本机 Node.js；
- 本机 FFmpeg 和项目所需模型；
- 当前 `.env` 配置。

如果删除或移动这些内容，App 需要重新配置或重新打包。

## 17. 未来产品化建议

如果未来要分发给其他 Mac 用户，建议分阶段完成：

### 17.1 独立运行环境

- 使用 PyInstaller/Nuitka 将 Python 后端打成 sidecar；
- 固定和打包 Python 依赖；
- 为 Node 前端提供内嵌运行时，或改造成可由 App 内置 HTTP Server 服务的静态包；
- 明确打包 FFmpeg、ONNX 模型和其他资源的许可。

### 17.2 数据目录迁移

将运行时数据从源码目录：

```text
EssayPod/tasks/
```

迁移到：

```text
~/Library/Application Support/EssayPod/
```

并提供一次性迁移、版本号和数据结构升级机制。

### 17.3 密钥安全

将 API Key 从普通 `.env` 迁移到 macOS Keychain，避免明文保存。

### 17.4 正式签名和公证

公开分发需要：

- Apple Developer ID Application 证书；
- Hardened Runtime；
- 必要的 Entitlements；
- `codesign` 正式签名；
- `notarytool` 提交 Apple 公证；
- `stapler` 写入公证票据。

### 17.5 多架构

当前可执行文件是：

```text
arm64
```

如需支持 Intel Mac，应构建：

```text
x86_64 + arm64 Universal Binary
```

Python、Node、FFmpeg 和原生依赖也需要对应的双架构版本。

### 17.6 自动更新

可加入：

- 应用版本检查；
- 下载新版本；
- 签名校验；
- 安全替换 `.app`；
- 数据迁移和失败回滚。

## 18. 当前开发环境记录

本次实现和验证环境：

```text
macOS 14.5
Apple Silicon arm64
Node.js v24.2.0
npm 11.3.0
Apple Clang 16.0.0
Python 3.11 virtual environment
FastAPI 0.139.2
Uvicorn 0.49.0
```

## 19. 关键文件索引

```text
desktop/main.m
    原生窗口、菜单、WKWebView、前后端启动、健康检查和进程清理

desktop/AppIcon.m
    图标绘制和 ICNS 容器生成

desktop/Info.plist
    Bundle 元数据、最低系统版本、本地网络和源码路径配置

desktop/build-macos-app.sh
    Web UI 验证、原生编译、图标生成、签名和原子替换

desktop/README.md
    面向日常使用的简明说明
```

## 20. 总结

EssayPod 没有被重写成新的原生业务应用，而是在现有成熟的 FastAPI + React
架构外增加了一层轻量、可维护的 AppKit 桌面壳。

该方案实现了：

- 从“应用程序”目录双击启动；
- 独立 macOS 窗口和 Dock 图标；
- 原生可拖动标题栏；
- 自动构建并启动前端；
- 自动启动和停止后端；
- 复用原有任务与配置；
- 源码继续快速迭代；
- 可重复打包、签名和回退。

它解决了当前“方便本机使用”和“仍需持续开发”之间的平衡问题，也为以后制作
完全独立、可签名和可公证的发行版保留了清晰的升级路径。
