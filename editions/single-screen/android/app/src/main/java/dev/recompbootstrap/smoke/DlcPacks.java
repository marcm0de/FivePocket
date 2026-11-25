package dev.recompbootstrap.smoke;

import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Locale;
import java.util.regex.Pattern;

/**
 * The player's own DLC packages (Update 1, owner task "put all the DLC files in its own folder ... get it all to work together").
 * Pure Java.
 *
 * The game code reads Xbox 360 content packages in place: `--stfs-content-directory DIR` (just before
 * `--installed-content-catalog`, because the game's argument reader takes arguments from the end) makes each package in DIR one
 * more installed-content row, beside the install disc's packages. Nothing is extracted. The game validates every package itself
 * and skips one it cannot read.
 *
 * DIR is private-trace/inputs/dlc-stfs. It holds only files the player copied there from their own folder; we never ship DLC.
 * LaunchRequest.checkArguments accepts it, because paths under private-trace/inputs/ are allowed.
 *
 * Off by default. When the setting is off, or the folder holds no package, the start request is the template's, byte for byte.
 */
final class DlcPacks {
    /** The folder, relative to private-trace. */
    static final String DIRECTORY = "inputs/dlc-stfs";
    /** The on/off switch, relative to the app's files folder: the Settings row writes it (like INPUT_UNBUFFERED, a switch the app reads). */
    static final String SWITCH = "private-trace/diag-dlc-packs";
    static final String FLAG = "--stfs-content-directory";
    static final String CATALOG_FLAG = "--installed-content-catalog";
    /** The game's content enumerator holds 64 rows; the install disc uses 4. */
    static final int MAXIMUM_PACKAGES = 60;
    /** An Xbox content package file name: what the game's own catalog loader accepts (it keeps the first 40 characters). */
    private static final Pattern NAME = Pattern.compile("[A-Za-z0-9]{1,42}");

    private DlcPacks() {
    }

    static boolean validName(String name) {
        return name != null && NAME.matcher(name).matches();
    }

    /**
     * The file name a pack takes in the folder: the name of a file already there that equals `wanted` without regard to case (the
     * game reads names without regard to case, so two would collide: the first one only would load), else `wanted` itself.
     * present: the folder's listing, null for none.
     */
    static String slotName(String[] present, String wanted) {
        if (present != null) {
            for (String existing : present) {
                if (existing.equalsIgnoreCase(wanted)) return existing;
            }
        }
        return wanted;
    }

    /**
     * The package files in the folder: regular files (no links) with a valid name and at least one byte, sorted by name without
     * regard to case, at most MAXIMUM_PACKAGES. Anything else in the folder is ignored. A missing folder = none.
     */
    static List<String> packageNames(File directory) {
        File[] entries = directory == null ? null : directory.listFiles();
        if (entries == null) {
            return Collections.emptyList();
        }
        List<String> names = new ArrayList<String>();
        for (File entry : entries) {
            if (validName(entry.getName()) && Files.isRegularFile(entry.toPath(), LinkOption.NOFOLLOW_LINKS)
                    && entry.length() > 0) {
                names.add(entry.getName());
            }
        }
        Collections.sort(names, (a, b) -> {
            int c = a.toLowerCase(Locale.ROOT).compareTo(b.toLowerCase(Locale.ROOT));
            return c != 0 ? c : a.compareTo(b);
        });
        return names.size() > MAXIMUM_PACKAGES ? new ArrayList<String>(names.subList(0, MAXIMUM_PACKAGES)) : names;
    }

    /**
     * Returns the start request with FLAG and the directory inserted as two arguments just before CATALOG_FLAG.
     * Refused (IllegalArgumentException, nothing changed): a request without CATALOG_FLAG (no installed game data: no DLC row
     * can be added), one that already holds FLAG, a request that is not zero-terminated, or a directory that is not a plain
     * absolute path.
     */
    static byte[] insertDirectory(byte[] arguments, String directory) {
        if (arguments == null || arguments.length == 0 || arguments[arguments.length - 1] != 0) {
            throw new IllegalArgumentException("the start request does not end with a zero byte");
        }
        if (directory == null || !directory.startsWith("/") || directory.indexOf('\0') >= 0 || directory.contains("//")
                || directory.contains("/../") || directory.endsWith("/..") || directory.contains("/./") || directory.endsWith("/")) {
            throw new IllegalArgumentException("the DLC folder is not a plain absolute path");
        }
        List<String> parts = split(arguments);
        if (parts.contains(FLAG)) {
            throw new IllegalArgumentException("the start request already names a DLC folder");
        }
        int at = parts.indexOf(CATALOG_FLAG);
        if (at < 0) {
            throw new IllegalArgumentException("the start request has no installed content catalog");
        }
        parts.add(at, directory);
        parts.add(at, FLAG);
        StringBuilder joined = new StringBuilder();
        for (String part : parts) {
            joined.append(part).append('\0');
        }
        return joined.toString().getBytes(StandardCharsets.US_ASCII);
    }

    /**
     * What LaunchRequest applies: the template's request when DLC is off or the folder is empty, otherwise the request with the
     * folder inserted. A refusal from insertDirectory propagates (the launcher reports it; no run is created).
     */
    static byte[] apply(byte[] arguments, boolean enabled, File privateTraceRoot) {
        if (!enabled || privateTraceRoot == null) {
            return arguments;
        }
        File directory = new File(privateTraceRoot, DIRECTORY);
        if (packageNames(directory).isEmpty()) {
            return arguments;
        }
        return insertDirectory(arguments, directory.getAbsolutePath());
    }

    private static List<String> split(byte[] arguments) {
        List<String> parts = new ArrayList<String>();
        int start = 0;
        for (int at = 0; at < arguments.length; ++at) {
            if (arguments[at] == 0) {
                parts.add(new String(arguments, start, at - start, StandardCharsets.US_ASCII));
                start = at + 1;
            }
        }
        return parts;
    }
}
