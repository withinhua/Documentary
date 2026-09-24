"""Footage finder: licence normalisation, source adapters against recorded-style API responses
(mock HTTP), quality filters, and the offline end-to-end flow find → contact sheet → fetch."""
import asyncio
import json
import shutil
import subprocess

import httpx
import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from footage import contact, fetch, find, quality
from footage.core import Candidate, Request, classify_license, license_allowed, text_match
from footage.net import Http
from footage.sources.archive import Archive
from footage.sources.commons import Commons
from footage.sources.loc import LOC
from footage.sources.local import LocalFolder
from footage.sources.nara import NARA
from footage.sources.openverse import Openverse
from footage.sources.smithsonian import Smithsonian
from footage.sources.stock import Pexels, Pixabay
from footage.sources.youtube import YouTube

REQ = Request.from_dict({"id": "b012", "kind": "photo", "subject": "Roberto Goizueta, Coca-Cola CEO",
                         "must_show": "his face, 1980s", "era": "1980-1990",
                         "queries": {"commons": ["Roberto Goizueta"]}, "min_width": 1000})


# ---------------------------------------------------------------- licences + model

@pytest.mark.parametrize("name,url,cls,flags", [
    ("CC BY-SA 4.0", "https://creativecommons.org/licenses/by-sa/4.0", "cc-by-sa", []),
    ("CC BY 2.0", None, "cc-by", []),
    (None, "https://creativecommons.org/publicdomain/zero/1.0/", "cc0", []),
    ("Public domain", None, "public-domain", []),
    (None, "http://creativecommons.org/publicdomain/mark/1.0/", "public-domain", []),
    ("No known restrictions on publication.", None, "public-domain", ["verify"]),
    ("CC BY-NC-SA 2.0", None, "other", ["nc"]),
    ("CC BY-ND 4.0", None, "other", ["nd"]),
    (None, "https://creativecommons.org/licenses/by-nc/4.0/", "other", ["nc"]),
    ("Pexels License", None, "other", []),
    ("All rights reserved", None, "unknown", []),
    ("", None, "unknown", []),
])
def test_classify_license(name, url, cls, flags):
    c, _, f = classify_license(name, url)
    assert c == cls and f == flags


def test_license_policy_excludes_nc_nd_and_fair_use_by_default():
    mk = lambda cls, flags: Candidate("x", "1", "photo", license=cls, license_flags=flags)
    assert license_allowed(mk("cc-by", []))
    assert not license_allowed(mk("other", ["nc"]))
    assert not license_allowed(mk("other", ["nd"]))
    assert not license_allowed(mk("other", ["fair-use"]))
    assert license_allowed(mk("other", ["fair-use"]), allow_fair_use=True)
    assert not license_allowed(mk("unknown", []), allow_unknown=False)


def test_request_helpers():
    assert REQ.era_range() == (1980, 1990)
    assert Request.from_dict({"id": "x", "era": "1960s"}).era_range() == (1960, 1969)
    assert REQ.queries_for("commons") == ["Roberto Goizueta"]
    assert REQ.queries_for("loc") == ["Roberto Goizueta, Coca-Cola CEO"]      # falls back to subject
    hit = Candidate("commons", "1", "photo", title="Roberto Goizueta 1985 portrait")
    miss = Candidate("commons", "2", "photo", title="Coca-Cola bottles")
    assert text_match(REQ, hit) > text_match(REQ, miss)


# ---------------------------------------------------------------- adapters (mock HTTP)

def run_search(source, routes, req=REQ, query="q", kind="photo", tmp_path=None):
    calls = []

    def handler(request: httpx.Request):
        calls.append(str(request.url))
        for key, body in routes.items():
            if key in str(request.url):
                return httpx.Response(200, json=body)
        return httpx.Response(404, json={})

    async def go():
        async with Http(cache_dir=tmp_path, transport=httpx.MockTransport(handler)) as http:
            return await source.search(http, req, query, kind, 20)
    return asyncio.run(go()), calls


