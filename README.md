# mkv-track-inventory

Reads the header of every video file in a library and writes one CSV.

It does not extract, decode, transcode or change anything. For each file it asks
`mkvmerge` what is inside, which reads the header and part of the index — not
the stream. A 60 GB remux costs about as much as a 600 MB episode. Ten thousand
titles take hours, not weeks.

The library is mounted **read-only**, the container has **no network**, no
capabilities and no Docker socket. The only thing it writes is `out/headers.csv`.

## What it is for

Working out what the subtitle and audio tracks in a library actually are —
which track is plain dialogue, which is for the hard of hearing, which only
translates on-screen signs, which belongs to a commentary. Deciding that
properly means extracting and reading subtitles, which is slow. This scan is
the cheap first pass that says *where it is worth looking*:

- how often discs already name or flag their tracks honestly (every honest name
  is a free example to learn from)
- which files have a stereo audio track alongside a 5.1 one — often a
  commentary, and a hint that a commentary subtitle track exists too
- what a large library actually contains: languages, codecs, track counts,
  series versus films, DVD rips versus Blu-ray
- whether files came off a disc or from somewhere else (`writing_app` says
  `MakeMKV` for a rip, `mkvmerge` for a remux, `HandBrake`/`Lavf` for a
  re-encode)

It cannot tell plain dialogue from a hearing-impaired track, and it cannot find
an unnamed commentary. Those need the subtitles themselves.

## Running it on a Synology

Container Manager → **Project** → Create, paste `docker-compose.ghcr.yml`, and
change three things:

```yaml
image: ghcr.io/OWNER/mkv-track-inventory:latest   # your GitHub account
volumes:
  - /volume1/media:/library:ro           # where your films are
  - /volume1/docker/mkv-track-inventory:/out      # where the CSV should land
user: "1026:100"                         # a user allowed to read the library
```

Nothing is built; the image is pulled. The run ends by itself and prints how
many files it saw.

For `user`, over SSH `id yourname` prints the two numbers. Get it wrong and the
run reports *not readable by uid …* rather than silently finding nothing.

## Running it anywhere else

```sh
git clone https://github.com/OWNER/mkv-track-inventory && cd mkv-track-inventory
mkdir -p out
export LIBRARY=/path/to/your/library
export OWNER=$(id -u):$(id -g)
docker compose build
docker compose run --rm inventory /library --limit 50     # try fifty first
```

Then the whole library, detached:

```sh
nohup docker compose run --rm inventory /library > scan.log 2>&1 &
tail -f scan.log
```

Progress prints every hundred files. Stopping it with Ctrl-C is safe: rerun the
same command and it carries on, skipping what is already in the CSV.

Several folders at once:

```sh
docker compose run --rm inventory /library/Movies /library/Series
```

## If you would rather not share your titles

```sh
docker compose run --rm inventory /library --anonymise
```

Paths become a hash and file and folder names are dropped. Every measurement
survives; what is lost is which film each row was. Enough for statistics, not
enough to go back and check a specific disc.

## What comes out

`out/headers.csv`, one row per track, with the file's values repeated on each of
its rows so the file can be read on its own.

Per file: `path` · `folder` · `filename` · `looks_like` (movie or series, guessed
from the path) · `size_mb` · `duration_s` · `container_title` · `writing_app` ·
`muxing_app` · `release_group` · `source_tags` · `n_video` · `n_audio` ·
`n_subtitle`

Per track: `track_id` · `track_number` · `type` · `codec` · `codec_id` ·
`language` · `language_ietf` · `name` · the flags `default` `forced`
`hearing_impaired` `visual_impaired` `text_descriptions` `original` `commentary`
`enabled` · `audio_channels` · `audio_rate` · `audio_bits` ·
`video_dimensions` · `text_subtitles`

Matroska only ever stores the flags that are set, so an absent flag is reported
as `0` (and `enabled` as `1`, which is its default).

Files `mkvmerge` could not read are listed in `out/headers-errors.csv` with the
reason, rather than stopping the run.

## A quick look at your own data

```sh
python3 - <<'EOF'
import csv, collections
rows = list(csv.DictReader(open('out/headers.csv')))
subs = [r for r in rows if r['type'] == 'subtitles']
print(len({r['path'] for r in rows}), 'files,', len(subs), 'subtitle tracks')
print('\nhow they were made:')
for k, v in collections.Counter(
        r['writing_app'].split()[0] if r['writing_app'] else '(none)'
        for r in rows).most_common(10):
    print(f'  {k:28s} {v}')
print('\nsubtitle track names that say what they are:')
for k, v in collections.Counter(
        r['name'] for r in subs if r['name']).most_common(25):
    print(f'  {k[:50]:52s} {v}')
print('\nflags actually set on subtitle tracks:')
for f in ('forced', 'hearing_impaired', 'commentary', 'default'):
    print(f'  {f:18s} {sum(1 for r in subs if r[f] == "1")}')
EOF
```

## Licence

MIT.
