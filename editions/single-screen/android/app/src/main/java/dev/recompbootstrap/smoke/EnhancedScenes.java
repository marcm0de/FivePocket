package dev.recompbootstrap.smoke;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/**
 * Menu revamp M1 (local-analysis/update-1/menu-revamp/SPEC.md §3.1): one launcher list page drawn as the game's own Enhanced
 * Settings frame, from the same plain description as MenuScenes (MenuScenes.Page), into the same 1920 x 1080 design panel.
 *
 * Every number is the template's (reference/enhanced-settings-graphics-{video,quality}.png, 1024 x 576) times 1.875, measured
 * 2026-11-07 by android-qol (menu-revamp/M1-NOTES.md): the frame from x 166 for 0.678 W; a blue line and a light header bar with
 * the page's name in small capitals; under it one grid of slots (pitch 21.55, bars 20) from y 128: the categories as separate dark
 * bars at the left, the pane at the right - the status block (two slots), the rows as one continuous dark block, the row that says
 * there is more, the description box - and the prompt chip with white key boxes in the bottom right corner of the screen.
 *
 * What our content adds is said in the game's own vocabulary: no change marks, no counter; a setting that applies at the next start
 * says so in the description box. Alerts still use MenuScenes' frame (M1 covers the list page only).
 */
final class EnhancedScenes {
    private EnhancedScenes() { }

    /** Template pixels (1024 x 576) to design pixels (1920 x 1080): the same factor on both axes. */
    static final float T = 1920f / 1024f;

    static float t(float templatePx) {
        return templatePx * T;
    }

    // ---------------------------------------------------------------------------------------------- geometry (design px)
    static final float FRAME_LEFT = t(166f), FRAME_W = 0.678f * MenuLayout.W, FRAME_RIGHT = FRAME_LEFT + FRAME_W;
    static final float ACCENT_TOP = t(92.6f), ACCENT_H = t(3.9f);
    static final float HEADER_TOP = ACCENT_TOP + ACCENT_H, HEADER_H = t(20.7f);
    static final float BODY_TOP = t(128f), PITCH = t(21.55f), BAR = t(20f), GAP = PITCH - BAR;
    static final float COLUMN_W = t(230f), PANE_LEFT = FRAME_LEFT + t(232f), PANE_W = FRAME_RIGHT - PANE_LEFT;
    /** The status block's slots and its text lines' pitch; the description box's line pitch; its most lines. */
    static final int STATUS_SLOTS = 2;
    static final float STATUS_LINE = t(14f), DESC_LINE = t(15f);
    static final int DESC_LINES = 3;
    /** The last slot the pane's rows may take (the template's rows end at slot 15, its scroll row is 16, its description 17). */
    static final int LAST_ROW_SLOT = 15;
    static final float COLUMN_PAD = t(5.5f), LABEL_PAD = t(5.8f), VALUE_PAD = t(6.7f);
    /** The header's text stands 2 right of the frame's centre (515 against 513 in both templates). */
    static final float HEADER_TEXT_DX = t(2f);
    /** The value's arrows on the highlighted row: 4 x 7, a 1.3 stroke, the right one ending 7.1 from the pane's edge (M2 re-measure). */
    static final float ARROW_W = t(4f), ARROW_H = t(7f), ARROW_T = t(1.3f), ARROW_PAD = t(7.1f), ARROW_GAP = t(3.75f);
    /** The bars: 100 x 4.75; a slider's right end 7 from the pane's edge, the status bar's 6. */
    static final float SLIDER_W = t(100f), SLIDER_H = t(4.75f), SLIDER_PAD = t(7f), STATUS_BAR_PAD = t(6f);
    /** The scroll row's two chevrons: each 9 x 5 with a 2 stroke, 2 apart, centred on the pane. */
    static final float SCROLL_W = t(9f), SCROLL_H = t(5f), SCROLL_T = t(1.9f), SCROLL_GAP = t(2.5f);
    /** The prompt chip: right edge 1008, top 548, 19 high; 15.25 x 15.25 key boxes (M2 re-measure, fractional); label to box 3.3, between prompts 14.4, left pad 6 (M2: from the label origins in the disc face). */
    static final float CHIP_RIGHT = t(1008f), CHIP_TOP = t(548f), CHIP_H = t(19f), CHIP_PAD_LEFT = t(6f), CHIP_PAD_RIGHT = t(8f);
    static final float KEY = t(15.25f), KEY_GAP = t(3.3f), PROMPT_SPACE = t(14.4f), KEY_WIDE_PAD = t(3f);
    /** A chip's touch rectangle reaches 0.02 H above and below it (SPEC §3.7). */
    static final float CHIP_TOUCH = 0.02f * MenuLayout.H;
    /** Text sizes (Nunito Sans, cap height 0.705 em) for the measured cap heights: rows 7.3, header 6.7, key letters 7. */
    static final int ROW_PX = 19, HEADER_PX = 18, KEY_PX = 17;
    /**
     * M2, the game's own face (Chalet London from the player's disc; E-F1, menu-revamp/M2-NOTES.md): every list text of the template
     * at 10.36 px (13 labels, a 391 px description line and the header within 1 px), the chip's labels at 9.61 px; times 1.875. The
     * baselines the template puts them on, fitted to its ink: 14.1 of a 20 bar in the pane, 13.8 in the categories, 12.8 of the 19
     * chip, 14.6 of the 20.7 header. The painter centres a text on its ascent and descent (Chalet: 245 and 64 of 256), so each kind's
     * box moves by the difference.
     */
    static final float GAME_PX = t(10.36f), GAME_CHIP_PX = t(9.61f);
    static final float GAME_ROW_SHIFT = t(0.45f), GAME_COLUMN_SHIFT = t(0.12f), GAME_CHIP_SHIFT = t(-0.12f), GAME_HEADER_SHIFT = t(0.59f);