COMMONS = {"batchcomplete": True, "query": {"pages": [
    {"pageid": 111, "ns": 6, "title": "File:Roberto Goizueta 1985.jpg", "index": 2, "imageinfo": [{
        "url": "https://upload.wikimedia.org/wikipedia/commons/a/ab/Roberto_Goizueta_1985.jpg",
        "descriptionurl": "https://commons.wikimedia.org/wiki/File:Roberto_Goizueta_1985.jpg",
        "thumburl": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/x/640px-x.jpg",
        "width": 2400, "height": 3000, "mime": "image/jpeg", "extmetadata": {
            "LicenseShortName": {"value": "CC BY-SA 3.0"},
            "LicenseUrl": {"value": "https://creativecommons.org/licenses/by-sa/3.0"},
            "Artist": {"value": "<a href='//commons.wikimedia.org/wiki/User:Foo'>Foo Bar</a>"},
            "ImageDescription": {"value": "<p>Roberto C. Goizueta, chairman of The Coca-Cola Company</p>"},
            "DateTimeOriginal": {"value": "1985-04-23"}}}]},
    {"pageid": 222, "ns": 6, "title": "File:Logo.svg", "index": 1,
     "imageinfo": [{"url": "https://u/x.svg", "mime": "image/svg+xml", "width": 1, "height": 1}]},
    {"pageid": 333, "ns": 6, "title": "File:Coke ad.jpg", "index": 3, "imageinfo": [{
        "url": "https://u/ad.jpg", "width": 800, "height": 600, "mime": "image/jpeg",
        "extmetadata": {"LicenseShortName": {"value": "CC BY-NC 2.0"}}}]},
]}}


def test_commons_adapter(tmp_path):
    cands, calls = run_search(Commons(), {"commons.wikimedia.org/w/api.php": COMMONS}, tmp_path=tmp_path)
    assert "gsrnamespace=6" in calls[0] and "filetype%3Abitmap" in calls[0] and "extmetadata" in calls[0]
    assert [c.id for c in cands] == ["111", "333"]                  # svg dropped, ordered by index
    c = cands[0]
    assert (c.license, c.license_name, c.author) == ("cc-by-sa", "CC BY-SA 3.0", "Foo Bar")
    assert c.width == 2400 and c.thumb_url.endswith("640px-x.jpg") and c.date == "1985-04-23"
    assert "Goizueta" in c.description
    assert cands[1].license_flags == ["nc"]


def test_commons_video_uses_videoinfo_and_small_derivative(tmp_path):
    data = {"query": {"pages": [{"pageid": 9, "title": "File:Newsreel.webm", "index": 1, "videoinfo": [{
        "url": "https://upload.wikimedia.org/x/Newsreel.webm", "thumburl": "https://u/t.jpg", "mime": "video/webm",
        "width": 1440, "height": 1080, "duration": 95.5,
        "derivatives": [{"src": "https://u/1080p.webm", "height": 1080}, {"src": "https://u/360p.webm", "height": 360}],
        "extmetadata": {"LicenseShortName": {"value": "Public domain"}}}]}]}}
    cands, calls = run_search(Commons(), {"api.php": data}, kind="video", tmp_path=tmp_path)
    assert "prop=videoinfo" in calls[0] and "filetype%3Avideo" in calls[0]
    assert cands[0].kind == "video" and cands[0].preview_url == "https://u/360p.webm" and cands[0].duration == 95.5


def test_openverse_adapter(tmp_path):
    data = {"result_count": 1, "results": [{
        "id": "abc", "title": "Goizueta", "foreign_landing_url": "https://flickr.com/p/1", "url": "https://live.staticflickr.com/1.jpg",
        "creator": "Jane", "license": "by", "license_version": "2.0", "license_url": "https://creativecommons.org/licenses/by/2.0/",
        "provider": "flickr", "width": 2048, "height": 1365, "thumbnail": "https://api.openverse.org/v1/images/abc/thumb/",
        "attribution": "\"Goizueta\" by Jane is licensed under CC BY 2.0.", "tags": [{"name": "coca-cola"}]}]}
    cands, calls = run_search(Openverse(), {"api.openverse.org": data}, tmp_path=tmp_path)
    assert "license_type=commercial%2Cmodification" in calls[0]
    c = cands[0]
    assert (c.license, c.license_name, c.author, c.width) == ("cc-by", "CC BY 2.0", "Jane", 2048)
    assert "coca-cola" in c.description and c.credit.startswith('"Goizueta"')


