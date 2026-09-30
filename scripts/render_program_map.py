"""Fill docs/program-map.md's marked tables from the track registry + routine.
  python scripts/render_program_map.py           # write
  python scripts/render_program_map.py --check   # exit 1 if the file would change (the drift test)"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from webull_api.paths import REPO_ROOT  # noqa: E402
from webull_web import program_map  # noqa: E402

DOC = REPO_ROOT / "docs" / "program-map.md"


def main() -> int:
    text = DOC.read_text(encoding="utf-8")
    out = program_map.render(text)
    if "--check" in sys.argv:
        if out != text:
            print(f"{DOC} is stale — run scripts/render_program_map.py")
            return 1
        print("program map current")
        return 0
    if out != text:
        DOC.write_text(out, encoding="utf-8", newline="\n")
        print(f"rendered {DOC}")
    else:
        print("no change")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
