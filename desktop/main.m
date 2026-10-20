#import <Cocoa/Cocoa.h>
#import <UniformTypeIdentifiers/UniformTypeIdentifiers.h>
#import <WebKit/WebKit.h>
#import <signal.h>

static NSString *const BackendAddress = @"http://127.0.0.1:8010/";
static NSString *const FrontendAddress = @"http://127.0.0.1:3000/";

@interface ManagedProcess : NSObject
@property(nonatomic, strong) NSTask *task;
@property(nonatomic, strong) NSFileHandle *logHandle;
- (void)terminateGroup;
@end

@implementation ManagedProcess
- (void)terminateGroup {
    if (self.task.running) {
        pid_t processID = self.task.processIdentifier;
        kill(-processID, SIGTERM);
        [self.task terminate];
    }
    [self.logHandle closeFile];
}
@end

// WKWebView script messages are not guaranteed to arrive while the original
// mouse-down event is still current. These transparent native surfaces sit only
// above known-empty header areas, so window dragging remains immediate and
// reliable without swallowing the sidebar toggle or traffic-light controls.
@interface WindowDragView : NSView
@end

@implementation WindowDragView
- (BOOL)acceptsFirstMouse:(NSEvent *)event {
    return YES;
}

- (void)mouseDown:(NSEvent *)event {
    [self.window performWindowDragWithEvent:event];
}
@end

@interface AppDelegate : NSObject <
    NSApplicationDelegate,
    WKNavigationDelegate,
    WKUIDelegate,
    WKDownloadDelegate,
    WKScriptMessageHandler
>
@property(nonatomic, strong) NSWindow *window;
@property(nonatomic, strong) WKWebView *webView;
@property(nonatomic, strong) WindowDragView *sidebarDragView;
@property(nonatomic, strong) WindowDragView *mainHeaderDragView;
@property(nonatomic, strong) NSMutableArray<ManagedProcess *> *managedProcesses;
@property(nonatomic, strong) NSMutableSet<WKDownload *> *activeDownloads;
@property(nonatomic, strong) NSURL *projectRoot;
@property(nonatomic, strong) NSURL *logDirectory;
@property(nonatomic, assign) BOOL terminating;
@end

@implementation AppDelegate

- (instancetype)init {
    self = [super init];
    if (self) {
        _managedProcesses = [NSMutableArray array];
        _activeDownloads = [NSMutableSet set];
        _projectRoot = [self resolveProjectRoot];
        NSURL *library = [[[NSFileManager defaultManager]
            URLsForDirectory:NSLibraryDirectory
            inDomains:NSUserDomainMask] firstObject];
        _logDirectory = [library URLByAppendingPathComponent:@"Logs/EssayPod" isDirectory:YES];
    }
    return self;
}

- (NSURL *)resolveProjectRoot {
    NSString *override = NSProcessInfo.processInfo.environment[@"ESSAYPOD_PROJECT_ROOT"];
    if (override.length > 0) {
        return [NSURL fileURLWithPath:override isDirectory:YES].URLByStandardizingPath;
    }
    NSString *configured = NSBundle.mainBundle.infoDictionary[@"EssayPodProjectRoot"];
    if (configured.length > 0 && ![configured isEqualToString:@"__ESSAYPOD_PROJECT_ROOT__"]) {
        return [NSURL fileURLWithPath:configured isDirectory:YES].URLByStandardizingPath;
    }
    return NSBundle.mainBundle.bundleURL.URLByDeletingLastPathComponent
        .URLByDeletingLastPathComponent.URLByStandardizingPath;
}

- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    [NSApp setActivationPolicy:NSApplicationActivationPolicyRegular];
    [self buildMenu];
    [self buildWindow];
    [NSApp activateIgnoringOtherApps:YES];
    [self startEssayPod];
}

- (BOOL)applicationShouldTerminateAfterLastWindowClosed:(NSApplication *)sender {
    return YES;
}

- (void)applicationWillTerminate:(NSNotification *)notification {
    self.terminating = YES;
    NSArray<ManagedProcess *> *processes = nil;
    @synchronized(self.managedProcesses) {
        processes = [self.managedProcesses.reverseObjectEnumerator allObjects];
        [self.managedProcesses removeAllObjects];
    }
    for (ManagedProcess *process in processes) {
        [process terminateGroup];
    }
}

