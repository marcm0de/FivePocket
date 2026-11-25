package dev.recompbootstrap.smoke;

/** Public setup sentences. No file paths, runtime identifiers or private-build advice in the UI. */
final class PublicCodeWords {
    static final String INSTALL = "Install Game Code";
    static final String HELP = "Choose the code bundle made on your computer from your own discs (in the file picker, open the menu and choose Downloads if it is not under Recent). Then copy both discs' files. App updates keep the code you imported.";
    static final String MISSING = "No game code is installed. Open Set Up Game and choose Install Game Code.";
    static final String APP_RECORD = "This app's game record cannot be read. Install the app again over this copy.";
    static final String VERSION = "This code bundle is for another app version. Make a bundle for this version on your computer.";
    static final String GAME = "This code bundle is for another release of the game. Make it from the supported discs.";
    /** Route A (lead 2026-11-24): a title update's bundle whose base program is not the supported play disc's. */
    static final String TITLE_UPDATE_BASE = "This code bundle updates another release of the game. Make it from the supported discs.";
    /** A bundle for an updated game program that does not name the disc program it updates (an older builder). */
    static final String TITLE_UPDATE_NO_BASE = "This code bundle does not name the game disc it updates. Make it again with the current builder.";
    /** The bundle screen's quiet line while the installed bundle carries an older builder's piece (lead 2026-11-24). */
    static final String OLDER_BUILDER = "Rebuild with the new builder for the intro-movie fix.";
    static final String COMPONENTS = "This code bundle is missing a required part. Make a complete bundle on your computer.";
    static final String EXTRA = "This code bundle contains an unexpected part. Make a bundle for this app version.";
    static final String DIFFERENT = "A part of this code bundle does not match this app's game record. Make a bundle for this version.";
    static final String SEAL = "The installed bundle's seal is missing or damaged. Your imported files were kept.";
    static final String CHANGED = "An imported game code file is missing or changed. Your other files were kept.";
    static final String BUSY = "Another copy or import is running. Wait until it finishes, then try again.";
    /** Next update: the bundle's TU or DLC parts could not be moved into the game's folders (CodeBundleImporter.placeExtras). */
    static final String EXTRAS = "The game code is in place, but its title update or DLC could not be put where the game reads them. Open the app again to finish.";
    static final String RUNNING = "The game is running. Close it before installing game code.";
    static final String NOT_CHOSEN = "No file was chosen. Choose Install Game Code to open the file picker again.";
    static final String PICKER = "This device has no file picker. Install one to choose your code bundle.";
    static final String READ = "The chosen bundle could not be read. Check that its storage is connected, then choose it again.";
    static final String STORAGE = "The game code could not be stored. Check this device's storage and try again.";
    static final String STOPPED = "The import stopped. Finished disc files and previously imported code were kept.";
    static final String INSTALLED = "The game code is installed. Copy both discs' files, then choose Play on the main menu.";
    static final String DAMAGED = "The code bundle is damaged or incomplete. Copy it to this device again.";
    static final String FORMAT = "This file is not a supported code bundle. Choose the bundle made by the desktop builder.";
    static final String EXISTS = "Game code is already imported. It was kept unchanged.";
    /** "Code verified (seal 078bb482)." from the installed bundle's seal, or nothing when it cannot be read. */
    static String verified(String seal) {
        return seal == null || seal.length() < 8 ? "" : " Code verified (seal " + seal.substring(0, 8) + ").";
    }
    /** " (seal 078bb482)" for a sentence that goes on, or nothing. */
    static String sealNote(String seal) {
        return seal == null || seal.length() < 8 ? "" : " (seal " + seal.substring(0, 8) + ")";
    }
    static String outcome(VerifiedExtractor.Outcome result) {
        if (result == null) return STORAGE;
        if (result.cancelled) return STOPPED;
        if (result.complete) return INSTALLED;
        // Our contract's individual refusals already have player-facing words.
        for (String text : new String[]{APP_RECORD, VERSION, GAME, TITLE_UPDATE_BASE, TITLE_UPDATE_NO_BASE, COMPONENTS, EXTRA, DIFFERENT, SEAL,
                CHANGED, RUNNING, EXTRAS})
            if (text.equals(result.error)) return text;
        switch (result.problem) {
            case BUSY: return BUSY;
            case SOURCE_UNREADABLE: return READ;
            case BUNDLE_DAMAGED: return DAMAGED;
            case BUNDLE_OTHER: return VERSION;
            case NOT_A_BUNDLE: return FORMAT;
            case EXISTING_INSTALL: return EXISTS;
            case NO_SPACE: return "There is not enough free space for the game code. Free space on this device, then try again.";
            default: return STORAGE;
        }
    }
    /** Reword installer failures without exposing app paths or claiming the APK contains game code. */
    static String activation(String reason) {
        if (reason == null) return STORAGE;
        if (reason.equals(ShipFirstRun.BUSY)) return BUSY;
        if (reason.startsWith("This app only works for the device's main user"))
            return "The game can run only in this device's main user profile. Switch to that profile and set up there.";
        if (reason.startsWith("Not enough free space"))
            return "There is not enough free space to finish installing the game code. Free space, then choose Install Game Code again.";
        if (reason.startsWith("The start request inside this app"))
            return "This app's start settings cannot be used on this device. Install the app again over this copy.";
        if (reason.startsWith("The game code inside this app is not usable"))
            return "Android cannot use this imported game code. Make a bundle for this app version.";
        return "The imported game code could not be put in place. Check this device's storage, then choose Install Game Code again.";
    }

    static String launch(String reason) {
        for (String known : new String[]{MISSING, VERSION, GAME, TITLE_UPDATE_BASE, TITLE_UPDATE_NO_BASE, COMPONENTS, EXTRA, DIFFERENT, SEAL, CHANGED})
            if (known.equals(reason)) return known;
        if (reason != null && reason.startsWith(ShipPlay.DATA_MISSING_PREFIX))
            return "Some disc files are missing or changed. Open Copy Disc Files to check them.";
        return "The game could not start. Open Set Up Game to check the imported code and disc files.";
    }
    private PublicCodeWords() {}
}
