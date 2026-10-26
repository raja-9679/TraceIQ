"""Job-queue view: what counts as waiting, and that payloads never leak."""
import asyncio
import json

from app.api import job_queue


def _id(n):
    return f"{n}-0"


class FakeStreams:
    """Just enough of redis.asyncio for one stream + one consumer group."""

    def __init__(self, entries, last_delivered="0-0", pending=None, group="execution-workers"):
        self.entries = list(entries)          # [(id, fields)]
        self.last_delivered = last_delivered
        self.pending = pending or {}          # id -> consumer
        self.group = group

    @staticmethod
    def _key(entry_id):
        a, b = entry_id.split("-")
        return int(a), int(b)

    async def xinfo_groups(self, stream):
        lag = sum(1 for i, _ in self.entries if self._key(i) > self._key(self.last_delivered))
        return [{"name": self.group, "last-delivered-id": self.last_delivered, "lag": lag}]

    async def xpending_range(self, stream, group, min, max, count):
        return [{"message_id": i, "consumer": c, "time_since_delivered": 4000,
                 "times_delivered": 1} for i, c in list(self.pending.items())[:count]]

    async def xrange(self, stream, min="-", max="+", count=None):
        exclusive = min.startswith("(")
        lo = min.lstrip("(")
        out = []
        for i, f in self.entries:
            if lo != "-":
                if exclusive and self._key(i) <= self._key(lo):
                    continue
                if not exclusive and self._key(i) < self._key(lo):
                    continue
            if max != "+" and self._key(i) > self._key(max):
                continue
            out.append((i, f))
            if count and len(out) >= count:
                break
        return out


def _job(n, run_id=7, secret="hunter2"):
    payload = {"job_id": f"j{n}", "run_id": run_id, "test_case_id": 100 + n,
               "test_case": {"id": 100 + n, "name": f"case {n}",
                             "steps": [{"type": "fill", "value": secret}]},
               "settings": {"headers": {"Authorization": f"Bearer {secret}"}},
               "browser": "chromium"}
    return (_id(n), {"job_id": f"j{n}", "run_id": str(run_id), "payload": json.dumps(payload)})


def test_summary_never_carries_the_payload():
    entry_id, fields = _job(1)
    out = job_queue.summarize_job("jobs:pending", entry_id, fields, now_ms=5000)
    assert "hunter2" not in json.dumps(out)
    assert out["run_id"] == 7 and out["test_case_name"] == "case 1"
    assert out["browser"] == "chromium" and out["claimed"] is False
    assert out["waiting_seconds"] == 5.0  # entry id 1 ms, now 5000 ms


def test_side_job_has_no_run():
    out = job_queue.summarize_job("jobs:pending", "1-0", {
        "job_id": "x", "run_id": "0",
        "payload": json.dumps({"job_type": "page_check", "job_id": "x"})})
    assert out["run_id"] is None and out["job_type"] == "page_check"


def test_acknowledged_entries_are_not_waiting():
    # 1-2 acked (before last-delivered, not pending), 3 claimed, 4-5 unclaimed.
    r = FakeStreams([_job(n) for n in range(1, 6)],
                    last_delivered=_id(3), pending={_id(3): "worker-a"})
    jobs, total = asyncio.run(job_queue.waiting_in_stream(r, "jobs:pending",
                                                          "execution-workers", 200))
    assert [j["entry_id"] for j in jobs] == [_id(3), _id(4), _id(5)]
    assert total == 3
    assert jobs[0]["claimed"] and jobs[0]["consumer"] == "worker-a"
    assert not jobs[1]["claimed"]


def test_no_group_means_everything_waits():
    r = FakeStreams([_job(n) for n in range(1, 4)], group="someone-else")
    jobs, total = asyncio.run(job_queue.waiting_in_stream(r, "jobs:pending",
                                                          "execution-workers", 200))
    assert total == 3 and len(jobs) == 3


def test_list_cap_still_reports_the_true_total():
    r = FakeStreams([_job(n) for n in range(1, 11)])
    jobs, total = asyncio.run(job_queue.waiting_in_stream(r, "jobs:pending",
                                                          "execution-workers", 4))
    assert len(jobs) == 4
    assert total == 10


def test_deleted_entry_still_in_pending_list_is_skipped():
    r = FakeStreams([_job(2)], last_delivered=_id(2), pending={_id(1): "w", _id(2): "w"})
    jobs, total = asyncio.run(job_queue.waiting_in_stream(r, "jobs:pending",
                                                          "execution-workers", 200))
    assert [j["entry_id"] for j in jobs] == [_id(2)]
