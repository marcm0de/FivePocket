package dev.recompbootstrap.smoke;

import android.content.Context;
import android.media.AudioAttributes;
import android.media.MediaPlayer;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;

/**
 * Next update (2026-11-24): plays MenuMusic's track on loop while the launcher is on screen. It uses a MediaPlayer with game usage,
 * so the system's media volume and mute apply. It fades in on start, fades out and stops when the launcher leaves the screen (the
 * game starting, Home, another app), and checks the Settings switch again each time a launcher screen is shown or Settings are saved. A track that fails
 * to play stays silent until the app starts again. Main thread only.
 */
final class MenuMusicPlayer {
    private static final long TICK_MS = 40;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final File filesDir;
    private MediaPlayer player;
    private boolean prepared, failed, visible;
    private float level, from, to;
    private long fadeStart, fadeMs, durationMs;
    private final Runnable tick = this::step;

    MenuMusicPlayer(Context context) {
        filesDir = context.getFilesDir();
    }

    /** The launcher is on screen (onResume). */
    void resumed() {
        visible = true;
        refresh();
    }

    /** The launcher left the screen (onPause: the game starts, Home, another app): fade out and stop. */
    void paused() {
        visible = false;
        refresh();
    }

    /** A launcher screen was shown, or Settings were saved: the Menu music switch may have changed. */
    void refresh() {
        boolean fadingOut = player != null && to == 0f;
        File track = failed ? null : MenuMusic.pick(filesDir);
        switch (MenuMusic.decide(visible, enabled(), track != null, player != null, fadingOut)) {
            case MenuMusic.START: start(track); break;
            case MenuMusic.FADE_IN: fade(1f, MenuMusic.FADE_IN_MS); break;
            case MenuMusic.FADE_OUT: fade(0f, MenuMusic.FADE_OUT_MS); break;
            default: break;
        }
    }

    /** onDestroy: stop at once. */
    void release() {
        visible = false;
        stop();
    }

    private boolean enabled() {
        File f = new File(filesDir, MenuMusic.FILE);
        if (!f.isFile()) return true;
        byte[] b = new byte[(int) Math.min(64, f.length())];
        try (FileInputStream in = new FileInputStream(f)) {
            int n = in.read(b);
            return MenuMusic.enabled(n <= 0 ? new byte[0] : java.util.Arrays.copyOf(b, n));
        } catch (IOException e) {
            return true;
        }
    }

    private void start(File track) {
        MediaPlayer p = new MediaPlayer();
        try {
            p.setAudioAttributes(new AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_GAME)
                    .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC).build());
            p.setDataSource(track.getAbsolutePath());
            p.setLooping(false);   // (MenuMusic: the end fades out, then the track starts again; a plain loop has a seam)
            p.setVolume(0f, 0f);
        } catch (IOException | RuntimeException e) {
            p.release();
            failed = true;
            return;
        }
        player = p;
        prepared = false;
        level = 0f;
        to = 1f;
        p.setOnPreparedListener(mp -> {
            if (mp != player) return;
            prepared = true;
            try { durationMs = mp.getDuration(); } catch (RuntimeException e) { durationMs = 0; }
            mp.start();
            fade(to, MenuMusic.FADE_IN_MS);   // (a pause during preparing set to = 0: the fade then just stops it)
        });
        p.setOnCompletionListener(mp -> {
            if (mp != player) return;
            try { mp.seekTo(0); mp.start(); } catch (RuntimeException e) { failed = true; stop(); }
        });
        p.setOnErrorListener((mp, what, extra) -> {
            if (mp == player) { failed = true; stop(); }
            return true;
        });
        try {
            p.prepareAsync();
        } catch (RuntimeException e) {
            failed = true;
            stop();
        }
    }

    private void fade(float target, long ms) {
        from = level;
        to = target;
        fadeStart = SystemClock.uptimeMillis();
        fadeMs = ms;
        handler.removeCallbacks(tick);
        if (prepared) step();
    }

    private void step() {
        if (player == null) return;
        level = MenuMusic.level(SystemClock.uptimeMillis() - fadeStart, fadeMs, from, to);
        if (level == 0f && to == 0f) { stop(); return; }
        try {
            float v = MenuMusic.volume(level * MenuMusic.loopGain(player.getCurrentPosition(), durationMs));
            player.setVolume(v, v);
        } catch (RuntimeException e) {
            stop();
            return;
        }
        handler.postDelayed(tick, TICK_MS);   // (while it plays: the fades and the loop fade at the end)
    }

    private void stop() {
        handler.removeCallbacks(tick);
        MediaPlayer p = player;
        player = null;
        prepared = false;
        level = 0f;
        to = 0f;
        if (p != null) {
            try { p.stop(); } catch (RuntimeException ignored) { }
            p.release();
        }
    }
}
