package com.simple.edgeblocker;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Build;
import android.provider.Settings;
import android.util.Log;

import androidx.core.content.ContextCompat;

public class BootReceiver extends BroadcastReceiver {
    
    private static final String TAG = "BootReceiver";
    
    @Override
    public void onReceive(Context context, Intent intent) {
        if (Intent.ACTION_BOOT_COMPLETED.equals(intent.getAction())) {
            Log.i(TAG, "收到开机广播");
            
            // 检查用户是否启用了自动启动
            SharedPreferences prefs = context.getSharedPreferences(EdgeBlockerPrefs.PREFS_NAME, Context.MODE_PRIVATE);
            boolean autoStart = prefs.getBoolean(EdgeBlockerPrefs.KEY_AUTO_START_ENABLED, true);
            
            if (autoStart) {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M && !Settings.canDrawOverlays(context)) {
                    Log.w(TAG, "缺少悬浮窗权限，跳过自动启动");
                    return;
                }

                Log.i(TAG, "自动启动已启用，启动服务...");
                try {
                    Intent serviceIntent = new Intent(context, EdgeBlockService.class);
                    ContextCompat.startForegroundService(context, serviceIntent);
                    Log.i(TAG, "服务启动命令已发送");
                } catch (Exception e) {
                    Log.e(TAG, "启动服务失败", e);
                }
            } else {
                Log.i(TAG, "自动启动已禁用，跳过");
            }
        }
    }
}
