package com.shzhihuan.webrtcphone;

import android.Manifest;
import android.annotation.SuppressLint;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.net.http.SslError;
import android.os.Bundle;
import android.text.InputType;
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
import android.widget.EditText;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;

import java.security.MessageDigest;
import java.security.cert.X509Certificate;
import java.util.Locale;

/**
 * A minimal WebView shell for the phone page.
 *
 * The web app provides accounts, contacts and WebRTC; this activity adds:
 *   - camera/microphone permission bridging for WebView getUserMedia
 *   - trust-on-first-use confirmation for the LAN self-signed certificate
 *   - a server URL setting and an error/retry screen
 */
public class MainActivity extends Activity {
    private static final String TAG = "WebRTCPhone";
    private static final String PREFS = "webrtc_phone";
    private static final String KEY_URL = "server_url";
    private static final String KEY_TRUST_PREFIX = "trusted_cert_";
    private static final String DEFAULT_URL = "https://192.168.1.110:8443/phone";
    private static final int REQUEST_MEDIA_PERMISSIONS = 1001;

    private WebView webView;
    private ProgressBar progress;
    private View errorView;
    private TextView errorMessage;
    private TextView serverLabel;
    private SharedPreferences prefs;
    private String currentUrl;
    private PermissionRequest pendingPermissionRequest;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);

        prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        currentUrl = prefs.getString(KEY_URL, DEFAULT_URL);

        webView = findViewById(R.id.web_view);
        progress = findViewById(R.id.progress);
        errorView = findViewById(R.id.error_view);
        errorMessage = findViewById(R.id.error_message);
        serverLabel = findViewById(R.id.server_label);

        Button settingsButton = findViewById(R.id.settings_button);
        Button retryButton = findViewById(R.id.retry_button);
        Button errorSettingsButton = findViewById(R.id.error_settings_button);
        settingsButton.setOnClickListener(view -> showSettingsDialog());
        errorSettingsButton.setOnClickListener(view -> showSettingsDialog());
        retryButton.setOnClickListener(view -> loadServer());

        configureWebView();
        loadServer();
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
                errorView.setVisibility(View.GONE);
                progress.setVisibility(View.VISIBLE);
                progress.setProgress(0);
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                progress.setVisibility(View.GONE);
                updateServerLabel(url);
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request == null || request.isForMainFrame()) {
                    String description = error == null ? getString(R.string.error_default)
                            : error.getDescription().toString();
                    showError(description);
                }
            }

            @SuppressWarnings("deprecation")
            @Override
            public void onReceivedError(WebView view, int errorCode, String description, String failingUrl) {
                if (failingUrl != null && failingUrl.equals(webView.getUrl())) {
                    showError(description);
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
        String currentHost = Uri.parse(currentUrl).getHost();
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

    private void loadServer() {
        errorView.setVisibility(View.GONE);
        updateServerLabel(currentUrl);
        webView.loadUrl(currentUrl);
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
        errorMessage.setText(description == null || description.isEmpty()
                ? getString(R.string.error_default)
                : description + "\n\n" + getString(R.string.error_default));
        errorView.setVisibility(View.VISIBLE);
        errorView.bringToFront();
    }

    private void handleSslError(SslErrorHandler handler, SslError error) {
        String host = Uri.parse(error.getUrl() != null ? error.getUrl() : currentUrl).getHost();
        String fingerprint = certificateFingerprint(error);
        String key = KEY_TRUST_PREFIX + host;
        String trusted = prefs.getString(key, null);

        if (fingerprint != null && fingerprint.equals(trusted)) {
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
                    if (fingerprint != null && host != null) {
                        prefs.edit().putString(key, fingerprint).apply();
                    }
                    handler.proceed();
                })
                .setNegativeButton(R.string.cancel, (dialog, which) -> handler.cancel())
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

    private void showSettingsDialog() {
        final EditText input = new EditText(this);
        input.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        input.setText(currentUrl);
        input.setSelectAllOnFocus(true);

        new AlertDialog.Builder(this)
                .setTitle(R.string.server_settings)
                .setMessage(getString(R.string.server_hint)
                        + "\n\n默认地址：" + DEFAULT_URL)
                .setView(input)
                .setPositiveButton(R.string.save_and_connect, (dialog, which) -> {
                    String url = input.getText().toString().trim();
                    if (!url.startsWith("https://")) {
                        Toast.makeText(this, R.string.invalid_url, Toast.LENGTH_LONG).show();
                        return;
                    }
                    Uri uri = Uri.parse(url);
                    if (uri.getHost() == null) {
                        Toast.makeText(this, R.string.invalid_url, Toast.LENGTH_LONG).show();
                        return;
                    }
                    currentUrl = url;
                    prefs.edit().putString(KEY_URL, url).apply();
                    loadServer();
                })
                .setNeutralButton(R.string.clear_cert, (dialog, which) -> {
                    SharedPreferences.Editor editor = prefs.edit();
                    for (String key : prefs.getAll().keySet()) {
                        if (key.startsWith(KEY_TRUST_PREFIX)) editor.remove(key);
                    }
                    editor.apply();
                    Toast.makeText(this, R.string.cert_cleared, Toast.LENGTH_SHORT).show();
                })
                .setNegativeButton(R.string.cancel, null)
                .show();
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
        if (webView != null) {
            webView.loadUrl("about:blank");
            webView.destroy();
            webView = null;
        }
        super.onDestroy();
    }
}
