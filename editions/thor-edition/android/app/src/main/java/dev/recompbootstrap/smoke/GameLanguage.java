package dev.recompbootstrap.smoke;

import java.nio.charset.StandardCharsets;

/**
 * The game's language (owner 2026-11-22, the Settings row "Language"; SettingsSchema.LANGUAGE). The game library reads it when the
 * game starts (since v1511, game_language.h): settings/language.txt holds ONE word, the name of one of the disc's text containers
 * (xbox360a.rpf data/lang/<word>.rpf). No file, an empty file or an unknown word = "american" (English), so English is saved as no
 * file. A change takes effect at the next game start.
 *
 * IDS are game_language.h's Choices in the same order (the game's own enum order; japanese is not on the disc and is not offered).
 * parse() reads a word the way game_language.h's parse() does, so the row always shows the language the game will use.
 */
final class GameLanguage {
    static final String FILE = "settings/language.txt";
    static final String[] IDS = {"american", "french", "german", "italian", "spanish", "portuguese", "polish", "russian", "korean",
            "chinese", "mexican"};
    /** Each language in its own name (what a player of that language looks for). */
    static final String[] LABELS = {"English", "Français", "Deutsch", "Italiano", "Español (España)", "Português",
            "Polski", "Русский", "한국어", "中文", "Español (México)"};
    static final int DEFAULT = 0;
    /** The row's text: when a change applies. */
    static final String HINT = "Takes effect at the next game start.";

    private GameLanguage() {
    }

    /**
     * The choice a FILE content names, as game_language.h parse() reads it: leading spaces, tabs and line ends skipped, then the
     * first word (up to a space, tab, line end or NUL), in any ASCII case; an empty or missing file, or a word that names no
     * container: DEFAULT. (The header also refuses words of 16+ characters or with other than letters; no such word names a
     * container, so the answer is the same.)
     */
    static int parse(byte[] content) {
        if (content == null) return DEFAULT;
        int at = 0;
        while (at < content.length && blank(content[at])) ++at;
        StringBuilder word = new StringBuilder();
        for (; at < content.length && content[at] != 0 && !blank(content[at]); ++at) {
            int c = content[at] & 0xFF;
            word.append((char) (c >= 'A' && c <= 'Z' ? c - 'A' + 'a' : c));
        }
        String w = word.toString();
        for (int i = 0; i < IDS.length; ++i) {
            if (IDS[i].equals(w)) return i;
        }
        return DEFAULT;
    }

    /** FILE's content for a choice: null (no file) for DEFAULT and for anything that is not a choice, else the word and a line end. */
    static byte[] content(int index) {
        return index > DEFAULT && index < IDS.length ? (IDS[index] + "\n").getBytes(StandardCharsets.US_ASCII) : null;
    }

    private static boolean blank(byte b) {
        return b == ' ' || b == '\t' || b == '\r' || b == '\n';
    }
}