def test_archive_adapter_video_with_thumbs(tmp_path):
    search = {"response": {"numFound": 1, "docs": [{"identifier": "coke1985", "title": "Coca-Cola newsreel",
                                                     "mediatype": "movies", "year": "1985"}]}}
    meta = {"metadata": {"identifier": "coke1985", "title": "Coca-Cola newsreel", "mediatype": "movies",
                         "licenseurl": "http://creativecommons.org/publicdomain/mark/1.0/", "date": "1985-05-01"},
            "files": [{"name": "coke1985.mpeg", "format": "MPEG2", "source": "original"},
                      {"name": "coke1985.mp4", "format": "h.264", "width": "640", "height": "480", "length": "125.3"},
                      {"name": "coke1985_512kb.mp4", "format": "512Kb MPEG4", "length": "02:05"},
                      *[{"name": f"coke1985.thumbs/coke1985_{i:06d}.jpg", "format": "Thumbnail"} for i in (1, 30, 60, 90, 120)]]}
    cands, calls = run_search(Archive(), {"advancedsearch.php": search, "archive.org/metadata/coke1985": meta},
                              kind="video", tmp_path=tmp_path)
    assert "mediatype%3Amovies" in calls[0]
    c = cands[0]
    assert c.kind == "video" and c.license == "public-domain"
    assert c.full_url.endswith("/coke1985.mp4") and c.preview_url.endswith("coke1985_512kb.mp4")
    assert len(c.preview_frames) == 5 and c.duration == 125.3 and (c.width, c.height) == (640, 480)


def test_loc_adapter(tmp_path):
    search = {"results": [{"id": "http://www.loc.gov/item/2016646421/", "title": "Coca-Cola sign",
                           "date": "1936", "url": "https://www.loc.gov/item/2016646421/",
                           "image_url": ["https://tile.loc.gov/x_150px.jpg#h=150&w=113",
                                         "https://tile.loc.gov/x_640px.jpg#h=640&w=480"]},
                          {"id": "http://www.loc.gov/item/1/", "title": "restricted", "access_restricted": True,
                           "image_url": ["https://tile.loc.gov/y.jpg"]}]}
    item = {"item": {"rights_advisory": ["No known restrictions on publication."]},
            "resources": [{"files": [[{"url": "https://tile.loc.gov/x.tif", "mimetype": "image/tiff", "width": 5000},
                                      {"url": "https://tile.loc.gov/x_r.jpg", "mimetype": "image/jpeg", "width": 3000, "height": 4000}]]}]}
    cands, calls = run_search(LOC(), {"loc.gov/photos/": search, "item/2016646421": item}, tmp_path=tmp_path)
    assert len(cands) == 1
    c = cands[0]
    assert c.license == "public-domain" and "verify" in c.license_flags
    assert c.full_url == "https://tile.loc.gov/x_r.jpg" and c.width == 3000 and c.thumb_url.endswith("x_640px.jpg")


def test_nara_adapter(tmp_path):
    data = {"body": {"hits": {"total": {"value": 1}, "hits": [{"_source": {"record": {
        "naId": 541234, "title": "President Reagan meets Coca-Cola executives",
        "productionDates": [{"year": 1985}], "useRestriction": {"status": "Unrestricted"},
        "digitalObjects": [{"objectType": "Image (JPG)", "objectUrl": "https://s3.amazonaws.com/NARAprodstorage/x.jpg"}]}}}]}}}
    cands, calls = run_search(NARA(), {"catalog.archives.gov": data}, tmp_path=tmp_path)
    c = cands[0]
    assert c.license == "public-domain" and c.date == "1985" and c.page_url.endswith("/id/541234")


