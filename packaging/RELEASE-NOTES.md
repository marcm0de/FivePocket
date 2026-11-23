# FivePocket {version} — hotfix

**Play the 2013 open-world classic natively on Android and the AYN Thor, from your own Xbox 360 discs.**
FivePocket is a static recompilation with a new renderer, not an emulator. No game code, data or shaders are in any
download: the FivePocket Builder makes everything from your own discs, on your own computer.

This hotfix is for Android (FivePocket and the Thor Edition) and the Windows builders. iOS and Duo stay on 1.2.0.

## What you need to do

| You play on | What to do |
|---|---|
| **FivePocket** (single screen) | Install the {version} app over the old one. Build your package once more with the **{version} builder** and import it (Play > Import Game Discs > Install Game Code). |
| **FivePocket Thor Edition** | Install the {version} app over the old one. Your 1.2.0 package keeps working: no rebuild needed. |

**Do not uninstall first: uninstalling deletes your saves.**

## Fixed

- **Importing a new package over the one you have.** A 1.2.0 or 1.1.5 app with game code already installed refused any
  other package ("already on this device"), so after an update with new game code there was no way forward short of
  uninstalling, which deletes your saves. Now the new package is read and checked in full first and replaces the old one
  only when it checks out; your saves and settings are not touched. If anything goes wrong, the game code you had stays.

## New

- **Language** (Settings > Display > Language): the game's text and menus in English, Français, Deutsch, Italiano,
  Español (España), Português, Polski, Русский, 한국어, 中文 or Español (México), from the languages on your own discs.
  Takes effect at the next game start.
- **Export Saves** and **Import Saves** (Settings > Save And Restore): Export writes all your saves (Story, Sandbox, Online
  character) into one file you choose; Import reads such a file, shows what it holds and asks first, and keeps your
  current saves in the app before it replaces them.

## Changed

- **Frame-rate counter**: the small, clean counter is back (Settings > Frame Rate > Frame-Rate Counter, off by default), a
  plain number in the top left corner while the game runs. The old boxy counter stays gone.
- Single-screen app: its own package from the builder, and no Thor Edition naming anywhere in the app.
- Windows builders: each edition's output folder holds its app beside its package; the single-screen builder makes the
  single-screen package.

## Known issues

- As in 1.2.0 (CHANGELOG.md): the other menu sounds (moving up and down or left and right, and the error sound) are not
  there yet; the online clothes shop menu can stay stuck after buying; Deathmatches and other jobs can hang when selected;
  finishing a race can crash the game.
- Windows: Microsoft Defender can wrongly flag `XenosRecomp.exe` (inside the builder) as "Trojan:Win32/Bearfoos.A!ml" and
  quarantine it, and the build then stops at shader-fixes. It is a false positive on a program built from the open
  source. Restore it from Protection history, allow the builder's folder, and start the build again.

## Editions

| Edition | Device | How you get it |
|---|---|---|
| **FivePocket** | Android 8.0+, arm64 + Vulkan, Snapdragon 8-class or better | `FivePocket-{version}-android.zip` + `FivePocket-Builder-{version}-windows-x64.zip` |
| **FivePocket Thor Edition** | AYN Thor (dual screen) | `FivePocket-Thor-Edition-{version}-android.zip` + the Thor Edition builder (x64 or ARM64) |
| **FivePocket iOS / Duo** | iPhone / iPad / the Duo | stays on 1.2.0: the Mac builder of the 1.2.0 release |

## Before you start

- You need your own Xbox 360 discs (Disc 1 and Disc 2) as images. FivePocket never downloads game files.
- **Never share what you build** (disc images, the bundle, the .apk package you build, game files, saves, save exports).
  See LEGAL.md.
- FivePocket is free and has no affiliation with the game's publisher or developer, Microsoft, Apple, Google or AYN.

Source: `FivePocket-{version}-source.tar.xz` (GPL-3.0-or-later). Checksums: `SHA256SUMS` and the table below.
Community and help: the FivePocket Discord, https://discord.gg/mZvqCsxgxT
