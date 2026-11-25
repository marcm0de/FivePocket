package dev.recompbootstrap.smoke;

import android.content.ComponentName;
import android.content.Context;
import android.content.pm.PackageManager;
import android.util.Log;

import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * The app icon (owner 2026-11-22: "just add an in-app settings icon so there's no confusion"). The launcher icon is always one of
 * this edition's three colourway logos (the owner's own art; the old white V is gone). The manifest declares one launcher alias of
 * ShipStartActivity per colourway; Pink/Blue Dusk is enabled from install, the others are disabled. The Settings row "App icon"
 * (SettingsSchema.APP_ICON, Display) saves the choice as one word in settings/app-icon.txt (no file = Pink/Blue Dusk), and the app
 * enables the matching alias after a save and at every start.
 *
 * The builder's pick (owner's final design, 2026-11-22): a bundle made by the builder window names the player's logo as
 * manifest.json "app_icon": "<edition>-<colourway>" (builder-gui; the edition word does not matter, both Android apps import the
 * same Thor-line bundle). After an import's game code is in place that pick becomes the saved choice, through the settings store as
 * the Settings tab saves it (BundleExtras.applyChoices, from afterActivation); a missing or unknown key means Pink/Blue Dusk. The player can change it in Settings afterwards, and
 * their choice holds until a later import brings a different pick: settings/app-icon-import.txt (IMPORT_RECORD) keeps the last
 * imported pick, and an import with that same pick changes nothing.
 *
 * Only alias names from the table below reach PackageManager. The wanted alias is enabled before the others are disabled, so the
 * app never has no launcher entry; when the aliases already stand as wanted nothing is called. Android keeps a component's enabled
 * state across app updates as long as its name stays, so the alias names are permanent.
 */
final class AppIcon {
    static final String FILE = "settings/app-icon.txt";
    /** The Java package (the manifest's namespace) the alias names live in; the application id differs per edition. */
    static final String CLASS_PACKAGE = "dev.recompbootstrap.smoke";
    /** In the row's order; DEFAULT (the icon from install) first. The ids are the words of FILE and of the builder's logo names. */
    static final String[] IDS = {"pink-blue-dusk", "golden-sunset", "peach-rose"};
    static final String[] LABELS = {"Pink/Blue Dusk", "Golden Sunset", "Peach/Rose"};
    static final String[] ALIASES = {CLASS_PACKAGE + ".LauncherPinkBlueDusk", CLASS_PACKAGE + ".LauncherGoldenSunset",
            CLASS_PACKAGE + ".LauncherPeachRose"};
    /** The row's previews: the painter's picture names (UiKit.Faces loads each from the launcher icon's own foreground). */
    static final String[] PICTURES = {"app-icon-pink-blue-dusk", "app-icon-golden-sunset", "app-icon-peach-rose"};
    static final int DEFAULT = 0;
    static final String MANIFEST_KEY = "app_icon";
    /** The last pick an import applied (one word of IDS); written only by BundleExtras.applyChoices, never part of the settings plan. */
    static final String IMPORT_RECORD = "settings/app-icon-import.txt";
    /** The edition words of the builder's logo files. */
    private static final String[] EDITION_WORDS = {"android", "thor", "ios", "duo"};

    private AppIcon() {
    }

    static List<String> aliases() {
        return Arrays.asList(ALIASES);
    }

    /** What the manifest itself says: only the default colourway's alias is enabled. */
    static boolean enabledInManifest(String alias) {
        return ALIASES[DEFAULT].equals(alias);
    }

    /** The colourway a FILE content names; no file, an empty one or an unknown word: DEFAULT. */
    static int parse(byte[] content) {
        if (content == null) return DEFAULT;
        String word = new String(content, StandardCharsets.US_ASCII).trim();
        for (int i = 0; i < IDS.length; ++i) {
            if (IDS[i].equals(word)) return i;
        }
        return DEFAULT;
    }

    /** FILE's word for a colourway; null (no file) for DEFAULT and for anything that is not a colourway. */
    static String word(int index) {
        return index > DEFAULT && index < IDS.length ? IDS[index] : null;
    }

