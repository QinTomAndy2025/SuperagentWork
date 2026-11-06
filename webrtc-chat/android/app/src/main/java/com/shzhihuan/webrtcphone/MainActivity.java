package com.shzhihuan.webrtcphone;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.net.http.SslError;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;
import android.view.View;
import android.view.WindowManager;
import android.webkit.ConsoleMessage;
import android.webkit.CookieManager;
import android.webkit.PermissionRequest;
import android.webkit.SslErrorHandler;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;

import java.security.MessageDigest;
import java.security.cert.X509Certificate;
import java.util.HashSet;
import java.util.Locale;
import java.util.Set;

/**
 * WebView shell for the phone page.
 *
 * The server address is fixed in the APK (no in-app URL setting):
 *   1. https://www.shzhihuan.cn:8443/phone
 *   2. fallback https://192.168.1.110:8443/phone
 *
 * The last successful base is remembered, so a LAN phone does not wait for
 * the public domain on every launch. The WebView also bridges camera/mic
 * permissions and confirms the self-signed certificate once per fingerprint.
 */
public class MainActivity extends Activity {
    private static final String TAG = "WebRTCPhone";
    private static final String PREFS = "webrtc_phone";
    private static final String KEY_LAST_BASE = "last_good_base";
    private static final String KEY_TRUST_PREFIX = "trusted_cert_";
    private static final String PHONE_PATH = "/phone";
    private static final String PRIMARY_BASE = "https://www.shzhihuan.cn:8443";
    private static final String FALLBACK_BASE = "https://192.168.1.110:8443";
    private static final int REQUEST_MEDIA_PERMISSIONS = 1001;
    private static final int LOAD_TIMEOUT_MS = 6000;

    private WebView webView;
    private ProgressBar progress;
    private View errorView;
    private TextView errorMessage;
    private TextView serverLabel;
    private SharedPreferences prefs;
    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private final Set<String> triedBases = new HashSet<>();

