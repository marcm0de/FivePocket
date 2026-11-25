// private_process.h - which process the loader serves (lead 2026-11-24: the app's own package name, so a test id such as
// dev.recompbootstrap.smoke.thor.tu27q works; the public app behaves as before).
//
// The loader runs the game only in this app's game window process, "<package>:private_trace" (AndroidManifest android:process).
// The package is read from the loader library's own install path, which Android names "<package>-<random>":
//   /data/app/~~XXXX==/<package>-YYYY==/lib/arm64/librecomp_private_debug.so        (extracted native libraries)
//   /data/app/~~XXXX==/<package>-YYYY==/base.apk!/lib/arm64-v8a/librecomp_...so      (libraries kept in the APK)
//   /data/app/<package>-1/lib/arm64/librecomp_private_debug.so                       (older Android)
// A package name holds only letters, digits, '_' and '.', so the first '-' ends it. When the path does not have that shape the
// caller keeps the edition's fixed name, exactly as before. Pure: host-tested (tests/private_process_test.cpp).
#pragma once
#include <cstring>
#include <string>

namespace recomp::private_process {

inline bool packageName(const std::string& name) {
    if (name.empty() || name.front() == '.' || name.back() == '.' || name.find('.') == std::string::npos) return false;
    for (char c : name) {
        const bool ok = (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '_' || c == '.';
        if (!ok) return false;
    }
    return true;
}

// The package of the app a library file belongs to, or "" when the path is not an app's install path.
inline std::string packageFromLibraryPath(const std::string& path) {
    if (path.rfind("/data/app/", 0) != 0) return {};
    std::size_t cut = path.find("/base.apk!/");
    if (cut == std::string::npos) cut = path.find("/lib/");
    if (cut == std::string::npos || cut == 0) return {};
    const std::size_t start = path.rfind('/', cut - 1);
    if (start == std::string::npos) return {};
    const std::string folder = path.substr(start + 1, cut - start - 1);
    const std::size_t dash = folder.find('-');
    if (dash == std::string::npos) return {};
    const std::string package = folder.substr(0, dash);
    return packageName(package) ? package : std::string{};
}

// The game window process's name for that package; fallback (the edition's fixed name) when the package is unknown.
inline std::string expectedProcess(const std::string& package, const char* fallback) {
    return package.empty() ? std::string(fallback) : package + ":private_trace";
}

}  // namespace recomp::private_process
