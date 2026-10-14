"""Golden table: every metric per video vs the re-scored per-video baseline (noise.json)
+ the track used.

usage: python3 golden_table.py <noise.json> <report.json> [<report.json> ...]
"""

import json
import subprocess
import sys

noise = json.load(open(sys.argv[1]))
base = noise["videos"]
reports = [json.load(open(p)) for p in sys.argv[2:]]


def tracks(ids: list[str]) -> dict[str, str]:
    js = (
        "const ids=%s;for(const i of ids){const d=db.videoSummaryCache.findOne("
        "{_id:ObjectId(i)},{transcriptMeta:1,language:1});const t=(d&&d.transcriptMeta)||{};"
        "print(i+'\\t'+[t.source,t.origin,t.captionTrack,t.captionLang,(d&&d.language)]"
        ".join('|'))}"
    ) % json.dumps(ids)
    cmd = ["docker", "exec", "vie-mongodb", "mongosh", "--quiet", "video-insight-engine"]
    out = subprocess.run(cmd + ["--eval", js], capture_output=True, text=True).stdout
    return dict(line.split("\t", 1) for line in out.splitlines() if "\t" in line)


def fmt(x: float | None) -> str:
    return "—" if x is None else f"{x:.3f}"


def mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


for rep, path in zip(reports, sys.argv[2:]):
    ids = [v["videoSummaryId"] for v in rep["videos"] if v.get("videoSummaryId")]
    tr = tracks(ids)
    print(f"## {path}")
    print(
        "| id | quality base→run | faith base→run | dup base→run | tabs got/exp | comp | content "
        "| empty | forbidden | assert fail | track (source|origin|kind|key|lang) |"
    )
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    sums = {"q": [], "qb": [], "f": [], "fb": [], "d": [], "db": []}
    for v in rep["videos"]:
        m = v.get("metrics") or {}
        b = base.get(v["id"]) or {}
        q = v.get("quality") or {}
        fails = sum(1 for a in v.get("assertions") or [] if not a.get("passed", a.get("ok", True)))
        n = len(v.get("assertions") or [])
        for key, mk in (("q", "quality"), ("f", "faithfulness"), ("d", "duplicateRate")):
            if m.get(mk) is not None and b.get(mk) is not None:
                sums[key].append(m[mk])
                sums[key + "b"].append(b[mk])
        print(
            f"| {v['id']} | {fmt(b.get('quality'))}→{fmt(m.get('quality'))} "
            f"| {fmt(b.get('faithfulness'))}→{fmt(m.get('faithfulness'))} "
            f"| {fmt(b.get('duplicateRate'))}→{fmt(m.get('duplicateRate'))} "
            f"| {q.get('tab_count')}/{q.get('expected_tab_count')} "
            f"| {fmt(q.get('component_coverage'))} | {fmt(q.get('content_coverage'))} "
            f"| {q.get('empty_tab_count')} | {q.get('forbidden_ok')} | {fails}/{n} "
            f"| {tr.get(v.get('videoSummaryId', ''), '?')} |"
        )
    print(
        f"| **paired mean** | {fmt(mean(sums['qb']))}→{fmt(mean(sums['q']))} "
        f"(n={len(sums['q'])}) | {fmt(mean(sums['fb']))}→{fmt(mean(sums['f']))} "
        f"(n={len(sums['f'])}) | {fmt(mean(sums['db']))}→{fmt(mean(sums['d']))} "
        f"(n={len(sums['d'])}) | | | | | | | |"
    )
    print(f"report set means: {rep.get('metrics')}  | noise full-set baseline: {noise['baseline']}")