- (void)buildMenu {
    NSMenu *mainMenu = [[NSMenu alloc] init];

    NSMenuItem *appItem = [[NSMenuItem alloc] init];
    NSMenu *appMenu = [[NSMenu alloc] initWithTitle:@"EssayPod"];
    [appMenu addItemWithTitle:@"关于 EssayPod"
                       action:@selector(orderFrontStandardAboutPanel:)
                keyEquivalent:@""];
    [appMenu addItem:NSMenuItem.separatorItem];
    [appMenu addItemWithTitle:@"退出 EssayPod"
                       action:@selector(terminate:)
                keyEquivalent:@"q"];
    appItem.submenu = appMenu;
    [mainMenu addItem:appItem];

    NSMenuItem *editItem = [[NSMenuItem alloc] init];
    NSMenu *editMenu = [[NSMenu alloc] initWithTitle:@"编辑"];
    [editMenu addItemWithTitle:@"撤销" action:@selector(undo:) keyEquivalent:@"z"];
    [editMenu addItemWithTitle:@"重做" action:@selector(redo:) keyEquivalent:@"Z"];
    [editMenu addItem:NSMenuItem.separatorItem];
    [editMenu addItemWithTitle:@"剪切" action:@selector(cut:) keyEquivalent:@"x"];
    [editMenu addItemWithTitle:@"复制" action:@selector(copy:) keyEquivalent:@"c"];
    [editMenu addItemWithTitle:@"粘贴" action:@selector(paste:) keyEquivalent:@"v"];
    [editMenu addItemWithTitle:@"全选" action:@selector(selectAll:) keyEquivalent:@"a"];
    editItem.submenu = editMenu;
    [mainMenu addItem:editItem];

    NSMenuItem *viewItem = [[NSMenuItem alloc] init];
    NSMenu *viewMenu = [[NSMenu alloc] initWithTitle:@"显示"];
    [viewMenu addItemWithTitle:@"重新载入"
                        action:@selector(reloadWebView:)
                 keyEquivalent:@"r"];
    [viewMenu addItemWithTitle:@"进入全屏幕"
                        action:@selector(toggleFullScreen:)
                 keyEquivalent:@"f"];
    viewItem.submenu = viewMenu;
    [mainMenu addItem:viewItem];

    NSApp.mainMenu = mainMenu;
}

- (void)buildWindow {
    WKWebViewConfiguration *configuration = [[WKWebViewConfiguration alloc] init];
    configuration.websiteDataStore = WKWebsiteDataStore.defaultDataStore;
    configuration.defaultWebpagePreferences.allowsContentJavaScript = YES;
    configuration.mediaTypesRequiringUserActionForPlayback = WKAudiovisualMediaTypeNone;

    // The page marks only its two quiet header surfaces as draggable. Buttons,
    // inputs, links, and project rows remain regular interactive web content.
    NSString *windowDragScript =
        @"document.addEventListener('mousedown',function(event){"
         "if(event.button!==0)return;"
         "var target=event.target instanceof Element?event.target:null;"
         "if(!target||!target.closest('[data-window-drag]'))return;"
         "if(target.closest('button,a,input,textarea,select,[role=button],[data-window-no-drag]'))return;"
         "event.preventDefault();"
         "window.webkit.messageHandlers.windowDrag.postMessage({});"
         "},true);";
    WKUserScript *dragUserScript = [[WKUserScript alloc]
        initWithSource:windowDragScript
        injectionTime:WKUserScriptInjectionTimeAtDocumentStart
        forMainFrameOnly:YES];
    [configuration.userContentController addUserScript:dragUserScript];
    [configuration.userContentController addScriptMessageHandler:self name:@"windowDrag"];
    [configuration.userContentController addScriptMessageHandler:self name:@"sidebarState"];
    [configuration.userContentController addScriptMessageHandler:self name:@"agentPanelState"];

    self.webView = [[WKWebView alloc] initWithFrame:NSZeroRect configuration:configuration];
    self.webView.navigationDelegate = self;
    self.webView.UIDelegate = self;

    self.window = [[NSWindow alloc]
        initWithContentRect:NSMakeRect(0, 0, 1440, 920)
        styleMask:
            NSWindowStyleMaskTitled |
            NSWindowStyleMaskClosable |
            NSWindowStyleMaskMiniaturizable |
            NSWindowStyleMaskResizable |
            NSWindowStyleMaskFullSizeContentView
        backing:NSBackingStoreBuffered
        defer:NO];
    self.window.title = @"EssayPod";
    // Let EssayPod's own header continue behind the traffic lights. This keeps
    // the standard window controls while removing the separate titlebar band.
    self.window.titlebarAppearsTransparent = YES;
    self.window.titleVisibility = NSWindowTitleHidden;
    self.window.movableByWindowBackground = NO;
    self.window.backgroundColor = [NSColor colorWithCalibratedRed:0.953 green:0.953 blue:0.945 alpha:1];
    self.webView.underPageBackgroundColor = self.window.backgroundColor;
    if (@available(macOS 11.0, *)) {
        self.window.titlebarSeparatorStyle = NSTitlebarSeparatorStyleNone;
    }
    self.window.minSize = NSMakeSize(1040, 680);

    NSView *contentContainer = [[NSView alloc] initWithFrame:NSMakeRect(0, 0, 1440, 920)];
    self.webView.frame = contentContainer.bounds;
    self.webView.autoresizingMask = NSViewWidthSizable | NSViewHeightSizable;
    [contentContainer addSubview:self.webView];

    CGFloat contentHeight = NSHeight(contentContainer.bounds);
    // Cover the full quiet part of the sidebar title rail. The traffic lights
    // stay to its left. When the sidebar is collapsed, the expand button is
    // positioned left of this surface, so it remains interactive.
    self.sidebarDragView = [[WindowDragView alloc]
        initWithFrame:NSMakeRect(88, contentHeight - 54, 172, 54)];
    self.sidebarDragView.autoresizingMask = NSViewMinYMargin;
    [contentContainer addSubview:self.sidebarDragView positioned:NSWindowAbove relativeTo:self.webView];

    // WKWebView script messages are not guaranteed to arrive while mouse-down is
    // still current. Add a second native drag surface over the quiet middle of
    // the workspace header. It starts after the sidebar toggle and leaves the
    // right edge free for the fixed Research Agent toggle and future controls.
    // This 180pt exclusion is intentionally much wider than the 36pt button,
    // so the native overlay can never intercept its pointer events.
    CGFloat headerRightSafety = 180;
    CGFloat headerDragWidth = MAX(0, NSWidth(contentContainer.bounds) - 520 - headerRightSafety);
    self.mainHeaderDragView = [[WindowDragView alloc]
        initWithFrame:NSMakeRect(520, contentHeight - 60, headerDragWidth, 60)];
    self.mainHeaderDragView.autoresizingMask = NSViewWidthSizable | NSViewMinYMargin;
    [contentContainer addSubview:self.mainHeaderDragView positioned:NSWindowAbove relativeTo:self.webView];

    self.window.contentView = contentContainer;
    [self.window center];
    [self.window makeKeyAndOrderFront:nil];
    [self showLoading:@"正在检查 EssayPod…"];
}