def test_smithsonian_keeps_only_cc0(tmp_path):
    def row(access):
        return {"id": "r" + access, "content": {"descriptiveNonRepeating": {
            "title": {"content": "Coca-Cola bottle"}, "record_link": "https://collections.si.edu/1",
            "online_media": {"media": [{"type": "Images", "content": "https://ids.si.edu/ids/deliveryService?id=X" + access,
                                        "idsId": "X" + access, "usage": {"access": access}}]}},
            "freetext": {"date": [{"content": "1985"}]}}}
    data = {"response": {"rows": [row("CC0"), row("Usage conditions apply")]}}
    cands, calls = run_search(Smithsonian(), {"api.si.edu": data}, tmp_path=tmp_path)
    assert "api_key=DEMO_KEY" in calls[0] or "api_key=" in calls[0]
    assert [c.license for c in cands] == ["cc0"] and cands[0].thumb_url.endswith("&max=640")


def test_stock_adapters(tmp_path, monkeypatch):
    monkeypatch.setenv("PEXELS_API_KEY", "k")
    monkeypatch.setenv("PIXABAY_API_KEY", "k")
    px = {"videos": [{"id": 5, "width": 3840, "height": 2160, "duration": 12, "url": "https://www.pexels.com/video/soda-can-5/",
                      "image": "https://images.pexels.com/v.jpg", "user": {"name": "Ann"},
                      "video_files": [{"link": "https://v/sd.mp4", "width": 640, "height": 360, "file_type": "video/mp4"},
                                      {"link": "https://v/4k.mp4", "width": 3840, "height": 2160, "file_type": "video/mp4"}],
                      "video_pictures": [{"picture": "https://p/0.jpg"}, {"picture": "https://p/1.jpg"}]}]}
    cands, _ = run_search(Pexels(), {"api.pexels.com/videos": px}, kind="video", tmp_path=tmp_path)
    assert cands[0].preview_url == "https://v/sd.mp4" and cands[0].full_url == "https://v/4k.mp4"
    assert cands[0].license_name == "Pexels License" and len(cands[0].preview_frames) == 2
    pb = {"hits": [{"id": 7, "pageURL": "https://pixabay.com/photos/7", "tags": "soda, can", "webformatURL": "https://c/w.jpg",
                    "largeImageURL": "https://c/l.jpg", "imageWidth": 4000, "imageHeight": 3000, "user": "bob"}]}
    cands, calls = run_search(Pixabay(), {"pixabay.com/api/": pb}, tmp_path=tmp_path)
    assert cands[0].full_url == "https://c/l.jpg" and cands[0].width == 4000
    assert not list((tmp_path / "json").rglob("*k*key*"))   # api keys are not part of cache names


def test_youtube_parse_is_flagged_fair_use():
    data = {"entries": [{"id": "abc123", "title": "New Coke 1985 news report", "duration": 150, "channel": "News"}]}
    c = YouTube().parse(data, "new coke")[0]
    assert "fair-use" in c.license_flags and c.preview_frames[0].endswith("/hq1.jpg")
    assert not YouTube().default


def test_http_cache_avoids_second_request(tmp_path):
    n = {"calls": 0}

    def handler(request):
        n["calls"] += 1
        return httpx.Response(200, json={"ok": True})

    async def go():
        async with Http(cache_dir=tmp_path, transport=httpx.MockTransport(handler)) as http:
            a = await http.get_json("https://example.org/api", {"q": "x", "api_key": "SECRET"})
            b = await http.get_json("https://example.org/api", {"q": "x", "api_key": "OTHER"})
            return a, b
    a, b = asyncio.run(go())
    assert a == b == {"ok": True} and n["calls"] == 1
    assert "SECRET" not in "".join(p.read_text() for p in tmp_path.rglob("*") if p.is_file())


