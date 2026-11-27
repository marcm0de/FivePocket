package dev.recompbootstrap.smoke;

import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;

/**
 * 2026-11-17 (owner: "I want the in game menu sound effects as well"): which of the game's own frontend sounds
 * (HUD_FRONTEND_DEFAULT_SOUNDSET) a launcher menu command plays, and which sound files may be played. Pure Java.
 *
 * The sounds are the player's: never in the APK. They are files the player's own setup writes from the player's discs into
 * files/ui/sounds/ (SELECT and BACK by the builder from the disc's frontend sound bank, the synthesized NAV_UP_DOWN,
 * NAV_LEFT_RIGHT and ERROR by the game library's one-time recording). A missing or unusable file is silence, never an error.
 */
final class MenuSounds {
    private MenuSounds() { }

    static final int NONE = -1, NAV_UP_DOWN = 0, NAV_LEFT_RIGHT = 1, SELECT = 2, BACK = 3, ERROR = 4;
    /** The files, by sound: the game's sound names in lower case. */
    static final String[] FILES = {"nav_up_down.wav", "nav_left_right.wav", "select.wav", "back.wav", "error.wav"};
    /** The imported bundle's sounds (builder: SELECT, BACK) first, then files/ui/sounds (the library's recording: NAV, ERROR). */
    static final String[] DIRS = {CodeBundle.UI_DIR + "/sounds", "ui/sounds"};
    /** A menu sound is a fraction of a second; anything bigger is not one of ours. */
    static final long MAX_BYTES = 2L * 1024 * 1024;

    /**
     * The sound for a command the visible screen was given. handled: what Screen.onNav returned; popped: a Back the screen did not
     * handle closed it (the host's own back). The pause menu's rules: a move up or down, a move left or right (and a tab change), Select,
     * Back; Select that nothing took is the error sound. A command nothing reacted to is silent.
     */
    static int soundFor(int command, boolean handled, boolean popped) {
        switch (command) {
            case NavInput.UP: case NavInput.DOWN: return handled ? NAV_UP_DOWN : NONE;
            case NavInput.LEFT: case NavInput.RIGHT: case NavInput.PAGE_PREV: case NavInput.PAGE_NEXT: return handled ? NAV_LEFT_RIGHT : NONE;
            case NavInput.CONFIRM: return handled ? SELECT : ERROR;
            case NavInput.BACK: return handled || popped ? BACK : NONE;
            default: return NONE;
        }
    }

    /** The file for a sound under the app's files folder, when it is a usable sound file (a RIFF WAVE of a sensible size); else null. */
    static File usable(File filesDir, int sound) {
        if (filesDir == null || sound < 0 || sound >= FILES.length) return null;
        for (String dir : DIRS) {
            File f = wave(new File(new File(filesDir, dir), FILES[sound]));
            if (f != null) return f;
        }
        return null;
    }

    private static File wave(File f) {
        if (!f.isFile() || f.length() < 44 || f.length() > MAX_BYTES) return null;
        byte[] head = new byte[12];
        try (FileInputStream in = new FileInputStream(f)) {
            if (in.read(head) != 12) return null;
        } catch (IOException e) {
            return null;
        }
        boolean riff = head[0] == 'R' && head[1] == 'I' && head[2] == 'F' && head[3] == 'F';
        boolean wave = head[8] == 'W' && head[9] == 'A' && head[10] == 'V' && head[11] == 'E';
        if (!riff || !wave) return null;
        return f.length() == V1_BYTES && !hasOnset(f) ? null : f;
    }

    /**
     * 1.3.0 reship (owner 2026-11-26: no sound when cycling the menu): the game library's first recordings (v1922) were taken on the
     * "Loading Story Mode" screen and hold only flat noise. Such a file has the v1 size (44 + 36000 frames of 16-bit stereo) and no
     * sound's onset; it is not used (silence, and setup asks for a recording again; the library's v2 moves it aside and records anew).
     */
    /**
     * 1.3.0 reship (Thor 2026-11-26: the library's recordings come from the game's mix, ~23 dB under the disc's own files): the gain that
     * brings a recording to the disc's level, from the one sound both have - the builder's SELECT (from the disc's frontend bank) over the
     * library's recorded SELECT, by peak, 1..MAX_GAIN; 1 when either is missing. The same gain for every recording keeps the game's own
     * levels between the sounds (a NAV tick stays softer than SELECT, as in the pause menu).
     */
    static final float MAX_GAIN = 40f;
    static final String RECORDED_DIR = "ui/sounds", LEVELLED_DIR = "ui/sounds-level";

