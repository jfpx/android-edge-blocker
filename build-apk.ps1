# Simple Edge Blocker - 一键构建脚本
# 前提：Java 17、Android SDK；使用仓库 Gradle Wrapper

param(
    [string]$AndroidSdkPath = $env:ANDROID_HOME,
    [switch]$Debug,
    [switch]$Release
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$projectRoot = Split-Path -Parent $PSCommandPath
if ($Debug -and $Release) {
    throw "请选择 -Debug 或 -Release，不可同时使用"
}
if (-not $AndroidSdkPath) { $AndroidSdkPath = $env:ANDROID_SDK_ROOT }

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Simple Edge Blocker - 自动构建" -ForegroundColor Cyan
Write-Host "========================================`n" -ForegroundColor Cyan

# 检查 Android SDK
if (-not $AndroidSdkPath) {
    Write-Host "❌ 未找到 Android SDK" -ForegroundColor Red
    Write-Host "请设置环境变量 ANDROID_HOME 或使用 -AndroidSdkPath 参数" -ForegroundColor Yellow
    Write-Host "`n常见路径：" -ForegroundColor Gray
    Write-Host "  Windows: C:\Users\<用户名>\AppData\Local\Android\Sdk" -ForegroundColor Gray
    Write-Host "  Mac/Linux: ~/Android/Sdk" -ForegroundColor Gray
    exit 1
}

if (-not (Test-Path -LiteralPath $AndroidSdkPath -PathType Container)) {
    Write-Host "❌ Android SDK 路径不存在: $AndroidSdkPath" -ForegroundColor Red
    exit 1
}

Write-Host "✓ Android SDK: $AndroidSdkPath`n" -ForegroundColor Green

# 设置环境变量
$env:ANDROID_HOME = (Resolve-Path -LiteralPath $AndroidSdkPath).Path
$env:ANDROID_SDK_ROOT = $env:ANDROID_HOME

# 检查 Gradle
$gradleCmd = Join-Path $projectRoot "gradlew.bat"
if (-not (Test-Path -LiteralPath $gradleCmd -PathType Leaf)) {
    throw "缺少仓库 Gradle Wrapper: gradlew.bat"
}
Write-Host "✓ 使用项目自带的 Gradle Wrapper`n" -ForegroundColor Green

# 导航到项目目录
Push-Location $projectRoot

try {
    # 构建
    if ($Release) {
        Write-Host "[1/2] 构建 Release 版本..." -ForegroundColor Yellow
        & $gradleCmd :app:assembleRelease
        $buildType = "release"
        $apkName = "app-release-unsigned.apk"
    } else {
        Write-Host "[1/2] 构建 Debug 版本..." -ForegroundColor Yellow
        & $gradleCmd :app:assembleDebug
        $buildType = "debug"
        $apkName = "app-debug.apk"
    }

    if ($LASTEXITCODE -ne 0) {
        throw "构建失败"
    }
    Write-Host "✓ 构建完成`n" -ForegroundColor Green

    # 查找 APK
    Write-Host "[2/2] 定位 APK..." -ForegroundColor Yellow
    $apkPath = "app\build\outputs\apk\$buildType\$apkName"
    
    if (Test-Path $apkPath) {
        $fullPath = Resolve-Path $apkPath
        $size = (Get-Item $fullPath).Length / 1KB
        
        Write-Host "`n========================================" -ForegroundColor Cyan
        Write-Host "✅ 构建成功！" -ForegroundColor Green
        Write-Host "========================================" -ForegroundColor Cyan
        Write-Host "`nAPK 位置：" -ForegroundColor White
        Write-Host "  $fullPath" -ForegroundColor Gray
        Write-Host "`nAPK 大小：" -ForegroundColor White
        Write-Host "  $([math]::Round($size, 2)) KB" -ForegroundColor Gray
        
        if ($Release) {
            Write-Host "`n⚠️  注意：Release 版本需要签名才能安装" -ForegroundColor Yellow
            Write-Host "使用以下命令签名：" -ForegroundColor Gray
            Write-Host "  使用 SDK zipalign + apksigner，详见 README 签名说明" -ForegroundColor Gray
        } else {
            Write-Host "`n✓ Debug 版本可直接安装（已自动签名）" -ForegroundColor Green
        }
        
        # 复制到方便的位置
        $outputDir = "$projectRoot\build\distributions"
        if (-not (Test-Path $outputDir)) {
            New-Item -ItemType Directory -Path $outputDir | Out-Null
        }
        $finalApk = "$outputDir\SimpleEdgeBlocker-$buildType.apk"
        Copy-Item $apkPath $finalApk -Force
        
        Write-Host "`n已复制到：" -ForegroundColor White
        Write-Host "  $finalApk" -ForegroundColor Gray
        
    } else {
        throw "APK 未找到: $apkPath"
    }

} catch {
    Write-Host "`n❌ 构建失败：$($_.Exception.Message)" -ForegroundColor Red
    Write-Host "`n查看详细日志：" -ForegroundColor Yellow
    Write-Host "  $gradleCmd assembleDebug --stacktrace" -ForegroundColor Gray
    exit 1
} finally {
    Pop-Location
}

Write-Host "`n下一步：" -ForegroundColor Cyan
Write-Host "1. 将 APK 传输到 Android 设备" -ForegroundColor White
Write-Host "2. 在设备上安装" -ForegroundColor White
Write-Host "3. 打开应用，点击'启动遮挡'" -ForegroundColor White
Write-Host "4. 授予悬浮窗权限" -ForegroundColor White
