package com.simple.edgeblocker;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.util.Log;

public class BootReceiver extends BroadcastReceiver {
    
    private static final String TAG = "BootReceiver";
    
    @Override
    public void onReceive(Context context, Intent intent) {
        if (Intent.ACTION_BOOT_COMPLETED.equals(intent.getAction())) {
            Log.i(TAG, "收到开机广播");
            
            // 检查用户是否启用了自动启动
            SharedPreferences prefs = context.getSharedPreferences("EdgeBlockerPrefs", Context.MODE_PRIVATE);
            boolean autoStart = prefs.getBoolean("auto_start_enabled", true); // 默认启用
            
            if (autoStart) {
                Log.i(TAG, "自动启动已启用，启动服务...");
                try {
                    Intent serviceIntent = new Intent(context, EdgeBlockService.class);
                    context.startService(serviceIntent);
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
