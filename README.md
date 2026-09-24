# Android Edge Blocker

**English** | [中文](#中文版本)

---

## Overview

A simple Android app that prevents accidental edge touches on smartphones, specifically designed for elderly users or those with trembling hands.

**One-sentence summary:** Adds semi-transparent overlays at screen edges to provide visual cues that help users naturally avoid accidental touches.

## Why This App?

**Problem:**
- Elderly users frequently trigger **accidental edge touches** on large-screen phones:
  - Unintended back navigation
  - Accidental notification panel opening
  - Triggering edge gestures (swipe from edge)
  - Accidentally closing apps in use
- Damaged USB port (water damage/aging) prevents adb debugging
- Root access not desired
- Existing similar apps are either paid, overly complex, or no longer maintained

**Solution:**
- Adds **semi-transparent overlays** (10% gray, barely visible) at **top 400px** and **bottom 400px**
- Users can see the overlay, but touches **pass through** (technically transparent to touch)
- **Psychological cue effect**: Users naturally avoid edge areas when seeing the overlay
- Auto-starts on boot, no manual activation needed

## Core Features

✅ **Edge Blocking**
- Top 400px + Bottom 400px (customizable in code)
- 10% gray transparency (barely visible, but provides visual cue)

✅ **Minimal Operation**
- Install → Open → Grant Permission → Start → Done
- No complex settings, no ads, no background uploads

✅ **Auto-start on Boot**
- Automatically restores overlay after reboot
- Persistent background service

❌ **No Bloat**
- No settings page (follows UNIX philosophy: do one thing well)
- No notifications (silent operation)
- No network permission (completely offline)

## Quick Start

### 1. Download APK

Download the APK file directly from the repository:
- **Filename:** `android-edge-blocker.apk`
- **Size:** ~3 MB
- **Location:** Repository root directory

### 2. Installation

1. Transfer APK to Android device (cloud drive/WiFi/Bluetooth)
2. Tap to install
3. Allow "Install unknown apps" (enable in settings)

### 3. Usage

1. Open the app
2. Tap "Start Blocking"
3. Grant "Display over other apps" permission (redirects to system settings)
4. Return to app, tap "Start Blocking" again
5. ✅ Done! Very faint gray areas appear at screen top and bottom

### 4. Verify Effect

- Press Home button → Overlay persists
- Open any app → Overlay covers all content
- Reboot device → Overlay auto-restores

### 5. Stop Blocking (Optional)

Reopen app → Tap "Stop Blocking"

## Customization (Developers)

To modify overlay parameters, edit `app/src/main/java/com/simple/edgeblocker/EdgeBlockService.java`:

```java
// Modify block height (pixels)
private static final int BLOCK_HEIGHT = 400;  // Change to 500, 600, etc.

// Modify color and transparency
view.setBackgroundColor(Color.argb(26, 128, 128, 128));  // ARGB: Alpha, R, G, B
// 26 = 10% transparency (range 0-255)
// Change to 51 = 20%, 77 = 30%, 128 = 50%
```

After modification, rebuild APK (see "Build from Source" below).

## Build from Source

> 💡 **Detailed Build Guide:** For complete steps, actual commands, and troubleshooting, see [`BUILD_STEP_BY_STEP.md`](BUILD_STEP_BY_STEP.md)

### Environment Requirements

Tested with the following environment:

| Component | Version | Notes |
|-----------|---------|-------|
| **JDK** | 17.0.x | **Must be Java 17-23** (Gradle 8.10 doesn't support Java 26+) |
| **Gradle** | 8.10 | Gradle Wrapper included, no separate installation needed |
| **Android SDK** | - | Requires the following components: |
| ├─ Build Tools | 34.0.0 | Compilation tools |
| ├─ Platform | API 34 (Android 14) | compileSdk target |
| └─ Platform | API 23 (Android 6.0) | minSdk minimum version |
| **Android Gradle Plugin** | 8.5.0 | Configured in build.gradle |

### Quick Environment Check

Before building:

```bash
# Check Java version (must be 17-23)
java -version
# Should display: openjdk version "17.x.x" or "21.x.x"

# Check Android SDK (if building from command line)
echo $ANDROID_HOME   # Linux/Mac
echo %ANDROID_HOME%  # Windows

# Or use Android Studio (recommended, auto-manages SDK)
```

### Build Steps

#### Method 1: Command Line Build

```bash
# 1. Clone repository
git clone https://github.com/jfpx/android-edge-blocker.git
cd android-edge-blocker

# 2. Clean previous builds
./gradlew clean       # Linux/Mac
gradlew.bat clean     # Windows

# 3. Build Debug version (with debugging info)
./gradlew assembleDebug

# 4. Output location
# app/build/outputs/apk/debug/app-debug.apk
```

**Release version (production):**
```bash
./gradlew assembleRelease
# Output: app/build/outputs/apk/release/app-release-unsigned.apk
# Requires manual signing (or use uber-apk-signer)
```

#### Method 2: Android Studio (Recommended)

1. Download Android Studio: https://developer.android.com/studio
2. First launch auto-downloads Android SDK
3. **File → Open** → Select this project directory
4. Wait for Gradle sync (first time is slow, downloads dependencies)
5. **Build → Build Bundle(s) / APK(s) → Build APK(s)**
6. After build completes, click "locate" in notification to view output file

### Common Issues

#### Issue 1: `Unsupported class file major version 70`

**Cause:** Using Java 26+ (Gradle 8.10 doesn't support it)

**Solution:**
```bash
# Check Java version
java -version

# If Java 26+, install Java 17 or 21
# Download: https://adoptium.net/temurin/releases/
```

#### Issue 2: `Android SDK not found`

**Cause:** `ANDROID_HOME` environment variable not set

**Solution (Windows):**
```powershell
# Set environment variable (replace with your SDK path)
$env:ANDROID_HOME = "C:\Users\YourName\AppData\Local\Android\Sdk"
$env:Path += ";$env:ANDROID_HOME\platform-tools"
```

**Or use Android Studio** (auto-manages SDK)

#### Issue 3: Build stuck at "Resolving dependencies"

**Cause:** Network issues or slow Maven repository connection

**Solution:**
1. Use proxy or mirror repository (modify `build.gradle`)
2. Be patient (first build downloads ~500MB dependencies)

#### Issue 4: APK crashes after installation

**Cause:** Unsigned or incomplete signature

**Solution:**
- Debug APK automatically uses debug.keystore (no manual action needed)
- Release APK requires manual signing:
  ```bash
  # Use uber-apk-signer (auto v1+v2+v3 signing)
  java -jar uber-apk-signer.jar -a app-release-unsigned.apk
  ```

### Dependencies

Very lightweight project, only 1 external dependency:

```gradle
dependencies {
    implementation 'androidx.appcompat:appcompat:1.6.1'  // Basic UI library
}
```

**Not required:**
- ❌ No Google Play Services
- ❌ No Firebase
- ❌ No other third-party libraries

### Version History

| Version | versionCode | Key Changes |
|---------|-------------|-------------|
| v2.0 | 6 | Production version (10% gray transparency) |
| v1.5 | 5 | Added auto-start on boot |
| v1.4 | 4 | Fixed FOREGROUND_SERVICE permission issue |
| v1.3 | 3 | Added debug logging and status display |
| v1.2 | 2 | Fixed PNG icon missing causing install failure |
| v1.0 | 1 | Initial version |

## Permissions

| Permission | Purpose | Necessity |
|------------|---------|-----------|
| `SYSTEM_ALERT_WINDOW` | Display floating overlay | Required |
| `FOREGROUND_SERVICE` | Keep service running | Required (Android 9+) |
| `RECEIVE_BOOT_COMPLETED` | Auto-start on boot | Optional |

**No network permission** - App is completely offline, no data uploads.

## Compatibility

- **Minimum:** Android 6.0 (API 23)
- **Target:** Android 11 (API 30)
- **Testing:** Should be tested on target device

## Troubleshooting

### Issue 1: Overlay not showing

**Cause:** Overlay permission not granted

**Solution:**
1. Settings → Apps → Android Edge Blocker → Permissions
2. Enable "Display over other apps"

### Issue 2: Not auto-starting after reboot

**Cause:** Manufacturer restriction on background auto-start (Xiaomi/Huawei/OPPO/Vivo, etc.)

**Solution:**
1. Settings → Apps → Android Edge Blocker → Auto-start management
2. Allow auto-start
3. Settings → Battery → Background activity → Allow background activity

### Issue 3: Overlay too visible/not visible enough

**Solution:** Modify transparency parameter in `EdgeBlockService.java` (see "Customization")

### Issue 4: Want to block left/right edges

**Solution:** Modify `createLayoutParams()` method to add left/right overlays (reference top/bottom implementation)

## Technical Implementation

### WindowManager Overlay

Uses Android's `TYPE_APPLICATION_OVERLAY` to create system-level floating window:

```java
// Key flag combination
params.flags = WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE      // Don't get focus
             | WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL     // Touch pass-through
             | WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS;   // Prevent clipping
```

### Foreground Service Keep-Alive

```java
// Prevent being killed by system
startForeground(NOTIFICATION_ID, createNotification());
```

### Auto-start on Boot

```java
// BootReceiver listens for BOOT_COMPLETED broadcast
@Override
public void onReceive(Context context, Intent intent) {
    if (Intent.ACTION_BOOT_COMPLETED.equals(intent.getAction())) {
        context.startService(new Intent(context, EdgeBlockService.class));
    }
}
```

## Overlay Coverage Details

### Permission Requirement

**Must grant "Display over other apps" permission (Overlay Permission):**
- Settings → Apps → Android Edge Blocker → Display over other apps
- First launch auto-redirects to permission settings page
- Overlay won't display without authorization

### Coverage Scope

**✅ Can block:**
- **Third-party apps** - Tested on:
  - Libre FreeStyle (Abbott glucose monitoring app)
  - Overlay covers app interface top layer, effectively prevents accidental touches
- **Theoretically supports all third-party apps** (not comprehensively tested)

**❌ Cannot block (system limitations):**
- **Bottom navigation bar** (3 buttons: Back/Home/Recents)
  - Android system protection layer, no third-party app can overlay
  - Purpose: Prevent malicious app hijacking
- **Top status bar/notification panel**
  - When pulling down notification panel, overlay is covered by system UI
  - But overlay still effective when displayed within apps
- **System settings pages** (Settings)
  - Android security mechanism to prevent phishing attacks
  - Overlay temporarily invisible when adjusting settings

### Typical Use Case

**Tested scenario: Blood glucose monitoring**
- Elderly person uses Libre FreeStyle to view glucose data
- ✅ Overlay covers screen top 400px + bottom 400px
- ✅ Fingers won't accidentally touch edge causing unexpected exit when viewing data
- ✅ Overlay auto-restores after device reboot

**Other potential scenarios** (not tested):
- Reading apps (prevent page-turn accidental touches)
- Video apps (prevent swipe accidental touches)
- Browser apps (prevent accidental touches on address bar/toolbar)

### Overlay Behavior

- **Touch pass-through**: Overlay doesn't intercept touch events, only provides visual cue
- **Psychological cue**: Users naturally avoid seeing faint gray area
- **Always displayed**: Overlays all blockable apps, won't be obscured
- **System-level permission**: Uses `TYPE_APPLICATION_OVERLAY` for global coverage

## Project Structure

```
android-edge-blocker/
├── android-edge-blocker.apk       # Directly installable APK
├── app/
│   ├── build.gradle               # App build configuration
│   └── src/main/
│       ├── AndroidManifest.xml    # Manifest file (permissions, components)
│       ├── java/com/simple/edgeblocker/
│       │   ├── MainActivity.java        # Main interface (start/stop buttons)
│       │   ├── EdgeBlockService.java    # Blocking service (core logic)
│       │   └── BootReceiver.java        # Boot receiver
│       └── res/
│           ├── layout/activity_main.xml     # UI layout
│           ├── values/strings.xml           # String resources
│           ├── mipmap-*/ic_launcher.png     # App icons (5 sets)
│           └── drawable/ic_launcher.xml     # Adaptive Icon
├── build.gradle                   # Project build configuration
├── settings.gradle                # Gradle settings
├── BUILD_STEP_BY_STEP.md          # Complete build guide (from scratch, with all actual commands)
├── TROUBLESHOOTING.md             # Development experience and troubleshooting guide
└── README.md                      # This document
```

## License

**MIT License** - Free to use, modify, and distribute

## AI-Friendly

This project's code is concise and clear (<500 lines Java), perfect for AI-assisted modifications:

**Common requests with GitHub Copilot / Claude / ChatGPT:**
- "Change blocking height to 500px"
- "Change to block left/right edges instead of top/bottom"
- "Add settings page for user-adjustable transparency"
- "Add whitelist feature to disable blocking in certain apps"
- "Add timer feature (e.g., only enable after 8 PM)"

**5 minutes to modify → rebuild APK → install and use**

## Related Projects

- **Original project:** Edge Block (no longer maintained, APK modification failed leading to creation of this project)
- **Technical documentation:** `TROUBLESHOOTING.md` (problems encountered during development and solutions)

## Acknowledgments

This project was created to solve real pain points for elderly users. Thanks to all open-source community contributors.

---

**Version:** v2.0  
**Last Updated:** 2026-09-03  
**Use Cases:** Elderly users, users with trembling hands, large-screen phones with serious accidental touches, devices with damaged USB preventing adb debugging  
**Development Motivation:** Solve real problems for family, open-source sharing for others with same needs

---

# 中文版本

## 概述

一个简易的 Android 应用，防止智能手机边缘的意外触摸，专为老年用户或手抖用户设计。

**一句话说明：** 在屏幕边缘添加半透明遮挡层，提供视觉提示，帮助用户自然地避免意外触摸。

## 为什么需要这个应用？

**问题场景：**
- 老年人使用大屏手机时，经常**误触屏幕边缘**导致：
  - 意外返回上一页
  - 不小心打开通知栏
  - 触发边缘手势（如侧边栏、返回等）
  - 导致正在使用的应用被意外关闭
- 手机 USB 口损坏（进水/老化）无法使用 adb 调试
- 不想 root 手机
- 市面上的类似应用要么收费，要么功能复杂，要么已停止维护

**本应用的解决方案：**
- 在屏幕**顶部 400px** 和**底部 400px** 区域添加**半透明遮挡层**（10% 灰色，几乎不可见）
- 用户视觉上能看到遮挡，但遮挡层**不拦截触摸**（技术上穿透）
- **心理暗示效果**：用户看到遮挡层会**自然避开边缘区域**，大幅减少误触
- 开机自动启动，无需每次手动开启

## 核心功能

✅ **遮挡屏幕边缘**
- 顶部 400px + 底部 400px（可代码自定义）
- 10% 灰色透明（几乎不可见，但能提示用户）

✅ **极简操作**
- 安装 → 打开 → 授权 → 启动 → 完成
- 无复杂设置，无广告，无后台上传

✅ **开机自启动**
- 重启后自动恢复遮挡
- 后台常驻服务

❌ **无多余功能**
- 无设置页面（遵循 UNIX 哲学：只做一件事）
- 无通知（静默运行）
- 无网络权限（完全离线）

## 快速开始

### 1. 下载 APK

直接下载仓库中的 APK 文件：
- **文件名：** `android-edge-blocker.apk`
- **大小：** 约 3 MB
- **位置：** 仓库根目录

### 2. 安装

1. 将 APK 传输到 Android 设备（云盘/WiFi/蓝牙）
2. 点击安装
3. 允许"安装未知应用"（设置中开启）

### 3. 使用

1. 打开应用
2. 点击"启动遮挡"
3. 授予"显示悬浮窗"权限（跳转到系统设置）
4. 返回应用，再次点击"启动遮挡"
5. ✅ 完成！屏幕顶部和底部会出现极淡的灰色区域

### 4. 验证效果

- 按 Home 键返回桌面 → 遮挡层仍在
- 打开任何应用 → 遮挡层覆盖在所有内容上
- 重启设备 → 遮挡层自动恢复

### 5. 停止遮挡（可选）

重新打开应用 → 点击"停止遮挡"

## 自定义配置（开发者）

如需修改遮挡参数，编辑 `app/src/main/java/com/simple/edgeblocker/EdgeBlockService.java`：

```java
// 修改遮挡高度（像素）
private static final int BLOCK_HEIGHT = 400;  // 改为 500、600 等

// 修改颜色和透明度
view.setBackgroundColor(Color.argb(26, 128, 128, 128));  // ARGB：透明度, R, G, B
// 26 = 10% 透明度（范围 0-255）
// 改为 51 = 20%，77 = 30%，128 = 50%
```

修改后重新构建 APK（见下方"从源码构建"）。

## 从源码构建

> 💡 **详细构建指南：** 完整的步骤、实际命令和常见问题解决方案请参考 [`BUILD_STEP_BY_STEP.md`](BUILD_STEP_BY_STEP.md)

### 环境要求（必读）

本项目在以下环境测试通过：

| 组件 | 版本 | 说明 |
|------|------|------|
| **JDK** | 17.0.x | **必须 Java 17-23**（Gradle 8.10 不支持 Java 26+） |
| **Gradle** | 8.10 | 项目已包含 Gradle Wrapper，无需单独安装 |
| **Android SDK** | - | 需要以下组件： |
| ├─ Build Tools | 34.0.0 | 编译工具 |
| ├─ Platform | API 34 (Android 14) | compileSdk 目标 |
| └─ Platform | API 23 (Android 6.0) | minSdk 最低版本 |
| **Android Gradle Plugin** | 8.5.0 | 已在 build.gradle 配置 |

### 快速环境检查

构建前请确认：

```bash
# 检查 Java 版本（必须是 17-23）
java -version
# 输出应显示：openjdk version "17.x.x" 或 "21.x.x"

# 检查 Android SDK（如果使用命令行构建）
echo $ANDROID_HOME   # Linux/Mac
echo %ANDROID_HOME%  # Windows

# 或直接使用 Android Studio（推荐，自动管理 SDK）
```

### 构建步骤

#### 方法 1：命令行构建

```bash
# 1. 克隆仓库
git clone https://github.com/jfpx/android-edge-blocker.git
cd android-edge-blocker

# 2. 清理之前的构建
./gradlew clean       # Linux/Mac
gradlew.bat clean     # Windows

# 3. 构建 Debug 版本（带调试信息）
./gradlew assembleDebug

# 4. 输出位置
# app/build/outputs/apk/debug/app-debug.apk
```

**Release 版本（生产环境）：**
```bash
./gradlew assembleRelease
# 输出：app/build/outputs/apk/release/app-release-unsigned.apk
# 需要手动签名（或使用 uber-apk-signer）
```

#### 方法 2：Android Studio（推荐）

1. 下载 Android Studio：https://developer.android.com/studio
2. 安装后首次启动会自动下载 Android SDK
3. **File → Open** → 选择本项目目录
4. 等待 Gradle 同步（首次较慢，需下载依赖）
5. **Build → Build Bundle(s) / APK(s) → Build APK(s)**
6. 构建完成后点击通知中的 "locate" 查看输出文件

### 常见问题排查

#### 问题 1：`Unsupported class file major version 70`

**原因：** 使用了 Java 26+（Gradle 8.10 不支持）

**解决：**
```bash
# 检查 Java 版本
java -version

# 如果是 Java 26+，需要安装 Java 17 或 21
# 下载地址：https://adoptium.net/temurin/releases/
```

#### 问题 2：`Android SDK not found`

**原因：** 未设置 `ANDROID_HOME` 环境变量

**解决（Windows）：**
```powershell
# 设置环境变量（替换为你的 SDK 路径）
$env:ANDROID_HOME = "C:\Users\YourName\AppData\Local\Android\Sdk"
$env:Path += ";$env:ANDROID_HOME\platform-tools"
```

**或使用 Android Studio**（自动管理 SDK）

#### 问题 3：构建卡住在 "Resolving dependencies"

**原因：** 网络问题或 Maven 仓库连接慢

**解决：**
1. 使用代理或镜像仓库（修改 `build.gradle`）
2. 耐心等待（首次构建需要下载约 500MB 依赖）

#### 问题 4：APK 安装后崩溃

**原因：** 未签名或签名不完整

**解决：**
- Debug APK 自动使用 debug.keystore 签名（无需手动）
- Release APK 需要手动签名：
  ```bash
  # 使用 uber-apk-signer（自动 v1+v2+v3 签名）
  java -jar uber-apk-signer.jar -a app-release-unsigned.apk
  ```

### 依赖说明

项目非常轻量，只有 1 个外部依赖：

```gradle
dependencies {
    implementation 'androidx.appcompat:appcompat:1.6.1'  // 基础 UI 库
}
```

**无需配置：**
- ❌ 无需 Google Play Services
- ❌ 无需 Firebase
- ❌ 无需其他第三方库

### 版本历史

| 版本 | versionCode | 关键变更 |
|------|-------------|---------|
| v2.0 | 6 | 生产版本（10% 灰色透明） |
| v1.5 | 5 | 添加开机自启动 |
| v1.4 | 4 | 修复 FOREGROUND_SERVICE 权限问题 |
| v1.3 | 3 | 添加调试日志和状态显示 |
| v1.2 | 2 | 修复 PNG 图标缺失导致安装失败 |
| v1.0 | 1 | 初始版本 |

## 权限说明

| 权限 | 用途 | 必需性 |
|------|------|--------|
| `SYSTEM_ALERT_WINDOW` | 显示悬浮窗遮挡层 | 必需 |
| `FOREGROUND_SERVICE` | 保持服务运行不被杀 | 必需（Android 9+） |
| `RECEIVE_BOOT_COMPLETED` | 开机自动启动 | 可选 |

**无网络权限** - 应用完全离线，无任何数据上传。

## 兼容性

- **最低版本：** Android 6.0（API 23）
- **目标版本：** Android 11（API 30）
- **测试设备：** 需在目标设备上实际测试

## 故障排查

### 问题 1：遮挡层不显示

**原因：** 未授予悬浮窗权限

**解决：**
1. 设置 → 应用 → Android Edge Blocker → 权限
2. 启用"显示悬浮窗"

### 问题 2：重启后没有自动启动

**原因：** 厂商限制后台自启动（小米/华为/OPPO/Vivo 等）

**解决：**
1. 设置 → 应用 → Android Edge Blocker → 自启动管理
2. 允许自启动
3. 设置 → 电池 → 后台运行 → 允许后台活动

### 问题 3：遮挡层太明显/不够明显

**解决：** 修改 `EdgeBlockService.java` 中的透明度参数（见"自定义配置"）

### 问题 4：想要遮挡左右边缘

**解决：** 修改 `createLayoutParams()` 方法，添加左右遮挡层（参考顶部/底部实现）

## 技术实现原理

### WindowManager Overlay

使用 Android 的 `TYPE_APPLICATION_OVERLAY` 创建系统级悬浮窗：

```java
// 关键标志组合
params.flags = WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE      // 不获取焦点
             | WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL     // 触摸穿透
             | WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS;   // 防止裁剪
```

### 前台服务保活

```java
// 防止被系统杀掉
startForeground(NOTIFICATION_ID, createNotification());
```

### 开机自启动

```java
// BootReceiver 监听 BOOT_COMPLETED 广播
@Override
public void onReceive(Context context, Intent intent) {
    if (Intent.ACTION_BOOT_COMPLETED.equals(intent.getAction())) {
        context.startService(new Intent(context, EdgeBlockService.class));
    }
}
```

## 遮挡机制详解

### 权限要求

**必须授予"显示悬浮窗"权限（Overlay Permission）：**
- 设置 → 应用 → Android Edge Blocker → 显示悬浮窗
- 首次启动时会自动跳转到权限设置页面
- 未授权时遮挡层无法显示

### 遮挡范围说明

**✅ 可以遮挡：**
- **第三方应用** - 已测试：
  - Libre FreeStyle（雅培瞬感血糖监测 App）
  - 遮挡层覆盖在 App 界面最上层，有效防止误触
- **理论上支持所有第三方应用**（未全面测试）

**❌ 无法遮挡（系统限制）：**
- **底部导航栏**（3 个按钮：返回/主屏幕/多任务）
  - Android 系统保护层，任何第三方应用都无法覆盖
  - 目的：防止恶意应用劫持导航
- **顶部状态栏/通知栏**
  - 下拉通知栏时遮挡层会被系统 UI 覆盖
  - 但在应用内显示时遮挡层仍然有效
- **系统设置页面**（Settings）
  - Android 安全机制，防止钓鱼攻击
  - 在设置中调整时遮挡层暂时不可见

### 典型使用场景

**实测场景：血糖监测**
- 老人使用 Libre FreeStyle 查看血糖数据
- ✅ 遮挡层覆盖屏幕顶部 400px + 底部 400px
- ✅ 查看数据时手指不会误触边缘导致意外退出
- ✅ 重启设备后自动恢复遮挡

**其他潜在场景**（未测试）：
- 阅读应用（防止翻页误触）
- 视频应用（防止滑动误触）
- 浏览器应用（防止误触地址栏/工具栏）

### 遮挡层行为

- **触摸穿透**：遮挡层不拦截触摸事件，只是视觉提示
- **心理暗示**：用户看到淡灰色区域会自然避开
- **始终显示**：覆盖在所有可遮挡应用之上，不会被遮挡消失
- **系统级权限**：使用 `TYPE_APPLICATION_OVERLAY` 实现全局覆盖

## 项目结构

```
android-edge-blocker/
├── android-edge-blocker.apk       # 可直接安装的 APK
├── app/
│   ├── build.gradle               # 应用构建配置
│   └── src/main/
│       ├── AndroidManifest.xml    # 清单文件（权限、组件）
│       ├── java/com/simple/edgeblocker/
│       │   ├── MainActivity.java        # 主界面（启动/停止按钮）
│       │   ├── EdgeBlockService.java    # 遮挡服务（核心逻辑）
│       │   └── BootReceiver.java        # 开机接收器
│       └── res/
│           ├── layout/activity_main.xml     # UI 布局
│           ├── values/strings.xml           # 字符串资源
│           ├── mipmap-*/ic_launcher.png     # 应用图标（5 套）
│           └── drawable/ic_launcher.xml     # Adaptive Icon
├── build.gradle                   # 项目构建配置
├── settings.gradle                # Gradle 设置
├── BUILD_STEP_BY_STEP.md          # 完整构建指南（从零开始，含所有实际命令）
├── TROUBLESHOOTING.md             # 开发经验和问题排查指南
└── README.md                      # 本文档
```

## Privacy release gate / 发布前隐私检查

### Clean publication history

This publication snapshot starts a new root history. Earlier development history
and recovery backups are retained privately, not included in the public branch.
Existing source files, license notices, and the distributed APK are preserved.
The APK was not rebuilt; byte preservation does not establish an exact source
match or change its existing debug-signing status. Replacing a branch cannot
recall old clones or guarantee removal from GitHub caches and hidden refs;
remaining sensitive cached objects may require GitHub Support.

Run locally with Python 3.10+ and Git; no packages, network calls, Android
SDK, emulator, or APK rebuild are needed for documentation/tooling changes.
Keep private audit output outside the repository.

```powershell
python -B -m unittest discover -s tests -p "test_*.py" -q
# Tracked working files, including newly staged files, plus the tracked APK:
python -B tools\privacy_gate.py
# Exact committed publication tree (run after a scoped commit, before pushing):
python -B tools\privacy_gate.py --revision HEAD
# Optional: also inspect a locally built, untracked release APK:
python -B tools\privacy_gate.py --apk app\build\outputs\apk\release\app-release.apk
# Separate historical review; use an explicit public branch/tag ref:
python -B tools\privacy_gate.py --history refs/heads/main
```

Exit codes: **0** = no findings in the inspected scope, **1** = findings,
**2** = incomplete/invalid input or an exceeded safety limit (block release).
Results contain rule IDs, opaque filename hashes, and source line numbers,
never matched values or raw paths. To locate a reference privately, compute
the first 16 hex digits of SHA-256 of the UTF-8 Git-relative filename
(forward slashes); archive names append `!member`, decoded strings append
`!string:index` or `!pool:offset:index`. Do not publish private reports.

The current check reads working-tree bytes of Git-index filenames, not staged
blob bytes. Untracked/ignored files are not publication inputs unless explicitly
passed as `--apk`; stage intended new files first. Ignoring a file does **not**
exclude it if it is already tracked. The revision check reads immutable blobs.
Symlinks, submodules, missing files, malformed archives and decoding failures
fail closed. Review the exact outgoing tree/diff and commit identities as well.

Checks cover concrete user-home/development paths, Copilot state paths,
session/device identifier assignments, recognizable tokens, credential URLs,
credential assignments, private-key headers, personal email candidates, and
private backup/credential/report filenames. Portable environment variables,
explicit username placeholders, and conventional SDK/tool installation examples
remain valid; there is no blanket exemption for documentation or tests.
Public credits and license text must be retained, not indiscriminately redacted:
the narrow email exceptions are reserved example domains, GitHub's
`users.noreply.github.com`, and the exact GNU/FSF contacts `gnu (at) gnu.org`,
`licensing (at) fsf.org`, `info (at) fsf.org` **only inside LICENSE/NOTICE/COPYING files**.
Any other legitimate contact requires a reviewed, equally scoped policy change,
not a general email suppression.

APK/JAR/ZIP entries are inspected in memory with CRC checks, without extraction,
execution or uploading. Directory names do not guarantee empty content: nonzero
declared directory sizes are rejected before decompression, and decoded directory
content must also be empty. Regular entries retain bounded reads and aggregate
byte accounting. DEX modified-UTF-8 and Android binary XML/resource string
pools are decoded before scanning; compressed bytes are not treated as text.
Other binaries receive a printable-string fallback. Default limits are 64 MiB
per input, 32 MiB per ZIP member, 256 MiB total per tree, 10,000 archive entries,
three nested archive levels, 200:1 expansion, and two million decoded strings.
Decoded string bytes have a separate 256 MiB per-tree budget; repeated string
offsets are scanned once. Missing Git objects fail rather than fetching them.
The gate is a bounded pattern check, **not proof of absence** of encoded,
encrypted, split, unknown-format or runtime secrets, nor build provenance.

Internal skill backups are excluded from publication; preserve any needed copy
privately before removal. A clean current tree does not clean Git history:
historical review still reports affected ancestors after a normal fix commit.
It scans only the explicitly selected branch/tag and never implicitly includes
private/original refs. It does not inventory remote releases, assets, caches or
forks. History rewriting, deleting assets and changing visibility are separate
owner decisions, not actions performed by this gate.

中文：提交前运行测试和当前文件检查，提交后再检查 `--revision HEAD`。
历史检查应单独报告，当前结果为零不代表历史已清除。不要上传审计资料、
会话文件、设备数据或签名私钥；保留合法开源署名。仅修改文档和检查工具时
无需重建或替换现有 APK。

## 开源协议

**MIT License** - 自由使用、修改、分发

## AI 友好特性

本项目代码简洁清晰（<500 行 Java），非常适合用 AI 辅助改造：

**用 GitHub Copilot / Claude / ChatGPT 改造的常见需求：**
- "把遮挡高度改为 500px"
- "改成遮挡左右边缘而不是上下"
- "添加一个设置页面让用户自己调整透明度"
- "加白名单功能，在某些应用里不显示遮挡"
- "改成定时启用（比如只在晚上 8 点后启用）"

**5 分钟就能改完 → 重新构建 APK → 安装使用**

## 相关项目

- **原项目：** Edge Block（已停止维护，APK 修改失败导致创建本项目）
- **技术文档：** `TROUBLESHOOTING.md`（开发过程中遇到的坑和解决方案）

## 致谢

本项目为解决老年用户实际痛点而创建。感谢所有开源社区贡献者。

---

**版本：** v2.0  
**最后更新：** 2026-09-03  
**适用场景：** 老年用户、手抖用户、大屏手机误触严重、USB 损坏无法 adb 调试的设备  
**开发初衷：** 为家人解决实际问题，开源分享给有同样需求的人
