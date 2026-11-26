# FivePocket Builder (window)

The builder with a window: pick your two disc images, choose what to build, press **BUILD**. It runs the same steps
as the command-line builder, so the files it makes are the same, byte for byte.

## What you need
- The unpacked FivePocket builder download for your edition: the Thor Edition, or the single-screen edition (Galaxy
  Fold and other phones). On a Mac, the macOS builder makes the iOS apps.
- Your own two GTA V Xbox 360 disc images: Disc 1 (Install) and Disc 2 (Play), as `.iso` files or extracted folders.
- About 20 GB of free space, and half an hour to a few hours.
- Windows 10 or 11 (the window is shown by Microsoft Edge, which Windows already has). Nothing gets installed.
- A PC with an Intel or AMD (x64) processor and the Windows builder download, or a Windows on ARM laptop (Snapdragon)
  and the **Windows on ARM** builder download. The ARM download brings its own native ARM64 compiler programs and
  makes the same files, byte for byte. The x64 download stops at its first check on an ARM laptop and says so: its
  Android compiler would run emulated there, and emulated floating point differs.

## How to use it
1. Unpack the builder into a short folder, for example `C:\FivePocket`. Then double-click **FivePocket Builder.exe**.
   You can also drop a disc image onto it.
   - Why short: Windows limits a path to 259 characters unless long paths are turned on (they are off by default), and
     the Android compiler unpacks files nearly 200 characters deep. If the download stops with "move the cache to a
     shorter folder", move the builder (or the cache) to a folder like `C:\FP` and start again.
2. In the sidebar, click **Disc 2 (Play)** and **Disc 1 (Install)** and pick your images. Each one is checked the way
   the build reads it: the play disc is recognised by the SHA-256 of its game executable. If you pick a disc for the
   wrong slot, it moves to its own slot.
3. Under **Editions**, click an edition to see it, and click it again to switch it between built and not built.
4. **SETTINGS:** General (the backdrop, the output folder, the log), Compilers (the licences), Builder packages, About.
   - The first build downloads two compilers once, from their publishers: Google's Android NDK and Microsoft's
     DirectX Shader Compiler.
   - Read their terms there and tick the box. Nothing is downloaded before you do.
5. Press **BUILD**. A dialog shows what will be built, the output folder (BROWSE to change it) and the space needed
   against the space free. Press **BUILD** in it to start. **PROGRESS** shows each step, each build stage and the
   builder's log.
   - **Cancel** stops the build.
   - **Resume** continues it later from where it stopped, even after closing the window.
6. **RESULTS** shows each finished file with its size and SHA-256.
   - A bundle also shows its seal; the FivePocket app shows the same seal when it imports the bundle.
   - An iOS app also shows its bundle identifier. Always sign with that identifier.

## Where things go
- The finished files go to the output folder (by default `local-analysis\builder\work\out` inside the builder folder).
- The work files stay in the builder folder's `local-analysis\builder\work`; that is what lets a build resume.
- The window's record of the current build and its log are in `local-analysis\builder\gui`.

## Privacy
The window only talks to the builder on your own computer (127.0.0.1). The only download is the compilers, after you
accept their terms. The Discord, X and GitHub buttons open those pages in your browser when you click them.

## For developers
- `builder_core.py`: the logic (disc checks, the plan, the runner, cancel and resume). Tests in
  `tests/test_builder_core.py`.
- `web_app.py`: the local server the window talks to. Tests in `tests/test_web_app.py`.
- `web/`: the window itself (HTML, CSS, JS, and the FivePocket mark as SVG).
- `windows/gui_launcher.c`: the `FivePocket Builder.exe` starter (llvm-mingw).
- Run the tests with `python3 -m unittest test_builder_core test_web_app` from `tests/`.
- `web/backdrops/` takes the backdrop pictures (jpg/png/webp); each one becomes a choice in SETTINGS.
- `web/logo.svg` (or `.png` / `.webp`), when present, is shown in the logo box of the home page instead of the drawn
  FivePocket lettering (`stage_gui.py --logo FILE` puts it there).
- `web/logos/` takes one logo per edition and backdrop: a file name holding the edition word (`thor`, `ios`,
  `android`, `duo`) and after it a backdrop's name (e.g. `five-pocket-thor-pink-blue-dusk.png` for the backdrop
  `pink-blue-dusk.jpg`) is shown for that edition on that backdrop; one with the edition word alone, on every
  backdrop. `stage_gui.py --logos DIR` copies a folder of them (sub-folders too) there.
- `web/button-build.png` (and `button-resume`, `button-cancel`, `button-blank`; .png/.webp/.svg) replaces the drawn
  button: the picture for the button's word is shown as it is, else the blank one with the word written over it. Its
  rim (found from its transparency) is fitted to the launcher button's box. `stage_gui.py --button DIR` copies them.
- If a step fails, the window shows the builder's own message in a dialog; RESUME continues from that step.
