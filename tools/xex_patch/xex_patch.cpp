// xex_patch: applies a title update's XEX delta patch (the default.xexp of the player's own title-update package) to
// the game executable from the player's own disc, with the pinned XenonRecomp patcher (XexPatcher::apply, its port of
// Xenia's XexModule::ApplyPatch). The result is a plain XEX2 (not encrypted, not compressed). This program holds no
// key and no game data; it prints only the patcher's result and sizes. It refuses to overwrite an existing output.
//
//   xex_patch <game.xex> <patch.xexp> <output.xex>
//
// Exit codes: 0 patched; 2 bad usage or an input that cannot be read; 3 the output cannot be written; 4 the game
// executable is not a supported XEX2; 5 the patch file is not a supported XEX delta patch; 6 the patch is made for
// another executable (XexPatcher's PatchIncompatible); 7 the patch did not apply.
#include <xex_patcher.h>

#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <iterator>
#include <vector>

namespace {

bool load(const std::filesystem::path& path, std::vector<uint8_t>& bytes) {
    std::ifstream file(path, std::ios::binary);
    if (!file) {
        return false;
    }
    bytes.assign(std::istreambuf_iterator<char>(file), std::istreambuf_iterator<char>());
    return !file.bad();
}

struct Outcome {
    const char* name;
    int code;
};

Outcome outcome(XexPatcher::Result result) {
    switch (result) {
    case XexPatcher::Result::Success: return {"patched", 0};
    case XexPatcher::Result::FileOpenFailed: return {"FileOpenFailed", 2};
    case XexPatcher::Result::FileWriteFailed: return {"FileWriteFailed", 3};
    case XexPatcher::Result::XexFileUnsupported: return {"XexFileUnsupported", 4};
    case XexPatcher::Result::XexFileInvalid: return {"XexFileInvalid", 4};
    case XexPatcher::Result::PatchFileInvalid: return {"PatchFileInvalid", 5};
    case XexPatcher::Result::PatchUnsupported: return {"PatchUnsupported", 5};
    case XexPatcher::Result::PatchIncompatible: return {"PatchIncompatible", 6};
    case XexPatcher::Result::PatchFailed: return {"PatchFailed", 7};
    }
    return {"unknown result", 7};
}

} // namespace

int main(int argc, char** argv) {
    if (argc != 4) {
        std::cerr << "Usage: xex_patch <game.xex> <patch.xexp> <output.xex>\n";
        return 2;
    }
    const std::filesystem::path basePath(argv[1]), patchPath(argv[2]), outputPath(argv[3]);
    std::vector<uint8_t> base, patch;
    if (!load(basePath, base) || !load(patchPath, patch)) {
        std::cerr << "xex_patch: an input file could not be read\n";
        return 2;
    }
    if (base.size() < 4 || base[0] != 'X' || base[1] != 'E' || base[2] != 'X' || base[3] != '2') {
        std::cerr << "xex_patch: the game executable is not an XEX2 file\n";
        return 4;
    }
    if (patch.size() < 4 || patch[0] != 'X' || patch[1] != 'E' || patch[2] != 'X' || patch[3] != '2') {
        std::cerr << "xex_patch: the patch is not an XEX2 delta patch file\n";
        return 5;
    }
    if (!outputPath.has_parent_path() || !std::filesystem::is_directory(outputPath.parent_path())) {
        std::cerr << "xex_patch: output parent directory does not exist\n";
        return 2;
    }
    if (std::filesystem::exists(outputPath)) {
        std::cerr << "xex_patch: refusing to overwrite an existing output file\n";
        return 2;
    }

    std::vector<uint8_t> patched;
    const Outcome result = outcome(XexPatcher::apply(base.data(), base.size(), patch.data(), patch.size(), patched, false));
    if (result.code != 0) {
        std::cerr << "xex_patch: " << result.name << "\n";
        return result.code;
    }

    std::ofstream output(outputPath, std::ios::binary);
    if (!output) {
        std::cerr << "xex_patch: output file could not be opened\n";
        return 3;
    }
    output.write(reinterpret_cast<const char*>(patched.data()), static_cast<std::streamsize>(patched.size()));
    output.close();
    if (!output) {
        std::cerr << "xex_patch: output file could not be written completely\n";
        return 3;
    }
    std::cout << "{\"result\": \"" << result.name << "\", \"game_bytes\": " << base.size() << ", \"patch_bytes\": " << patch.size()
              << ", \"output_bytes\": " << patched.size() << "}\n";
    return 0;
}
