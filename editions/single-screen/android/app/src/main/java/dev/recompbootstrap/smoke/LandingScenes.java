package dev.recompbootstrap.smoke;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/**
 * Menu revamp M6a (local-analysis/update-1/menu-revamp/M6-NOTES.md): the launcher's Play tab drawn as the game's own Enhanced landing
 * (reference/enhanced-landing.webp, 1024 x 576; every number is the template's times 1.875), from the same MenuScenes.Page as before -
 * today's navigation and content: a black top bar with our tabs as the template's tab boxes (the active one a light box), the hero band
 * (the disc's art; the highlighted item's name as the title, its state text as the paragraph), the tile row = the Play tab's items
 * (the highlighted one framed), the hint line, and the prompt strip on the black ground. Faces measured on the template (E-F2): tabs,
 * paragraph and hint Chalet London; the title and the tile captions Chalet Comprimé. Pure Java.
 */
final class LandingScenes {
    private LandingScenes() { }

    static float t(float templatePx) {
        return EnhancedScenes.t(templatePx);
    }

    static final float BAR_H = t(65.5f), TAB_LEFT = t(52f), TAB_TOP = t(29f), TAB_H = t(19.97f), TAB_PAD = t(12f), TAB_GAP = t(2f);
    static final float HERO_TOP = BAR_H, HERO_BOTTOM = t(404.5f), TEXT_LEFT = t(51f), TEXT_W = t(323f);
    static final float TITLE_BASE = t(154f), PARA_BASE = t(174.8f), PARA_PITCH = t(16.4f), TAB_BASE = t(44.8f), HINT_BASE = t(544f);
    static final float TILE_TOP = t(416f), TILE_W = t(176f), TILE_H = t(99f), TILE_PITCH = t(186.5f), TILE_LEFT = t(51f), FRAME = t(3f);
    static final float CAPTION_PAD = t(12f), CAPTION_BASE = TILE_TOP + TILE_H - t(11f) + t(0.5f);
    /** Sizes (template px x 1.875), E-F2: tabs 13.3, title 27.4 (condensed), paragraph and hint 12.8, captions 15.75 (condensed). */
    static final float TAB_PX = t(13.3f), TITLE_PX = t(27.4f), PARA_PX = t(12.8f), CAPTION_PX = t(15.75f);
    static final int PARA_LINES = 9, MAX_TILES = 5, MAX_TILES_LOCKED = 6;
    static final int BLACK = 0xFF000000, WHITE = 0xFFF0F0F0, GROUND = 0xFF2B2F2C, DIMMED = 0x8C000000;

    static Scene build(MenuScenes.Page page, Scene.Measure measure) {
        return build(page, measure, MenuLayout.H, false);
    }

