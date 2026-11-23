package dev.recompbootstrap.smoke;

import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.*;

/** Bridges a sealed, private import to the existing checked installer and launch contract. No game bytes in the APK. */
final class PublicGameCode implements ShipBundle {
    // The portable library of the build the desktop builder makes for this app (thor-builder, 2026-11-05): linked without
    // debug sections, with the attempt's own build id, name strings removed; its loaded sections equal the attempt's library.
    // Written by local-analysis/builder/thor-rc1/make_public_config.py from the bundle. The package check binds the record to it.
    static final String PORTABLE_LIBRARY_SHA256 = "f26f86ceae9b59d152e3d51809ff4adaf04cf649fcf939ed51b24e99035beeaf";
    static final String CONFIG = "public/config.json", REQUEST = "public/start-request.bin";
    private final ShipBundle apk;
    private final File filesDir;
    private final byte[] record;
    final ShipConfig config;

    PublicGameCode(ShipBundle apk, File filesDir, byte[] record) throws IOException {
        this.apk = apk;
        this.filesDir = filesDir;
        this.record = record.clone();
        this.config = ShipConfig.parse(record);
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

    static String contractProblem(CodeBundle bundle, ShipConfig config) {
        if (bundle.version != 2 || !CodeBundle.PUBLIC_ABI.equals(bundle.runtimeAbi)) return PublicCodeWords.VERSION;
        ShipConfig.DataFile title = config.pinned("inputs/" + bundle.executableSha256 + ".bin");
        if (title == null) return PublicCodeWords.GAME;
        Map<String, ShipConfig.Piece> expected = new LinkedHashMap<String, ShipConfig.Piece>();
        expected.put(CodeBundle.ROLE_LIBRARY, config.library);
        expected.put(CodeBundle.ROLE_SHADERS, config.shaderPack);
        expected.put(CodeBundle.ROLE_EXECUTABLE, new ShipConfig.Piece("", title.bytes, title.sha256));
        for (ShipConfig.BundledData p : config.bundledData) {
            String key = p.path.equals("decoded.image.bin") ? CodeBundle.ROLE_IMAGE : "runtime/" + p.path;
            expected.put(key, new ShipConfig.Piece(p.asset, p.bytes, p.sha256));
        }
        Set<String> seen = new HashSet<String>();
        for (CodeBundle.Entry entry : bundle.entries) {
            if (CodeBundle.ROLE_UI.equals(entry.role)) continue;   // 1.2.0: the player's own launcher files, not pinned (the seal covers them)
            String key = CodeBundle.ROLE_RUNTIME.equals(entry.role) ? entry.name : entry.role;
            ShipConfig.Piece piece = expected.get(key);
            if (piece == null || !seen.add(key)) return PublicCodeWords.EXTRA;
            if (entry.bytes != piece.bytes || !entry.sha256.equals(piece.sha256)) return PublicCodeWords.DIFFERENT;
        }
        return seen.size() == expected.size() ? null : PublicCodeWords.COMPONENTS;
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
        String problem = contractProblem(bundle, config);
        if (problem != null) throw new IOException(problem);
        seal(root);
        if (ShipConfig.ASSET.equals(asset)) return new ByteArrayInputStream(record);
        if (config.startRequest.asset.equals(asset)) return apk.open(REQUEST);
        String role = asset.equals(config.library.asset) ? CodeBundle.ROLE_LIBRARY
                : asset.equals(config.shaderPack.asset) ? CodeBundle.ROLE_SHADERS : null;
        String name = null;
        for (ShipConfig.BundledData piece : config.bundledData) if (piece.asset.equals(asset)) {
            if (piece.path.equals("decoded.image.bin")) role = CodeBundle.ROLE_IMAGE;
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

    String activate(ShipFirstRun.Progress progress) {
        String problem = installedProblem(InstallGuard.root(filesDir), config);
        if (problem != null) return problem;
        ShipFirstRun.Result result = ShipFirstRun.run(this, filesDir, progress, ShipFiles.DISK);
        if (result.ok()) CodeBundleImporter.dropPrevious(InstallGuard.root(filesDir));   // (1.2.1 replace flow: the replaced bundle, kept until now)
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