- (void)reloadWebView:(id)sender {
    [self.webView reload];
}

- (void)userContentController:(WKUserContentController *)userContentController
      didReceiveScriptMessage:(WKScriptMessage *)message {
    if ([message.name isEqualToString:@"sidebarState"]) {
        NSDictionary *state = [message.body isKindOfClass:NSDictionary.class]
            ? (NSDictionary *)message.body
            : nil;
        self.sidebarDragView.hidden = [state[@"collapsed"] boolValue];
        return;
    }
    if ([message.name isEqualToString:@"agentPanelState"]) {
        NSDictionary *state = [message.body isKindOfClass:NSDictionary.class]
            ? (NSDictionary *)message.body
            : nil;
        BOOL open = [state[@"open"] boolValue];
        CGFloat contentWidth = NSWidth(self.window.contentView.bounds);
        CGFloat rightSafety = open ? 640 : 180;
        NSRect frame = self.mainHeaderDragView.frame;
        frame.size.width = MAX(0, contentWidth - frame.origin.x - rightSafety);
        self.mainHeaderDragView.frame = frame;
        return;
    }
    if (![message.name isEqualToString:@"windowDrag"]) {
        return;
    }
    NSEvent *event = NSApp.currentEvent;
    if (event.type == NSEventTypeLeftMouseDown) {
        [self.window performWindowDragWithEvent:event];
    }
}

- (void)showLoading:(NSString *)message {
    NSString *html = [NSString stringWithFormat:
        @"<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
         "<meta name='viewport' content='width=device-width,initial-scale=1'>"
         "<style>"
         "*{box-sizing:border-box}body{margin:0;height:100vh;display:grid;place-items:center;"
         "font-family:-apple-system,BlinkMacSystemFont,'SF Pro Text',sans-serif;color:#202124;"
         "background:#f5f5f2}"
         "main{text-align:center}.mark{width:82px;height:82px;margin:0 auto 24px;border-radius:23px;"
         "display:grid;place-items:center;background:#f7f7f3;border:1px solid rgba(0,0,0,.07);"
         "box-shadow:0 18px 46px rgba(20,20,18,.12)}"
         ".core{position:relative;width:54px;height:54px;border-radius:14px;background:#17181b}"
         ".network{position:absolute;left:14px;top:14px;width:27px;height:27px;color:#f8f8f3}"
         ".network b{position:absolute;left:2px;top:2px;width:4px;height:23px;border-radius:4px;background:currentColor}"
         ".network i{position:absolute;left:4px;height:4px;border-radius:4px;background:currentColor}"
         ".network i:nth-child(1){top:2px;width:19px}.network i:nth-child(2){top:11.5px;width:14px}"
         ".network i:nth-child(3){top:21px;width:19px}.network i:after{content:'';position:absolute;"
         "right:-3px;top:-1px;width:6px;height:6px;border-radius:50%%;background:currentColor}"
         ".network i:nth-child(1):after{background:#2eb67d}"
         "h1{margin:0 0 10px;font-size:29px;letter-spacing:-.8px}"
         "p{margin:0;color:#77797f;font-size:15px}.bar{width:220px;height:3px;margin:26px auto 0;"
         "overflow:hidden;border-radius:999px;background:#dfdfdb}"
         ".bar:after{content:'';display:block;width:42%%;height:100%%;border-radius:inherit;"
         "background:#202124;animation:move 1.25s ease-in-out infinite}"
         "@keyframes move{0%%{transform:translateX(-110%%)}100%%{transform:translateX(350%%)}}"
         "</style></head><body><main><div class='mark'><div class='core'><span class='network'>"
         "<i></i><i></i><i></i><b></b></span></div></div><h1>EssayPod</h1>"
         "<p id='status'>%@</p><div class='bar'></div></main></body></html>",
        message];
    [self.webView loadHTMLString:html baseURL:nil];
}

