import java.io.File
import java.security.MessageDigest
plugins {
    id("com.android.application")
}

// -PshipAssets=<absolute folder>: the staged game code for the "ship" build type. The folder holds ship/config.json
// and the files it names; tools/ship_stage.py makes it (BUILD.md has the commands). assembleShip refuses to run
// without it; the other build types never look at it.
val shipAssets: String? = providers.gradleProperty("shipAssets").orNull
// Turnip in the PUBLIC build (lead 2026-11-24): ON since both Fold boots passed (default 11241026 on SM8475, off switch 11241031; 0 crashes).
// The public APK carries the Turnip loader code, the driver and its licences (src/turnip/); the loader picks Turnip only on the Adreno 730
// (diag-turnip-off forces Qualcomm). -PturnipPublic=false builds the public APK without it.
val turnipPublic: Boolean = providers.gradleProperty("turnipPublic").map { it != "false" }.getOrElse(true)

android {
    namespace = "dev.recompbootstrap.smoke"
    compileSdk = 35
    buildToolsVersion = "35.0.0"
    ndkVersion = "27.2.12479018"

    defaultConfig {
        // Single-screen QoL edition (2026-10-24): its own application id, so it installs beside the normal app and never replaces it.
        // The Java package and namespace stay dev.recompbootstrap.smoke (the JNI names follow the package, not the id).
        applicationId = "dev.recompbootstrap.smoke.qol"
        minSdk = 26
        targetSdk = 35
        // Stays 1 on purpose: an app that is not debuggable cannot be downgraded without an uninstall, and an
        // uninstall removes the game data and the saves. With the same version code the debug build can still be
        // installed over the ship build (and the other way round) with "adb install -r", data kept.
        versionCode = 6
        versionName = "1.3.0"

        ndk {
            abiFilters += "arm64-v8a"
        }

        externalNativeBuild {
            cmake {
                arguments += listOf("-DANDROID_STL=c++_shared")
                cppFlags += listOf("-std=c++17", "-Wall", "-Wextra", "-Wpedantic")
            }
        }
    }

    buildTypes {
        debug {
            // The loader (librecomp_private_debug.so) is a normal CMake target since ship week.
            // Turnip default-on path (lead 2026-11-24, test copies only): the loader picks Turnip on the Adreno 730 (CMakeLists.txt,
            // RECOMP_TURNIP); the driver ships in src/debug/jniLibs. Release, public and ship builds never get either.
            externalNativeBuild { cmake { arguments += "-DRECOMP_TURNIP=ON" } }
        }
        release {
            isMinifyEnabled = false
            // 2026-11-22: the launcher icons (owner art) ship byte for byte, as the public raster check pins them; aapt2's PNG
            // crunching would re-encode them. The public and ship builds start from this one.
            isCrunchPngs = false
            ndk {
                debugSymbolLevel = "SYMBOL_TABLE"
            }
        }
        // The build that is handed out: not debuggable, game code inside (see shipAssets above), and signed with the
        // key the installed app carries today (the debug keystore, certificate SHA-256 f0ab38fa...37b0), so that it
        // installs over that app without an uninstall. BUILD.md says what a release key would change.
        // Public APK: only our loader/menu and text records. Never inherits the ship asset directory.
        create("public") {
            initWith(getByName("release"))
            isDebuggable = false
            isJniDebuggable = false
            // Unsigned by default: what is published is signed with a key of the owner's choosing. For test installs
            // -PpublicTestSigned makes Gradle sign it with the key the installed app carries today (the debug keystore,
            // as the ship build), so it installs over that app; nobody types a key or a password for that.
            signingConfig = if (project.hasProperty("publicTestSigned")) signingConfigs.getByName("debug") else null
            matchingFallbacks += "release"
            externalNativeBuild {
                cmake {
                    arguments += "-DCMAKE_BUILD_TYPE=Debug"
                    if (turnipPublic) arguments += "-DRECOMP_TURNIP=ON"   // (see turnipPublic above)
                }
            }
        }
        create("ship") {
            initWith(getByName("release"))
            isDebuggable = false
            isJniDebuggable = false
            signingConfig = signingConfigs.getByName("debug")
            matchingFallbacks += "release"
            externalNativeBuild {
                cmake {
                    // The qualified loader is a Debug build (-O0). Shipping the same code generation keeps the
                    // loader's machine code equal to the one every qualified session ran with; an optimised
                    // loader is a separate, measured change.
                    arguments += "-DCMAKE_BUILD_TYPE=Debug"
                    // The loader's three log lines about opening the game library (CMakeLists.txt). Remove this line to
                    // ship the loader without them; ship_checks.sh apk then fails its line about them, on purpose.
                    arguments += "-DRECOMP_LOADER_OPEN_LOG=ON"
                }
            }
        }
    }

    sourceSets {
        getByName("public") {
            assets.srcDir("src/public/assets")
            if (turnipPublic) {   // (see turnipPublic above)
                jniLibs.srcDir("src/turnip/jniLibs")
                assets.srcDir("src/turnip/assets")
            }
        }
        // The game window, its launcher and its gamepad input live in src/debug/java for historical reasons (they
        // were debug-only until ship week). They are compiled into every build type now; the folder keeps its name
        // because test scripts and LauncherContractTest read it by path. The debug source set itself adds no Java.
        getByName("main") {
            java.srcDir("src/debug/java")
        }
        getByName("debug") {
            java.setSrcDirs(emptyList<String>())
            jniLibs.srcDir("src/turnip/jniLibs")   // Turnip default-on path: the packaged driver (debug test copies)
        }
        getByName("ship") {
            if (shipAssets != null) {
                assets.srcDir(shipAssets)
            }
        }
    }

    externalNativeBuild {
        cmake {
            path = file("src/main/cpp/CMakeLists.txt")
            version = "3.22.1"
        }
    }

    // Turnip default-on path: the packaged driver (src/turnip/jniLibs: debug builds, and the public one with -PturnipPublic) ships byte for byte as measured (out-net
    // 880bd041...), not stripped.
    packaging {
        jniLibs {
            keepDebugSymbols += "**/libvulkan_freedreno.so"
        }
    }

    buildFeatures {
        buildConfig = false
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

// Turnip default-on path (debug only): adrenotools loads its hook libraries and the driver from nativeLibraryDir, so they must be real
// files there (legacy packaging extracts them at install). Release, public and ship builds keep the default packaging.
androidComponents {
    onVariants(selector().withBuildType("debug")) { variant ->
        variant.packaging.jniLibs.useLegacyPackaging.set(true)
    }
    if (turnipPublic) onVariants(selector().withBuildType("public")) { variant ->
        variant.packaging.jniLibs.useLegacyPackaging.set(true)
    }
}

// A ship APK without game code would install and then have nothing to start: refuse to build one.
tasks.matching { it.name == "preShipBuild" }.configureEach {
    val staged = shipAssets
    doFirst {
        if (staged == null || !File(staged, "ship/config.json").isFile) {
            throw GradleException("The ship build needs -PshipAssets=<absolute folder made by ship_stage.py>;"
                    + " that folder must hold ship/config.json. Given: " + (staged ?: "nothing"))
        }
    }
}

// Source-set isolation is checked before packaging, including accidental additions to main/assets.
tasks.matching { it.name == "prePublicBuild" }.configureEach {
    // The folders are resolved here, not inside doFirst: a task action that calls the script's own file() cannot be
    // kept by Gradle's configuration cache, and the build then ends as failed although every task ran (2026-10-08).
    val source = file("src/public/assets")
    val main = file("src/main/assets")
    val prebuilt = listOf(file("src/main/jniLibs"), file("src/public/jniLibs"))
    // Turnip (-PturnipPublic only): the Mesa 26.3.1 driver as measured (out-net, with the project's ir3 fmulz patch) and its licences.
    val turnipEnabled = turnipPublic
    val turnipDir = file("src/turnip")
    val approvedTurnip = mapOf(
        "jniLibs/arm64-v8a/libvulkan_freedreno.so" to "880bd04102754137175cdf61c1729f612ec62ec678ae7b4b2939156013b96c97",
        "assets/licenses/Mesa-license.txt" to "0d1a0472ecc81830e75c20d59b0ea02841e3db21255e0ebad97ab682c54d6615",
        "assets/licenses/Mesa-MIT.txt" to "323c587d0ccf10e376f8bf9a7f31fb4ca6078105194b42e0b1e0ee2bc9bde71f",
        "assets/licenses/libadrenotools-BSD-2-Clause.txt" to "7ea796e199e46093c2f5c391697bce5abeacd02b7216aa0a84e5839afb5584f3")
    // (thor-builder 2026-11-05, as in the Thor public copy) The owner's builds may carry game art and sound in the debug source
    // set (src/debug/assets/owner/, side-menu/alert-restart.png): the public app holds no raster image and no audio at all, in its
    // assets or its resources, and no asset folder of the public variant lies under src/debug/.
    val resources = listOf(file("src/main/res"), file("src/public/res"), file("src/main/assets"), file("src/public/assets"))
    val raster = Regex("(?i).*\\.(png|jpe?g|webp|gif|bmp|m4a|ogg|mp3|wav|aac)$")
    val approvedMedia = mapOf("owner/trainer-icon.png" to "21db69ba8d8a8521dcd95a9dcc1441026c0d041c549ec8fee38e8069e07bc7eb",
        "owner/trainer-open.m4a" to "ac27aa7487f92bcaeb7bf52de920c724ccf51cbeaf8ccc13bf4b84ecffd3eb16",
        // the owner's approved alert art (2026-11-06): his capture with the button glyph blacked out, and the game-layout restart notice
        "owner/alert-screen.png" to "1b801a2cb4d47aab70315f36234adb07401042b28ee09d990b6d064d431e170d",
        "side-menu/alert-restart.png" to "b02d4330cd4ea0fab4fdc7bf118fcab5eb08cf8bf6578b1a8c25c73384c67a1c",
        // 1.2.0 (owner): his own art for the Verify Game Data and Import Game Discs tiles (update-1/menu-revamp/owner-art, 704 px)
        "ui-own/tile-verify-data.png" to "438f02666d59af51e0143505b7c14b5f92f57320a6159a1d0b6e058bacc71bf1",
        "ui-own/tile-import-discs.png" to "d8a86a9ed4b82c58c1b68db2652d659cc8cf4757126268289bcc827495187cfc")
    // 2026-11-22 owner decision (the app icon follows the builder's logo choice): the owner's own FivePocket logos of this edition
    // (android-*), scaled for the launcher (local-analysis/gui-builder/logo-full/logos), byte for byte in src/main/res.
    val mainRes = file("src/main/res")
    val approvedIcons = mapOf("mipmap-xxxhdpi/ic_launcher_fg_golden_sunset.png" to "bd687a5326d56745ab9a40e9695cb29fb8b96eb4e47280f0fb791167b8989b61",
        "mipmap-xxxhdpi/ic_launcher_fg_peach_rose.png" to "7a02849e5bcc79490b3672a7a28e7942656da382699550a4045522d75efd71ea",
        "mipmap-xxxhdpi/ic_launcher_fg_pink_blue_dusk.png" to "f68f8c4c5b7f9666b7fec9ec449f1006cd3ada71a5b2ef0428d3e1534b72a05e")
    fun sha256(f: File): String = MessageDigest.getInstance("SHA-256").digest(f.readBytes()).joinToString("") { b -> "%02x".format(b) }
    val publicAssetDirs = (android.sourceSets.getByName("main").assets.srcDirs + android.sourceSets.getByName("public").assets.srcDirs)
        .map { it.invariantSeparatorsPath }
    doFirst {
        // Update 1 (owner 2026-11-06): the TRAINER button's picture and its open sound, and the approved alert art, ship byte for byte
        // in src/public/assets/{owner,side-menu}/. Any other image or audio, or a changed copy of those four, still stops the build.
        val images = resources.flatMap { folder -> folder.walkTopDown().filter { it.isFile && raster.matches(it.name) }.toList() }
            .filter { f -> !(f.startsWith(source) && approvedMedia[f.relativeTo(source).invariantSeparatorsPath] == sha256(f)) }
            .filter { f -> !(f.startsWith(mainRes) && approvedIcons[f.relativeTo(mainRes).invariantSeparatorsPath] == sha256(f)) }
        if (images.isNotEmpty()) throw GradleException("The public app cannot package raster images or audio (possible game art or sound): " + images.joinToString { it.name })
        val fromDebug = publicAssetDirs.filter { it.contains("/src/debug/") }
        if (fromDebug.isNotEmpty()) throw GradleException("The public app cannot take assets from the debug source set (game art): " + fromDebug)
        val found = source.walkTopDown().filter { it.isFile }.map { it.relativeTo(source).invariantSeparatorsPath }.toSet()
        // 2026-11-24 (owner: one app for TU players): an optional second pinned profile, both of its files or neither (PublicGameCode.EXTRA_PROFILES)
        val titleUpdateProfile = setOf("public/title-update/config.json", "public/title-update/start-request.bin")
        val expected = setOf("public/config.json", "public/start-request.bin") + approvedMedia.keys +
            (if (found.containsAll(titleUpdateProfile)) titleUpdateProfile else emptySet())
        if (found != expected) throw GradleException("The public app needs exactly its metadata assets (one or two pinned profiles) and the approved art and sound files.")
        val shared = main.walkTopDown().filter { it.isFile }.map { it.relativeTo(main).invariantSeparatorsPath }.toSet()
        // menu-redesign 2026-10-25 (the owner: "implement it with Nunito Sans"): the menus' typeface and its OFL licence, reviewed.
        // 2026-11-05 (owner-approved): the GPLv3 text the app itself is under (the repository's COPYING, byte for byte).
        if (shared != setOf("licenses/AMD-FidelityFX-FSR1-MIT.txt", "fonts/NunitoSans-VF.ttf", "licenses/NunitoSans-OFL.txt",
                        "licenses/GPL-3.0.txt"))
            throw GradleException("An unreviewed shared asset would enter the public app.")
        if (prebuilt.any { folder -> folder.walkTopDown().any { it.isFile } })
            throw GradleException("The public app cannot package prebuilt game libraries.")
        // Turnip (-PturnipPublic): exactly the measured driver and its three licence files, byte for byte.
        if (turnipEnabled) {
            val turnipFound = turnipDir.walkTopDown().filter { it.isFile }.associate { it.relativeTo(turnipDir).invariantSeparatorsPath to sha256(it) }
            if (turnipFound != approvedTurnip)
                throw GradleException("The public app's Turnip files are not the approved driver and licences: " + turnipFound.keys.sorted())
        }
    }
}
