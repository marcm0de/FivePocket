package dev.recompbootstrap.smoke;

import android.app.Activity;
import android.content.ContentResolver;
import android.content.Intent;
import android.database.Cursor;
import android.net.Uri;
import android.os.Build;
import android.provider.DocumentsContract;
import android.view.View;
import java.io.File;
import java.io.IOException;

/**
 * The Settings tab (local-analysis/agents/app-ui-4/DESIGN.md section 5). This class only forwards and fills in what
 * is drawn: every decision about values, questions and saving is {@link MenuModel}'s, the grouping into categories
 * is {@link SettingsPages}', both tested on the host. Files are read and written by CapabilityScanner and
 * SettingsStore, off the UI thread, and written only after the user chose Save (or "Apply again"). Opening and
 * leaving the tab writes no setting.
 */
final class SettingsScreen {
    private static final long SAVED_NOTE_MS = 2000;

    private final ScreenHost host;
    private final LauncherScreen owner;
    private final ShellNav nav;
    private final SettingsPages pages = new SettingsPages();
    private MenuModel model = new MenuModel();
    private Capabilities capabilities;
    private boolean saving;
    private int loads, alertScroll;
    private View timerView;

    private final Runnable clearSavedNote = new Runnable() {
        @Override public void run() {
            model.clearSavedStatus();
            owner.render();
        }
    };

    SettingsScreen(ScreenHost host, LauncherScreen owner, ShellNav nav) {
        this.host = host;
        this.owner = owner;
        this.nav = nav;
        nav.setColumnCount(ShellNav.SETTINGS, SettingsPages.COUNT);
    }

    /** The view whose message queue carries the "Saved." timer. */
    void attach(View view) {
        timerView = view;
    }

    // ------------------------------------------------------------------------------------------ state for the frame

    boolean ready() {
        return model.placeholder() == null;
    }

    boolean dirty() {
        return model.dirty();
    }

    /** True while every command belongs to the model: a question is open or a save is running. */
    boolean capturing() {
        return saving || savesBusy || addingDlc || model.overlay() != MenuModel.OVERLAY_NONE || model.pendingAction() != MenuModel.ACTION_NONE;
    }

    /** True when the highlight can go from the category's name into its rows. */
    boolean enterable() {
        return ready() && SettingsPages.rows(model, nav.column(ShellNav.SETTINGS)).length > 0;
    }

    /** The tab became the current one, or the app came back to the front: read the files again unless edits stand. */
    void selected() {
        if (!dirty() && !capturing()) load();
    }

    // ------------------------------------------------------------------------------------------ commands

    /** A command for the model itself: anything while a question is open, Start (save) and Y (restore) otherwise. */
    void command(int command, boolean repeat) {
        if (model.overlay() == MenuModel.OVERLAY_DRIFT_LIST && (command == NavInput.UP || command == NavInput.DOWN)) {
            alertScroll = ConfirmOverlay.scrolled(alertScroll, command == NavInput.UP ? -1 : 1,
                    MenuScenes.alertDetailLines(model.overlayDetail(), owner.measure()));
            owner.render();
            return;
        }
        forward(command, repeat);
    }

    /** A command while the highlight is in the rows. */
    void paneCommand(int command, boolean repeat) {
        if (!ready()) return;
        if (command == NavInput.UP || command == NavInput.DOWN) {
            pages.move(model, command == NavInput.UP ? -1 : 1, repeat);
            owner.render();
        } else if (command == NavInput.CONFIRM && modsPageAsked(model.focused())) {
            host.push(new ModsScreen(owner.modsWarning()));   // Update 1: A on Script mods opens the Mods page
        } else if (command == NavInput.CONFIRM && dlcAddAsked(model.focused())) {
            addDlc();   // next update: A on the DLC row adds the player's own packs from a folder
        } else if (command == NavInput.LEFT || command == NavInput.RIGHT || command == NavInput.CONFIRM) {
            forward(command, repeat);
        }
    }

    /**
     * Update 1 (Mods page): A, or a tap on the highlighted row, on Script mods opens the Mods page instead of switching the row
     * (LEFT / RIGHT still switch it). Only while no edit waits to be saved: the page saves the same setting itself.
     */
    private boolean modsPageAsked(int row) {
        return row >= 0 && SettingsSchema.SCRIPT_MODS.equals(model.rowKey(row)) && model.rowEnabled(row) && !dirty() && !capturing();
    }

    // ------------------------------------------------------------------------------------------ save export / import (next update)

