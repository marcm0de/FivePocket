package dev.recompbootstrap.smoke;

import java.util.ArrayList;
import java.util.Collections;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * The manifest of a code bundle: the one file the desktop builder (tools/own_disc/disc_builder.py) writes and the
 * app imports. The formats are "gta5-code-bundle/1" and the public "/2" as described in tools/own_disc/BUNDLE-FORMAT.md: a ZIP file whose
 * first entry is manifest.json. Pure Java.
 */
final class CodeBundle {
    static final String SCHEMA_PREFIX = "gta5-code-bundle/";
    static final String MANIFEST_NAME = "manifest.json";
    /** The interface between the app and the game library that this app version implements. */
    static final String RUNTIME_ABI = "private-trace-fd-dlopen/1";
    /** Sizes must fit the ZIP format without its 64-bit extension. */
    static final long MAX_BYTES = (1L << 32) - 2;
    static final String ROLE_LIBRARY = "game-library", ROLE_EXECUTABLE = "title-xex", ROLE_IMAGE = "decoded-image",
            ROLE_SHADERS = "shader-pack", ROLE_RUNTIME = "runtime-data";
    /**
     * 1.2.0 (LEDGER 1b): the launcher's fonts, glyphs, art and sounds, made by the builder from the player's own discs. Optional;
     * read in place from files/game-install/bundle/ui (UI_DIR); never part of the public pin (PublicGameCode skips them).
     */
    static final String ROLE_UI = "ui-asset", UI_DIR = "game-install/bundle/ui";
    static final long UI_MAX_BYTES = 16L * 1024 * 1024;

    /** A ui-asset is a file under ui/ (or ui/sounds/, or the one launcher track in ui/music/) of a kind the launcher reads, of a sensible size. */
    static boolean uiAssetAllowed(String name, long bytes) {
        if (!name.startsWith("ui/") || bytes > UI_MAX_BYTES) return false;
        String rest = name.substring(3);
        if (rest.startsWith("sounds/")) return rest.indexOf('/', 7) < 0 && rest.endsWith(".wav");
        if (rest.startsWith("music/")) return MenuMusic.allowedName(rest.substring(6));   // (next update: the launcher music, MenuMusic.NAMES)
        return rest.indexOf('/') < 0 && (rest.endsWith(".ttf") || rest.endsWith(".png") || rest.endsWith(".txt"));
    }
    static final String PUBLIC_ABI = "private-trace-fd-dlopen/2";
    /**
     * Next update (builder-gui, agreed 2026-11-22): the player's own title update and DLC packs, carried for TU27's Online. Format 2
     * only, never part of the public pin (the seal and each component's SHA-256 cover them). After the import they are moved out of
     * bundle/ into the game's own folders (BundleExtras): update/update.rpf to private-trace/inputs/native-update/update.rpf (the
     * start request's --native-update-directory), each dlc/<name> to private-trace/inputs/dlc-stfs (the DLC row's folder).
     */
    static final String ROLE_TITLE_UPDATE = "title-update-data", ROLE_DLC = "dlc-package";
    static final String TITLE_UPDATE_NAME = "update/update.rpf", DLC_PREFIX = "dlc/";

    /** A role whose component leaves bundle/ after the import (BundleExtras): not pinned, not re-checked in bundle/. */
    static boolean placedOutside(String role) {
        return ROLE_TITLE_UPDATE.equals(role) || ROLE_DLC.equals(role);
    }

    /** dlc/<name>: name is a package name (DlcPacks.validName); lower case, as validName requires of every component name. */
    static boolean dlcNameAllowed(String name) {
        return name.startsWith(DLC_PREFIX) && DlcPacks.validName(name.substring(DLC_PREFIX.length()));
    }

    static final class Entry {
        /** The ZIP entry's name; lower case, parts joined by '/'. */
        final String name;
        final long bytes;
        final String sha256;
        final String role;
        /** "built", "disc" or "supplied" (a file handed to the builder because no stage builds it yet). */
        final String provenance;