    /** The face and sizes of one build: the disc's face when the painter has it, else the bundled Nunito Sans (M1's sizes). */
    static final class Type {
        final int face, header;
        final float row, chip, rowShift, columnShift, chipShift, headerShift;

        Type(int face, int header, float row, float chip, float rowShift, float columnShift, float chipShift, float headerShift) {
            this.face = face;
            this.header = header;
            this.row = row;
            this.chip = chip;
            this.rowShift = rowShift;
            this.columnShift = columnShift;
            this.chipShift = chipShift;
            this.headerShift = headerShift;
        }
    }

    static final Type NUNITO = new Type(Scene.REGULAR, Scene.CAPS, ROW_PX, ROW_PX, 0f, 0f, 0f, 0f);
    static final Type GAME = new Type(Scene.GAME, Scene.GAME, GAME_PX, GAME_CHIP_PX, GAME_ROW_SHIFT, GAME_COLUMN_SHIFT, GAME_CHIP_SHIFT,
            GAME_HEADER_SHIFT);

    static Type type(Scene.Measure measure) {
        return measure.gameFont() ? GAME : NUNITO;
    }

    // ---------------------------------------------------------------------------------------------- colours
    static final int ACCENT = 0xFF2D6EB9, HEADER = 0xFFF0F0F0, HEADER_TEXT = 0xFF000000;
    /** HUD_COLOUR_INGAME_BG: black at 186 / 255 (the measured 0.27 transmission). */
    static final int PANEL = 0xBA000000;
    static final int TEXT = 0xFFF0F0F0, TEXT_OFF = 0xFF5D5D5D, SELECT = 0xFFF0F0F0, SELECT_TEXT = 0xFF000000, SELECT_TEXT_OFF = 0x73000000;
    static final int SLIDER_FILL = 0xFF2D6EB9, SLIDER_TRACK = 0xFF22344B, STATUS_FILL = 0xFF72CC72, STATUS_TRACK = 0xFF4E584B;
    static final int CHIP = 0xB8000000, KEY_BOX = 0xFFF4F4F4, KEY_TEXT = 0xFF000000, FAINT = 0x40F0F0F0, FAINT_ON_SELECT = 0x33000000;
    static final String RESTART_NOTE = "This setting applies the next time the game starts.";
    /** The fallback while the revamp is checked: this file under the app's files folder keeps the console frame on every tab. */
    static final String CLASSIC_SWITCH = "settings/menu-classic";

    static float slotTop(int slot) {
        return BODY_TOP + slot * PITCH;
    }

    /** From the top of one slot to the bottom of the bar of the slot n - 1 further on: n slots drawn as one block. */
    static float slotsHeight(int slots) {
        return slots <= 0 ? 0f : slots * PITCH - GAP;
    }

    /** The status block's slots for this many text lines (two for one or two lines, as the template). */
    static int statusSlots(int lines) {
        if (lines <= 0) return 0;
        return Math.max(STATUS_SLOTS, (int) Math.ceil((lines * STATUS_LINE + BAR - STATUS_LINE + GAP) / PITCH));
    }

    /** The description box's height for this many lines: one bar, then a line pitch each. */
    static float descHeight(int lines) {
        return lines <= 0 ? 0f : BAR + (lines - 1) * DESC_LINE;
    }

    /** How many rows the pane shows from the slot `first`: all, or as many as leave room for the scroll row before LAST_ROW_SLOT + 1. */
    static int visibleRows(int count, int firstSlot) {
        int room = LAST_ROW_SLOT + 1 - firstSlot;
        if (count <= room + 1) return Math.min(count, room + 1);   // the slot of the scroll row can hold the last row instead
        return Math.max(1, room);
    }

    static Scene build(MenuScenes.Page page, Scene.Measure measure) {
        return build(page, measure, MenuLayout.H, 1f, false);
    }

    /**
     * 2026-11-17 (owner: "the game setup screen still looks like shit"; SPEC §125): a question (ConfirmOverlay: the setup's Copy / Start /
     * Not Now, the error and done notices) in this frame instead of the console alert - its heading in the header bar, its title and
     * detail in the description box, its buttons as the column's rows (the highlighted one lit). Only the drawing changes: the buttons'
     * touch areas keep HIT_ALERT_BUTTON + i and the prompts are the page's own, so every screen answers exactly as before.
     */
    static Scene alertPage(MenuScenes.Page page, Scene.Measure measure, int panelH, float k, boolean touchIcons) {
        ConfirmOverlay alert = page.alert;
        MenuScenes.Page q = new MenuScenes.Page();
        q.title = alert.heading != null && !alert.heading.isEmpty() ? alert.heading : page.title;
        q.backdrop = page.backdrop;
        q.status.addAll(page.status);
        if (alert.title != null && !alert.title.isEmpty()) q.info.add(new MenuScenes.Line(alert.title, MenuScenes.TONE_NORMAL));
        if (alert.detail != null && !alert.detail.isEmpty()) q.info.add(new MenuScenes.Line(alert.detail, MenuScenes.TONE_NORMAL));
        q.column = new String[alert.buttons.length];
        for (int i = 0; i < alert.buttons.length; ++i) q.column[i] = Scene.titleCase(alert.buttons[i]);
        q.columnSelected = Math.max(0, Math.min(alert.focused, Math.max(0, alert.buttons.length - 1)));
        q.columnLit = true;
        q.prompts.addAll(page.prompts);
        Scene scene = build(q, measure, panelH, k, touchIcons);
        java.util.List<Scene.Hit> hits = new java.util.ArrayList<Scene.Hit>(scene.hits);
        scene.hits.clear();
        for (Scene.Hit h : hits) {
            int id = h.id >= MenuLayout.HIT_COLUMN && h.id < MenuLayout.HIT_COLUMN + 100 ? MenuLayout.HIT_ALERT_BUTTON + (h.id - MenuLayout.HIT_COLUMN) : h.id;
            scene.hit(h.x, h.y, h.w, h.h, id);
        }
        return scene;
    }