    static float recordedGain(File filesDir) {
        File disc = wave(new File(new File(filesDir, CodeBundle.UI_DIR + "/sounds"), FILES[SELECT]));
        File rec = wave(new File(new File(filesDir, RECORDED_DIR), FILES[SELECT]));
        if (disc == null || rec == null) return 1f;
        float d = peak(read(disc)), r = peak(read(rec));
        if (d <= 0f || r <= 0f) return 1f;
        return Math.max(1f, Math.min(MAX_GAIN, d / r));
    }

    /** The sound file to play: a library recording at the disc's level (written once beside it, ui/sounds-level), else usable(). */
    static File levelled(File filesDir, int sound, float gain) {
        File f = usable(filesDir, sound);
        if (f == null || gain <= 1.01f || !f.getParentFile().equals(new File(filesDir, RECORDED_DIR))) return f;
        File out = new File(new File(filesDir, LEVELLED_DIR), FILES[sound]);
        try {
            if (out.isFile() && out.lastModified() >= f.lastModified()) return out;
            byte[] src = read(f);
            if (sound == NAV_UP_DOWN || sound == NAV_LEFT_RIGHT) src = tickOnly(src);   // (the ticks: the click only, no room tone after it)
            byte[] amplified = amplify(src, gain);
            if (amplified == null) return f;
            out.getParentFile().mkdirs();
            File tmp = new File(out.getPath() + ".part");
            java.nio.file.Files.write(tmp.toPath(), amplified);
            if (!tmp.renameTo(out)) return f;
            return out;
        } catch (IOException | RuntimeException e) {
            return f;
        }
    }

    /**
     * 1.3.0 reship (Thor 2026-11-26 23:23: the recorded NAV_UP_DOWN kept ~100 ms of room tone after its ~20 ms click): a NAV tick cut to its
     * click - from the start to the last 5 ms window whose high-passed energy (first difference over two frames) is a quarter of the loudest
     * window's or more, plus one window, at least 20 ms - with a 5 ms fade. Other sounds and anything that is not 16-bit PCM WAVE: unchanged.
     */
    static final int TICK_WINDOW = 240, TICK_MIN_FRAMES = 960;

    static byte[] tickOnly(byte[] w) {
        int d = dataStart(w);
        if (d < 0) return w;
        int frames = (w.length - d) / 4, windows = frames / TICK_WINDOW;
        if (windows < 2) return w;
        double[] e = new double[windows];
        double loud = 0;
        for (int k = 0; k < windows; ++k) {
            double acc = 0;
            for (int f = Math.max(2, k * TICK_WINDOW); f < (k + 1) * TICK_WINDOW; ++f) {
                for (int c = 0; c < 2; ++c) {
                    int a = d + f * 4 + c * 2, b = d + (f - 2) * 4 + c * 2;
                    double x = (short) ((w[a] & 0xFF) | (w[a + 1] << 8)) - (short) ((w[b] & 0xFF) | (w[b + 1] << 8));
                    acc += x * x;
                }
            }
            e[k] = acc;
            loud = Math.max(loud, acc);
        }
        int last = 0;
        for (int k = 0; k < windows; ++k) if (e[k] >= loud / 16) last = k;   // (energy ratio 1/16 = amplitude ratio 1/4)
        int keep = Math.min(frames, Math.max(TICK_MIN_FRAMES, (last + 2) * TICK_WINDOW));
        if (keep >= frames) return w;
        byte[] o = new byte[d + keep * 4];
        System.arraycopy(w, 0, o, 0, o.length);
        for (int f = keep - TICK_WINDOW; f < keep; ++f) {   // 5 ms fade
            float g = (keep - f) / (float) TICK_WINDOW;
            for (int c = 0; c < 2; ++c) {
                int p = d + f * 4 + c * 2;
                int v = Math.round((short) ((o[p] & 0xFF) | (o[p + 1] << 8)) * g);
                o[p] = (byte) v; o[p + 1] = (byte) (v >> 8);
            }
        }
        int riff = o.length - 8, bytes = keep * 4;
        o[4] = (byte) riff; o[5] = (byte) (riff >> 8); o[6] = (byte) (riff >> 16); o[7] = (byte) (riff >> 24);
        o[d - 4] = (byte) bytes; o[d - 3] = (byte) (bytes >> 8); o[d - 2] = (byte) (bytes >> 16); o[d - 1] = (byte) (bytes >> 24);
        return o;
    }

