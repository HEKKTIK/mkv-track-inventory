#!/usr/bin/env python3
"""
scan.py - read the headers of every video file in a library, write one CSV.

Nothing is extracted, decoded or changed. For each file mkvmerge is asked what
is inside; that reads the header and a little of the index, not the stream, so
a 60 GB remux costs about as much as a 600 MB episode.

One row per track. File-level values are repeated on each of its rows so the
CSV can be read on its own without a join.

Resumable: run it again and it carries on, skipping files already in the CSV.
"""

import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
import time

VIDEO_SUFFIXES = {".mkv", ".mp4", ".m4v", ".avi", ".ts", ".m2ts", ".mov", ".webm"}

# Release groups put their tag last, after a dash: "...1080p.BluRay.x264-SPARKS"
RE_GROUP = re.compile(r"-([A-Za-z0-9]{2,20})$")
# Source and encoder words a scene release carries and a hand-made rip does not
RE_SOURCE = re.compile(
    r"\b(BluRay|BDRip|BRRip|WEB-?DL|WEBRip|HDTV|DVDRip|REMUX|UHD|HDR|x264|x265|"
    r"H\.?26[45]|HEVC|AVC|DDP?5\.1|DTS(-HD)?|TrueHD|Atmos|AAC|1080p|2160p|720p)\b",
    re.I)
# "Season 2", "S02E05", "2x05" - the usual ways an episode announces itself
RE_SERIES = re.compile(r"(\bS\d{1,2}[ ._-]?E\d{1,3}\b|\bSeason[ ._-]?\d{1,2}\b|"
                       r"\bStaffel[ ._-]?\d{1,2}\b|\b\d{1,2}x\d{2}\b)", re.I)

# name, the keys mkvmerge may use, and what it means when the key is missing.
# Every flag defaults to off in Matroska except enabled, which defaults to on.
FLAGS = [
    ("default", ("default_track",), "0"),
    ("forced", ("forced_track",), "0"),
    ("hearing_impaired", ("flag_hearing_impaired",), "0"),
    ("visual_impaired", ("flag_visual_impaired",), "0"),
    ("text_descriptions", ("flag_text_descriptions",), "0"),
    ("original", ("flag_original",), "0"),
    ("commentary", ("flag_commentary",), "0"),
    ("enabled", ("enabled_track",), "1"),
]

COLUMNS = [
    # file level
    "path", "folder", "filename", "looks_like", "size_mb", "duration_s",
    "container_title", "writing_app", "muxing_app",
    "release_group", "source_tags", "n_video", "n_audio", "n_subtitle",
    # track level
    "track_id", "track_number", "type", "codec", "codec_id",
    "language", "language_ietf", "name",
    "default", "forced", "hearing_impaired", "visual_impaired",
    "text_descriptions", "original", "commentary", "enabled",
    "audio_channels", "audio_rate", "audio_bits",
    "video_dimensions", "text_subtitles",
]


def walk(roots, problems):
    """
    Yields video files. os.walk hides permission errors by default, which
    turns an unreadable mount into a silent "0 files" - so they are collected
    and reported instead.
    """
    for root in roots:
        if not os.path.exists(root):
            problems.append(f"{root}: no such folder inside the container - "
                            f"check what the library is mounted as")
            continue
        if os.path.isfile(root):
            yield root
            continue
        if not os.access(root, os.R_OK | os.X_OK):
            problems.append(f"{root}: not readable by uid {os.getuid()}:"
                            f"{os.getgid()} - the container is running as a "
                            f"user the share does not let in")
            continue
        for base, dirs, names in os.walk(root, onerror=problems.append):
            dirs.sort()
            for n in sorted(names):
                if os.path.splitext(n)[1].lower() in VIDEO_SUFFIXES:
                    yield os.path.join(base, n)


