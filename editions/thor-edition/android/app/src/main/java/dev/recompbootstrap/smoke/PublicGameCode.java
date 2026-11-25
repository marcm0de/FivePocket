package dev.recompbootstrap.smoke;

import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.*;

/** Bridges a sealed, private import to the existing checked installer and launch contract. No game bytes in the APK. */
final class PublicGameCode implements ShipBundle {
    // The portable library of the build the desktop builder makes for this app (thor-builder, 2026-11-05): linked without
    // debug sections, with the attempt's own build id, name strings removed; its loaded sections equal the attempt's library.
    // Written by local-analysis/builder/thor-rc1/make_public_config.py from the bundle. The package check binds the record to it.
    static final String PORTABLE_LIBRARY_SHA256 = "9f77c6c8e8134cb9921a1f5048d9d3826a72dc81e32ff2d323248d1e291f25cf";
    static final String CONFIG = "public/config.json", REQUEST = "public/start-request.bin";
    /**
     * One app for TU players (owner 2026-11-24): besides its 1.0 profile (CONFIG, REQUEST) the APK may carry whole other pinned
     * profiles, each a record and a start request of its own, each naming its own program (ShipConfig.titleExecutable). An imported
     * bundle's program only selects one of them; nothing about the launch comes from the bundle. Absent: a one-profile app, as before.
     */
    static final String[][] EXTRA_PROFILES = {{"public/title-update/config.json", "public/title-update/start-request.bin"}};
    private final ShipBundle apk;
    private final File filesDir;
    /** The app's own pinned profiles, the 1.0 one first. */
    private final List<Profile> profiles;
    /** The profile the installed bundle selects (the first one when none is installed). */
    final ShipConfig config;

    /** One pinned profile: its record, parsed, and the APK asset of its start request. */
    static final class Profile {
        final byte[] record;
        final ShipConfig config;
        final String request;
        Profile(byte[] record, String request) throws IOException {
            this.record = record.clone();
            this.config = ShipConfig.parse(record);
            this.request = request;
        }
    }

    PublicGameCode(ShipBundle apk, File filesDir, byte[] record) throws IOException {
        this.apk = apk;
        this.filesDir = filesDir;
        List<Profile> all = new ArrayList<Profile>();
        all.add(new Profile(record, REQUEST));
        for (String[] extra : EXTRA_PROFILES) {
            byte[] bytes;
            try { bytes = ShipFiles.readAll(apk.open(extra[0]), ShipConfig.MAX_BYTES); }
            catch (FileNotFoundException absent) { continue; }
            Profile profile = new Profile(bytes, extra[1]);
            if (profile.config.titleExecutable == null) throw new IOException("a second profile names no program of its own");
            for (Profile other : all) {
                if (profile.config.titleExecutable.equals(other.config.titleExecutable) || profile.config.runId.equals(other.config.runId)) {
                    throw new IOException("two profiles share a program or a run");
                }
            }
            all.add(profile);
        }
        this.profiles = Collections.unmodifiableList(all);
        this.config = installedConfig(InstallGuard.root(filesDir));
    }

    /** The installed bundle's profile, with the pins it runs with (effectiveRecord); the first profile when none is installed. */
    private ShipConfig installedConfig(File root) {
        Profile profile = profiles.get(installedIndex(root, configs()));
        try {
            CodeBundle bundle = manifest(root);
            List<String> older = olderPiecesIn(bundle, profile.config);
            return older.isEmpty() ? profile.config : ShipConfig.parse(effectiveRecord(profile.record, older, profile.config));
        } catch (IOException | RuntimeException none) { return profile.config; }
    }

    /** Every pinned profile's record, the 1.0 one first: what an import may select from. */
    List<ShipConfig> configs() {
        List<ShipConfig> out = new ArrayList<ShipConfig>();
        for (Profile p : profiles) out.add(p.config);
        return out;
    }

    /**
     * The profile a bundle selects: the one whose own program it names, else the first without a program of its own (the disc's).
     * Whether that profile then takes the bundle is gameProblem's and contractProblem's answer, against it alone.
     */
    static int select(CodeBundle bundle, List<ShipConfig> profiles) {
        for (int i = 0; i < profiles.size(); ++i) if (bundle.executableSha256.equals(profiles.get(i).titleExecutable)) return i;
        for (int i = 0; i < profiles.size(); ++i) if (profiles.get(i).titleExecutable == null) return i;
        return 0;
    }

    /** The profile the installed bundle selects; the first when none is installed or it cannot be read (its checks then say why). */
    static int installedIndex(File root, List<ShipConfig> profiles) {
        try { return select(manifest(root), profiles); } catch (IOException | RuntimeException none) { return 0; }
    }

