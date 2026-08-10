#!/usr/bin/env python3
"""
Repoint Jellyfin's encoder config from NVENC to Intel QSV.

Written for the 2026-08-10 move off the RTX 4080 (WSL) onto the NAS, whose only
GPU is an Intel Iris Xe (Raptor Lake-P). Jellyfin keeps hardware settings in
encoding.xml, and a config that names `nvenc` on a box with no NVIDIA device
does not error — it silently falls back to software transcoding, which on this
CPU means a 4K HDR stream simply cannot keep up. So this has to be changed
deliberately rather than left to the UI.

What Raptor Lake-P can and cannot do, since the answer is not symmetric:

  decode   H.264, HEVC 8/10-bit, VP9, AV1
  encode   H.264, HEVC 8/10-bit  —  NO AV1 ENCODE

AV1 encode arrived on Intel with Arc (DG2) and Meteor Lake. Raptor Lake-P is
Xe-LP and decodes AV1 only, so AllowAv1Encoding stays false.

Usage:  switch-jellyfin-qsv.py <path-to-encoding.xml> [--dry-run]
"""
import re
import shutil
import sys

# (element, new value, why)
CHANGES = [
    ("HardwareAccelerationType", "qsv",
     "nvenc -> qsv; the whole point of the change"),
    ("QsvDevice", "/dev/dri/renderD128",
     "was empty; QSV needs the render node named explicitly"),
    ("EnableEnhancedNvdecDecoder", "false",
     "NVDEC is NVIDIA-only and meaningless here"),
    ("EnableVppTonemapping", "true",
     "Intel's fixed-function tonemapper. On Intel this is far cheaper than the "
     "OpenCL path, and HDR->SDR is the most expensive thing this iGPU will do"),
    ("DeinterlaceMethod", "bwdif",
     "bwdif beats yadif and is the QSV-accelerated one — matters for the 1080i "
     "Peaky Blinders remux, the file that forced transcoding in the first place"),
]

# Left deliberately alone, recorded so the next reader does not 'fix' them:
#   AllowAv1Encoding      false  — hardware cannot do it (see above)
#   EncoderPreset         slow   — a preset tuned against a 4080. If transcodes
#                                  fall behind on the Iris Xe, this is the first
#                                  knob to loosen, but change it on evidence.
#   EnableIntelLowPower*  false  — usually a win on Intel, occasionally breaks
#                                  depending on driver. Enable after QSV is
#                                  confirmed working, not at the same time.


def patch(path, dry_run=False):
    with open(path, encoding="utf-8") as fh:
        xml = fh.read()

    changed = []
    for element, value, why in CHANGES:
        # Matches both <E>old</E> and the self-closing <E /> form Jellyfin uses
        # for unset values.
        pair = re.compile(rf"<{element}>(.*?)</{element}>", re.S)
        empty = re.compile(rf"<{element}\s*/>")

        m = pair.search(xml)
        if m:
            if m.group(1) == value:
                continue
            xml = pair.sub(f"<{element}>{value}</{element}>", xml, count=1)
            changed.append((element, m.group(1), value, why))
        elif empty.search(xml):
            xml = empty.sub(f"<{element}>{value}</{element}>", xml, count=1)
            changed.append((element, "(empty)", value, why))
        else:
            print(f"  ! {element} not present — skipped", file=sys.stderr)

    for element, old, new, why in changed:
        print(f"  {element}\n      {old!r} -> {new!r}\n      {why}")

    if not changed:
        print("  already QSV — nothing to do")
        return 0
    if dry_run:
        print(f"\n[dry-run] {len(changed)} change(s) not written")
        return 0

    shutil.copy2(path, path + ".nvenc.bak")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(xml)
    print(f"\nwrote {path}  (original kept at {path}.nvenc.bak)")
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        sys.exit(2)
    sys.exit(patch(args[0], "--dry-run" in sys.argv))
