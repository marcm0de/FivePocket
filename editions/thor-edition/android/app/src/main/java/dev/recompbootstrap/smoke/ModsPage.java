package dev.recompbootstrap.smoke;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Locale;

/**
 * The Mods page (Update 1 P5): what it shows and what each button asks for. Pure Java, host tested (ModsPageTest). It decides; ModsScreen
 * only draws page(), forwards commands and carries out takeAction() off the UI thread, then hands the result back (loaded / done / failed).
 *
 * Rows, top to bottom: Mods (the master switch, the same setting as Settings > World > Script mods), "Start Once Without Mods" (the
 * safe start), "Add A Mod Folder", "Add A Mod Zip", then one row per pack in the library's apply order (a switch, with its state from
 * the last start when known). The fixed rows come first so that the recovery button is on screen however many packs and notices there
 * are; the pack list below scrolls (scroll(): a drag moves the highlight). Y on a pack the app imported asks whether to remove it.
 * Nothing changes while the game runs (it read mods.txt when it started), and nothing is offered by a game build that does not read
 * mods.txt; per-mod switches and the safe start need a build with the v2 status (perMod), which reads disabled= and safe=1.
 *
 * Timing (ModsScreen runs the I/O on one thread and hands results back here):
 *  - takeAction() of a change (Mods switch, a pack, the safe start, Remove) locks the page at once (an operation): no second change can
 *    be asked from words that are about to be stale. The screen calls finished() when the work is done and then loads again.
 *  - begin() locks it for an import the same way.
 *  - loadAsked() hands out a ticket; loaded() takes only the newest ticket's answer and none while an operation runs, so a load that
 *    started before or during an operation can neither show old words nor unlock the page early.
 *  - held buttons repeat: a repeated LEFT / RIGHT / A / Y does nothing (one press, one change).
 */
final class ModsPage {
    static final String TITLE = "MODS";
    static final String TEXT_INTRO = "Mods replace the game's own files from your discs. If the game stops, switch them off here.";
    static final String TEXT_CANNOT = "Mods made as Xbox 360 code plugins or PC scripts do not work.";
    static final String TEXT_NEXT_START = "Changes apply the next time the game starts.";
    static final String TEXT_GAME_RUNNING = "The game is running. Close it to change mods.";
    static final String TEXT_TOO_MANY_OFF = "Too many mods are switched off to remember. Remove some you no longer use.";
    static final String TEXT_LOCAL_ONLY = "Only files stored on this device can be added. Files in a cloud service cannot.";
    static final String TEXT_SANDBOX = "In Sandbox, mods of missions that Sandbox holds back have no effect.";
    static final String TEXT_NONE = "No mods yet. Add a folder or a zip file from this device.";
    static final String TEXT_SAFE_PENDING = "Mods are off for the next start only.";
    static final String TEXT_SAVING = "Saving the change.";
    /**
     * The library's v2 export in its dynamic symbol table, the probe name CapabilityScanner looks for: a build that has it reads the
     * per-mod words (disabled=, safe=1); an older build ignores them, so the page does not offer those switches there.
     */
    static final String LIBRARY_V2_MARKER = "recomp_mods_status_v2";
    /** Rows above the pack list: Mods, Start Once Without Mods, Add A Mod Folder, Add A Mod Zip. */
    static final int FIXED_ROWS = 4;
    static final String TEXT_LOOSE = "Files placed straight in the mods folder. They follow Script Mods.";
    static final String TEXT_EMPTY = "Nothing in this folder can load. A mod's files go in a folder called game inside it.";
    static final String LABEL_MASTER = "Script Mods", LABEL_SAFE = "Start Once Without Mods", LABEL_ADD_FOLDER = "Add A Mod Folder",
            LABEL_ADD_ZIP = "Add A Mod Zip", LABEL_LOOSE = "Loose Files";
    static final String PROMPT_REMOVE = "Remove";
    static final String[] REMOVE_ANSWERS = {"Remove", "Keep"};

    enum Kind { MASTER, PACK, SAFE, ADD_FOLDER, ADD_ZIP }

    /**
     * What the screen must do now. WRITE_WORDS is one change: name = the pack (null: the safe word) switched to on; the screen applies it
     * to mods.txt as it is on disk (ModsLibrary.change). words = what the page expects the file to say afterwards.
     */
    static final class Action {
        enum Type { NONE, SET_MASTER, WRITE_WORDS, ADD_FOLDER, ADD_ZIP, REMOVE }
        final Type type;
        final boolean on;
        final ModsWords words;
        final String name;