    /** Where the installed bundle's update.rpf belongs (its profile's updateDirectory); the default for a developer app or on a failure. */
    static String updateDirectory(String apkPath, File filesDir) {
        try (ShipBundle bundle = open(apkPath, filesDir)) {
            return bundle instanceof PublicGameCode ? ((PublicGameCode) bundle).config.updateDirectory : ShipConfig.DEFAULT_UPDATE_DIRECTORY;
        } catch (IOException | RuntimeException e) { return ShipConfig.DEFAULT_UPDATE_DIRECTORY; }
    }

    static ShipBundle open(String path, File filesDir) throws IOException {
        ShipBundle apk = new ShipBundle.Apk(path);
        try {
            byte[] record;
            try { record = ShipFiles.readAll(apk.open(CONFIG), ShipConfig.MAX_BYTES); }
            catch (FileNotFoundException developerOrShip) { return apk; }
            return new PublicGameCode(apk, filesDir, record);
        } catch (IOException | RuntimeException e) { apk.close(); throw e; }
    }

    static boolean isPublic(String path) {
        if (path == null) return false;
        try (java.util.zip.ZipFile zip = new java.util.zip.ZipFile(path)) {
            return zip.getEntry("assets/" + CONFIG) != null;
        } catch (IOException | RuntimeException e) { return false; }
    }

    /**
     * Which game program a profile runs a bundle with (lead 2026-11-24). discExecutable: the player's play disc program (SetupCatalog).
     * A profile without a program of its own (1.0) takes the disc's program only, as before. A title-update profile takes only its
     * own program, and only when the bundle names the disc's program as its base. Everything else is refused with what is wrong; a
     * one-profile 1.0 app answers a TU bundle as before (GAME).
     */
    static String gameProblem(CodeBundle bundle, String discExecutable, ShipConfig config) {
        if (config.titleExecutable == null) return bundle.executableSha256.equals(discExecutable) ? null : PublicCodeWords.GAME;
        if (!config.titleExecutable.equals(bundle.executableSha256)) return PublicCodeWords.GAME;
        if (bundle.baseExecutableSha256 == null) return PublicCodeWords.TITLE_UPDATE_NO_BASE;
        if (!bundle.baseExecutableSha256.equals(discExecutable)) return PublicCodeWords.TITLE_UPDATE_BASE;
        return null;
    }

    /** The pinned piece that is the profile's own (title update) program, served from the bundle's title-xex entry; else null. */
    static ShipConfig.BundledData titleUpdatePiece(CodeBundle bundle, ShipConfig config) {
        if (config.titleExecutable == null || !config.titleExecutable.equals(bundle.executableSha256)) return null;
        return config.bundled(ShipDataManifest.INPUTS + config.titleExecutable + ".bin");
    }

    static String contractProblem(CodeBundle bundle, ShipConfig config) {
        if (bundle.version != 2 || !CodeBundle.PUBLIC_ABI.equals(bundle.runtimeAbi)) return PublicCodeWords.VERSION;
        ShipConfig.DataFile title = config.pinned("inputs/" + bundle.executableSha256 + ".bin");
        if (title == null) return PublicCodeWords.GAME;
        if (config.titleExecutable != null && !config.titleExecutable.equals(bundle.executableSha256)) return PublicCodeWords.GAME;
        ShipConfig.BundledData program = titleUpdatePiece(bundle, config);
        if (program != null && bundle.baseExecutableSha256 == null) return PublicCodeWords.TITLE_UPDATE_NO_BASE;
        Map<String, ShipConfig.Piece> expected = new LinkedHashMap<String, ShipConfig.Piece>();
        expected.put(CodeBundle.ROLE_LIBRARY, config.library);
        expected.put(CodeBundle.ROLE_SHADERS, config.shaderPack);
        expected.put(CodeBundle.ROLE_EXECUTABLE, new ShipConfig.Piece("", title.bytes, title.sha256));
        for (ShipConfig.BundledData p : config.bundledData) {
            if (program != null && p.path.equals(program.path)) continue;   // the title-xex entry above (route A)
            String key = p.path.equals("decoded.image.bin") ? CodeBundle.ROLE_IMAGE : "runtime/" + p.path;
            expected.put(key, new ShipConfig.Piece(p.asset, p.bytes, p.sha256));
        }
        Set<String> seen = new HashSet<String>();
        for (CodeBundle.Entry entry : bundle.entries) {
            if (CodeBundle.ROLE_UI.equals(entry.role)) continue;   // 1.2.0: the player's own launcher files, not pinned (the seal covers them)
            if (CodeBundle.placedOutside(entry.role)) continue;   // next update: the player's own TU and DLC, not pinned (the seal covers them)
            String key = CodeBundle.ROLE_RUNTIME.equals(entry.role) ? entry.name : entry.role;
            ShipConfig.Piece piece = expected.get(key);
            if (piece == null || !seen.add(key)) return PublicCodeWords.EXTRA;
            if (entry.bytes != piece.bytes || !entry.sha256.equals(piece.sha256)) {
                ShipConfig.DataFile older = key.startsWith("runtime/") ? config.older(key.substring("runtime/".length())) : null;
                if (older == null || entry.bytes != older.bytes || !entry.sha256.equals(older.sha256)) return PublicCodeWords.DIFFERENT;
            }
        }
        return seen.size() == expected.size() ? null : PublicCodeWords.COMPONENTS;
    }