def test_http_retries_on_429(tmp_path, monkeypatch):
    seq = [429, 200]

    def handler(request):
        return httpx.Response(seq.pop(0), json={"ok": True}, headers={"retry-after": "0"})

    async def go():
        async with Http(cache_dir=tmp_path, transport=httpx.MockTransport(handler)) as http:
            return await http.get_json("https://example.org/r")
    assert asyncio.run(go()) == {"ok": True}


# ---------------------------------------------------------------- quality

def _photo(seed=0, size=(1200, 800)):
    rng = np.random.default_rng(seed)
    base = rng.integers(0, 255, (size[1] // 20, size[0] // 20, 3), dtype=np.uint8)
    im = Image.fromarray(base).resize(size, Image.BICUBIC)
    d = ImageDraw.Draw(im)
    for i in range(12):
        x, y = rng.integers(0, size[0] - 200), rng.integers(0, size[1] - 200)
        d.ellipse([x, y, x + int(rng.integers(40, 200)), y + int(rng.integers(40, 200))],
                  fill=tuple(int(v) for v in rng.integers(0, 255, 3)), outline=(0, 0, 0), width=3)
    return im


def test_quality_blur_dedupe_and_text():
    sharp = _photo(1)
    blurry = sharp.filter(ImageFilter.GaussianBlur(10))
    assert quality.sharpness(sharp) > 4 * quality.sharpness(blurry)
    assert quality.sharpness(blurry) < quality.BLUR_REJECT < quality.sharpness(sharp)
    near_dup = sharp.resize((600, 400))
    assert quality.hamming(quality.phash(sharp), quality.phash(near_dup)) <= 6
    assert quality.hamming(quality.phash(sharp), quality.phash(_photo(2))) > 10
    texty = _photo(3)
    f = ImageFont.truetype(contact._FONT_FILES[True][0], 36) if shutil.os.path.exists(contact._FONT_FILES[True][0]) else None
    ImageDraw.Draw(texty).text((700, 720), "© STOCK AGENCY 1985", font=f, fill=(255, 255, 255))
    q_text, q_clean = quality.analyse(texty), quality.analyse(_photo(3))
    if q_text["text_area"] is not None:
        assert q_text["corner_text"] and q_text["text_area"] > q_clean["text_area"]
        assert quality.quality_score(q_text) < quality.quality_score(q_clean)


# ---------------------------------------------------------------- offline end to end

@pytest.fixture
def library(tmp_path):
    lib = tmp_path / "lib"
    (lib / "b001").mkdir(parents=True)
    p = _photo(10, (1600, 1100))
    p.save(lib / "b001" / "goizueta_press.jpg")
    (lib / "b001" / "goizueta_press.jpg.json").write_text(json.dumps(
        {"title": "Roberto Goizueta at the New Coke press conference", "date": "1985", "license": "CC0",
         "author": "AP test", "page_url": "https://example.org/g"}))
    p.resize((1200, 825)).save(lib / "b001" / "goizueta_copy.jpg")                  # duplicate
    p.filter(ImageFilter.GaussianBlur(12)).save(lib / "b001" / "soft.jpg")            # blurry
    _photo(11, (1500, 1000)).save(lib / "b001" / "nc.jpg")
    (lib / "b001" / "nc.json").write_text(json.dumps({"license": "CC BY-NC 2.0"}))    # excluded licence
    _photo(12, (1400, 900)).save(lib / "b001" / "other.jpg")
    _photo(13, (1300, 900)).save(lib / "shared.jpg")
    if shutil.which("ffmpeg"):
        (lib / "b002").mkdir()
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=25:d=3",
                        "-f", "lavfi", "-i", "smptebars=s=640x360:r=25:d=3", "-filter_complex", "[0][1]concat=n=2:v=1[v]",
                        "-map", "[v]", "-c:v", "libx264", "-g", "25", "-pix_fmt", "yuv420p", str(lib / "b002" / "reel.mp4")],
                       check=True)
        (lib / "b002" / "reel.json").write_text(json.dumps({"title": "Coca-Cola bottling line newsreel", "license": "Public domain"}))
    return lib


