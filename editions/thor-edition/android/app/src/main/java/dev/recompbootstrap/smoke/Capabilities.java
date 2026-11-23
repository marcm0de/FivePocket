package dev.recompbootstrap.smoke;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Set;
import java.util.TreeSet;

/**
 * Which setting values the installed game build can honour (design section 3.3). Pure Java, immutable.
 *
 * The knowledge comes from the names of the input files the game library contains (CapabilityScanner finds them).
 * A default value is always supported. With no game library nothing but the defaults is supported.
 */
final class Capabilities {
    /**
     * Marker strings. A marker is not a file: it is a text a game build carries (zero-terminated, like a file name)
     * to announce a feature that a file name alone cannot prove.
     * GSR1_MARKER: the build contains the Snapdragon GSR 1 pass AND takes the word gsr1 from settings/scaler.txt
     * (in the library from v462: direct_present.h, the RENDER_DIRECT_GSR log format ends with it).
     * WORLD_LOD_MARKER: the build honours draw_distance in world-load.txt without a developer file (proposed by lane
     * world-load, SETTINGS-SCREEN.md section 6; in no build yet).
     * FRAME_GENERATION_MARKER: the build contains the picture generator of frame-generation stage 1b and is meant to be
     * switched by this screen (local-analysis/agents/frame-gen/DESIGN-stage1.md sections 1, 6 and 7): it makes a second
     * picture per drawn frame while files/private-trace/diag-fg-between or diag-fg-ahead exists (both: between) and
     * the pacer's fixed 30 fps cap (diag-pace-30) paces the game; with neither file every decision is as without the
     * generator. No build carries it yet (v464 holds only the timing dry run, diag-fg-pair-ahead / diag-fg-pair-between,
     * which makes no picture and is deliberately not looked at). The marker alone enables nothing: a value is offered
     * only when the build also contains its switch file's name and the name of the 30 fps cap file.
     * RENDER_STAGE_B_MARKER: the build shows a render size as it is drawn (render-resolution stage B, in the library
     * from v464: the end of a RENDER_DIRECT_SOURCE log format). With FSR 1 or GSR 1 the scaler works from the render
     * size, and exactly 1920x1080 on a 1920x1080 screen is shown 1:1 with no scaler and no sharpening. A build
     * without it (v463) brings every render size to 1280x720 first.
     */
    static final String GSR1_MARKER = "recomp-cap:gsr1";
    static final String WORLD_LOD_MARKER = "recomp-cap:wl-lod";
    static final String FRAME_GENERATION_MARKER = "recomp-cap:framegen1";
    static final String RENDER_STAGE_B_MARKER = "recomp-cap:render-stage-b";
    /**
     * Update 1 haptics: the export the app's haptics thread waits in (rumble.h; a library exports it together with
     * recomp_rumble_bind_v1, rumble_port.h needs both). An exported name is a zero-terminated string of the library's .dynstr, so the
     * scan finds it like a file name. Without it the Vibration row still works for the buttons' tick, and says the build has
     * no rumble (SettingsSchema.hint(key, value, capabilities)).
     */
    static final String RUMBLE_EXPORT = "recomp_rumble_wait_v1";
    /** Update 1 (the in-game creator, trainer 17 4): one of its refusal words, a literal of its own in creator_menu.h, so in every
     *  library that has the menu and in none before it (checked v963..v972). */
    static final String CREATOR_MENU_MARKER = "menu-already-open";
    /** Update 1 (ALLOCATION: Quick Menu Style): the game library's Interaction Menu export, int recomp_imenu_v1(int op); the side
     *  menu's Quick Menu Style row exists only with it (SideMenuRows). */
    static final String INTERACTION_MENU_EXPORT = "recomp_imenu_v1";
    /** Update 1 touch (TOUCH-DESIGN.md item 3): the game's own targeting modes, int recomp_aim_assist_v1(int mode); the Aim Assist row
     *  exists only with it. */
    static final String AIM_ASSIST_EXPORT = "recomp_aim_assist_v1";
    /** Update 1 touch (TOUCH-DESIGN.md item 4): void recomp_camera_turn_v1(float dYawDeg, float dPitchDeg), the gameplay camera turned
     *  directly; the camera rows exist only with it (without it the right side stays the right-stick swipe). */
    static final String CAMERA_TURN_EXPORT = "recomp_camera_turn_v1";
    /** Update 1 P6: a game library with My Music exports recomp_my_music_v1 (its name stands in the library's symbol table). */
    static final String MY_MUSIC_MARKER = "recomp_my_music_v1";
    /** Thor edition: the word of second-screen.txt that keeps the game's own weapon wheel (a build with the lower-screen wheel holds it). */
    static final String WHEEL_MAIN_WORD = "wheel=main";
    static final String RADIO_MAIN_WORD = "radio=main"; // (Thor edition, thor-radio: game builds from v651)
    static final String PHONE_MAIN_WORD = "phone=main"; // (Thor edition, thor-radio: game builds from v815)
    static final String CHARACTER_MAIN_WORD = "character=main"; // (Thor edition, thor-char: game builds from the character wheel attempt on)

