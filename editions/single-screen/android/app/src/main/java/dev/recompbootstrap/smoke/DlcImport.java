package dev.recompbootstrap.smoke;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.StandardCopyOption;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/**
 * "Add DLC" (next update): copies the player's OWN Xbox 360 DLC package files from a folder they chose into
 * private-trace/inputs/dlc-stfs (DlcPacks.DIRECTORY), where the launcher finds them. We never ship DLC. Pure Java: the Android
 * side lists the chosen folder's documents (Storage Access Framework) and hands them here; every rule is here and host tested.
 *
 * Rules: only documents whose name is a package name (DlcPacks.validName) and whose size is at least one byte; everything else in
 * the folder (an index text, pictures) is ignored. At most DlcPacks.MAXIMUM_PACKAGES. A file already in the folder under the same
 * name in any case (slot) with the same size and the same SHA-256 is skipped; with other bytes it is replaced. Each copy goes to NAME.tmp (owner read/write only), is hashed while it is written, read back
 * and hashed again, and only then renamed over NAME. The space the new files need (plus a margin) is checked before anything is
 * written. Nothing is ever deleted except this import's own failed .tmp file; the player's source is only read.
 */
final class DlcImport {
    /** A document of the chosen folder. */
    interface Doc {
        String name();
        /** Its size in bytes, or -1 when the provider does not say. */
        long size();
        InputStream open() throws IOException;
    }

    static final long MARGIN_BYTES = 64L * 1024 * 1024;
    static final String TEXT_NONE = "No DLC package files in that folder.";
    static final String TEXT_SPACE_PREFIX = "Not enough space: the packs need ";
    static final String TEXT_CANCELLED = "Cancelled.";

    static final class Result {
        final boolean ok;
        final int added, skipped;
        final long bytes;
        final String sentence;

        Result(boolean ok, int added, int skipped, long bytes, String sentence) {
            this.ok = ok;
            this.added = added;
            this.skipped = skipped;
            this.bytes = bytes;
            this.sentence = sentence;
        }
    }

    private DlcImport() {
    }

    /** The documents that are package files, in the folder's order, at most MAXIMUM_PACKAGES. */
    static List<Doc> packages(List<Doc> docs) {
        List<Doc> out = new ArrayList<Doc>();
        for (Doc d : docs) {
            if (d != null && DlcPacks.validName(d.name()) && d.size() != 0 && out.size() < DlcPacks.MAXIMUM_PACKAGES) {
                out.add(d);
            }
        }
        return out;
    }

