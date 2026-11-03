"""Measure the shorts_plan draft against the user's archive (no tokens). Run on the PC:

    uv run --project engine python docs/shorts-editing/cut_grammar_extract.py "D:/유튜브 채널/Project" cuts.jsonl
    uv run --project engine python docs/shorts-editing/calibrate_plan.py cuts.jsonl [--json]

Change a rule in engine/lumina/shorts_plan.py, run it again, keep the change if F1 goes up.
"""
import json
import sys

from lumina import shorts_calibrate as sc

if __name__ == "__main__":
    with open(sys.argv[1], encoding="utf-8") as f:
        rep = sc.calibrate(sc.load(f))
    print(json.dumps(rep, ensure_ascii=False, indent=1) if "--json" in sys.argv else sc.render(rep))
