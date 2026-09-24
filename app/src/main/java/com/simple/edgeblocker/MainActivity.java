package com.simple.edgeblocker;

import android.content.Intent;
import android.content.SharedPreferences;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.provider.Settings;
import android.util.Log;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.TextView;
import android.widget.Toast;

import androidx.appcompat.app.AppCompatActivity;

public class MainActivity extends AppCompatActivity {
    
    private static final String TAG = "MainActivity";
    private static final int REQUEST_CODE_OVERLAY_PERMISSION = 1001;
    
    private Button btnStart;
    private Button btnStop;
    private TextView tvStatus;
    private CheckBox cbAutoStart;
    private Handler handler = new Handler();
    private SharedPreferences prefs;
    
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);
        
        Log.i(TAG, "MainActivity 启动");
        
        prefs = getSharedPreferences("EdgeBlockerPrefs", MODE_PRIVATE);
        
        btnStart = findViewById(R.id.btnStart);
        btnStop = findViewById(R.id.btnStop);
        tvStatus = findViewById(R.id.tvStatus);
        cbAutoStart = findViewById(R.id.cbAutoStart);
        
        // 恢复自动启动设置
        cbAutoStart.setChecked(prefs.getBoolean("auto_start_enabled", true));
        
        // 自动启动开关
        cbAutoStart.setOnCheckedChangeListener((buttonView, isChecked) -> {
            prefs.edit().putBoolean("auto_start_enabled", isChecked).apply();
            Log.i(TAG, "自动启动设置已" + (isChecked ? "启用" : "禁用"));
            Toast.makeText(this, "开机自启动已" + (isChecked ? "启用" : "禁用"), Toast.LENGTH_SHORT).show();
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
        
        // 定期更新状态
        updateStatus();
    }
    
    @Override
    protected void onResume() {
        super.onResume();
        updateStatus();
    }
    
    private void updateStatus() {
        handler.postDelayed(new Runnable() {
            @Override
            public void run() {
                StringBuilder status = new StringBuilder();
                status.append("服务状态: ").append(EdgeBlockService.isRunning ? "运行中 ✓" : "已停止 ✗").append("\n");
                status.append("Android 版本: ").append(Build.VERSION.RELEASE).append("\n");
                status.append("API Level: ").append(Build.VERSION.SDK_INT).append("\n");
                status.append("悬浮窗权限: ").append(checkOverlayPermission() ? "已授予 ✓" : "未授予 ✗").append("\n");
                
                if (EdgeBlockService.lastError != null) {
                    status.append("\n错误信息:\n").append(EdgeBlockService.lastError);
                }
                
                if (EdgeBlockService.isRunning) {
                    status.append("\n\n应该可以看到屏幕顶部和底部的红色半透明框");
                }
                
                tvStatus.setText(status.toString());
                
                // 继续更新
                if (!isFinishing()) {
                    handler.postDelayed(this, 1000);
                }
            }
        }, 100);
    }
    
    private boolean checkOverlayPermission() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            return Settings.canDrawOverlays(this);
        }
        return true; // Android 6.0 以下默认有权限
    }
    
    private void requestOverlayPermission() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            Intent intent = new Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                    Uri.parse("package:" + getPackageName()));
            startActivityForResult(intent, REQUEST_CODE_OVERLAY_PERMISSION);
        }
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
            startService(intent);
            Toast.makeText(this, "边缘遮挡已启动", Toast.LENGTH_SHORT).show();
            Log.i(TAG, "服务启动命令已发送");
            
            // 1秒后更新状态
            handler.postDelayed(this::updateStatus, 1000);
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
            
            // 1秒后更新状态
            handler.postDelayed(this::updateStatus, 1000);
        } catch (Exception e) {
            Log.e(TAG, "停止服务失败", e);
            Toast.makeText(this, "停止失败: " + e.getMessage(), Toast.LENGTH_LONG).show();
        }
    }
}
