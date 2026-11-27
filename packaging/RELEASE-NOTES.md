# FivePocket {version}

**Play the 2013 open-world classic natively on Android and the AYN Thor, from your own Xbox 360 discs.**
FivePocket is a static recompilation with a new renderer, not an emulator. No game code, data or shaders are in any
download: the FivePocket Builder makes everything from your own discs, on your own computer.

This release is for Android (FivePocket and the Thor Edition) and the Windows builders. iOS and Duo follow in a later
1.3.x release and stay on 1.2.0 until then.

## Updated release (27 November 2026)

The first 1.3.0 downloads were replaced. **Download the apps and the builder again** and install the app over the old one.

- **Title Update 27 now builds on Windows.** The first 1.3.0 builders stopped every title-update build before it started
  ("needs the update disc (not given)"), and their code generator also lacked five vector instructions the title update uses.
  Both are fixed; a title-update build now runs to the end on Windows.
- **Unsaved settings**: leaving Settings with changes you have not saved now asks with the game's own alert:
  A saves, X discards, B goes back.
- **Menu sounds when moving through the menus**: the moves (up, down, left, right) now play the game's own tick, recorded
  from your game, as the other menu sounds do.

## What you need to do

| You play on | What to do |
|---|---|
| **FivePocket** (single screen) | Install the 1.3.0 app over the old one. Build your package with the **1.3.0 builder** and import it (Play > Import Game Discs > Install Game Code). |
| **FivePocket Thor Edition** | Install the 1.3.0 app over the old one. Build your package with the **1.3.0 Thor Edition builder** and import it. |

A new package replaces the old one only after it has been checked in full; your saves and settings are not touched.
**Do not uninstall first: uninstalling deletes your saves.**

## New

- **Title Update 27 and Online, in the same app.** Give the builder your own title-update file (and, if you have them,
  your own DLC packs) and it makes a title-update package as well; the app runs either package. Online keeps your
  character, rank, cash, purchases, look and stats on your own device, across restarts. Online unlocks once you have a
  Story save (or import one in Settings > Save And Restore).
- **Turnip graphics driver** on Adreno 730 phones (Galaxy Z Fold4 / Flip4 class), on by default: measured about 4-7 %
  more frames per second on a busy street. Other phones keep their own driver. (FivePocket single-screen app only.)
- **App icon**: choose one of three colourways in the builder, or later in Settings.
- **Menu music**: the game's own launcher theme, made by the builder from your discs, plays in the launcher.
- **Single-screen app**: its own game code (map tap on the minimap, phone tracking) instead of the Thor Edition's.

## Fixed

- Copying a disc or importing a large package could get the app closed by Android for using too much memory.
- After an interrupted "Preparing menu sounds" step, every later start could go back to that step.
- Online: cash could be lost or reset when the game saved during loading.
- Shop and LS Customs fixes, shader-cache stutter fix, and the audit fixes from the 1.2.x line, now in one merged game
  library for both editions.
- The intro movie fix (in packages made with the 1.3.0 builder).

## Known issues

- On a fresh install, "Preparing menu sounds" can wait up to about 4 minutes once (also in 1.2.x).
- As in 1.2.x (CHANGELOG.md): the online clothes shop menu can stay stuck after buying;
  Deathmatches and other jobs can hang when selected; finishing a race can crash the game.
- Windows: Microsoft Defender can wrongly flag `XenosRecomp.exe` (inside the builder) as "Trojan:Win32/Bearfoos.A!ml".
  It is a false positive on a program built from the open source. Restore it from Protection history, allow the
  builder's folder, and start the build again.

## Editions

| Edition | Device | How you get it |
|---|---|---|
| **FivePocket** | Android 8.0+, arm64 + Vulkan, Snapdragon 8-class or better | `FivePocket-{version}-android.zip` + `FivePocket-Builder-{version}-windows-x64.zip` |
| **FivePocket Thor Edition** | AYN Thor (dual screen) | `FivePocket-Thor-Edition-{version}-android.zip` + the Thor Edition builder (x64 or ARM64) |
| **FivePocket iOS / Duo** | iPhone / iPad / the Duo | stays on 1.2.0 until the iOS/Duo 1.3.x release |

## Before you start

- You need your own Xbox 360 discs (Disc 1 and Disc 2) as images. FivePocket never downloads game files.
- **Never share what you build** (disc images, the bundle, the .apk package you build, game files, saves, save exports).
  See LEGAL.md.
- FivePocket is free and has no affiliation with the game's publisher or developer, Microsoft, Apple, Google or AYN.

Source: `FivePocket-{version}-source.tar.xz` (GPL-3.0-or-later). Checksums: `SHA256SUMS` and the table below.
Community and help: the FivePocket Discord, https://discord.gg/mZvqCsxgxT