        Entry(String name, long bytes, String sha256, String role, String provenance) {
            this.name = name;
            this.bytes = bytes;
            this.sha256 = sha256;
            this.role = role;
            this.provenance = provenance;
        }
    }

    /** The N of "gta5-code-bundle/N". */
    final int version;
    final String executableSha256;
    /**
     * game.base_executable_sha256, or null: a title update's bundle names the patched program it runs (executableSha256) and the
     * play disc's own program it was made from (tools/own_disc/disc_builder.py). Which bundles a public app accepts:
     * PublicGameCode.gameProblem.
     */
    final String baseExecutableSha256;
    final String runtimeAbi;
    final String profile, flavour;
    /** In the manifest's order. Unmodifiable. */
    final List<Entry> entries;

    private CodeBundle(int version, String executableSha256, String baseExecutableSha256, String runtimeAbi, String profile, String flavour,
            List<Entry> entries) {
        this.version = version;
        this.executableSha256 = executableSha256;
        this.baseExecutableSha256 = baseExecutableSha256;
        this.runtimeAbi = runtimeAbi;
        this.profile = profile;
        this.flavour = flavour;
        this.entries = Collections.unmodifiableList(entries);
    }

    long totalBytes() {
        long total = 0;
        for (Entry e : entries) total += e.bytes;
        return total;
    }

    /** null when the manifest lists no such component. */
    Entry entry(String name) {
        for (Entry e : entries) {
            if (e.name.equals(name)) return e;
        }
        return null;
    }

