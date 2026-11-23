# The Android editions' app sources

The two Android apps are built from these two trees, in full:

| Published app | Built from | classes.dex (sha256) |
|---|---|---|
| FivePocket-Thor-Edition-1.2.1.apk (FivePocket Thor Edition) | `editions/thor-edition/` | 91ed68b341bf... |
| FivePocket-1.2.1.apk (FivePocket, single screen) | `editions/single-screen/` | dd5249d3849a... |

Each tree, built with the command below, gives a classes.dex byte-identical to the published app's, the same resources, manifest
and assets, and native libraries equal in every loaded section (only the build-id note, which records the build folder, differs).
The release checks verify this for every release (`tools/release/check_apk_sources.py`: every class in each app has its source in
its tree; and a rebuild of each app from this folder).

    cd editions/<edition>/android
    ./gradlew --offline --no-daemon --max-workers=2 --no-configuration-cache :app:assemblePublic

(JDK 17, Android SDK with NDK 27.2.12479018; the native build reads `editions/<edition>/tools/host_guest_trace`.) The published
apps are this unsigned output signed with the project's release key. `MANIFEST.tsv` lists every file with its SHA-256.
Licence: GPL-3.0-or-later (see COPYING), like the rest of the app sources.

1.1.0-alpha.1's source download carried only one Android tree (`android/`) and lacked part of these two apps' classes; from this
release on, the source download carries both edition trees.