    /**
     * The page in a panel 1920 wide and panelH high (taller than 1080 on a landscape screen taller than 16:9: the Fold), drawn k times
     * the template's size (1: exact; more on a touch-only screen, SPEC §3.7 and main's touch decision 2026-11-07, see touchScale).
     * The menu is scaled about the panel's centre; the prompt chip about the screen's right edge and the status chip about its left
     * edge, so they stay in the corners. With k = 1 in a 1080 panel nothing moves (the template's own place).
     * touchIcons: a touch screen with no gamepad (the owner's rule, 2026-10-25): the chip keeps only Back and Select, their key boxes
     * showing our X and check instead of button letters (START and Y prompts get none: their rows do the same by touch).
     */
    static Scene build(MenuScenes.Page page, Scene.Measure measure, int panelH, float k, boolean touchIcons) {
        if (page.alert != null) return alertPage(page, measure, panelH, k, touchIcons);
        Scene scene = new Scene();
        // the Enhanced main menu's backdrop: the loading-screen art, sharp (the disc's, when imported); else the player's own picture
        if (page.backdrop && measure.hasArt()) scene.backdropArt();
        else if (page.backdrop) scene.backdrop(MenuLayout.BACKDROP_TOP, MenuLayout.BACKDROP_BOTTOM);
        else scene.fill(MenuLayout.DIM_OVER_HUD);
        header(scene, page, measure);
        if (page.column != null) column(scene, page, measure);
        pane(scene, page, measure);
        scene.layer(Scene.CHROME);
        int chipItems = scene.items.size(), chipHits = scene.hits.size();
        List<MenuScenes.Prompt> prompts = touchIcons ? touchPrompts(page.prompts) : page.prompts;
        prompts(scene, prompts, touchIcons, measure);
        int statusItems = scene.items.size(), statusHits = scene.hits.size();
        status(scene, page, prompts, measure);
        scene.contentKey = page.tab + "/" + page.columnSelected + "/" + (page.text != null);
        if (k != 1f || panelH != MenuLayout.H) place(scene, panelH, k, chipItems, statusItems, chipHits, statusHits);
        // (2026-11-09) the top tab bar after place(): it is the panel's own top edge, never scaled; touchScale keeps the frame below it.
        // (This edition has no in-game 360 pause frame: every Enhanced page is a launcher page.)
        if (page.tabs.length > 1) tabBar(scene, page, measure);
        return scene;
    }

    /** The touch scale for a touch-only screen: 4.4 mm row pitch, but the frame within 0.95 of the width and the menu within the panel's height; never below 1. */
    static float touchScale(float xdpi, float u, int panelH) {
        if (!(xdpi > 0f) || !(u > 0f)) return 1f;
        float pitchMm = PITCH * u * 25.4f / xdpi;
        float byPitch = 4.4f / pitchMm, byWidth = 0.95f * MenuLayout.W / FRAME_W;
        float half = panelH / 2f, byHeight = Math.min((half - t(4f)) / (CHIP_TOP + CHIP_H - MenuLayout.H / 2f), (half - TABBAR_H) / (MenuLayout.H / 2f - ACCENT_TOP));   // (2026-11-09: the frame stays below the top tab bar)
        return Math.max(1f, Math.min(byPitch, Math.min(byWidth, byHeight)));
    }

    private static void place(Scene scene, int panelH, float k, int chipItems, int statusItems, int chipHits, int statusHits) {
        float cx = MenuLayout.W / 2f, cy = MenuLayout.H / 2f, py = panelH / 2f;
        for (int i = 0; i < scene.items.size(); ++i) {
            Scene.Item it = scene.items.get(i);
            if (it.type == Scene.BACKDROP || it.type == Scene.FILL) continue;
            float ax = i < chipItems ? cx : i < statusItems ? MenuLayout.W : 0f;   // the menu / the prompt chip / the status chip
            it.x = ax + (it.x - ax) * k;
            it.y = py + (it.y - cy) * k;
            it.w *= k;
            it.h *= k;
            it.stroke *= k;
            if (it.type == Scene.TEXT) {
                it.size = Math.max(1, Math.round(it.size * k));
                it.exact *= k;
            }
            if (it.points != null) {
                for (int p = 0; p + 1 < it.points.length; p += 2) {
                    it.points[p] = ax + (it.points[p] - ax) * k;
                    it.points[p + 1] = py + (it.points[p + 1] - cy) * k;
                }
            }
        }
        List<Scene.Hit> hits = new ArrayList<Scene.Hit>(scene.hits);
        scene.hits.clear();
        for (int i = 0; i < hits.size(); ++i) {
            Scene.Hit h = hits.get(i);
            float ax = i < chipHits ? cx : i < statusHits ? MenuLayout.W : 0f;
            scene.hit(ax + (h.x - ax) * k, py + (h.y - cy) * k, h.w * k, h.h * k, h.id);
        }
    }

    /** The page's name: the current tab's (the launcher), else the page title. */
    static String title(MenuScenes.Page page) {
        String name = page.tabs.length > 0 && page.tab >= 0 && page.tab < page.tabs.length ? page.tabs[page.tab] : page.title;
        return name == null ? "" : name.toUpperCase(Locale.ROOT);
    }

