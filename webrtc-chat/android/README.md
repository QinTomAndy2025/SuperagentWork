# WebRTC 手机 Android App

把 `/phone` 页面封装成 Android WebView 应用，供手机用户安装使用。

- 默认地址：`https://192.168.1.110:8443/phone`
- 支持摄像头 / 麦克风（WebView `getUserMedia` 权限桥接）
- 支持自签名证书首次确认（记住证书 SHA-256 指纹，证书变化会再次提示）
- 顶部“设置”可修改服务器地址、清除已信任证书
- 支持手机横竖屏旋转、通话时保持屏幕常亮、网络错误重试
- 回到手机页面；外部链接用系统浏览器打开

## 安装 APK

预构建的 debug APK：

[`releases/webrtc-phone-debug.apk`](releases/webrtc-phone-debug.apk)

1. 在手机浏览器中打开/下载该 APK；
2. 允许“安装未知来源应用”；
3. 安装后打开“WebRTC 手机”；
4. 首次会提示证书不受信任并显示 SHA-256，确认是 `192.168.1.110` 的服务器后点“继续访问”；
5. 允许摄像头和麦克风权限，注册/登录手机用户后使用。

也可以用 adb：

```bash
adb install -r releases/webrtc-phone-debug.apk
```

> debug APK 使用 Android debug key 签名，适合内测/侧载，不适合上架应用商店。正式分发请生成自己的 release keystore 并签名。

## 服务器地址

默认连接 `https://192.168.1.110:8443/phone`。如果使用域名或端口不同，点顶部“设置”修改，例如：

```
https://www.shzhihuan.cn:8443/phone
```

地址必须是 `https://`（应用禁用了明文 HTTP）。修改后立即重连。

## 从源码构建

需要 JDK 17、Android SDK（platform 34、build-tools 34.0.0）和 Gradle 8.7。

```bash
export JAVA_HOME=/path/to/jdk-17
export ANDROID_HOME=/path/to/android-sdk
echo "sdk.dir=$ANDROID_HOME" > local.properties
gradle assembleDebug
# 产物：app/build/outputs/apk/debug/app-debug.apk
```

本项目不依赖 AndroidX/Material；只使用 Android framework 和 WebView。

## 限制

- WebView 的 WebRTC 能力依赖系统 WebView 版本；Android 8+ 一般可用。
- 没有推送通知、后台来电、离线缓存；需要能访问服务器。
- 自签名证书采用 TOFU（首次信任）方式，不是受信任 CA 证书。
- 应用只是手机页面的原生外壳，账号/联系人/通话逻辑都在服务端和网页中。

## 构建验证

- `gradle assembleDebug` 成功；APK 已用 Android debug key 签名，`apksigner verify` 通过。
- `aapt dump badging` 确认包名 `com.shzhihuan.webrtcphone`、启动 Activity、摄像头/麦克风/网络权限。
- 当前构建环境没有 KVM/实体 Android 设备，未能在模拟器或真机上启动 APK；封装页面本身已用 Playwright 在手机尺寸下做端到端验证。请在手机上安装后重点验证：自签名证书确认、摄像头/麦克风授权、横屏通话。
- APK SHA-256 见 [`releases/SHA256SUMS`](releases/SHA256SUMS)。