    private String currentBase;
    private String currentUrl;
    private int loadToken;
    private boolean pageFinished;
    private boolean pageFailed;
    private boolean sslPending;
    private PermissionRequest pendingPermissionRequest;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);

        prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        webView = findViewById(R.id.web_view);
        progress = findViewById(R.id.progress);
        errorView = findViewById(R.id.error_view);
        errorMessage = findViewById(R.id.error_message);
        serverLabel = findViewById(R.id.server_label);

        Button retryButton = findViewById(R.id.retry_button);
        retryButton.setOnClickListener(view -> retry());

        configureWebView();
        String lastBase = prefs.getString(KEY_LAST_BASE, PRIMARY_BASE);
        if (!PRIMARY_BASE.equals(lastBase) && !FALLBACK_BASE.equals(lastBase)) {
            lastBase = PRIMARY_BASE;
        }
        startLoad(lastBase);
    }

    private void configureWebView() {
        WebView.setWebContentsDebuggingEnabled(BuildConfig.DEBUG);

        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setMediaPlaybackRequiresUserGesture(false);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setUserAgentString(settings.getUserAgentString() + " WebRTCPhone/1.0");
        settings.setJavaScriptCanOpenWindowsAutomatically(false);
        settings.setSupportMultipleWindows(false);

        CookieManager.getInstance().setAcceptCookie(true);
        webView.setBackgroundColor(0xFF060A14);
        webView.setOverScrollMode(View.OVER_SCROLL_NEVER);

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                return openExternallyIfNeeded(request.getUrl());
            }

            @SuppressWarnings("deprecation")
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, String url) {
                return openExternallyIfNeeded(Uri.parse(url));
            }

            @Override
            public void onPageStarted(WebView view, String url, android.graphics.Bitmap favicon) {
                pageFinished = false;
                pageFailed = false;
                progress.setVisibility(View.VISIBLE);
                progress.setProgress(0);
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                if (url == null || !url.startsWith(currentBase)) return;
                pageFinished = true;
                progress.setVisibility(View.GONE);
                if (pageFailed) return;
                errorView.setVisibility(View.GONE);
                prefs.edit().putString(KEY_LAST_BASE, currentBase).apply();
                updateServerLabel(url);
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request == null || request.isForMainFrame()) {
                    if (request != null && request.getUrl() != null
                            && !request.getUrl().toString().startsWith(currentBase)) {
                        return;
                    }
                    pageFailed = true;
                    pageFinished = false;
                    progress.setVisibility(View.GONE);
                    if (!sslPending) {
                        tryFallbackOrError(error == null ? null : error.getDescription().toString());
                    }
                }
            }

            @SuppressWarnings("deprecation")
            @Override
            public void onReceivedError(WebView view, int errorCode, String description, String failingUrl) {
                if (failingUrl != null && failingUrl.startsWith(currentBase)) {
                    pageFailed = true;
                    pageFinished = false;
                    progress.setVisibility(View.GONE);
                    if (!sslPending) tryFallbackOrError(description);
                }
            }

            @Override
            public void onReceivedSslError(WebView view, SslErrorHandler handler, SslError error) {
                handleSslError(handler, error);
            }
        });

        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onProgressChanged(WebView view, int newProgress) {
                progress.setVisibility(newProgress >= 100 ? View.GONE : View.VISIBLE);
                progress.setProgress(newProgress);
            }

            @Override
            public void onPermissionRequest(final PermissionRequest request) {
                runOnUiThread(() -> {
                    if (hasMediaPermissions()) {
                        request.grant(request.getResources());
                    } else {
                        pendingPermissionRequest = request;
                        requestMediaPermissions();
                    }
                });
            }

            @Override
            public boolean onConsoleMessage(ConsoleMessage message) {
                Log.d(TAG, message.message() + " @" + message.sourceId() + ":" + message.lineNumber());
                return true;
            }
        });
    }

    private boolean openExternallyIfNeeded(Uri uri) {
        String scheme = uri.getScheme();
        if (scheme == null) return false;
        if (!scheme.equals("http") && !scheme.equals("https")) {
            try {
                startActivity(new Intent(Intent.ACTION_VIEW, uri));
            } catch (Exception ignored) {
                // no app can handle it
            }
            return true;
        }
        String currentHost = currentUrl == null ? null : Uri.parse(currentUrl).getHost();
        if (currentHost != null && currentHost.equalsIgnoreCase(uri.getHost())) {
            return false;
        }
        try {
            startActivity(new Intent(Intent.ACTION_VIEW, uri));
        } catch (Exception ignored) {
            // no browser available
        }
        return true;
    }

    private void startLoad(String base) {
        currentBase = base;
        currentUrl = base + PHONE_PATH;
        triedBases.add(base);
        pageFinished = false;
        pageFailed = false;
        sslPending = false;
        final int token = ++loadToken;

        errorView.setVisibility(View.GONE);
        progress.setVisibility(View.VISIBLE);
        progress.setProgress(0);
        updateServerLabel(currentUrl);

        webView.stopLoading();
        webView.loadUrl(currentUrl);

        mainHandler.postDelayed(() -> {
            if (token != loadToken || pageFinished || pageFailed || sslPending) return;
            tryFallbackOrError("连接超时");
        }, LOAD_TIMEOUT_MS);
    }

    private void tryFallbackOrError(String reason) {
        if (pageFinished || sslPending) return;
        String next = PRIMARY_BASE.equals(currentBase) ? FALLBACK_BASE : PRIMARY_BASE;
        if (!triedBases.contains(next)) {
            Log.i(TAG, "failed " + currentBase + " (" + reason + "), trying " + next);
            startLoad(next);
            return;
        }
        showError(reason);
    }

    private void retry() {
        triedBases.clear();
        pageFinished = false;
        pageFailed = false;
        sslPending = false;
        String base = currentBase == null ? PRIMARY_BASE : currentBase;
        startLoad(base);
    }

    private void updateServerLabel(String url) {
        Uri uri = Uri.parse(url);
        String host = uri.getHost();
        int port = uri.getPort();
        if (host == null) {
            serverLabel.setText("");
            return;
        }
        serverLabel.setText(port > 0 ? host + ":" + port : host);
    }

    private void showError(String description) {
        progress.setVisibility(View.GONE);
        String detail = description == null || description.isEmpty()
                ? getString(R.string.error_default)
                : description + "\n\n" + getString(R.string.error_default);
        errorMessage.setText(detail);
        errorView.setVisibility(View.VISIBLE);
        errorView.bringToFront();
    }

    private void handleSslError(SslErrorHandler handler, SslError error) {
        sslPending = true;
        String host = Uri.parse(error.getUrl() != null ? error.getUrl() : currentUrl).getHost();
        String fingerprint = certificateFingerprint(error);
        String key = KEY_TRUST_PREFIX + host;
        String trusted = prefs.getString(key, null);

        if (fingerprint != null && fingerprint.equals(trusted)) {
            sslPending = false;
            handler.proceed();
            return;
        }

        String message = "服务器 " + host + " 使用了浏览器不信任的证书。\n\n"
                + "SHA-256: " + (fingerprint == null ? "未知" : fingerprint)
                + "\n\n仅当你确认这是自己的局域网服务器时才继续。继续后，该证书指纹会被记住。";
        new AlertDialog.Builder(this)
                .setTitle(R.string.cert_title)
                .setMessage(message)
                .setCancelable(false)
                .setPositiveButton("继续访问", (dialog, which) -> {
                    sslPending = false;
                    if (fingerprint != null && host != null) {
                        prefs.edit().putString(key, fingerprint).apply();
                    }
                    handler.proceed();
                })
                .setNegativeButton(R.string.cancel, (dialog, which) -> {
                    sslPending = false;
                    pageFailed = true;
                    handler.cancel();
                    showError("证书未被信任");
                })
                .show();
    }

    private String certificateFingerprint(SslError error) {
        try {
            X509Certificate certificate = error.getCertificate().getX509Certificate();
            if (certificate == null) return null;
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            byte[] bytes = digest.digest(certificate.getEncoded());
            StringBuilder result = new StringBuilder();
            for (byte value : bytes) {
                if (result.length() > 0) result.append(':');
                result.append(String.format(Locale.US, "%02X", value & 0xFF));
            }
            return result.toString();
        } catch (Exception exception) {
            Log.w(TAG, "cannot read certificate fingerprint", exception);
            return null;
        }
    }

    private boolean hasMediaPermissions() {
        return checkSelfPermission(Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED
                && checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED;
    }

    private void requestMediaPermissions() {
        if (hasMediaPermissions()) {
            grantPendingPermission();
            return;
        }
        requestPermissions(new String[]{
                Manifest.permission.CAMERA,
                Manifest.permission.RECORD_AUDIO
        }, REQUEST_MEDIA_PERMISSIONS);
    }

    private void grantPendingPermission() {
        if (pendingPermissionRequest == null) return;
        pendingPermissionRequest.grant(pendingPermissionRequest.getResources());
        pendingPermissionRequest = null;
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode != REQUEST_MEDIA_PERMISSIONS || pendingPermissionRequest == null) return;
        boolean granted = grantResults.length > 0;
        for (int result : grantResults) {
            if (result != PackageManager.PERMISSION_GRANTED) granted = false;
        }
        if (granted) {
            grantPendingPermission();
        } else {
            pendingPermissionRequest.deny();
            pendingPermissionRequest = null;
            Toast.makeText(this, "未获得摄像头/麦克风权限，仍可使用文字聊天", Toast.LENGTH_LONG).show();
        }
    }

    @Override
    public void onBackPressed() {
        if (webView != null && webView.canGoBack()) {
            webView.goBack();
            return;
        }
        new AlertDialog.Builder(this)
                .setTitle(R.string.exit_title)
                .setMessage(R.string.exit_message)
                .setPositiveButton(R.string.exit, (dialog, which) -> finish())
                .setNegativeButton(R.string.cancel, null)
                .show();
    }

    @Override
    protected void onDestroy() {
        mainHandler.removeCallbacksAndMessages(null);
        if (webView != null) {
            webView.loadUrl("about:blank");
            webView.destroy();
            webView = null;
        }
        super.onDestroy();
    }
}