    // 2026-11-09 (lead, owner on the touch screens: from Settings there was no way back to PLAY by touch): the launcher's top tab bar on
    // every tab page, visible and tappable - a tap on a tab switches to it at once (ShellNav.tapTab), as the GTA pause menu's tabs. The
    // landing's bar (LandingScenes: same geometry and faces), above the frame: the bar ends at TABBAR_H, the frame's accent starts lower.
    static final float TABBAR_H = t(65.5f), TABBAR_LEFT = t(52f), TABBAR_TOP = t(29f), TABBAR_TAB_H = t(19.97f), TABBAR_PAD = t(12f),
            TABBAR_GAP = t(2f), TABBAR_BASE = t(44.8f), TABBAR_PX = t(13.3f);
    static final int TABBAR_GROUND = 0xFF000000, TABBAR_LIT = 0xFFF0F0F0;

    static void tabBar(Scene scene, MenuScenes.Page page, Scene.Measure measure) {
        boolean game = measure.gameFont();
        int face = game ? Scene.GAME : Scene.REGULAR;
        float half = game ? (245f - 64f) / 512f : (1.011f - 0.353f) / 2f, h = TABBAR_PX * 1.3f;
        scene.rect(0, 0, MenuLayout.W, TABBAR_H, TABBAR_GROUND);
        float x = TABBAR_LEFT;
        for (int i = 0; i < page.tabs.length; ++i) {
            String name = page.tabs[i].toUpperCase(Locale.ROOT);
            float w = measure.width(name, TABBAR_PX, face) + 2 * TABBAR_PAD;
            boolean active = i == page.tab;
            if (active) scene.rect(x, TABBAR_TOP, w, TABBAR_TAB_H, TABBAR_LIT);
            scene.textExact(name, x, TABBAR_BASE - half * TABBAR_PX - h / 2f, w, h, TABBAR_PX, face, Scene.CENTER, active ? TABBAR_GROUND : TABBAR_LIT);
            scene.hit(x, 0, w, TABBAR_H, MenuLayout.HIT_TAB + i);
            x += w + TABBAR_GAP;
        }
    }

    private static void header(Scene scene, MenuScenes.Page page, Scene.Measure measure) {
        scene.layer(Scene.CHROME);
        scene.rect(FRAME_LEFT, ACCENT_TOP, FRAME_W, ACCENT_H, ACCENT);
        scene.rect(FRAME_LEFT, HEADER_TOP, FRAME_W, HEADER_H, HEADER);
        Type ty = type(measure);
        float size = ty == GAME ? ty.row : HEADER_PX;
        String title = Scene.fit(title(page), size, ty.header, FRAME_W - 2 * LABEL_PAD, measure);
        scene.textExact(title, FRAME_LEFT + HEADER_TEXT_DX, HEADER_TOP + ty.headerShift, FRAME_W, HEADER_H, size, ty.header, Scene.CENTER, HEADER_TEXT);
        // M1 keeps the launcher's tabs (Settings opens from the current launcher until M6): the header's halves are LB and RB.
        if (page.tabs.length > 1) {
            float top = ACCENT_TOP - GAP, height = BODY_TOP - GAP - top;
            scene.hit(FRAME_LEFT, top, FRAME_W / 2f, height, MenuLayout.HIT_PROMPT + NavInput.PAGE_PREV);
            scene.hit(FRAME_LEFT + FRAME_W / 2f, top, FRAME_W / 2f, height, MenuLayout.HIT_PROMPT + NavInput.PAGE_NEXT);
        }
    }

    private static void column(Scene scene, MenuScenes.Page page, Scene.Measure measure) {
        float textWidth = COLUMN_W - 2 * COLUMN_PAD;
        Type ty = type(measure);
        int last = Math.min(page.column.length, LAST_ROW_SLOT + 3);
        for (int i = 0; i < last; ++i) {
            float top = slotTop(i);
            boolean lit = i == page.columnSelected && page.columnLit;
            boolean enabled = page.columnEnabled == null || page.columnEnabled[i];
            scene.layer(Scene.CHROME);
            scene.rect(FRAME_LEFT, top, COLUMN_W, BAR, lit ? SELECT : PANEL);
            scene.hit(FRAME_LEFT, top - GAP / 2f, COLUMN_W, PITCH, MenuLayout.HIT_COLUMN + i);
            scene.layer(Scene.CONTENT);
            scene.textExact(Scene.fit(page.column[i], ty.row, ty.face, textWidth, measure), FRAME_LEFT + COLUMN_PAD, top + ty.columnShift, textWidth,
                    BAR, ty.row, ty.face, Scene.LEFT, lit ? (enabled ? SELECT_TEXT : SELECT_TEXT_OFF) : enabled ? TEXT : TEXT_OFF);
        }
    }

    private static List<MenuScenes.Line> wrap(List<MenuScenes.Line> parts, float width, Scene.Measure measure) {
        List<MenuScenes.Line> out = new ArrayList<MenuScenes.Line>();
        Type ty = type(measure);
        for (MenuScenes.Line part : parts) {
            for (String line : Scene.wrap(part.text, ty.row, ty.face, width, measure)) out.add(new MenuScenes.Line(line, part.tone));
        }
        return out;
    }

    /** The first row the pane draws (the page's rowFirst moved only as far as the focused row needs). */
    static int firstRow(MenuScenes.Page page, Scene.Measure measure) {
        int slot = statusSlots(wrap(page.info, statusTextWidth(page), measure).size()) + (page.diagram ? MenuLayout.DIAGRAM_SLOTS : 0);
        int count = page.rows.size();
        return MenuLayout.windowStart(page.rowFirst, page.rowFocused, count, visibleRows(count, slot));
    }

