# collage: paper-collage scene renderer

This package renders one "stop-motion paper collage" scene to an H.264 mp4. The output is 1920x1080, 30 fps, yuv420p, with no audio, and it lasts exactly `duration` seconds. The look is halftone B&W cut-outs with white sticker borders, on warm paper, with torn orange accent cards, typewriter strips and tape.

Everything runs on the CPU in Python (numpy, OpenCV, Pillow, onnxruntime). There is no browser. Every frame is a pure function of its frame index, so renders are deterministic. Frames are rendered in parallel threads and piped to ffmpeg.

```bash
pip install pillow numpy opencv-python-headless onnxruntime      # + ffmpeg on PATH
python -m collage.render spec.json out.mp4
python -m collage.render --still 2.5 spec.json preview.jpg        # one frame, for quick previews
python -m collage.render --demo outdir/ [--photo test.jpg] [--only quote headline]
```

```python
from collage.render import render_scene
render_scene({"type": "title", "duration": 4, "kicker": "Chapter 2", "title": "The Turning Point"}, "out.mp4")
```

`render_scene(spec, out_path, workdir=None, threads=None, crf=18, preset="veryfast") -> Path`

## Scene specs

Fields that apply to every scene:

| field | default | notes |
|---|---|---|
| `type` | required | `cutout`, `number_card`, `full`, `quote`, `headline` or `title` |
| `duration` | required | seconds. The frame count is `round(duration*30)` |
| `accent` | `#e8603c` | the colour of the torn accent cards and the marker stroke |
| `motion` | varies by type | camera: `push`, `pull`, `left`, `right`, `drift` or `none` |
| `fade_in` / `fade_out` | 0 | *added.* Seconds of fade from/to black |
| `grain` | 0.035 | *added.* Film-grain amount (0 turns it off) |
| `stop_motion` | true | *added.* `false` switches off the per-element boil (the 6 fps micro-jitter) |

A background (`bg`) is either `{"type":"paper"}`, optionally with `"color":"#rrggbb"`, or `{"type":"photo","src":path}`. The photo background also takes these added optional fields: `style` (`halftone`, `archival` or `clean`; default `halftone`), `focus` (`[x,y]` in 0..1 for the crop; default `[0.5,0.3]`) and `mute` (0.12, how far it is washed toward paper).

Cut-outs accept these added optional fields: `style` (`halftone`, `archival` or `clean`) and `subject`. `subject` is `"largest"` by default. It can be `"all"` to keep every segmented object, or `[x,y]` (0..1) to pick the object under that point.

| type | fields |
|---|---|
| `cutout` | `photo`, `position` (`left`, `right` or `center`), `bg`, `caption: {text, sub?}`, `motion` (`push` or `drift`). *Added:* `scale` (1.0 means the subject height is about 1.05x the frame height, bleeding off the bottom) |
| `number_card` | `number` (str), `label` (strip on the card), `bg`, `photo?` (a cut-out beside the card), `photo_position` (`left` or `right`). *Added:* `sub` (a big typewriter name strip under the card with tape, like the "PARNELLI JONES" reference) |
| `full` | `photo` **or** `video` + `in` (seconds), `motion` (`push`, `pull`, `left` or `right`), `style` (`halftone`, `archival` or `clean`), `caption` (a str or `{text, sub?}`). *Added:* `focus` |
| `quote` | `text`, `attribution`, `photo?` (a cut-out on the left). The text is typed out character by character and finishes by about 65% of the scene |
| `headline` | `text`, `publication`, `date`. *Added:* `photo?` (a halftone news photo across two columns). The clipping has a blackletter masthead, a dateline, a serif headline and greeked columns. An orange marker underline is drawn at 1 s |
| `title` | `kicker` (on an accent card), `title` (typewriter strips, one per wrapped line, popping in one after another) |

## Timing and motion

- Entrances such as the pop, slap and slide use an overshoot ease, and they are **quantised to 12 fps**, which gives the stop-motion feel. The camera moves smoothly at 30 fps.
- Paper elements "boil" with sub-pixel jitter and ±0.1° rotation. They take a new pose about 6 times a second.
- The camera has parallax. Backgrounds move at 0.4–0.55x the camera. Cut-outs move at 1.0x, and cards and strips at 1.1–1.2x.
- Order of entrances: the subject cut-out slides in at 0 s, the card slaps down at about 0.25 s, then the strips and tape arrive at about 0.45–0.85 s. The first element is on screen within about 0.2 s, so no empty lead-in is needed.

## Cut-outs

Cut-outs use the rembg ISNet model (`isnet-general-use.onnx`) through onnxruntime on the CPU. It takes about 5.5 s per photo the first time. The mask is cached in `$COLLAGE_CACHE` (default `/tmp/collage-cache`), keyed by the photo's sha1, so later runs take about 0 s. The model is downloaded from the rembg GitHub release to `collage/assets/models/` on first use. That folder is git-ignored. You can override its location with `COLLAGE_MODELS`. If ISNet fails, the code falls back to `u2netp.onnx`.

## Speed

On a 4-core cloud container, a 5 s scene takes **about 10–12 s** to render in total. That covers segmentation (when the mask is cached), frame compositing and x264 `veryfast` at CRF 18. `full` with a still photo takes about 7 s for 4 s of footage. `full` with a video, halftoned per frame, takes about 11 s for 3 s of footage.

## Files

- `render.py`: the API, the CLI and the demo reel
- `scenes.py`: the scene builders (layout and choreography per type)
- `anim.py`: the compositor (sprite layers, affine camera with parallax, easing, stop-motion stepping, grain)
- `imaging.py`: halftone, paper, torn cards, strips, tape, sticker cut-outs, distressed text
- `segment.py`: ISNet background removal and mask caching
- `assets/`: fonts (OFL/Apache, see `assets/LICENSES.md`), plus models that are downloaded rather than committed
- `demo_stills/`: JPEG stills from the demo reel, taken at 12%, 50% and 95% of each scene
