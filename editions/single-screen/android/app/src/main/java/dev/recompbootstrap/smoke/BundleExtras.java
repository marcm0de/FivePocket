package dev.recompbootstrap.smoke;

import java.io.File;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.StandardCopyOption;
import java.util.Locale;

/**
 * Puts the bundle's title update and DLC packs where the game reads them (next update; builder-gui's roles
 * CodeBundle.ROLE_TITLE_UPDATE and ROLE_DLC). Pure Java.
 *
 * The import writes them into bundle/ like every component, checked against the manifest and the seal. Then they are moved
 * (renamed, the same storage: no second copy of 2 GB of packs) out of bundle/:
 *  - update/update.rpf to private-trace/inputs/native-update/update.rpf, the folder every start request names with
 *    --native-update-directory (the game opens update.rpf in the update: device);
 *  - dlc/<name> to private-trace/inputs/dlc-stfs/<NAME>, the DLC row's folder (DlcPacks.DIRECTORY), where "Add DLC" copies too.
 *    Component names are lower case; real package names are upper-case hex, and the game reads names without regard to case
 *    (it sorts and de-duplicates them in lower case), so a pack is placed under its upper-case name, and a pack already in the
 *    folder under the same name in any case is replaced, never doubled.
 * Nothing else is removed. A destination that is a link or not a regular file stops the placing (nothing replaced through it).
 * Called after a completed import and at every start of the app (a placing interrupted earlier is finished); never while the
 * game runs, since the game holds these files open.
 */
final class BundleExtras {
    static final String UPDATE_DIRECTORY = "inputs/native-update", UPDATE_FILE = "update.rpf";

    static final class Result {
        final int packs;
        final boolean update;
        /** null: everything that was waiting in bundle/ is in place. */
        final String problem;

        Result(int packs, boolean update, String problem) {
            this.packs = packs;
            this.update = update;
            this.problem = problem;
        }
    }

    private BundleExtras() {
    }

    /** The upper-case file name a dlc/<name> component is placed under. */
    static String packFileName(String componentName) {
        return componentName.substring(CodeBundle.DLC_PREFIX.length()).toUpperCase(Locale.ROOT);
    }

    /**
     * Moves every title-update and DLC component still in filesDir/game-install/bundle into place. Nothing to do (no bundle,
     * a bundle without them, all placed already) is a Result with no problem.
     */
    static Result place(File filesDir) {
        return place(filesDir, UPDATE_DIRECTORY);
    }

