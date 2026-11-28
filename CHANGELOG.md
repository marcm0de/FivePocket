# Changelog

One version number covers every edition and every download of a release (the app, the builder for each computer and
the source archive). Format: [Keep a Changelog](https://keepachangelog.com/); versions follow
[Semantic Versioning](https://semver.org/).

## [1.3.0] - unreleased

Android (single-screen and Thor Edition) and the Windows builders; iOS and Duo follow in a later 1.3.x release.
**Everyone**: install the 1.3.0 app over the old one, build your package with the 1.3.0 builder and import it (saves are kept).

### Added
- Title Update 27 and Online in the same app: the builder also makes a title-update package from your own title-update
  file (and your own DLC packs, if any); Online keeps character, rank, cash, purchases, look and stats across restarts.
- Turnip graphics driver on Adreno 730 phones, on by default (single-screen app; about 4-7 % more fps measured).
- App icon choice (three colourways), launcher menu music made from your discs.
- Single-screen app built from its own game code (minimap tap, phone tracking).

### Fixed
- Updated release (2026-11-27): Windows builders build Title Update 27 from both the builder window and CLI (the check no
  longer refuses it, the window shows the optional Title Update and DLC pickers, and the code generator carries patch 0020);
  Windows Job Objects clean up child build processes on exit/cancel; large-cluster exFAT drives (> 64 KB clusters) are
  refused early with NTFS guidance; unsaved settings ask with the game's alert (A save, X discard, B back); menu moves
  play the game's tick.
- Disc copy / large package import no longer gets the app closed by Android's low-memory killer.
- An interrupted "Preparing menu sounds" step no longer repeats on every start.
- Online cash no longer lost or reset when the game saves during loading.
- Shop / LS Customs, shader-cache stutter and the 1.2.x audit fixes, merged into one game library; intro movie fix.

### Known issues
- Fresh install: "Preparing menu sounds" can wait up to about 4 minutes once.

## [1.2.1] - 2026-11-22

Android hotfix (single-screen and Thor Edition) and the Windows builders. **Single-screen players**: install the 1.2.1 app
over the old one, build your package once more with the 1.2.1 builder and import it. **Thor Edition players**: install the
1.2.1 app over the old one; your 1.2.0 package keeps working, no rebuild needed.

### Fixed
- **Importing a new package over the one you have**: a 1.2.0 or 1.1.5 app with game code already installed refused any
  other package ("already on this device"), so after an update with new game code there was no way forward short of
  uninstalling, which deletes your saves. Now the new package is read and checked in full first and replaces the old one
  only when it checks out; your saves and settings are not touched. If anything goes wrong, the game code you had stays.

### Added
- **Language** (Settings > Display > Language): the game's text and menus in English, Français, Deutsch, Italiano,
  Español (España), Português, Polski, Русский, 한국어, 中文 or Español (México), from the languages on your own discs.
  Takes effect at the next game start.
- **Export Saves** and **Import Saves** (Settings > Save And Restore): Export writes all your saves (Story, Sandbox, Online
  character) into one file you choose; Import reads such a file, shows what it holds and asks first, and keeps your
  current saves in the app before it replaces them.

### Changed
- **Frame-rate counter**: the small, clean counter is back (Settings > Frame Rate > Frame-Rate Counter, off by default), a
  plain number in the top left corner while the game runs. The old boxy counter stays gone.
- Single-screen app: its own package from the builder, and no Thor Edition naming anywhere in the app.
- Windows builders: each edition's output folder holds its app beside its package; the single-screen builder makes the
  single-screen package.

## [1.2.0] - 2026-11-17

Android (single-screen and Thor Edition), the Windows builders and the Mac builder for iOS / Duo (new menus; Online
next update). The new features need game code made
with the 1.2.0 builder: rebuild your package from your discs.

### Added
- **New menus**: the launcher is drawn like the game's own Enhanced menus. The Play tab shows its choices as tiles (Story
  Mode, Sandbox, Online, Verify Game Data, Import Game Discs) over the loading-screen art from your discs; Settings,
  Controller and About keep the same tab strip on every screen size.
- **Verify Game Data** and **Import Game Discs** on the Play tab: Verify looks at every game file on the device again,
  disc by disc; Import opens the setup page (game code and both discs).
- **Locked until set up**: on a fresh install Story Mode, Sandbox and Online show a padlock and say "Set up the game to
  unlock." until setup is complete.
- **Setup pages** in the same frame as the menus, with the same wording on every step.
- **The game's own fonts**: once your game code is imported, every menu of the app (launcher, settings, setup pages,
  dialogs, the in-game side menu) uses the game's fonts from your discs. Before that, the app's own font.