    /** 2026-11-17 (setup redesign): a page without a column (the setup pages) has its pane across the whole frame, not the right pane. */
    static float paneLeft(MenuScenes.Page page) {
        return page.column == null ? FRAME_LEFT : PANE_LEFT;
    }

    static float paneW(MenuScenes.Page page) {
        return page.column == null ? FRAME_W : PANE_W;
    }

    static float statusTextWidth(MenuScenes.Page page) {
        float width = paneW(page) - LABEL_PAD - VALUE_PAD;
        return page.infoBar >= 0f ? width - SLIDER_W - STATUS_BAR_PAD - LABEL_PAD : width;
    }

    private static void pane(Scene scene, MenuScenes.Page page, Scene.Measure measure) {
        float left = paneLeft(page), width = paneW(page), textWidth = width - LABEL_PAD - VALUE_PAD;
        Type ty = type(measure);
        // The status block: its lines centred in two slots (or as many as they need), the bar at its right.
        List<MenuScenes.Line> info = wrap(page.info, statusTextWidth(page), measure);
        int slot = statusSlots(info.size());
        if (slot > 0) {
            float height = slotsHeight(slot), top = BODY_TOP + (height - info.size() * STATUS_LINE) / 2f;
            scene.layer(Scene.CHROME);
            scene.rect(left, BODY_TOP, width, height, PANEL);
            if (page.infoBar >= 0f) {
                float barLeft = left + width - STATUS_BAR_PAD - SLIDER_W, barTop = BODY_TOP + (height - SLIDER_H) / 2f;
                scene.rect(barLeft, barTop, SLIDER_W, SLIDER_H, STATUS_TRACK);
                scene.rect(barLeft, barTop, SLIDER_W * clamp(page.infoBar), SLIDER_H, STATUS_FILL);
            }
            scene.layer(Scene.CONTENT);
            for (int i = 0; i < info.size(); ++i) {
                scene.textExact(info.get(i).text, left + LABEL_PAD, top + i * STATUS_LINE + ty.rowShift, textWidth, STATUS_LINE, ty.row, ty.face, Scene.LEFT,
                        MenuScenes.color(info.get(i).tone, TEXT));
            }
        }
        if (page.text != null) {
            text(scene, page, slot, ty, left, width);
            return;
        }
        if (page.diagram) {   // M5: the Controller tab's diagram, 8 slots, as the game's own Gamepad page (the owner's PC capture)
            diagram(scene, slotTop(slot), measure);
            slot += MenuLayout.DIAGRAM_SLOTS;
        }
        // The rows: one continuous dark block; the highlighted row a light bar on the grid.
        int count = page.rows.size();
        if (count > 0) {
            int visible = visibleRows(count, slot);
            int first = MenuLayout.windowStart(page.rowFirst, page.rowFocused, count, visible), last = Math.min(count, first + visible);
            boolean more = visible < count;
            scene.layer(Scene.CHROME);
            scene.rect(left, slotTop(slot), width, slotsHeight(last - first), PANEL);
            for (int index = first; index < last; ++index) {
                row(scene, page.rows.get(index), index, slotTop(slot + index - first), index == page.rowFocused, measure, left, width);
            }
            slot += last - first;
            if (more) scrollRow(scene, slotTop(slot++), left, width);
        }
        description(scene, page, slot, measure, left, width);
    }

    private static float clamp(float f) {
        return f < 0f ? 0f : f > 1f ? 1f : f;
    }

