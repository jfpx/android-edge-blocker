# Android Edge Blocker - Troubleshooting & Development Guide

本文档记录了开发过程中遇到的所有问题、解决方案和最佳实践，帮助其他开发者避免同样的坑。

## 项目概述

**需求:** 为老年用户开发一个 Android 应用,遮挡屏幕顶部和底部各 400px 区域,防止意外触摸边缘。

**最终方案:** 从零构建自定义 Android 应用（Simple Edge Blocker）,使用 WindowManager 覆盖层实现。

**关键技术:**
- WindowManager overlay (TYPE_APPLICATION_OVERLAY)
- Foreground Service
- Boot BroadcastReceiver
- SharedPreferences

---

## 开发环境配置

### 实际使用的构建环境

本项目在以下环境成功构建和测试：

```
操作系统：Windows 11
Java 版本：OpenJDK 17.0.12
Gradle 版本：8.10 (via wrapper)
Android Gradle Plugin：8.5.0
Android SDK Build Tools：34.0.0
Android SDK Platform：API 34 (Android 14)

构建命令：
  gradlew.bat clean
  gradlew.bat assembleDebug

输出 APK：
  app/build/outputs/apk/debug/app-debug.apk (约 2.9 MB)
```

### 版本约束（重要）

| 组件 | 约束 | 原因 |
|------|------|------|
| **Java** | 必须 17-23 | Gradle 8.10 不支持 Java 26+ |
| **Gradle** | 8.10 | AGP 8.5 要求 Gradle 8.7+ |
| **compileSdk** | 34 | 使用最新 API |
| **targetSdk** | 30 | 避免 Android 12+ 的额外限制（更好兼容性） |
| **minSdk** | 23 | Android 6.0+（覆盖 95% 设备） |

### 依赖下载注意事项

首次构建需要下载约 **500MB 依赖**（Gradle、AGP、AndroidX 库等）：

```
下载来源：
  google()       - Google Maven 仓库
  mavenCentral() - Maven Central

国内用户建议使用镜像（可选）：
  修改 build.gradle 中的 repositories 部分
  阿里云镜像：maven { url 'https://maven.aliyun.com/repository/google' }
```

---

## Android 覆盖层完整实现

### 1. 核心 Service 代码

```java
public class EdgeBlockService extends Service {
    
    private WindowManager windowManager;
    private FrameLayout topBlockView;
    private FrameLayout bottomBlockView;
    
    private static final int BLOCK_HEIGHT = 400; // 可配置
    
    @Override
    public void onCreate() {
        super.onCreate();
        
        // 前台服务（防止被杀）
        createNotificationChannel();
        startForeground(NOTIFICATION_ID, createNotification());
        
        // 创建覆盖层
        windowManager = (WindowManager) getSystemService(WINDOW_SERVICE);
        createBlockViews();
    }
    
    private void createBlockViews() {
        // 顶部
        topBlockView = createBlockView();
        WindowManager.LayoutParams topParams = createLayoutParams();
        topParams.gravity = Gravity.TOP | Gravity.START;
        windowManager.addView(topBlockView, topParams);
        
        // 底部
        bottomBlockView = createBlockView();
        WindowManager.LayoutParams bottomParams = createLayoutParams();
        bottomParams.gravity = Gravity.BOTTOM | Gravity.START;
        windowManager.addView(bottomBlockView, bottomParams);
    }
    
    private FrameLayout createBlockView() {
        FrameLayout view = new FrameLayout(this);
        // 生产版本：10% 灰色透明（几乎不可见）
        view.setBackgroundColor(Color.argb(26, 128, 128, 128));
        return view;
    }
    
    private WindowManager.LayoutParams createLayoutParams() {
        WindowManager.LayoutParams params = new WindowManager.LayoutParams();
        
        // 类型
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            params.type = WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY;
        } else {
            params.type = WindowManager.LayoutParams.TYPE_SYSTEM_ALERT;
        }
        
        // 标志（关键）
        params.flags = WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                     | WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL
                     | WindowManager.LayoutParams.FLAG_WATCH_OUTSIDE_TOUCH
                     | WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN
                     | WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS;
        
        params.format = PixelFormat.TRANSLUCENT;
        params.width = WindowManager.LayoutParams.MATCH_PARENT;
        params.height = BLOCK_HEIGHT;
        
        return params;
    }
    
    @Override
    public void onDestroy() {
        super.onDestroy();
        if (topBlockView != null) {
            windowManager.removeView(topBlockView);
        }
        if (bottomBlockView != null) {
            windowManager.removeView(bottomBlockView);
        }
    }
}
```

### 2. AndroidManifest.xml 必需配置

