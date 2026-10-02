# Android Edge Blocker

**English** | [中文](#中文版本)

---

## Overview

A simple Android app that blocks accidental edge touches on smartphones, specifically designed for elderly users or those with trembling hands.

**One-sentence summary:** Adds touch-blocking edge overlays at the screen sides and top/bottom to reduce accidental touches.

## Current behavior (v2.3)

- Targets are **top 300px / bottom 600px**, in physical pixels. Short screens shrink them proportionally to retain the existing **200–400px center safe zone**. Side widths remain **50px**.
- Left/right edge overlays are **50px physical pixels**, independently enabled by checkboxes, and default to **on** for fresh installs and upgraded prefs.
- Upgrades preserve the existing **auto_start_enabled** preference.
- The foreground-service notification includes a **Stop** action. Allow notifications on Android 13+ to show it; the main-screen Stop button also works.
- Overlays are **touchable/blocking windows**: taps inside the overlay rectangles are blocked; the app remains interactive outside them.
- Rotation/config changes rebuild all overlays; stop, permission revocation, or fatal errors remove all windows cleanly.
- Layout uses the **physical display** (not RTL-mirrored edges), ignores insets on Android R+, and allows cutouts.

## Published versions and fixed downloads

| Version | versionCode | Top / bottom targets | Left / right | Immutable APK |
|---------|-------------|----------------------|--------------|---------------|
| **2.3 (current)** | **9** | **300px / 600px** | 50px each, independent toggles | [Download 2.3](https://raw.githubusercontent.com/jfpx/android-edge-blocker/6754e6cb449b7af6c6fcaaca337ecab9d2db5d27/android-edge-blocker.apk) |
| 2.1 (previous public version) | 7 | 400px / 400px | 50px each, independent toggles | [Download 2.1](https://raw.githubusercontent.com/jfpx/android-edge-blocker/1e98c89c7b3e5d345f266a0b73079b552743b0b7/android-edge-blocker.apk) |

All dimensions are physical pixels, not dp. Both retain the existing **200–400px minimum-center safety clamp** (the actual center may be larger): on short screens 2.1 shrinks the top/bottom equally; 2.3 shrinks them proportionally **1:2**, with integer rounding. 2.3 provides more bottom coverage and less top coverage; side toggles and saved auto-start preferences remain.

These URLs pin the APK to its source publication commit, not moving `main`. APK SHA-256:

- **2.3:** `009a6a515b8b5b2b6f397d07c1b5c5f5ef6b303f4cea06a4dc9ea41cf3b653e1`
- **2.1:** `923dc266c5e709197819b6cb9a632a214c640c9d3238da941b109ed13434b023`

Both public APKs have signing-certificate SHA-256 `7228483a6dbcecfd795a36e3a06a1482003eb890ce458faad84ec1ad4b3a6156`. Android normally permits the same-signer 2.1 → 2.3 upgrade; downgrading is not a normal in-place install. No intermediate 2.2 download is published.

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
- Adds **semi-transparent overlays** (10% gray, barely visible) at **top 300px** and **bottom 600px** targets, plus optional **left/right 50px** edge blocks
- The overlay windows are **touch-blocking**, so taps inside those rectangles are prevented while the rest of the app still works
- **Psychological cue effect**: Users naturally avoid edge areas when seeing the overlay
- Auto-starts on boot, no manual activation needed

## Core Features

✅ **Edge Blocking**
- Top 300px + Bottom 600px targets, plus optional Left 50px + Right 50px edge blocks
- 10% gray transparency (barely visible, but provides visual cue)
- Left/right are independently enabled and default to on

✅ **Minimal Operation**
- Install → Open → Grant Permission → Start → Done
- Main screen includes auto-start and left/right edge checkboxes
- No ads, no background uploads

✅ **Auto-start on Boot**
- Automatically restores overlay after reboot
- Persistent background service

✅ **System Notification**
- Foreground-service notification is required on Android 8+ and includes a Stop action

❌ **No Bloat**
- No separate settings page; controls stay on the main screen
- No network permission (completely offline)

## Quick Start

### 1. Download APK

Download the [current v2.3 APK](https://raw.githubusercontent.com/jfpx/android-edge-blocker/6754e6cb449b7af6c6fcaaca337ecab9d2db5d27/android-edge-blocker.apk), or choose the previous version in [the comparison above](#published-versions-and-fixed-downloads):
- **Filename:** `android-edge-blocker.apk`
- **Size:** ~3 MB
- **Location:** Repository root directory

### 2. Installation

1. Transfer APK to Android device (cloud drive/WiFi/Bluetooth)
2. Tap to install
3. Allow "Install unknown apps" (enable in settings)

### 3. Usage

1. Open the app
2. Leave the left/right checkboxes enabled if you want side blocking (default on)
3. Optional: toggle auto-start
4. Tap "Start Blocking"
5. Grant "Display over other apps" permission (redirects to system settings)
6. Return to app, tap "Start Blocking" again
7. ✅ Done! Faint gray areas appear at screen top/bottom and enabled side edges

### 4. Verify Effect

- Press Home button → Overlay persists
- Open any app → Overlay covers all content
- Reboot device → Overlay auto-restores

### 5. Stop Blocking (Optional)

Reopen app → Tap "Stop Blocking"

> Allow notifications to use the notification's Stop action. Otherwise, use the main-screen Stop button.

## Customization (Developers)

To modify overlay parameters, edit `app/src/main/java/com/simple/edgeblocker/EdgeOverlayGeometry.java`:

```java
// Independent top/bottom target heights (physical pixels)
static final int TOP_TARGET_PX = 300;
static final int BOTTOM_TARGET_PX = 600;

// Left/right edge target width (physical pixels)
static final int SIDE_TARGET_PX = 50;

// In EdgeBlockService.java, modify color and transparency
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
| **JDK** | 17.0.x | Use Java 17 for the supported reproduction commands below |
| **Gradle** | 8.10 | Gradle Wrapper included, no separate installation needed |
| **Android SDK** | - | Requires the following components: |
| ├─ Build Tools | 34.0.0 | Compilation tools |
| ├─ Platform | API 34 (Android 14) | compileSdk target |
| └─ Minimum runtime | API 23 (Android 6.0) | minSdk; a separate API 23 SDK platform is not required to build |
| **Android Gradle Plugin** | 8.5.0 | Configured in build.gradle |

### Quick Environment Check

Before building:

```powershell
# Check Java version (must be 17-23)
java -version
# Use JDK 17 for this workflow

# Check Android SDK (if building from command line)
$env:ANDROID_HOME

# Or use Android Studio (recommended, auto-manages SDK)
```

### Build Steps

#### Method 1: Command Line Build

```powershell
# 1. Clone repository
git clone https://github.com/jfpx/android-edge-blocker.git
cd android-edge-blocker

# 2. Build Debug version (no clean: retain ignored local evidence)
.\build-apk.ps1 -AndroidSdkPath $env:ANDROID_HOME -Debug

# 4. Output location
# app\build\outputs\apk\debug\app-debug.apk
```

**Release version (production):**
```powershell
.\build-apk.ps1 -AndroidSdkPath $env:ANDROID_HOME -Release
# Output: app\build\outputs\apk\release\app-release-unsigned.apk
# Requires your own signing key; see signing notes below
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
| v2.3 | 9 | Top 300px / bottom 600px targets; proportional 1:2 short-screen shrink; unchanged 50px side toggles |
| v2.1 | 7 | Independent left/right toggles, 50px physical side overlays, geometry safety clamp, foreground Stop action, and permission-safe boot/rotation handling |
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

**Solution:** Modify the geometry constants in `EdgeOverlayGeometry.java` and the color/transparency in `EdgeBlockService.java` (see "Customization")

### Issue 4: Want to block left/right edges

**Solution:** Use the left/right checkboxes on the main screen; the side overlays are already built in

## Technical Implementation

### WindowManager Overlay

Uses Android's `TYPE_APPLICATION_OVERLAY` to create system-level floating window:

```java
// Key flag combination
params.flags = WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE      // Don't get focus
             | WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL     // Block touches inside overlay windows
             | WindowManager.LayoutParams.FLAG_WATCH_OUTSIDE_TOUCH // Keep outside touches available
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
        SharedPreferences prefs = context.getSharedPreferences(EdgeBlockerPrefs.PREFS_NAME, Context.MODE_PRIVATE);
        if (prefs.getBoolean(EdgeBlockerPrefs.KEY_AUTO_START_ENABLED, true)
                && Settings.canDrawOverlays(context)) {
            ContextCompat.startForegroundService(context, new Intent(context, EdgeBlockService.class));
        }
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
- **Bottom navigation bar / system gesture areas**
   - Android reserves these zones; behavior depends on OS/OEM gesture handling
- **Top status bar / notification panel**
   - System UI and security surfaces may cover the overlay
- **System settings and other security-sensitive screens**
   - Android may reserve gestures or hide overlays for safety
- **Some OEM physical devices**
   - Real-device behavior is not guaranteed across vendors or custom ROMs

### Typical Use Case

**Historical tested scenario: Blood glucose monitoring (not a new v2.3 device validation)**
- Elderly person uses Libre FreeStyle to view glucose data
- ✅ Earlier coverage used top 400px + bottom 400px, plus enabled left/right 50px edges; current v2.3 targets are 300px / 600px
- ✅ Fingers won't accidentally touch edge causing unexpected exit when viewing data
- ✅ Overlay auto-restores after device reboot

**Other potential scenarios** (not tested):
- Reading apps (prevent page-turn accidental touches)
- Video apps (prevent swipe accidental touches)
- Browser apps (prevent accidental touches on address bar/toolbar)

### Overlay Behavior

- **Touchable blocking windows**: taps inside the overlay rectangles are blocked by the overlay windows
- **Psychological cue**: Users naturally avoid seeing faint gray area
- **Always displayed**: Overlays sit above blockable apps, but OS security surfaces can still win
- **Physical left/right edges**: Uses screen-left and screen-right, not RTL-mirrored sides
- **System-level permission**: Uses `TYPE_APPLICATION_OVERLAY` for global coverage

## Project Structure

```
android-edge-blocker/
├── android-edge-blocker.apk       # APK artifact (published feature build after validation)
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
├── native-fixture/                # Controlled white/black touch-probe source
├── tools/                         # Owned emulator, native validation, privacy gate
├── tests/                         # Python host regressions (stdlib unittest)
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

**Version:** v2.3
**Last Updated:** 2026-09-30  
**Use Cases:** Elderly users, users with trembling hands, large-screen phones with serious accidental touches, devices with damaged USB preventing adb debugging  
**Development Motivation:** Solve real problems for family, open-source sharing for others with same needs

---

# 中文版本

## 概述

一个简易的 Android 应用，阻止智能手机边缘的意外触摸，专为老年用户或手抖用户设计。

**一句话说明：** 在屏幕边缘添加可拦截触摸的遮挡层，减少意外误触。

## 当前行为（v2.3）

- 目标高度为 **顶部 300px / 底部 600px**（物理像素）；短屏按比例收缩，仍保留原有 **200–400px** 中间安全区。左右宽度保持 **50px**。
- 左/右边缘遮挡为 **50px 物理像素**，可独立勾选，新安装和升级后的默认值都是 **开启**。
- 升级时会保留已有的 **auto_start_enabled** 设置。
- 前台服务通知提供 **停止** 按钮；Android 13+ 需允许通知才能显示，也可使用主界面的停止按钮。
- 遮挡层是**可触摸/可拦截的窗口**：遮挡矩形内部的点击会被拦住，矩形外仍可正常操作。
- 旋转/配置变化会重建全部遮挡；停止、撤销权限或严重错误会清理所有窗口。
- 布局使用**物理屏幕边缘**（不是 RTL 镜像边缘），R 及以上忽略 insets，并允许 cutout。

## 两个公开版本的区别与固定下载链接

| 版本 | versionCode | 顶部 / 底部目标 | 左侧 / 右侧 | 固定 APK 链接 |
|------|-------------|----------------|-------------|---------------|
| **2.3（当前）** | **9** | **300px / 600px** | 各 50px，可独立开关 | [下载 2.3](https://raw.githubusercontent.com/jfpx/android-edge-blocker/6754e6cb449b7af6c6fcaaca337ecab9d2db5d27/android-edge-blocker.apk) |
| 2.1（上一公开版本） | 7 | 400px / 400px | 各 50px，可独立开关 | [下载 2.1](https://raw.githubusercontent.com/jfpx/android-edge-blocker/1e98c89c7b3e5d345f266a0b73079b552743b0b7/android-edge-blocker.apk) |

单位均为物理像素，不是 dp。两版都保留原有 **200–400px 最小中间安全区约束**（实际中间区域可以更大）；短屏时 2.1 上下均衡收缩，2.3 按 **1:2** 比例收缩并取整。2.3 主要增加底部、减少顶部遮挡；左右开关及已有自启动偏好不变。

链接绑定各版源码发布提交，不会随 `main` 更新而变化。[上方英文对照表](#published-versions-and-fixed-downloads)列出了两个 APK 的 SHA-256。两版签名证书 SHA-256 均为 `7228483a6dbcecfd795a36e3a06a1482003eb890ce458faad84ec1ad4b3a6156`，同签名 2.1 → 2.3 通常可覆盖升级；降级不是普通覆盖安装。中间版本 2.2 不提供公开下载。

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
- 在屏幕以**顶部 300px** 和**底部 600px** 为目标添加**半透明遮挡层**（10% 灰色，几乎不可见），并支持**左/右 50px** 边缘遮挡
- 遮挡层窗口会**拦截触摸**，矩形内部点击会被阻止，矩形外仍可正常操作
- **心理暗示效果**：用户看到遮挡层会**自然避开边缘区域**，大幅减少误触
- 开机自动启动，无需每次手动开启

## 核心功能

✅ **遮挡屏幕边缘**
- 顶部 300px + 底部 600px 目标值，并支持左侧 50px + 右侧 50px 遮挡
- 10% 灰色透明（几乎不可见，但能提示用户）
- 左右边缘可独立启用，默认开启

✅ **极简操作**
- 安装 → 打开 → 授权 → 启动 → 完成
- 主界面包含自启动和左右边缘复选框
- 无广告，无后台上传

✅ **开机自启动**
- 重启后自动恢复遮挡
- 后台常驻服务

✅ **系统通知**
- Android 8+ 需要前台服务通知，并提供停止按钮

❌ **无多余功能**
- 无单独设置页；控制项直接放在主界面
- 无网络权限（完全离线）

## 快速开始

### 1. 下载 APK

直接[下载当前 v2.3 APK](https://raw.githubusercontent.com/jfpx/android-edge-blocker/6754e6cb449b7af6c6fcaaca337ecab9d2db5d27/android-edge-blocker.apk)，上一公开版本见上方对照表：
- **文件名：** `android-edge-blocker.apk`
- **大小：** 约 3 MB
- **位置：** 仓库根目录

### 2. 安装

1. 将 APK 传输到 Android 设备（云盘/WiFi/蓝牙）
2. 点击安装
3. 允许"安装未知应用"（设置中开启）

### 3. 使用

1. 打开应用
2. 需要侧边遮挡时，保持左/右复选框开启（默认开启）
3. 可按需切换自动启动
4. 点击"启动遮挡"
5. 授予"显示悬浮窗"权限（跳转到系统设置）
6. 返回应用，再次点击"启动遮挡"
7. ✅ 完成！屏幕顶部/底部以及已启用的左右边缘会出现极淡的灰色区域

### 4. 验证效果

- 按 Home 键返回桌面 → 遮挡层仍在
- 打开任何应用 → 遮挡层覆盖在所有内容上
- 重启设备 → 遮挡层自动恢复

### 5. 停止遮挡（可选）

重新打开应用 → 点击"停止遮挡"

> 注意：允许通知后可使用通知里的“停止”按钮；否则请使用主界面的停止按钮。

## 自定义配置（开发者）

如需修改遮挡参数，编辑 `app/src/main/java/com/simple/edgeblocker/EdgeOverlayGeometry.java`：

```java
// 顶部/底部独立目标高度（物理像素）
static final int TOP_TARGET_PX = 300;
static final int BOTTOM_TARGET_PX = 600;

// 左/右边缘目标宽度（物理像素）
static final int SIDE_TARGET_PX = 50;

// 在 EdgeBlockService.java 中修改颜色和透明度
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
| **JDK** | 17.0.x | 下方支持的复现流程使用 Java 17 |
| **Gradle** | 8.10 | 项目已包含 Gradle Wrapper，无需单独安装 |
| **Android SDK** | - | 需要以下组件： |
| ├─ Build Tools | 34.0.0 | 编译工具 |
| ├─ Platform | API 34 (Android 14) | compileSdk 目标 |
| └─ 最低运行版本 | API 23 (Android 6.0) | minSdk；构建无需另装 API 23 SDK 平台 |
| **Android Gradle Plugin** | 8.5.0 | 已在 build.gradle 配置 |

### 快速环境检查

构建前请确认：

```powershell
# 检查 Java 版本（本流程使用 Java 17）
java -version
# 输出应显示：openjdk version "17.x.x"

# 检查 Android SDK（如果使用命令行构建）
$env:ANDROID_HOME

# 或直接使用 Android Studio（推荐，自动管理 SDK）
```

### 构建步骤

#### 方法 1：命令行构建

```powershell
# 1. 克隆仓库
git clone https://github.com/jfpx/android-edge-blocker.git
cd android-edge-blocker

# 2. 构建 Debug 版本（不执行 clean，保留被忽略的本地证据）
.\build-apk.ps1 -AndroidSdkPath $env:ANDROID_HOME -Debug

# 4. 输出位置
# app\build\outputs\apk\debug\app-debug.apk
```

**Release 版本（生产环境）：**
```powershell
.\build-apk.ps1 -AndroidSdkPath $env:ANDROID_HOME -Release
# 输出：app\build\outputs\apk\release\app-release-unsigned.apk
# 需要自己的签名密钥，见下方签名说明
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
| v2.3 | 9 | 顶部 300px / 底部 600px；短屏按 1:2 收缩；左右 50px 独立开关不变 |
| v2.1 | 7 | 左右边缘独立开关、50px 物理侧边遮挡、安全区收缩、前台通知停止按钮、开机/旋转时的权限安全处理 |
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

**解决：** 修改 `EdgeOverlayGeometry.java` 中的几何常量，以及 `EdgeBlockService.java` 中的颜色/透明度（见"自定义配置"）

### 问题 4：想要遮挡左右边缘

**解决：** 在主界面使用左右复选框即可，左右遮挡已经内置

## 技术实现原理

### WindowManager Overlay

使用 Android 的 `TYPE_APPLICATION_OVERLAY` 创建系统级悬浮窗：

```java
// 关键标志组合
params.flags = WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE      // 不获取焦点
             | WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL     // 遮挡窗口内部触摸会被拦住
             | WindowManager.LayoutParams.FLAG_WATCH_OUTSIDE_TOUCH // 保留窗口外触摸
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
        SharedPreferences prefs = context.getSharedPreferences(EdgeBlockerPrefs.PREFS_NAME, Context.MODE_PRIVATE);
        if (prefs.getBoolean(EdgeBlockerPrefs.KEY_AUTO_START_ENABLED, true)
                && Settings.canDrawOverlays(context)) {
            ContextCompat.startForegroundService(context, new Intent(context, EdgeBlockService.class));
        }
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
- **底部导航栏 / 系统手势区域**
   - Android 会保留这些区域，行为取决于系统和厂商的手势实现
- **顶部状态栏 / 通知栏**
   - 系统 UI 和安全区域可能覆盖遮挡层
- **系统设置和其他安全敏感页面**
   - 为了安全，系统可能保留手势或隐藏遮挡层
- **部分 OEM 真机**
   - 不同厂商/定制 ROM 的实际表现无法保证一致

### 典型使用场景

**历史实测场景：血糖监测（不是本次 v2.3 真机验证）**
- 老人使用 Libre FreeStyle 查看血糖数据
- ✅ 之前覆盖顶部 400px + 底部 400px，以及已启用的左右 50px 边缘；当前 v2.3 目标为 300px / 600px
- ✅ 查看数据时手指不会误触边缘导致意外退出
- ✅ 重启设备后自动恢复遮挡

**其他潜在场景**（未测试）：
- 阅读应用（防止翻页误触）
- 视频应用（防止滑动误触）
- 浏览器应用（防止误触地址栏/工具栏）

### 遮挡层行为

- **可触摸遮挡窗口**：遮挡矩形内部的点击会被窗口拦住
- **心理暗示**：用户看到淡灰色区域会自然避开
- **始终显示**：覆盖在可遮挡应用之上，但系统安全界面仍可能优先
- **物理左/右边缘**：使用屏幕左侧和右侧，不做 RTL 镜像
- **系统级权限**：使用 `TYPE_APPLICATION_OVERLAY` 实现全局覆盖

## 项目结构

```
android-edge-blocker/
├── android-edge-blocker.apk       # v2.3 APK
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
├── native-fixture/                # 可控黑白对比背景和触摸探针源码
├── tools/                         # 专属模拟器、原生验证、隐私检查
├── tests/                         # Python 主机回归测试（标准库 unittest）
├── BUILD_STEP_BY_STEP.md          # 完整构建指南（从零开始，含所有实际命令）
├── TROUBLESHOOTING.md             # 开发经验和问题排查指南
└── README.md                      # 本文档
```

## Reproducible local tests / 可复现的本地测试

All required harness code is in this repository; no session files, private scripts,
prebuilt probe APK, signing keys, or Python packages are required. Generated logs,
screenshots, AVD userdata and build outputs stay under ignored `build\` directories.
The older one-off `device_final.py`, `verify_final.py`, `run-edge-e2e*.ps1` and
`start-final.ps1` workflows are superseded by the tools below, not runtime inputs.

所有必备测试代码均在仓库中：不依赖会话目录、私人脚本、预编译探针 APK、
签名私钥或第三方 Python 包。旧的一次性测试脚本已由下列工具替代；
日志、截图、模拟器用户数据及构建输出保留在被忽略的 `build\` 中，不提交。

### Tools and prerequisites / 工具与依赖

| Checked-in input / 已提交输入 | Purpose / 用途 |
|---|---|
| `build-apk.ps1` | Repository Wrapper build from any working directory; no automatic clean or tracked APK replacement / 从任意目录构建，不清理证据、不替换仓库 APK |
| `tools\emulator.ps1` | Create a fresh owned AVD; receipt-proven Start/Stop / 创建独立 AVD，凭所有权记录启停 |
| `tools\native_owner_check.ps1` | Live PID identity and listener ownership checks / 核对活进程身份及端口归属 |
| `tools\native_validation.py` | Explicit-serial native smoke/full validation and private evidence / 指定设备的原生冒烟、完整验证及私有证据 |
| `native-fixture\` | Independent Android test-app source; built by the runner / 独立触摸测试应用源码，由验证器构建 |
| `tests\test_build_helper.py`, `tests\test_emulator.py`, `tests\test_native_validation.py` | Offline safety/protocol/geometry regression tests / 离线安全、协议及几何回归测试 |
| `tests\test_privacy*.py`, `tools\privacy_gate.py`, `app\src\test\` | Existing privacy tests/gate and JVM geometry tests / 原有隐私检查及 JVM 几何测试 |

Use **Windows**, **Python 3.11+**, **PowerShell 7 (`pwsh`)** for all host tests
(live scripts also support Windows PowerShell 5.1), **JDK 17** through `JAVA_HOME`,
the checked-in **Gradle 8.10 Wrapper / AGP 8.5.0**, and an official Android SDK.
Enable hardware virtualization/Windows Hypervisor Platform for the emulator.
SDK installation and license acceptance are explicit developer actions, never test
import side effects. `Get-Help .\tools\emulator.ps1 -Full` includes fresh-SDK setup.

完整主机测试需要 Windows、Python 3.11+、PowerShell 7（`pwsh`）；
原生脚本也支持 Windows PowerShell 5.1。配置 `JAVA_HOME` 指向 JDK 17，
使用仓库自带 Gradle Wrapper，并安装官方 Android SDK。模拟器需硬件虚拟化支持。
SDK 下载及许可证确认由开发者明确执行，不会在导入测试时静默安装。

From the repository root, set `$sdk` to your SDK directory and install missing
packages explicitly / 在仓库根目录，将 `$sdk` 设为自己的 SDK 路径，显式安装缺少的组件：

```powershell
$sdk = $env:ANDROID_HOME
# If ANDROID_HOME is unset, assign your SDK directory to $sdk first.
& "$sdk\cmdline-tools\latest\bin\sdkmanager.bat" "--sdk_root=$sdk" --licenses
& "$sdk\cmdline-tools\latest\bin\sdkmanager.bat" "--sdk_root=$sdk" `
  'platform-tools' 'platforms;android-34' 'build-tools;34.0.0' `
  'emulator' 'system-images;android-34;default;x86_64'

$env:ANDROID_HOME = $sdk
$env:ANDROID_SDK_ROOT = $sdk
python -B -m unittest discover -s tests -v
.\gradlew.bat :app:testDebugUnitTest
.\build-apk.ps1 -AndroidSdkPath $sdk -Debug
.\gradlew.bat :native-fixture:assembleDebug
```

Host tests use mocks, **not native-device evidence**. The fixture is a separate
module with no dependency from `:app`; app-only builds do not build it. The build
helper copies successful output only to `build\distributions\SimpleEdgeBlocker-debug.apk`
(or `SimpleEdgeBlocker-release.apk`, unsigned). It never replaces
`android-edge-blocker.apk`. `:app:lintDebug` has the known pre-existing
`ExpiredTargetSdkVersion` failure for target SDK 30; this is not a passing lint run.

主机模拟测试不等于设备实测。探针模块不被 `:app` 依赖；仅构建应用时不会构建探针。
构建助手只在成功后复制到 `build\distributions\`，不会覆盖仓库 APK。
`lintDebug` 仍存在 target SDK 30 的既有 `ExpiredTargetSdkVersion` 错误，
不能将其报告为 lint 通过。

### Owned emulator lifecycle / 独立模拟器启停

The tools require a compatible **external adb server at `127.0.0.1:5037`**.
If one exists, coordinate with its owner and keep it unchanged. Do not run
`start-server` with a mismatched SDK: adb may replace the shared server.
Only when no server exists, use a separate terminal with the same `$sdk`:

工具要求 `127.0.0.1:5037` 已有兼容的 adb 服务。已有共享服务时应与其所有者协调，
测试期间不要替换它。仅在没有服务时，在另一终端使用相同 `$sdk` 执行：

```powershell
Remove-Item Env:ADB_SERVER_SOCKET,Env:ANDROID_ADB_SERVER_ADDRESS,Env:ANDROID_ADB_SERVER_PORT,Env:ADB_SERVER_PORT -ErrorAction SilentlyContinue
& "$sdk\platform-tools\adb.exe" -L tcp:127.0.0.1:5037 server nodaemon
```

This command binds or fails; it does not replace an existing server. Leave it
running. In the original terminal / 此命令只绑定端口或报错，不替换已有服务。
保留该终端运行，在原终端执行：

```powershell
.\tools\emulator.ps1 Start -Sdk $sdk -Port 5580 -DryRun
.\tools\emulator.ps1 Start -Sdk $sdk -Port 5580
# Set $receipt to the exact owner.json path printed by Start.
$receipt = '<receipt path printed by Start>'
```

Choose a free even port in 5554–5682; the adjacent port must also be free.
The default physical display is **1080×1920, density 420**, matching the native
suite without scaled touch-coordinate rounding. `-Width`, `-Height`, `-Density`
configure a new AVD for other uses; keep the defaults for this validation suite.
Start creates a unique AVD and receipt under `build\emulator\`. It never attaches
to your existing AVD. Stop accepts only receipt-proven process identities, not
names or serial alone, retains userdata/logs, and never stops the shared adb server.
Raw server-version checks cannot eliminate every check/use race: do not replace
the server or interfere with the disposable emulator during validation.

选择空闲偶数端口（5554–5682），相邻端口也须空闲。
默认物理屏幕为 **1080×1920、density 420**，避免缩放触摸坐标造成边界取整。
其他用途可用 `-Width`、`-Height`、`-Density` 配置新 AVD；本验证流程保持默认值。
Start 每次创建独立 AVD 和所有权记录，不接管已有模拟器。Stop 核对 PID、启动时间、命令和路径，
只停止确认归属的进程，保留用户数据及日志，不停止共享 adb 服务。
检查与执行间的竞态无法完全消除；测试时不要替换服务或操作该独立模拟器。

### Native smoke and full validation / 原生冒烟与完整验证

```powershell
# Minimal actual-device smoke: current published APK, strict current geometry.
python -B tools\native_validation.py --serial emulator-5580 `
  --receipt $receipt --apk android-edge-blocker.apk --profile 2.3 `
  --expected-sha256 009a6a515b8b5b2b6f397d07c1b5c5f5ef6b303f4cea06a4dc9ea41cf3b653e1 `
  --smoke --output build\native-validation\smoke-example

# Full flow: omit --smoke; default output is a fresh UUID directory.
python -B tools\native_validation.py --serial emulator-5580 `
  --receipt $receipt --apk android-edge-blocker.apk --profile 2.3

.\tools\emulator.ps1 Stop -Receipt $receipt -DryRun
.\tools\emulator.ps1 Stop -Receipt $receipt
```

Check each command's exit code before continuing. Always stop your owned emulator
after testing, including on failure; retain failed evidence for diagnosis.
`--output` must be a **new** child of `build\native-validation`, with no
symlinks/junctions; choose a different name for a rerun. SDK selection comes from
the validated receipt, preventing mismatches. `--serial`, `--receipt`, `--apk`
and `--profile` are mandatory; there is no default adb device.

逐条检查退出码。失败也应停止本次自有模拟器并保留失败证据。
`--output` 必须是 `build\native-validation` 下不存在的新目录，不能经过符号链接
或目录联接；重跑时换新名称。SDK 由已验证的所有权记录确定。
必须显式提供设备序列号、所有权记录、APK 和版本配置，不会默认选择 adb 设备。

Smoke builds the fixture from source, verifies installed APK bytes/version,
checks denied overlay permission, then tests portrait stopped/started rendering,
exact edge boundaries, blocked edge taps, passing center taps and Stop cleanup.
Full mode additionally checks both side toggles and persistence, portrait/landscape,
visible system bars, short-screen safety, landscape Stop scrolling, notification
Stop, permission revocation and reboot auto-start. JSON evidence explicitly labels
scope, skipped checks, failures and cleanup errors; screenshots are limited to
the controlled test fixture. This is not physical-phone/OEM/API23 validation.

冒烟会从源码构建探针、核对安装 APK 字节及版本，并验证权限拒绝、竖屏启停渲染、
精确边界、边缘拦截和中心放行。完整模式再验证左右独立开关与持久化、横竖屏、
系统栏、短屏安全区、横屏滚动停止、通知停止、权限撤销及重启恢复。
JSON 明确记录范围、跳过项、失败及清理错误；只截取受控测试探针画面。
这不代表实体手机、OEM 或 API23 实测。

For the previous published APK, supply your downloaded file with `--profile 2.1`
and its SHA-256 above. Profiles strictly select code 7 / symmetric 400+400 or
code 9 / proportional 300+600; both expect 50px sides. Wrong version/geometry
fails rather than relaxing pixel thresholds. Full mode optionally accepts
`--upgrade-from <downloaded-2.1.apk>` for a 2.1 → 2.3 same-signer preference
upgrade (2.0/code6 → 2.1 is also supported when you supply that older APK).
Upgrade is explicitly skipped without this argument; it cannot combine with smoke.

测试上一公开版本时，提供下载文件并使用 `--profile 2.1` 和上表哈希。
版本与几何配置严格匹配，不放宽像素阈值。完整模式可加
`--upgrade-from <下载的2.1.apk>` 测试同签名升级及偏好保留；
未提供时明确跳过升级，冒烟模式不接受此参数。

Both app packages must initially be absent on the disposable emulator. The runner
refuses existing installations instead of clearing data/uninstalling them.
It restores display overrides and uninstalls only successfully installed owned
test packages in cleanup; uncertain installs are retained for inspection.
**A developer's new debug certificate will not upgrade the published APK.**
Use a fresh emulator for local builds; never uninstall a user's app or clear data
to work around a signature mismatch. Private signing material is not included.

独立模拟器中两个测试包必须事先不存在；工具不会清除或卸载已有应用来继续测试。
结束时恢复显示设置，只卸载本次确认安装成功的测试包；安装结果不确定时保留检查。
**开发者新生成的 debug 证书不能覆盖升级公开 APK。** 本地构建使用独立模拟器，
不要为绕过签名不一致而卸载用户应用或清数据；仓库不包含签名私钥。

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

**版本：** v2.3
**最后更新：** 2026-09-30  
**适用场景：** 老年用户、手抖用户、大屏手机误触严重、USB 损坏无法 adb 调试的设备  
**开发初衷：** 为家人解决实际问题，开源分享给有同样需求的人