    static final int REQUEST_EXPORT_SAVES = 0x5A1, REQUEST_IMPORT_SAVES = 0x5A2;
    static final String TEXT_SAVES_GAME_RUNNING = "Close the game first: it writes its saves while it runs.",
            TEXT_SAVES_WORKING = "Working on your saves\u2026", TEXT_SAVES_REPLACED = "Your saves were replaced. The ones before are kept in the app.",
            TEXT_EXPORT_UNCHECKED = "The export file did not read back correctly, so it was removed. Try another place to save it.";
    private boolean savesBusy, stagedImport;

    private void savesAction(int action) {
        if (savesBusy) return;
        if (host.gameRunning()) {
            model.note(TEXT_SAVES_GAME_RUNNING);
            return;
        }
        if (action == MenuModel.ACTION_EXPORT_SAVES) {
            host.startForResult(new Intent(Intent.ACTION_CREATE_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType("application/zip")
                    .putExtra(Intent.EXTRA_TITLE, "FivePocket-saves-" + stamp("yyyyMMdd-HHmm") + ".zip"), REQUEST_EXPORT_SAVES);
        } else if (action == MenuModel.ACTION_IMPORT_SAVES) {
            host.startForResult(new Intent(Intent.ACTION_OPEN_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType("*/*")
                    .putExtra(Intent.EXTRA_MIME_TYPES, new String[] {"application/zip", "application/x-zip-compressed", "application/octet-stream"})
                    .putExtra(Intent.EXTRA_LOCAL_ONLY, true), REQUEST_IMPORT_SAVES);
        } else {   // the player chose Replace my saves
            stagedImport = false;
            final File filesDir = host.filesDir();
            final String backupName = stamp("yyyyMMdd-HHmmss");
            final String[] problem = new String[1];
            savesBusy = true;
            model.note(TEXT_SAVES_WORKING);
            host.background(new Runnable() {
                @Override public void run() {
                    problem[0] = SaveArchive.restore(filesDir, backupName);
                }
            }, new Runnable() {
                @Override public void run() {
                    savesBusy = false;
                    model.note(problem[0] == null ? TEXT_SAVES_REPLACED : problem[0]);
                    owner.render();
                }
            });
        }
    }

    private void savesPicked(final int requestCode, final Uri uri) {
        if (uri == null) return;   // closed without a choice: nothing changes
        if (host.gameRunning()) {
            model.note(TEXT_SAVES_GAME_RUNNING);
            owner.render();
            return;
        }
        final File filesDir = host.filesDir();
        final ContentResolver resolver = host.activity().getContentResolver();
        final java.util.Map<String, String> about = about();
        final SaveArchive.Result[] result = new SaveArchive.Result[1];
        savesBusy = true;
        model.note(TEXT_SAVES_WORKING);
        owner.render();
        host.background(new Runnable() {
            @Override public void run() {
                try {
                    if (requestCode == REQUEST_EXPORT_SAVES) {
                        try (java.io.OutputStream out = resolver.openOutputStream(uri, "w")) {
                            if (out == null) throw new IOException("the file cannot be written");
                            result[0] = SaveArchive.export(filesDir, out, about);
                        }
                        if (result[0].ok) {   // read back through Import's own checks: "exported" only for a file Import takes
                            try (java.io.InputStream back = resolver.openInputStream(uri)) {
                                SaveArchive.Result checked = back == null ? SaveArchive.Result.failed("") : SaveArchive.check(back);
                                if (!checked.ok) result[0] = SaveArchive.Result.failed(TEXT_EXPORT_UNCHECKED);
                            }
                        }
                        if (!result[0].ok) {   // a half-written or unchecked file must not look like a good export
                            try {
                                DocumentsContract.deleteDocument(resolver, uri);
                            } catch (Exception kept) {
                                result[0] = SaveArchive.Result.failed(result[0].sentence + " Delete the file it made: it is not a usable export.");
                            }
                        }
                    } else {
                        try (java.io.InputStream in = resolver.openInputStream(uri)) {
                            if (in == null) throw new IOException("the file cannot be opened");
                            result[0] = SaveArchive.read(filesDir, in);
                        }
                    }
                } catch (IOException | RuntimeException e) {
                    result[0] = SaveArchive.Result.failed("The file could not be used (" + e.getClass().getSimpleName() + ").");
                }
            }
        }, new Runnable() {
            @Override public void run() {
                savesBusy = false;
                SaveArchive.Result r = result[0];
                if (requestCode == REQUEST_IMPORT_SAVES && r != null && r.ok) {
                    stagedImport = true;
                    model.note(null);
                    model.askImportSaves(SaveArchive.summary(r));
                } else {
                    model.note(r == null ? "The file could not be used." : r.sentence);
                }
                owner.render();
            }
        });
    }

    /** What the manifest says an export came from. */
    private java.util.Map<String, String> about() {
        java.util.Map<String, String> about = new java.util.LinkedHashMap<String, String>();
        try {
            android.content.pm.PackageInfo info = host.activity().getPackageManager().getPackageInfo(host.activity().getPackageName(), 0);
            about.put("app", info.versionName == null ? "" : info.versionName);
        } catch (Exception unknown) {
            about.put("app", "");
        }
        about.put("edition", host.activity().getPackageName());
        about.put("created", stamp("yyyy-MM-dd HH:mm"));
        return about;
    }

    private static String stamp(String pattern) {
        return new java.text.SimpleDateFormat(pattern, java.util.Locale.ROOT).format(new java.util.Date());
    }

    // ------------------------------------------------------------------------------------------ Add DLC (next update)

    static final int REQUEST_DLC = 0xD1C;
    static final String TEXT_DLC_ADDING = "Adding DLC\u2026", TEXT_DLC_GAME_RUNNING = "Stop the game first: it reads the DLC folder while it runs.",
            TEXT_DLC_SWITCH_ON = " Switch DLC on and save to use them.";
    private boolean addingDlc;

    /** A (or a tap) on the highlighted DLC row opens the folder picker; LEFT / RIGHT still switch the row. Only with nothing unsaved. */
    private boolean dlcAddAsked(int row) {
        return row >= 0 && SettingsSchema.DLC_PACKS.equals(model.rowKey(row)) && model.rowEnabled(row) && !dirty() && !capturing() && !addingDlc;
    }

    private void addDlc() {
        if (host.gameRunning()) {
            model.note(TEXT_DLC_GAME_RUNNING);
            owner.render();
            return;
        }
        host.startForResult(new Intent(Intent.ACTION_OPEN_DOCUMENT_TREE).putExtra(Intent.EXTRA_LOCAL_ONLY, true), REQUEST_DLC);
    }

    /** The picker's answer: the chosen folder's package files are copied by DlcImport (pure Java, tested), off the UI thread. */
    void onActivityResult(int requestCode, int resultCode, Intent data) {
        if (requestCode == REQUEST_EXPORT_SAVES || requestCode == REQUEST_IMPORT_SAVES) {
            savesPicked(requestCode, resultCode == Activity.RESULT_OK && data != null ? data.getData() : null);
            return;
        }
        if (requestCode != REQUEST_DLC) return;
        final Uri tree = resultCode == Activity.RESULT_OK && data != null ? data.getData() : null;
        if (tree == null) return;   // closed without a choice: nothing changes
        if (!ModsImport.localProvider(tree.getAuthority())) {
            model.note(ModsImport.TEXT_CLOUD);
            owner.render();
            return;
        }
        final File filesDir = host.filesDir();
        final ContentResolver resolver = host.activity().getContentResolver();
        final boolean rowOn = rowValueOn(SettingsSchema.DLC_PACKS);
        final String[] sentence = new String[1];
        addingDlc = true;
        model.note(TEXT_DLC_ADDING);
        owner.render();
        host.background(new Runnable() {
            @Override public void run() {
                try {
                    DlcImport.Result r = DlcImport.run(new File(filesDir, "private-trace"), documents(resolver, tree), filesDir.getUsableSpace(), null);
                    sentence[0] = r.sentence + (r.ok && r.added > 0 && !rowOn ? TEXT_DLC_SWITCH_ON : "");
                } catch (IOException | RuntimeException e) {
                    sentence[0] = "The DLC could not be added (" + (e.getMessage() == null ? e.getClass().getSimpleName() : e.getMessage()) + ").";
                }
            }
        }, new Runnable() {
            @Override public void run() {
                addingDlc = false;
                model.note(sentence[0]);
                owner.render();
            }
        });
    }

    private boolean rowValueOn(String key) {
        for (int i = 0; i < model.rowCount(); ++i) if (key.equals(model.rowKey(i))) return "On".equals(model.rowValue(i));
        return false;
    }

    /** The chosen folder's documents (one level: packages sit side by side), each opened only when DlcImport copies it. */
    private static java.util.List<DlcImport.Doc> documents(final ContentResolver resolver, final Uri tree) throws IOException {
        java.util.List<DlcImport.Doc> docs = new java.util.ArrayList<DlcImport.Doc>();
        Cursor cursor;
        try {
            cursor = resolver.query(DocumentsContract.buildChildDocumentsUriUsingTree(tree, DocumentsContract.getTreeDocumentId(tree)),
                    new String[] {DocumentsContract.Document.COLUMN_DOCUMENT_ID, DocumentsContract.Document.COLUMN_DISPLAY_NAME,
                            DocumentsContract.Document.COLUMN_MIME_TYPE, DocumentsContract.Document.COLUMN_SIZE, DocumentsContract.Document.COLUMN_FLAGS},
                    null, null, null);
        } catch (RuntimeException e) {
            throw new IOException("the folder cannot be listed");
        }
        if (cursor == null) throw new IOException("the folder cannot be listed");
        try {
            while (cursor.moveToNext()) {
                final String id = cursor.getString(0), name = cursor.getString(1), mime = cursor.getString(2);
                final long size = cursor.isNull(3) ? -1 : cursor.getLong(3);
                int flags = cursor.isNull(4) ? 0 : cursor.getInt(4);
                if (id == null || name == null || DocumentsContract.Document.MIME_TYPE_DIR.equals(mime)) continue;
                if ((flags & DocumentsContract.Document.FLAG_VIRTUAL_DOCUMENT) != 0) throw new IOException(ModsImport.TEXT_CLOUD);
                final Uri document = DocumentsContract.buildDocumentUriUsingTree(tree, id);
                docs.add(new DlcImport.Doc() {
                    @Override public String name() { return name; }
                    @Override public long size() { return size; }
                    @Override public java.io.InputStream open() throws IOException {
                        java.io.InputStream in = resolver.openInputStream(document);
                        if (in == null) throw new IOException("the file cannot be opened");
                        return in;
                    }
                });
            }
        } finally {
            cursor.close();
        }
        return docs;
    }

    /** The highlight goes from the category's name into its rows. */
    void enter() {
        int category = nav.column(ShellNav.SETTINGS);
        if (!ready() || !pages.enter(model, category)) nav.place(ShellNav.SETTINGS, category, false);
    }

    /** Leaving with unsaved changes was asked for: the model puts its question. */
    void askLeave() {
        model.nav(NavInput.BACK, false);
        settle();
    }

    /** Touch. Returns true when the tap was this tab's. */
    boolean hit(int base, int index) {
        if (base == MenuLayout.HIT_ALERT_BUTTON) {
            pages.tapAnswer(model, nav, index);   // the highlighted answer: as A; another one: the highlight moves to it
            settle();
            return true;
        }
        if (!ready() || capturing()) return false;
        if (base != MenuLayout.HIT_ROW && base != MenuLayout.HIT_ARROW_LEFT && base != MenuLayout.HIT_ARROW_RIGHT) return false;
        if (base == MenuLayout.HIT_ROW && nav.level() == ShellNav.PANE) {   // Update 1: a tap on the highlighted Script mods row
            int[] rows = SettingsPages.rows(model, nav.column(ShellNav.SETTINGS));
            if (index >= 0 && index < rows.length && rows[index] == model.focused() && modsPageAsked(rows[index])) {
                host.push(new ModsScreen(owner.modsWarning()));
                return true;
            }
            if (index >= 0 && index < rows.length && rows[index] == model.focused() && dlcAddAsked(rows[index])) {   // (next update: as A)
                addDlc();
                return true;
            }
        }
        if (!pages.tap(model, nav, base, index)) return false;
        settle();
        return true;
    }

    private void forward(int command, boolean repeat) {
        pages.forward(model, nav, command, repeat);
        settle();
    }

    /** Draws the model's new state and does what it asks for. */
    private void settle() {
        int action = model.pendingAction();
        if (action == MenuModel.ACTION_EXPORT_SAVES || action == MenuModel.ACTION_IMPORT_SAVES || action == MenuModel.ACTION_IMPORT_CONFIRMED) {
            model.actionTaken();
            savesAction(action);   // next update: save export / import
            owner.render();
            return;
        }
        if (stagedImport && model.overlay() != MenuModel.OVERLAY_IMPORT_SAVES && action == MenuModel.ACTION_NONE) {
            stagedImport = false;   // the question was cancelled: the checked copy goes
            final File filesDir = host.filesDir();
            host.background(new Runnable() { @Override public void run() { SaveArchive.discardStaging(filesDir); } },
                    new Runnable() { @Override public void run() { } });
        }
        if (action == MenuModel.ACTION_SAVE) {
            save();
            return;
        }
        if (action == MenuModel.ACTION_LEAVE) {
            // The question was answered with Save or Discard. That model is finished: start from the files again.
            model = new MenuModel();
            alertScroll = 0;
            load();
            owner.settingsLeft(nav.leaveGranted());
            return;
        }
        if (nav.asking() && !capturing()) nav.leaveCancelled();   // Keep editing, or the save failed
        if (model.overlay() == MenuModel.OVERLAY_NONE) alertScroll = 0;
        owner.render();
    }

    // ------------------------------------------------------------------------------------------ work off the UI thread

    /** Reads what the installed game build supports and the saved settings. Writes no setting. */
    private void load() {
        final int ticket = ++loads;
        final File filesDir = host.filesDir();
        final boolean inputOption = inputOptionInApp();
        final Object[] result = new Object[2];
        final String[] failure = new String[1];
        host.background(new Runnable() {
            @Override public void run() {
                try {
                    Capabilities found = CapabilityScanner.load(filesDir, inputOption);
                    result[0] = found;
                    result[1] = store(filesDir).load(found);
                } catch (IOException | RuntimeException problem) {
                    failure[0] = describe(problem);
                }
            }
        }, new Runnable() {
            @Override public void run() {
                // Only the newest read counts, and never over edits or a question that appeared meanwhile.
                if (ticket != loads || dirty() || capturing()) return;
                MenuModel fresh = new MenuModel();
                SettingsStore.Loaded loaded = (SettingsStore.Loaded) result[1];
                if (failure[0] != null || loaded == null) {
                    fresh.loadFailed(failure[0] == null ? "no result" : failure[0]);
                } else {
                    capabilities = (Capabilities) result[0];
                    fresh.setGameRunning(host.gameRunning());
                    fresh.loaded(loaded.settings, loaded.fromFile, loaded.warnings, loaded.drift, capabilities);
                }
                model = fresh;
                pages.reloaded(model, nav);
                owner.render();
            }
        });
    }

    /** The one place a setting is written: the model asked for it after the user chose Save or "Apply again". */
    private void save() {
        ++loads;   // a read that began before this save holds what the files said before it: its result no longer counts
        final File filesDir = host.filesDir();
        final boolean onDriftRow = SettingsPages.onDriftRow(model);
        final Settings settings = model.beginSave();
        final Capabilities supported = capabilities;
        final SettingsStore.SaveResult[] result = new SettingsStore.SaveResult[1];
        final String[] failure = new String[1];
        saving = true;
        owner.render();
        host.background(new Runnable() {
            @Override public void run() {
                try {
                    result[0] = store(filesDir).save(settings, supported);
                } catch (RuntimeException problem) {
                    failure[0] = describe(problem);
                }
            }
        }, new Runnable() {
            @Override public void run() {
                saving = false;
                if (result[0] != null && result[0].ok && host.activity() instanceof BootstrapActivity) {
                    ((BootstrapActivity) host.activity()).menuMusicChanged();   // (next update: Menu music takes effect at once)
                }
                if (result[0] != null && result[0].ok) {   // 2026-11-22: the App icon row takes effect at once (AppIcon reads what was saved)
                    final android.content.Context app = host.activity().getApplicationContext();
                    host.background(new Runnable() {
                        @Override public void run() {
                            AppIcon.reconcile(app);
                        }
                    }, new Runnable() {
                        @Override public void run() {
                        }
                    });
                }
                if (result[0] != null) model.saveFinished(result[0].ok, result[0].refusedGameRunning, result[0].error);
                else model.saveFinished(false, false, failure[0]);
                if (MenuModel.TEXT_SAVED.equals(model.status()) && timerView != null) {
                    timerView.removeCallbacks(clearSavedNote);
                    timerView.postDelayed(clearSavedNote, SAVED_NOTE_MS);
                }
                pages.afterSave(model, nav, onDriftRow);
                settle();
            }
        });
    }

    void hidden() {
        if (timerView != null) timerView.removeCallbacks(clearSavedNote);
    }

    private SettingsStore store(File filesDir) {
        return new SettingsStore(filesDir, new SettingsStore.GameRunning() {
            @Override public boolean check() {
                return host.gameRunning();
            }
        });
    }

    static boolean inputOptionInApp() {   // (Update 1: also the Mods page's capability scan)
        if (Build.VERSION.SDK_INT < 30) return false;
        try {
            Class.forName("dev.recompbootstrap.smoke.InputDispatchOption");
            return true;
        } catch (ClassNotFoundException | LinkageError missing) {
            return false;
        }
    }

    /** The words for an exception: SettingsPages.failure (plain words first, the technical cause in brackets). */
    private static String describe(Exception problem) {
        return SettingsPages.failure(problem);
    }

    /** Fills in what the tab shows. */
    void fill(MenuScenes.Page page) {
        pages.fill(page, model, nav, alertScroll);
    }
}