```xml
<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android">
    
    <!-- 必需权限 -->
    <uses-permission android:name="android.permission.SYSTEM_ALERT_WINDOW" />
    <uses-permission android:name="android.permission.FOREGROUND_SERVICE" />
    <uses-permission android:name="android.permission.RECEIVE_BOOT_COMPLETED" />
    
    <application
        android:icon="@mipmap/ic_launcher"
        android:label="Simple Edge Blocker">
        
        <!-- 主界面 -->
        <activity
            android:name=".MainActivity"
            android:exported="true">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>
        
        <!-- 服务 -->
        <service
            android:name=".EdgeBlockService"
            android:enabled="true"
            android:exported="false" />
        
        <!-- 开机自启动 -->
        <receiver
            android:name=".BootReceiver"
            android:enabled="true"
            android:exported="true">
            <intent-filter>
                <action android:name="android.intent.action.BOOT_COMPLETED" />
            </intent-filter>
        </receiver>
    </application>
</manifest>
```

### 3. 开机自启动

```java
public class BootReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        if (Intent.ACTION_BOOT_COMPLETED.equals(intent.getAction())) {
            // 检查用户偏好
            SharedPreferences prefs = context.getSharedPreferences("EdgeBlockerPrefs", Context.MODE_PRIVATE);
            boolean autoStart = prefs.getBoolean("auto_start_enabled", true);
            
            if (autoStart) {
                Intent serviceIntent = new Intent(context, EdgeBlockService.class);
                context.startService(serviceIntent);
            }
        }
    }
}
```

### 4. MainActivity 权限检查

```java
private boolean checkOverlayPermission() {
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
        return Settings.canDrawOverlays(this);
    }
    return true;
}

private void requestOverlayPermission() {
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
        Intent intent = new Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                Uri.parse("package:" + getPackageName()));
        startActivityForResult(intent, REQUEST_CODE_OVERLAY_PERMISSION);
    }
}
```

---

## 调试最佳实践

### 1. UI 实时状态显示

在 MainActivity 添加状态 TextView,显示：
- 服务运行状态
- Android 版本
- 权限状态
- **错误信息**

```java
StringBuilder status = new StringBuilder();
status.append("服务状态: ").append(EdgeBlockService.isRunning ? "运行中 ✓" : "已停止 ✗").append("\n");
status.append("Android 版本: ").append(Build.VERSION.RELEASE).append("\n");
status.append("悬浮窗权限: ").append(checkOverlayPermission() ? "已授予 ✓" : "未授予 ✗").append("\n");

if (EdgeBlockService.lastError != null) {
    status.append("\n错误信息:\n").append(EdgeBlockService.lastError);
}

tvStatus.setText(status.toString());
```

### 2. 完整的日志输出

所有关键操作都输出到 LogCat：

```java
private static final String TAG = "EdgeBlockService";

Log.i(TAG, "服务创建中...");
Log.i(TAG, "创建覆盖层视图...");
Log.i(TAG, "顶部视图参数: width=" + params.width + ", height=" + params.height);
Log.i(TAG, "顶部覆盖层已添加");
Log.e(TAG, "创建覆盖层失败", e);
```

**查看日志:**
```bash
adb logcat -s EdgeBlockService:I MainActivity:I
```

### 3. 静态变量暴露状态

在 Service 中添加静态变量,供 Activity 读取：

```java
public class EdgeBlockService extends Service {
    public static boolean isRunning = false;
    public static String lastError = null;
    
    @Override
    public void onCreate() {
        try {
            // ... 初始化逻辑
            isRunning = true;
            lastError = null;
        } catch (Exception e) {
            lastError = "创建失败: " + e.getMessage();
            isRunning = false;
        }
    }
}
```

### 4. 使用明显的调试颜色

开发阶段使用红色,确认可见后再改为微透明：

```java
// 调试（鲜艳颜色,容易看到）
view.setBackgroundColor(Color.argb(128, 255, 0, 0)); // 50% 红色

// 生产（几乎不可见）
view.setBackgroundColor(Color.argb(26, 128, 128, 128)); // 10% 灰色
```

---

## 构建配置

### build.gradle (app)

```gradle
android {
    namespace 'com.simple.edgeblocker'
    compileSdk 34
    
    defaultConfig {
        applicationId "com.simple.edgeblocker"
        minSdk 23        // Android 6.0
        targetSdk 30     // Android 11（兼容性好）
        versionCode 6
        versionName "2.0"
    }
    
    buildTypes {
        release {
            minifyEnabled false
        }
    }
}

dependencies {
    implementation 'androidx.appcompat:appcompat:1.6.1'
}
```

### Java 版本要求

