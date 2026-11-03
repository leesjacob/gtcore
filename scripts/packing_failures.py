"""V8 packing-failure table (Paper 2 Phase 4 campaign; coordinator 2026-11-03):
the optimizer's main failure mode stated honestly.

Post-processing only -- no solver is run here.  Inputs are the campaign CSVs
of ``scripts/validation_optimize.py`` (one commit):

* ``v2_rows.csv``  -- per (cavity, N, arm[, SA seed]) status / n_placed;
* ``capacity.csv`` -- ``packing_capacity`` (greedy maximal independent set,
  a conflict-free selection) per cavity at the SAME candidate grid;
* ``v3_gap.csv``   -- optional, one or more (campaign grid / reduced grid):
  where the V100 bound packs N while greedy / SA fail (``sa_start`` =
  ``v100_bound`` marks the rev-1 restart rule).

Per (cavity, N) the table says whether truth / the V100 bound / greedy /
SA (k of the seeds) / uniform packed N, and classifies the existence of a
conflict-free N-packing ON THE CANDIDATE GRID:

* ``yes`` -- some arm returned a conflict-free selection of M >= N tiles
  (any N-subset of it packs N), or ``capacity_at_grid >= N``; the source is
  named;
* ``no (proved)`` -- the V100 bound returned status ``infeasible`` at some
  M <= N (no M-selection satisfies the conflict rows, hence none of N);
* ``unknown`` -- neither (the bound was not run at this N, or hit its time
  limit without an incumbent).

Truth is listed but never counts as evidence: the generator places its 8
truth tiles by seed clearance, not footprint, and every truth implant
overlaps under the planner rule (``n_overlaps`` > 0 on all 18 cavities), so
it is not a conflict-free packing; truth tiles are also off the candidate
grid.  Every SA failure is then labelled ``packing exists (SA/greedy start
failed)``, ``no packing exists (proved)`` or ``existence unknown``.

Usage::

    python scripts/packing_failures.py --v2 <out>/v2_rows.csv --capacity <out>/capacity.csv \
        --v3 <out>/v3_gap.csv --v3 <out_reduced>/v3_gap.csv --out docs/figures/optimize/data
"""
from __future__ import annotations

import argparse
import collections
import csv
import datetime as _dt
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                                       stderr=subprocess.DEVNULL).decode().strip()
    except Exception:                                                 # noqa: BLE001
        return "unknown"


def read_csv(path):
    with open(path, encoding="utf-8") as fh:
        head = []
        lines = []
        for ln in fh:
            (head if ln.startswith("#") else lines).append(ln)
    return head, list(csv.DictReader(lines))


def commit_of(head):
    for ln in head:
        m = re.search(r"commit: ([0-9a-f]{6,})", ln)
        if m:
            return m.group(1)
    return "?"


def write_csv(path, rows, cols, header):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        for ln in header:
            fh.write("# " + ln + "\n")
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in cols})