    static final String REASON_NO_LIBRARY = "Game code is not installed";
    static final String REASON_NOT_IN_BUILD = "Not in the installed game build";
    static final String REASON_NO_UNCAPPED = "No game build can run without a frame cap yet";
    static final String REASON_INPUT = "Needs Android 11 or newer and an app build that has this option";
    static final String REASON_NOT_A_VALUE = "Not a value of this setting";
    static final String REASON_NOT_BUILT = "Not in this version";
    static final String REASON_NEEDS_463 = REASON_NOT_IN_BUILD + " (needs game build v463 or newer)";
    static final String REASON_LOD_LOCKED = "Not in this version";

    private final TreeSet<String> names;
    private final boolean libraryPresent;
    private final boolean inputOptionInApp;

    private Capabilities(Set<String> names, boolean libraryPresent, boolean inputOptionInApp) {
        this.names = new TreeSet<String>(names);
        this.libraryPresent = libraryPresent;
        this.inputOptionInApp = inputOptionInApp;
    }

    /**
     * namesInLibrary: the probe names found in the game library (after the lead's override file was applied).
     * libraryPresent: false when there is no game library; the names are then ignored.
     * inputOptionInApp: the low-delay input option exists in this app build and the device runs Android 11 or newer.
     */
    static Capabilities of(Set<String> namesInLibrary, boolean libraryPresent, boolean inputOptionInApp) {
        return new Capabilities(libraryPresent ? namesInLibrary : Collections.<String>emptySet(), libraryPresent, inputOptionInApp);
    }

    /** True when the game library contains that name (a base name such as "diag-pace-40", or the GSR 1 marker). */
    boolean has(String name) {
        return names.contains(name);
    }

    /** has() for a path of RuntimeMapping, e.g. "private-trace/diag-pace-40". */
    boolean reads(String ownedPath) {
        return has(RuntimeMapping.name(ownedPath));
    }

    /** IllegalArgumentException for an unknown key. A value the setting cannot hold is not supported. */
    boolean valueSupported(String key, int value) {
        return reason(key, value) == null;
    }

