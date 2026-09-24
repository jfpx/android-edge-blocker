# Simple Edge Blocker - 快速构建指南

以下命令中的 `$env:PROJECT_ROOT` 应指向你自己的项目检出目录。

## 最简单的构建方法（推荐）

### 方法 1：使用在线 Android 构建服务（无需安装任何工具）

**Appetize.io Build Service**（如果可用）：
1. 将项目打包为 ZIP
2. 上传到在线构建服务
3. 下载构建好的 APK

**或使用 GitHub Actions**（如果有 GitHub 账号）：
1. 在 GitHub 创建仓库
2. 推送代码
3. 配置 GitHub Actions 自动构建
4. 下载 Artifacts 中的 APK

### 方法 2：在 Android 设备上直接构建（无需 PC）

**使用 APK Editor Studio（在设备上）**：
1. 安装 APK Editor Studio（Google Play / F-Droid）
2. 导入本项目的所有文件
3. 点击"构建 APK"

**使用 AIDE（Android IDE）**：
1. 安装 AIDE（Google Play）
2. 打开项目
3. 点击运行按钮

### 方法 3：使用 Android Studio（传统方式）

**如果你已经有 Android Studio**：

1. 打开 Android Studio
2. File → Open → 选择 `simple-edge-blocker` 目录
3. 等待 Gradle 同步（首次需要下载依赖，可能需要 10-30 分钟）
4. Build → Build Bundle(s) / APK(s) → Build APK(s)
5. APK 位于 `app/build/outputs/apk/debug/app-debug.apk`

**如果没有 Android Studio**：

下载地址：https://developer.android.com/studio
- Windows: 约 1GB 下载 + 3GB 安装空间
- 首次启动需要下载 Android SDK（约 2-5GB）

---

## 我已经帮你做什么

✅ **完整的 Android 项目结构**
✅ **核心功能代码**：
- `EdgeBlockService.java` - 遮挡服务（顶部 400px + 底部 400px，50% 透明）
- `MainActivity.java` - 主界面（启动/停止按钮）
- `BootReceiver.java` - 开机自启动

✅ **配置文件**：
- `AndroidManifest.xml` - 权限和组件声明
- `build.gradle` - 构建配置
- UI 布局和资源文件

✅ **详细文档**：
- `README.md` - 完整的使用和构建说明

---

## 你接下来需要做什么

### 选项 A：如果你有 Android Studio

```powershell
# 1. 打开 Android Studio
# 2. File → Open → 选择自己的 android-edge-blocker 项目目录
# 3. 等待 Gradle 同步
# 4. Build → Build APK
```

APK 将生成在 `app/build/outputs/apk/debug/app-debug.apk`

### 选项 B：如果你没有 Android Studio，但有 Gradle 和 Android SDK

```powershell
Set-Location $env:PROJECT_ROOT

# Windows
gradlew.bat assembleDebug

# 或直接用系统的 Gradle（如果已安装）
gradle assembleDebug
```

**注意**：首次运行会下载 Gradle 和依赖，需要互联网连接。

### 选项 C：如果你想在 Android 设备上构建

1. 将整个 `simple-edge-blocker` 目录打包为 ZIP
2. 传到 Android 设备（云盘/微信/邮件）
3. 解压
4. 使用 AIDE 或 Termux 构建

### 选项 D：我可以帮你构建（如果你有 Android SDK）

如果你本地有 Android SDK 但不熟悉命令，告诉我：
- Android SDK 路径（如 `C:\Users\...\AppData\Local\Android\Sdk`）
- 我可以生成一键构建脚本

---

## 快速测试（验证代码正确性）

**不构建 APK，直接验证代码**：

### 检查语法错误

```powershell
Set-Location $env:PROJECT_ROOT

# 检查 Java 代码
javac -version  # 确保有 Java 编译器
find app/src/main/java -name "*.java" | ForEach-Object { javac -sourcepath app/src/main/java $_ }
```

如果没有语法错误，代码就是正确的。

### 检查 XML 格式

```powershell
# 检查 AndroidManifest.xml
[xml](Get-Content app/src/main/AndroidManifest.xml)

# 检查布局文件
[xml](Get-Content app/src/main/res/layout/activity_main.xml)
```

如果没有报错，XML 格式正确。

---

## 依赖项清单（如果需要离线构建）

本项目依赖：
- `androidx.appcompat:appcompat:1.6.1`
- Gradle 8.1.0
- Android SDK Build Tools 34
- Target SDK 34, Min SDK 23

**离线构建**（如果没有互联网）：
1. 在有网络的机器上运行一次 `gradle build`
2. 复制 `~/.gradle/caches` 目录到离线机器
3. 在离线机器上运行 `gradle build --offline`

---

## 常见问题

### Q: 我没有 Android Studio，也不想安装怎么办？

A: 使用**在线构建服务**或**在 Android 设备上构建**（见方法 1 和 2）。

### Q: 构建需要多长时间？

A: 
- 首次构建（下载依赖）：10-30 分钟
- 后续构建：1-3 分钟
- 设备上构建（AIDE）：5-10 分钟

### Q: APK 多大？

A: 预计 300-500 KB（非常小，因为没有额外库）

### Q: 我可以先测试吗？

A: 可以！我可以生成一个**伪 APK 结构**用于静态分析，或者你可以用 Android Studio 的模拟器测试。

### Q: 我想修改参数（高度、透明度）

A: 编辑 `app/src/main/java/com/simple/edgeblocker/EdgeBlockService.java`：

```java
// 第 12-13 行
private static final int BLOCK_HEIGHT = 400;  // 改为 500、600 等
private static final float ALPHA = 0.5f;      // 改为 0.3、0.7 等
```

---

## 给老人的最简使用指南

**一旦 APK 构建好**：

1. **传输 APK 到老人设备**
   - 微信发送
   - 或上传到云盘（百度网盘/OneDrive）
   - 老人下载

2. **安装**
   - 点击 APK 文件
   - 允许"安装未知应用"
   - 点击"安装"

3. **使用**
   - 打开"Simple Edge Blocker"应用
   - 点击"启动遮挡"
   - 授予"显示悬浮窗"权限
   - 返回应用，再次点击"启动遮挡"
   - 看到顶部和底部有黑色半透明条 → 成功

4. **之后**
   - 可以关闭应用（遮挡继续运行）
   - 重启设备后自动启动
   - 如需停止，重新打开应用，点击"停止遮挡"

---

## 下一步

请告诉我：
1. 你想用哪种方法构建？（A/B/C/D）
2. 是否需要我生成自动化构建脚本？
3. 是否需要修改参数（高度/透明度）？
4. 是否需要添加其他功能？

---

**文档版本**：Build Guide v1.0  
**最后更新**：2026-09-03
