import json
import tarfile

import pytest

from burst import bundle, launch
from burst.vast import VastError, pick_offers


def _job(tmp_path, words_per_chapter):
    (tmp_path / "media").mkdir(parents=True)
    chapters = []
    for i, w in enumerate(words_per_chapter):
        (tmp_path / "media" / f"c{i}.mp4").write_bytes(b"x" * (i + 1))
        chapters.append({"id": f"c{i}", "beats": [{"text": "w " * w, "visual": {"src": f"media/c{i}.mp4"}}]})
    (tmp_path / "job.json").write_text(json.dumps({"chapters": chapters}))
    return tmp_path


def test_plan_shards_contiguous_and_balanced():
    chapters = [{"beats": [{"text": "w " * n}]} for n in (100, 100, 100, 100, 100, 100, 100, 100)]
    plan = bundle.plan_shards(chapters, 4)
    assert plan == [[0, 1], [2, 3], [4, 5], [6, 7]]
    assert bundle.plan_shards(chapters[:2], 5) == [[0], [1]]
    uneven = bundle.plan_shards([{"beats": [{"text": "w " * n}]} for n in (500, 10, 10, 10)], 3)
    assert [i for s in uneven for i in s] == [0, 1, 2, 3] and len(uneven) == 3


def test_shard_bundles_carry_only_their_media(tmp_path):
    job = _job(tmp_path / "job", [50, 50, 50])
    tars = bundle.build_shards(job, tmp_path / "out", 3)
    with tarfile.open(tars[1]) as t:
        names = set(t.getnames())
        t.extractall(tmp_path / "x", filter="data")
    assert names == {"job.json", "manifest.json", "media/c1.mp4"}
    bundle.verify_extracted(tmp_path / "x")
    (tmp_path / "x" / "media" / "c1.mp4").write_bytes(b"corrupt")
    with pytest.raises(ValueError):
        bundle.verify_extracted(tmp_path / "x")


def test_missing_media_is_caught_before_renting(tmp_path):
    job = _job(tmp_path, [10])
    (job / "media" / "c0.mp4").unlink()
    with pytest.raises(ValueError, match="media missing"):
        bundle.load_job(job)


def test_pick_offers_same_gpu_distinct_machines_fastest_first():
    offers = [
        {"id": 1, "machine_id": 10, "gpu_name": "RTX 4090", "inet_down": 9000, "cpu_cores_effective": 64, "dph_total": .4},
        {"id": 2, "machine_id": 11, "gpu_name": "RTX 5090", "inet_down": 3000, "cpu_cores_effective": 32, "dph_total": .7},
        {"id": 3, "machine_id": 11, "gpu_name": "RTX 5090", "inet_down": 3000, "cpu_cores_effective": 32, "dph_total": .7},
        {"id": 4, "machine_id": 12, "gpu_name": "RTX 5090", "inet_down": 5000, "cpu_cores_effective": 48, "dph_total": .8},
    ]
    assert [o["id"] for o in pick_offers(offers, 2)] == [4, 2]        # 5090 preferred, one per machine
    assert [o["id"] for o in pick_offers(offers, 1)] == [4]
    with pytest.raises(VastError):
        pick_offers(offers, 3)                                         # only 2 distinct 5090 machines


# ── launcher lifecycle with fake vast.ai + fake storage ──────────────────────────────────────────
class FakeStore:
    bucket = "b"

    def __init__(self, *a, **k):
        self.objects = {}
        FakeStore.last = self

    def upload(self, path, key):
        self.objects[key] = b"tar"

    def get_url(self, key, ttl=0):
        return f"https://get/{key}"

    def put_url(self, key, ttl=0):
        return f"https://put/{key}"

    def read_json(self, key):
        return self.objects.get(key)


class FakeVast:
    def __init__(self, *a, fail_shard=None, **k):
        self.instances, self.destroyed, self.ticks, self.fail = {}, set(), 0, fail_shard
        FakeVast.last = self

    def search_offers(self, **k):
        return [{"id": i, "machine_id": i, "gpu_name": "RTX 5090", "inet_down": 5000,
                 "cpu_cores_effective": 64, "dph_total": 0.6} for i in range(8)]

    def create(self, offer_id, image, env, disk, label, args):
        iid = 100 + len(self.instances)
        self.instances[iid] = {"env": env, "label": label}
        return iid

    def show(self, iid):
        self.ticks += 1
        store = FakeStore.last
        if self.ticks > 3:  # machines finish: followers first, then the leader's final
            for i, inst in self.instances.items():
                k = int(inst["env"]["SHARD_INDEX"])
                key = f"runs/{inst['env']['RUN_ID']}/out/shard-{k:02d}.status.json"
                store.objects[key] = {"error": "boom"} if k == self.fail else {"seconds": 30}
            run = next(iter(self.instances.values()))["env"]["RUN_ID"]
            if self.fail is None and self.ticks > 8:
                store.objects[f"runs/{run}/out/final.status.json"] = {"video_seconds": 3600}
        return {"actual_status": "running"}

    def destroy(self, iid):
        self.destroyed.add(iid)

    def list(self):
        return [{"id": i, "label": v["label"], "actual_status": "running"}
                for i, v in self.instances.items() if i not in self.destroyed]


@pytest.fixture
def fakes(monkeypatch):
    monkeypatch.setattr("burst.storage.Store", FakeStore)
    monkeypatch.setattr(launch.time, "sleep", lambda s: None)
    monkeypatch.setenv("VAST_API_KEY", "k")
    monkeypatch.setenv("S3_BUCKET", "b")


def test_launch_success_destroys_everything(tmp_path, fakes, monkeypatch):
    monkeypatch.setattr(launch, "Vast", FakeVast)
    job = _job(tmp_path, [100] * 8)
    assert launch.main([str(job), "--shards", "4", "--image", "img"]) == 0
    v = FakeVast.last
    assert len(v.instances) == 4 and v.destroyed == set(v.instances)
    leader = [i for i in v.instances.values() if i["env"]["SHARD_INDEX"] == "0"][0]["env"]
    assert len(json.loads(leader["PEER_URLS"])) == 3 and "FINAL_PUT_URL" in leader
    follower = [i for i in v.instances.values() if i["env"]["SHARD_INDEX"] == "1"][0]["env"]
    assert "FINAL_PUT_URL" not in follower and "VAST_API_KEY" not in json.dumps(follower)


def test_launch_failure_still_destroys_everything(tmp_path, fakes, monkeypatch):
    monkeypatch.setattr(launch, "Vast", lambda *a, **k: FakeVast(fail_shard=0))
    job = _job(tmp_path, [100] * 4)
    with pytest.raises(RuntimeError, match="shard 0 failed"):
        launch.main([str(job), "--shards", "2", "--image", "img"])
    v = FakeVast.last
    assert v.destroyed == set(v.instances)
