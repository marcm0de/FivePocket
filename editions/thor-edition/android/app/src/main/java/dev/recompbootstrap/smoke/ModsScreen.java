package dev.recompbootstrap.smoke;

import android.app.Activity;
import android.content.ContentResolver;
import android.content.Intent;
import android.database.Cursor;
import android.net.Uri;
import android.os.Handler;
import android.os.Looper;
import android.provider.DocumentsContract;
import android.provider.OpenableColumns;
import android.view.View;
import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.util.ArrayDeque;
import java.util.Deque;
import java.util.List;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.ThreadFactory;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * The Mods page (Update 1 P5), pushed from Settings > World > Script mods (A on that row). Thin: every decision is ModsPage's, every
 * file rule ModsLibrary's and ModsImport's (pure Java, host tested). This class draws the model, opens the system file chooser for a
 * folder or a zip (local files only), and runs the reads and writes off the UI thread.
 *
 * Threads: every read and write of this page runs on IO, one thread for the whole process, in the order asked (two changes can never
 * overtake each other, and an import never runs beside a load's staging sweep). The answers come back on the UI thread, where ModsPage
 * decides what they still mean (its operation lock and load tickets: see ModsPage).
 *
 * Writes: mods.txt (ModsLibrary.writeWords, through the settings store's gate), the Mods switch through SettingsStore.save (so that
 * settings.json and Settings > World stay the same choice), files/mods/&lt;pack&gt; (an import, or Remove of a pack the app imported).
 */
final class ModsScreen implements Screen {
    static final int REQUEST_FOLDER = 0x5E21, REQUEST_ZIP = 0x5E22;
    /** One thread, created when needed and gone after 10 idle seconds (no thread kept while the page is not used). */
    private static final ThreadPoolExecutor IO = new ThreadPoolExecutor(0, 1, 10, TimeUnit.SECONDS, new LinkedBlockingQueue<Runnable>(),
            new ThreadFactory() {
                @Override public Thread newThread(Runnable r) {
                    Thread t = new Thread(r, "mods-page");
                    t.setDaemon(true);
                    return t;
                }
            });
    private static final Handler MAIN = new Handler(Looper.getMainLooper());

    private final ModsPage model = new ModsPage();
    /** The crash guard's count of the last judged start (LauncherScreen hands it over); 0: no warning. */
    private final int warnMods;
    private ScreenHost host;
    private UiKit.SceneView root;
    private boolean shown;
    /** The running import's stop flag (each import has its own: leaving the page stops that one only). */
    private volatile AtomicBoolean cancel = new AtomicBoolean();

    ModsScreen(int warnMods) {
        this.warnMods = warnMods;
    }

    @Override public View createView(ScreenHost screenHost) {
        host = screenHost;
        root = new UiKit.SceneView(host.activity(), host.unit(), new UiKit.Listener() {
            @Override public void onHit(int id) {
                int base = id / 100 * 100, index = id - base;
                if (base == MenuLayout.HIT_ROW) model.tap(index);
                else if (base == MenuLayout.HIT_ALERT_BUTTON) model.answerTap(index);
                else if (base == MenuLayout.HIT_ARROW_LEFT || base == MenuLayout.HIT_ARROW_RIGHT) model.nav(NavInput.RIGHT, false);
                else if (base == MenuLayout.HIT_PROMPT) {
                    if (!onNav(index, false) && index == NavInput.BACK && shown) host.pop();
                    return;
                }
                perform(model.takeAction());
                render();
            }

            @Override public void onScroll(int lines) {
                model.scroll(lines);   // (the highlight moves; the drawn window follows it)
                render();
            }
        });
        LauncherScreen.showBackdrop(host, root);
        render();
        return root;
    }

    @Override public void onShown() {
        shown = true;
        load(null, false);
    }

    @Override public void onHidden() {
        shown = false;
        cancel.set(true);   // an import running when the page is left stops at its next block and keeps nothing
    }

    @Override public void onAppBack() {
        load(null, false);   // the game may have been started or left meanwhile
    }

    @Override public boolean onNav(int command, boolean repeat) {
        boolean handled = model.nav(command, repeat);
        perform(model.takeAction());
        render();
        return handled;
    }

    @Override public void onActivityResult(int requestCode, int resultCode, Intent data) {
        if (requestCode != REQUEST_FOLDER && requestCode != REQUEST_ZIP) return;
        final Uri uri = resultCode == Activity.RESULT_OK && data != null ? data.getData() : null;
        if (uri == null) {
            model.done(null, false);   // closed without a choice: nothing changes
            render();
            return;
        }
        if (!ModsImport.localProvider(uri.getAuthority())) {
            model.done(ModsImport.TEXT_CLOUD, true);
            render();
            return;
        }
        importFrom(uri, requestCode == REQUEST_FOLDER);
    }

    // ------------------------------------------------------------------------------------------ actions

    private void perform(ModsPage.Action action) {
        switch (action.type) {
            case SET_MASTER:
                setMaster(action.on);
                break;
            case WRITE_WORDS:
                write(action.name, action.on);
                break;
            case ADD_FOLDER:
                host.startForResult(new Intent(Intent.ACTION_OPEN_DOCUMENT_TREE).putExtra(Intent.EXTRA_LOCAL_ONLY, true), REQUEST_FOLDER);
                break;
            case ADD_ZIP:
                host.startForResult(new Intent(Intent.ACTION_OPEN_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE)
                        .setType("application/zip").putExtra(Intent.EXTRA_LOCAL_ONLY, true), REQUEST_ZIP);
                break;
            case REMOVE:
                remove(action.name);
                break;
            default:
                break;
        }
    }

    /** work on IO, then done on the UI thread. */
    private static void io(final Runnable work, final Runnable done) {
        IO.execute(new Runnable() {
            @Override public void run() {
                try {
                    work.run();
                } finally {
                    MAIN.post(done);
                }
            }
        });
    }

    private static String why(Throwable problem) {
        return problem.getMessage() == null ? problem.getClass().getSimpleName() : problem.getMessage();
    }

    /**
     * Reads what is on disk and whether the game runs; then shows sentence (null: keep the page's last line). On the way: the safe word
     * of a start that is over is removed, and staging folders of imports cut short are deleted (no import can run: IO is one thread).
     */
    private void load(final String sentence, final boolean failed) {
        final File filesDir = host.filesDir();
        final int ticket = model.loadAsked(sentence, failed);
        final Object[] found = new Object[5];
        final SettingsStore.GameRunning running = running();
        io(new Runnable() {
            @Override public void run() {
                boolean available = false, perMod = false;
                try {
                    Capabilities capabilities = CapabilityScanner.load(filesDir, SettingsScreen.inputOptionInApp());
                    available = capabilities.reads(RuntimeMapping.MODS_TXT);
                    perMod = capabilities.has(ModsPage.LIBRARY_V2_MARKER);
                } catch (IOException | RuntimeException unknown) {
                    available = false;
                }
                found[0] = available;
                found[4] = perMod;
                ModsLibrary.sweepStaging(filesDir);
                ModsLibrary.consumeSafe(filesDir, running);
                found[1] = ModsLibrary.list(filesDir);
                found[2] = ModsLibrary.readWords(filesDir);
                String newest = LaunchRequest.newestLauncherRun(filesDir);
                found[3] = newest == null ? null : ModsLibrary.statusOf(filesDir, newest);
            }
        }, new Runnable() {
            @SuppressWarnings("unchecked")
            @Override public void run() {
                if (found[1] == null) return;   // (the work failed before the listing: a later load answers)
                if (model.loaded(ticket, (Boolean) found[0], (Boolean) found[4], (List<ModsLibrary.Installed>) found[1], (ModsWords) found[2],
                        (ModsStatus) found[3], host.gameRunning(), warnMods, false /* sandbox: from play mode once SB P4 lands (NOTES) */)) {
                    render();
                }
            }
        });
    }

    private SettingsStore.GameRunning running() {
        return new SettingsStore.GameRunning() {
            @Override public boolean check() {
                return host.gameRunning();
            }
        };
    }

    /** The end of an operation: unlock after the next load, which shows sentence. */
    private void finish(String sentence, boolean failed) {
        model.finished();
        load(sentence, failed);
    }

    /** The Mods switch: the same setting as Settings > World > Script mods, saved the way that tab saves it. */
    private void setMaster(final boolean on) {
        final File filesDir = host.filesDir();
        final String[] outcome = new String[1];
        final boolean[] failed = new boolean[1];
        render();
        final SettingsStore.GameRunning running = running();
        io(new Runnable() {
            @Override public void run() {
                try {
                    synchronized (ModsLibrary.WORDS_LOCK) {   // (RuntimeMapping's plan rewrites mods.txt's scripts= word from the file)
                        Capabilities capabilities = CapabilityScanner.load(filesDir, SettingsScreen.inputOptionInApp());
                        SettingsStore store = new SettingsStore(filesDir, running);
                        Settings settings = store.load(capabilities).settings.with(SettingsSchema.SCRIPT_MODS, on ? 1 : 0);
                        SettingsStore.SaveResult saved = store.save(settings, capabilities);
                        failed[0] = !saved.ok;
                        outcome[0] = saved.ok ? ModsPage.TEXT_NEXT_START : saved.refusedGameRunning ? ModsPage.TEXT_GAME_RUNNING
                                : "Not saved: " + saved.error;
                    }
                } catch (IOException | RuntimeException problem) {
                    failed[0] = true;
                    outcome[0] = SettingsPages.failure(problem);
                }
            }
        }, new Runnable() {
            @Override public void run() {
                finish(outcome[0], failed[0]);
            }
        });
    }

    /** One change (a pack, or the safe word), applied to mods.txt as it is on disk (ModsLibrary.change). */
    private void write(final String pack, final boolean on) {
        final File filesDir = host.filesDir();
        final String[] outcome = new String[1];
        final SettingsStore.GameRunning running = running();
        render();
        io(new Runnable() {
            @Override public void run() {
                try {
                    ModsLibrary.change(filesDir, pack, on, running);
                } catch (IOException | RuntimeException problem) {
                    outcome[0] = why(problem);
                }
            }
        }, new Runnable() {
            @Override public void run() {
                finish(outcome[0] == null ? ModsPage.TEXT_NEXT_START : outcome[0], outcome[0] != null);
            }
        });
    }

    private void remove(final String name) {
        final File filesDir = host.filesDir();
        final String[] outcome = new String[1];
        final boolean[] failed = new boolean[1];
        render();
        final SettingsStore.GameRunning running = running();
        io(new Runnable() {
            @Override public void run() {
                try {
                    ModsLibrary.removePack(filesDir, name, running);
                    outcome[0] = "Removed " + name + ".";
                } catch (IOException | RuntimeException problem) {
                    failed[0] = true;
                    outcome[0] = why(problem);
                }
            }
        }, new Runnable() {
            @Override public void run() {
                finish(outcome[0], failed[0]);
            }
        });
    }

    private void importFrom(final Uri uri, final boolean folder) {
        final File filesDir = host.filesDir();
        final ContentResolver resolver = host.activity().getContentResolver();
        final ModsImport.Result[] result = new ModsImport.Result[1];
        final AtomicBoolean stop = new AtomicBoolean();
        cancel = stop;
        model.begin("Adding the mod.");
        render();
        io(new Runnable() {
            @Override public void run() {
                ModsImport.Progress progress = new ModsImport.Progress() {
                    @Override public void update(int files, long bytes, String current) { }

                    @Override public boolean cancelled() {
                        return stop.get();
                    }
                };
                try {
                    if (folder) {
                        String top = DocumentsContract.getTreeDocumentId(uri);
                        result[0] = ModsImport.run(filesDir, displayName(resolver, DocumentsContract.buildDocumentUriUsingTree(uri, top)),
                                tree(resolver, uri), filesDir.getUsableSpace(), progress);
                    } else {
                        if (virtual(resolver, uri)) throw new IOException(ModsImport.TEXT_CLOUD);   // (refused before any byte is read)
                        try (InputStream raw = resolver.openInputStream(uri)) {
                            if (raw == null) throw new IOException("The file cannot be opened.");
                            result[0] = ModsImport.run(filesDir, displayName(resolver, uri), ModsImport.zip(raw),
                                    filesDir.getUsableSpace(), progress);
                        }
                    }
                } catch (IOException | RuntimeException problem) {
                    String why = why(problem);
                    result[0] = new ModsImport.Result(false, null, 0, 0, why.equals(ModsImport.TEXT_CLOUD) ? why
                            : "The mod could not be added (" + why + ").");
                }
            }
        }, new Runnable() {
            @Override public void run() {
                ModsImport.Result r = result[0] != null ? result[0] : new ModsImport.Result(false, null, 0, 0, "The mod could not be added.");
                finish(r.sentence, !r.ok);
            }
        });
    }

    /** The chosen folder as a ModsImport.Source: depth first, every document checked for a cloud provider or a virtual document. */
    private static ModsImport.Source tree(final ContentResolver resolver, final Uri tree) {
        final String authority = tree.getAuthority();
        final Deque<String[]> folders = new ArrayDeque<String[]>();   // {document id, path inside the mod}
        final Deque<ModsImport.Entry> ready = new ArrayDeque<ModsImport.Entry>();
        folders.push(new String[] {DocumentsContract.getTreeDocumentId(tree), ""});
        return new ModsImport.Source() {
            @Override public ModsImport.Entry next() throws IOException {
                while (ready.isEmpty() && !folders.isEmpty()) {
                    String[] folder = folders.pop();
                    Cursor cursor;
                    try {
                        cursor = resolver.query(DocumentsContract.buildChildDocumentsUriUsingTree(tree, folder[0]),
                                new String[] {DocumentsContract.Document.COLUMN_DOCUMENT_ID, DocumentsContract.Document.COLUMN_DISPLAY_NAME,
                                        DocumentsContract.Document.COLUMN_MIME_TYPE, DocumentsContract.Document.COLUMN_FLAGS},
                                null, null, null);
                    } catch (RuntimeException e) {
                        throw new IOException("the folder cannot be listed (" + e.getClass().getSimpleName() + ")");
                    }
                    if (cursor == null) throw new IOException("the folder cannot be listed");
                    try {
                        while (cursor.moveToNext()) {
                            String id = cursor.getString(0), name = cursor.getString(1), mime = cursor.getString(2);
                            int flags = cursor.isNull(3) ? 0 : cursor.getInt(3);
                            if (name == null || id == null) continue;
                            String path = folder[1].isEmpty() ? name : folder[1] + "/" + name;
                            ModsImport.refuseCloud(authority, (flags & DocumentsContract.Document.FLAG_VIRTUAL_DOCUMENT) != 0);
                            if (DocumentsContract.Document.MIME_TYPE_DIR.equals(mime)) {
                                folders.push(new String[] {id, path});
                                ready.add(new ModsImport.Entry(path, true, null));
                            } else {
                                ready.add(new ModsImport.Entry(path, false, new LazyStream(resolver,
                                        DocumentsContract.buildDocumentUriUsingTree(tree, id))));
                            }
                        }
                    } finally {
                        cursor.close();
                    }
                }
                return ready.poll();
            }
        };
    }

    /** Opens the document only when ModsImport reads it (a skipped file is never opened). */
    private static final class LazyStream extends InputStream {
        private final ContentResolver resolver;
        private final Uri uri;
        private InputStream in;

        LazyStream(ContentResolver resolver, Uri uri) {
            this.resolver = resolver;
            this.uri = uri;
        }

        private InputStream open() throws IOException {
            if (in == null) {
                in = resolver.openInputStream(uri);
                if (in == null) throw new IOException("a file cannot be opened");
            }
            return in;
        }

        @Override public int read() throws IOException {
            return open().read();
        }

        @Override public int read(byte[] b, int off, int len) throws IOException {
            return open().read(b, off, len);
        }

        @Override public void close() throws IOException {
            if (in != null) in.close();
        }
    }

    private static String displayName(ContentResolver resolver, Uri uri) {
        try (Cursor c = resolver.query(uri, new String[] {OpenableColumns.DISPLAY_NAME}, null, null, null)) {
            if (c != null && c.moveToFirst() && !c.isNull(0)) return c.getString(0);
        } catch (RuntimeException unknown) {
            // a name the provider does not give: the pack is called "Mod"
        }
        return null;
    }

    private static boolean virtual(ContentResolver resolver, Uri uri) {
        try (Cursor c = resolver.query(uri, new String[] {DocumentsContract.Document.COLUMN_FLAGS}, null, null, null)) {
            return c != null && c.moveToFirst() && !c.isNull(0) && (c.getInt(0) & DocumentsContract.Document.FLAG_VIRTUAL_DOCUMENT) != 0;
        } catch (RuntimeException unknown) {
            return false;
        }
    }

    private void render() {
        if (root == null) return;
        root.show(EnhancedScenes.build(model.page(), root));   // 1.3.0 reship: the Enhanced frame (SPEC §125), as Setup and Settings
    }
}
