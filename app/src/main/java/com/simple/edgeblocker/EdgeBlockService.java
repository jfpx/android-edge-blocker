package com.simple.edgeblocker;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.res.Configuration;
import android.graphics.Color;
import android.graphics.PixelFormat;
import android.graphics.Point;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.provider.Settings;
import android.util.Log;
import android.view.Gravity;
import android.view.View;
import android.view.WindowMetrics;
import android.view.WindowManager;
import android.widget.FrameLayout;

import androidx.core.app.NotificationCompat;

public class EdgeBlockService extends Service {

    private static final String TAG = "EdgeBlockService";
    private static final String ACTION_STOP = "com.simple.edgeblocker.action.STOP";
    private static final String CHANNEL_ID = "edge_blocker_channel";
    private static final int NOTIFICATION_ID = 1;
    private static final long PERMISSION_CHECK_INTERVAL_MS = 2000L;

    public static boolean isRunning = false;
    public static String lastError = null;

    private final Handler handler = new Handler(Looper.getMainLooper());
    private final Runnable permissionCheckRunnable = new Runnable() {
        @Override
        public void run() {
            if (!hasOverlayPermission()) {
                handleFatalError("悬浮窗权限已撤销", null);
                return;
            }
            handler.postDelayed(this, PERMISSION_CHECK_INTERVAL_MS);
        }
    };

    private WindowManager windowManager;
    private FrameLayout topBlockView;
    private FrameLayout bottomBlockView;
    private FrameLayout leftBlockView;
    private FrameLayout rightBlockView;
    private boolean stoppingForError = false;

    @Override
    public void onCreate() {
        super.onCreate();
        Log.i(TAG, "服务创建中...");

        try {
            createNotificationChannel();
            startForeground(NOTIFICATION_ID, createNotification());
            windowManager = (WindowManager) getSystemService(WINDOW_SERVICE);
            Log.i(TAG, "前台通知已创建");
        } catch (Exception e) {
            handleFatalError("创建失败: " + e.getMessage(), e);
        }
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (stoppingForError) {
            stopSelf();
            return START_NOT_STICKY;
        }
        if (intent != null && ACTION_STOP.equals(intent.getAction())) {
            stoppingForError = false;
            lastError = null;
            stopSelf();
            return START_NOT_STICKY;
        }

        if (!hasOverlayPermission()) {
            handleFatalError("缺少悬浮窗权限", null);
            return START_NOT_STICKY;
        }

        applyOverlayLayout();
        if (!isRunning) {
            return START_NOT_STICKY;
        }
        startPermissionChecks();
        return START_STICKY;
    }

    @Override
    public void onConfigurationChanged(Configuration newConfig) {
        super.onConfigurationChanged(newConfig);
        if (windowManager != null && isRunning && hasOverlayPermission()) {
            Log.i(TAG, "配置已变化，重新布局覆盖层");
            applyOverlayLayout();
        }
    }