    /**
     * updateDirectory: where update.rpf goes, relative to files/private-trace: the folder the installed bundle's profile's own start
     * request names (ShipConfig.updateDirectory; one app, two pinned profiles, owner 2026-11-24). It must lie under inputs/.
     */
    static Result place(File filesDir, String updateDirectory) {
        if (updateDirectory == null || !updateDirectory.startsWith("inputs/") || !ShipConfig.dataDirectory(updateDirectory)) {
            return new Result(0, false, "the update folder is not a game folder under inputs/");
        }
        int packs = 0;
        boolean update = false;
        try {
            File root = InstallGuard.root(filesDir);
            File manifest;
            try {
                manifest = InstallGuard.under(root, CodeBundleImporter.BUNDLE + "/" + CodeBundle.MANIFEST_NAME);
            } catch (IOException noInstall) {
                return new Result(0, false, null);
            }
            byte[] data = InstallGuard.readSmall(manifest, CodeBundleImporter.MAX_MANIFEST_BYTES);
            if (data == null) return new Result(0, false, null);
            CodeBundle bundle = CodeBundle.parseManifest(new String(data, StandardCharsets.UTF_8));
            File privateTrace = new File(filesDir, "private-trace");
            for (CodeBundle.Entry entry : bundle.entries) {
                if (!CodeBundle.placedOutside(entry.role)) continue;
                File source = InstallGuard.under(root, CodeBundleImporter.BUNDLE + "/" + entry.name);
                if (!Files.isRegularFile(source.toPath(), LinkOption.NOFOLLOW_LINKS)) continue;   // placed already
                if (source.length() != entry.bytes) return new Result(packs, update, entry.name + " has another size than the bundle says");
                File target;
                if (CodeBundle.ROLE_TITLE_UPDATE.equals(entry.role)) {
                    File folder = folder(privateTrace, updateDirectory);
                    target = new File(folder, UPDATE_FILE);
                } else {
                    File folder = folder(privateTrace, DlcPacks.DIRECTORY);
                    target = new File(folder, DlcPacks.slotName(folder.list(), packFileName(entry.name)));   // the same pack under another case
                }
                if (Files.isSymbolicLink(target.toPath()) || (target.exists() && !Files.isRegularFile(target.toPath(), LinkOption.NOFOLLOW_LINKS))) {
                    return new Result(packs, update, target.getName() + ": something else stands where it goes");
                }
                Files.move(source.toPath(), target.toPath(), StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
                if (CodeBundle.ROLE_TITLE_UPDATE.equals(entry.role)) update = true;
                else ++packs;
            }
            return new Result(packs, update, null);
        } catch (IOException | RuntimeException | JsonLite.ParseException e) {
            return new Result(packs, update, e.getClass().getSimpleName() + (e.getMessage() == null ? "" : " (" + e.getMessage() + ")"));
        }
    }

    /** The installed bundle whose choices were last applied: the SHA-256 of its manifest.json. Written only by applyChoices. */
    static final String APPLIED_RECORD = "settings/bundle-applied.txt";

    /**
     * After an import's game code is in place: the bundle's choices become saved settings, through SettingsStore (the Settings tab's
     * own way, so its files and settings.json agree), in one save:
     *  - the icon pick (AppIcon.pick; missing or unknown = Pink/Blue Dusk), unless the last import brought the same pick (the
     *    player's later choice in Settings stands; AppIcon.IMPORT_RECORD);
     *  - DLC on (lead 2026-11-22), when the bundle brings packs and it is not the bundle whose choices were applied last
     *    (APPLIED_RECORD), so the player can switch it off again.
     * A record is written only when the save kept its value: DLC under a library without the STFS reader is dropped by the store and
     * tried again at the next activation. Returns "kept", "saved[ <icon id>][ dlc on]" or "not saved: <reason>". Never throws.
     */
    static String applyChoices(File filesDir, Capabilities capabilities, SettingsStore.GameRunning running) {
        try {
            byte[] data;
            try {
                data = InstallGuard.readSmall(InstallGuard.under(InstallGuard.root(filesDir), CodeBundleImporter.BUNDLE + "/" + CodeBundle.MANIFEST_NAME),
                        CodeBundleImporter.MAX_MANIFEST_BYTES);
            } catch (IOException noInstall) {
                data = null;
            }
            String json = data == null ? null : new String(data, StandardCharsets.UTF_8);
            String identity = data == null ? "" : VerifiedExtractor.hex(sha256(data));
            boolean packs = false;
            if (json != null) {
                try {
                    for (CodeBundle.Entry entry : CodeBundle.parseManifest(json).entries) packs |= CodeBundle.ROLE_DLC.equals(entry.role);
                } catch (JsonLite.ParseException | IllegalArgumentException unreadable) {
                    packs = false;
                }
            }
            File iconRecord = new File(filesDir, AppIcon.IMPORT_RECORD), applied = new File(filesDir, APPLIED_RECORD);
            int pick = AppIcon.pick(json);
            boolean icon = AppIcon.importApplies(pick, small(iconRecord));
            boolean dlc = packs && !identity.equals(text(small(applied)));
            if (!icon && !dlc) return "kept";
            if (running.check()) return "not saved: the game is running";
            boolean dlcKept = false;
            if (!new File(filesDir, "private-trace").isDirectory() && !new File(filesDir, RuntimeMapping.SETTINGS_JSON).exists()) {
                // Before the game is put in place SettingsStore saves nothing yet (no game folder), and with no settings.json its load
                // reads each row from its file, so the icon file alone is the saved choice. (DLC waits for the next activation.)
                if (icon) {
                    File target = new File(filesDir, AppIcon.FILE);
                    String word = AppIcon.word(pick);
                    if (word == null) {
                        if (target.exists() && !target.delete()) return "not saved: the icon file could not be removed";
                    } else {
                        folderOf(target);
                        AtomicFiles.write(target, (word + "\n").getBytes(StandardCharsets.US_ASCII));
                    }
                }
            } else {
                SettingsStore store = new SettingsStore(filesDir, running);
                Settings settings = store.load(capabilities).settings;
                if (icon) settings = settings.with(SettingsSchema.APP_ICON, pick);
                if (dlc) settings = settings.with(SettingsSchema.DLC_PACKS, 1);
                SettingsStore.SaveResult saved = store.save(settings, capabilities);
                if (!saved.ok) return "not saved: " + (saved.refusedGameRunning ? "the game is running" : saved.error);
                dlcKept = dlc && store.load(capabilities).settings.get(SettingsSchema.DLC_PACKS) == 1;
            }
            if (icon) {
                folderOf(iconRecord);
                AtomicFiles.write(iconRecord, (AppIcon.IDS[pick] + "\n").getBytes(StandardCharsets.US_ASCII));
            }
            if (dlcKept) {
                folderOf(applied);
                AtomicFiles.write(applied, (identity + "\n").getBytes(StandardCharsets.US_ASCII));
            }
            return "saved" + (icon ? " " + AppIcon.IDS[pick] : "") + (dlcKept ? " dlc on" : "");
        } catch (IOException | RuntimeException e) {
            return "not saved: " + e.getClass().getSimpleName();
        }
    }

    private static byte[] small(File f) {
        try {
            return f.isFile() ? InstallGuard.readSmall(f, 256) : null;
        } catch (IOException e) {
            return null;
        }
    }

    private static String text(byte[] b) {
        return b == null ? "" : new String(b, StandardCharsets.US_ASCII).trim();
    }

    private static void folderOf(File f) throws IOException {
        File folder = f.getParentFile();
        if (!folder.isDirectory() && !folder.mkdirs()) throw new IOException("no settings folder");
    }

    private static byte[] sha256(byte[] data) {
        java.security.MessageDigest md = VerifiedExtractor.sha256();
        md.update(data);
        return md.digest();
    }

    /** privateTrace/relative as a real folder (made when missing); a link on the way is refused. */
    private static File folder(File privateTrace, String relative) throws IOException {
        File at = privateTrace;
        for (String part : ("." + "/" + relative).split("/")) {
            if (!part.equals(".")) at = new File(at, part);
            if (Files.isSymbolicLink(at.toPath())) throw new IOException(at.getName() + " is a link");
            if (!at.isDirectory() && !at.mkdir()) throw new IOException(at.getName() + " could not be made");
        }
        return at;
    }
}