    /** The pinned paths whose accepted OLDER bytes this bundle carries (ShipConfig.olderPieces); empty for a current bundle. */
    static List<String> olderPiecesIn(CodeBundle bundle, ShipConfig config) {
        List<String> out = new ArrayList<String>();
        for (CodeBundle.Entry entry : bundle.entries) {
            if (!CodeBundle.ROLE_RUNTIME.equals(entry.role) || !entry.name.startsWith("runtime/")) continue;
            ShipConfig.DataFile older = config.older(entry.name.substring("runtime/".length()));
            if (older != null && entry.bytes == older.bytes && entry.sha256.equals(older.sha256)) out.add(older.path);
        }
        return out;
    }

    /**
     * The record this bundle runs with: the profile's own, or, when the bundle carries accepted older bytes of a piece, the same record
     * with that piece's pin (data and pieces) set to those bytes, so the install, the readiness check and Play all agree with what is
     * installed (lead 2026-11-24: a 1.2.0 bundle runs as before, without the intro-movie fix). Nothing else changes.
     */
    static byte[] effectiveRecord(byte[] record, List<String> olderPaths, ShipConfig config) throws IOException {
        if (olderPaths.isEmpty()) return record;
        Object parsed;
        try { parsed = JsonLite.parse(new String(record, StandardCharsets.UTF_8)); }
        catch (JsonLite.ParseException e) { throw new IOException(PublicCodeWords.APP_RECORD); }
        @SuppressWarnings("unchecked") Map<String, Object> map = (Map<String, Object>) parsed;
        for (String list : new String[] {"data", "pieces"}) {
            for (Object o : (List<?>) map.get(list)) {
                @SuppressWarnings("unchecked") Map<String, Object> entry = (Map<String, Object>) o;
                Object path = entry.get("path");
                if (!olderPaths.contains(path)) continue;
                ShipConfig.DataFile older = config.older((String) path);
                entry.put("size", Long.valueOf(older.bytes));
                entry.put("sha256", older.sha256);
            }
        }
        // (now the pins themselves: no longer listed as accepted older bytes, which ShipConfig refuses for a pin)
        List<Object> stillOlder = new ArrayList<Object>();
        for (Object o : (List<?>) map.get("acceptedOlderPieces")) if (!olderPaths.contains(((Map<?, ?>) o).get("path"))) stillOlder.add(o);
        if (stillOlder.isEmpty()) map.remove("acceptedOlderPieces"); else map.put("acceptedOlderPieces", stillOlder);
        return (JsonLite.write(map) + "\n").getBytes(StandardCharsets.UTF_8);
    }

    /** The installed bundle is an earlier builder's that the app still accepts: the bundle screen's quiet line (PublicCodeWords.OLDER_BUILDER). */
    boolean olderBuild() {
        try {
            CodeBundle bundle = manifest(InstallGuard.root(filesDir));
            return !olderPiecesIn(bundle, profiles.get(select(bundle, configs())).config).isEmpty();
        } catch (IOException | RuntimeException none) { return false; }
    }

    static CodeBundle manifest(File root) throws IOException {
        byte[] data = InstallGuard.readSmall(InstallGuard.under(root, "bundle/manifest.json"), CodeBundleImporter.MAX_MANIFEST_BYTES);
        if (data == null) throw new IOException(PublicCodeWords.MISSING);
        try { return CodeBundle.parseManifest(new String(data, StandardCharsets.UTF_8)); }
        catch (JsonLite.ParseException | IllegalArgumentException e) { throw new IOException(PublicCodeWords.CHANGED); }
    }

    static String seal(File root) throws IOException {
        byte[] data = InstallGuard.readSmall(InstallGuard.under(root, "bundle/seal.txt"), 65);
        if (data == null || data.length != 65 || data[64] != '\n') throw new IOException(PublicCodeWords.SEAL);
        String seal = new String(data, 0, 64, StandardCharsets.US_ASCII);
        if (!SetupCatalog.isSha256(seal)) throw new IOException(PublicCodeWords.SEAL);
        return seal;
    }

