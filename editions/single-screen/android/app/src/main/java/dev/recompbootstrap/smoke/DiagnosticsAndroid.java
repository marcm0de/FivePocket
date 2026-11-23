package dev.recompbootstrap.smoke;

import android.app.Activity;
import android.app.ActivityManager;
import android.app.ApplicationExitInfo;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.Intent;
import android.content.pm.FeatureInfo;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.os.Build;
import android.os.StatFs;
import java.io.BufferedReader;
import java.io.File;
import java.io.IOException;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.List;
import java.util.TimeZone;

/**
 * Update 1 "Copy diagnostics": the Android side of Diagnostics. text() gathers the facts and builds the report; call it OFF the UI
 * thread, and only when the player used the row (nothing here runs otherwise, so the game's frames never pay for it). deliver() puts
 * the text on the clipboard and, when asked (the launcher; never the game window, which leaving would end), opens the share sheet.
 * Nothing is sent by the app itself.
 */
final class DiagnosticsAndroid {
    private DiagnosticsAndroid() {}

    /** The report (redacted). Never throws: a fact Android does not give stays empty. */
    static String text(Activity activity) {
        Diagnostics.Facts f = new Diagnostics.Facts();
        File filesDir = activity.getFilesDir();
        f.nowMs = System.currentTimeMillis();
        f.packageName = activity.getPackageName();
        try { f.app = String.valueOf(activity.getApplicationInfo().loadLabel(activity.getPackageManager())); } catch (RuntimeException e) { f.app = "?"; }
        f.edition = "single screen";   // (1.2.1: the single-screen app names no other edition)
        f.version = version(activity);
        try { f.gameCode = gameCode(activity, filesDir); } catch (RuntimeException | LinkageError e) { f.gameCode = "unreadable (" + e.getClass().getSimpleName() + ")"; }
        f.librarySha = librarySha(filesDir);
        f.device = Build.MANUFACTURER + " " + Build.MODEL + " (" + Build.DEVICE + ", board " + Build.BOARD + ")";
        f.android = Build.VERSION.RELEASE + " (API " + Build.VERSION.SDK_INT + ", security patch " + Build.VERSION.SECURITY_PATCH + ")";
        f.soc = (Build.VERSION.SDK_INT >= 31 ? Api31.soc() + ", " : "") + "hardware " + Build.HARDWARE + ", ABI " + String.join("/", Build.SUPPORTED_ABIS);
        f.gpu = gpu(activity);
        try { StatFs s = new StatFs(filesDir.getPath()); f.freeBytes = s.getAvailableBytes(); f.totalBytes = s.getTotalBytes(); } catch (RuntimeException e) { /* unknown */ }
        f.starts = Diagnostics.starts(filesDir, Diagnostics.STARTS);
        if (Build.VERSION.SDK_INT >= 30) {
            try { f.exits = Api30.exits(activity); } catch (RuntimeException | LinkageError e) { /* none */ }
        }
        f.log = Diagnostics.logTail(logcat(), Diagnostics.LOG_LINES);
        try { f.gpuCheck = GpuSupport.graphicsCheck(GpuCheck.line(filesDir), filesDir); } catch (RuntimeException | LinkageError e) { /* no block */ }   // (bugfix-stability)
        f.secrets = Diagnostics.secrets(filesDir);
        return Diagnostics.report(f, TimeZone.getDefault());
    }