        /** A change that locks the page until finished() and the next load. */
        boolean changes() {
            return type == Type.SET_MASTER || type == Type.WRITE_WORDS || type == Type.REMOVE;
        }

        private Action(Type type, boolean on, ModsWords words, String name) {
            this.type = type;
            this.on = on;
            this.words = words;
            this.name = name;
        }

        static final Action NONE = new Action(Type.NONE, false, null, null);
    }

    // ------------------------------------------------------------------------------------------ state

    private boolean loaded, available, perMod, gameRunning, sandbox, busy;
    /** Operations under way (a change or an import); the page stays locked while any runs and until the load after it. */
    private int ops;
    /** The newest load the screen asked for; an older load's answer is dropped. */
    private int ticket;
    /** The sentence the next accepted load shows (the result of the last operation); null: keep the page's line. */
    private String nextSentence;
    private boolean nextFailed;
    private List<ModsLibrary.Installed> packs = Collections.emptyList();
    private ModsWords words = ModsWords.NONE;
    private ModsStatus status;
    private int warnMods;
    private String message;
    private int messageTone = MenuScenes.TONE_NORMAL;
    private int focus;
    /** The remove question is open for this pack (null: no question); answer 0 = Remove, 1 = Keep. */
    private String asking;
    private int answer = 1;
    private Action pending = Action.NONE;

    /** The screen starts a read of the disk; sentence (null: none) is shown when an answer is accepted. Returns the ticket. */
    int loadAsked(String sentence, boolean failed) {
        if (sentence != null) {
            nextSentence = sentence;
            nextFailed = failed;
        }
        return ++ticket;
    }

    /** Tests and simple callers: a load asked and answered at once (the newest ticket). */
    void loaded(boolean available, List<ModsLibrary.Installed> packs, ModsWords words, ModsStatus status, boolean gameRunning,
                int warnMods, boolean sandbox) {
        loaded(loadAsked(null, false), available, available, packs, words, status, gameRunning, warnMods, sandbox);
    }

    /**
     * What is on disk and what the game said, for the load with this ticket. available: the installed game build reads mods.txt
     * (Capabilities.reads). perMod: it also reads disabled= and safe=1 (LIBRARY_V2_MARKER). status: the library's v2 status of the last
     * start, or null. warnMods: ModsLibrary.AfterStart.warnMods of the last judged start. False (nothing changes): an older ticket, or
     * an operation still runs (its own load follows).
     */
    boolean loaded(int ticket, boolean available, boolean perMod, List<ModsLibrary.Installed> packs, ModsWords words, ModsStatus status,
                   boolean gameRunning, int warnMods, boolean sandbox) {
        if (ticket != this.ticket || ops > 0) return false;
        this.loaded = true;
        this.available = available;
        this.perMod = available && perMod;
        this.packs = new ArrayList<ModsLibrary.Installed>(packs);
        this.words = words;
        this.status = status;
        this.gameRunning = gameRunning;
        this.warnMods = warnMods;
        this.sandbox = sandbox;
        this.busy = false;
        if (nextSentence != null) {
            done(nextSentence, nextFailed);
            nextSentence = null;
        } else if (TEXT_SAVING.equals(message)) {
            message = null;
        }
        // (the highlight stays where it is, also on a grey row a drag put it on; only a list that got shorter moves it)
        if (focus >= rowCount()) focus = firstEnabled();
        return true;
    }

    /** An operation starts (an import; takeAction() starts the others): the page is locked, what shows the sentence. */
    void begin(String what) {
        ops++;
        busy = true;
        message = what;
        messageTone = MenuScenes.TONE_NORMAL;
    }

    /** The operation's work is done (whatever its outcome); the screen loads again next, and that load unlocks the page. */
    void finished() {
        if (ops > 0) ops--;
    }

    boolean busy() {
        return busy || ops > 0;
    }

    /** A sentence on the page now (null: none), and whether it is a failure. The lock is not touched. */
    void done(String sentence, boolean failed) {
        message = sentence;
        messageTone = failed ? MenuScenes.TONE_ERROR : MenuScenes.TONE_NORMAL;
    }

