package dev.recompbootstrap.smoke;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.StandardCopyOption;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;
import java.util.zip.ZipOutputStream;

/**
 * Save export and import (owner order 2026-11-22, via the lead). Pure Java, so it can go into a hotfix on its own: a player stuck
 * on an old bundle can take the saves out before clearing the app's data.
 *
 * The saves are the game's own folders under the app's files: saves/ and profile/ (Story), sandbox-mode/ (Sandbox: its CURRENT
 * file and generations g<N>, save_sandbox.h) and online-mode/ (the Online character, the same layout), and saves-sandbox/ (the
 * save sandboxes a test switch plays from, private-trace/diag-save-sandbox "reuse <name>": on such a device Story, Sandbox and
 * Online are all played from saves-sandbox/<name>/, so leaving it out would leave out what was played; lead 2026-11-22: every save
 * the player has). A player without the switch has no saves-sandbox/ and their export is the four folders above.
 *
 * The archive is a ZIP: manifest.json first (FORMAT, the app and game code it came from, every file's path, size and SHA-256),
 * then each file as "files/" + its path. Export reads every file twice (hash, then write with the hash checked again), so a file
 * that changes meanwhile fails the export instead of making a mixed archive. Import reads the whole ZIP into a staging folder,
 * checking each file against the manifest (nothing extra, nothing missing, no other path); only then can restore() swap it in.
 * Restore moves the current copy of each root the archive holds into saves-backup/<name>/ (kept in the app, never removed
 * here), then moves the staged copy into place; a failure puts back what was moved. Roots the archive does not hold are not
 * touched. Nothing here runs unless the player chose Export or Import; the caller refuses both while the game runs.
 */
final class SaveArchive {
    static final String FORMAT = "fivepocket-saves/1";
    static final String MANIFEST = "manifest.json", FILE_PREFIX = "files/";
    static final String[] ROOTS = {"saves", "profile", "sandbox-mode", "online-mode", "saves-sandbox"};
    static final String STAGING = "saves-import.part", BACKUPS = "saves-backup";
    static final int MAX_FILES = 20000, MAX_MANIFEST_BYTES = 4 << 20;
    static final long MAX_FILE_BYTES = 512L << 20, MAX_TOTAL_BYTES = 4L << 30;

    static final String TEXT_FOREIGN = "This file is not a FivePocket save export.";
    static final String TEXT_DAMAGED = "This save export is damaged: ";
    static final String TEXT_NO_SAVES = "There are no saves to export yet.";

    /** One file of the save folders. */
    static final class Item {
        final String path;
        final long size;
        final String sha256;

        Item(String path, long size, String sha256) {
            this.path = path;
            this.size = size;
            this.sha256 = sha256;
        }
    }

    /** What an export wrote, or an import read into staging. */
    static final class Result {
        final boolean ok;
        final String sentence;
        final List<Item> items;
        final Map<String, String> about;

        Result(boolean ok, String sentence, List<Item> items, Map<String, String> about) {
            this.ok = ok;
            this.sentence = sentence;
            this.items = items == null ? Collections.<Item>emptyList() : items;
            this.about = about == null ? Collections.<String, String>emptyMap() : about;
        }

        static Result failed(String sentence) {
            return new Result(false, sentence, null, null);
        }
    }

    private SaveArchive() {
    }

    // ------------------------------------------------------------------------------------------ the save folders

    /** Every regular file under the save roots (links and other kinds are left out), in a stable order. */
    static List<Item> list(File filesDir) throws IOException {
        return list(filesDir, new ArrayList<String>());
    }

    /** As list(); folders receives every folder of the save roots, the roots themselves and empty ones included (the game needs some). */
    static List<Item> list(File filesDir, List<String> folders) throws IOException {
        List<Item> items = new ArrayList<Item>();
        for (String root : ROOTS) walk(new File(filesDir, root), root, items, folders);
        // files and folders count apart, as read() checks them: a test copy's many save sandboxes (11,508 files in some 8,500 folders on
        // the Fold 2026-11-22) still export, and anything export writes, read() takes
        if (items.size() > MAX_FILES) throw new IOException("more than " + MAX_FILES + " save files");
        if (folders.size() > MAX_FILES) throw new IOException("more than " + MAX_FILES + " save folders");
        return items;
    }