    /** null when the value is supported; otherwise the one-line reason shown under the row. */
    String reason(String key, int value) {
        SettingSpec spec = SettingsSchema.spec(key);
        if (!spec.valid(value)) return REASON_NOT_A_VALUE;
        if (value == spec.defaultValue) return null;
        if (!libraryPresent) return REASON_NO_LIBRARY;
        if (key.equals(SettingsSchema.PERFORMANCE_PRESET)) return PerformancePresets.reason(this, value);
        if (key.equals(SettingsSchema.MY_MUSIC) || key.equals(SettingsSchema.MY_MUSIC_VOLUME) || key.equals(SettingsSchema.MY_MUSIC_ORDER)) {
            return has(MY_MUSIC_MARKER) ? null : REASON_NOT_IN_BUILD; // (Update 1 P6: read by this app; needs the library's My Music)
        }
        if (key.equals(SettingsSchema.FRAME_CAP)) {
            if (value == SettingsSchema.CAP_OFF) return REASON_NO_UNCAPPED;
            if (value == SettingsSchema.CAP_30) {
                return reads(RuntimeMapping.PACE_30) || reads(RuntimeMapping.LIMIT30) ? null : needs(460);
            }
            String file = value == SettingsSchema.CAP_40 ? RuntimeMapping.PACE_40
                    : value == SettingsSchema.CAP_24 ? RuntimeMapping.PACE_24 : RuntimeMapping.PACE_AUTO;
            return reads(file) ? null : needs(460);
        }
        if (key.equals(SettingsSchema.SCALER)) {
            // Nearest is "no scaler input says otherwise": a build that reads no scaler input at all cannot be told.
            if (value == SettingsSchema.SCALER_NEAREST) return scalerInputs() ? null : REASON_NOT_IN_BUILD;
            if (value == SettingsSchema.SCALER_BICUBIC) {
                return reads(RuntimeMapping.SCALER_TXT) || reads(RuntimeMapping.SCALE_CUBIC) ? null : REASON_NOT_IN_BUILD;
            }
            return reads(RuntimeMapping.SCALER_TXT) && has(GSR1_MARKER) ? null : needs(462);
        }
        if (key.equals(SettingsSchema.FSR_SHARPNESS)) {
            return reads(RuntimeMapping.SHARPNESS_TXT) || reads(RuntimeMapping.FSR1_STOPS) ? null : REASON_NOT_IN_BUILD;
        }
        if (key.equals(SettingsSchema.RENDER_SCALE)) {
            return reads(RuntimeMapping.RENDER_SCALE_TXT) ? null : REASON_NEEDS_463;
        }
        if (key.equals(SettingsSchema.FRAME_GENERATION)) {
            // Live only in a build that announces its generator; the dry-run names prove nothing (see the marker's text).
            if (!has(FRAME_GENERATION_MARKER)) return REASON_NOT_BUILT;
            String file = value == SettingsSchema.FG_BETWEEN ? RuntimeMapping.FG_BETWEEN : RuntimeMapping.FG_AHEAD;
            // The generator makes its pictures only under the pacer's 30 fps cap, so that file must be in the build too.
            return reads(file) && reads(RuntimeMapping.PACE_30) ? null : REASON_NOT_IN_BUILD;
        }
        if (key.equals(SettingsSchema.FPS_OVERLAY)) {
            return reads(RuntimeMapping.FPS_OVERLAY) ? null : REASON_NOT_IN_BUILD;
        }
        if (key.equals(SettingsSchema.INPUT_UNBUFFERED)) {
            return inputOptionInApp ? null : REASON_INPUT;
        }
        if (key.equals(SettingsSchema.HAPTICS)) {
            // Update 1 haptics: the app's own row, live in any build for the buttons' tick; the game's rumble only with a library that
            // exports RUMBLE_EXPORT (rumble()), and the row's text says which (SettingsSchema.hint(key, value, capabilities))
            return null;
        }
        if (key.equals(SettingsSchema.WEAPON_WHEEL)) {
            // Thor edition: "main" is a word of second-screen.txt that a game build with the lower-screen wheel contains (v605+)
            return has(WHEEL_MAIN_WORD) && reads(RuntimeMapping.SECOND_SCREEN_TXT) ? null : REASON_NOT_IN_BUILD;
        }
        if (key.equals(SettingsSchema.RADIO_WHEEL)) {
            // Thor edition: "main" is a word of second-screen.txt that a game build with the lower-screen radio wheel contains (v651+)
            return has(RADIO_MAIN_WORD) && reads(RuntimeMapping.SECOND_SCREEN_TXT) ? null : REASON_NOT_IN_BUILD;
        }
        if (key.equals(SettingsSchema.CHARACTER_WHEEL)) {
            // Thor edition: "main" is a word of second-screen.txt that a game build with the lower-screen character wheel contains (v651+)
            return has(CHARACTER_MAIN_WORD) && reads(RuntimeMapping.SECOND_SCREEN_TXT) ? null : REASON_NOT_IN_BUILD;
        }
        if (key.equals(SettingsSchema.PHONE_SCREEN)) {
            // Thor edition (v826): "main" is a word of second-screen.txt that a game build with the lower-screen phone contains (v815+)
            return has(PHONE_MAIN_WORD) && reads(RuntimeMapping.SECOND_SCREEN_TXT) ? null : REASON_NOT_IN_BUILD;
        }
        if (key.equals(SettingsSchema.PHONE_BACKGROUND)) {
            return reads(RuntimeMapping.PHONE_POV_TXT) ? null : REASON_NOT_IN_BUILD; // (library v824+: phone_pov.h)
        }
        if (key.equals(SettingsSchema.SCRIPT_MODS)) {
            return reads(RuntimeMapping.MODS_TXT) ? null : REASON_NOT_IN_BUILD; // (script-mods: the library reads settings/mods.txt)
        }
        if (key.equals(SettingsSchema.LANGUAGE)) {
            return reads(RuntimeMapping.LANGUAGE_TXT) ? null : REASON_NOT_IN_BUILD; // (1.2.1: the library reads settings/language.txt, v1511+)
        }
        if (key.equals(SettingsSchema.LOWER_SCREEN)) {
            // Thor edition (v820): read by this app; it matters only with a game build that has the lower screen (second-screen.txt)
            return reads(RuntimeMapping.SECOND_SCREEN_TXT) ? null : REASON_NOT_IN_BUILD;
        }
        if (key.equals(SettingsSchema.MINIMAP_SCREENS)) {
            // Thor edition: "top" is read by this app (it opens no lower-screen HUD), which matters only with a game build that has the
            // lower screen (it reads second-screen.txt); "bottom" needs a game build that reads minimap.txt.
            return reads(value == SettingsSchema.MINIMAP_TOP ? RuntimeMapping.SECOND_SCREEN_TXT : RuntimeMapping.MINIMAP_TXT) ? null : REASON_NOT_IN_BUILD;
        }
        // the world sliders share one input file; draw distance is also locked inside the runtime
        if (!reads(RuntimeMapping.WORLD_LOAD_TXT)) return needs(461);
        if (key.equals(SettingsSchema.WORLD_DRAW_DISTANCE)) return has(WORLD_LOD_MARKER) ? null : REASON_LOD_LOCKED;
        return null;
    }