    /** The colourway a bundle manifest's "app_icon" names: <edition>-<colourway>; no key, not a string, not JSON, unknown: DEFAULT. */
    static int pick(String manifestJson) {
        String value = null;
        if (manifestJson != null) {
            try {
                Object parsed = JsonLite.parse(manifestJson);
                Object v = parsed instanceof Map ? ((Map<?, ?>) parsed).get(MANIFEST_KEY) : null;
                if (v instanceof String) value = (String) v;
            } catch (JsonLite.ParseException | RuntimeException e) {
                value = null;
            }
        }
        if (value == null) return DEFAULT;
        for (String edition : EDITION_WORDS) {
            for (int i = 0; i < IDS.length; ++i) {
                if (value.equals(edition + "-" + IDS[i])) return i;
            }
        }
        return DEFAULT;
    }

    /** True when an import's pick should become the saved choice: no earlier import recorded, or it brought another pick. */
    static boolean importApplies(int pick, byte[] lastRecord) {
        return lastRecord == null || !IDS[pick].equals(new String(lastRecord, StandardCharsets.US_ASCII).trim());
    }

    /**
     * After the game code of an import is in place (activation passed): the bundle's icon pick and its DLC (BundleExtras.applyChoices,
     * with the app's own capability scan of the library now in place; inputOption: SettingsScreen.inputOptionInApp()), then the
     * launcher alias. Never throws.
     */
    static void afterActivation(Context context, SettingsStore.GameRunning running, boolean inputOption) {
        try {
            File filesDir = context.getFilesDir();
            String outcome = BundleExtras.applyChoices(filesDir, CapabilityScanner.load(filesDir, inputOption), running);
            Log.i(ShipLog.TAG, "BUNDLE_CHOICES " + outcome);
        } catch (Exception e) {
            Log.w(ShipLog.TAG, "BUNDLE_CHOICES: " + e.getClass().getSimpleName());
        }
        reconcile(context);
    }

    /** The preview picture for a row value text (LABELS), or null. */
    static String pictureForLabel(String label) {
        for (int i = 0; i < LABELS.length; ++i) {
            if (LABELS[i].equals(label)) return PICTURES[i];
        }
        return null;
    }

    /**
     * The changes that leave exactly `wanted` enabled, in the order to apply them: the wanted alias first (when it is not
     * enabled), then every other enabled alias off. `enabled` holds each alias's effective state now. Empty = nothing to do.
     */
    static Map<String, Boolean> plan(String wanted, Map<String, Boolean> enabled) {
        Map<String, Boolean> changes = new LinkedHashMap<String, Boolean>();
        if (!Boolean.TRUE.equals(enabled.get(wanted))) changes.put(wanted, true);
        for (String alias : aliases()) {
            if (!alias.equals(wanted) && Boolean.TRUE.equals(enabled.get(alias))) changes.put(alias, false);
        }
        return changes;
    }

    /** Reads the saved choice and brings the aliases in line. Never throws; logs one line when it changed something. */
    static void reconcile(Context context) {
        try {
            int choice = parse(read(new File(context.getFilesDir(), FILE)));
            String wanted = ALIASES[choice];
            PackageManager pm = context.getPackageManager();
            Map<String, Boolean> enabled = new LinkedHashMap<String, Boolean>();
            for (String alias : aliases()) {
                int state = pm.getComponentEnabledSetting(new ComponentName(context.getPackageName(), alias));
                enabled.put(alias, state == PackageManager.COMPONENT_ENABLED_STATE_DEFAULT ? enabledInManifest(alias)
                        : state == PackageManager.COMPONENT_ENABLED_STATE_ENABLED);
            }
            Map<String, Boolean> changes = plan(wanted, enabled);
            for (Map.Entry<String, Boolean> c : changes.entrySet()) {
                pm.setComponentEnabledSetting(new ComponentName(context.getPackageName(), c.getKey()),
                        c.getValue() ? PackageManager.COMPONENT_ENABLED_STATE_ENABLED : PackageManager.COMPONENT_ENABLED_STATE_DISABLED,
                        PackageManager.DONT_KILL_APP);
            }
            if (!changes.isEmpty()) Log.i(ShipLog.TAG, "APP_ICON " + IDS[choice] + " changes=" + changes.size());
        } catch (Exception e) {
            Log.w(ShipLog.TAG, "APP_ICON unchanged: " + e.getClass().getSimpleName());
        }
    }

    /** At most 64 bytes of the file; null when it is missing or unreadable. */
    private static byte[] read(File file) {
        if (!file.isFile()) return null;
        try (InputStream in = new FileInputStream(file)) {
            byte[] buffer = new byte[64];
            int n = in.read(buffer);
            return n <= 0 ? new byte[0] : Arrays.copyOf(buffer, n);
        } catch (IOException e) {
            return null;
        }
    }
}
