FFmpeg's libavcodec/wmaprodec.c, changed by this project (audit fix COR-02, 2026-11-16): a flush callback for the raw-frames
XMA decoder (ff_xmaframes_decoder had none), so that a flushed decoder starts the next sound as a newly opened one does.
Without it a reset decoder mixed the previous sound's leftover into the first 320 samples of the next (7,996 of 9,300
comparisons on the owner's disc audio; 9,300 of 9,300 bit-identical with the change).

- libavcodec/wmaprodec.c: the changed file (LGPL version 2.1 or later, as FFmpeg; revision 15ece0882e8d of
  https://github.com/xenia-project/FFmpeg, the revision the XMA stage builds). SHA-256 fb2f1fd97ad3072b...
- xmaframes-flush.patch: the whole difference from the revision's file.

How the builder uses it (lead's decision 2026-11-24, "option A"): the XMA stage still compiles the revision's own file into
libavcodec.a, unchanged. The game library links this changed file's object as one extra unit, compiled in the player's run
from this file with the XMA plan's own command (xma_extra_unit.py); the object must be byte-equal to the one the library was
made with (08322eee... for the Android plan), or the build refuses.