def test_offline_find_then_fetch(tmp_path, library):
    reqs = [Request.from_dict({"id": "b001", "kind": "photo", "subject": "Roberto Goizueta", "must_show": "face",
                               "era": "1980-1990", "min_width": 1000})]
    has_ff = shutil.which("ffmpeg") is not None
    if has_ff:
        reqs.append(Request.from_dict({"id": "b002", "kind": "video", "subject": "Coca-Cola bottling line", "min_width": 640}))
    out = tmp_path / "out"
    src = LocalFolder(library)
    opt = find.Options(use_clip=False)

    async def go():
        async with Http(cache_dir=tmp_path / "cache", offline=True) as http:
            return await find.run(reqs, [src], out, opt, http=http)
    doc = asyncio.run(go())

    r = doc["requests"]["b001"]
    ids = [c["id"] for c in r["candidates"]]
    assert ids[0] == "b001/goizueta_press.jpg"                    # metadata names the subject → top
    assert "b001/nc.jpg" not in ids and "b001/soft.jpg" not in ids and "b001/goizueta_copy.jpg" not in ids
    assert "shared.jpg" not in ids                                # own subfolder wins
    assert r["stats"]["rejected_blur"] == 1 and r["stats"]["rejected_duplicate"] == 1
    sheet = Image.open(out / r["contact_sheet"])
    assert sheet.width == contact.W and (out / "contact" / "b001" / "1.jpg").is_file()
    assert json.loads((out / "candidates.json").read_text())["requests"]["b001"]["candidates"][0]["n"] == 1
    assert (out / "picks.template.json").is_file()

    picks = [{"id": "b001", "pick": 1, "alt": 2, "crop": "face", "notes": "press conference"},
             {"id": "b009", "pick": 1}]
    if has_ff:
        v = doc["requests"]["b002"]["candidates"][0]
        assert v["kind"] == "video" and len(v["frames"]) >= 2 and all(f["t"] is not None for f in v["frames"])
        picks.append({"id": "b002", "pick": 1, "in": 1.0, "dur": 2.0})
    picks.append({"id": "b001x", "pick": None})

    async def go2():
        async with Http(cache_dir=tmp_path / "cache") as http:
            doc2 = dict(doc)
            doc2["requests"] = {**doc["requests"], "b001x": {"request": {"id": "b001x", "subject": "HQ 1985"}, "candidates": []}}
            return await fetch.run(picks, doc2, out / "media", http)
    res = asyncio.run(go2())
    assert res["errors"] == {"b009": "no such request in candidates.json"}
    ledger = json.loads((out / "sources.json").read_text())
    assert ledger["media/b001.jpg"]["license"] == "CC0 1.0" and ledger["media/b001.jpg"]["url"] == "https://example.org/g"
    assert (out / "media" / "b001.jpg").is_file()
    assert json.loads((out / "picked.json").read_text())["b001"]["crop"] == "face"
    assert [q["id"] for q in json.loads((out / "requery.json").read_text())] == ["b001x"]
    if has_ff:
        dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                    str(out / "media" / "b002.mp4")], capture_output=True, text=True).stdout)
        assert 1.5 < dur < 2.6
        assert ledger["media/b002.mp4"]["license_class"] == "public-domain"


def test_contact_sheet_handles_empty_and_missing_previews(tmp_path):
    req = {"id": "b777", "kind": "photo", "subject": "Nothing", "must_show": "", "era": "", "min_width": 1000}
    contact.contact_sheet(req, [], tmp_path / "empty.jpg")
    entry = {"n": 1, "kind": "video", "source": "archive", "license": "unknown", "license_name": "Unknown",
             "title": "x" * 200, "frames": [], "thumb_path": None, "duration": 3725}
    contact.contact_sheet(req, [entry], tmp_path / "one.jpg")
    assert Image.open(tmp_path / "one.jpg").width == contact.W
    assert contact.fmt_time(3725) == "1:02:05" and contact.warnings(entry) == ["LICENCE?"]
