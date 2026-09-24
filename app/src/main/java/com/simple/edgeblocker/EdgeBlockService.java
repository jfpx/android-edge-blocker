package com.simple.edgeblocker;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
import android.graphics.Color;
import android.graphics.PixelFormat;
import android.os.Build;
import android.os.IBinder;
import android.util.Log;
import android.view.Gravity;
import android.view.WindowManager;
import android.widget.FrameLayout;
import androidx.core.app.NotificationCompat;

public class EdgeBlockService extends Service {
    
    private static final String TAG = "EdgeBlockService";
    
    private WindowManager windowManager;
    private FrameLayout topBlockView;
    private FrameLayout bottomBlockView;
    
    // 配置参数（可以后续改为可调节）
    private static final int BLOCK_HEIGHT = 400; // 400px
    private static final float ALPHA = 0.5f;      // 50% 透明度
    private static final String CHANNEL_ID = "edge_blocker_channel";
    private static final int NOTIFICATION_ID = 1;
    
    // 状态标志
    public static boolean isRunning = false;
    public static String lastError = null;
    
    @Override
    public void onCreate() {
        super.onCreate();
        
        Log.i(TAG, "服务创建中...");
        
        try {
            // 创建前台通知
            createNotificationChannel();
            startForeground(NOTIFICATION_ID, createNotification());
            Log.i(TAG, "前台通知已创建");
            
            // 创建遮挡视图
            windowManager = (WindowManager) getSystemService(WINDOW_SERVICE);
            createBlockViews();
            
            isRunning = true;
            lastError = null;
            Log.i(TAG, "服务创建成功");
        } catch (Exception e) {
            Log.e(TAG, "服务创建失败", e);
            lastError = "创建失败: " + e.getMessage();
            isRunning = false;
        }
    }
    
    private void createBlockViews() {
        Log.i(TAG, "创建覆盖层视图...");
        
        try {
            // 创建顶部遮挡视图
            topBlockView = createBlockView("TOP");
            WindowManager.LayoutParams topParams = createLayoutParams();
            topParams.gravity = Gravity.TOP | Gravity.START;
            topParams.x = 0;
            topParams.y = 0;
            
            Log.i(TAG, "顶部视图参数: width=" + topParams.width + ", height=" + topParams.height + 
                      ", type=" + topParams.type + ", flags=" + topParams.flags);
            
            // 创建底部遮挡视图
            bottomBlockView = createBlockView("BOTTOM");
            WindowManager.LayoutParams bottomParams = createLayoutParams();
            bottomParams.gravity = Gravity.BOTTOM | Gravity.START;
            bottomParams.x = 0;
            bottomParams.y = 0;
            
            Log.i(TAG, "底部视图参数: width=" + bottomParams.width + ", height=" + bottomParams.height);
            
            // 添加到窗口
            windowManager.addView(topBlockView, topParams);
            Log.i(TAG, "顶部覆盖层已添加");
            
            windowManager.addView(bottomBlockView, bottomParams);
            Log.i(TAG, "底部覆盖层已添加");
            
            Log.i(TAG, "覆盖层创建完成！应该可以看到淡灰色半透明框了（几乎不可见）");
        } catch (Exception e) {
            Log.e(TAG, "创建覆盖层失败", e);
            lastError = "覆盖层创建失败: " + e.getMessage();
            throw e;
        }
    }
    
    private FrameLayout createBlockView(String position) {
        FrameLayout view = new FrameLayout(this);
        
        // 10% 不透明的灰色（几乎不可见，但提供轻微视觉提示）
        // alpha=26 约等于 10% 不透明度 (26/255 ≈ 0.1)
        view.setBackgroundColor(Color.argb(26, 128, 128, 128)); // 10% 灰色
        
        Log.i(TAG, position + " 视图已创建，颜色=灰色，透明度=10%");
        return view;
    }
    
    private WindowManager.LayoutParams createLayoutParams() {
        WindowManager.LayoutParams params = new WindowManager.LayoutParams();
        
        // 窗口类型（Android 8.0+ 使用 TYPE_APPLICATION_OVERLAY）
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            params.type = WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY;
            Log.i(TAG, "使用 TYPE_APPLICATION_OVERLAY (Android 8.0+)");
        } else {
            params.type = WindowManager.LayoutParams.TYPE_SYSTEM_ALERT;
            Log.i(TAG, "使用 TYPE_SYSTEM_ALERT (Android 6-7)");
        }
        
        // 窗口标志：不可聚焦，不阻止触摸事件传递到下层
        params.flags = WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                     | WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL
                     | WindowManager.LayoutParams.FLAG_WATCH_OUTSIDE_TOUCH
                     | WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN
                     | WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS;  // 允许超出屏幕边界
        
        // 像素格式（支持透明度）
        params.format = PixelFormat.TRANSLUCENT;
        
        // 尺寸：全宽，高度为 BLOCK_HEIGHT
        params.width = WindowManager.LayoutParams.MATCH_PARENT;
        params.height = BLOCK_HEIGHT;
        
        Log.i(TAG, "窗口参数: width=MATCH_PARENT, height=" + BLOCK_HEIGHT + "px");
        
        return params;
    }
    
    /**
     * 创建通知渠道（Android 8.0+ 必需）
     */
    private void createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationChannel channel = new NotificationChannel(
                CHANNEL_ID,
                "Edge Blocker Service",
                NotificationManager.IMPORTANCE_LOW  // 低优先级，不打扰用户
            );
            channel.setDescription("Keeps edge blocking active");
            channel.setShowBadge(false);
            
            NotificationManager manager = getSystemService(NotificationManager.class);
            if (manager != null) {
                manager.createNotificationChannel(channel);
            }
        }
    }
    
    /**
     * 创建前台服务通知
     */
    private Notification createNotification() {
        // 点击通知打开主界面
        Intent intent = new Intent(this, MainActivity.class);
        PendingIntent pendingIntent = PendingIntent.getActivity(
            this, 
            0, 
            intent, 
            PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE
        );
        
        return new NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("Edge Blocker Active")
            .setContentText("Blocking " + BLOCK_HEIGHT + "px at top and bottom")
            .setSmallIcon(android.R.drawable.ic_menu_view)  // 系统图标
            .setContentIntent(pendingIntent)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setOngoing(true)  // 不可清除
            .setShowWhen(false)
            .build();
    }
    
    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        return START_STICKY; // 服务被杀后自动重启
    }
    
    @Override
    public void onDestroy() {
        super.onDestroy();
        
        Log.i(TAG, "服务销毁中...");
        
        try {
            if (topBlockView != null) {
                windowManager.removeView(topBlockView);
                Log.i(TAG, "顶部覆盖层已移除");
            }
            if (bottomBlockView != null) {
                windowManager.removeView(bottomBlockView);
                Log.i(TAG, "底部覆盖层已移除");
            }
            
            isRunning = false;
            Log.i(TAG, "服务已停止");
        } catch (Exception e) {
            Log.e(TAG, "销毁服务时出错", e);
        }
    }
    
    @Override
    public IBinder onBind(Intent intent) {
        return null; // 不需要绑定
    }
}