    private static void row(Scene scene, MenuScenes.Row row, int index, float top, boolean focused, Scene.Measure measure, float left, float width) {
        int ink = focused ? (row.enabled ? SELECT_TEXT : SELECT_TEXT_OFF) : row.enabled ? TEXT : TEXT_OFF;
        Type ty = type(measure);
        scene.layer(Scene.CHROME);
        if (focused) scene.rect(left, top, width, BAR, SELECT);
        scene.hit(left, top - GAP / 2f, width, PITCH, MenuLayout.HIT_ROW + index);
        scene.layer(Scene.CONTENT);
        float labelLeft = left + LABEL_PAD, right = left + width - VALUE_PAD, labelRight = right;
        boolean slider = row.fraction >= 0f;
        if (row.value != null && slider) {
            float barLeft = left + width - SLIDER_PAD - SLIDER_W, barTop = top + (BAR - SLIDER_H) / 2f;
            scene.layer(Scene.CHROME);
            scene.rect(barLeft, barTop, SLIDER_W, SLIDER_H, SLIDER_TRACK);
            scene.rect(barLeft, barTop, SLIDER_W * clamp(row.fraction), SLIDER_H, row.enabled ? SLIDER_FILL : TEXT_OFF);
            scene.layer(Scene.CONTENT);
            // the highlighted slider's value and arrows stand before the bar: the right arrow keeps its own room
            right = barLeft - 2 * LABEL_PAD - (focused && row.arrows && row.enabled ? ARROW_W + ARROW_GAP : 0f);
            labelRight = right;
        }
        // A slider says its value with its bar; its number stands before the bar only on the highlighted row.
        if (row.value != null && (!slider || focused)) {
            boolean arrows = focused && row.arrows && row.enabled;
            float arrowTop = top + (BAR - ARROW_H) / 2f;
            float valueRight = arrows && !slider ? left + width - ARROW_PAD - ARROW_W - ARROW_GAP : right;
            if (arrows && !slider) {
                chevron(scene, left + width - ARROW_PAD - ARROW_W, arrowTop, ARROW_W, ARROW_H, ARROW_T, MenuLayout.POINT_RIGHT,
                        row.canRight ? SELECT_TEXT : FAINT_ON_SELECT);
            }
            float labelWidth = Math.min(measure.width(row.label, ty.row, ty.face), width * 0.45f);
            float room = valueRight - (labelLeft + labelWidth + 2 * LABEL_PAD) - (arrows ? ARROW_W + ARROW_GAP : 0f);
            String value = Scene.fit(row.value, ty.row, ty.face, room, measure);
            float valueWidth = measure.width(value, ty.row, ty.face);
            scene.textExact(value, valueRight - valueWidth, top + ty.rowShift, valueWidth, BAR, ty.row, ty.face, Scene.RIGHT,
                    focused ? ink : MenuScenes.color(row.valueTone, ink));
            labelRight = valueRight - valueWidth - 2 * LABEL_PAD;
            if (arrows) {
                float arrowLeft = valueRight - valueWidth - ARROW_GAP - ARROW_W;
                chevron(scene, arrowLeft, arrowTop, ARROW_W, ARROW_H, ARROW_T, MenuLayout.POINT_LEFT, row.canLeft ? SELECT_TEXT : FAINT_ON_SELECT);
                labelRight = arrowLeft - 2 * LABEL_PAD;
                if (slider) {
                    chevron(scene, valueRight + ARROW_GAP, arrowTop, ARROW_W, ARROW_H, ARROW_T, MenuLayout.POINT_RIGHT,
                            row.canRight ? SELECT_TEXT : FAINT_ON_SELECT);
                }
            }
            if (row.art != null) {   // 2026-11-22: the App icon row's logo, square and as tall as the bar, before the value
                float x = labelRight + LABEL_PAD - BAR;
                if (x > labelLeft + labelWidth + 2 * LABEL_PAD) {
                    scene.image(row.art, x, top, BAR, BAR);
                    labelRight = x - 2 * LABEL_PAD;
                }
            }
        }
        scene.textExact(Scene.fit(row.label, ty.row, ty.face, labelRight - labelLeft, measure), labelLeft, top + ty.rowShift, labelRight - labelLeft,
                BAR, ty.row, ty.face, Scene.LEFT, ink);
        if (focused && row.halves) {
            scene.hit(left, top - GAP / 2f, width / 2f, PITCH, MenuLayout.HIT_ARROW_LEFT + index);
            scene.hit(left + width / 2f, top - GAP / 2f, width / 2f, PITCH, MenuLayout.HIT_ARROW_RIGHT + index);
        }
    }

    private static void chevron(Scene scene, float x, float y, float w, float h, float t, int direction, int color) {
        scene.poly(color, MenuLayout.chevron(x, y, w, h, t, direction));
    }

    private static void scrollRow(Scene scene, float top, float left, float width) {
        scene.layer(Scene.CHROME);
        scene.rect(left, top, width, BAR, PANEL);
        float x = left + (width - SCROLL_W) / 2f, y = top + (BAR - 2 * SCROLL_H - SCROLL_GAP) / 2f;
        // both chevrons always at full white: the game's marker says "this list scrolls", not which way (both templates and the
        // owner's pause-menu capture show both lit at a list's top); the game shows no marker on a list that fits (its 360 Saving page)
        chevron(scene, x, y, SCROLL_W, SCROLL_H, SCROLL_T, MenuLayout.POINT_UP, TEXT);
        chevron(scene, x, y + SCROLL_H + SCROLL_GAP, SCROLL_W, SCROLL_H, SCROLL_T, MenuLayout.POINT_DOWN, TEXT);
    }

    /**
     * M5: the controller diagram in the Enhanced pane, measured on the owner's capture of the game's own Gamepad page (PC pause menu,
     * the same frame: pane 867 wide from x 746 at 1080): a dark block of 8 slots, "ON FOOT" centred in its first bar, the pad drawing
     * (MenuScenes.Pad, one of its units = 1 design px) centred 165 below the block's top, nine labels a side in capitals every 30 from 55
     * below the top, their near ends 177 from the pane's centre, each joined to its part by a thin right-angled line; the face buttons
     * the disc's own glyphs when imported (the capture's small coloured discs), else MenuScenes' marks.
     */
    private static void diagram(Scene scene, float top, Scene.Measure measure) {
        Type ty = type(measure);
        float left = PANE_LEFT, width = PANE_W, k = 1f;
        scene.layer(Scene.CHROME);
        scene.rect(left, top, width, slotsHeight(MenuLayout.DIAGRAM_SLOTS), PANEL);
        scene.layer(Scene.CONTENT);
        scene.textExact(MenuScenes.DIAGRAM_HEADING, left, top + ty.rowShift, width, BAR, ty.row, ty.face, Scene.CENTER, TEXT);
        float cx = left + width / 2f, cy = top + 165 * k, line = 2.4f * k;
        for (float[] path : MenuScenes.Pad.OUTLINE) scene.line(TEXT, line, place(path, cx, cy, k));
        for (float[] ring : MenuScenes.Pad.RINGS) scene.ring(cx + ring[0] * k, cy + ring[1] * k, ring[2] * k, line, TEXT);
        scene.line(TEXT, 3 * k, place(new float[] {-8, -30, 8, -14}, cx, cy, k));
        scene.line(TEXT, 3 * k, place(new float[] {8, -30, -8, -14}, cx, cy, k));
        String[] faces = {"Y", "X", "B", "A"};
        float d = 21 * k;
        for (int i = 0; i < 4; ++i) {
            float fx = cx + MenuScenes.Pad.FACES[2 * i] * k, fy = cy + MenuScenes.Pad.FACES[2 * i + 1] * k;
            if (measure.hasGlyph(faces[i])) scene.image(UiAssets.glyphFile(faces[i]), fx - d / 2f, fy - d / 2f, d, d);
            else MenuScenes.mark(scene, faces[i], fx, fy, 19 * k);
        }
        int lead = 0x9FF0F0F0;
        for (int side = 0; side < 2; ++side) {
            float sign = side == 0 ? -1 : 1;
            for (int i = 0; i < MenuScenes.Pad.LABELS[side].length; ++i) {
                String label = MenuScenes.Pad.LABELS[side][i];
                float y = top + (55 + 30 * i) * k, labelX = cx + sign * 177 * k, w = measure.width(label, ty.row, ty.face);
                scene.textExact(label, side == 0 ? labelX - w : labelX, y - BAR / 2f + ty.rowShift, w, BAR, ty.row, ty.face,
                        side == 0 ? Scene.RIGHT : Scene.LEFT, TEXT);
                float tx = cx + MenuScenes.Pad.TARGETS[side][2 * i] * k, ty2 = cy + MenuScenes.Pad.TARGETS[side][2 * i + 1] * k;
                float sx = labelX - sign * 7 * k;
                if (Math.abs(ty2 - y) < 3 * k) scene.line(lead, 1.4f * k, sx, y, tx, y);
                else scene.line(lead, 1.4f * k, sx, y, tx, y, tx, ty2);
            }
        }
    }