    private static void walk(File at, String path, List<Item> items, List<String> folders) throws IOException {
        if (Files.isSymbolicLink(at.toPath())) return;
        if (Files.isRegularFile(at.toPath(), LinkOption.NOFOLLOW_LINKS)) {
            if (!validPath(path)) throw new IOException(UNCARRIED + path.substring(path.lastIndexOf('/') + 1));
            if (at.length() > MAX_FILE_BYTES) throw new IOException(path + " is larger than a save file can be");
            items.add(new Item(path, at.length(), hashOf(at)));
            return;
        }
        if (!Files.isDirectory(at.toPath(), LinkOption.NOFOLLOW_LINKS)) return;
        if (!validFolder(path)) throw new IOException(UNCARRIED + path.substring(path.lastIndexOf('/') + 1));
        folders.add(path);
        String[] names = at.list();
        if (names == null) return;
        java.util.Arrays.sort(names);
        for (String name : names) walk(new File(at, name), path + "/" + name, items, folders);
    }

    /** Export refuses a name Import could not carry back, before it writes anything (a player must never find out after the fact). */
    static final String UNCARRIED = "a save has a name this export cannot carry: ";

    /** A folder of the saves: a root itself, or a save path. */
    static boolean validFolder(String path) {
        for (String root : ROOTS) if (root.equals(path)) return true;
        return validPath(path);
    }

    // ------------------------------------------------------------------------------------------ export

    /**
     * Writes the archive of every save file to out. about: what the manifest says the archive came from (app, edition, game code;
     * the caller's words). Never throws; out is not closed.
     */
    static Result export(File filesDir, OutputStream out, Map<String, String> about) {
        try {
            List<String> folders = new ArrayList<String>();
            List<Item> items = list(filesDir, folders);
            if (items.isEmpty()) return Result.failed(TEXT_NO_SAVES);
            // refuse here, before a byte is written, what read() would refuse: a list or a total over its limits
            byte[] manifest = manifest(items, folders, about).getBytes(StandardCharsets.UTF_8);
            if (manifest.length > MAX_MANIFEST_BYTES) throw new IOException("the list of save files is too long");
            long sum = 0;
            for (Item item : items) sum += item.size;
            if (sum > MAX_TOTAL_BYTES) throw new IOException("the saves are larger than " + megabytes(MAX_TOTAL_BYTES));
            ZipOutputStream zip = new ZipOutputStream(out);
            zip.putNextEntry(new ZipEntry(MANIFEST));
            zip.write(manifest);
            zip.closeEntry();
            byte[] buffer = new byte[1 << 16];
            for (Item item : items) {
                zip.putNextEntry(new ZipEntry(FILE_PREFIX + item.path));
                MessageDigest md = VerifiedExtractor.sha256();
                long copied = 0;
                try (InputStream in = new FileInputStream(new File(filesDir, item.path))) {
                    for (int n; (n = in.read(buffer)) > 0; ) {
                        md.update(buffer, 0, n);
                        zip.write(buffer, 0, n);
                        copied += n;
                    }
                }
                zip.closeEntry();
                if (copied != item.size || !VerifiedExtractor.hex(md.digest()).equals(item.sha256)) {
                    return Result.failed(item.path + " changed while it was exported. Close the game and try again.");
                }
            }
            zip.finish();
            zip.flush();
            long bytes = 0;
            for (Item item : items) bytes += item.size;
            return new Result(true, items.size() + " save files exported (" + megabytes(bytes) + ").", items, about);
        } catch (IOException | RuntimeException e) {
            return Result.failed("The saves could not be exported (" + reason(e) + ").");
        }
    }

    static String manifest(List<Item> items, Map<String, String> about) {
        return manifest(items, new ArrayList<String>(), about);
    }

    static String manifest(List<Item> items, List<String> folders, Map<String, String> about) {
        Map<String, Object> map = new LinkedHashMap<String, Object>();
        map.put("format", FORMAT);
        for (Map.Entry<String, String> e : about.entrySet()) map.put(e.getKey(), e.getValue());
        List<Object> files = new ArrayList<Object>();
        for (Item item : items) {
            Map<String, Object> f = new LinkedHashMap<String, Object>();
            f.put("path", item.path);
            f.put("size", Long.valueOf(item.size));
            f.put("sha256", item.sha256);
            files.add(f);
        }
        map.put("files", files);
        map.put("folders", new ArrayList<Object>(folders));
        return JsonLite.write(map) + "\n";
    }

    // ------------------------------------------------------------------------------------------ import

