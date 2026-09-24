# Android Edge Blocker - 完整构建指南（从零开始）

本文档记录了实际使用的所有构建命令和步骤，让新的 agent 或开发者可以从零开始复现整个构建过程。

路径已改为可移植示例。运行命令前，请将 `$env:PROJECT_ROOT` 设置为自己的项目检出目录；`$env:ANDROID_HOME` 和 `$env:JAVA_HOME` 分别指向本机已安装的 Android SDK 和 JDK。

## 前置条件检查

### 1. 检查 Java 版本

```powershell
# 检查当前 Java 版本
java -version

# 应该显示 Java 17 或 21（不能是 Java 26+）
# 示例输出：openjdk version "17.0.12" 2024-07-16
```

**如果版本不对：**
- 下载 Java 17 LTS：https://adoptium.net/temurin/releases/?version=17
- 解压到本地目录（例如：`D:\jdk-17.0.13+11`）
- 设置环境变量：
  ```powershell
  $env:JAVA_HOME = "D:\path\to\jdk-17.0.13+11"
  $env:Path = "$env:JAVA_HOME\bin;" + $env:Path
  ```

### 2. 检查 Android SDK（可选，Android Studio 会自动管理）

如果使用命令行构建，需要 Android SDK：

```powershell
# 检查 ANDROID_HOME 环境变量
echo $env:ANDROID_HOME

# 如果没有设置，需要下载 Android Command Line Tools
```

## 方法 1：使用 Android Studio（推荐，最省事）

### 步骤 1：安装 Android Studio

1. 下载：https://developer.android.com/studio
2. 安装，首次启动会自动下载 Android SDK（约 2GB）
3. 完成 SDK 安装向导

### 步骤 2：打开项目

1. 启动 Android Studio
2. **File → Open** → 选择自己的 `android-edge-blocker` 项目检出目录
3. 等待 Gradle 同步完成（首次约 5-10 分钟，下载依赖约 500MB）

### 步骤 3：构建 APK

**构建 Debug 版本（开发测试用）：**
1. **Build → Build Bundle(s) / APK(s) → Build APK(s)**
2. 等待构建完成（约 30 秒）
3. 点击通知中的 "locate" 查看 APK
4. APK 位置：`app/build/outputs/apk/debug/app-debug.apk`

**构建 Release 版本（生产环境）：**
1. **Build → Generate Signed Bundle / APK...**
2. 选择 **APK**
3. 创建或选择 Key Store（签名密钥）
4. 填写 Key Store 密码和 Key 别名
5. 选择 release build variant
6. 点击 Finish
7. APK 位置：`app/build/outputs/apk/release/app-release.apk`

---

## 方法 2：命令行构建（适合 CI/CD 或无 GUI 环境）

### 前置准备：下载和安装 Android SDK

```powershell
# 1. 下载 Android Command Line Tools
# 访问：https://developer.android.com/studio#command-tools
# 下载 Windows 版本：commandlinetools-win-*.zip（约 150MB）

# 2. 解压到指定目录
Expand-Archive -Path "D:\downloads\commandlinetools-win-*.zip" -DestinationPath "D:\android-sdk"

# 3. 移动 cmdline-tools 到正确位置（重要！）
New-Item -ItemType Directory -Path "D:\android-sdk\cmdline-tools\latest" -Force
Move-Item -Path "D:\android-sdk\cmdline-tools\*" -Destination "D:\android-sdk\cmdline-tools\latest\" -Force

# 4. 设置 ANDROID_HOME 环境变量
$env:ANDROID_HOME = "D:\android-sdk"
$env:Path += ";$env:ANDROID_HOME\cmdline-tools\latest\bin"
$env:Path += ";$env:ANDROID_HOME\platform-tools"

# 5. 安装必需的 SDK 组件
sdkmanager --install "build-tools;34.0.0"
sdkmanager --install "platforms;android-34"
sdkmanager --install "platform-tools"

# 接受许可协议（首次构建必需）
sdkmanager --licenses
```

### 构建步骤

```powershell
# 1. 进入项目目录
Set-Location $env:PROJECT_ROOT

# 2. 清理之前的构建（可选，首次构建可跳过）
.\gradlew.bat clean

# 3. 构建 Debug 版本（自动签名，直接可用）
.\gradlew.bat assembleDebug

# 输出位置：
# app\build\outputs\apk\debug\app-debug.apk (约 2.9 MB)

# 4. 构建 Release 版本（需要手动签名）
.\gradlew.bat assembleRelease

# 输出位置：
# app\build\outputs\apk\release\app-release-unsigned.apk
```

### 签名 Release APK（使用 uber-apk-signer）