    /** The action asked for, once. A change locks the page (begin) before it is handed out. */
    Action takeAction() {
        Action a = pending;
        pending = Action.NONE;
        if (a.changes()) {
            begin(a.type == Action.Type.SET_MASTER ? (a.on ? "Switching mods on." : "Switching mods off.")
                    : a.type == Action.Type.REMOVE ? "Removing " + a.name + "." : TEXT_SAVING);
        }
        return a;
    }

    boolean asking() {
        return asking != null;
    }

    // ------------------------------------------------------------------------------------------ rows

    int rowCount() {
        return FIXED_ROWS + packs.size();
    }

    Kind kind(int row) {
        switch (row) {
            case 0: return Kind.MASTER;
            case 1: return Kind.SAFE;
            case 2: return Kind.ADD_FOLDER;
            case 3: return Kind.ADD_ZIP;
            default: return Kind.PACK;
        }
    }

    /** The row of pack i (0 = the first in the library's order). */
    static int packRow(int i) {
        return FIXED_ROWS + i;
    }

    private ModsLibrary.Installed pack(int row) {
        return row >= FIXED_ROWS && row < rowCount() ? packs.get(row - FIXED_ROWS) : null;
    }

    private boolean changeable() {
        return loaded && available && !gameRunning && !busy();
    }

    /** A row A can act on now. */
    boolean enabled(int row) {
        if (row < 0 || row >= rowCount() || !changeable()) return false;
        switch (kind(row)) {
            case MASTER: return true;
            case PACK: return perMod && words.scripts && !pack(row).loose;
            case SAFE: return perMod && words.scripts;
            default: return true;
        }
    }

    /** A grey row UP / DOWN still stop on: a pack's own row (not the loose files'), once the page is loaded. */
    private boolean padStop(int row) {
        ModsLibrary.Installed p = loaded ? pack(row) : null;
        return p != null && !p.loose;
    }

    private int firstEnabled() {
        for (int i = 0; i < rowCount(); ++i) if (enabled(i)) return i;
        return 0;
    }

    int focused() {
        return focus;
    }

    /** The packs that will load at the next start: on, with something to load, while Mods is on and not a safe start. */
    int modsOn() {
        if (!words.scripts || words.safe) return 0;
        int n = 0;
        for (ModsLibrary.Installed p : packs) if (p.files > 0 && words.packOn(p.name)) n++;
        return n;
    }

    static String summary(int n) {
        return n == 0 ? "No mods on" : n == 1 ? "1 mod on" : n + " mods on";
    }

    static String warning(int n) {
        return "The game stopped last time with " + (n == 1 ? "1 mod" : n + " mods") + " on. " + LABEL_SAFE
                + (n == 1 ? " switches it off for one start." : " switches them off for one start.");
    }

    /** The launcher's Play page, after the outcome sentence of a start the guard blames on mods. */
    static String launcherNote(int n) {
        return n == 1 ? "1 mod was on. To start once without it, open Settings > World > Script Mods."
                : n + " mods were on. To start once without them, open Settings > World > Script Mods.";
    }

    /** The outcomes the crash guard blames on mods (ModsLibrary.afterStart). Low memory and a refused start are not. */
    static boolean blames(LaunchOutcome.Kind kind) {
        return kind == LaunchOutcome.Kind.CRASHED || kind == LaunchOutcome.Kind.STOPPED || kind == LaunchOutcome.Kind.NOT_RESPONDING;
    }

    /** "Loaded", "Failed", ... for a pack row, with its tone in tone[0]. */
    String value(int row, int[] tone) {
        tone[0] = MenuScenes.TONE_NORMAL;
        switch (kind(row)) {
            case MASTER: return words.scripts ? "On" : "Off";
            case SAFE: return words.scripts && words.safe ? "Yes" : "No";
            case ADD_FOLDER:
            case ADD_ZIP: return "";
            default: break;
        }
        ModsLibrary.Installed p = pack(row);
        if (!words.scripts || !words.packOn(p.name)) return "Off";
        if (p.files == 0) {
            tone[0] = MenuScenes.TONE_WARNING;
            return "Empty";
        }
        ModsStatus.Pack s = status == null || !status.v2 ? null : status.pack(p.name);
        if (status != null && status.v2 && status.safe) return "Off Last Start";
        if (s == null || !s.on) return "On";
        if (s.applied > 0 && s.skipped == 0) {
            tone[0] = MenuScenes.TONE_GOOD;
            return "Loaded";
        }
        if (s.applied > 0) {
            tone[0] = MenuScenes.TONE_WARNING;
            return "Partly Loaded";
        }
        if (s.skipped > 0) {
            tone[0] = MenuScenes.TONE_ERROR;
            return "Failed";
        }
        return "Waiting";
    }