    /** One or more parts of [a-z0-9][a-z0-9._-]* joined by '/'. Such a name cannot be "..", absolute or empty. */
    static boolean validName(String name) {
        if (name == null || name.isEmpty() || name.length() > 512) return false;
        boolean partStart = true;
        for (int i = 0; i < name.length(); ++i) {
            char c = name.charAt(i);
            boolean alnum = (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9');
            if (partStart) {
                if (!alnum) return false;
                partStart = false;
            } else if (c == '/') {
                partStart = true;
            } else if (!alnum && c != '.' && c != '_' && c != '-') {
                return false;
            }
        }
        return !partStart;
    }

    /**
     * Reads manifest.json. JsonLite.ParseException when it is not JSON; IllegalArgumentException (with a plain
     * reason) when it is JSON of the wrong shape. Checks the shape only; whether this app accepts the version, the
     * runtime interface and the game executable is the importer's decision.
     */
    static CodeBundle parseManifest(String manifestJson) throws JsonLite.ParseException {
        Object parsed = JsonLite.parse(manifestJson);
        if (!(parsed instanceof Map)) throw new IllegalArgumentException("the manifest is not a JSON object");
        Map<?, ?> map = (Map<?, ?>) parsed;
        String schema = string(map.get("schema"), "schema");
        if (!schema.startsWith(SCHEMA_PREFIX)) throw new IllegalArgumentException("this is not a code bundle (schema " + schema + ")");
        int version;
        try {
            version = Integer.parseInt(schema.substring(SCHEMA_PREFIX.length()));
        } catch (NumberFormatException e) {
            throw new IllegalArgumentException("this is not a code bundle (schema " + schema + ")");
        }
        Object game = map.get("game");
        if (!(game instanceof Map)) throw new IllegalArgumentException("the manifest names no game");
        String executable = string(((Map<?, ?>) game).get("executable_sha256"), "game.executable_sha256");
        if (!SetupCatalog.isSha256(executable)) throw new IllegalArgumentException("the manifest names no game executable hash");
        Object baseValue = ((Map<?, ?>) game).get("base_executable_sha256");
        String base = baseValue == null ? null : string(baseValue, "game.base_executable_sha256");
        if (base != null && !SetupCatalog.isSha256(base)) throw new IllegalArgumentException("the manifest names no base executable hash");
        String abi = string(map.get("runtime_abi"), "runtime_abi");
        Object components = map.get("components");
        if (!(components instanceof List) || ((List<?>) components).isEmpty()) {
            throw new IllegalArgumentException("the manifest lists no components");
        }
        List<Entry> entries = new ArrayList<Entry>();
        Set<String> names = new HashSet<String>();
        int dlcPacks = 0;
        for (Object o : (List<?>) components) {
            if (!(o instanceof Map)) throw new IllegalArgumentException("a component is not a JSON object");
            Map<?, ?> c = (Map<?, ?>) o;
            String name = string(c.get("name"), "component name");
            if (!validName(name) || name.equals(MANIFEST_NAME)) throw new IllegalArgumentException("component name not allowed: " + name);
            if (!names.add(name)) throw new IllegalArgumentException("component listed twice: " + name);
            Object size = c.get("size");
            if (!(size instanceof Long) && !(size instanceof Integer)) throw new IllegalArgumentException("component " + name + " has no size");
            long bytes = ((Number) size).longValue();
            if (bytes < 0 || bytes > MAX_BYTES) throw new IllegalArgumentException("component " + name + " has an impossible size");
            String sha = string(c.get("sha256"), "sha256 of " + name);
            if (!SetupCatalog.isSha256(sha)) throw new IllegalArgumentException("component " + name + " has no SHA-256 value");
            String role = string(c.get("role"), "role of " + name);
            if (ROLE_UI.equals(role) && !uiAssetAllowed(name, bytes)) {
                throw new IllegalArgumentException("component " + name + " is not a launcher file this app reads");
            }
            if (placedOutside(role)) {
                if (version != 2 || !PUBLIC_ABI.equals(abi)) throw new IllegalArgumentException("component " + name + " needs bundle format 2");
                if (ROLE_TITLE_UPDATE.equals(role) && !TITLE_UPDATE_NAME.equals(name)) {
                    throw new IllegalArgumentException("a title update must be named " + TITLE_UPDATE_NAME);
                }
                if (ROLE_DLC.equals(role) && !dlcNameAllowed(name)) {
                    throw new IllegalArgumentException("component " + name + " is not a DLC package name");
                }
                if (bytes == 0) throw new IllegalArgumentException("component " + name + " is empty");
                // (one title update at most follows from its one allowed name: a name cannot be listed twice)
                if (ROLE_DLC.equals(role) && ++dlcPacks > DlcPacks.MAXIMUM_PACKAGES) {
                    throw new IllegalArgumentException("more than " + DlcPacks.MAXIMUM_PACKAGES + " DLC packages");
                }
            }
            if (!ROLE_LIBRARY.equals(role) && !ROLE_EXECUTABLE.equals(role) && !ROLE_IMAGE.equals(role) && !ROLE_SHADERS.equals(role)
                    && !ROLE_UI.equals(role) && !placedOutside(role) && !(version == 2 && PUBLIC_ABI.equals(abi) && ROLE_RUNTIME.equals(role))) {
                throw new IllegalArgumentException("component " + name + " has a role this app does not know: " + role);
            }
            if (ROLE_EXECUTABLE.equals(role) && !sha.equals(executable)) {
                throw new IllegalArgumentException("the bundled executable is not the one the manifest names");
            }
            Object provenance = c.get("provenance");
            entries.add(new Entry(name, bytes, sha, role, provenance instanceof String ? (String) provenance : ""));
        }
        Object profile = map.get("profile"), flavour = map.get("flavour");
        return new CodeBundle(version, executable, base, abi, profile instanceof String ? (String) profile : "",
                flavour instanceof String ? (String) flavour : "", entries);
    }

    private static String string(Object value, String what) {
        if (!(value instanceof String)) throw new IllegalArgumentException("the manifest has no " + what);
        return (String) value;
    }
}