- (void)updateLoading:(NSString *)message {
    NSData *data = [NSJSONSerialization dataWithJSONObject:@[message] options:0 error:nil];
    NSString *json = [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding];
    dispatch_async(dispatch_get_main_queue(), ^{
        NSString *script = [NSString stringWithFormat:
            @"document.getElementById('status').textContent=(%@)[0]", json];
        [self.webView evaluateJavaScript:script completionHandler:nil];
    });
}

- (void)startEssayPod {
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
        NSError *error = nil;
        if (![self validateProject:&error]) {
            [self presentStartupError:error];
            return;
        }
        [[NSFileManager defaultManager]
            createDirectoryAtURL:self.logDirectory
            withIntermediateDirectories:YES
            attributes:nil
            error:&error];
        if (error) {
            [self presentStartupError:error];
            return;
        }

        [self updateLoading:@"正在启动论文处理服务…"];
        if (![self isEssayPodBackendReady]) {
            ManagedProcess *backend = [self launchBackend:&error];
            if (!backend) {
                [self presentStartupError:error];
                return;
            }
            [self addManagedProcess:backend];
            if (![self waitForService:@"论文处理服务"
                              timeout:60
                                check:^BOOL { return [self isEssayPodBackendReady]; }
                                error:&error]) {
                [self presentStartupError:error];
                return;
            }
        }

        if (![self isEssayPodFrontendReady]) {
            [self updateLoading:@"正在准备应用界面…"];
            if (![self buildFrontend:&error]) {
                [self presentStartupError:error];
                return;
            }
            [self updateLoading:@"正在打开应用窗口…"];
            ManagedProcess *frontend = [self launchFrontend:&error];
            if (!frontend) {
                [self presentStartupError:error];
                return;
            }
            [self addManagedProcess:frontend];
            if (![self waitForService:@"应用界面"
                              timeout:60
                                check:^BOOL { return [self isEssayPodFrontendReady]; }
                                error:&error]) {
                [self presentStartupError:error];
                return;
            }
        }

        // A previous EssayPod instance can still answer one health check while
        // it is shutting down. Re-check both services after the frontend build
        // so the new window never inherits a process that disappears mid-launch.
        if (![self isEssayPodBackendReady]) {
            [self updateLoading:@"正在重新连接论文处理服务…"];
            ManagedProcess *backend = [self launchBackend:&error];
            if (!backend) {
                [self presentStartupError:error];
                return;
            }
            [self addManagedProcess:backend];
            if (![self waitForService:@"论文处理服务"
                              timeout:60
                                check:^BOOL { return [self isEssayPodBackendReady]; }
                                error:&error]) {
                [self presentStartupError:error];
                return;
            }
        }
        if (![self isEssayPodFrontendReady]) {
            [self updateLoading:@"正在重新打开应用界面…"];
            if (![self buildFrontend:&error]) {
                [self presentStartupError:error];
                return;
            }
            ManagedProcess *frontend = [self launchFrontend:&error];
            if (!frontend) {
                [self presentStartupError:error];
                return;
            }
            [self addManagedProcess:frontend];
            if (![self waitForService:@"应用界面"
                              timeout:60
                                check:^BOOL { return [self isEssayPodFrontendReady]; }
                                error:&error]) {
                [self presentStartupError:error];
                return;
            }
        }

        if (self.terminating) {
            return;
        }
        dispatch_async(dispatch_get_main_queue(), ^{
            [self.webView loadRequest:[NSURLRequest
                requestWithURL:[NSURL URLWithString:FrontendAddress]]];
        });
    });
}

- (BOOL)validateProject:(NSError **)error {
    NSArray<NSString *> *required = @[
        [self.projectRoot URLByAppendingPathComponent:@"app.py"].path,
        [self.projectRoot URLByAppendingPathComponent:@"web_ui/package.json"].path,
    ];
    for (NSString *path in required) {
        if (![[NSFileManager defaultManager] fileExistsAtPath:path]) {
            if (error) {
                *error = [NSError
                    errorWithDomain:@"EssayPodDesktop"
                    code:10
                    userInfo:@{
                        NSLocalizedDescriptionKey: [NSString stringWithFormat:
                            @"找不到 EssayPod 源码目录。\n当前配置：%@",
                            self.projectRoot.path]
                    }];
            }
            return NO;
        }
    }
    return YES;
}