    /**
     * 2026-11-17 (owner: "play, settings, controller and about does not scale correctly. fix this as I have previously"): the landing in a
     * panel 1920 wide and panelH high, like the list tabs (EnhancedScenes.build): the top tab bar is EnhancedScenes.tabBar, the same code,
     * size, faces and place on every tab, never moved or scaled; below it the landing moves down by half the extra height as the list
     * tabs' frame does (their prompt chip and ours stand at the same place), the hero band growing to meet it. touchIcons: the prompts as
     * the X / check icons (a touch screen without a gamepad), the list tabs' rule.
     */
    static Scene build(MenuScenes.Page page, Scene.Measure measure, int panelH, boolean touchIcons) {
        if (page.alert != null) return EnhancedScenes.build(page, measure, panelH, 1f, touchIcons);   // 1.3.0 reship: questions in the Enhanced frame (SPEC §125)
        Scene scene = new Scene();
        scene.fill(BLACK);
        boolean game = measure.gameFont();
        int face = game ? Scene.GAME : Scene.REGULAR, cond = game ? Scene.GAME_COND : Scene.REGULAR;
        scene.layer(Scene.CHROME);
        // the hero band: the disc's art (cropped to fill), else a flat ground
        // 2026-11-17 (owner: "I want the background to stay the same scale"): the disc's art is the same full-window backdrop as on
        // every other page (Scene.backdropArt: the painter crops it to the view alone, so its size and place never depend on the page);
        // the black above and below the hero band masks it, so the band shows the backdrop's own middle, never a rescaled copy.
        if (measure.hasArt()) {
            scene.backdropArt();
            scene.rect(0, 0, MenuLayout.W, HERO_TOP, BLACK);
            scene.rect(0, HERO_BOTTOM, MenuLayout.W, MenuLayout.H - HERO_BOTTOM, BLACK);
        } else scene.rect(0, HERO_TOP, MenuLayout.W, HERO_BOTTOM - HERO_TOP, GROUND);
        // Departure (said so, M6-NOTES): the template's hero is a night scene and its text stands on dark; the 360's loading-screen art is
        // pastel daylight, so a soft dark run under the text keeps it readable (black 62 % at the left edge to 0 at 0.5 W)
        shade(scene, 0, HERO_TOP, MenuLayout.W * 0.5f, HERO_BOTTOM - HERO_TOP, 0.62f, 0f, true);
        int barItems = scene.items.size(), barHits = scene.hits.size();
        // the hero's text: the highlighted item's name, then its state text (a paragraph per line, an empty line between)
        int sel = page.column == null ? -1 : Math.max(0, Math.min(page.columnSelected, page.column.length - 1));
        scene.layer(Scene.CONTENT);
        if (sel >= 0) at(scene, page.column[sel].toUpperCase(Locale.ROOT), TEXT_LEFT, TITLE_BASE, MenuLayout.W / 2f, TITLE_PX, cond, game,
                Scene.LEFT, WHITE);
        List<String> lines = paragraph(page, measure, face);
        for (int i = 0; i < lines.size(); ++i) at(scene, lines.get(i), TEXT_LEFT, PARA_BASE + i * PARA_PITCH, TEXT_W, PARA_PX, face, game, Scene.LEFT, WHITE);
        // the tiles
        scene.layer(Scene.CHROME);
        int count = page.column == null ? 0 : Math.min(page.column.length, MAX_TILES_LOCKED);
        // 1.2.0: the pre-setup column has six tiles (Set Up Game, the three locked modes, Verify, Import): the row is narrowed to fit
        // the same width; five or fewer keep the template's size
        float fit = count > MAX_TILES ? (MenuLayout.W - 2 * TILE_LEFT) / ((count - 1) * TILE_PITCH + TILE_W) : 1f;   // (the last tile ends at the right margin)
        final float TILE_PITCH = LandingScenes.TILE_PITCH * fit, TILE_W = LandingScenes.TILE_W * fit, TILE_H = LandingScenes.TILE_H * fit;
        final float CAPTION_BASE = TILE_TOP + TILE_H - t(11f) + t(0.5f);
        for (int i = 0; i < count; ++i) {
            float tx = TILE_LEFT + i * TILE_PITCH;
            if (i == sel && page.columnLit) scene.rect(tx - FRAME, TILE_TOP - FRAME, TILE_W + 2 * FRAME, TILE_H + 2 * FRAME, WHITE);
            String art = page.columnArt != null ? page.columnArt[i] : null;
            if (art != null && measure.hasPicture(art)) scene.image(art, tx, TILE_TOP, TILE_W, TILE_H);
            else scene.rect(tx, TILE_TOP, TILE_W, TILE_H, GROUND);
            shade(scene, tx, TILE_TOP + TILE_H * 0.5f, TILE_W, TILE_H * 0.5f, 0f, 0.6f, false);   // under the caption (same departure)
            boolean enabled = page.columnEnabled == null || page.columnEnabled[i];
            if (!enabled) scene.rect(tx, TILE_TOP, TILE_W, TILE_H, DIMMED);
            if (page.columnLocked != null && i < page.columnLocked.length && page.columnLocked[i]) padlock(scene, tx + TILE_W / 2f, TILE_TOP + TILE_H * 0.42f, TILE_H * 0.36f);
            // (1.2.0: the caption scales with a narrowed row, and a caption still wider than its tile - the fallback face before the disc's
            //  condensed one exists - is set smaller until it fits, never past the tile)
            String caption = page.column[i].toUpperCase(Locale.ROOT);
            float room = TILE_W - 2 * CAPTION_PAD * fit, px = CAPTION_PX * fit, wide = measure.width(caption, px, cond);
            if (wide > room && wide > 0f) px *= room / wide;
            at(scene, caption, tx + CAPTION_PAD * fit, CAPTION_BASE, room, px, cond, game, Scene.LEFT, enabled ? WHITE : 0xFF9A9A9A);
            scene.hit(tx, TILE_TOP, TILE_W, TILE_H, MenuLayout.HIT_COLUMN + i);
        }
        // the hint line and the prompts: the list tabs' chip and icon rule (one prompt style on every tab)
        if (sel >= 0) at(scene, hint(page, sel), TEXT_LEFT - t(1f), HINT_BASE, MenuLayout.W / 2f, PARA_PX, face, game, Scene.LEFT, WHITE);
        EnhancedScenes.prompts(scene, page.prompts, touchIcons, measure, true);
        float down = Math.max(0, panelH - MenuLayout.H) / 2f;
        if (down > 0f) lower(scene, barItems, barHits, down);
        // the shared top tab bar last, at the panel's own top edge (EnhancedScenes.build draws it the same way, after its place())
        scene.layer(Scene.CHROME);
        if (page.tabs.length > 1) EnhancedScenes.tabBar(scene, page, measure);
        scene.panelW = MenuLayout.W;
        scene.panelH = panelH;
        scene.contentKey = "landing/" + page.tab + "/" + sel;
        return scene;
    }