    static String installedProblem(File root, ShipConfig config) {
        try {
            CodeBundle bundle = manifest(root);
            String problem = contractProblem(bundle, config);
            if (problem != null) return problem;
            seal(root);
            File files = root.getParentFile();
            for (CodeBundle.Entry entry : bundle.entries) {
                if (CodeBundle.placedOutside(entry.role)) continue;   // moved into the game's folders after the import (BundleExtras)
                File file = InstallGuard.under(root, "bundle/" + entry.name);
                if (!ShipFiles.regularFileIn(files, file) || file.length() != entry.bytes
                        || !entry.sha256.equals(ShipFiles.sha256Of(file))) return PublicCodeWords.CHANGED;
            }
            return null;
        } catch (IOException | RuntimeException e) { return PublicCodeWords.MISSING.equals(e.getMessage()) ? PublicCodeWords.MISSING
                : PublicCodeWords.SEAL.equals(e.getMessage()) ? PublicCodeWords.SEAL : PublicCodeWords.CHANGED; }
    }

    /** Only pinned destinations can be read; a bundle never supplies paths, switches or launch arguments to execute. */
    @Override public InputStream open(String asset) throws IOException {
        File root = InstallGuard.root(filesDir);
        CodeBundle bundle = manifest(root);
        Profile profile = profiles.get(select(bundle, configs()));   // the installed bundle's profile: its record, request and pins
        String problem = contractProblem(bundle, profile.config);
        if (problem != null) throw new IOException(problem);
        byte[] record = effectiveRecord(profile.record, olderPiecesIn(bundle, profile.config), profile.config);
        ShipConfig config = record == profile.record ? profile.config : ShipConfig.parse(record);   // (the pins this bundle runs with)
        seal(root);
        if (ShipConfig.ASSET.equals(asset)) return new ByteArrayInputStream(record);
        if (config.startRequest.asset.equals(asset)) return apk.open(profile.request);
        String role = asset.equals(config.library.asset) ? CodeBundle.ROLE_LIBRARY
                : asset.equals(config.shaderPack.asset) ? CodeBundle.ROLE_SHADERS : null;
        String name = null;
        for (ShipConfig.BundledData piece : config.bundledData) if (piece.asset.equals(asset)) {
            if (piece.path.equals("decoded.image.bin")) role = CodeBundle.ROLE_IMAGE;
            else if (piece.path.equals("inputs/" + bundle.executableSha256 + ".bin")) role = CodeBundle.ROLE_EXECUTABLE;   // route A: the TU program
            else name = "runtime/" + piece.path;
        }
        for (CodeBundle.Entry entry : bundle.entries) {
            if (role != null ? role.equals(entry.role) : name != null && name.equals(entry.name)) {
                File file = InstallGuard.under(root, "bundle/" + entry.name);
                if (!ShipFiles.regularFileIn(filesDir, file)) throw new IOException(PublicCodeWords.CHANGED);
                return new FileInputStream(file);
            }
        }
        throw new FileNotFoundException(PublicCodeWords.COMPONENTS);
    }

    /** The last activation's full refusal (ShipFirstRun's sentence, with paths), or null: logged by the setup screen, never shown
     *  (the screen shows PublicCodeWords.activation's plain sentence; TU27 2026-11-24 had to infer the reason on the Thor). */
    String lastActivationReason;

    String activate(ShipFirstRun.Progress progress) {
        // the profile the bundle installed now selects (an import since this object was opened may have swapped it)
        List<ShipConfig> all = configs();
        String problem = installedProblem(InstallGuard.root(filesDir), all.get(installedIndex(InstallGuard.root(filesDir), all)));
        if (problem != null) return problem;
        ShipFirstRun.Result result = ShipFirstRun.run(this, filesDir, progress, ShipFiles.DISK);
        lastActivationReason = result.ok() ? null : result.error;   // (the full reason, for the screen's log line)
        if (result.ok()) CodeBundleImporter.dropPrevious(InstallGuard.root(filesDir));   // (next update: the replaced bundle, kept until now)
        return result.ok() ? null : PublicCodeWords.activation(result.error);
    }

    static String about(File filesDir) {
        try {
            File root = InstallGuard.root(filesDir);
            CodeBundle bundle = manifest(root);
            StringBuilder text = new StringBuilder("Installed bundle seal:\n").append(seal(root));
            boolean supplied = false;
            for (CodeBundle.Entry entry : bundle.entries) supplied |= "supplied".equals(entry.provenance);
            if (supplied) text.append("\nThis bundle includes parts supplied to its builder.");
            return text.toString();
        } catch (IOException | RuntimeException e) { return PublicCodeWords.MISSING; }
    }

    @Override public void close() throws IOException { apk.close(); }
}