    /** On the UI thread: the clipboard (and the share sheet when share). The result sentence for the row. */
    static String deliver(Activity activity, String text, boolean share) {
        try {
            ClipboardManager clip = (ClipboardManager) activity.getSystemService(Context.CLIPBOARD_SERVICE);
            if (clip == null) return Diagnostics.failed("no clipboard");
            clip.setPrimaryClip(ClipData.newPlainText(Diagnostics.TITLE, text));
            if (share) {
                Intent send = new Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_SUBJECT, Diagnostics.TITLE).putExtra(Intent.EXTRA_TEXT, text);
                activity.startActivity(Intent.createChooser(send, Diagnostics.TITLE));
            }
            return Diagnostics.DONE;
        } catch (RuntimeException e) {
            return Diagnostics.failed(e.getClass().getSimpleName());
        }
    }

    @SuppressWarnings("deprecation")   // versionCode is the field before API 28
    private static String version(Activity a) {
        try {
            PackageInfo info = a.getPackageManager().getPackageInfo(a.getPackageName(), 0);
            long code = Build.VERSION.SDK_INT >= 28 ? info.getLongVersionCode() : info.versionCode;
            return info.versionName + " (version code " + code + ")";
        } catch (PackageManager.NameNotFoundException impossible) { return "unknown"; }
    }

    /** The same game-code words as the About tab's Game Build section. */
    private static String gameCode(Activity a, File filesDir) {
        String apk = a.getApplicationInfo().sourceDir;
        ShipReady ship = ShipReady.remembered(apk, filesDir, ShipVerify.WHY_ABOUT);
        InstallState install = InstallState.detect(filesDir, ship);
        String text = ShipApplication.playerApp() ? ShipAdvice.aboutBuild(install, ship) : AboutScreen.installBlock(install, TimeZone.getDefault());
        if (ShipApplication.publicApp()) text = PublicGameCode.about(filesDir) + "\n" + text;
        return text;
    }

    /** The bundle's game-library SHA-256 (the public app), else the version settings/qualified-code.txt names (other builds). */
    private static String librarySha(File filesDir) {
        try {
            for (CodeBundle.Entry e : PublicGameCode.manifest(InstallGuard.root(filesDir)).entries)
                if (CodeBundle.ROLE_LIBRARY.equals(e.role)) return e.sha256;
        } catch (IOException | RuntimeException | LinkageError none) { /* not a bundle install */ }
        try {
            File q = new File(filesDir, "settings/qualified-code.txt");
            if (q.isFile() && q.length() < 256) return new String(Files.readAllBytes(q.toPath()), StandardCharsets.US_ASCII).trim();
        } catch (IOException none) { /* unknown */ }
        return "";
    }

    private static String gpu(Activity a) {
        StringBuilder g = new StringBuilder();
        try {
            ActivityManager am = (ActivityManager) a.getSystemService(Context.ACTIVITY_SERVICE);
            if (am != null) g.append("OpenGL ES ").append(am.getDeviceConfigurationInfo().getGlEsVersion());
        } catch (RuntimeException e) { /* unknown */ }
        try {
            for (FeatureInfo fi : a.getPackageManager().getSystemAvailableFeatures())
                if (PackageManager.FEATURE_VULKAN_HARDWARE_VERSION.equals(fi.name))
                    g.append(", Vulkan ").append(fi.version >> 22).append('.').append((fi.version >> 12) & 0x3ff);
        } catch (RuntimeException e) { /* unknown */ }
        String egl = prop("ro.hardware.egl"), vk = prop("ro.hardware.vulkan");
        if (!egl.isEmpty() || !vk.isEmpty()) g.append(", driver egl=").append(egl).append(" vulkan=").append(vk);
        return g.toString();
    }

    private static String prop(String name) {
        List<String> out = run(new String[]{"getprop", name}, 4);
        return out.isEmpty() ? "" : out.get(0).trim();
    }

    /** The app's own log (Android shows an app its own lines only), the last 3000 lines; filtered and cut by Diagnostics.logTail. */
    private static List<String> logcat() {
        return run(new String[]{"logcat", "-d", "-v", "threadtime", "-t", "3000"}, 4000);
    }

    private static List<String> run(String[] command, int maxLines) {
        List<String> lines = new ArrayList<>();
        Process p = null;
        try {
            p = new ProcessBuilder(command).redirectErrorStream(true).start();
            try (BufferedReader r = new BufferedReader(new InputStreamReader(p.getInputStream(), StandardCharsets.UTF_8))) {
                String l;
                while ((l = r.readLine()) != null && lines.size() < maxLines) lines.add(l);
            }
        } catch (IOException | RuntimeException e) { /* nothing */ }
        finally { if (p != null) p.destroy(); }
        return lines;
    }

    @android.annotation.TargetApi(30)
    private static final class Api30 {
        static List<Diagnostics.Exit> exits(Activity a) {
            List<Diagnostics.Exit> out = new ArrayList<>();
            ActivityManager am = (ActivityManager) a.getSystemService(Context.ACTIVITY_SERVICE);
            if (am == null) return out;
            String game = a.getPackageName() + ScreenStack.GAME_PROCESS_SUFFIX;
            for (ApplicationExitInfo info : am.getHistoricalProcessExitReasons(a.getPackageName(), 0, 16))
                if (game.equals(info.getProcessName()))
                    out.add(new Diagnostics.Exit(info.getTimestamp(), info.getReason(), info.getStatus(), info.getDescription()));
            return out;
        }
    }

    @android.annotation.TargetApi(31)
    private static final class Api31 {
        static String soc() { return Build.SOC_MANUFACTURER + " " + Build.SOC_MODEL; }
    }
}