    /** The help strip for a row: what it is, and for a pack what the last start made of it. */
    String help(int row) {
        if (!available) return SettingsPages.TEXT_NOT_IN_VERSION;
        if (gameRunning) return TEXT_GAME_RUNNING;
        switch (kind(row)) {
            case MASTER: return TEXT_INTRO + " " + TEXT_NEXT_START;
            case SAFE: return perMod ? "One start with every mod off, then they are back as they were." : SettingsPages.TEXT_NOT_IN_VERSION;
            case ADD_FOLDER: return "Copies a mod folder from this device into the app. " + TEXT_LOCAL_ONLY;
            case ADD_ZIP: return "Copies a mod from a zip file on this device into the app. " + TEXT_CANNOT;
            default: break;
        }
        ModsLibrary.Installed p = pack(row);
        StringBuilder h = new StringBuilder();
        if (p.loose) h.append(TEXT_LOOSE).append(' ');
        else if (!perMod) h.append("Switching one mod off: ").append(SettingsPages.TEXT_NOT_IN_VERSION).append(' ');
        if (p.files == 0) return h.append(TEXT_EMPTY).toString();
        h.append(p.files == 1 ? "1 file" : p.files + " files").append(", ").append(size(p.bytes)).append('.');
        if (p.refused > 0) h.append(' ').append(p.refused == 1 ? "1 file" : p.refused + " files").append(" cannot load (outside a game folder).");
        ModsStatus.Pack s = status == null || !status.v2 ? null : status.pack(p.name);
        if (s != null && s.on) {
            if (s.skipped > 0 && !s.reason.isEmpty()) h.append(" Last start: ").append(s.reason).append('.');
            else if (s.applied == 0 && s.skipped == 0) h.append(" The game loads it when it first opens those files.");
            if (s.conflicts > 0) {
                String winner = winner(p.name);
                h.append(' ').append(s.conflicts == 1 ? "1 file is" : s.conflicts + " files are").append(" also in ")
                        .append(winner == null ? "another mod" : winner.equals(ModsStatus.LOOSE) ? "the loose files" : winner)
                        .append(": that copy is used.");
            }
        }
        if (p.imported && changeable()) h.append(" Y removes it.");
        return h.toString();
    }

    private String winner(String loser) {
        if (status == null) return null;
        for (ModsStatus.Outcome o : status.outcomes) if (o.kind.equals("conflict") && o.pack.equals(loser)) return o.detail;
        return null;
    }

    static String size(long bytes) {
        if (bytes < 1024 * 1024) return Math.max(1, (bytes + 1023) / 1024) + " KB";
        return String.format(Locale.ROOT, "%.1f MB", bytes / (1024.0 * 1024.0));
    }

    // ------------------------------------------------------------------------------------------ commands

    /** A NavInput command. False: not handled (BACK with no question open: leave the page). */
    boolean nav(int command) {
        return nav(command, false);
    }

    /** repeat: the button is held. A held LEFT / RIGHT / A / Y does nothing more (one press, one change); a held UP / DOWN moves on. */
    boolean nav(int command, boolean repeat) {
        if (repeat && (command == NavInput.LEFT || command == NavInput.RIGHT || command == NavInput.CONFIRM || command == NavInput.ALT)) {
            return true;
        }
        if (asking != null) {
            if (command == NavInput.LEFT || command == NavInput.RIGHT) answer = 1 - answer;
            else if (command == NavInput.UP) answer = 0;     // 1.3.0 reship: the answers listed top to bottom (Remove, Keep)
            else if (command == NavInput.DOWN) answer = 1;
            else if (command == NavInput.BACK) asking = null;
            else if (command == NavInput.CONFIRM) answerTap(answer);
            return true;
        }
        switch (command) {
            case NavInput.UP:
            case NavInput.DOWN: {
                int step = command == NavInput.UP ? -1 : 1;
                // A pack row takes the pad's highlight even when A cannot switch it (no per-mod status in the game code, Mods off, the
                // game running), as a drag already reaches it: its help and Y (Remove) must be reachable by pad too (hunt-android H1).
                // The loose files' row stays skipped: it has neither a switch nor Remove.
                for (int i = focus + step; i >= 0 && i < rowCount(); i += step) {
                    if (enabled(i) || padStop(i)) {
                        focus = i;
                        break;
                    }
                }
                return true;
            }
            case NavInput.LEFT:
            case NavInput.RIGHT:
                if (kind(focus) == Kind.ADD_FOLDER || kind(focus) == Kind.ADD_ZIP) return true;
                act(focus);
                return true;
            case NavInput.CONFIRM:
                act(focus);
                return true;
            case NavInput.ALT: {
                ModsLibrary.Installed p = pack(focus);
                if (p != null && p.imported && changeable()) {
                    asking = p.name;
                    answer = 1;   // (Keep is highlighted: a remove takes a deliberate move)
                }
                return true;
            }
            case NavInput.BACK:
                return false;
            default:
                return false;
        }
    }

