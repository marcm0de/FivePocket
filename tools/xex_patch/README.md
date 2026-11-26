# Title-update patcher (host tool)

`xex_patch` applies a title update's XEX delta patch (the `default.xexp` inside the
player's own title-update package) to the game executable from the player's own play
disc, with the pinned XenonRecomp patcher (`XexPatcher::apply`, its port of Xenia's
`XexModule::ApplyPatch`). The output is a plain XEX2. The program holds no key and no
game data; it prints only the patcher's result and sizes, and refuses to overwrite an
existing output.

```sh
cmake -S tools/xex_patch -B out/host-tools/xex-patch -G Ninja -DCMAKE_BUILD_TYPE=Release
cmake --build out/host-tools/xex-patch --target xex_patch

out/host-tools/xex-patch/xex_patch game.xex default.xexp private-analysis/patched.xex
```

It builds like `tools/xex_image`: against the generator's tree beside it
(`tools/XenonRecomp`, or the `sources/XenonRecomp` copy a builder package ships).
Exit codes: 0 patched; 2 usage or unreadable input; 3 output not written; 4 the game
executable is not a supported XEX2; 5 not a supported delta patch; 6 the patch is for
another executable (`PatchIncompatible`); 7 the patch did not apply.

The builder runs it as a profile stage (`disc_builder.py --title-update`; see
`tools/own_disc/title_update.py`). Outputs are game-derived and stay outside Git.