    /** True when the build reads any input that chooses the scaler (the word file or one of the two switch files). */
    boolean scalerInputs() {
        return reads(RuntimeMapping.SCALER_TXT) || reads(RuntimeMapping.SCALE_FSR1) || reads(RuntimeMapping.SCALE_CUBIC);
    }

    private static String needs(int build) {
        return REASON_NOT_IN_BUILD + " (needs game build v" + build + " or newer)";
    }

    /** True when the build reads render-scale.txt and shows the render size as it is drawn (RENDER_STAGE_B_MARKER). */
    boolean renderStageB() {
        return has(RENDER_STAGE_B_MARKER) && reads(RuntimeMapping.RENDER_SCALE_TXT);
    }

    /**
     * True when the row offers the value as a step. Every supported value is offered, with one exception: in a
     * stage B build the Render Resolution row has five named modes, and 50 % and 125 % are not among them. Those two
     * stay supported - the build honours them, so a saved one is shown and written as it is - but the row does not
     * step onto them.
     */
    boolean offered(String key, int value) {
        if (!valueSupported(key, value)) return false;
        // (Custom is what the rows hold: shown, never a step of the preset row - the rows themselves change it)
        if (key.equals(SettingsSchema.PERFORMANCE_PRESET) && value == PerformancePresets.CUSTOM) return false;
        return !(key.equals(SettingsSchema.RENDER_SCALE) && renderStageB() && !SettingsSchema.renderMode(value));
    }

    /** The value itself when it is supported, otherwise the setting's default. */
    int effective(String key, int value) {
        return valueSupported(key, value) ? value : SettingsSchema.spec(key).defaultValue;
    }

    /** True when at least one non-default value is supported (false: the row is disabled). */
    boolean anyChoice(String key) {
        SettingSpec spec = SettingsSchema.spec(key);
        for (int value = spec.min; value <= spec.max; value += spec.step) {
            if (value != spec.defaultValue && valueSupported(key, value)) return true;
        }
        return false;
    }

    /** Update 1 haptics: the game library exports the rumble wait (RUMBLE_EXPORT): the game's rumble reaches the device's motor. */
    boolean rumble() {
        return has(RUMBLE_EXPORT);
    }

    /** Update 1: the game library has the in-game creator (trainer 17 4, state bit 22): the Online page offers Open Creator. */
    boolean creatorMenu() {
        return has(CREATOR_MENU_MARKER);
    }

    /** Update 1: the game library has the Interaction Menu (INTERACTION_MENU_EXPORT). */
    /** The swipe camera is offered only once a library's camera turn has passed its device check (main 2026-11-07: v988's pitch had no
     *  effect, a combined turn was refused). Off for Update 1 until then; the tests switch it on to check the rows and the path. */
    static boolean cameraTurnReleased = false;

    boolean cameraTurn() {
        return cameraTurnReleased && has(CAMERA_TURN_EXPORT);
    }

    boolean aimAssist() {
        return has(AIM_ASSIST_EXPORT);
    }

    boolean interactionMenu() {
        return has(INTERACTION_MENU_EXPORT);
    }

    /** The names found in the game library, sorted (for the About screen). Unmodifiable. */
    List<String> names() {
        return Collections.unmodifiableList(new ArrayList<String>(names));
    }
}
