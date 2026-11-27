package dev.recompbootstrap.smoke;

import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;

/**
 * 2026-11-17 (owner: "no silent first menu"): setup's last step, "Preparing menu sounds". Contract with the game library (v1922,
 * multithread-perf): the app creates files/private-trace/SWITCH and starts the game as a normal Story start; the game window covers itself
 * with this step's page (MenuSoundPassOverlay); the library records the five frontend sounds that are missing into files/ui/sounds
 * (MenuSounds.FILES) and writes DONE_FILE there by rename, one line "written=N missing=M reason=TEXT". The game window then ends the game
 * process, and the app removes SWITCH and DONE_FILE. A failure, the time limit or B still complete setup; the library records again by
 * itself at the first real Story starts. Pure Java: the state, its page and the files it reads.
 */
final class MenuSoundsPrep {
    static final String SWITCH = "diag-menu-sound-pass", DONE_FILE = ".record-pass-done";
    /** The app's limit for the whole pass (a normal boot to the first script frames); the game window's own is PASS_LIMIT_MS. */
    static final long TIMEOUT_MS = 255_000, PASS_LIMIT_MS = 240_000, POLL_MS = 500;

    static final int STARTING = 0, PREPARING = 1, READY = 2, FAILED = 3, TIMED_OUT = 4, SKIPPED = 5;
    static final String TITLE = "PREPARING MENU SOUNDS";

    int state = STARTING;
    long startedAt = -1;
    int found;
    String reason = "";

    /** Setup needs the step: the library can record, the game will reach its scripts (profileReady), and a sound file is missing. */
    static boolean needed(File filesDir, boolean libraryRecords) {
        return libraryRecords && profileReady(filesDir) && found(filesDir) < MenuSounds.FILES.length;
    }

    /**
     * 1.3.0 reship (lead 2026-11-26): whether the pass can record at all. The library asks the game for the sounds from its script thread, and
     * on a profile without the game's own settings (files/profile/title_*.settings, written when the player confirms the game's first-start
     * Display Calibration) the game waits on that screen and its scripts never start: the pass then sat at "2 of 5" for its whole
     * TIMEOUT_MS (Fold 11-24 twice; Thor .tu27q 2026-11-26 12:56, device-runs/menusound-pass-thor-20261126.log: start screens drawn
     * for 100 s, no script tick, Display Calibration on screen). Without the file the step is left out and the library records the
     * five sounds by itself at the first real start, after the player confirms the screen (Thor 13:00: all five 42 s after A,
     * device-runs/menusound-firststart-thor-20261126.log).
     */
    static boolean profileReady(File filesDir) {
        File[] files = new File(filesDir, "profile").listFiles();
        if (files == null) return false;
        for (File f : files) {
            String n = f.getName();
            if (f.isFile() && f.length() > 0 && n.startsWith("title_") && n.endsWith(".settings")) return true;
        }
        return false;
    }

    static File library(File filesDir) {
        return new File(new File(filesDir, InstallState.PRIVATE_TRACE), InstallState.LIVE_LIBRARY);
    }

    /** Whether the installed game library knows the pass: its file holds the switch's name (read off the UI thread; it is large). */
    static boolean libraryRecords(File filesDir) {
        return contains(library(filesDir), SWITCH.getBytes(StandardCharsets.US_ASCII));
    }

    static boolean contains(File file, byte[] needle) {
        if (file == null || !file.isFile() || needle.length == 0) return false;
        byte[] buf = new byte[1 << 20];
        int keep = needle.length - 1, have = 0;
        try (FileInputStream in = new FileInputStream(file)) {
            for (int n; (n = in.read(buf, have, buf.length - have)) > 0; ) {
                int end = have + n;
                for (int i = 0; i + needle.length <= end; ++i) {
                    if (buf[i] != needle[0]) continue;
                    int j = 1;
                    while (j < needle.length && buf[i + j] == needle[j]) ++j;
                    if (j == needle.length) return true;
                }
                int tail = Math.min(keep, end);
                System.arraycopy(buf, end - tail, buf, 0, tail);
                have = tail;
            }
        } catch (IOException e) {
            return false;
        }
        return false;
    }

    static File switchFile(File filesDir) {
        return new File(new File(filesDir, InstallState.PRIVATE_TRACE), SWITCH);
    }

    static File dir(File filesDir) {
        return new File(filesDir, "ui/sounds");
    }

    static File doneFile(File filesDir) {
        return new File(dir(filesDir), DONE_FILE);
    }

    /**
     * A switch no running step stands behind (TU27 2026-11-24 on the Thor: a launcher that died, or a start that failed, during the step
     * left it, and every later start of any mode became a pass, until the app's data was cleared): all five sounds are in place already
     * (nothing to record), or it is older than the step's whole limit TIMEOUT_MS. nowMillis: the wall clock (the file's time base).
     */
    static boolean stale(File filesDir, long nowMillis) {
        File f = switchFile(filesDir);
        if (!f.isFile()) return false;
        return found(filesDir) == MenuSounds.FILES.length || nowMillis - f.lastModified() > TIMEOUT_MS;
    }