def identify(path, timeout):
    """mkvmerge -J, or None with a reason."""
    try:
        p = subprocess.run(["mkvmerge", "-J", path],
                           capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except OSError as exc:
        return None, f"cannot run mkvmerge: {exc}"
    if not p.stdout:
        return None, (p.stderr.decode("utf-8", "replace").strip()[:120]
                      or f"exit {p.returncode}, no output")
    try:
        info = json.loads(p.stdout.decode("utf-8", "replace"))
    except json.JSONDecodeError as exc:
        return None, f"unreadable output: {exc}"
    # mkvmerge answers for anything handed to it; this is how it says no.
    if not info.get("container", {}).get("recognized"):
        reasons = info.get("errors") or ["container not recognised"]
        return None, str(reasons[0])[:120]
    return info, None


def flag_of(props, keys, absent):
    """mkvmerge only prints these when they are set, so absent means not set."""
    for k in keys:
        if k in props:
            return "1" if props[k] else "0"
    return absent


def guess_kind(path):
    """movie or series, from the path. A guess, not a verdict."""
    return "series" if RE_SERIES.search(path) else "movie"


def rows_for(path, info, anonymise):
    container = info.get("container", {})
    cprops = container.get("properties", {})
    tracks = info.get("tracks", [])
    counts = {"video": 0, "audio": 0, "subtitles": 0}
    for t in tracks:
        counts[t.get("type", "")] = counts.get(t.get("type", ""), 0) + 1

    filename = os.path.basename(path)
    folder = os.path.basename(os.path.dirname(path))
    stem = os.path.splitext(filename)[0]
    group = RE_GROUP.search(stem)
    tags = sorted({m.group(0).lower() for m in RE_SOURCE.finditer(stem)})

    if anonymise:
        digest = hashlib.sha256(path.encode("utf-8")).hexdigest()[:16]
        shown_path, folder, filename = digest, "", ""

    else:
        shown_path = path

    try:
        size_mb = round(os.path.getsize(path) / 1e6, 1)
    except OSError:
        size_mb = ""

    duration = cprops.get("duration")
    base = {
        "path": shown_path,
        "folder": folder,
        "filename": filename,
        "looks_like": guess_kind(path),
        "size_mb": size_mb,
        "duration_s": round(duration / 1e9, 1) if duration else "",
        "container_title": cprops.get("title", ""),
        "writing_app": cprops.get("writing_application", ""),
        "muxing_app": cprops.get("muxing_application", ""),
        "release_group": group.group(1) if group else "",
        "source_tags": " ".join(tags),
        "n_video": counts.get("video", 0),
        "n_audio": counts.get("audio", 0),
        "n_subtitle": counts.get("subtitles", 0),
    }

    if not tracks:
        row = dict.fromkeys(COLUMNS, "")
        row.update(base)
        return [row]

    out = []
    for t in tracks:
        p = t.get("properties", {})
        row = dict.fromkeys(COLUMNS, "")
        row.update(base)
        row.update({
            "track_id": t.get("id", ""),
            "track_number": p.get("number", ""),
            "type": t.get("type", ""),
            "codec": t.get("codec", ""),
            "codec_id": p.get("codec_id", ""),
            "language": p.get("language", ""),
            "language_ietf": p.get("language_ietf", ""),
            "name": p.get("track_name", ""),
            "audio_channels": p.get("audio_channels", ""),
            "audio_rate": p.get("audio_sampling_frequency", ""),
            "audio_bits": p.get("audio_bits_per_sample", ""),
            "video_dimensions": p.get("pixel_dimensions", ""),
            "text_subtitles": "1" if p.get("text_subtitles") else "0",
        })
        for name, keys, absent in FLAGS:
            row[name] = flag_of(p, keys, absent)
        out.append(row)
    return out


def already_done(out_path):
    done = set()
    if not os.path.exists(out_path):
        return done
    with open(out_path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r.get("path"):
                done.add(r["path"])
    return done


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("roots", nargs="+", help="folders to scan")
    ap.add_argument("--out", default="/out/headers.csv")
    ap.add_argument("--errors", default="/out/headers-errors.csv")
    ap.add_argument("--timeout", type=int, default=120,
                    help="seconds per file before giving up (default 120)")
    ap.add_argument("--anonymise", action="store_true",
                    help="replace paths with a hash and drop file and folder "
                         "names. Keeps every measurement, loses which film it was.")
    ap.add_argument("--limit", type=int, default=0,
                    help="stop after this many files (for a trial run)")
    args = ap.parse_args()

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    done = already_done(args.out)
    if done:
        print(f"{len(done)} files already in {args.out}, continuing", flush=True)

    fresh = not os.path.exists(args.out)
    out = open(args.out, "a", newline="", encoding="utf-8")
    writer = csv.DictWriter(out, fieldnames=COLUMNS)
    if fresh:
        writer.writeheader()
    err_fresh = not os.path.exists(args.errors)
    errs = open(args.errors, "a", newline="", encoding="utf-8")
    ewriter = csv.DictWriter(errs, fieldnames=["path", "reason"])
    if err_fresh:
        ewriter.writeheader()

    started = time.time()
    seen = failed = 0
    problems = []
    try:
        for path in walk(args.roots, problems):
            key = (hashlib.sha256(path.encode("utf-8")).hexdigest()[:16]
                   if args.anonymise else path)
            if key in done:
                continue
            info, reason = identify(path, args.timeout)
            if info is None:
                failed += 1
                ewriter.writerow({"path": key, "reason": reason})
                errs.flush()
            else:
                writer.writerows(rows_for(path, info, args.anonymise))
                out.flush()
            seen += 1
            if seen % 100 == 0:
                rate = seen / max(time.time() - started, 1e-9)
                print(f"  {seen} files, {failed} unreadable, "
                      f"{rate:.1f}/s", flush=True)
            if args.limit and seen >= args.limit:
                print("limit reached", flush=True)
                break
    except KeyboardInterrupt:
        print("\nstopped - rerun the same command to carry on", flush=True)
    finally:
        out.close()
        errs.close()

    took = time.time() - started
    for p in problems:
        print(f"  ! {p}")
    if not seen:
        print("\nNo video files found. That is almost always the mount or the "
              "user the container runs as, not an empty library:")
        print("    docker compose run --rm --entrypoint sh inventory "
              "-c 'id; ls -la /library'")
    print(f"\n{seen} files in {took/60:.1f} min, {failed} unreadable")
    print(f"written to {args.out}")
    if failed:
        print(f"the unreadable ones are listed in {args.errors}")


if __name__ == "__main__":
    main()
