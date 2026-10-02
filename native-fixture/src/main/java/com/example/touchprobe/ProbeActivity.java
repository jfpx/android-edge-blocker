package com.example.touchprobe;

import android.app.Activity;
import android.content.Intent;
import android.content.pm.ActivityInfo;
import android.content.res.Configuration;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.Point;
import android.os.Build;
import android.os.Bundle;
import android.system.ErrnoException;
import android.system.Os;
import android.view.MotionEvent;
import android.view.View;
import android.view.WindowInsets;
import android.view.WindowInsetsController;
import android.view.WindowManager;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.util.Properties;

public final class ProbeActivity extends Activity {
    private File stateFile;
    private MarkerView markerView;
    private long downCount;
    private int requestedRotation;
    private boolean immersive;
    private int suppliedMarkerX;
    private int suppliedMarkerY;
    private int markerX;
    private int markerY;
    private final Point screenSize = new Point();
    private final int[] viewOrigin = new int[2];

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        stateFile = new File(getFilesDir(), "state.txt");
        loadCount();
        readOptions(getIntent());
        markerView = new MarkerView();
        setContentView(markerView);
        applyWindowMode();
        writeState(false);
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        readOptions(intent);
        applyWindowMode();
        writeState(false);
        markerView.post(markerView::publishLayout);
    }

    private void readOptions(Intent intent) {
        requestedRotation = intent.getIntExtra("rotation", 0);
        if (requestedRotation != 0 && requestedRotation != 1) {
            throw new IllegalArgumentException("rotation must be 0 or 1");
        }
        immersive = intent.getBooleanExtra("immersive", true);
        suppliedMarkerX = intent.getIntExtra("marker_x", -1);
        suppliedMarkerY = intent.getIntExtra("marker_y", -1);
        if (suppliedMarkerX < -1 || suppliedMarkerY < -1) {
            throw new IllegalArgumentException("marker coordinates must be nonnegative or -1");
        }
        setRequestedOrientation(requestedRotation == 0
                ? ActivityInfo.SCREEN_ORIENTATION_PORTRAIT
                : ActivityInfo.SCREEN_ORIENTATION_LANDSCAPE);
    }

    @Override
    public void onWindowFocusChanged(boolean hasFocus) {
        super.onWindowFocusChanged(hasFocus);
        if (hasFocus && markerView != null) {
            applyWindowMode();
            markerView.post(markerView::publishLayout);
        }
    }

    @Override
    public boolean dispatchTouchEvent(MotionEvent event) {
        if (event.getActionMasked() == MotionEvent.ACTION_DOWN) {
            downCount++;
            writeState(isLayoutReady());
        }
        return true;
    }

    private void applyWindowMode() {
        View decor = getWindow().getDecorView();
        int flags = View.SYSTEM_UI_FLAG_LAYOUT_STABLE
                | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
                | View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            flags |= View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR;
        }
        if (immersive) {
            flags |= View.SYSTEM_UI_FLAG_FULLSCREEN
                    | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                    | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY;
        }
        getWindow().setStatusBarColor(Color.WHITE);
        getWindow().setNavigationBarColor(Color.WHITE);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            getWindow().setStatusBarContrastEnforced(false);
            getWindow().setNavigationBarContrastEnforced(false);
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            WindowManager.LayoutParams attributes = getWindow().getAttributes();
            attributes.layoutInDisplayCutoutMode = Build.VERSION.SDK_INT >= Build.VERSION_CODES.R
                    ? WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_ALWAYS
                    : WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES;
            getWindow().setAttributes(attributes);
        }
        decor.setSystemUiVisibility(flags);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            getWindow().setDecorFitsSystemWindows(false);
            WindowInsetsController controller = getWindow().getInsetsController();
            if (controller != null) {
                controller.setSystemBarsBehavior(
                        WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE);
                if (immersive) {
                    controller.hide(WindowInsets.Type.systemBars());
                } else {
                    controller.show(WindowInsets.Type.systemBars());
                }
            }
        }
    }

    private boolean isLayoutReady() {
        int orientation = requestedRotation == 0
                ? Configuration.ORIENTATION_PORTRAIT : Configuration.ORIENTATION_LANDSCAPE;
        return markerView.isLaidOut() && !markerView.isLayoutRequested()
                && markerView.getWidth() > 0 && markerView.getHeight() > 0
                && getResources().getConfiguration().orientation == orientation;
    }

    private void loadCount() {
        if (!stateFile.exists()) {
            return;
        }
        Properties state = new Properties();
        try (InputStreamReader reader = new InputStreamReader(
                new FileInputStream(stateFile), StandardCharsets.UTF_8)) {
            state.load(reader);
            downCount = Long.parseLong(state.getProperty("count"));
            if (downCount < 0) {
                throw new IllegalStateException("Negative persisted count");
            }
        } catch (IOException | IllegalArgumentException exception) {
            throw new IllegalStateException("Cannot read fixture state", exception);
        }
    }

    private void updateGeometry() {
        getWindowManager().getDefaultDisplay().getRealSize(screenSize);
        markerView.getLocationOnScreen(viewOrigin);
        markerX = suppliedMarkerX == -1 ? screenSize.x / 2 : suppliedMarkerX;
        markerY = suppliedMarkerY == -1 ? screenSize.y / 2 : suppliedMarkerY;
    }

    private void writeState(boolean ready) {
        updateGeometry();
        String state = "count=" + downCount + "\n"
                + "size=" + screenSize.x + "x" + screenSize.y + "\n"
                + "rotation=" + requestedRotation + "\n"
                + "immersive=" + (immersive ? 1 : 0) + "\n"
                + "ready=" + (ready ? 1 : 0) + "\n"
                + "marker_x=" + markerX + "\n"
                + "marker_y=" + markerY + "\n";
        File pending = new File(getFilesDir(), "state.txt.pending");
        try {
            try (FileOutputStream output = new FileOutputStream(pending)) {
                output.write(state.getBytes(StandardCharsets.UTF_8));
                output.getFD().sync();
            }
            // Same-directory POSIX rename atomically replaces the complete snapshot.
            Os.rename(pending.getAbsolutePath(), stateFile.getAbsolutePath());
        } catch (IOException | ErrnoException exception) {
            throw new IllegalStateException("Cannot persist fixture state", exception);
        }
    }

    private final class MarkerView extends View {
        private final Paint black = new Paint();

        MarkerView() {
            super(ProbeActivity.this);
            black.setColor(Color.BLACK);
            setBackgroundColor(Color.WHITE);
        }

        @Override
        protected void onSizeChanged(int width, int height, int oldWidth, int oldHeight) {
            super.onSizeChanged(width, height, oldWidth, oldHeight);
            post(this::publishLayout);
        }

        @Override
        protected void onLayout(boolean changed, int left, int top, int right, int bottom) {
            super.onLayout(changed, left, top, right, bottom);
            post(this::publishLayout);
        }

        void publishLayout() {
            if (!isAttachedToWindow()) {
                return;
            }
            writeState(isLayoutReady());
            invalidate();
        }

        @Override
        protected void onDraw(Canvas canvas) {
            super.onDraw(canvas);
            updateGeometry();
            int x = markerX - viewOrigin[0];
            int y = markerY - viewOrigin[1];
            canvas.drawColor(Color.WHITE);
            canvas.drawRect(x - 20, y - 20, x + 20, y + 20, black);
        }
    }
}