- (NSMutableDictionary<NSString *, NSString *> *)baseEnvironment {
    NSMutableDictionary *environment =
        [NSProcessInfo.processInfo.environment mutableCopy];
    NSString *venv = [[self.projectRoot URLByDeletingLastPathComponent]
        URLByAppendingPathComponent:@".venv/bin"].path;
    NSArray *paths = @[
        venv,
        @"/opt/homebrew/bin",
        @"/usr/local/bin",
        @"/usr/bin",
        @"/bin",
        @"/usr/sbin",
        @"/sbin",
        environment[@"PATH"] ?: @"",
    ];
    environment[@"PATH"] = [paths componentsJoinedByString:@":"];
    NSString *venvRoot = [[self.projectRoot URLByDeletingLastPathComponent]
        URLByAppendingPathComponent:@".venv" isDirectory:YES].path;
    NSString *libRoot = [venvRoot stringByAppendingPathComponent:@"lib"];
    NSArray<NSString *> *libEntries = [[NSFileManager defaultManager]
        contentsOfDirectoryAtPath:libRoot
        error:nil] ?: @[];
    for (NSString *entry in libEntries) {
        if (![entry hasPrefix:@"python"]) {
            continue;
        }
        NSString *sitePackages = [[libRoot stringByAppendingPathComponent:entry]
            stringByAppendingPathComponent:@"site-packages"];
        BOOL isDirectory = NO;
        if ([[NSFileManager defaultManager] fileExistsAtPath:sitePackages isDirectory:&isDirectory] && isDirectory) {
            NSString *existing = environment[@"PYTHONPATH"] ?: @"";
            environment[@"PYTHONPATH"] = existing.length > 0
                ? [@[sitePackages, existing] componentsJoinedByString:@":"]
                : sitePackages;
            break;
        }
    }
    environment[@"VIRTUAL_ENV"] = venvRoot;
    environment[@"PYTHONUNBUFFERED"] = @"1";
    environment[@"NEXT_PUBLIC_API_BASE"] = @"http://127.0.0.1:8010";
    return environment;
}

- (NSURL *)pythonExecutable:(NSError **)error {
    NSArray<NSURL *> *candidates = @[
        [[self.projectRoot URLByDeletingLastPathComponent]
            URLByAppendingPathComponent:@".venv/bin/python"],
        [self.projectRoot URLByAppendingPathComponent:@".venv/bin/python"],
    ];
    for (NSURL *candidate in candidates) {
        if ([[NSFileManager defaultManager] isExecutableFileAtPath:candidate.path]) {
            // Launching a venv symlink from NSTask can stall inside CPython's
            // getpath/pyvenv.cfg initialization on macOS. The environment
            // above already provides this venv's site-packages, so execute the
            // resolved interpreter directly and avoid that startup path.
            NSString *resolved = [candidate.path stringByResolvingSymlinksInPath];
            return [NSURL fileURLWithPath:resolved];
        }
    }
    if (error) {
        *error = [NSError
            errorWithDomain:@"EssayPodDesktop"
            code:11
            userInfo:@{
                NSLocalizedDescriptionKey:
                    @"找不到 EssayPod 的 Python 环境。请确认工作区根目录中的 .venv 仍然存在。"
            }];
    }
    return nil;
}

- (NSURL *)npmExecutable:(NSError **)error {
    NSArray<NSString *> *candidates = @[
        @"/opt/homebrew/bin/npm",
        @"/usr/local/bin/npm",
        @"/usr/bin/npm",
    ];
    for (NSString *candidate in candidates) {
        if ([[NSFileManager defaultManager] isExecutableFileAtPath:candidate]) {
            return [NSURL fileURLWithPath:candidate];
        }
    }
    if (error) {
        *error = [NSError
            errorWithDomain:@"EssayPodDesktop"
            code:12
            userInfo:@{
                NSLocalizedDescriptionKey:
                    @"找不到 Node.js/npm。请先安装项目要求的 Node.js 版本。"
            }];
    }
    return nil;
}

- (ManagedProcess *)launchBackend:(NSError **)error {
    NSURL *python = [self pythonExecutable:error];
    if (!python) {
        return nil;
    }
    NSTask *task = [[NSTask alloc] init];
    // NSTask may reject a resolved pyenv binary even though the same target is
    // executable through the venv link. Launch the system env shim and pass
    // every argument separately; this avoids both the venv getpath stall and
    // any shell command construction.
    task.executableURL = [NSURL fileURLWithPath:@"/usr/bin/env"];
    task.arguments = @[
        python.path,
        @"-m", @"uvicorn", @"app:app",
        @"--host", @"127.0.0.1",
        @"--port", @"8010",
    ];
    task.currentDirectoryURL = self.projectRoot;
    task.environment = [self baseEnvironment];
    return [self launchTask:task logName:@"backend.log" error:error];
}

