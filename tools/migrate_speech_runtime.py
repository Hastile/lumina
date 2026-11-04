"""One-time local dependency migration. Leaves the old app untouched.

Hardlinks save space on the same drive; deleting the old directory does not
delete the new links. Do not pip-upgrade either linked environment in place:
create a fresh environment for upgrades. No Phrase server code/data is copied.
"""
from pathlib import Path
import os
import shutil
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
source = Path(sys.argv[1])
target = root / 'engine' / '.speech-venv'
if (target / 'Lib' / 'site-packages' / 'stable_whisper').exists():
    raise SystemExit('Target already exists; refusing to replace a runtime')
subprocess.run([r'C:\Python314\python.exe', '-m', 'venv', '--without-pip', '--system-site-packages', str(target)], check=True)

def link_tree(src, dst):
    count = 0
    for path in src.rglob('*'):
        out = dst / path.relative_to(src)
        if path.is_dir():
            out.mkdir(parents=True, exist_ok=True)
        elif not out.exists():
            out.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(path.resolve(), out)
            except OSError:
                shutil.copy2(path, out)
            count += 1
    return count

print('Dependency files:', link_tree(source / '.venv' / 'Lib' / 'site-packages', target / 'Lib' / 'site-packages'))
print('Model files:', link_tree(source / 'models', root / '.speech-models'))
subprocess.run([str(target / 'Scripts' / 'python.exe'), '-c', 'import stable_whisper, torch, transformers, kiwipiepy; print("Lumina speech runtime ready; CUDA:", torch.cuda.is_available())'], check=True)