    /** Below the bar everything moves down by `down`, and the hero band (the art and its shade, from HERO_TOP) grows by it. */
    static void lower(Scene scene, int fromItem, int fromHit, float down) {
        for (int i = 0; i < scene.items.size(); ++i) {
            Scene.Item it = scene.items.get(i);
            if (it.type == Scene.FILL || it.type == Scene.BACKDROP) continue;
            if (Math.abs(it.y - HERO_TOP) < 0.5f && Math.abs(it.y + it.h - HERO_BOTTOM) < 0.5f) { it.h += down; continue; }
            if (it.type == Scene.RECT && Math.abs(it.y - HERO_BOTTOM) < 0.5f && Math.abs(it.y + it.h - MenuLayout.H) < 0.5f && it.w >= MenuLayout.W - 0.5f) {
                it.y += down; it.h += down; continue;   // the black under the band reaches the panel's bottom
            }
            if (it.points != null) {   // a POLY / LINE (the icons' glyphs) lives in its points
                if (it.points.length > 1 && it.points[1] >= HERO_TOP - 0.5f) for (int p = 1; p < it.points.length; p += 2) it.points[p] += down;
                continue;
            }
            if (it.y >= HERO_TOP - 0.5f) it.y += down;
        }
        java.util.List<Scene.Hit> hits = new java.util.ArrayList<Scene.Hit>(scene.hits);
        scene.hits.clear();
        for (Scene.Hit h : hits) scene.hit(h.x, h.y >= HERO_TOP - 0.5f ? h.y + down : h.y, h.w, h.h, h.id);
    }

    /**
     * 1.2.0 (owner: a simple padlock over what is locked): our own vector drawing, white - the shackle an arch of lines, the body a box,
     * a dark keyhole - centred at (cx, cy), size = its height. Nothing from the disc, no picture file.
     */
    static void padlock(Scene scene, float cx, float cy, float size) {
        float bodyW = size * 0.72f, bodyH = size * 0.52f, bodyTop = cy + size / 2f - bodyH, r = bodyW * 0.30f, stroke = size * 0.11f;
        float arcCy = bodyTop, steps = 12;
        float[] arc = new float[2 * ((int) steps + 1) + 4];
        int n = 0;
        arc[n++] = cx - r; arc[n++] = bodyTop + stroke * 0.5f;
        for (int k = 0; k <= steps; ++k) {
            double a = Math.PI + Math.PI * k / steps;
            arc[n++] = cx + (float) (Math.cos(a) * r);
            arc[n++] = arcCy - size * 0.18f + (float) (Math.sin(a) * r);
        }
        arc[n++] = cx + r; arc[n++] = bodyTop + stroke * 0.5f;
        scene.line(WHITE, stroke, arc);
        scene.rect(cx - bodyW / 2f, bodyTop, bodyW, bodyH, WHITE);
        float hole = size * 0.09f;
        scene.rect(cx - hole / 2f, bodyTop + bodyH * 0.30f, hole, bodyH * 0.42f, 0xFF2B2F2C);
    }

    /** A black run from alpha a0 to a1 across the box (horizontal: left to right; else top to bottom). */
    static void shade(Scene scene, float x, float y, float w, float h, float a0, float a1, boolean horizontal) {
        scene.gradient(x, y, w, h, Math.round(a0 * 255f) << 24, Math.round(a1 * 255f) << 24, horizontal);
    }

    /** The paragraph: each info line wrapped to the template's column, an empty line between them; at most PARA_LINES lines. */
    static List<String> paragraph(MenuScenes.Page page, Scene.Measure measure, int face) {
        List<String> out = new ArrayList<String>();
        for (MenuScenes.Line part : page.info) {
            if (part.text == null || part.text.isEmpty()) continue;
            if (!out.isEmpty()) out.add("");
            out.addAll(Scene.wrap(part.text, PARA_PX, face, TEXT_W, measure));
        }
        if (out.size() > PARA_LINES) {
            List<String> cut = new ArrayList<String>(out.subList(0, PARA_LINES));
            cut.set(PARA_LINES - 1, Scene.fit(cut.get(PARA_LINES - 1) + " " + Scene.ELLIPSIS + Scene.ELLIPSIS, PARA_PX, face, TEXT_W, measure));
            return cut;
        }
        return out;
    }

    /** On the tab bar (2026-11-08: the menu opens there): "Select to choose a mode". On a tile: "Select to start Story Mode", or why the
     *  highlighted item cannot be started (its last warning line). */
    static final String BAR_HINT = "Select to choose a mode";
    static String hint(MenuScenes.Page page, int sel) {
        if (!page.columnLit) return BAR_HINT;
        boolean enabled = page.columnEnabled == null || page.columnEnabled[sel];
        if (enabled) return "Select to start " + page.column[sel];
        for (int i = page.info.size() - 1; i >= 0; --i) if (page.info.get(i).tone == MenuScenes.TONE_WARNING) return page.info.get(i).text;
        return page.column[sel] + " cannot be started yet";
    }

    /**
     * A text whose baseline is at `base`: the painter centres a text on its face's ascent and descent, so the box is placed for it
     * (Chalet London 245 / 64, Comprimé 223 / 50 of 256; Nunito Sans 1.011 / 0.353 of the size without the disc's faces).
     */
    static void at(Scene scene, String text, float x, float base, float w, float size, int face, boolean game, int align, int color) {
        float half = face == Scene.GAME_COND && game ? (223f - 50f) / 512f : game ? (245f - 64f) / 512f : (1.011f - 0.353f) / 2f;
        float h = size * 1.3f;
        scene.textExact(text, x, base - half * size - h / 2f, w, h, size, face, align, color);
    }
}