    /**
     * A drag of lines text lines (positive: the finger moved up, the list down): the highlight moves that many rows, over every row
     * (also the grey ones: the loose files, packs while Mods is off, all rows while the game runs), so the drawn window follows it
     * (MenuScenes keeps the highlighted row on screen) and a touch-only player sees every row. A then acts only on a row it can act on;
     * UP / DOWN on a pad still skip the grey rows.
     */
    void scroll(int lines) {
        if (asking != null || !loaded) return;
        focus = Math.max(0, Math.min(rowCount() - 1, focus + lines));
    }

    /** A tap on a row: the highlighted row acts as A; another one only takes the highlight. */
    void tap(int row) {
        if (asking != null || !enabled(row)) return;
        if (row == focus) act(row);
        else focus = row;
    }

    /** A tap on an answer of the remove question (0 Remove, 1 Keep). */
    void answerTap(int which) {
        if (asking == null) return;
        if (which == 0) pending = new Action(Action.Type.REMOVE, false, null, asking);
        asking = null;
    }

    private void act(int row) {
        if (!enabled(row)) return;
        switch (kind(row)) {
            case MASTER:
                pending = new Action(Action.Type.SET_MASTER, !words.scripts, null, null);
                break;
            case PACK: {
                // (words of packs that are gone are dropped: ModsLibrary.change does the same on the file)
                String name = pack(row).name;
                boolean on = !words.packOn(name);
                ModsWords next = words.withPack(name, on).keepOnly(ModsLibrary.names(packs));
                if (!next.fits()) done(TEXT_TOO_MANY_OFF, true);
                else pending = new Action(Action.Type.WRITE_WORDS, on, next, name);
                break;
            }
            case SAFE:
                pending = new Action(Action.Type.WRITE_WORDS, !words.safe, words.withSafe(!words.safe).keepOnly(ModsLibrary.names(packs)), null);
                break;
            case ADD_FOLDER:
                pending = new Action(Action.Type.ADD_FOLDER, false, null, null);
                break;
            default:
                pending = new Action(Action.Type.ADD_ZIP, false, null, null);
                break;
        }
    }

    // ------------------------------------------------------------------------------------------ the page

