package dev.recompbootstrap.smoke;

import java.io.File;
import java.io.IOException;
import java.io.RandomAccessFile;
import java.util.ArrayList;
import java.util.Arrays;

/**
 * Which characters a TrueType file has (its 'cmap' table), so that a menu text the player's disc font cannot draw is drawn in the app's
 * own face instead (owner 2026-11-22, the Language row: the disc's menu font, font_lib_efigs, has Latin and Cyrillic but no Korean or
 * Chinese). Reads Unicode subtables of format 4 (BMP) and 12 (all planes); any other subtable is skipped. Pure Java; never throws:
 * an unreadable or malformed file gives null (nothing known, the caller keeps its face).
 */
final class FontCoverage {
    /** Sorted, non-overlapping inclusive ranges: start0, end0, start1, end1, ... */
    private final int[] ranges;

    private FontCoverage(int[] ranges) {
        this.ranges = ranges;
    }

    /** The coverage of a font file, or null when the file is missing, too large or not a TrueType font with a Unicode cmap. */
    static FontCoverage read(File file) {
        if (file == null || !file.isFile() || file.length() > 32L << 20) return null;
        try (RandomAccessFile in = new RandomAccessFile(file, "r")) {
            byte[] data = new byte[(int) in.length()];
            in.readFully(data);
            return parse(data);
        } catch (IOException | RuntimeException e) {
            return null;
        }
    }

    /** The coverage of a font's bytes, or null (see read). */
    static FontCoverage parse(byte[] d) {
        try {
            int tables = u16(d, 4);
            int cmap = -1;
            for (int i = 0; i < tables; ++i) {
                int rec = 12 + 16 * i;
                if (d[rec] == 'c' && d[rec + 1] == 'm' && d[rec + 2] == 'a' && d[rec + 3] == 'p') cmap = (int) u32(d, rec + 8);
            }
            if (cmap < 0) return null;
            ArrayList<int[]> found = new ArrayList<int[]>();
            boolean any = false;
            int subtables = u16(d, cmap + 2);
            for (int i = 0; i < subtables; ++i) {
                int platform = u16(d, cmap + 4 + 8 * i), encoding = u16(d, cmap + 6 + 8 * i);
                boolean unicode = platform == 0 || (platform == 3 && (encoding == 1 || encoding == 10));
                if (!unicode) continue;
                int at = cmap + (int) u32(d, cmap + 8 + 8 * i);
                int format = u16(d, at);
                if (format == 4) {
                    int segments = u16(d, at + 6) / 2;
                    for (int s = 0; s < segments; ++s) {
                        int end = u16(d, at + 14 + 2 * s), start = u16(d, at + 16 + 2 * segments + 2 * s);
                        if (start == 0xFFFF || start > end) continue;
                        found.add(new int[] {start, end});
                    }
                    any = true;
                } else if (format == 12) {
                    long groups = u32(d, at + 12);
                    if (groups > (d.length - at - 16) / 12) return null;
                    for (int g = 0; g < groups; ++g) {
                        long start = u32(d, at + 16 + 12 * g), end = u32(d, at + 20 + 12 * g);
                        if (start > end || end > 0x10FFFF) continue;
                        found.add(new int[] {(int) start, (int) end});
                    }
                    any = true;
                }
            }
            if (!any) return null;
            return new FontCoverage(merge(found));
        } catch (RuntimeException malformed) {
            return null;
        }
    }

    /** True when every character of text other than spaces and line ends is in the font. */
    boolean covers(String text) {
        if (text == null) return true;
        for (int i = 0; i < text.length(); ) {
            int c = text.codePointAt(i);
            i += Character.charCount(c);
            if (c == ' ' || c == '\n' || c == '\r' || c == '\t') continue;
            if (!has(c)) return false;
        }
        return true;
    }

    boolean has(int codePoint) {
        int lo = 0, hi = ranges.length / 2 - 1;
        while (lo <= hi) {
            int mid = (lo + hi) >>> 1;
            if (codePoint < ranges[2 * mid]) hi = mid - 1;
            else if (codePoint > ranges[2 * mid + 1]) lo = mid + 1;
            else return true;
        }
        return false;
    }

    private static int[] merge(ArrayList<int[]> found) {
        int[][] all = found.toArray(new int[0][]);
        Arrays.sort(all, (a, b) -> Integer.compare(a[0], b[0]));
        int[] out = new int[all.length * 2];
        int n = 0;
        for (int[] r : all) {
            if (n > 0 && r[0] <= out[n - 1] + 1) {
                out[n - 1] = Math.max(out[n - 1], r[1]);
            } else {
                out[n++] = r[0];
                out[n++] = r[1];
            }
        }
        return Arrays.copyOf(out, n);
    }

    private static int u16(byte[] d, int at) {
        return ((d[at] & 0xFF) << 8) | (d[at + 1] & 0xFF);
    }

    private static long u32(byte[] d, int at) {
        return ((long) u16(d, at) << 16) | u16(d, at + 2);
    }
}