    private static float[] place(float[] g, float cx, float cy, float k) {
        float[] out = new float[g.length];
        for (int i = 0; i < g.length; i += 2) {
            out[i] = cx + g[i] * k;
            out[i + 1] = cy + g[i + 1] * k;
        }
        return out;
    }

    /** The description of the highlighted row (the page's help), plus the restart note: at most DESC_LINES lines. */
    static List<String> descriptionLines(MenuScenes.Page page, Scene.Measure measure) {
        return descriptionLines(page, measure, DESC_LINES);
    }

    /**
     * 2026-11-17 (setup redesign): a page without a column (the setup pages: few rows, long instructions) may use more description lines,
     * as many as end where the deepest list's 3-line box ends (never lower than any page could reach before), at most DESC_LINES_WIDE.
     */
    static final int DESC_LINES_WIDE = 6;
    static int descriptionMax(MenuScenes.Page page, int slot) {
        if (page.column != null) return DESC_LINES;
        float bottom = slotTop(LAST_ROW_SLOT + 2) + descHeight(DESC_LINES), top = slotTop(slot);
        int fit = 1 + (int) Math.floor((bottom - top - BAR) / DESC_LINE + 1e-3f);
        return Math.max(DESC_LINES, Math.min(DESC_LINES_WIDE, fit));
    }

    static List<String> descriptionLines(MenuScenes.Page page, Scene.Measure measure, int max) {
        List<MenuScenes.Line> parts = new ArrayList<MenuScenes.Line>(page.help);
        if (page.rowFocused >= 0 && page.rowFocused < page.rows.size() && page.rows.get(page.rowFocused).restart) {
            parts.add(new MenuScenes.Line(RESTART_NOTE, MenuScenes.TONE_NORMAL));
        }
        float width = paneW(page) - LABEL_PAD - VALUE_PAD;
        Type ty = type(measure);
        List<String> lines = new ArrayList<String>();
        for (MenuScenes.Line line : wrap(parts, width, measure)) lines.add(line.text);
        if (lines.size() > max) {
            List<String> cut = new ArrayList<String>(lines.subList(0, max));
            cut.set(max - 1, Scene.fit(cut.get(max - 1) + " " + Scene.ELLIPSIS + Scene.ELLIPSIS, ty.row, ty.face, width, measure));
            return cut;
        }
        return lines;
    }

    private static void description(Scene scene, MenuScenes.Page page, int slot, Scene.Measure measure, float left, float width) {
        List<String> lines = descriptionLines(page, measure, descriptionMax(page, slot));
        if (lines.isEmpty()) return;
        Type ty = type(measure);
        float top = slotTop(slot);
        scene.layer(Scene.CHROME);
        scene.rect(left, top, width, descHeight(lines.size()), PANEL);
        scene.layer(Scene.CONTENT);
        for (int i = 0; i < lines.size(); ++i) {
            scene.textExact(lines.get(i), left + LABEL_PAD, top + (BAR - DESC_LINE) / 2f + i * DESC_LINE + ty.rowShift, width - LABEL_PAD - VALUE_PAD,
                    DESC_LINE, ty.row, ty.face, Scene.LEFT, TEXT);
        }
    }

    /** A pane text page (About's notices): its lines in the rows' slots, in one dark block. */
    private static void text(Scene scene, MenuScenes.Page page, int slot, Type ty, float left, float width) {
        int room = LAST_ROW_SLOT + 2 - slot, from = Math.max(0, page.textScroll), shown = Math.min(room, page.text.size() - from);
        if (shown <= 0) return;
        float top = slotTop(slot);
        scene.layer(Scene.CHROME);
        scene.rect(left, top, width, slotsHeight(shown), page.textFocused ? 0xD0000000 : PANEL);
        scene.hit(left, top, width, slotsHeight(shown), MenuLayout.HIT_TEXT);
        scene.layer(Scene.CONTENT);
        for (int i = 0; i < shown; ++i) {
            scene.textExact(page.text.get(from + i), left + LABEL_PAD, slotTop(slot + i) + ty.rowShift, width - LABEL_PAD - VALUE_PAD, BAR,
                    ty.row, ty.face, Scene.LEFT, TEXT);
        }
    }

    /** A key box's width: 16 square for one letter, wider for a word (START). */
    static float keyWidth(String glyph, Scene.Measure measure) {
        if (measure.hasGlyph(glyph)) return KEY;   // M3: the game's own glyph, square
        return glyph.length() <= 1 ? KEY : Math.max(KEY, measure.width(glyph, KEY_PX - 4, Scene.BOLD) + 2 * KEY_WIDE_PAD);
    }