- **Menu sounds**: the launcher plays the game's Select and Back sounds, made by the builder from your discs.
- **Online** on the Play tab when the game code has it (Thor Edition and single-screen).
- **Unsupported GPU**: About > Unsupported GPU can let the game try to start on a graphics chip below its minimum (off by
  default).

### Changed
- Builder (Windows x64 and ARM64): makes the menu fonts, the controller glyphs, the tile pictures and the Select / Back
  sounds from your discs and puts them into the package.
- Game library v1922.

### Fixed
- A leftover developer frame-rate readout could show up in the corner of the game. It no longer appears.
- Touch controls (single-screen app): the left stick no longer stays on screen after you lift your finger, open the side
  menu or switch controllers.
- Tile captions always fit inside their tiles.

### Known issues
- The other menu sounds (moving up and down or left and right, and the error sound) are not there yet: the game's one-time
  recording of them does not finish on a fresh install. The menus stay silent for those.

## [1.1.5] - unreleased

The first update. The new features need game code made with this version's builder: rebuild your package (Android) or
your app (iOS, Duo) from your discs with the 1.1.5 builder. With older game code, the new rows say "Not available in this
version."

### Added
- **Sandbox** (Thor Edition): a second choice on the Play tab, next to Story Mode. Free roam on its own save, copied from
  your story saves the first time; your story saves are never changed. Story missions are held back and the trainer is
  always on. Sandbox needs at least one story save. To switch between Story Mode and Sandbox, close the game first.
  The single-screen app has the same Sandbox; it was tested on the Thor.
- **GTA Online's world, offline, in Sandbox** (Thor Edition): the side menu's Online tab takes you into the online mode's
  world. Everything runs on the device; nothing connects to any online service. Tested on the Thor: free roam, the race
  tutorial, the phone and its apps (including Snapmatic), and the game's own Interaction Menu by holding Select. What you
  buy (clothes, haircuts, Ammu-Nation, cars) is still there after a restart.
- **Switch Character** in Sandbox (Thor Edition, experimental): play as Michael, Franklin or Trevor, outdoors, right where
  you stand.
- **Sandbox** (iOS): the same Play tab choice; it plays on its own copy of your story saves and never changes them.
- **Vibration** (Off / Light / Full): the game's rumble. Thor Edition: on the device, Settings > Controls > Vibration.
  iOS: side menu > Settings > Controls > Vibration.
- **Performance presets** (iOS): Battery (30 fps cap) and Max (60 fps cap) set the frame rate cap and render resolution
  together. A preset never changes the World rows.
- **Mods** (iOS): a warning when the game stopped last time with mods on, and Start Once Without Mods; add a mod as a
  folder or a zip file from the device.
- **Interaction Menu button** (FivePocket, touch controls): in the online world, a button in the top row of the touch
  controls opens the game's Interaction Menu with one tap. Checked on the Fold4.

### Changed
- Thor Edition: **Hold Select Opens Menu** is now Off by default, so holding Select in the online world opens the game's own
  Interaction Menu. The side menu still opens with its touch gesture; turn the setting back on in Settings > Controls.
- The Play tab's first item is called Story Mode wherever Sandbox is offered.
- Android: the apps now ask for the vibration permission. Android grants it at install time and shows no prompt.
- Android: the apps report the release's version number, 1.1.5 (About page and Android's app info).

### Fixed
- Explosions: fireballs, smoke and most fire now appear (they never drew before). Checked on the Thor and the Fold4.
- Samsung phones whose picture froze while the sound went on (reported on Exynos / Xclipse phones): when the graphics
  driver stops answering, the game now switches to its backup drawing path instead of freezing the picture. Tested on the
  Thor by forcing that stop; not yet confirmed on an affected Samsung phone, so a log from one helps.
- Holes in the clouds on phones whose GPU lacks one depth feature (Mali, Xclipse): the Thor showed the holes with that
  feature switched off, and the new fallback removed them. The fallback only acts on phones without the feature.
