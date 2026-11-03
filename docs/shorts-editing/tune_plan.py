"""Tune the shorts_plan draft's rules on the archive (no tokens). Run on the PC after cut_grammar_extract.py:

    uv run --project engine python docs/shorts-editing/tune_plan.py cuts.jsonl          # report only
    uv run --project engine python docs/shorts-editing/tune_plan.py cuts.jsonl --write  # also save engine/lumina/shorts_rules.json

Then commit engine/lumina/shorts_rules.json; shorts_plan / shorts_build use it from then on.
"""
import json
import sys

from lumina import shorts_calibrate as sc
from lumina import shorts_tune as st

if __name__ == "__main__":
    with open(sys.argv[1], encoding="utf-8") as f:
        rows = sc.load(f)
    rep = st.tune(rows)
    print(st.render(rep))
    if "--json" in sys.argv:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
    if "--write" in sys.argv:
        print("written:", st.write(rep))