    /** A save path: a root of ROOTS, then parts of [A-Za-z0-9._-] that are not "." or ".."; no empty part. */
    static boolean validPath(String path) {
        if (path == null || path.isEmpty() || path.length() > 512) return false;
        String[] parts = path.split("/", -1);
        boolean root = false;
        for (String r : ROOTS) root |= r.equals(parts[0]);
        if (!root || parts.length < 2) return false;
        for (String part : parts) {
            if (part.isEmpty() || part.equals(".") || part.equals("..") || part.length() > 255) return false;
            for (int i = 0; i < part.length(); ++i) {
                char c = part.charAt(i);
                if (!((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '.' || c == '_' || c == '-')) return false;
            }
        }
        return true;
    }

    /**
     * Reads an archive into filesDir/STAGING (made anew), checking every file against the manifest. Result.ok: staging holds
     * exactly the manifest's files; else staging is removed. Never throws; zip is not closed.
     */
    static Result read(File filesDir, InputStream zipStream) {
        return read(filesDir, zipStream, true);
    }

    /**
     * Checks an archive without writing anything (the file Export just wrote, read back: Export says "exported" only when Import
     * would take it). Never throws; zip is not closed.
     */
    static Result check(InputStream zipStream) {
        return read(null, zipStream, false);
    }

    private static Result read(File filesDir, InputStream zipStream, boolean store) {
        File staging = store ? new File(filesDir, STAGING) : null;
        try {
            if (store) removeTree(staging);
            ZipInputStream zip = new ZipInputStream(zipStream);
            ZipEntry first = zip.getNextEntry();
            if (first == null || !MANIFEST.equals(first.getName())) return failedRead(staging, TEXT_FOREIGN);
            byte[] manifestBytes = readUpTo(zip, MAX_MANIFEST_BYTES);
            if (manifestBytes == null) return failedRead(staging, TEXT_FOREIGN);
            Object parsed;
            try {
                parsed = JsonLite.parse(new String(manifestBytes, StandardCharsets.UTF_8), MAX_MANIFEST_BYTES);   // not JsonLite's 1 MiB
            } catch (JsonLite.ParseException e) {
                return failedRead(staging, TEXT_FOREIGN);
            }
            if (!(parsed instanceof Map) || !FORMAT.equals(((Map<?, ?>) parsed).get("format"))) return failedRead(staging, TEXT_FOREIGN);
            Map<?, ?> map = (Map<?, ?>) parsed;
            Map<String, String> about = new LinkedHashMap<String, String>();
            for (Map.Entry<?, ?> e : map.entrySet()) {
                if (e.getKey() instanceof String && e.getValue() instanceof String && !"format".equals(e.getKey())) about.put((String) e.getKey(), (String) e.getValue());
            }
            Object list = map.get("files");
            if (!(list instanceof List) || ((List<?>) list).isEmpty() || ((List<?>) list).size() > MAX_FILES) return failedRead(staging, TEXT_DAMAGED + "its list of files is unusable.");
            Map<String, Item> expected = new LinkedHashMap<String, Item>();
            long total = 0;
            for (Object o : (List<?>) list) {
                if (!(o instanceof Map)) return failedRead(staging, TEXT_DAMAGED + "its list of files is unusable.");
                Map<?, ?> f = (Map<?, ?>) o;
                Object path = f.get("path"), size = f.get("size"), sha = f.get("sha256");
                if (!(path instanceof String) || !validPath((String) path)) return failedRead(staging, TEXT_DAMAGED + "it names a file outside the saves.");
                if (!(size instanceof Long || size instanceof Integer) || !(sha instanceof String) || !SetupCatalog.isSha256((String) sha)) {
                    return failedRead(staging, TEXT_DAMAGED + "its list of files is unusable.");
                }
                long bytes = ((Number) size).longValue();
                if (bytes < 0 || bytes > MAX_FILE_BYTES) return failedRead(staging, TEXT_DAMAGED + path + " has an impossible size.");
                total += bytes;
                if (total > MAX_TOTAL_BYTES) return failedRead(staging, TEXT_DAMAGED + "it is larger than saves can be.");
                if (expected.put((String) path, new Item((String) path, bytes, (String) sha)) != null) return failedRead(staging, TEXT_DAMAGED + path + " is listed twice.");
            }
            Set<String> seen = new HashSet<String>();
            byte[] buffer = new byte[1 << 16];
            for (ZipEntry entry; (entry = zip.getNextEntry()) != null; ) {
                String name = entry.getName();
                String path = name.startsWith(FILE_PREFIX) ? name.substring(FILE_PREFIX.length()) : null;
                Item item = path == null ? null : expected.get(path);
                if (item == null || entry.isDirectory()) return failedRead(staging, TEXT_DAMAGED + "it holds " + name + ", which its list does not name.");
                if (!seen.add(path)) return failedRead(staging, TEXT_DAMAGED + path + " appears twice.");
                MessageDigest md = VerifiedExtractor.sha256();
                long copied = 0;
                OutputStream out = null;
                try {
                    if (store) {
                        File target = new File(staging, path);
                        File folder = target.getParentFile();
                        if (!folder.isDirectory() && !folder.mkdirs()) throw new StorageProblem("the staging folder could not be made");
                        try {
                            out = new FileOutputStream(target);
                        } catch (IOException e) {
                            throw new StorageProblem(reason(e));
                        }
                    }
                    for (int n; (n = zip.read(buffer)) > 0; ) {
                        copied += n;
                        if (copied > item.size) return failedRead(staging, TEXT_DAMAGED + path + " is longer than its list says.");
                        md.update(buffer, 0, n);
                        if (out != null) {
                            try {
                                out.write(buffer, 0, n);
                            } catch (IOException e) {
                                throw new StorageProblem(reason(e));   // (the device is out of space: not the file's fault)
                            }
                        }
                    }
                } finally {
                    if (out != null) out.close();
                }
                if (copied != item.size || !VerifiedExtractor.hex(md.digest()).equals(item.sha256)) {
                    return failedRead(staging, TEXT_DAMAGED + path + " does not match its list.");
                }
            }
            for (String path : expected.keySet()) if (!seen.contains(path)) return failedRead(staging, TEXT_DAMAGED + path + " is missing.");
            Object folderList = map.get("folders");
            if (folderList != null) {
                if (!(folderList instanceof List) || ((List<?>) folderList).size() > MAX_FILES) return failedRead(staging, TEXT_DAMAGED + "its list of folders is unusable.");
                for (Object o : (List<?>) folderList) {
                    if (!(o instanceof String) || !validFolder((String) o)) return failedRead(staging, TEXT_DAMAGED + "it names a folder outside the saves.");
                    if (store) {
                        File folder = new File(staging, (String) o);
                        if (!folder.isDirectory() && !folder.mkdirs()) throw new StorageProblem("a folder could not be made");
                    }
                }
            }
            return new Result(true, null, new ArrayList<Item>(expected.values()), about);
        } catch (StorageProblem e) {
            return failedRead(staging, TEXT_NO_ROOM + reason(e) + ").");
        } catch (IOException | RuntimeException e) {
            return failedRead(staging, TEXT_DAMAGED + reason(e) + ".");
        }
    }

    static final String TEXT_NO_ROOM = "The saves could not be read into the app: there may be too little space (";

    /** Writing the staging copy failed: the device, not the file (as opposed to a file that does not check out). */
    private static final class StorageProblem extends IOException {
        StorageProblem(String message) {
            super(message);
        }
    }

    private static Result failedRead(File staging, String sentence) {
        if (staging == null) return Result.failed(sentence);
        try {
            removeTree(staging);
        } catch (IOException ignored) {
            // removed at the next import
        }
        return Result.failed(sentence);
    }

    /** The question's text (three lines): what the archive holds, kind by kind, where it came from, and that the current saves are kept. */
    static String summary(Result read) {
        Map<String, int[]> counts = new LinkedHashMap<String, int[]>();
        for (String root : ROOTS) counts.put(root, new int[1]);
        long bytes = 0;
        for (Item item : read.items) {
            counts.get(item.path.substring(0, item.path.indexOf('/')))[0]++;
            bytes += item.size;
        }
        StringBuilder text = new StringBuilder();
        text.append(kind("Story", counts.get("saves")[0] + counts.get("profile")[0])).append(" \u00b7 ")
                .append(kind("Sandbox", counts.get("sandbox-mode")[0])).append(" \u00b7 ")
                .append(kind("Online character", counts.get("online-mode")[0]));
        if (counts.get("saves-sandbox")[0] > 0) text.append(" \u00b7 Test save sandboxes: ").append(counts.get("saves-sandbox")[0]).append(" files");
        text.append('\n');
        text.append(read.items.size()).append(" files, ").append(megabytes(bytes));
        String created = read.about.get("created"), app = read.about.get("app");
        if (created != null && !created.isEmpty()) text.append(", exported ").append(created);
        if (app != null && !app.isEmpty()) text.append(" by FivePocket ").append(app);
        text.append(".\nYour current saves of these kinds are kept in the app first.");
        return text.toString();
    }

    private static String kind(String what, int files) {
        return what + ": " + (files == 0 ? "not in this export (yours stay)" : files + (files == 1 ? " file" : " files"));
    }

    // ------------------------------------------------------------------------------------------ restore

    /**
     * Puts the staged saves in place: each root the archive holds is first moved, as it is now, to BACKUPS/backupName/, then the
     * staged root is moved in. A failure moves back what was moved. Returns null when done, else the sentence. Never throws.
     */
    static String restore(File filesDir, String backupName) {
        File staging = new File(filesDir, STAGING);
        File backup = new File(new File(filesDir, BACKUPS), backupName);
        List<String[]> done = new ArrayList<String[]>();   // {root, "backup" | "staged"} in order, for the way back
        try {
            if (!staging.isDirectory()) return "There is nothing to import: choose the file again.";
            if (backup.exists()) return "A backup with that name exists already: try again.";
            boolean story = new File(staging, "saves").isDirectory() || new File(staging, "profile").isDirectory();
            for (String root : ROOTS) {
                File staged = new File(staging, root), current = new File(filesDir, root);
                boolean storyRoot = root.equals("saves") || root.equals("profile");
                if (!staged.isDirectory() && !(storyRoot && story && current.exists())) continue;   // (saves/ and profile/ are one unit: Story)
                if (!staged.isDirectory()) {   // Story comes in without this half: the current half goes to the backup too, nothing replaces it
                    if (Files.isSymbolicLink(current.toPath())) throw new IOException(root + " is a link");
                    if (!backup.isDirectory() && !backup.mkdirs()) throw new IOException("the backup folder could not be made");
                    Files.move(current.toPath(), new File(backup, root).toPath(), StandardCopyOption.ATOMIC_MOVE);
                    done.add(new String[] {root, "backup"});
                    continue;
                }
                if (Files.isSymbolicLink(current.toPath())) throw new IOException(root + " is a link");
                if (current.exists()) {
                    if (!backup.isDirectory() && !backup.mkdirs()) throw new IOException("the backup folder could not be made");
                    Files.move(current.toPath(), new File(backup, root).toPath(), StandardCopyOption.ATOMIC_MOVE);
                    done.add(new String[] {root, "backup"});
                }
                Files.move(staged.toPath(), current.toPath(), StandardCopyOption.ATOMIC_MOVE);
                done.add(new String[] {root, "staged"});
            }
            removeTree(staging);
            return null;
        } catch (IOException | RuntimeException e) {
            for (int i = done.size() - 1; i >= 0; --i) {   // the way back, newest step first
                String root = done.get(i)[0];
                try {
                    if ("staged".equals(done.get(i)[1])) {
                        Files.move(new File(filesDir, root).toPath(), new File(staging, root).toPath(), StandardCopyOption.ATOMIC_MOVE);
                    } else {
                        Files.move(new File(backup, root).toPath(), new File(filesDir, root).toPath(), StandardCopyOption.ATOMIC_MOVE);
                    }
                } catch (IOException | RuntimeException ignored) {
                    // the backup folder still holds it: nothing is lost
                }
            }
            return "The saves could not be replaced (" + reason(e) + "); yours are as they were.";
        }
    }

    /** Removes the staging folder of an import that was not confirmed. */
    static void discardStaging(File filesDir) {
        try {
            removeTree(new File(filesDir, STAGING));
        } catch (IOException ignored) {
            // removed at the next import
        }
    }

    // ------------------------------------------------------------------------------------------ helpers

    private static void removeTree(File tree) throws IOException {
        if (!Files.exists(tree.toPath(), LinkOption.NOFOLLOW_LINKS)) return;
        if (Files.isDirectory(tree.toPath(), LinkOption.NOFOLLOW_LINKS)) {
            String[] names = tree.list();
            if (names != null) for (String name : names) removeTree(new File(tree, name));
        }
        Files.delete(tree.toPath());
    }

    private static byte[] readUpTo(InputStream in, int max) throws IOException {
        java.io.ByteArrayOutputStream out = new java.io.ByteArrayOutputStream();
        byte[] buffer = new byte[1 << 16];
        for (int n; (n = in.read(buffer)) > 0; ) {
            out.write(buffer, 0, n);
            if (out.size() > max) return null;
        }
        return out.toByteArray();
    }

    static String hashOf(File f) throws IOException {
        MessageDigest md = VerifiedExtractor.sha256();
        byte[] buffer = new byte[1 << 16];
        try (InputStream in = new FileInputStream(f)) {
            for (int n; (n = in.read(buffer)) > 0; ) md.update(buffer, 0, n);
        }
        return VerifiedExtractor.hex(md.digest());
    }

    private static String megabytes(long bytes) {
        return bytes < 1048576 ? (bytes + 1023) / 1024 + " KB" : String.format(java.util.Locale.ROOT, "%.1f MB", bytes / 1048576.0);
    }

    /** Words for the player: the exception's message, unless it names a path of the device (then only its kind). */
    private static String reason(Exception e) {
        String m = e.getMessage();
        return m == null || m.indexOf('/') >= 0 ? e.getClass().getSimpleName() : m;
    }
}
