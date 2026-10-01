package com.simple.edgeblocker;

import android.content.Intent;
import android.content.SharedPreferences;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.util.Log;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.TextView;
import android.widget.Toast;

import androidx.appcompat.app.AppCompatActivity;
import androidx.core.content.ContextCompat;

public class MainActivity extends AppCompatActivity {
    
    private static final String TAG = "MainActivity";
    private static final int REQUEST_CODE_OVERLAY_PERMISSION = 1001;
    
    private Button btnStart;
    private Button btnStop;
    private TextView tvStatus;
    private CheckBox cbAutoStart;
    private CheckBox cbLeftEdge;
    private CheckBox cbRightEdge;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final Runnable statusRunnable = new Runnable() {
        @Override
        public void run() {
            refreshStatus();
            if (!isFinishing() && !isDestroyed()) {
                handler.postDelayed(this, 1000);
            }
        }
    };
    private SharedPreferences prefs;
    
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);
        
        Log.i(TAG, "MainActivity 启动");
        
        prefs = getSharedPreferences(EdgeBlockerPrefs.PREFS_NAME, MODE_PRIVATE);
        
        btnStart = findViewById(R.id.btnStart);
        btnStop = findViewById(R.id.btnStop);
        tvStatus = findViewById(R.id.tvStatus);
        cbAutoStart = findViewById(R.id.cbAutoStart);
        cbLeftEdge = findViewById(R.id.cbLeftEdge);
        cbRightEdge = findViewById(R.id.cbRightEdge);
        
        // 恢复自动启动设置
        cbAutoStart.setChecked(prefs.getBoolean(EdgeBlockerPrefs.KEY_AUTO_START_ENABLED, true));
        cbLeftEdge.setChecked(prefs.getBoolean(EdgeBlockerPrefs.KEY_LEFT_EDGE_ENABLED, true));
        cbRightEdge.setChecked(prefs.getBoolean(EdgeBlockerPrefs.KEY_RIGHT_EDGE_ENABLED, true));
        
        // 自动启动开关
        cbAutoStart.setOnCheckedChangeListener((buttonView, isChecked) -> {
            prefs.edit().putBoolean(EdgeBlockerPrefs.KEY_AUTO_START_ENABLED, isChecked).apply();
            Log.i(TAG, "自动启动设置已" + (isChecked ? "启用" : "禁用"));
            Toast.makeText(this, "开机自启动已" + (isChecked ? "启用" : "禁用"), Toast.LENGTH_SHORT).show();
        });

        cbLeftEdge.setOnCheckedChangeListener((buttonView, isChecked) -> {
            prefs.edit().putBoolean(EdgeBlockerPrefs.KEY_LEFT_EDGE_ENABLED, isChecked).apply();
            refreshRunningOverlays();
        });

        cbRightEdge.setOnCheckedChangeListener((buttonView, isChecked) -> {
            prefs.edit().putBoolean(EdgeBlockerPrefs.KEY_RIGHT_EDGE_ENABLED, isChecked).apply();
            refreshRunningOverlays();
        });
        
        // 启动按钮
        btnStart.setOnClickListener(v -> {
            Log.i(TAG, "点击启动按钮");
            if (checkOverlayPermission()) {
                startService();
            } else {
                Log.w(TAG, "缺少悬浮窗权限");
                requestOverlayPermission();
            }
        });
        
        // 停止按钮
        btnStop.setOnClickListener(v -> {
            Log.i(TAG, "点击停止按钮");
            stopService();
        });
        
        refreshStatus();
    }
    
    @Override
    protected void onResume() {
        super.onResume();
        handler.removeCallbacks(statusRunnable);
        statusRunnable.run();
    }

    @Override
    protected void onPause() {
        super.onPause();
        handler.removeCallbacks(statusRunnable);
    }

    @Override
    protected void onDestroy() {
        handler.removeCallbacks(statusRunnable);
        super.onDestroy();
    }
    
    private void refreshStatus() {
        StringBuilder status = new StringBuilder();
        status.append("服务状态: ").append(EdgeBlockService.isRunning ? "运行中 ✓" : "已停止 ✗").append("\n");
        status.append("悬浮窗权限: ").append(checkOverlayPermission() ? "已授予 ✓" : "未授予 ✗").append("\n");
        status.append("顶部/底部: 目标 400px，空间不足时自动收缩\n");
        status.append("左侧 50px: ").append(cbLeftEdge.isChecked() ? "启用" : "禁用").append("\n");
        status.append("右侧 50px: ").append(cbRightEdge.isChecked() ? "启用" : "禁用");

        if (EdgeBlockService.lastError != null) {
            status.append("\n\n错误信息:\n").append(EdgeBlockService.lastError);
        } else if (EdgeBlockService.isRunning) {
            status.append("\n\n灰色半透明遮挡应出现在上下边缘，以及已启用的左右边缘。");
        }

        tvStatus.setText(status.toString());
    }
    
    private boolean checkOverlayPermission() {
        return Settings.canDrawOverlays(this);
    }
    
    private void requestOverlayPermission() {
        Intent intent = new Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                Uri.parse("package:" + getPackageName()));
        startActivityForResult(intent, REQUEST_CODE_OVERLAY_PERMISSION);
    }
    
    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode == REQUEST_CODE_OVERLAY_PERMISSION) {
            if (checkOverlayPermission()) {
                startService();
            } else {
                Toast.makeText(this, "需要悬浮窗权限才能运行", Toast.LENGTH_SHORT).show();
            }
        }
    }
    
    private void startService() {
        try {
            Intent intent = new Intent(this, EdgeBlockService.class);
            ContextCompat.startForegroundService(this, intent);
            Toast.makeText(this, "边缘遮挡已启动", Toast.LENGTH_SHORT).show();
            Log.i(TAG, "服务启动命令已发送");
            handler.postDelayed(this::refreshStatus, 300);
        } catch (Exception e) {
            Log.e(TAG, "启动服务失败", e);
            Toast.makeText(this, "启动失败: " + e.getMessage(), Toast.LENGTH_LONG).show();
        }
    }
    
    private void stopService() {
        try {
            Intent intent = new Intent(this, EdgeBlockService.class);
            stopService(intent);
            Toast.makeText(this, "边缘遮挡已停止", Toast.LENGTH_SHORT).show();
            Log.i(TAG, "服务停止命令已发送");
            handler.postDelayed(this::refreshStatus, 300);
        } catch (Exception e) {
            Log.e(TAG, "停止服务失败", e);
            Toast.makeText(this, "停止失败: " + e.getMessage(), Toast.LENGTH_LONG).show();
        }
    }

    private void refreshRunningOverlays() {
        if (EdgeBlockService.isRunning && checkOverlayPermission()) {
            try {
                ContextCompat.startForegroundService(this, new Intent(this, EdgeBlockService.class));
            } catch (Exception e) {
                Log.e(TAG, "刷新遮挡失败", e);
                Toast.makeText(this, "刷新遮挡失败: " + e.getMessage(), Toast.LENGTH_LONG).show();
            }
        }
        refreshStatus();
    }
}