- (BOOL)buildFrontend:(NSError **)error {
    NSURL *webRoot = [self.projectRoot URLByAppendingPathComponent:@"web_ui" isDirectory:YES];
    NSString *vinext = [webRoot URLByAppendingPathComponent:@"node_modules/.bin/vinext"].path;
    if (![[NSFileManager defaultManager] fileExistsAtPath:vinext]) {
        if (error) {
            *error = [NSError
                errorWithDomain:@"EssayPodDesktop"
                code:13
                userInfo:@{
                    NSLocalizedDescriptionKey:
                        @"前端依赖尚未安装。请先在 EssayPod/web_ui 中运行 npm install。"
                }];
        }
        return NO;
    }

    NSURL *npm = [self npmExecutable:error];
    if (!npm) {
        return NO;
    }
    NSTask *task = [[NSTask alloc] init];
    task.executableURL = npm;
    task.arguments = @[@"run", @"build"];
    task.currentDirectoryURL = webRoot;
    task.environment = [self baseEnvironment];
    ManagedProcess *managed = [self
        launchTask:task
        logName:@"frontend-build.log"
        error:error];
    if (!managed) {
        return NO;
    }
    [self addManagedProcess:managed];
    [task waitUntilExit];
    [self removeManagedProcess:managed];
    [managed.logHandle closeFile];
    if (task.terminationStatus != 0) {
        if (error) {
            *error = [NSError
                errorWithDomain:@"EssayPodDesktop"
                code:14
                userInfo:@{
                    NSLocalizedDescriptionKey:
                        @"应用界面构建失败。详细信息已写入 frontend-build.log。"
                }];
        }
        return NO;
    }
    return YES;
}

- (ManagedProcess *)launchFrontend:(NSError **)error {
    NSURL *npm = [self npmExecutable:error];
    if (!npm) {
        return nil;
    }
    NSTask *task = [[NSTask alloc] init];
    task.executableURL = npm;
    task.arguments = @[@"run", @"start", @"--", @"--port", @"3000"];
    task.currentDirectoryURL =
        [self.projectRoot URLByAppendingPathComponent:@"web_ui" isDirectory:YES];
    task.environment = [self baseEnvironment];
    return [self launchTask:task logName:@"frontend.log" error:error];
}

- (ManagedProcess *)launchTask:(NSTask *)task
                       logName:(NSString *)logName
                         error:(NSError **)error {
    NSURL *logURL = [self.logDirectory URLByAppendingPathComponent:logName];
    if (![[NSFileManager defaultManager] fileExistsAtPath:logURL.path]) {
        [[NSFileManager defaultManager] createFileAtPath:logURL.path contents:nil attributes:nil];
    }
    NSFileHandle *handle = [NSFileHandle fileHandleForWritingToURL:logURL error:error];
    if (!handle) {
        return nil;
    }
    [handle seekToEndOfFile];
    NSString *separator = [NSString stringWithFormat:@"\n\n=== %@ ===\n", NSDate.date];
    [handle writeData:[separator dataUsingEncoding:NSUTF8StringEncoding]];
    task.standardOutput = handle;
    task.standardError = handle;
    if (![task launchAndReturnError:error]) {
        [handle closeFile];
        return nil;
    }
    setpgid(task.processIdentifier, task.processIdentifier);
    ManagedProcess *managed = [[ManagedProcess alloc] init];
    managed.task = task;
    managed.logHandle = handle;
    return managed;
}

- (void)addManagedProcess:(ManagedProcess *)process {
    @synchronized(self.managedProcesses) {
        [self.managedProcesses addObject:process];
    }
}

- (void)removeManagedProcess:(ManagedProcess *)process {
    @synchronized(self.managedProcesses) {
        [self.managedProcesses removeObjectIdenticalTo:process];
    }
}

