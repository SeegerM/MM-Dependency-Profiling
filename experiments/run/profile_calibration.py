"""Calibrates the Heterogeneity Profile: does each component measure the thing it names?

Each knob changes one kind of heterogeneity; the component named after it should move while the
others stay put.

  rename        one store's extractors are renamed; values untouched
  perturb       one store's values receive single-character edits; names untouched
  denormalize   two observation sets of one store are replaced by their join; both untouched

FD and UCC counts are taken at threshold 1.0, since HyFD-Partial's violation accounting is not
reliable for composite determinants below it.

usage: profile_calibration.py [--export=<clean>] [--out=<dir>]
  requires MM_CP; run  source core/adapters/build.sh  first.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import paths

REPO = Path(__file__).resolve().parents[2]


def flag(name, default):
    return next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith(f"--{name}=")), default)


CLEAN = Path(flag("export", str(paths.WORK_ROOT / "unibench" / "metalevel")))
OUT = Path(flag("out", str(paths.WORK_ROOT / "calibration")))

VARIANTS = [
    ("baseline", None),
    ("rename", ["--mode=rename", "--store=relational", "--rate=1.0"]),
    ("perturb", None),          # handled by perturb_export.py
    ("denormalize", ["--mode=denormalize",
                     "--join=public.invoice,public.customer,customer_id"]),
]


def run(command):
    return subprocess.run(command, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, cwd=REPO)


def build_variant(name, args):
    if name == "baseline":
        return CLEAN
    target = OUT / name
    if name == "perturb":
        run([sys.executable, str(REPO / "experiments/datasets/perturb_export.py"),
             str(CLEAN), str(target), "0.5", "--store=graph"])
    else:
        run([sys.executable, str(REPO / "experiments/datasets/vary_export.py"),
             str(CLEAN), str(target)] + args)
    return target


def discover(export, name):
    """The three algorithms plus the similarity path, all over one export."""
    results = OUT / f"results-{name}"
    results.mkdir(parents=True, exist_ok=True)
    classpath = os.environ["MM_CP"]

    run(["java", "-Xmx8g", "-cp", classpath, "SpindMetaLevel", str(export), "0.5",
         str(OUT / "temp"), str(results / "ind")])
    run(["java", "-Xmx8g", "-cp", classpath, "SpindSimilarity", str(export), "0.5", "1",
         str(OUT / "temp"), str(results / "sim")])

    fd_log, ucc_log = results / "fd.log", results / "ucc.log"
    fd_lines, ucc_lines = [], []
    for entry in json.loads((export / "catalog.json").read_text(encoding="utf-8")):
        path = export / entry["file"]
        fd_lines.append(f"--- {entry['file']}")
        ucc_lines.append(f"--- {entry['file']}")
        if entry["columns"] < 2:
            continue
        for main_class, sink in (("HyFdMultiModel", fd_lines), ("HyUccMultiModel", ucc_lines)):
            out = run(["java", "-Xmx8g", "-cp", classpath, main_class,
                       "file", str(path), "-", "1.0"]).stdout
            sink.extend(line for line in out.splitlines()
                        if line.startswith(("FDs", "UCCs")))
    fd_log.write_text("\n".join(fd_lines), encoding="utf-8")
    ucc_log.write_text("\n".join(ucc_lines), encoding="utf-8")

    profile = results / "profile.json"
    run([sys.executable, str(REPO / "profile/build_profile.py"), str(export),
         str(results / "ind"), f"--ucc={ucc_log}", f"--fd={fd_log}",
         f"--similarity={results / 'sim'}", f"--json={profile}"])
    return json.loads(profile.read_text(encoding="utf-8"))


def main():
    if not os.environ.get("MM_CP"):
        sys.exit("MM_CP is not set: run  source core/adapters/build.sh  first")
    OUT.mkdir(parents=True, exist_ok=True)

    rows = []
    for name, args in VARIANTS:
        export = build_variant(name, args)
        data = discover(export, name)
        stores = data["stores"]
        extractors = sum(s["extractors"] for s in stores.values())
        # The first three components are rates over the discovered links, not counts, so a knob
        # that changes how many links are found does not move every component at once.
        links = data["summary"]["links"] or 1
        rows.append((name, {
            "schema (renamed)": data["summary"]["renamed"] / links,
            "syntactic (sim-only)": data["summary"]["similarity_only"] / links,
            "data-level (partial)": data["summary"]["partial"] / links,
            "structural (FDs/extr)": sum(s["fds"] for s in stores.values()) / extractors,
            "scopes": sum(s["scopes"] for s in stores.values()),
            "links": data["summary"]["links"],
        }))

    components = list(rows[0][1].keys())
    print(f"\n  {'variant':<14}" + "".join(f"{c:>23}" for c in components))
    base = rows[0][1]
    for name, values in rows:
        line = f"  {name:<14}"
        for component in components:
            value = values[component]
            text = f"{value:.2f}" if isinstance(value, float) else str(value)
            if name != "baseline":
                delta = value - base[component]
                if abs(delta) > (0.005 if isinstance(value, float) else 0):
                    text += f" ({delta:+.2f})" if isinstance(value, float) else f" ({delta:+d})"
            line += f"{text:>23}"
        print(line)

    print("\n  Each knob should move its own component and leave the others alone. Read down a")
    print("  column: the row named after it should be the only one that changes materially.")


if __name__ == "__main__":
    main()
