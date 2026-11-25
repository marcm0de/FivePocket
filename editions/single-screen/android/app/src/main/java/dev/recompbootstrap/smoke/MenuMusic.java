package dev.recompbootstrap.smoke;

import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;

/**
 * Next update (owner, 2026-11-24): the game's own launcher music on loop while the launcher is on screen. Pure Java: which file
 * plays, whether the player switched it off, the fade levels and what the player does on each launcher event (MenuMusicPlayer
 * plays it).
 *
 * The music is the player's own, never in the APK: the builder writes it from the player's discs into the bundle as
 * ui/music/launcher-music.wav (builder-gui 229ef5e: the score track "greyhound" from the player's disc, 16-bit PCM stereo 32 kHz,
 * about 99 s, about 12.7 MB), and the import puts it in files/game-install/bundle/ui/music. The track ends at full level and starts
 * from silence, so it is not played as a plain loop: its last LOOP_FADE_MS fade out and it starts again from the beginning. A missing or unusable file means
 * silence, never an error. The Settings row "Menu music" (on by default) writes settings/menu-music.txt = "off" when it is off.
 * When it is on, there is no file.
 */
final class MenuMusic {
    private MenuMusic() { }

    /** The app's own switch file (RuntimeMapping APP_FILES): "off" = no music; no file, or any other word, = music. */
    static final String FILE = "settings/menu-music.txt";
    static final String OFF = "off";
    static final String DIR = CodeBundle.UI_DIR + "/music";
    /** The bundle names the importer accepts (CodeBundle.uiAssetAllowed): the builder's one file. */
    static final String[] NAMES = {"launcher-music.wav"};
    /** The ui-asset cap (CodeBundle.UI_MAX_BYTES): a few minutes of compressed music fit well under it. */
    static final long MAX_BYTES = CodeBundle.UI_MAX_BYTES;
    static final long FADE_IN_MS = 1200, FADE_OUT_MS = 700, LOOP_FADE_MS = 1500;

    /** True unless the switch file says "off" (null = no file = on, the default). */
    static boolean enabled(byte[] content) {
        if (content == null) return true;
        return !new String(content, StandardCharsets.UTF_8).trim().equalsIgnoreCase(OFF);
    }

    /** The switch file's content for the Settings value (1 = on: no file, null; 0 = off). */
    static byte[] content(int value) {
        return value == 1 ? null : (OFF + "\n").getBytes(StandardCharsets.UTF_8);
    }

    /** True when the name (a bundle path without the ui/music/ part) is one of NAMES. */
    static boolean allowedName(String name) {
        for (String n : NAMES) if (n.equals(name)) return true;
        return false;
    }

    /** The music file under the app's files folder, when there is a usable one; else null. */
    static File pick(File filesDir) {
        if (filesDir == null) return null;
        File dir = new File(filesDir, DIR);
        for (String name : NAMES) {
            File f = new File(dir, name);
            if (usable(f, name)) return f;
        }
        return null;
    }

    /** A regular file of a sensible size whose first bytes match its extension. */
    static boolean usable(File f, String name) {
        if (!f.isFile() || f.length() < 64 || f.length() > MAX_BYTES) return false;
        byte[] head = new byte[12];
        try (FileInputStream in = new FileInputStream(f)) {
            if (in.read(head) != 12) return false;
        } catch (IOException e) {
            return false;
        }
        return matches(head, name);
    }

    static boolean matches(byte[] h, String name) {
        if (h == null || h.length < 12) return false;
        if (name.endsWith(".wav")) return h[0] == 'R' && h[1] == 'I' && h[2] == 'F' && h[3] == 'F' && h[8] == 'W' && h[9] == 'A' && h[10] == 'V' && h[11] == 'E';
        return false;
    }

    /** The level elapsedMs into a fade of durationMs from one level to another (0..1, linear; clamped). */
    static float level(long elapsedMs, long durationMs, float from, float to) {
        if (durationMs <= 0 || elapsedMs >= durationMs) return to;
        if (elapsedMs <= 0) return from;
        return from + (to - from) * (elapsedMs / (float) durationMs);
    }

    /**
     * The gain near the end of the track (0..1): 1 until the last LOOP_FADE_MS, then down to 0 at the end, where the track starts
     * again from its own silent start. An unknown length (0 or less) is 1.
     */
    static float loopGain(long positionMs, long durationMs) {
        if (durationMs <= 0) return 1f;
        long remaining = durationMs - positionMs;
        if (remaining >= LOOP_FADE_MS) return 1f;
        return remaining <= 0 ? 0f : remaining / (float) LOOP_FADE_MS;
    }

    /** The player volume for a level: squared, so the fade sounds even to the ear rather than dropping off at the end. */
    static float volume(float level) {
        float l = Math.max(0f, Math.min(1f, level));
        return l * l;
    }

    /** What the player does: nothing, start the track (fading in), fade back in a fading track, or fade out and stop. */
    static final int NONE = 0, START = 1, FADE_IN = 2, FADE_OUT = 3;

    /**
     * The decision for a launcher event. visible: the launcher is on screen (resumed, not paused for the game, Home or another app);
     * enabled: the Settings switch; hasFile: pick() found the track; player: the track's player exists; fadingOut: it is fading out.
     */
    static int decide(boolean visible, boolean enabled, boolean hasFile, boolean player, boolean fadingOut) {
        boolean want = visible && enabled && hasFile;
        if (!want) return player && !fadingOut ? FADE_OUT : NONE;
        if (!player) return START;
        return fadingOut ? FADE_IN : NONE;
    }
}