```powershell
# 1. 下载 uber-apk-signer（如果还没有）
Invoke-WebRequest -Uri "https://github.com/patrickfav/uber-apk-signer/releases/download/v1.3.0/uber-apk-signer-1.3.0.jar" -OutFile "D:\tools\uber-apk-signer.jar"

# 2. 签名 Release APK（自动生成 v1+v2+v3 签名）
java -jar D:\tools\uber-apk-signer.jar --apks app\build\outputs\apk\release\app-release-unsigned.apk

# 输出：
# app\build\outputs\apk\release\app-release-aligned-debugSigned.apk

# 3. 重命名为最终版本
Move-Item -Path "app\build\outputs\apk\release\app-release-aligned-debugSigned.apk" -Destination "android-edge-blocker.apk" -Force
```

---

## 实际构建过程记录（本项目使用的命令）

以下是本项目实际使用的完整构建流程：

### 环境准备

```powershell
# 设置 UTF-8 编码（防止中文乱码）
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"

# 先将 ANDROID_HOME 和 JAVA_HOME 设置为本机 SDK、JDK 的安装目录
# 示例：$env:ANDROID_HOME = "D:\path\to\android-sdk"
# 示例：$env:JAVA_HOME = "D:\path\to\jdk-17"
$env:Path = "$env:JAVA_HOME\bin;$env:ANDROID_HOME\cmdline-tools\latest\bin;$env:ANDROID_HOME\platform-tools;" + $env:Path
```

### 首次构建（Debug 版本）

```powershell
Set-Location $env:PROJECT_ROOT

# 清理
.\gradlew.bat clean

# 构建
.\gradlew.bat assembleDebug

# 输出：app\build\outputs\apk\debug\app-debug.apk
```

**首次构建耗时：** 约 15-30 分钟（下载 Gradle、AGP、AndroidX 依赖，总计约 500MB）

**后续构建耗时：** 约 30 秒（增量构建）

### 生成 PNG 图标（本项目踩过的坑）

初版 APK 无法安装，因为缺少 PNG 格式的启动图标。使用以下命令生成：

```powershell
# 使用 ImageMagick 或在线工具生成 5 套 PNG 图标
# 手动创建以下文件：
# app/src/main/res/mipmap-mdpi/ic_launcher.png     (48x48)
# app/src/main/res/mipmap-hdpi/ic_launcher.png     (72x72)
# app/src/main/res/mipmap-xhdpi/ic_launcher.png    (96x96)
# app/src/main/res/mipmap-xxhdpi/ic_launcher.png   (144x144)
# app/src/main/res/mipmap-xxxhdpi/ic_launcher.png  (192x192)

# 然后修改 AndroidManifest.xml：
# android:icon="@mipmap/ic_launcher"  # 从 @drawable 改为 @mipmap
```

### 添加 FOREGROUND_SERVICE 权限（本项目踩过的坑）

第一次构建的 APK 启动时崩溃，错误信息：

```
Permission Denial: startForeground requires android.permission.FOREGROUND_SERVICE
```

**解决方法：** 在 `AndroidManifest.xml` 添加：

```xml
<uses-permission android:name="android.permission.FOREGROUND_SERVICE" />
```

### 最终生产版本构建

```powershell
# 修改颜色为 10% 灰色透明（生产版本）
# 编辑 EdgeBlockService.java line 108:
# view.setBackgroundColor(Color.argb(26, 128, 128, 128));

# 更新版本号
# 编辑 app/build.gradle:
# versionCode 6
# versionName "2.0"

# 清理并构建
.\gradlew.bat clean
.\gradlew.bat assembleDebug

# 输出：app\build\outputs\apk\debug\app-debug.apk

# 重命名为最终 APK
Copy-Item -Path "app\build\outputs\apk\debug\app-debug.apk" -Destination "android-edge-blocker.apk" -Force
```

---

## 验证 APK（推荐步骤）

### 1. 检查 APK 结构

```powershell
# 列出 APK 内容
jar -tf android-edge-blocker.apk | Select-String "ic_launcher"

# 应该能看到 5 套 PNG 图标：
# res/mipmap-mdpi-v4/ic_launcher.png
# res/mipmap-hdpi-v4/ic_launcher.png
# res/mipmap-xhdpi-v4/ic_launcher.png
# res/mipmap-xxhdpi-v4/ic_launcher.png
# res/mipmap-xxxhdpi-v4/ic_launcher.png
```

### 2. 检查签名

```powershell
# 验证 APK 签名（Debug APK 自动用 debug.keystore 签名）
jarsigner -verify -verbose android-edge-blocker.apk

# 应该显示：jar verified.
```