- **Gradle 8.10 只支持 Java 17-23**
- Java 26 不兼容 → 下载 Java 17

---

## Windows 开发环境

### 1. UTF-8 编码
```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"
```

### 2. Android SDK 安装
```bash
# 下载 Command Line Tools
# 解压到自己的 Android SDK 安装目录（ANDROID_HOME）

# 安装组件
sdkmanager "build-tools;34.0.0" "platforms;android-34" "platform-tools"
```

### 3. 构建命令
```powershell
# 先将 ANDROID_HOME 和 JAVA_HOME 设置为本机 SDK、JDK 的安装目录
# 将 PROJECT_ROOT 设置为自己的项目检出目录

Set-Location $env:PROJECT_ROOT
.\gradlew.bat assembleDebug
```

**输出:** `app\build\outputs\apk\debug\app-debug.apk`

---

## 版本演进历史

| 版本 | 修复内容 |
|------|---------|
| v1.0 | 初始实现（无图标,无前台服务权限） |
| v1.2 | 添加 PNG mipmap 图标（修复安装失败） |
| v1.3 | 添加调试日志和 UI 状态显示 |
| v1.4 | 添加 FOREGROUND_SERVICE 权限（修复启动失败） |
| v1.5 | 添加自动启动开关 |
| v2.0 | 生产版本：10% 灰色透明（几乎不可见） |

---

## 快速参考

### 检查 APK 图标
```bash
aapt dump badging app.apk | grep icon
unzip -l app.apk | grep mipmap
```

### 查看日志
```bash
adb logcat -s EdgeBlockService:I MainActivity:I
adb logcat -s EdgeBlockService:I MainActivity:I -d > log.txt
```

### 安装/卸载
```bash
adb install -r android-edge-blocker.apk
adb uninstall com.simple.edgeblocker
```

### 验证权限
```bash
aapt dump badging app.apk | grep uses-permission
```

---

## 项目文件结构

```
simple-edge-blocker/
├── app/
│   ├── build.gradle
│   └── src/main/
│       ├── AndroidManifest.xml
│       ├── java/com/simple/edgeblocker/
│       │   ├── MainActivity.java          # UI 和权限管理
│       │   ├── EdgeBlockService.java      # 核心覆盖层服务
│       │   └── BootReceiver.java          # 开机自启动
│       └── res/
│           ├── layout/activity_main.xml   # UI 布局
│           ├── values/                    # 字符串、样式
│           └── mipmap-*/ic_launcher.png   # 应用图标（5 套）
├── build.gradle
├── gradle/wrapper/
├── gradlew.bat
└── android-edge-blocker.apk                  # 最终 APK
```

---

## 联系与支持

**项目位置:** 自己的项目检出目录（`$env:PROJECT_ROOT`）

**最终 APK:** 项目根目录下的 `android-edge-blocker.apk`

**问题排查流程:**
1. 检查 UI 状态框的错误信息
2. 查看 LogCat 日志
3. 验证权限是否授予
4. 检查 Android 版本兼容性

---

## 开发过程中遇到的问题（重要）

### 问题 1：APK 无法安装（静默失败）

**现象：** 点击 APK 安装时没有任何错误提示，直接消失

**根本原因：** 缺少 PNG 格式的应用图标

**详细说明：**
- AndroidManifest.xml 只配置了 Adaptive Icon（XML 格式）：`android:icon="@drawable/ic_launcher"`
- Adaptive Icon 只在 Android 8.0+ 支持
- Android 6.0-7.1 无法解析 XML 图标 → 安装器静默失败（无错误提示）

**解决方案：**
1. 生成 5 套 PNG 图标：
   ```
   res/mipmap-mdpi/ic_launcher.png     (48x48)
   res/mipmap-hdpi/ic_launcher.png     (72x72)
   res/mipmap-xhdpi/ic_launcher.png    (96x96)
   res/mipmap-xxhdpi/ic_launcher.png   (144x144)
   res/mipmap-xxxhdpi/ic_launcher.png  (192x192)
   ```

2. 修改 AndroidManifest.xml：
   ```xml
   android:icon="@mipmap/ic_launcher"  <!-- 从 @drawable 改为 @mipmap -->
   ```

3. 保留 Adaptive Icon 作为备用（Android 8.0+ 会优先使用）

**验证命令：**
```bash
unzip -l app.apk | grep mipmap  # 检查 PNG 图标是否存在
aapt dump badging app.apk | grep icon  # 查看图标配置
```

---

### 问题 2：应用启动后崩溃（Permission Denial）

**现象：** 应用安装成功，点击"启动遮挡"后立即崩溃

