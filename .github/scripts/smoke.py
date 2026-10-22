"""Smoke-test a ccpaste build: `--selftest` (Pillow + sixel encoder), then fake Claude data, then `--list` and `-s <id> --list` must succeed."""
import json, os, subprocess, sys, tempfile, time

cmd = sys.argv[1:]                      # e.g. dist/ccpaste.exe   or   python ccpaste
st = subprocess.run(cmd + ["--selftest"], capture_output=True, text=True, encoding="utf-8")
print(st.stdout, st.stderr, sep="")
if st.returncode or "selftest ok" not in st.stdout:
    sys.exit("smoke failed: --selftest (rc=%d)" % st.returncode)
tmp = tempfile.mkdtemp()
cfg, root = os.path.join(tmp, "cfg"), os.path.join(tmp, "root")
sid = "e1e1e1e1-0000-4000-8000-000000000001"
img = os.path.join(root, "-x-demo", sid, "images")
os.makedirs(cfg)
os.makedirs(img)
from PIL import Image
Image.new("RGB", (64, 48), (40, 90, 160)).save(os.path.join(img, "1.png"))
with open(os.path.join(cfg, "history.jsonl"), "w") as f:
    f.write(json.dumps(dict(display="demo [Pasted text #2 +1 lines]", project="/x/demo", sessionId=sid,
                            timestamp=int(time.time() * 1000),
                            pastedContents={"2": dict(id=2, type="text", content="a\nb")})) + "\n")
env = dict(os.environ, CCPASTE_CONFIG_DIRS=cfg, CCPASTE_TMP_ROOT=root, XDG_CONFIG_HOME=os.path.join(tmp, "x"),
           APPDATA=os.path.join(tmp, "x"))
for extra, want in (([], sid[:8]), (["-s", sid[:8]], "#1")):
    out = subprocess.run(cmd + extra + ["--list"], env=env, capture_output=True, text=True, encoding="utf-8")
    print(out.stdout, out.stderr, sep="")
    if out.returncode or want not in out.stdout:
        sys.exit("smoke failed: %s --list (rc=%d)" % (" ".join(extra), out.returncode))
print("smoke ok")
