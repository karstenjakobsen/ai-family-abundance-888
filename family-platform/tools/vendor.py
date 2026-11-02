"""Copy MIT-licensed browser terminal assets from npm's exact locked packages."""
from pathlib import Path
import shutil

root = Path(__file__).resolve().parents[1]
target = root / "family_workspace" / "static" / "vendor"
target.mkdir(parents=True, exist_ok=True)
for source, name in [
    ("@xterm/xterm/lib/xterm.js", "xterm.js"),
    ("@xterm/xterm/css/xterm.css", "xterm.css"),
    ("@xterm/xterm/LICENSE", "xterm-LICENSE"),
    ("@xterm/addon-fit/lib/addon-fit.js", "addon-fit.js"),
    ("@xterm/addon-fit/LICENSE", "addon-fit-LICENSE"),
]:
    shutil.copyfile(root / "node_modules" / source, target / name)
print("Vendored terminal assets and licenses:", target)