    /** Removes a stale switch (see stale); true when one was removed. Never touches a switch a step may still be using. */
    static boolean clearIfStale(File filesDir, long nowMillis) {
        return stale(filesDir, nowMillis) && switchFile(filesDir).delete();
    }

    /** The game was started for the pass (the switch is there). */
    static boolean passRequested(File filesDir) {
        return switchFile(filesDir).isFile();
    }

    /** How many of the five sound files are usable. */
    static int found(File filesDir) {
        int n = 0;
        for (int i = 0; i < MenuSounds.FILES.length; ++i) if (MenuSounds.usable(filesDir, i) != null) ++n;
        return n;
    }

    /** DONE_FILE's line, or null when there is none (yet). */
    static String done(File filesDir) {
        File f = doneFile(filesDir);
        if (!f.isFile() || f.length() > 4096) return null;
        try {
            String s = new String(Files.readAllBytes(f.toPath()), StandardCharsets.UTF_8).trim();
            int nl = s.indexOf('\n');
            return nl < 0 ? s : s.substring(0, nl).trim();
        } catch (IOException e) {
            return null;
        }
    }

    /** A field of the done line ("written=N missing=M reason=TEXT"; reason runs to the end); null when absent. */
    static String field(String line, String key) {
        int at = (" " + line).indexOf(" " + key + "=");
        if (at < 0) return null;
        int from = at + key.length() + 1;
        if (key.equals("reason")) return line.substring(from).trim();
        int sp = line.indexOf(' ', from);
        return sp < 0 ? line.substring(from) : line.substring(from, sp);
    }

    /** The game was started at now (DONE_FILE was removed and SWITCH created before). */
    void started(long now) {
        state = PREPARING;
        startedAt = now;
    }

    /** The game could not be started: setup completes without the step. */
    void notStarted(String why) {
        state = FAILED;
        reason = why == null ? "" : why;
    }

    /**
     * One poll: the sound files found, the done line (null: none yet), whether the game process is still running. Returns true when the
     * step has ended.
     */
    boolean update(long now, int foundNow, String doneLine, boolean gameRunning) {
        if (ended()) return true;
        found = foundNow;
        if (doneLine != null) {
            String missing = field(doneLine, "missing");
            if ("0".equals(missing) || (missing == null && foundNow == MenuSounds.FILES.length)) state = READY;
            else {
                state = FAILED;
                String why = field(doneLine, "reason");
                reason = (missing == null ? "" : missing + " missing") + (why == null || why.isEmpty() ? "" : (missing == null ? "" : ": ") + why);
            }
        } else if (startedAt >= 0 && now - startedAt >= TIMEOUT_MS) {
            state = TIMED_OUT;
        } else if (startedAt >= 0 && now - startedAt >= 5_000 && !gameRunning) {
            state = foundNow == MenuSounds.FILES.length ? READY : FAILED;
            if (state == FAILED) reason = "the game closed before it answered";
        }
        return ended();
    }

    void skip() {
        if (!ended()) state = SKIPPED;
    }

    boolean ended() {
        return state >= READY;
    }

    /** The step's page in the setup pages' frame: the status line with its bar, one help text, B to skip while it runs. */
    MenuScenes.Page page() {
        MenuScenes.Page p = new MenuScenes.Page();
        p.title = ShellPages.TITLE;
        p.tabs = new String[] {TITLE};   // the header bar, as SetupDiscPage's
        p.backdrop = true;
        String status;
        switch (state) {
            case READY: status = "Menu sounds ready."; break;
            case FAILED: status = "The menu sounds could not be prepared now" + (reason.isEmpty() ? "." : " (" + reason + ").") + " They are made at the first game start."; break;
            case TIMED_OUT: status = "Preparing the menu sounds is taking too long. They are made at the first game start."; break;
            case SKIPPED: status = "Skipped. The menu sounds are made at the first game start."; break;
            case STARTING: status = "Checking the game code..."; break;
            default: status = "Recording the game's own menu sounds: " + found + " of " + MenuSounds.FILES.length + "."; break;
        }
        p.info.add(new MenuScenes.Line(status, state == FAILED || state == TIMED_OUT ? MenuScenes.TONE_WARNING : MenuScenes.TONE_NORMAL));
        p.infoBar = ended() ? (state == READY ? 1f : -1f) : (float) found / MenuSounds.FILES.length;
        MenuScenes.Row row = new MenuScenes.Row();
        row.label = "Menu Sounds";
        row.value = state == READY ? "Done" : ended() ? "Later" : found + " / " + MenuSounds.FILES.length;
        p.rows.add(row);
        p.rowFocused = 0;
        p.help.add(new MenuScenes.Line("The launcher plays the game's own menu sounds, recorded once by the game itself while it starts behind"
                + " this page, silently. This takes about as long as the game's loading." + (ended() ? " A: open the main menu." : " B: skip."), MenuScenes.TONE_NORMAL));
        if (ended()) p.prompts.add(new MenuScenes.Prompt("Select", "A", NavInput.CONFIRM));
        else p.prompts.add(new MenuScenes.Prompt("Skip", "B", NavInput.BACK));
        return p;
    }
}