- (BOOL)fetchURL:(NSString *)address
          status:(NSInteger *)status
            body:(NSString **)body {
    NSString *separator = [address containsString:@"?"] ? @"&" : @"?";
    NSString *uncachedAddress = [address stringByAppendingFormat:
        @"%@desktop_health=%@", separator, NSUUID.UUID.UUIDString];
    NSMutableURLRequest *request = [NSMutableURLRequest
        requestWithURL:[NSURL URLWithString:uncachedAddress]];
    request.timeoutInterval = 2;
    request.cachePolicy = NSURLRequestReloadIgnoringLocalCacheData;
    [request setValue:@"no-cache, no-store" forHTTPHeaderField:@"Cache-Control"];
    dispatch_semaphore_t semaphore = dispatch_semaphore_create(0);
    __block NSData *receivedData = nil;
    __block NSURLResponse *receivedResponse = nil;
    __block NSError *receivedError = nil;
    NSURLSessionConfiguration *configuration =
        NSURLSessionConfiguration.ephemeralSessionConfiguration;
    configuration.URLCache = nil;
    configuration.requestCachePolicy = NSURLRequestReloadIgnoringLocalCacheData;
    NSURLSession *session = [NSURLSession sessionWithConfiguration:configuration];
    NSURLSessionDataTask *task = [session
        dataTaskWithRequest:request
        completionHandler:^(NSData *data, NSURLResponse *response, NSError *error) {
            receivedData = data;
            receivedResponse = response;
            receivedError = error;
            dispatch_semaphore_signal(semaphore);
        }];
    [task resume];
    dispatch_semaphore_wait(semaphore, DISPATCH_TIME_FOREVER);
    [session finishTasksAndInvalidate];
    if (receivedError) {
        return NO;
    }
    if (status) {
        *status = [(NSHTTPURLResponse *)receivedResponse statusCode];
    }
    if (body) {
        *body = [[NSString alloc]
            initWithData:receivedData ?: [NSData data]
            encoding:NSUTF8StringEncoding] ?: @"";
    }
    return YES;
}

- (BOOL)isEssayPodBackendReady {
    NSInteger status = 0;
    NSString *body = nil;
    return [self fetchURL:BackendAddress status:&status body:&body] &&
        status == 200 &&
        [body localizedCaseInsensitiveContainsString:@"EssayPod"];
}

- (BOOL)isEssayPodFrontendReady {
    NSInteger status = 0;
    NSString *body = nil;
    return [self fetchURL:FrontendAddress status:&status body:&body] &&
        status == 200 &&
        [body localizedCaseInsensitiveContainsString:@"EssayPod"];
}

- (BOOL)waitForService:(NSString *)name
               timeout:(NSTimeInterval)timeout
                 check:(BOOL (^)(void))check
                 error:(NSError **)error {
    NSDate *deadline = [NSDate dateWithTimeIntervalSinceNow:timeout];
    while ([deadline timeIntervalSinceNow] > 0) {
        if (self.terminating) {
            return NO;
        }
        if (check()) {
            return YES;
        }
        [NSThread sleepForTimeInterval:0.25];
    }
    if (error) {
        *error = [NSError
            errorWithDomain:@"EssayPodDesktop"
            code:15
            userInfo:@{
                NSLocalizedDescriptionKey: [NSString stringWithFormat:
                    @"%@未能在 %ld 秒内启动。请查看 EssayPod 日志。",
                    name,
                    (long)timeout]
            }];
    }
    return NO;
}

- (void)presentStartupError:(NSError *)error {
    if (self.terminating) {
        return;
    }
    NSString *diagnostic = [NSString stringWithFormat:
        @"%@\nDomain: %@\nCode: %ld\n",
        error.localizedDescription ?: @"未知错误",
        error.domain ?: @"",
        (long)error.code];
    NSURL *diagnosticURL = [self.logDirectory URLByAppendingPathComponent:@"startup-error.log"];
    [diagnostic writeToURL:diagnosticURL atomically:YES encoding:NSUTF8StringEncoding error:nil];
    dispatch_async(dispatch_get_main_queue(), ^{
        NSAlert *alert = [[NSAlert alloc] init];
        alert.alertStyle = NSAlertStyleCritical;
        alert.messageText = @"EssayPod 启动失败";
        alert.informativeText = error.localizedDescription ?: @"未知错误";
        [alert addButtonWithTitle:@"打开日志"];
        [alert addButtonWithTitle:@"退出"];
        NSModalResponse response = [alert runModal];
        if (response == NSAlertFirstButtonReturn) {
            [NSWorkspace.sharedWorkspace openURL:self.logDirectory];
        }
        [NSApp terminate:nil];
    });
}

- (void)webView:(WKWebView *)webView
    runOpenPanelWithParameters:(WKOpenPanelParameters *)parameters
              initiatedByFrame:(WKFrameInfo *)frame
             completionHandler:(void (^)(NSArray<NSURL *> *URLs))completionHandler {
    NSOpenPanel *panel = [NSOpenPanel openPanel];
    panel.canChooseFiles = YES;
    panel.canChooseDirectories = parameters.allowsDirectories;
    panel.allowsMultipleSelection = parameters.allowsMultipleSelection;
    panel.allowsOtherFileTypes = NO;
    panel.allowedContentTypes = @[UTTypePDF];
    panel.prompt = @"选择 PDF";
    panel.message = @"选择要导入 EssayPod 的论文 PDF";

    [panel beginSheetModalForWindow:self.window completionHandler:^(NSModalResponse result) {
        if (result == NSModalResponseOK) {
            completionHandler(panel.URLs);
        } else {
            completionHandler(nil);
        }
    }];
}