### 3. 检查权限

```powershell
# 使用 aapt 工具查看权限（需要 Android SDK）
aapt dump badging android-edge-blocker.apk | Select-String "uses-permission"

# 应该包含：
# uses-permission: name='android.permission.SYSTEM_ALERT_WINDOW'
# uses-permission: name='android.permission.RECEIVE_BOOT_COMPLETED'
# uses-permission: name='android.permission.FOREGROUND_SERVICE'
```

---

## 常见构建错误和解决方案

### 错误 1：`Unsupported class file major version 70`

**原因：** Java 版本太新（Java 26+），Gradle 8.10 不支持

**解决：**
```powershell
# 下载 Java 17
# 设置环境变量
$env:JAVA_HOME = "D:\path\to\jdk-17"
java -version  # 验证
```

### 错误 2：`ANDROID_HOME is not set`

**原因：** 未安装 Android SDK 或环境变量未设置

**解决：**
```powershell
$env:ANDROID_HOME = "D:\path\to\android-sdk"
```

或使用 Android Studio（自动管理 SDK）

### 错误 3：`SDK location not found`

**原因：** 项目中缺少 `local.properties` 文件

**解决：**
```powershell
# 手动创建 local.properties
@"
sdk.dir=D:\\android-sdk
"@ | Out-File -FilePath "local.properties" -Encoding ASCII
```

### 错误 4：构建卡在 "Resolving dependencies"

**原因：** 首次下载依赖（约 500MB）

**解决：** 耐心等待 15-30 分钟，或使用国内镜像（修改 `build.gradle` 中的 `repositories`）

### 错误 5：APK 安装后崩溃

**可能原因：**
1. 缺少 FOREGROUND_SERVICE 权限 → 添加到 AndroidManifest.xml
2. 缺少 PNG 图标 → 生成 5 套 mipmap 图标
3. 签名不完整 → 使用 uber-apk-signer 重新签名

---

## 增量修改和重新构建

### 修改代码后重新构建

```powershell
# 1. 修改 Java 代码（例如：改变遮挡高度）
# 编辑 app/src/main/java/com/simple/edgeblocker/EdgeBlockService.java

# 2. 增量构建（只编译修改的部分，约 10 秒）
.\gradlew.bat assembleDebug

# 3. 输出：app\build\outputs\apk\debug\app-debug.apk
```

### 修改资源后重新构建

```powershell
# 1. 修改 XML 资源（例如：改变字符串）
# 编辑 app/src/main/res/values/strings.xml

# 2. 清理并构建（资源修改建议 clean）
.\gradlew.bat clean
.\gradlew.bat assembleDebug
```

### 修改版本号

```powershell
# 编辑 app/build.gradle
# versionCode 7      # 每次发布递增
# versionName "2.1"

# 重新构建
.\gradlew.bat assembleDebug
```

---

## 安装到设备

### 方法 1：adb 安装（推荐）

```powershell
# 连接设备并开启 USB 调试
adb devices

# 安装 APK
adb install -r android-edge-blocker.apk

# -r 表示覆盖安装（如果已安装旧版本）
```

### 方法 2：手动安装

1. 将 APK 传输到设备（微信/云盘/浏览器下载）
2. 设置 → 允许从未知来源安装应用
3. 点击 APK 文件安装

---

## 调试和日志

### 查看实时日志

```powershell
# 查看应用日志（EdgeBlockService 和 MainActivity）
adb logcat -s EdgeBlockService:I MainActivity:I

# 保存日志到文件
adb logcat -s EdgeBlockService:I MainActivity:I -d > debug.log
```

### 查看崩溃日志

```powershell
# 查看最近的崩溃信息
adb logcat -b crash

# 或查看所有错误级别日志
adb logcat *:E
```

---

## 总结

**最简单的方式：** 使用 Android Studio（自动管理所有依赖和工具）

**最灵活的方式：** 命令行构建（适合 CI/CD，但需要手动配置环境）

**关键要点：**
1. Java 必须是 17-23（不能是 26+）
2. 首次构建需要下载约 500MB 依赖（15-30 分钟）
3. Debug APK 自动签名，可直接安装
4. Release APK 需要手动签名（使用 uber-apk-signer）
5. 必须包含 PNG 图标和 FOREGROUND_SERVICE 权限

**本项目的完整构建历史：**
- v1.0：初始版本（缺 PNG 图标，无法安装）
- v1.2：添加 PNG 图标（安装成功）
- v1.3：添加调试日志
- v1.4：添加 FOREGROUND_SERVICE 权限（修复崩溃）
- v1.5：添加开机自启动
- v2.0：生产版本（10% 灰色透明）