    /** A touch screen with no gamepad: only the prompts of B and A, in their order (the owner's rule 2026-10-25). */
    static List<MenuScenes.Prompt> touchPrompts(List<MenuScenes.Prompt> all) {
        List<MenuScenes.Prompt> out = new ArrayList<MenuScenes.Prompt>();
        for (MenuScenes.Prompt p : all) if (p.command == NavInput.BACK || p.command == NavInput.CONFIRM) out.add(p);
        return out;
    }

    /** The prompt chip's width for these prompts (0 for none). */
    static float chipWidth(List<MenuScenes.Prompt> prompts, Scene.Measure measure) {
        if (prompts.isEmpty()) return 0f;
        float total = CHIP_PAD_LEFT + CHIP_PAD_RIGHT + (prompts.size() - 1) * PROMPT_SPACE;
        Type ty = type(measure);
        for (MenuScenes.Prompt p : prompts) total += measure.width(p.label, ty.chip, ty.face) + KEY_GAP + keyWidth(p.glyph, measure);
        return total;
    }

    private static void prompts(Scene scene, List<MenuScenes.Prompt> prompts, boolean touchIcons, Scene.Measure measure) {
        prompts(scene, prompts, touchIcons, measure, true);
    }

    /** The prompt strip; chip false: no dark chip behind it (the landing's strip stands on its black ground, M6). */
    static void prompts(Scene scene, List<MenuScenes.Prompt> prompts, boolean touchIcons, Scene.Measure measure, boolean chip) {
        float total = chipWidth(prompts, measure);
        if (total == 0f) return;
        float left = CHIP_RIGHT - total, x = left + CHIP_PAD_LEFT, keyTop = CHIP_TOP + (CHIP_H - KEY) / 2f;
        Type ty = type(measure);
        scene.layer(Scene.CHROME);
        if (chip) scene.rect(left, CHIP_TOP, total, CHIP_H, CHIP);
        for (MenuScenes.Prompt p : prompts) {
            float label = measure.width(p.label, ty.chip, ty.face), key = keyWidth(p.glyph, measure), keyLeft = x + label + KEY_GAP;
            scene.textExact(p.label, x, CHIP_TOP + ty.chipShift, label, CHIP_H, ty.chip, ty.face, Scene.LEFT, TEXT);
            if (!touchIcons && measure.hasGlyph(p.glyph)) {
                // M3: the game's own controller glyph from the player's disc (gfxfontlib_360) in the key box's place
                scene.image(UiAssets.glyphFile(p.glyph), keyLeft, keyTop, KEY, KEY);
            } else if (touchIcons) {
                scene.rect(keyLeft, keyTop, key, KEY, KEY_BOX);
                touchGlyph(scene, p.command == NavInput.BACK, keyLeft, keyTop, KEY);
            } else {
                scene.rect(keyLeft, keyTop, key, KEY, KEY_BOX);
                int size = p.glyph.length() <= 1 ? KEY_PX : KEY_PX - 4;
                scene.text(p.glyph, keyLeft, keyTop, key, KEY, size, Scene.BOLD, Scene.CENTER, KEY_TEXT);
            }
            float hitLeft = Math.max(left, x - PROMPT_SPACE / 2f), hitRight = Math.min(CHIP_RIGHT, keyLeft + key + PROMPT_SPACE / 2f);
            scene.hit(hitLeft, CHIP_TOP - CHIP_TOUCH, hitRight - hitLeft, CHIP_H + 2 * CHIP_TOUCH, MenuLayout.HIT_PROMPT + p.command);
            x = keyLeft + key + PROMPT_SPACE;
        }
    }

    /** Our X (back) or check (select) in a key box of side `side`, black: the touch screen's glyphs in the chip's style. */
    private static void touchGlyph(Scene scene, boolean back, float x, float y, float side) {
        float t = side * 0.13f;
        if (back) {
            float g = side * 0.27f;
            scene.line(KEY_TEXT, t, x + g, y + g, x + side - g, y + side - g);
            scene.line(KEY_TEXT, t, x + side - g, y + g, x + g, y + side - g);
        } else {
            scene.line(KEY_TEXT, t, x + side * 0.22f, y + side * 0.53f, x + side * 0.42f, y + side * 0.72f, x + side * 0.79f, y + side * 0.30f);
        }
    }

    /** The status ("Saving...", "Saved.") in a chip of the same kind at the bottom left, as far in as the prompt chip. */
    private static void status(Scene scene, MenuScenes.Page page, List<MenuScenes.Prompt> prompts, Scene.Measure measure) {
        if (page.footerStatus == null) return;
        float margin = MenuLayout.W - CHIP_RIGHT, room = CHIP_RIGHT - chipWidth(prompts, measure) - PROMPT_SPACE - 2 * margin;
        Type ty = type(measure);
        String text = Scene.fit(page.footerStatus.text, ty.chip, ty.face, room - CHIP_PAD_LEFT - CHIP_PAD_RIGHT, measure);
        float w = measure.width(text, ty.chip, ty.face) + CHIP_PAD_LEFT + CHIP_PAD_RIGHT;
        scene.rect(margin, CHIP_TOP, w, CHIP_H, CHIP);
        scene.textExact(text, margin + CHIP_PAD_LEFT, CHIP_TOP + ty.chipShift, w - CHIP_PAD_LEFT - CHIP_PAD_RIGHT, CHIP_H, ty.chip, ty.face, Scene.LEFT,
                MenuScenes.color(page.footerStatus.tone, TEXT));
    }
}
