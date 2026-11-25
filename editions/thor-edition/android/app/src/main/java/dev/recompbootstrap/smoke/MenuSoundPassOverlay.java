package dev.recompbootstrap.smoke;

import android.app.Activity;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.util.DisplayMetrics;
import android.util.Log;
import android.view.KeyEvent;
import android.view.ViewGroup;
import android.widget.FrameLayout;
import java.io.File;

/**
 * 2026-11-17 (owner: "no silent first menu"; the library contract in MenuSoundsPrep): in the game window started for setup's menu-sound
 * pass (the switch is there), the step's page covers the game, so the player never sees the game window; every key and touch is taken
 * (the game gets no input). When the library's answer (MenuSoundsPrep.DONE_FILE) appears, at PASS_LIMIT_MS, or on B, the window ends the
 * game process: the library does not exit by itself. The launcher's step reads the answer and cleans up.
 */
final class MenuSoundPassOverlay {
    private final Activity activity;
    private final File filesDir;
    private final UiKit.SceneView view;
    private final MenuSoundsPrep prep = new MenuSoundsPrep();
    private final Handler handler = new Handler(Looper.getMainLooper());
    private boolean ending;

    private final Runnable poll = new Runnable() {
        @Override public void run() {
            if (ending) return;
            long now = SystemClock.elapsedRealtime();
            String done = MenuSoundsPrep.done(filesDir);
            prep.found = MenuSoundsPrep.found(filesDir);
            render();
            if (done != null || now - prep.startedAt >= MenuSoundsPrep.PASS_LIMIT_MS) end();
            else handler.postDelayed(this, MenuSoundsPrep.POLL_MS);
        }
    };

    private MenuSoundPassOverlay(Activity activity, FrameLayout content) {
        this.activity = activity;
        this.filesDir = activity.getFilesDir();
        view = new UiKit.SceneView(activity, unit(activity), new UiKit.Listener() {
            @Override public void onHit(int id) {
                if (id / 100 * 100 == MenuLayout.HIT_PROMPT && id % 100 == NavInput.BACK) end();
            }

            @Override public void onScroll(int lines) {
            }
        });
        view.setClickable(true);   // the game below gets no touch
        content.addView(view, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        prep.started(SystemClock.elapsedRealtime());
        render();
        handler.postDelayed(poll, MenuSoundsPrep.POLL_MS);
    }

    /** The overlay when this game window was started for the pass; null otherwise (a normal start: nothing changes). */
    static MenuSoundPassOverlay attach(Activity activity, FrameLayout content) {
        File filesDir = activity.getFilesDir();
        // a switch no step stands behind (MenuSoundsPrep.stale): removed, and this is a normal start (TU27 2026-11-24)
        if (MenuSoundsPrep.clearIfStale(filesDir, System.currentTimeMillis())) Log.i("RecompSetup", "MENU_SOUNDS_PASS stale switch removed");
        boolean pass = MenuSoundsPrep.passRequested(filesDir);
        Log.i("RecompSetup", "MENU_SOUNDS_PASS window switch=" + pass);
        return pass ? new MenuSoundPassOverlay(activity, content) : null;
    }

    /** Every key is the overlay's while it is up; B ends the pass. */
    boolean key(KeyEvent event) {
        if (event.getKeyCode() == KeyEvent.KEYCODE_BUTTON_B || event.getKeyCode() == KeyEvent.KEYCODE_BACK) {
            if (event.getAction() == KeyEvent.ACTION_UP) end();
        }
        return true;
    }

    private void end() {
        if (ending) return;
        ending = true;
        Log.i("RecompSetup", "MENU_SOUNDS_PASS end done=" + MenuSoundsPrep.done(filesDir) + " found=" + MenuSoundsPrep.found(filesDir));
        // the pass is over whoever ends it: the switch goes now (the launcher's step reads DONE_FILE, which stays for it)
        MenuSoundsPrep.switchFile(filesDir).delete();
        handler.removeCallbacks(poll);
        activity.finish();
        // The library does not exit by itself and its threads keep running after finish(): end this (the game's own) process.
        handler.postDelayed(() -> android.os.Process.killProcess(android.os.Process.myPid()), 400);
    }

    private void render() {
        SetupFrame.show(view, prep.page());
    }

    @SuppressWarnings("deprecation")
    private static float unit(Activity activity) {
        int width, height;
        if (Build.VERSION.SDK_INT >= 30) {
            android.graphics.Rect bounds = activity.getWindowManager().getCurrentWindowMetrics().getBounds();
            width = bounds.width();
            height = bounds.height();
        } else {
            DisplayMetrics metrics = new DisplayMetrics();
            activity.getWindowManager().getDefaultDisplay().getRealMetrics(metrics);
            width = metrics.widthPixels;
            height = metrics.heightPixels;
        }
        return ScreenStack.unit(width, height);
    }
}
