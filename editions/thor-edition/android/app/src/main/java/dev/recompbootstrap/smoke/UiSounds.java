package dev.recompbootstrap.smoke;

import android.media.AudioAttributes;
import android.media.SoundPool;
import android.os.SystemClock;
import java.io.File;

/**
 * 2026-11-17: plays MenuSounds' files with a SoundPool (game usage, so the system's media volume and mute apply). Loaded once when the
 * launcher starts; a sound whose file is missing stays silent. A repeat of the same sound within MIN_GAP_MS is skipped (a held stick).
 */
final class UiSounds {
    static final long MIN_GAP_MS = 45;
    private final SoundPool pool;
    private final int[] ids = new int[MenuSounds.FILES.length];
    private final boolean[] ready = new boolean[MenuSounds.FILES.length];
    private final long[] last = new long[MenuSounds.FILES.length];

    UiSounds(File filesDir) {
        pool = new SoundPool.Builder().setMaxStreams(3).setAudioAttributes(new AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_GAME).setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build()).build();
        pool.setOnLoadCompleteListener((p, sample, status) -> {
            for (int i = 0; i < ids.length; ++i) if (ids[i] == sample) ready[i] = status == 0;
        });
        float gain = MenuSounds.recordedGain(filesDir);   // (1.3.0 reship: the library's recordings at the disc's level)
        for (int i = 0; i < ids.length; ++i) {
            File f = MenuSounds.levelled(filesDir, i, gain);
            ids[i] = f == null ? 0 : pool.load(f.getAbsolutePath(), 1);
        }
    }

    void play(int sound) {
        if (sound < 0 || sound >= ids.length || !ready[sound]) return;
        long now = SystemClock.uptimeMillis();
        if (now - last[sound] < MIN_GAP_MS) return;
        last[sound] = now;
        pool.play(ids[sound], 1f, 1f, 1, 0, 1f);
    }

    void release() {
        pool.release();
    }
}