    static byte[] read(File f) {
        try { return java.nio.file.Files.readAllBytes(f.toPath()); } catch (IOException e) { return new byte[0]; }
    }

    /** Offset of the 16-bit PCM data chunk of a RIFF WAVE, or -1. */
    static int dataStart(byte[] w) {
        if (w.length < 44 || w[0] != 'R' || w[8] != 'W') return -1;
        int p = 12;
        while (p + 8 <= w.length) {
            int size = (w[p + 4] & 0xFF) | (w[p + 5] & 0xFF) << 8 | (w[p + 6] & 0xFF) << 16 | (w[p + 7] & 0xFF) << 24;
            if (w[p] == 'd' && w[p + 1] == 'a' && w[p + 2] == 't' && w[p + 3] == 'a') return p + 8;
            if (size < 0) return -1;
            p += 8 + size + (size & 1);
        }
        return -1;
    }

    static float peak(byte[] w) {
        int d = dataStart(w);
        if (d < 0) return 0f;
        int m = 0;
        for (int i = d; i + 1 < w.length; i += 2) m = Math.max(m, Math.abs((short) ((w[i] & 0xFF) | (w[i + 1] << 8))));
        return m / 32767f;
    }

    /** The same WAVE with every sample times gain (clipped); null when it is not 16-bit PCM WAVE. */
    static byte[] amplify(byte[] w, float gain) {
        int d = dataStart(w);
        if (d < 0) return null;
        byte[] o = w.clone();
        for (int i = d; i + 1 < o.length; i += 2) {
            int v = Math.round((short) ((o[i] & 0xFF) | (o[i + 1] << 8)) * gain);
            v = Math.max(-32768, Math.min(32767, v));
            o[i] = (byte) v; o[i + 1] = (byte) (v >> 8);
        }
        return o;
    }

    static final long V1_BYTES = 144044L;
    static final int WINDOW = 960;
    static final float ONSET_RATIO = 4f;

    /** The library's rule (menu_sound_capture.h soundIn): a 20 ms window ONSET_RATIO times louder than the quiet quarter of the windows. */
    static boolean hasOnset(File f) {
        byte[] b;
        try (FileInputStream in = new FileInputStream(f)) {
            b = new byte[(int) f.length()];
            int got = 0;
            while (got < b.length) { int n = in.read(b, got, b.length - got); if (n < 0) break; got += n; }
            if (got != b.length) return false;
        } catch (IOException e) {
            return false;
        }
        return hasOnset(b, 44);
    }

    static boolean hasOnset(byte[] wav, int dataStart) {
        int samples = (wav.length - dataStart) / 2, frames = samples / 2, windows = (frames + WINDOW - 1) / WINDOW;
        if (windows < 4) return false;
        float[] rms = new float[windows];
        for (int w = 0; w < windows; ++w) {
            int a = w * WINDOW, e = Math.min(frames, a + WINDOW);
            double sum = 0;
            for (int i = a * 2; i < e * 2; ++i) {
                int p = dataStart + 2 * i;
                double v = (short) ((wav[p] & 0xFF) | (wav[p + 1] << 8)) / 32767.0;
                sum += v * v;
            }
            rms[w] = (float) Math.sqrt(sum / ((e - a) * 2));
        }
        float[] sorted = rms.clone();
        java.util.Arrays.sort(sorted);
        float quiet = Math.max(1e-6f, sorted[windows / 4]), loud = 0;
        for (int w = 0; w < windows; ++w) loud = Math.max(loud, rms[w]);
        return loud >= ONSET_RATIO * quiet;
    }
}