- Ocean water near the beach could break up on GPUs that round texture reads slightly low: the two ocean shaders no longer
  read exactly on pixel edges. Tested on the Thor with that rounding forced; not yet tested on an affected phone.
- Controllers: the right stick turns the camera on pads whose sticks the app read from the wrong axes (the camera did not
  turn, or turned on its own). Checked on the Thor and the Fold4.
- FivePocket (single screen): camera swipes on the touch screen turn the view directly and evenly. Checked on the Fold4.
- Thor Edition, tested on the AYN Thor (the single-screen app uses the same game code, not re-tested with this build):
  - Sandbox online world: barber, clothes, Ammu-Nation, cash and bought-car purchases now survive a restart.
  - Los Santos Customs: a change bought for your own car is saved when you leave the shop and is still there after a restart.
  - Sandbox online world: the character starts with the weapons and ammo the game saved, not a full set that replaced them.
  - Connecting or disconnecting headphones no longer stops the game; the sound moves to the new output (tested with a
    simulated route change; a real Bluetooth change is not yet tested on this build).
  - After an error stop the app shows its stop message instead of crashing a few seconds later.

### Known issues
- Clothes shop (online world): after buying, backing out can leave the shop menu stuck. Use the trainer's teleport to get
  out.
- Los Santos Customs: after you leave, the car may not drive. Get out and back in, or use the trainer.
- Deathmatches and other jobs can hang when you select them.
- The Interaction Menu's header picture can look smeared.
- Finishing a race can crash the game.

### Upgrade note
- Android: install the new app over the old one. Do not uninstall first, because uninstalling deletes your saves.
- Android: rebuild your package with the 1.1.5 builder; the 1.1.5 app accepts only the 1.1.5 package.
- iOS and Duo: re-sign with the same bundle identifier, as for every update. Your imported files and saves stay.

## [1.1.0-alpha.1] - 2026-11-06

First public release. In this release the Android apps still report their own app versions: FivePocket shows
1.0.2-alpha-qol and FivePocket Thor Edition shows 1.0.3-alpha-thor (About page and Android's app info). Both are the apps
of release 1.1.0-alpha.1; the next update gives them the release's version number.

### Added
- Four editions, one version:
  - **FivePocket** (Android 8.0+, arm64, Vulkan; tested on a Galaxy Z Fold4) and **FivePocket Thor Edition** (AYN
    Thor, dual screen): apps with no game code inside; the app imports the package the builder makes from your discs.
  - **FivePocket iOS** (iOS / iPadOS 17.4 or later; tested on an iPad mini with A17 Pro, 8 GB) and **FivePocket Duo**
    (the Duo, iPhone19,4; tested on iOS 27.2): the Mac builder makes the app from your discs, you sign it with your
    own Apple ID, and the app imports the two disc images on the device (Files, Finder or a USB drive; about
    2 minutes on the iPad mini). The app contains no disc files.
- Builders: Windows (x86-64) for the Android editions, one per edition (`check`, `fetch`, `build`, `verify`), and
  macOS (Apple silicon, Xcode) for the iOS and Duo apps (`check`, `fetch`, `build --ios`/`--duo`, `sign`).
- Thor Edition: the game's HUD on the second screen (bigger minimap, wanted stars), the weapon wheel on the lower
  screen, a touch phone with Answer / Decline.
- Duo: the game on the top half, map, wheels and phone on the lower half; Top Screen setting Fit (default) / Zoom /
  Stretch / 4:3 (4:3 after a restart); landscape or Lower Screen Off for full screen.
- Touch controls laid out like mobile open-world games, hidden while a controller is connected; steering arrows in
  vehicles; icons instead of button prompts on touch screens without a controller.
- Side menu with settings, a trainer and script-mod support.
- Release checks: taint scan against your own game files, name check, key and secret scan, personal-detail scan.

### Fixed
- Leaving the app briefly no longer loses the game: it is held still and silent for 25 seconds in the background and
  carries on when you come back (Android).

### Changed
- New name (FivePocket) and a new signing key for all editions.

### Upgrade note
- Apps from the closed alpha were signed with a different key. Android only installs an update signed with the same
  key, so the alpha app must be uninstalled first, and **uninstalling deletes its saves**. Copy your saves out before
  you uninstall.
