package dev.recompbootstrap.smoke;

/** Pure setup state: no setup action is enabled while an import is in flight or the game is running. */
final class PublicSetupModel {
    static final int NONE = 0, PICK = 1, DISCS = 2, LEAVE = 3, STOP = 4, FINISH = 5;
    static final String FINISH_LABEL = "Finish Setup";
    int focus;
    boolean busy, ready;
    /** The imported bundle on this device passes its check (PublicGameCode.installedProblem). */
    boolean imported;
    /** This app version's game code is not in place (ShipReady CODE or PIECE): what the activation after an import does.
     *  Missing disc files (DATA) are the disc page's work, not this row's. */
    boolean codeMissing;
    /** The installed bundle carries an earlier builder's piece the app still accepts (PublicGameCode.olderBuild): one quiet line. */
    boolean olderBuild;
    /** The first row's action: with a valid import and the code not in place, finish setup from that import instead of
     *  asking for the file again (Thor RC1.1 device test, 2026-10-25: a failed activation left the import, and only a
     *  second pick of the same file finished it). */
    boolean finishes() { return imported && codeMissing; }
    String notice = PublicCodeWords.HELP;
    long done, total;
    int confirm(boolean gameRunning) {
        if (busy) return NONE;
        if (gameRunning) { notice = PublicCodeWords.RUNNING; return NONE; }
        if (focus == 0) return finishes() ? FINISH : PICK;
        if (focus == 1) return DISCS;
        return LEAVE;
    }
    /** Finish Setup runs the activation only, which cannot be stopped part-way: B then does nothing until it ends. */
    boolean activating;
    int back() { return busy ? (activating ? NONE : STOP) : LEAVE; }
    void move(int delta, boolean repeat) {
        if (busy) return;
        int next = focus + delta;
        if (next < 0 || next > 2) { if (repeat) return; next = (next + 3) % 3; }
        focus = next;
    }
    void started() { busy = true; done = total = 0; notice = "Checking and installing the game code. B: stop."; }
    void finishing() { busy = true; activating = true; done = total = 0; notice = "Finishing setup from the imported game code."; }
    void finished(VerifiedExtractor.Outcome outcome, String activationProblem) { finished(outcome, activationProblem, null); }
    /** seal: the installed bundle's seal when the import completed (shown as its first 8 digits), else null. */
    void finished(VerifiedExtractor.Outcome outcome, String activationProblem, String seal) {
        busy = false; activating = false;
        notice = activationProblem == null ? PublicCodeWords.outcome(outcome) + PublicCodeWords.verified(seal)
                : "The bundle was kept" + PublicCodeWords.sealNote(seal) + ", but setup could not finish. " + activationProblem;
    }
    MenuScenes.Page page() {
        MenuScenes.Page p = new MenuScenes.Page();
        p.title = ShellPages.TITLE;
        p.tabs = new String[]{"SET UP GAME"}; p.shoulders = false;
        for (String label : new String[]{finishes() ? FINISH_LABEL : PublicCodeWords.INSTALL, "Copy Disc Files", ready ? "Ready — Back to Menu" : "Back to Menu"}) {
            MenuScenes.Row r = new MenuScenes.Row(); r.label = label; r.enabled = !busy; p.rows.add(r);
        }
        p.rowFocused = focus;
        p.help.add(new MenuScenes.Line(notice, MenuScenes.TONE_NORMAL));
        if (olderBuild && !busy) p.help.add(new MenuScenes.Line(PublicCodeWords.OLDER_BUILDER, MenuScenes.TONE_NORMAL));
        if (busy && total > 0) p.infoBar = Math.max(0f, Math.min(1f, (float)((double)done / total)));
        p.prompts.addAll(MenuScenes.legendPrompts(activating ? "" : busy ? "B Stop" : "A Select   B Back"));
        return p;
    }
}