    MenuScenes.Page page() {
        MenuScenes.Page page = new MenuScenes.Page();
        page.title = ShellPages.TITLE;
        page.tabs = new String[] {TITLE};
        page.shoulders = false;
        if (!loaded) {
            page.info.add(new MenuScenes.Line("Looking at the mods folder.", MenuScenes.TONE_NORMAL));
            page.prompts.add(new MenuScenes.Prompt(SettingsPages.PROMPT_BACK, "B", NavInput.BACK));
            return page;
        }
        page.status.add(new MenuScenes.Line(words.safe && words.scripts ? "Mods off next start" : summary(modsOn()), MenuScenes.TONE_NORMAL));
        if (!available) page.info.add(new MenuScenes.Line(SettingsPages.TEXT_NOT_IN_VERSION, MenuScenes.TONE_WARNING));
        if (warnMods > 0 && !words.safe) page.info.add(new MenuScenes.Line(warning(warnMods), MenuScenes.TONE_WARNING));
        if (words.scripts && words.safe) page.info.add(new MenuScenes.Line(TEXT_SAFE_PENDING, MenuScenes.TONE_NORMAL));
        if (gameRunning) page.info.add(new MenuScenes.Line(TEXT_GAME_RUNNING, MenuScenes.TONE_WARNING));
        if (sandbox && modsOn() > 0) page.info.add(new MenuScenes.Line(TEXT_SANDBOX, MenuScenes.TONE_NORMAL));
        if (message != null) page.info.add(new MenuScenes.Line(message, messageTone));
        else if (packs.isEmpty() && available) page.info.add(new MenuScenes.Line(TEXT_NONE, MenuScenes.TONE_NORMAL));
        int[] tone = new int[1];
        for (int i = 0; i < rowCount(); ++i) {
            MenuScenes.Row row = new MenuScenes.Row();
            ModsLibrary.Installed p = pack(i);
            row.label = p == null ? label(i) : p.loose ? LABEL_LOOSE : p.name;   // (a folder name is shown as it is)
            row.value = value(i, tone);
            row.valueTone = tone[0];
            row.enabled = enabled(i) || (p != null && p.loose && words.scripts);
            boolean toggle = kind(i) == Kind.MASTER || (perMod && (kind(i) == Kind.SAFE || (p != null && !p.loose)));
            row.arrows = toggle && enabled(i);
            row.canLeft = row.canRight = row.arrows;
            row.restart = toggle;
            page.rows.add(row);
        }
        page.rowFocused = asking == null ? focus : -1;
        page.help.add(new MenuScenes.Line(help(focus), gameRunning || !available ? MenuScenes.TONE_WARNING : MenuScenes.TONE_NORMAL));
        page.prompts.add(new MenuScenes.Prompt(SettingsPages.PROMPT_BACK, "B", NavInput.BACK));
        if (asking != null) {
            page.alert = new ConfirmOverlay(PROMPT_REMOVE, "Remove " + asking + "?",
                    "Its files are deleted from this app. The copy you added it from is not touched.", REMOVE_ANSWERS, answer);
            page.prompts.clear();
            page.prompts.add(new MenuScenes.Prompt(REMOVE_ANSWERS[1], "B", NavInput.BACK));
            page.prompts.add(new MenuScenes.Prompt(SettingsPages.PROMPT_SELECT, "A", NavInput.CONFIRM));
            return page;
        }
        if (enabled(focus)) page.prompts.add(new MenuScenes.Prompt(SettingsPages.PROMPT_SELECT, "A", NavInput.CONFIRM));
        ModsLibrary.Installed p = pack(focus);
        if (p != null && p.imported && changeable()) page.prompts.add(new MenuScenes.Prompt(PROMPT_REMOVE, "Y", NavInput.ALT));
        return page;
    }

    private String label(int row) {
        switch (kind(row)) {
            case MASTER: return LABEL_MASTER;
            case SAFE: return LABEL_SAFE;
            case ADD_FOLDER: return LABEL_ADD_FOLDER;
            default: return LABEL_ADD_ZIP;
        }
    }

    // ------------------------------------------------------------------------------------------ in the game (side menu)

    /**
     * The side menu's Mods line from the library's live status (v2, or v1's first line): "2 mods on: Gun Pack loaded, Car Pack failed
     * (resource version 10, the game's entry has 9)." "" without a status or with mods off; v1 text gives SideMenuRows' older line.
     */
    static String inGame(String raw) {
        ModsStatus s = ModsStatus.parse(raw);
        if (s == null || !s.v2) return null;   // (the caller keeps its v1 words)
        if (s.safe) return "Mods are off for this start.";
        if (!s.enabled) return "";
        ArrayList<String> parts = new ArrayList<String>();
        int on = 0;
        for (ModsStatus.Pack p : s.packs) {
            if (!p.on || p.files == 0) continue;
            on++;
            String name = p.loose ? "loose files" : p.name;
            if (p.applied > 0 && p.skipped == 0) parts.add(name + " loaded");
            else if (p.applied > 0) parts.add(name + " partly loaded (" + p.reason + ")");
            else if (p.skipped > 0) parts.add(name + " failed (" + p.reason + ")");
            else parts.add(name + " waiting");
        }
        if (on == 0) return "No mods on.";
        StringBuilder out = new StringBuilder(summary(on)).append(": ");
        for (int i = 0; i < parts.size(); ++i) out.append(i == 0 ? "" : ", ").append(parts.get(i));
        return out.append('.').toString();
    }
}