    @Override
    public void onDestroy() {
        Log.i(TAG, "服务销毁中...");
        handler.removeCallbacks(permissionCheckRunnable);
        removeAllBlockViews();
        isRunning = false;
        if (!stoppingForError) {
            lastError = null;
        }
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    private void applyOverlayLayout() {
        try {
            removeAllBlockViews();
            EdgeOverlayGeometry.Geometry geometry = calculateGeometry();

            if (geometry.topHeightPx > 0) {
                topBlockView = createBlockView("TOP");
                windowManager.addView(topBlockView, createLayoutParams(
                    geometry.screenWidthPx,
                    geometry.topHeightPx,
                    Gravity.TOP | Gravity.LEFT,
                    0,
                    0
                ));
            }

            if (geometry.bottomHeightPx > 0) {
                bottomBlockView = createBlockView("BOTTOM");
                windowManager.addView(bottomBlockView, createLayoutParams(
                    geometry.screenWidthPx,
                    geometry.bottomHeightPx,
                    Gravity.BOTTOM | Gravity.LEFT,
                    0,
                    0
                ));
            }

            if (geometry.hasLeftOverlay()) {
                leftBlockView = createBlockView("LEFT");
                windowManager.addView(leftBlockView, createLayoutParams(
                        geometry.leftWidthPx,
                        geometry.centerHeightPx,
                        Gravity.TOP | Gravity.LEFT,
                        0,
                        geometry.topHeightPx
                ));
            }

            if (geometry.hasRightOverlay()) {
                rightBlockView = createBlockView("RIGHT");
                windowManager.addView(rightBlockView, createLayoutParams(
                        geometry.rightWidthPx,
                        geometry.centerHeightPx,
                        Gravity.TOP | Gravity.RIGHT,
                        0,
                        geometry.topHeightPx
                ));
            }

            stoppingForError = false;
            isRunning = true;
            lastError = null;
            updateNotification();
            Log.i(TAG, "覆盖层已更新: top=" + geometry.topHeightPx
                    + ", bottom=" + geometry.bottomHeightPx
                    + ", left=" + geometry.leftWidthPx
                    + ", right=" + geometry.rightWidthPx
                    + ", center=" + geometry.centerHeightPx);
        } catch (Exception e) {
            handleFatalError("覆盖层创建失败: " + e.getMessage(), e);
        }
    }

    private FrameLayout createBlockView(String position) {
        FrameLayout view = new FrameLayout(this);
        view.setBackgroundColor(Color.argb(26, 128, 128, 128));
        // 独立窗口本身就会拦截命中的触摸，不额外消费 view 事件以保持原有语义。
        Log.i(TAG, position + " 视图已创建，颜色=灰色，透明度=10%");
        return view;
    }

    private WindowManager.LayoutParams createLayoutParams(int width, int height, int gravity, int x, int y) {
        WindowManager.LayoutParams params = new WindowManager.LayoutParams();
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            params.type = WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY;
        } else {
            params.type = WindowManager.LayoutParams.TYPE_SYSTEM_ALERT;
        }
        params.flags = WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                | WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL
                | WindowManager.LayoutParams.FLAG_WATCH_OUTSIDE_TOUCH
                | WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN
                | WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS;
        // Geometry and gravity must use the same physical display, not navigation-bar insets.
        params.systemUiVisibility = View.SYSTEM_UI_FLAG_LAYOUT_STABLE
                | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            params.setFitInsetsTypes(0);
            params.layoutInDisplayCutoutMode = WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_ALWAYS;
        } else if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            params.layoutInDisplayCutoutMode = WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES;
        }
        params.format = PixelFormat.TRANSLUCENT;
        params.width = Math.max(1, width);
        params.height = Math.max(1, height);
        params.gravity = gravity;
        params.x = x;
        params.y = y;
        return params;
    }

    private void createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationChannel channel = new NotificationChannel(
                    CHANNEL_ID,
                    "Edge Blocker Service",
                    NotificationManager.IMPORTANCE_LOW
            );
            channel.setDescription("Keeps edge blocking active");
            channel.setShowBadge(false);

            NotificationManager manager = getSystemService(NotificationManager.class);
            if (manager != null) {
                manager.createNotificationChannel(channel);
            }
        }
    }

    private Notification createNotification() {
        PendingIntent openPendingIntent = PendingIntent.getActivity(
                this,
                0,
                new Intent(this, MainActivity.class),
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE
        );
        PendingIntent stopPendingIntent = PendingIntent.getService(
                this,
                1,
                new Intent(this, EdgeBlockService.class).setAction(ACTION_STOP),
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE
        );

        return new NotificationCompat.Builder(this, CHANNEL_ID)
                .setContentTitle("Edge Blocker Active")
                .setContentText("Blocking top/bottom 400px and optional left/right 50px")
                .setSmallIcon(android.R.drawable.ic_menu_view)
                .setContentIntent(openPendingIntent)
                .setPriority(NotificationCompat.PRIORITY_LOW)
                .setOngoing(true)
                .setShowWhen(false)
                .addAction(0, "Stop", stopPendingIntent)
                .build();
    }

    private EdgeOverlayGeometry.Geometry calculateGeometry() {
        Point displaySize = new Point();
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            WindowMetrics metrics = windowManager.getCurrentWindowMetrics();
            displaySize.x = Math.max(1, metrics.getBounds().width());
            displaySize.y = Math.max(1, metrics.getBounds().height());
        } else {
            windowManager.getDefaultDisplay().getRealSize(displaySize);
            displaySize.x = Math.max(1, displaySize.x);
            displaySize.y = Math.max(1, displaySize.y);
        }

        SharedPreferences prefs = getSharedPreferences(EdgeBlockerPrefs.PREFS_NAME, MODE_PRIVATE);
        boolean leftEnabled = prefs.getBoolean(EdgeBlockerPrefs.KEY_LEFT_EDGE_ENABLED, true);
        boolean rightEnabled = prefs.getBoolean(EdgeBlockerPrefs.KEY_RIGHT_EDGE_ENABLED, true);
        return EdgeOverlayGeometry.calculate(displaySize.x, displaySize.y, leftEnabled, rightEnabled);
    }

    private void removeAllBlockViews() {
        removeView(rightBlockView);
        rightBlockView = null;
        removeView(leftBlockView);
        leftBlockView = null;
        removeView(bottomBlockView);
        bottomBlockView = null;
        removeView(topBlockView);
        topBlockView = null;
    }

    private void removeView(FrameLayout view) {
        if (view == null || windowManager == null) {
            return;
        }
        try {
            windowManager.removeViewImmediate(view);
        } catch (IllegalArgumentException e) {
            Log.w(TAG, "覆盖层已不在窗口中", e);
        }
    }

    private void startPermissionChecks() {
        handler.removeCallbacks(permissionCheckRunnable);
        handler.postDelayed(permissionCheckRunnable, PERMISSION_CHECK_INTERVAL_MS);
    }

    private boolean hasOverlayPermission() {
        return Build.VERSION.SDK_INT < Build.VERSION_CODES.M || Settings.canDrawOverlays(this);
    }

    private void handleFatalError(String message, Exception exception) {
        stoppingForError = true;
        lastError = message;
        isRunning = false;
        Log.e(TAG, message, exception);
        handler.removeCallbacks(permissionCheckRunnable);
        removeAllBlockViews();
        stopSelf();
    }

    private void updateNotification() {
        NotificationManager notificationManager = getSystemService(NotificationManager.class);
        if (notificationManager != null) {
            notificationManager.notify(NOTIFICATION_ID, createNotification());
        }
    }
}