    /**
     * Copies the package documents into privateTraceRoot/inputs/dlc-stfs. freeBytes: the space left on the app's storage
     * (File.getUsableSpace). progress may be null. Never throws.
     */
    static Result run(File privateTraceRoot, List<Doc> docs, long freeBytes, ModsImport.Progress progress) {
        List<Doc> packs = packages(docs);
        if (packs.isEmpty()) {
            return new Result(false, 0, 0, 0, TEXT_NONE);
        }
        File directory = new File(privateTraceRoot, DlcPacks.DIRECTORY);
        long needed = 0;
        for (Doc d : packs) {
            File target = slot(directory, d.name());
            if (!(target.isFile() && d.size() >= 0 && target.length() == d.size())) {
                needed += Math.max(0, d.size());
            }
        }
        if (needed + MARGIN_BYTES > freeBytes) {
            return new Result(false, 0, 0, 0, TEXT_SPACE_PREFIX + megabytes(needed + MARGIN_BYTES) + ", " + megabytes(freeBytes) + " free.");
        }
        if (!directory.isDirectory() && !directory.mkdirs()) {
            return new Result(false, 0, 0, 0, "The DLC folder could not be made.");
        }
        int added = 0, skipped = 0;
        long bytes = 0;
        for (Doc d : packs) {
            if (progress != null) {
                if (progress.cancelled()) {
                    return new Result(false, added, skipped, bytes, TEXT_CANCELLED + " " + summary(added, skipped));
                }
                progress.update(added + skipped, bytes, d.name());
            }
            File target = slot(directory, d.name());
            File temporary = new File(directory, d.name() + ".tmp");
            try {
                if (Files.isSymbolicLink(target.toPath()) || (target.exists() && !Files.isRegularFile(target.toPath(), LinkOption.NOFOLLOW_LINKS))) {
                    return new Result(false, added, skipped, bytes, d.name() + ": the DLC folder holds something else under that name.");
                }
                String sourceHash;
                long copied;
                try (InputStream in = d.open(); OutputStream out = new FileOutputStream(temporary)) {
                    restrict(temporary);
                    MessageDigest md = sha256();
                    byte[] buffer = new byte[1 << 16];
                    copied = 0;
                    for (int n; (n = in.read(buffer)) > 0; ) {
                        md.update(buffer, 0, n);
                        out.write(buffer, 0, n);
                        copied += n;
                        if (progress != null && progress.cancelled()) {
                            break;
                        }
                    }
                    sourceHash = hex(md.digest());
                }
                if (progress != null && progress.cancelled()) {
                    temporary.delete();
                    return new Result(false, added, skipped, bytes, TEXT_CANCELLED + " " + summary(added, skipped));
                }
                if (copied == 0 || (d.size() >= 0 && copied != d.size())) {
                    temporary.delete();
                    return new Result(false, added, skipped, bytes, d.name() + ": the copy has " + copied + " bytes, the folder said " + d.size() + ".");
                }
                if (target.isFile() && target.length() == copied && sourceHash.equals(hashOf(target))) {
                    temporary.delete();   // (the same package is already there: skipped)
                    ++skipped;
                    continue;
                }
                if (!sourceHash.equals(hashOf(temporary))) {
                    temporary.delete();
                    return new Result(false, added, skipped, bytes, d.name() + ": the copy does not read back as the original.");
                }
                Files.move(temporary.toPath(), target.toPath(), StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
                ++added;
                bytes += copied;
            } catch (IOException | RuntimeException failed) {
                temporary.delete();
                return new Result(false, added, skipped, bytes, d.name() + ": " + failed.getClass().getSimpleName()
                        + (failed.getMessage() == null ? "" : " (" + failed.getMessage() + ")") + ". " + summary(added, skipped));
            }
        }
        return new Result(true, added, skipped, bytes, summary(added, skipped) + " " + DlcPacks.packageNames(directory).size() + " in the DLC folder.");
    }

    /**
     * Where a pack goes: the file already in the folder under the same name in any case (the game reads names without regard to
     * case, so two would collide; the bundle's packs arrive in upper case, BundleExtras), else the name as the player's file has it.
     */
    static File slot(File directory, String name) {
        return new File(directory, DlcPacks.slotName(directory.list(), name));
    }

    static String summary(int added, int skipped) {
        return added + (added == 1 ? " pack added" : " packs added") + (skipped > 0 ? ", " + skipped + " already there." : ".");
    }

    private static String megabytes(long bytes) {
        return String.format(Locale.ROOT, "%.0f MB", bytes / 1048576.0);
    }

    private static void restrict(File f) {
        f.setReadable(false, false);
        f.setWritable(false, false);
        f.setReadable(true, true);
        f.setWritable(true, true);
    }

    static String hashOf(File f) throws IOException {
        MessageDigest md = sha256();
        try (InputStream in = new FileInputStream(f)) {
            byte[] buffer = new byte[1 << 16];
            for (int n; (n = in.read(buffer)) > 0; ) {
                md.update(buffer, 0, n);
            }
        }
        return hex(md.digest());
    }

    private static MessageDigest sha256() {
        try {
            return MessageDigest.getInstance("SHA-256");
        } catch (NoSuchAlgorithmException impossible) {
            throw new IllegalStateException(impossible);
        }
    }

    private static String hex(byte[] b) {
        StringBuilder s = new StringBuilder(b.length * 2);
        for (byte x : b) {
            s.append(Character.forDigit((x >> 4) & 15, 16)).append(Character.forDigit(x & 15, 16));
        }
        return s.toString();
    }
}