- (void)webView:(WKWebView *)webView
    decidePolicyForNavigationAction:(WKNavigationAction *)navigationAction
                    decisionHandler:(void (^)(WKNavigationActionPolicy))decisionHandler {
    NSURL *url = navigationAction.request.URL;
    if (!url) {
        decisionHandler(WKNavigationActionPolicyCancel);
        return;
    }
    if (navigationAction.shouldPerformDownload || [self isEssayPodDownloadURL:url]) {
        decisionHandler(WKNavigationActionPolicyDownload);
        return;
    }
    if (!navigationAction.targetFrame) {
        [NSWorkspace.sharedWorkspace openURL:url];
        decisionHandler(WKNavigationActionPolicyCancel);
        return;
    }
    NSString *host = url.host;
    if (host.length > 0 &&
        ![host isEqualToString:@"127.0.0.1"] &&
        ![host isEqualToString:@"localhost"]) {
        [NSWorkspace.sharedWorkspace openURL:url];
        decisionHandler(WKNavigationActionPolicyCancel);
        return;
    }
    decisionHandler(WKNavigationActionPolicyAllow);
}

- (BOOL)isEssayPodDownloadURL:(NSURL *)url {
    NSString *host = url.host.lowercaseString;
    if (![host isEqualToString:@"127.0.0.1"] && ![host isEqualToString:@"localhost"]) {
        return NO;
    }
    NSString *path = url.path.lowercaseString;
    return [path hasSuffix:@"/translation/pdf"] ||
        [path hasSuffix:@"/translation/reading-pdf"] ||
        [path hasSuffix:@"/translation/deepl-pdf"];
}

- (void)trackDownload:(WKDownload *)download {
    download.delegate = self;
    [self.activeDownloads addObject:download];
}

- (void)webView:(WKWebView *)webView
    navigationAction:(WKNavigationAction *)navigationAction
    didBecomeDownload:(WKDownload *)download {
    [self trackDownload:download];
}

- (void)webView:(WKWebView *)webView
    navigationResponse:(WKNavigationResponse *)navigationResponse
    didBecomeDownload:(WKDownload *)download {
    [self trackDownload:download];
}

- (void)download:(WKDownload *)download
    decideDestinationUsingResponse:(NSURLResponse *)response
                 suggestedFilename:(NSString *)suggestedFilename
                  completionHandler:(void (^)(NSURL * _Nullable destination))completionHandler {
    NSSavePanel *panel = [NSSavePanel savePanel];
    panel.canCreateDirectories = YES;
    panel.nameFieldStringValue = suggestedFilename.length > 0
        ? suggestedFilename
        : @"EssayPod-translation.pdf";
    NSURL *downloadsDirectory = [[NSFileManager.defaultManager
        URLsForDirectory:NSDownloadsDirectory
        inDomains:NSUserDomainMask] firstObject];
    if (downloadsDirectory) {
        panel.directoryURL = downloadsDirectory;
    }
    if ([response.MIMEType.lowercaseString isEqualToString:@"application/pdf"]) {
        panel.allowedContentTypes = @[UTTypePDF];
    }
    [panel beginSheetModalForWindow:self.window completionHandler:^(NSModalResponse result) {
        if (result != NSModalResponseOK || !panel.URL) {
            completionHandler(nil);
            return;
        }
        NSURL *destination = panel.URL;
        if ([NSFileManager.defaultManager fileExistsAtPath:destination.path]) {
            [NSFileManager.defaultManager removeItemAtURL:destination error:nil];
        }
        completionHandler(destination);
    }];
}

- (void)downloadDidFinish:(WKDownload *)download {
    [self.activeDownloads removeObject:download];
}

- (void)download:(WKDownload *)download
    didFailWithError:(NSError *)error
          resumeData:(NSData *)resumeData {
    [self.activeDownloads removeObject:download];
    if (error.code == NSURLErrorCancelled) {
        return;
    }
    NSAlert *alert = [[NSAlert alloc] init];
    alert.alertStyle = NSAlertStyleWarning;
    alert.messageText = @"下载失败";
    alert.informativeText = error.localizedDescription ?: @"无法保存译文 PDF。";
    [alert beginSheetModalForWindow:self.window completionHandler:nil];
}

- (WKWebView *)webView:(WKWebView *)webView
    createWebViewWithConfiguration:(WKWebViewConfiguration *)configuration
               forNavigationAction:(WKNavigationAction *)navigationAction
                    windowFeatures:(WKWindowFeatures *)windowFeatures {
    NSURL *url = navigationAction.request.URL;
    if (url) {
        [NSWorkspace.sharedWorkspace openURL:url];
    }
    return nil;
}

@end

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        NSApplication *application = NSApplication.sharedApplication;
        AppDelegate *delegate = [[AppDelegate alloc] init];
        application.delegate = delegate;
        [application run];
    }
    return 0;
}