**错误日志：**
```
Permission Denial: startForeground from pid=xxx uid=xxx 
requires android.permission.FOREGROUND_SERVICE
```

**根本原因：** AndroidManifest.xml 缺少 `FOREGROUND_SERVICE` 权限

**详细说明：**
- Android 9.0 (API 28) 引入强制要求：调用 `startForeground()` 必须声明权限
- 这是 **manifest 权限**（不是 runtime 权限），用户无需手动授予
- 如果缺少此权限，系统会在 Service 启动时立即抛出 SecurityException

**解决方案：**
在 AndroidManifest.xml 添加权限：
```xml
<uses-permission android:name="android.permission.FOREGROUND_SERVICE" />
```

**与其他权限的区别：**
- `SYSTEM_ALERT_WINDOW` - **Runtime 权限**，需要用户手动授予（跳转到设置页面）
- `RECEIVE_BOOT_COMPLETED` - Manifest 权限，自动授予
- `FOREGROUND_SERVICE` - Manifest 权限，自动授予

---

### 问题 3：Gradle 构建失败（Unsupported class file major version）

**现象：** 运行 `gradlew.bat assembleDebug` 时报错

**错误信息：**
```
Unsupported class file major version 70
```

**根本原因：** 使用了 Java 26+，但 Gradle 8.10 只支持 Java 17-23

**详细说明：**
- Class file major version 对应关系：
  - 70 = Java 26
  - 65 = Java 21
  - 61 = Java 17
- Gradle 8.10 的 Java 支持范围：17-23
- Android Gradle Plugin 8.5.0 要求 Gradle 8.7+

**解决方案：**
1. 检查当前 Java 版本：
   ```bash
   java -version
   ```

2. 下载并安装 Java 17 或 21：
   - 推荐来源：https://adoptium.net/temurin/releases/
   - 选择 JDK 17 LTS 或 JDK 21 LTS

3. 设置环境变量：
   ```powershell
   $env:JAVA_HOME = "D:\path\to\jdk-17.0.13+11"
   ```

4. 重新构建：
   ```bash
   gradlew.bat clean
   gradlew.bat assembleDebug
   ```

---

### 问题 4：修改现有 APK 失败（apktool 不兼容）

**背景：** 最初尝试修改 Edge Block APK（改宽度限制从 200px 到 500px），但失败

**现象：**
- 用 apktool 反编译成功
- 修改 XML 配置后重新编译成功
- 签名后安装成功
- **但启动时崩溃：** `Resources.NotFoundException`

**根本原因：** apktool 与 R8 代码混淆不兼容

**详细说明：**
1. Edge Block 使用 R8 混淆（`minifyEnabled true`）
2. R8 混淆后的代码直接引用**硬编码的资源 ID**（如 `0x7f080001`）
3. apktool 重新编译时会**重新生成 resources.arsc**
4. 新的资源 ID 与混淆代码中的硬编码 ID 不匹配
5. 运行时访问资源 → `Resources.NotFoundException`

**尝试过的方案（均失败）：**
- ✗ 使用旧版 apktool 2.9.3
- ✗ 使用 `--use-aapt2` 标志
- ✗ 只修改 XML 不修改代码

**最终方案：**
放弃修改现有 APK，**从零构建新应用**（本项目）

**教训：**
- **apktool 只适合修改未混淆的 APK**
- 对于混淆后的 APK，必须从源码修改（如果有）
- 或者重新实现功能（如果无源码）

---

### 问题 5：首次构建极慢（依赖下载）

**现象：** 首次运行 `gradlew.bat assembleDebug` 卡在 "Resolving dependencies" 超过 10 分钟

**原因：** 需要下载大量依赖（约 500MB）

**依赖来源：**
- Gradle 8.10 本身（~150MB）
- Android Gradle Plugin 8.5.0（~50MB）
- AndroidX AppCompat 1.6.1（~30MB）
- 其他传递依赖（~270MB）

**解决方案：**
1. **耐心等待**（首次构建需要 15-30 分钟，取决于网络速度）
2. 使用国内镜像（可选）：
   ```gradle
   // 在 build.gradle 的 repositories 中添加：
   maven { url 'https://maven.aliyun.com/repository/google' }
   maven { url 'https://maven.aliyun.com/repository/public' }
   ```

3. 使用 Android Studio（推荐）：
   - Android Studio 会自动管理 SDK 和依赖
   - 有进度条显示下载状态
   - 首次同步完成后缓存所有依赖

**后续构建速度：**
- 清理构建：~30 秒
- 增量构建：~10 秒

---

## 标签

`#android` `#windowmanager` `#overlay` `#foreground-service` `#elderly-accessibility` `#edge-blocker`