def n_placed(r):
    try:
        return int(float(r.get("n_placed") or 0))
    except ValueError:
        return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--v2", required=True)
    p.add_argument("--capacity", default=None)
    p.add_argument("--v3", action="append", default=[])
    p.add_argument("--out", required=True)
    a = p.parse_args(argv)
    h2, v2 = read_csv(a.v2)
    src_commit = commit_of(h2)
    cap = {}
    if a.capacity:
        hc, crow = read_csv(a.capacity)
        for r in crow:
            if r.get("dataset") == "synthetic" and r.get("status") == "ok":
                cap[r["cavity"]] = (int(float(r["capacity_at_grid"])), r.get("h_mm"), r.get("n_spins"))
    by = collections.defaultdict(list)
    for r in v2:
        by[(r["cavity"], int(r["N"]))].append(r)
    cavities = sorted({c for c, _ in by}, key=lambda k: (float(k.split("_x")[1]), k))
    ns = sorted({n for _, n in by})

    # conflict-free selections returned, per cavity: max M over ok arms (truth excluded)
    best_m = collections.defaultdict(lambda: (0, None))
    proved_none_at = {}            # cavity -> smallest N with bound status infeasible
    for (c, n), rows in by.items():
        for r in rows:
            if r["arm"] in ("truth", "truth_raw"):
                continue
            if r["status"] == "ok" and n_placed(r) >= n:
                if n > best_m[c][0]:
                    best_m[c] = (n, r["arm"])
            if r["arm"] == "v100_bound" and r["status"] == "failed" and "status infeasible" in r["reason"]:
                proved_none_at[c] = min(proved_none_at.get(c, 99), n)

    out_rows = []
    for c in cavities:
        for n in ns:
            rows = by.get((c, n), [])
            arm = collections.defaultdict(list)
            for r in rows:
                arm[r["arm"]].append(r)

            def packed(name):
                rr = arm.get(name, [])
                if not rr:
                    return "not run"
                if all(r["status"] == "skipped" for r in rr):
                    return "not run" if "restricted" in rr[0]["reason"] else "no (%d)" % max(n_placed(r) for r in rr)
                ok = sum(1 for r in rr if r["status"] == "ok")
                if name == "sa":
                    return "%d/%d" % (ok, len(rr))
                if ok:
                    return "yes"
                return "no (%d)" % max(n_placed(r) for r in rr)

            bound = arm.get("v100_bound", [])
            if not bound or bound[0]["status"] == "skipped":
                b = "not run"
            elif bound[0]["status"] == "ok":
                b = "yes (%s)" % (bound[0].get("bound_method") or "")
            elif "status infeasible" in bound[0]["reason"]:
                b = "infeasible (proved)"
            else:
                b = "no incumbent (time limit)"
            truth = arm.get("truth", [])
            t = ("8 tiles, %s planner overlaps" % truth[0]["n_overlaps"]) if truth else "-"
            m, m_arm = best_m[c]
            evid = []
            if m >= n:
                evid.append("%s packed %d" % (m_arm, m))
            if c in cap and cap[c][0] >= n:
                evid.append("capacity %d" % cap[c][0])
            if evid:
                exists = "yes"
            elif c in proved_none_at and proved_none_at[c] <= n:
                exists = "no (proved)"
            else:
                exists = "unknown"
            sa = arm.get("sa", [])
            n_sa_fail = sum(1 for r in sa if r["status"] != "ok")
            if not sa or n_sa_fail == 0:
                sa_class = "-" if not sa else "SA packed N"
            elif exists == "yes":
                sa_class = "packing exists (SA/greedy start failed)"
            elif exists == "no (proved)":
                sa_class = "no packing exists (proved)"
            else:
                sa_class = "existence unknown"
            out_rows.append({
                "cavity": c, "N": n, "truth": t if n == 8 else "-", "v100_bound": b,
                "greedy": packed("greedy"), "sa_seeds_packed": packed("sa"), "uniform": packed("uniform"),
                "capacity_at_grid": cap.get(c, (None,))[0], "max_conflict_free_packed": m,
                "packing_exists_on_grid": exists, "evidence": "; ".join(evid) or (
                    "bound infeasible at N=%d" % proved_none_at[c] if exists == "no (proved)" else ""),
                "n_sa_failed": n_sa_fail, "sa_failure_class": sa_class})

    # summary per N
    summ = []
    for n in ns:
        rr = [r for r in out_rows if r["N"] == n]
        cls = collections.Counter(r["sa_failure_class"] for r in rr)
        ex = collections.Counter(r["packing_exists_on_grid"] for r in rr)
        summ.append({"N": n, "n_cavities": len(rr),
                     "sa_all_seeds_packed": cls.get("SA packed N", 0),
                     "sa_failed_packing_exists": cls.get("packing exists (SA/greedy start failed)", 0),
                     "sa_failed_no_packing_proved": cls.get("no packing exists (proved)", 0),
                     "sa_failed_existence_unknown": cls.get("existence unknown", 0),
                     "packing_exists_yes": ex.get("yes", 0), "packing_exists_proved_no": ex.get("no (proved)", 0),
                     "packing_exists_unknown": ex.get("unknown", 0),
                     "greedy_packed": sum(1 for r in rr if r["greedy"] == "yes"),
                     "bound_packed": sum(1 for r in rr if r["v100_bound"].startswith("yes")),
                     "bound_run": sum(1 for r in rr if r["v100_bound"] != "not run")})

    # V3: where the bound packs N while greedy / SA (greedy start) fail
    v3_rows = []
    for path in a.v3:
        h3, rows3 = read_csv(path)
        grid = None
        per = collections.defaultdict(list)
        for r in rows3:
            per[(r["cavity"], int(r["N"]))].append(r)
            grid = r.get("candidate_grid") or grid
        for (c, n), rr in sorted(per.items()):
            ok = [r for r in rr if r["status"] == "ok"]
            starts = collections.Counter(r.get("sa_start") or "" for r in ok)
            reasons = " | ".join(sorted({r["reason"][:120] for r in rr if r["status"] != "ok"}))
            bound_ok = bool(ok) or any("v100_bound" not in (r.get("reason") or "") and r.get("bound_status") in
                                       ("optimal", "time_limit", "ok") for r in rr)
            v3_rows.append({"v3_file": os.path.basename(os.path.dirname(os.path.abspath(path))),
                            "candidate_grid": grid, "commit": commit_of(h3), "cavity": c, "N": n,
                            "n_seed_rows": len(rr), "n_ok": len(ok),
                            "sa_from_greedy": starts.get("greedy", 0),
                            "sa_from_bound_greedy_failed": starts.get("v100_bound", 0),
                            "bound_packed_N": "yes" if ok else ("yes" if bound_ok else "no"),
                            "failed_reason": reasons})

    os.makedirs(a.out, exist_ok=True)
    hdr = ["V8 packing failures -- scripts/packing_failures.py (post-processing of the campaign CSVs; no solver run)",
           "command: python scripts/packing_failures.py --v2 <v2_rows.csv> --capacity <capacity.csv> "
           + " ".join("--v3 <v3_gap.csv>" for _ in a.v3) + " --out <dir>",
           "campaign commit (inputs): %s; this table generated at %s, %s" % (
               src_commit, git_commit(), _dt.datetime.now().strftime("%Y-%m-%d %H:%M")),
           "datasets: synthetic cavities (make_head_phantom seeds 1-6 x scales 0.8/1.0/1.25); no patient data",
           "packing_exists_on_grid: yes = some arm returned a conflict-free selection of >= N tiles at this "
           "cavity, or packing_capacity (greedy MIS) >= N; no (proved) = the V100 bound returned infeasible "
           "at some M <= N; unknown otherwise. Candidate grid h 4 mm / 6 spins / M_opt 1000 (V2).",
           "truth never counts as evidence: the generator's 8 truth tiles overlap under the planner rule on every "
           "cavity and lie off the candidate grid",
           "SA starts from greedy (frozen solver); a failed greedy start is a FAILED SA row, never a lower score"]
    cols = ["cavity", "N", "truth", "v100_bound", "greedy", "sa_seeds_packed", "uniform", "capacity_at_grid",
            "max_conflict_free_packed", "packing_exists_on_grid", "evidence", "n_sa_failed", "sa_failure_class"]
    write_csv(os.path.join(a.out, "v8_packing_failures.csv"), out_rows, cols, hdr)
    scols = list(summ[0].keys())
    write_csv(os.path.join(a.out, "v8_packing_failures_summary.csv"), summ, scols, hdr)
    if v3_rows:
        write_csv(os.path.join(a.out, "v3_bound_packs_where_greedy_fails.csv"), v3_rows, list(v3_rows[0].keys()),
                  hdr[:3] + ["sa_from_bound_greedy_failed = SA seeds restarted from the bound's selection because "
                             "greedy could not pack N (rev-1 V3 rule) -- the bound packed N where greedy failed"])
    print("| N | cavities | SA all seeds packed | SA failed, packing exists | SA failed, none (proved) | "
          "SA failed, unknown | greedy packed | bound packed / run |")
    print("|---|---|---|---|---|---|---|---|")
    for s in summ:
        print("| %d | %d | %d | %d | %d | %d | %d | %d / %d |" % (
            s["N"], s["n_cavities"], s["sa_all_seeds_packed"], s["sa_failed_packing_exists"],
            s["sa_failed_no_packing_proved"], s["sa_failed_existence_unknown"], s["greedy_packed"],
            s["bound_packed"], s["bound_run"]))
    for r in v3_rows:
        print(r)
    return 0


if __name__ == "__main__":
    sys.exit(main())
