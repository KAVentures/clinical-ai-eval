"""Mirror runtime data into caeval/_data so a built wheel is self-contained.

The harness reads tests/, configs/, prompts/, schemas/ and selection_rules.yaml at
runtime. In a checkout they sit beside the package; in a wheel they must live
INSIDE it. Run before `python -m build`; CI does this automatically.
"""
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DST = ROOT / "caeval" / "_data"
DIRS = ("tests", "configs", "prompts", "schemas")

def main() -> None:
    if DST.exists():
        shutil.rmtree(DST)
    DST.mkdir(parents=True)
    shutil.copy(ROOT / "selection_rules.yaml", DST / "selection_rules.yaml")
    for d in DIRS:
        shutil.copytree(ROOT / d, DST / d)
    print(f"bundled -> {DST.relative_to(ROOT)}: " + ", ".join(sorted(p.name for p in DST.iterdir())))

if __name__ == "__main__":
    main()
