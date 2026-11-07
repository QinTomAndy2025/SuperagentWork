# WebRTC 手机 / 设备 Android App

把手机页面 `/phone` 和设备页面 `/device` 分别封装成两个 Android WebView 应用：

| 应用 | APK | 包名 | 默认页面 |
| --- | --- | --- | --- |
| WebRTC 手机 | `releases/webrtc-phone-debug.apk` | `com.shzhihuan.webrtcphone` | `/phone` |
| WebRTC 设备 | `releases/webrtc-device-debug.apk` | `com.shzhihuan.webrtcdevice` | `/device` |

两个应用可以同时安装（包名不同），分别适配手机和 Pad。

## 地址（写死，无手动设置）

两个应用都使用同一套地址策略，只差页面路径：

```java
PRIMARY_BASE   = "https://www.shzhihuan.cn:8443";
FALLBACK_BASE  = "https://192.168.1.110:8443";
PAGE_PATH      = "/phone";  // 手机 App
                = "/device"; // 设备 App
```

启动时优先访问域名；域名不可达时自动回退到局域网 IP；会记住上次成功的地址。应用内**没有地址设置界面**，两者都不可达时只显示“重试”。

因此：
- 手机/Pad 与服务器在同一局域网：即使域名暂时不通，也会自动回退到 `192.168.1.110`；
- 外网使用：需要先修正域名解析和 8443 端口映射，域名可用后应用会自动优先走域名。

## 共同功能

- 页面与浏览器一致：圆形头像通讯录、点击头像呼叫、全屏来电/通话、聊天抽屉、设置菜单、任一方结束自动退出
- 摄像头 / 麦克风（WebView `getUserMedia` 权限桥接）：首次进入应用时请求一次，通话之间复用同一路流；Android 系统授权后 WebView 自动放行，不会每次通话弹浏览器授权框
- 自签名证书首次确认：显示 SHA-256，确认后按主机记住指纹；证书变化会再次提示
- 手机/Pad 横竖屏旋转、通话保持屏幕常亮、加载进度、网络错误重试
- 外部链接用系统浏览器打开
- 设备 App 使用平板图标和“WebRTC 设备”名称，适合安装在 Pad

## 安装

1. 在手机/Pad 下载对应 APK；
2. 允许“安装未知来源应用”；
3. 打开应用，首次确认自签名证书；
4. 使用手机号或 12 位设备号登录；
5. 发起通话时再授权摄像头/麦克风。

也可以：

```bash
adb install -r releases/webrtc-phone-debug.apk
adb install -r releases/webrtc-device-debug.apk
```

> debug APK 使用 Android debug key 签名，适合内测/侧载，不适合上架应用商店。正式分发请生成自己的 release keystore 并签名。

## 从源码构建

需要 JDK 17、Android SDK（platform 34、build-tools 34.0.0）和 Gradle 8.7。

```bash
cd android
echo "sdk.dir=/path/to/android-sdk" > local.properties
export JAVA_HOME=/path/to/jdk-17

gradle assemblePhoneDebug
gradle assembleDeviceDebug

# 产物：
# app/build/outputs/apk/phone/debug/app-phone-debug.apk
# app/build/outputs/apk/device/debug/app-device-debug.apk
```

两个变体通过 Gradle product flavor `phone` / `device` 生成；共享同一份 `MainActivity`，只在
`applicationId`、`app_name`、`PAGE_PATH` 和图标上不同。项目不依赖 AndroidX/Material。

## 构建验证

- `gradle assemblePhoneDebug assembleDeviceDebug` 成功；两个 APK 均已用 Android debug key 签名，`apksigner verify` 通过。
- `aapt dump badging` 确认包名、启动 Activity、摄像头/麦克风/网络权限。
- 当前构建环境没有 KVM/实体 Android 设备，未能在模拟器或真机上启动 APK；封装页面本身已用 Playwright 在手机/Pad 尺寸下做端到端验证。请在设备上重点验证：自签名证书确认、摄像头/麦克风授权、横屏通话。
- APK SHA-256 见 [`releases/SHA256SUMS`](releases/SHA256SUMS)。

## 页面更新与 APK

App 只是服务器页面的原生外壳，**页面 UI 更新不需要重新安装 APK**：服务器更新后重新打开应用（或在应用内刷新）即可看到新界面；只有地址、包名、权限等原生逻辑变化时才需要重新构建 APK。

## 限制

- WebView 的 WebRTC 能力依赖系统 WebView 版本；Android 8+ 一般可用。
- 没有推送通知、后台来电、离线缓存；需要能访问服务器。
- 自签名证书采用 TOFU（首次信任）方式，不是受信任 CA 证书。
- 应用只是页面的原生外壳，账号/联系人/通话逻辑都在服务端和网页中。
