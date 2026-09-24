# Footage review protocol (Claude's eyes)

`python -m footage.find` ranks candidates. It doesn't decide what we use. A Claude session looks
at every contact sheet and picks, following this protocol. Nothing goes into a video unless a
reviewer has seen it and it passes every check below.

## What you look at

For each request in `<out>/candidates.json` (`requests.<id>`):

1. **`<out>/contact/<id>.jpg`**: one sheet per request.
   - The header shows the request id, the subject, what the shot must show, the era and the minimum width.
   - Each tile has a big yellow number in its top-left corner. That number is what you pick.
   - The first line under a tile is `source · licence · resolution`. Its colour is the licence
     class: green means public domain or CC0, blue means CC BY or BY-SA, amber means another free
     licence (Pexels, Pixabay), and red means unknown.
   - The second line is `date · title`.
   - A red badge on a tile flags a machine-detected issue. `LICENCE?` means the licence is unknown,
     `FAIR-USE` means the file is copyrighted, `TEXT` means overlaid text or a watermark, `SOFT`
     means blurry, and `LOW-RES` means it's under the requested width.
   - A **video tile** is a 2×2 filmstrip, with a timestamp on each frame and `VIDEO m:ss` (the
     total length) in its top-right corner.
2. **`<out>/contact/<id>/<n>.jpg`**: a zoom of candidate *n*, with its description. Open it for
   close calls, faces (is it really the person?), small text and watermarks, and to choose the
   seconds of a video.
3. For facts the image can't show, such as the full description, page URL, licence URL, flags and
   scores, read `candidates.json → requests.<id>.candidates[n-1]`.

Review one request at a time: the sheet, then zooms if needed, then write its pick.

## Checks, in order. A candidate must pass all of them.

1. **It shows the actual subject.** The right person, product, place or event, not a lookalike,
   a namesake, a modern replica or a generic stand-in.
   - For a person, compare the face with other tiles and the zooms. When several tiles agree on
     one face, that's probably the person. If you aren't confident it's them, don't pick it.
   - The title or description naming the subject isn't enough on its own. The picture has to
     match, especially for Openverse, Flickr and stock photos.
   - Stock sites (Pexels, Pixabay) are only for generic b-roll: a city, an office, a crowd, a
     texture. Never use them to stand in for a specific real person or event.
2. **It's from the right era.** Clothing, cars, film stock (black and white, Kodachrome
   colour), technology and signage should fit `era`. Dates on tiles are often upload dates, so
   trust the picture over the metadata. For "his face, 1980s", a 2005 portrait fails.
3. **It shows what `must_show` asks for.** If the request says "his face", a crowd shot where he's
   a speck fails. If it says "the can", the can must be readable.
4. **The quality is usable.** It's sharp at the size we'll show it, with no heavy JPEG blocking,
   and the resolution in the caption is at least `min_width`. The exception is a `LOW-RES` tile
   when there's truly nothing better and the image is iconic. Say so in the notes.
5. **It's clean.** Reject tiles with:
   - watermarks, stock-agency stamps, TV channel bugs, burned-in subtitles or news tickers;
   - someone else's graphics (a documentary's title cards or cutouts);
   - social-media screenshots or UI chrome;
   - modern logos that aren't the subject.

   A `TEXT` flag is only a hint, so confirm it in the zoom. Text that is part of the scene
   (signage, a newspaper page that is the subject) is fine.
6. **The composition works for us.** Our style cuts subjects out as halftone collages and uses
   slow pushes. Prefer:
   - a clear, unobstructed subject with separation from the background;
   - the subject not cropped at the head or chin;
   - enough margin for a push-in;
   - landscape orientation for full-frame shots. Portrait is fine for cutouts.

   For a cutout, one person facing the camera or three-quarter beats a group shot.
7. **The licence is OK for a monetised channel.**
   - Green and blue pass.
   - Amber (Pexels or Pixabay) passes for b-roll.
   - Red `LICENCE?` needs the page URL checked. Pick it only if the image is essential, and write
     `"licence: verify"` in the notes.
   - `FAIR-USE` (YouTube) is allowed only when the narration comments on that exact clip, and for
     8 s or less. Say why in the notes.
   - NC and ND licences never reach the sheet, because they're filtered out upstream.

When several candidates pass, rank them: subject certainty first, then era, then quality, then
composition, then licence (public domain or CC0 beats BY-SA). Pick the best one, and pick an
`alt` from a different source or photo where possible, so the edit has a fallback.

## Videos

Choose the seconds you want from the filmstrip timestamps (open the zoom). Set `in` to the start
in seconds and `dur` to the length. Aim for 4–8 s, since the house rule is that no stretch of
source footage runs longer than about 8 s without a cut. If none of the four frames shows the
subject, don't guess. Either treat it as no pick, or pick with `in: null` and write `"check full
clip"` in the notes, and the whole file is fetched.

## picks.json

`find` writes a template, `<out>/picks.template.json`. Copy it to `picks.json` and fill in one
object per request:

```json
[
  {"id": "b012", "pick": 3, "alt": 7, "crop": "face", "in": null, "dur": null,
   "notes": "Goizueta at the 1985 New Coke press conference; #7 same event, softer"},
  {"id": "b013", "pick": 5, "alt": null, "crop": "full", "in": 42.0, "dur": 6.0,
   "notes": "newsreel, cans on the line 0:42-0:48"},
  {"id": "b014", "pick": null, "alt": null, "crop": null,
   "notes": "all tiles show the 2010s building, not 1985",
   "requery": {"commons": ["Coca-Cola headquarters Atlanta 1980s"], "loc": ["Coca-Cola building Atlanta"],
               "archive": ["Coca-Cola 1985 news"]}}
]
```

| field | meaning |
|---|---|
| `id` | the request id |
| `pick` | the tile number to use, or `null` if nothing passes |
| `alt` | the second-best tile number, or `null` |
| `crop` | `"face"` for a tight cutout on the person's face or head, `"full"` to use the whole frame, `null` to leave it to the editor |
| `in`, `dur` | videos only: the start and length in seconds to fetch |
| `notes` | one line: what the image shows and why it won, plus any licence caveat |
| `requery` | only with `pick: null`: new queries per source, fed back to `footage.find` |

When `pick` is `null`, say **why** in the notes (wrong person, wrong era, all watermarked, and so
on) and suggest better queries. Common fixes:

- the full name plus a year;
- the event name, like "New Coke press conference 1985";
- the Commons category-style name, like "Roberto C. Goizueta";
- the archive's own vocabulary: LOC uses "Photograph shows…" and archive.org uses "newsreel".

## Then

```bash
python -m footage.fetch picks.json --candidates <out>/candidates.json --out <out>/media [--with-alt]
```

This writes `<out>/media/<id>.<ext>`, the `<out>/sources.json` licence ledger (the job.json
`sources` field), `<out>/picked.json` (crop, in and dur per beat) and `<out>/requery.json`.
`requery.json` is itself a valid requests file, carrying your new queries. Run
`python -m footage.find <out>/requery.json --out <out>` to search again. It updates those requests
in `candidates.json` and redraws their sheets, then you review them again.

## Reusing a candidate from another beat

A pick may point at a tile on another request's sheet with `"from": "<other id>"` — e.g. a clip found
for b060 that also shows the subject of b061: `{"id":"b061","from":"b060","pick":5,"in":12,"dur":6}`.
Use it whenever the best shot for a beat turned up on a different sheet.
