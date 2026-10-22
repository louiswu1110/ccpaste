import threading, queue, importlib.util, importlib.machinery, os, pty, re, select, struct, fcntl, termios, subprocess, sys, time, unittest, json
import site; sys_path_hack = __import__("sys").path.append(site.getusersitepackages())

os.environ["XDG_CONFIG_HOME"] = __import__("tempfile").mkdtemp()      # never read the real hide list
PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ccpaste")
loader = importlib.machinery.SourceFileLoader("ccpaste", PATH)
spec = importlib.util.spec_from_loader("ccpaste", loader)
ccpaste = importlib.util.module_from_spec(spec)
loader.exec_module(ccpaste)


import contextlib


@contextlib.contextmanager
def cfgdirs(*dirs):
    """Point ccpaste at fake config dirs (CCPASTE_CONFIG_DIRS) with clean caches."""
    old = os.environ.get("CCPASTE_CONFIG_DIRS")
    os.environ["CCPASTE_CONFIG_DIRS"] = ":".join(dirs)
    caches = (ccpaste._hist_cache, ccpaste._title_state, ccpaste._title_path, ccpaste._text_cache)
    for c in caches:
        c.clear()
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("CCPASTE_CONFIG_DIRS", None)
        else:
            os.environ["CCPASTE_CONFIG_DIRS"] = old
        for c in caches:
            c.clear()


@contextlib.contextmanager
def fake_tree(sids=("e6b6aaaa-0000-4000-8000-000000000001",), nimg=3):
    """Self-contained Claude data: real PNGs (Pillow) per session + history.jsonl, in a throwaway dir.
    Yields (env_overrides, tmp, cfg, root). env_overrides point a ccpaste subprocess at the fake tree."""
    import tempfile, shutil
    from PIL import Image
    tmp = tempfile.mkdtemp()
    try:
        cfg, root = os.path.join(tmp, "cfg"), os.path.join(tmp, "root")
        os.makedirs(cfg)
        now = time.time()
        rows = []
        for i, sid in enumerate(sids):
            d = os.path.join(root, "-x-demo%d" % i, sid, "images")
            os.makedirs(d)
            for n in range(1, nimg + 1):
                Image.new("RGB", (80 + 10 * n, 60), (30 * n, 90, 160)).save(os.path.join(d, "%d.png" % n))
            rows.append(dict(display="demo%d" % i, project="/x/demo%d" % i, sessionId=sid,
                             timestamp=int((now - i) * 1000), pastedContents={}))
        with open(os.path.join(cfg, "history.jsonl"), "w") as f:
            f.write("\n".join(json.dumps(r) for r in rows) + "\n")
        env = dict(CCPASTE_CONFIG_DIRS=cfg, CCPASTE_TMP_ROOT=root, XDG_CONFIG_HOME=os.path.join(tmp, "x"))
        yield env, tmp, cfg, root
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def PIL_RATIO(p):
    from PIL import Image
    w, h = Image.open(p).size
    return w / h


class T(unittest.TestCase):
    @contextlib.contextmanager
    def fake_pastes(self):
        """A throwaway Claude config dir + tmp root: one session with images 1, 2, 5 and two text pastes
        (one inline, one through paste-cache), plus a second session with one inline text paste."""
        import tempfile, shutil
        tmp = tempfile.mkdtemp()
        old_root = ccpaste.TMP_ROOT
        cfg = os.path.join(tmp, "cfg")
        os.makedirs(os.path.join(cfg, "paste-cache"))
        with cfgdirs(cfg):
            try:
                ccpaste.TMP_ROOT = os.path.join(tmp, "root")
                sid = "e1e1e1e1-0000-4000-8000-000000000001"
                d = os.path.join(ccpaste.TMP_ROOT, "-x-demo", sid, "images")
                os.makedirs(d)
                for n in (1, 2, 5):
                    open(os.path.join(d, "%d.png" % n), "wb").write(b"x")
                open(os.path.join(cfg, "paste-cache", "abc123.txt"), "w").write("cached\ntext")
                now = int(time.time() * 1000)
                rows = [
                    dict(display="p1 [Pasted text #3 +2 lines]", project="/x/demo", sessionId=sid, timestamp=now - 2000,
                         pastedContents={"3": dict(id=3, type="text", content="a\nb\nc")}),
                    dict(display="p2 [Pasted text #4 +1 lines]", project="/x/demo", sessionId=sid, timestamp=now - 1000,
                         pastedContents={"4": dict(id=4, type="text", contentHash="abc123")}),
                    dict(display="other [Pasted text #1 +1 lines]", project="/x/other", sessionId="f2f2f2f2", timestamp=now - 9000,
                         pastedContents={"1": dict(id=1, type="text", content="q\nr")}),
                ]
                with open(os.path.join(cfg, "history.jsonl"), "w") as f:
                    f.write("\n".join(json.dumps(r) for r in rows) + "\n")
                yield sid
            finally:
                ccpaste.TMP_ROOT = old_root
                shutil.rmtree(tmp, ignore_errors=True)

    def test_session_and_images(self):
        with self.fake_pastes() as fake:
            sid, proj = ccpaste.pick_session()
            self.assertEqual(sid, fake)                                   # newest session wins
            sid2, _ = ccpaste.pick_session(sid[:6])
            self.assertEqual(sid, sid2)
            real = ccpaste.image_dir(sid)
            self.assertTrue(real and os.path.isdir(real))
            imgs = [i.label for i in ccpaste.list_items(sid) if i.kind == "img"]
            self.assertEqual(imgs, ["#1", "#2", "#5"])                    # numbering follows the files, gaps stay

    def test_text(self):
        with self.fake_pastes() as fake:
            expect = {}
            for d in ccpaste.read_history():
                for k, v in (d.get("pastedContents") or {}).items():
                    if v.get("type") == "text" and ccpaste.paste_available(v, d.get("_dir")):
                        expect.setdefault(d["sessionId"], set()).add(int(v["id"]))
            self.assertEqual(expect, {fake: {3, 4}, "f2f2f2f2": {1}})
            for sid, ids in expect.items():
                got = {i.num for i in ccpaste.list_items(sid) if i.kind == "txt"}
                self.assertEqual(got, ids)
                for i in ccpaste.list_items(sid):
                    if i.kind == "txt":
                        self.assertTrue(i.label.startswith("T#"))
                        self.assertNotEqual(i.text, "[paste-cache entry unavailable]")

    def test_layout(self):
        L = ccpaste.layout(120, 40, 7, 6)
        self.assertEqual(L["per_row"], 7)
        self.assertEqual(L["strip_rows"], 1)
        self.assertEqual(L["prev_top"], 1 + 7 + 1)
        self.assertEqual(L["prev_h"], 40 - 9 - 1)
        self.assertEqual(L["body_top"], 11)
        self.assertEqual(L["tw"], 17)               # 120 cols / 7 cells, stretched to the pane width
        self.assertEqual(ccpaste.tw_for(40), (2, 20))
        L = ccpaste.layout(120, 40, 30, 29)       # 5 rows of thumbs, 1 visible, scrolled to selection
        self.assertEqual(L["first_row"], 4)
        self.assertEqual(ccpaste.cell_pos(L, 29)[0], 1)
        self.assertIsNone(ccpaste.cell_pos(L, 0))
        self.assertEqual(ccpaste.fit_pixels(2000, 1000, 800, 600), (800, 400))
        self.assertEqual(ccpaste.fit_pixels(10, 10, 800, 600), (10, 10))

    def test_sixel(self):
        from PIL import Image
        im = Image.new("RGB", (37, 25), (200, 10, 10))
        for x in range(10):
            im.putpixel((x, 3), (0, 255, 0))
        b = ccpaste.encode_sixel(im)
        self.assertTrue(b.startswith(b"\x1bP"))
        self.assertTrue(b.endswith(b"\x1b\\"))
        self.assertIn(b'"1;1;37;25', b)
        self.assertEqual(b.count(b"-"), 5)   # 25 rows -> 5 bands
        p = os.path.join(__import__("tempfile").mkdtemp(), "photo.png")
        Image.new("RGB", (1600, 900), (40, 90, 160)).save(p)            # wide image: must be scaled into 800x600
        data, wc, hc, info = ccpaste.render_thumb(p, 800, 600, 10, 20)
        self.assertTrue(data.startswith(b"\x1bP") and data.endswith(b"\x1b\\"))
        m = re.search(rb'"1;1;(\d+);(\d+)', data)
        self.assertLessEqual(int(m.group(1)), 800)
        self.assertLessEqual(int(m.group(2)), 600)
        self.assertEqual(round(int(m.group(1)) / int(m.group(2)), 1), round(PIL_RATIO(p), 1))
        # bad file -> placeholder, no crash
        bad = os.path.join(__import__("tempfile").gettempdir(), "ccpaste-test-bad.png")
        open(bad, "wb").write(b"not a png")
        self.assertIsNone(ccpaste.render_thumb(bad, 100, 100, 10, 20)[0])

    def test_session_list_fake_tree(self):
        import tempfile, shutil
        base = __import__("tempfile").gettempdir()
        tmp = tempfile.mkdtemp(dir=base)
        old = (ccpaste.TMP_ROOT,)
        os.makedirs(os.path.join(tmp, "cfg"))
        cm = cfgdirs(os.path.join(tmp, "cfg"))
        cm.__enter__()
        try:
            ccpaste.TMP_ROOT = os.path.join(tmp, "root")
            HIST = os.path.join(tmp, "cfg", "history.jsonl")
            def mk(slug, sid, names, t):
                d = os.path.join(ccpaste.TMP_ROOT, slug, sid, "images")
                os.makedirs(d)
                for n in names:
                    p = os.path.join(d, n); open(p, "wb").write(b"x"); os.utime(p, (t, t))
                os.utime(d, (t, t))
            now = time.time()
            mk("-x-projA", "aaaa1111", ["1.png", "3.png", "notes.txt"], now - 7200)
            mk("-x-projB", "bbbb2222", ["2.png"], now - 100)
            with open(HIST, "w") as f:
                rows = [
                    dict(display="second prompt", pastedContents={}, timestamp=int((now - 5000) * 1000), project="/x/projA", sessionId="aaaa1111"),
                    dict(display="first \u4e2d\u6587 prompt [Pasted text #2 +3 lines]", pastedContents={"2": dict(id=2, type="text", content="a\nb\nc")}, timestamp=int((now - 6000) * 1000), project="/x/projA", sessionId="aaaa1111"),
                    dict(display="only text", pastedContents={"1": dict(id=1, type="text", content="q")}, timestamp=int((now - 10) * 1000), project="/x/projC", sessionId="cccc3333"),
                    dict(display="nothing pasted", pastedContents={}, timestamp=int(now * 1000), project="/x/projD", sessionId="dddd4444"),
                ]
                for r in rows:
                    f.write(json.dumps(r) + "\n")
            ss = ccpaste.build_sessions()
            self.assertEqual([e["sid"] for e in ss], ["cccc3333", "bbbb2222", "aaaa1111"])   # newest first, empty dropped
            by = {e["sid"]: e for e in ss}
            self.assertEqual((by["aaaa1111"]["nimg"], by["aaaa1111"]["ntxt"]), (2, 1))
            self.assertEqual((by["bbbb2222"]["nimg"], by["bbbb2222"]["ntxt"]), (1, 0))
            self.assertEqual((by["cccc3333"]["nimg"], by["cccc3333"]["ntxt"]), (0, 1))
            self.assertTrue(by["aaaa1111"]["prompt"].startswith("first \u4e2d\u6587 prompt"))
            self.assertEqual(by["bbbb2222"]["prompt"], "")
            self.assertEqual(by["aaaa1111"]["proj"], "projA")
            self.assertAlmostEqual(by["aaaa1111"]["ts"], now - 5000, delta=2)   # latest history ts beats image mtime
            self.assertEqual(len(ccpaste.match_sessions("dddd")), 1)                # -s still sees empty sessions
            l1, l2, idt = ccpaste.side_row(ss[2], 40, time.time())
            self.assertTrue(idt.startswith("aaaa1111"))
            self.assertIn("projA", l2)
            self.assertIn("2 img / 1 txt", l2)
            self.assertEqual(sum(ccpaste.cwidth(ch) for ch in l1 + idt), 40)
            self.assertEqual(by["aaaa1111"]["title"], "first \u4e2d\u6587 prompt [Pasted text #2 +3 lines]")   # no transcript -> prompt
            allrow = ccpaste.all_entry(ss)
            self.assertEqual(ccpaste.side_row(allrow, 40)[0].strip(), "\u2605 All sessions")
            self.assertIn("3 img / 2 txt", ccpaste.side_row(allrow, 40)[1])
            # All view: every session's items, time ordered, newest last, labels carry the 4-char session prefix
            allitems = ccpaste.list_items_all(ss)
            self.assertEqual([i.dlabel(True) for i in allitems],
                             ["aaaa #1", "aaaa #3", "aaaa T#2", "bbbb #2", "cccc T#1"])
        finally:
            ccpaste.TMP_ROOT = old[0]
            cm.__exit__(None, None, None)
            shutil.rmtree(tmp)

    def test_worker_priority_cache_stale(self):
        gate, calls = threading.Event(), []
        def fn(tag):
            calls.append(tag)
            if tag == "first":
                gate.wait(5)
            return (b"x", 1, 1, tag)
        w = ccpaste.Worker(fn=fn)
        w.request("k0", ("first",), 5)
        t0 = time.time()
        while "first" not in calls and time.time() - t0 < 2:
            time.sleep(0.01)
        for key, pr in (("low", 9), ("stale", 3), ("high", 0), ("mid", 4)):
            w.request(key, (key,), pr)
        w.keep({"low", "high", "mid", "k0"})                 # "stale" dropped
        gate.set()
        t0 = time.time()
        while len(w.log) < 4 and time.time() - t0 < 3:
            time.sleep(0.01)
        self.assertEqual(list(w.log), ["k0", "high", "mid", "low"])
        self.assertIsNone(w.get("stale"))
        n = len(calls)
        w.request("high", ("high",), 0)                      # cached -> no new job
        time.sleep(0.1)
        self.assertEqual(len(calls), n)
        self.assertEqual(w.get("high")[3], "high")
        self.assertEqual(w.take_done(), {"k0", "high", "mid", "low"})

    def test_worker_inflight_bytes_error(self):
        gate, calls = threading.Event(), []
        def fn(tag):
            calls.append(tag)
            if tag == "slow":
                gate.wait(5)
            if tag == "boom":
                raise RuntimeError("x")
            return (b"y" * 1000, 1, 1, tag)
        w = ccpaste.Worker(fn=fn, max_bytes=2500)
        w.request("slow", ("slow",), 0)
        t0 = time.time()
        while not calls and time.time() - t0 < 2:
            time.sleep(0.01)
        w.request("slow", ("slow",), 0)                     # in flight: must not be queued again
        self.assertNotIn("slow", w.pending)
        gate.set()
        t0 = time.time()
        while "slow" not in [k for k in w.log] and time.time() - t0 < 2:
            time.sleep(0.01)
        self.assertEqual(calls.count("slow"), 1)
        w.keep({"slow"})                                    # protected on-screen entry survives eviction
        for k in ("a", "b", "c"):
            w.request(k, (k,), 1)
        t0 = time.time()
        while len(w.log) < 4 and time.time() - t0 < 2:
            time.sleep(0.01)
        self.assertIsNotNone(w.get("slow"))
        self.assertLessEqual(w.cbytes, 2500 + 1200)
        self.assertLess(len(w.cache), 4)
        w.request("boom", ("boom",), 0)
        t0 = time.time()
        while not w.error and time.time() - t0 < 2:
            time.sleep(0.01)
        self.assertIn("worker error", w.error)
        w.request("after", ("after",), 0)                   # thread still alive
        t0 = time.time()
        while "after" not in w.log and time.time() - t0 < 2:
            time.sleep(0.01)
        self.assertIn("after", w.log)
        self.assertEqual(w.log.maxlen, 256)

    def test_titles(self):
        import tempfile, shutil
        base = __import__("tempfile").gettempdir()
        tmp = tempfile.mkdtemp(dir=base)
        cm = cfgdirs(tmp)
        cm.__enter__()
        try:
            d = os.path.join(tmp, "projects", "-x-p"); os.makedirs(d)
            f = os.path.join(d, "tttt0001.jsonl")
            L = lambda **k: json.dumps(k) + "\n"
            with open(f, "w") as fh:
                fh.write(L(type="user", message="ai-title decoy"))
                fh.write(L(type="ai-title", aiTitle="first title", sessionId="tttt0001"))
                fh.write(L(type="ai-title", aiTitle="second  title", sessionId="tttt0001"))
            self.assertEqual(ccpaste.session_title("tttt0001", "prompt"), "second title")        # last ai-title wins
            off1 = ccpaste._title_state[f]["off"]
            with open(f, "a") as fh:
                fh.write(L(type="ai-title", aiTitle="third title"))
                fh.write('{"type":"ai-title","aiTitle":"half writ')                          # partial line ignored
            self.assertEqual(ccpaste.session_title("tttt0001"), "third title")
            self.assertGreater(ccpaste._title_state[f]["off"], off1)
            self.assertLess(ccpaste._title_state[f]["off"], os.path.getsize(f))                # read only complete lines
            with open(f, "a") as fh:
                fh.write('n"}\n' + L(type="custom-title", customTitle="my name"))
            self.assertEqual(ccpaste.session_title("tttt0001"), "my name")                       # custom beats ai
            with open(f, "w") as fh:                                                           # truncation -> re-read
                fh.write(L(type="ai-title", aiTitle="fresh"))
            self.assertEqual(ccpaste.session_title("tttt0001"), "fresh")
            self.assertEqual(ccpaste.session_title("nope0000", "  my   first\nprompt "), "my first prompt")
            self.assertEqual(ccpaste.session_title("nope0000", ""), "(untitled)")
        finally:
            cm.__exit__(None, None, None)
            shutil.rmtree(tmp)

    def test_sidebar_geometry(self):
        self.assertEqual(ccpaste.sidebar_geom(120), (33, 34))
        self.assertEqual(ccpaste.sidebar_geom(100)[0], 28)
        self.assertEqual(ccpaste.sidebar_geom(200)[0], 42)          # clamp max
        self.assertEqual(ccpaste.sidebar_geom(70)[0], 22)           # clamp min
        self.assertEqual(ccpaste.sidebar_geom(120, False), (0, 0))  # hidden: right pane at column 0
        self.assertEqual(ccpaste.sidebar_geom(50), (0, 0))          # too narrow: auto-hide
        for cols in (60, 90, 120, 250):
            sbw, x0 = ccpaste.sidebar_geom(cols)
            self.assertGreaterEqual(x0, sbw + 1)
            L = ccpaste.layout(cols - x0, 40, 5, 0)
            self.assertLessEqual(L["per_row"] * L["tw"], cols - x0)   # right pane never overlaps the sidebar
        app = ccpaste.App()
        app.show_side = True
        app.items = [ccpaste.Item("img", 1, path="/nonexistent.png", sig=(1, 1), sid="s")]
        app.cellpx, app.cellsrc = (10, 20), "CCPASTE_CELL"
        import unittest.mock as um
        with um.patch.object(ccpaste, "winsize", return_value=(40, 120, 0, 0)):
            ps = app.placements()
            self.assertEqual(app.x0, 34)
            for p in ps:
                self.assertGreaterEqual(p["c"] + app.x0, 34)   # 0-based column >= sidebar width + 1
            app.show_side = False
            app.placements()
            self.assertEqual(app.x0, 0)

    def test_tmp_root_env_and_image_formats(self):
        u = os.getuid()
        self.assertEqual(ccpaste.default_tmp_root({}), "/tmp/claude-%d" % u)
        self.assertEqual(ccpaste.default_tmp_root({"CLAUDE_CODE_TMPDIR": "/var/x//"}), "/var/x/claude-%d" % u)
        self.assertEqual(ccpaste.default_tmp_root({"CLAUDE_CODE_TMPDIR": ""}), "/tmp/claude-%d" % u)
        self.assertEqual(ccpaste.default_tmp_root({"CCPASTE_TMP_ROOT": "/q"}), "/q")
        import tempfile, shutil
        tmp = tempfile.mkdtemp()
        old = (ccpaste.TMP_ROOT,)
        cm = cfgdirs(tmp)
        cm.__enter__()
        try:
            ccpaste.TMP_ROOT = os.path.join(tmp, "root")
            d = os.path.join(ccpaste.TMP_ROOT, "-x-p", "ffff0001", "images")
            os.makedirs(d)
            for n in ("1.png", "2.webp", "3.jpg", "4.gif", "notes.txt"):
                open(os.path.join(d, n), "wb").write(b"x")
            self.assertEqual([i.label for i in ccpaste.list_items("ffff0001")], ["#1", "#2", "#3", "#4"])
            self.assertEqual(ccpaste.build_sessions()[0]["nimg"], 4)
        finally:
            ccpaste.TMP_ROOT = old[0]
            cm.__exit__(None, None, None)
            shutil.rmtree(tmp)

    def test_multiple_config_dirs(self):
        import tempfile, shutil
        old_root = ccpaste.TMP_ROOT
        tmp = tempfile.mkdtemp()
        try:
            a, b = os.path.join(tmp, "a"), os.path.join(tmp, "b")
            for d in (a, b):
                os.makedirs(os.path.join(d, "projects", "-x-p"))
                os.makedirs(os.path.join(d, "paste-cache"))
            now = int(time.time() * 1000)
            def hist(d, sid, pc, disp):
                with open(os.path.join(d, "history.jsonl"), "a") as f:
                    f.write(json.dumps(dict(display=disp, pastedContents=pc, timestamp=now, project="/x/p", sessionId=sid)) + "\n")
            hist(a, "aaaa0001", {"1": dict(id=1, type="text", content="from a")}, "d1")
            hist(b, "bbbb0002", {"1": dict(id=1, type="text", contentHash="h123")}, "d2")
            hist(b, "bbbb0002", {"1": dict(id=1, type="text", contentHash="h123")}, "d2")     # duplicate line, deduped
            open(os.path.join(b, "paste-cache", "h123.txt"), "w").write("from b cache")
            with open(os.path.join(b, "projects", "-x-p", "bbbb0002.jsonl"), "w") as f:       # transcript only in dir b
                f.write(json.dumps(dict(type="ai-title", aiTitle="Title from B")) + "\n")
            with cfgdirs(a, b):
                ccpaste.TMP_ROOT = os.path.join(tmp, "noimages")
                ss = {e["sid"]: e for e in ccpaste.build_sessions()}
                self.assertEqual(set(ss), {"aaaa0001", "bbbb0002"})
                self.assertEqual(ss["bbbb0002"]["title"], "Title from B")               # transcript found in the other dir
                self.assertEqual(ss["bbbb0002"]["ntxt"], 1)                               # duplicate not double counted
                self.assertEqual(ccpaste.list_items("bbbb0002")[0].text, "from b cache")  # paste-cache of its own dir
                self.assertEqual(ccpaste.list_items("aaaa0001")[0].text, "from a")
                self.assertTrue(ccpaste.list_items("aaaa0001")[0].path.startswith(a))
                self.assertEqual(ccpaste.list_items("bbbb0002")[0].path, os.path.join(b, "paste-cache", "h123.txt"))   # cache file
            with cfgdirs(a):
                self.assertEqual([e["sid"] for e in ccpaste.build_sessions()], ["aaaa0001"])
            home = os.path.join(tmp, "home")
            os.makedirs(os.path.join(home, ".claude-b", "projects")); os.makedirs(os.path.join(home, ".claude-empty"))
            os.makedirs(os.path.join(home, ".claude"))
            env = {"HOME": home}
            old = os.environ.get("HOME"); os.environ["HOME"] = home
            try:
                got = [os.path.basename(x) for x in ccpaste.config_dirs(env)]
                self.assertEqual(got, [".claude-b"] if not os.path.isdir(os.path.join(home, ".claude", "projects")) else [".claude", ".claude-b"])
                env["CLAUDE_CONFIG_DIR"] = os.path.join(home, ".claude-empty")
                self.assertIn(".claude-empty", [os.path.basename(x) for x in ccpaste.config_dirs(env)])   # explicit env dir always counts
            finally:
                os.environ["HOME"] = old
        finally:
            ccpaste.TMP_ROOT = old_root
            shutil.rmtree(tmp)

    def test_text_helpers(self):
        self.assertEqual(ccpaste.fit_left("/a/b/c/d.png", 8), "\u2026c/d.png")
        self.assertEqual(ccpaste.fit_left("/a.png", 10), "/a.png    ")
        self.assertEqual(ccpaste.osc52("/tmp/x.png"), "\x1b]52;c;L3RtcC94LnBuZw==\x07")

    def test_pty_narrow_wide_image_bounds(self):
        import tempfile, shutil
        from PIL import Image
        tmp = tempfile.mkdtemp()
        try:
            d = os.path.join(tmp, "root", "-x-w", "wide0001", "images")
            os.makedirs(d); os.makedirs(os.path.join(tmp, "cfg"))
            Image.new("RGB", (1600, 400), (30, 120, 200)).save(os.path.join(d, "1.png"))
            Image.new("RGB", (400, 400), (200, 60, 60)).save(os.path.join(d, "2.png"))
            m, sl = pty.openpty()
            fcntl.ioctl(sl, termios.TIOCSWINSZ, struct.pack("HHHH", 38, 107, 0, 0))
            env = dict(os.environ, CCPASTE_CELL="10x20", TERM="xterm", CCPASTE_TMP_ROOT=os.path.join(tmp, "root"),
                       CCPASTE_CONFIG_DIRS=os.path.join(tmp, "cfg"))
            p = subprocess.Popen([PATH, "-s", "wide"], stdin=sl, stdout=sl, stderr=sl, env=env, close_fds=True,
                                 start_new_session=True, preexec_fn=lambda: fcntl.ioctl(0, termios.TIOCSCTTY, 0))
            os.close(sl)
            buf = b""
            def pump(t):
                nonlocal buf
                end = time.time() + t
                while time.time() < end:
                    r, _, _ = select.select([m], [], [], 0.1)
                    if r:
                        try:
                            buf += os.read(m, 65536)
                        except OSError:
                            return
            pump(2.5)
            os.write(m, b"h"); pump(2.0)                      # select the 4:1 image (#1)
            os.write(m, b"q"); pump(0.5)
            self.assertEqual(p.wait(timeout=5), 0)
            sbw, x0 = ccpaste.sidebar_geom(107)
            draws = re.findall(rb"\x1b7\x1b\[(\d+);(\d+)H\x1bP9;1q\"1;1;(\d+);(\d+)", buf)
            self.assertGreaterEqual(len(draws), 3)
            for row, col, w, h in draws:
                col0, cells = int(col) - 1, -(-int(w) // 10)
                self.assertGreaterEqual(col0, x0)                 # never over the sidebar/divider
                self.assertLessEqual(col0 + cells, 107)           # never past the right edge
                self.assertLessEqual(int(row) - 1 + -(-int(h) // 20), 37)   # never onto the footer row
            wide = [d for d in draws if int(d[2]) == 4 * int(d[3])]
            self.assertTrue(wide)                                 # the 4:1 preview keeps its aspect ratio
            self.assertNotIn(b"Traceback", buf)
        finally:
            shutil.rmtree(tmp)

    # ---------------------------------------------------------------- mouse / platform
    def test_sgr_parser(self):
        P = ccpaste.InputParser
        p = P()
        ev = p.feed("\x1b[<0;12;5M")
        self.assertEqual(ev[0][0], "mouse")
        m = ev[0][1]
        self.assertEqual((m["kind"], m["btn"], m["x"], m["y"]), ("down", 0, 11, 4))      # 1-based -> 0-based
        # split across reads
        p = P()
        self.assertEqual(p.feed("\x1b[<0;1"), [])
        self.assertEqual(p.feed("2;"), [])
        ev = p.feed("5M")
        self.assertEqual([e[1]["kind"] for e in ev], ["down"])
        self.assertEqual(p.buf, "")
        p = P()
        self.assertEqual(p.feed("\x1b"), [])
        self.assertEqual(p.feed("["), [])
        self.assertEqual(p.feed("<35;7;8"), [])
        self.assertEqual([e[1]["kind"] for e in p.feed("M")], ["move"])               # cb 35 = motion, no button
        k = lambda s: [e[1]["kind"] for e in P().feed(s) if e[0] == "mouse"]
        self.assertEqual(k("\x1b[<0;1;1m"), ["up"])
        self.assertEqual(k("\x1b[<32;1;1M"), ["drag"])
        self.assertEqual(k("\x1b[<64;1;1M\x1b[<65;1;1M"), ["scroll", "scroll"])
        self.assertEqual([e[1]["dir"] for e in P().feed("\x1b[<64;1;1M\x1b[<65;1;1M")], ["up", "down"])
        self.assertTrue(P().feed("\x1b[<16;1;1M")[0][1]["ctrl"])
        self.assertTrue(P().feed("\x1b[<8;1;1M")[0][1]["alt"])
        self.assertTrue(P().feed("\x1b[<4;1;1M")[0][1]["shift"])
        # keys and mouse mixed in one read
        ev = P().feed("a\x1b[<0;3;3Mb\x1b[A")
        self.assertEqual([e[0] for e in ev], ["key", "mouse", "key", "key"])
        self.assertEqual([e[1] for e in ev if e[0] == "key"], ["a", "b", "up"])
        # lone ESC is held, then released as the Esc key; an ESC that starts a sequence is not
        p = P()
        self.assertEqual(p.feed("\x1b", now=100.0), [])
        self.assertEqual(p.flush(now=100.05), [])
        self.assertEqual(p.flush(now=100.2), [("key", "\x1b")])
        self.assertEqual(p.buf, "")
        p = P()
        p.feed("\x1b", now=1.0)
        self.assertEqual(p.feed("[B", now=1.01), [("key", "down")])
        # an orphaned partial mouse sequence is dropped after the hold time, never leaked as keys
        p = P()
        p.feed("\x1b[<0;1", now=5.0)
        self.assertEqual(p.flush(now=5.2), [])
        self.assertEqual(p.flush(now=6.0), [])
        self.assertEqual(p.buf, "")
        self.assertEqual(ccpaste.parse_keys("\x1b[Cq"), ["right", "q"])

    def _app_with_items(self, n=3):
        app = ccpaste.App()
        app.rows = [dict(sid="*", proj="all", title="all", ts=0, nimg=0, ntxt=0, prompt=""),
                    dict(sid="s1", proj="p", title="t1", ts=0, nimg=1, ntxt=0, prompt=""),
                    dict(sid="s2", proj="p", title="t2", ts=0, nimg=1, ntxt=0, prompt="")]
        app.items = [ccpaste.Item("txt", i + 1, text="word%d\n" % i * 20, sid="s1", ts=1.0) for i in range(n)]
        app.sid, app.sel = "s1", 0
        return app

    def test_hit_testing(self):
        import unittest.mock as um
        for rows, cols in ((40, 120), (30, 90), (50, 200)):
            with um.patch.object(ccpaste, "winsize", return_value=(rows, cols, 0, 0)):
                app = self._app_with_items()
                L = app.L()
                x0 = app.x0
                self.assertGreater(x0, 0)
                self.assertEqual(app.hit(5, 0), ("header",))
                self.assertEqual(app.hit(5, rows - 1), ("footer",))
                self.assertEqual(app.hit(2, 1), ("side", 0))
                self.assertEqual(app.hit(2, 2), ("side", 0))          # a row is two lines
                self.assertEqual(app.hit(2, 3), ("side", 1))
                self.assertEqual(app.hit(2, 5), ("side", 2))
                self.assertEqual(app.hit(2, 7), ("side_blank",))
                self.assertEqual(app.hit(x0 - 1, 10), ("divider",))
                self.assertEqual(app.hit(x0 + 1, L["strip_top"] + 1), ("thumb", 0))
                self.assertEqual(app.hit(x0 + L["tw"] + 1, L["strip_top"] + 3), ("thumb", 1))
                self.assertEqual(app.hit(x0 + 3 * L["tw"] + 1, L["strip_top"] + 3), ("strip",))   # past the last item
                self.assertEqual(app.hit(x0 + 4, L["prev_top"]), ("info",))
                self.assertEqual(app.hit(x0 + 4, L["prev_top"] + 1), ("path", 3))
                self.assertEqual(app.hit(x0 + 4, L["body_top"] + 2), ("preview", 3, 2))
                self.assertEqual(app.hit(x0 + 4, rows - 2)[0], "preview")
                app.show_side = False                                  # hidden sidebar: right pane at column 0
                app.L()
                self.assertEqual(app.x0, 0)
                self.assertEqual(app.hit(1, app.L()["strip_top"] + 1), ("thumb", 0))

    def test_selection_text_cjk_multiline(self):
        lines = ["hello world", "\u4e2d\u6587\u5b57\u7b26 test", "third line"]
        self.assertEqual(ccpaste.selection_text(lines, (0, 6), (1, 3)), "world\n\u4e2d\u6587")
        self.assertEqual(ccpaste.selection_text(lines, (1, 3), (0, 6)), "world\n\u4e2d\u6587")     # either direction
        self.assertEqual(ccpaste.selection_text(lines, (1, 2), (1, 4)), "\u6587\u5b57")
        self.assertEqual(ccpaste.selection_text(lines, (1, 3), (1, 3)), "\u6587")                   # inclusive, wide char
        self.assertEqual(ccpaste.selection_text(lines, (0, 0), (2, 99)), "\n".join(lines))
        self.assertEqual(ccpaste.selection_text(lines, (0, 4), (2, 2)), "o world\n\u4e2d\u6587\u5b57\u7b26 test\nthi")
        self.assertEqual(ccpaste.col_to_index(lines[1], 1), 0)
        self.assertEqual(ccpaste.col_to_index(lines[1], 2), 1)
        self.assertEqual(ccpaste.col_to_index(lines[1], 99), len(lines[1]))
        self.assertEqual(ccpaste.word_range("hello world", 8), (6, 10))
        self.assertEqual(ccpaste.word_range("\u4e2d\u6587\u5b57\u7b26 test", 1), (0, 7))
        self.assertEqual(ccpaste.word_range("a  b", 1), (1, 2))
        self.assertIsNone(ccpaste.word_range("abc", 9))
        self.assertEqual(ccpaste.hl_line("hello", 1, 2), "h\x1b[7mel\x1b[0mlo")
        self.assertEqual(ccpaste.hl_line("\u4e2d\u6587x", 2, None), "\u4e2d\x1b[7m\u6587x\x1b[0m")

    def test_selection_state_machine(self):
        import unittest.mock as um
        with um.patch.object(ccpaste, "winsize", return_value=(40, 120, 0, 0)), \
             um.patch.object(ccpaste, "out_write"), \
             um.patch.object(ccpaste, "copy_text", return_value="xclip") as cp:
            app = self._app_with_items(1)
            app.items[0].text = "alpha beta gamma\ndelta epsilon\n" * 3
            app.focus = "main"
            L = app.L()
            x, y = app.x0 + 1, L["body_top"]
            app.on_down(x + 1, y)                                         # plain click: pressed, nothing copied
            self.assertEqual(app.sel_state["state"], "pressed")
            app.on_up(x + 1, y)
            self.assertIsNone(app.sel_state)
            cp.assert_not_called()
            app.last_down = None                                          # not a double click
            app.on_down(x + 1, y)
            app.on_drag(x + 5, y + 1, force=True)
            self.assertEqual(app.sel_state["state"], "extending")
            app.on_up(x + 5, y + 1)
            self.assertEqual(cp.call_args[0][0], "lpha beta gamma\ndelta ")
            self.assertIsNone(app.sel_state)
            self.assertIn("copied 22 chars", app.toast["text"])
            # double click selects the word under the pointer and copies on release
            app.on_down(x + 1 + 6, y)
            app.on_up(x + 1 + 6, y)
            app.on_down(x + 1 + 6, y)
            self.assertEqual(app.sel_state["state"], "word")
            app.on_up(x + 1 + 6, y)
            self.assertEqual(cp.call_args[0][0], "beta")

    def test_windows_paths_and_modes(self):
        j = os.path.join
        self.assertEqual(ccpaste.default_tmp_root({"TEMP": "C:\\Temp"}, win=True), j("C:\\Temp", "claude"))
        self.assertEqual(ccpaste.default_tmp_root({"CLAUDE_CODE_TMPDIR": "D:\\x\\", "TEMP": "C:\\Temp"}, win=True), j("D:\\x", "claude"))
        self.assertEqual(ccpaste.default_tmp_root({"CCPASTE_TMP_ROOT": "Q:\\r"}, win=True), "Q:\\r")
        self.assertEqual(ccpaste.default_tmp_root({"CLAUDE_CODE_TMPDIR": "/v/"}, win=False), "/v/claude-%d" % os.getuid())
        self.assertEqual(ccpaste.short_project("C:\\Users\\L\\proj\\"), "proj")
        self.assertEqual(ccpaste.short_project("/x/y/z"), "z")
        import tempfile, shutil
        tmp = tempfile.mkdtemp()
        try:
            home = j(tmp, "up")
            for d in (".claude", ".claude-b", ".claude-none"):
                os.makedirs(j(home, d, "projects") if d != ".claude-none" else j(home, d))
            got = [os.path.basename(x) for x in ccpaste.config_dirs({"USERPROFILE": home, "HOME": "/nonexistent"}, win=True)]
            self.assertEqual(got, [".claude", ".claude-b"])
            a, b = j(tmp, "a"), j(tmp, "b")
            os.makedirs(a); os.makedirs(b)
            self.assertEqual(ccpaste.config_dirs({"CCPASTE_CONFIG_DIRS": a + ";" + b}, win=True), [a, b])      # ';' on Windows
            self.assertEqual(ccpaste.config_dirs({"CCPASTE_CONFIG_DIRS": a + ":" + b}, win=False), [a, b])
        finally:
            shutil.rmtree(tmp)
        m = ccpaste.win_input_mode(0x1 | 0x2 | 0x4 | 0x40 | 0x10 | 0x20)
        for bit in (0x1, 0x2, 0x4, 0x40, 0x10):
            self.assertFalse(m & bit, hex(bit))                  # raw, quick-edit off, no native mouse in VT mode
        for bit in (0x80, 0x8, 0x200, 0x20):
            self.assertTrue(m & bit, hex(bit))                   # extended flags, window input, VT input, insert kept
        m2 = ccpaste.win_input_mode(0, vt=False)
        self.assertTrue(m2 & 0x10 and not m2 & 0x200)             # fallback: native mouse records
        self.assertEqual(ccpaste.win_output_mode(0), 0x5)
        self.assertEqual(ccpaste.win_output_mode(0x8), 0xD)

    def test_module_imports_portable(self):
        import ast, py_compile, unittest.mock as um
        py_compile.compile(PATH, cfile=os.path.join(__import__("tempfile").mkdtemp(), "x.pyc"), doraise=True)
        tree = ast.parse(open(PATH).read())
        bad = {"fcntl", "termios", "tty", "msvcrt", "ctypes", "pty", "select"}
        for node in tree.body:                                   # module level, outside any if/def/class
            if isinstance(node, ast.Import):
                self.assertFalse(bad & {a.name.split(".")[0] for a in node.names}, ast.dump(node))
            elif isinstance(node, ast.ImportFrom):
                self.assertNotIn((node.module or "").split(".")[0], bad)
        # executing the module as if on Windows, with the Unix modules unimportable, must work
        blocked = {m: None for m in ("fcntl", "termios", "tty")}
        loader2 = importlib.machinery.SourceFileLoader("ccpaste_win", PATH)
        mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("ccpaste_win", loader2))
        with um.patch.dict(sys.modules, blocked), um.patch.object(os, "name", "nt"):
            loader2.exec_module(mod)
        self.assertTrue(mod.IS_WIN)
        self.assertEqual(mod.TMP_ROOT, os.path.join(os.environ.get("TEMP") or os.environ.get("TMP") or __import__("tempfile").gettempdir(), "claude")
                         if not os.environ.get("CCPASTE_TMP_ROOT") and not os.environ.get("CLAUDE_CODE_TMPDIR") else mod.TMP_ROOT)

    def test_clipboard_backend(self):
        wh = lambda *have: (lambda n: "/bin/" + n if n in have else None)
        cb = ccpaste.clipboard_backend
        self.assertEqual(cb({}, "win32", wh())[0], "win32")
        self.assertEqual(cb({}, "darwin", wh("pbcopy"))[0], "pbcopy")
        self.assertEqual(cb({}, "darwin", wh())[0], "osc52")
        self.assertEqual(cb({"WAYLAND_DISPLAY": "w", "DISPLAY": ":0"}, "linux", wh("wl-copy", "xclip"))[0], "wl-copy")
        self.assertEqual(cb({"DISPLAY": ":0"}, "linux", wh("xclip", "xsel"))[0], "xclip")
        self.assertEqual(cb({"DISPLAY": ":0"}, "linux", wh("xsel"))[0], "xsel")
        self.assertEqual(cb({"DISPLAY": ":0"}, "linux", wh())[0], "osc52")
        self.assertEqual(cb({"DISPLAY": ":0", "SSH_TTY": "x"}, "linux", wh("xclip"))[0], "osc52")
        self.assertEqual(cb({"DISPLAY": ":0"}, "linux", wh("xclip"), wsl=True)[0], "osc52")
        self.assertTrue(ccpaste.is_wsl({"WSL_DISTRO_NAME": "U"}))
        out = []
        import unittest.mock as um
        with um.patch.object(ccpaste, "clipboard_backend", return_value=("osc52", None)):
            self.assertEqual(ccpaste.copy_text("hi", out.append), "osc52")
        self.assertEqual(out, ["\x1b]52;c;aGk=\x07"])

    def test_pty_mouse(self):
        import tempfile, shutil
        from PIL import Image
        tmp = tempfile.mkdtemp()
        try:
            d = os.path.join(tmp, "root", "-x-m", "mous0001", "images")
            os.makedirs(d); os.makedirs(os.path.join(tmp, "cfg"))
            Image.new("RGB", (300, 200), (30, 120, 200)).save(os.path.join(d, "1.png"))
            os.utime(os.path.join(d, "1.png"), (time.time() - 100, time.time() - 100))
            text = "\n".join("line %02d alpha beta gamma \u4e2d\u6587" % i for i in range(60))
            with open(os.path.join(tmp, "cfg", "history.jsonl"), "w") as f:
                f.write(json.dumps(dict(display="m", pastedContents={"2": dict(id=2, type="text", content=text)},
                                        timestamp=int(time.time() * 1000), project="/x/m", sessionId="mous0001")) + "\n")
            m, sl = pty.openpty()
            fcntl.ioctl(sl, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
            env = dict(os.environ, CCPASTE_CELL="10x20", TERM="xterm", CCPASTE_TMP_ROOT=os.path.join(tmp, "root"),
                       CCPASTE_CONFIG_DIRS=os.path.join(tmp, "cfg"))
            for k in ("DISPLAY", "WAYLAND_DISPLAY"):
                env.pop(k, None)                                   # never touch the real clipboard from a test
            env["WSL_DISTRO_NAME"] = "test"                        # -> OSC 52 backend
            p = subprocess.Popen([PATH, "-s", "mous"], stdin=sl, stdout=sl, stderr=sl, env=env, close_fds=True,
                                 start_new_session=True, preexec_fn=lambda: fcntl.ioctl(0, termios.TIOCSCTTY, 0))
            os.close(sl)
            buf = b""
            def pump(t):
                nonlocal buf
                end = time.time() + t
                while time.time() < end:
                    r, _, _ = select.select([m], [], [], 0.05)
                    if r:
                        try:
                            buf += os.read(m, 65536)
                        except OSError:
                            return
            def send(s, t=0.25):
                os.write(m, s.encode()); pump(t)
            pump(2.5)
            self.assertIn(b"\x1b[?1002h", buf)                       # mouse tracking enabled
            self.assertIn(b"\x1b[?1006h", buf)
            x0 = ccpaste.sidebar_geom(120)[1]
            send("\x1b[<0;5;4M\x1b[<0;5;4m", 0.8)                  # click sidebar row (All sessions is row 0; this is row 1)
            send("\x1b[<0;%d;3M\x1b[<0;%d;3m" % (x0 + 4, x0 + 4), 0.8)          # click first thumbnail (the image)
            self.assertIn(b"\x1bP9;1q", buf)                         # sixel emitted
            send("\x1b[<65;%d;3M" % (x0 + 4))                       # wheel over thumbnails -> next item (text)
            send("\x1b[<64;%d;3M" % (x0 + 4))
            send("\x1b[<65;%d;3M" % (x0 + 4), 0.6)
            mark = len(buf)
            body = 11 + 1                                           # 1-based row of the first text line (body_top=11)
            send("\x1b[<65;%d;%dM" % (x0 + 10, body + 3))           # wheel over the text preview scrolls it
            send("\x1b[<0;%d;%dM" % (x0 + 5, body))                 # drag-select over text
            send("\x1b[<32;%d;%dM" % (x0 + 9, body + 1))
            send("\x1b[<32;%d;%dM" % (x0 + 12, body + 2))
            send("\x1b[<0;%d;%dm" % (x0 + 12, body + 2), 0.5)
            self.assertIn(b"\x1b]52;c;", buf[mark:])                 # copied through OSC 52
            self.assertIn(b"copied", buf[mark:])
            mark = len(buf)
            for _ in range(2):                                      # double click -> word
                send("\x1b[<0;%d;%dM\x1b[<0;%d;%dm" % (x0 + 8, body, x0 + 8, body), 0.05)
            pump(0.5)
            self.assertIn(b"\x1b]52;c;", buf[mark:])
            send("\x1b[<0;%d;10M\x1b[<0;%d;10m" % (x0, x0), 0.8)    # click the divider: sidebar hides
            send("\x1b[<0;1;10M\x1b[<0;1;10m", 0.3)
            send("\x1b[<0;200;200M\x1b[<0;200;200m", 0.2)           # out-of-range click must not crash
            os.write(m, b"q"); pump(0.8)
            self.assertEqual(p.wait(timeout=5), 0)
            self.assertNotIn(b"Traceback", buf)
            tail = buf[-120:]
            for seq in (b"\x1b[?1000l", b"\x1b[?1002l", b"\x1b[?1006l", b"\x1b[?1049l"):
                self.assertIn(seq, tail)                            # mouse tracking disabled on exit
        finally:
            shutil.rmtree(tmp)

    def test_hide_store_roundtrip_and_corrupt(self):
        import tempfile, shutil
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, "sub", "hidden.json")
            hs = ccpaste.HideStore(path).load()                      # missing file: empty, no crash
            self.assertEqual((hs.sessions, hs.items, hs.days()), (set(), set(), None))
            self.assertTrue(hs.toggle_session("s1"))
            self.assertTrue(hs.toggle_item("s1", "img", 3))
            self.assertTrue(hs.toggle_item("s2", "txt", "7"))
            self.assertFalse(hs.toggle_item("s2", "txt", 7))              # same key via int id: toggles back
            d = json.load(open(path))
            self.assertEqual(sorted(d), ["auto_hide_days", "items", "sessions", "version"])
            self.assertEqual((d["version"], d["sessions"], d["auto_hide_days"]), (1, ["s1"], None))
            self.assertEqual(sorted(os.listdir(os.path.dirname(path))), ["hidden.json"])   # atomic: no temp file left
            hs2 = ccpaste.HideStore(path).load()
            self.assertEqual((hs2.sessions, hs2.items), (hs.sessions, hs.items))
            self.assertTrue(hs2.item_hidden("s1", "img", 3))
            self.assertFalse(hs2.toggle_session("s1"))               # toggle back
            self.assertEqual(ccpaste.HideStore(path).load().sessions, set())
            # auto_hide_days survives a save and is validated
            json.dump(dict(version=1, sessions=[], items=[], auto_hide_days=30), open(path, "w"))
            hs3 = ccpaste.HideStore(path).load()
            self.assertEqual(hs3.days(), 30)
            hs3.toggle_session("x")
            self.assertEqual(json.load(open(path))["auto_hide_days"], 30)
            hs3.auto_cli = 5
            self.assertEqual(hs3.days(), 5)                          # --hide-older wins and is not saved
            self.assertEqual(json.load(open(path))["auto_hide_days"], 30)
            # corrupt / wrong-shaped files: empty store, .bak kept, never an exception, can save afterwards
            for junk in ("{not json", "[1,2,3]", '{"sessions": 5}'):
                open(path, "w").write(junk)
                for f in os.listdir(os.path.dirname(path)):
                    if f.endswith(".bak"):
                        os.remove(os.path.join(os.path.dirname(path), f))
                hc = ccpaste.HideStore(path).load()
                self.assertEqual((hc.sessions, hc.items), (set(), set()), junk)
                self.assertEqual(open(path + ".bak").read(), junk)
                self.assertTrue(hc.toggle_session("ok"))
                self.assertEqual(ccpaste.HideStore(path).load().sessions, {"ok"})
            self.assertEqual(ccpaste.hidden_path({"XDG_CONFIG_HOME": "/x"}, win=False), "/x/ccpaste/hidden.json")
            self.assertTrue(ccpaste.hidden_path({"HOME": "/h"}, win=False).endswith("/h/.config/ccpaste/hidden.json"))
            self.assertEqual(ccpaste.hidden_path({"APPDATA": "C:\\A"}, win=True), os.path.join("C:\\A", "ccpaste", "hidden.json"))
        finally:
            shutil.rmtree(tmp)

    def test_hide_filters_counts_all_view_and_auto(self):
        import tempfile, shutil
        tmp = tempfile.mkdtemp()
        old_root, old_hide = ccpaste.TMP_ROOT, ccpaste.HIDE
        cm = cfgdirs(os.path.join(tmp, "cfg"))
        os.makedirs(os.path.join(tmp, "cfg"))
        cm.__enter__()
        try:
            ccpaste.TMP_ROOT = os.path.join(tmp, "root")
            now = time.time()
            def mk(sid, names, t):
                d = os.path.join(ccpaste.TMP_ROOT, "-x-p", sid, "images")
                os.makedirs(d)
                for n in names:
                    p = os.path.join(d, n); open(p, "wb").write(b"x"); os.utime(p, (t, t))
                os.utime(d, (t, t))
            mk("aaaa1111", ["1.png", "3.png"], now - 100)
            mk("bbbb2222", ["2.png"], now - 200)
            mk("cccc3333", ["1.png"], now - 5 * 86400)
            with open(os.path.join(tmp, "cfg", "history.jsonl"), "w") as f:
                f.write(json.dumps(dict(display="d", pastedContents={"2": dict(id=2, type="text", content="t")},
                                        timestamp=int((now - 150) * 1000), project="/x/p", sessionId="aaaa1111")) + "\n")
            ccpaste.HIDE = ccpaste.HideStore(os.path.join(tmp, "h.json")).load()
            ss = {e["sid"]: e for e in ccpaste.build_sessions()}
            self.assertEqual({k: (e["nimg"], e["ntxt"]) for k, e in ss.items()},
                             {"aaaa1111": (2, 1), "bbbb2222": (1, 0), "cccc3333": (1, 0)})
            ccpaste.HIDE.toggle_item("aaaa1111", "img", 3)
            ccpaste.HIDE.toggle_item("aaaa1111", "txt", 2)
            ccpaste.HIDE.toggle_session("bbbb2222")
            sl = ccpaste.build_sessions()
            self.assertEqual([(e["sid"], e["nimg"], e["ntxt"]) for e in sl], [("aaaa1111", 1, 0), ("cccc3333", 1, 0)])
            self.assertEqual([i.dlabel(True) for i in ccpaste.list_items_all(sl)], ["cccc #1", "aaaa #1"])
            self.assertEqual([i.label for i in ccpaste.list_items("aaaa1111")], ["#1"])
            self.assertEqual(ccpaste.all_entry(sl)["nimg"], 2)
            # show hidden: everything back, flagged
            sh = {e["sid"]: e for e in ccpaste.build_sessions(show_hidden=True)}
            self.assertEqual((sh["aaaa1111"]["nimg"], sh["aaaa1111"]["ntxt"]), (2, 1))
            self.assertTrue(sh["bbbb2222"]["hidden"] and not sh["aaaa1111"]["hidden"])
            hi = {i.label: i.hidden for i in ccpaste.list_items("aaaa1111", True)}
            self.assertEqual(hi, {"#1": False, "#3": True, "T#2": True})
            self.assertIn("bbbb2222", [e["sid"] for e in ccpaste.build_sessions(include_empty=True, always=("bbbb2222",))])
            # auto-hide by age (computed, nothing written)
            ccpaste.HIDE.auto_cli = 2
            sl = ccpaste.build_sessions()
            self.assertEqual([e["sid"] for e in sl], ["aaaa1111"])           # cccc is 5 days old
            sh = {e["sid"]: e for e in ccpaste.build_sessions(show_hidden=True)}
            self.assertTrue(sh["cccc3333"]["hidden"] and sh["cccc3333"]["auto_hidden"])
            self.assertFalse(sh["bbbb2222"]["auto_hidden"] and False)
            self.assertNotIn("cccc3333", ccpaste.HIDE.sessions)
            self.assertFalse(os.path.exists(os.path.join(tmp, "h.json")) and "cccc3333" in open(os.path.join(tmp, "h.json")).read())
            ccpaste.HIDE.auto_cli = None
            self.assertEqual(len(ccpaste.build_sessions()), 2)               # without N nothing is auto-hidden
            # App: auto-hidden sessions are not toggled; hide / undo actions
            import unittest.mock as um
            ccpaste.HIDE.auto_cli = 2
            with um.patch.object(ccpaste, "winsize", return_value=(40, 120, 0, 0)), um.patch.object(ccpaste, "out_write"):
                app = ccpaste.App()
                app.show_hidden = True
                app.refresh()
                app.hide_session("cccc3333")
                self.assertNotIn("cccc3333", ccpaste.HIDE.sessions)
                self.assertIn("by age", app.toast["text"])
        finally:
            ccpaste.TMP_ROOT, ccpaste.HIDE = old_root, old_hide
            cm.__exit__(None, None, None)
            shutil.rmtree(tmp)

    def test_pty_hide(self):
        import tempfile, shutil
        from PIL import Image
        tmp = tempfile.mkdtemp()
        try:
            root, cfg, xdg = (os.path.join(tmp, n) for n in ("root", "cfg", "xdg"))
            now = time.time()
            for sid, t in (("hida0001", now - 10), ("hidb0002", now - 100)):
                d = os.path.join(root, "-x-h", sid, "images"); os.makedirs(d)
                Image.new("RGB", (200, 100), (10, 100, 200)).save(os.path.join(d, "1.png"))
                os.utime(os.path.join(d, "1.png"), (t, t)); os.utime(d, (t, t))
            os.makedirs(cfg)
            with open(os.path.join(cfg, "history.jsonl"), "w") as f:
                f.write(json.dumps(dict(display="h", pastedContents={"2": dict(id=2, type="text", content="hello")},
                                        timestamp=int((now - 50) * 1000), project="/x/h", sessionId="hidb0002")) + "\n")
            m, sl = pty.openpty()
            fcntl.ioctl(sl, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
            env = dict(os.environ, CCPASTE_CELL="10x20", TERM="xterm", CCPASTE_TMP_ROOT=root, CCPASTE_CONFIG_DIRS=cfg,
                       XDG_CONFIG_HOME=xdg)
            p = subprocess.Popen([PATH], stdin=sl, stdout=sl, stderr=sl, env=env, close_fds=True, start_new_session=True,
                                 preexec_fn=lambda: fcntl.ioctl(0, termios.TIOCSCTTY, 0))
            os.close(sl)
            buf = b""
            def pump(t):
                nonlocal buf
                end = time.time() + t
                while time.time() < end:
                    r, _, _ = select.select([m], [], [], 0.05)
                    if r:
                        try:
                            buf += os.read(m, 65536)
                        except OSError:
                            return
            def key(k, t=0.7):
                os.write(m, k.encode()); pump(t)
            def state():
                try:
                    d = json.load(open(os.path.join(xdg, "ccpaste", "hidden.json")))
                except OSError:
                    return [], []
                return d["sessions"], d["items"]
            pump(2.0)
            key("d")                                                  # hide newest session (hida0001)
            self.assertEqual(state(), (["hida0001"], []))
            self.assertIn(b"u to undo", buf)
            key("u")                                                  # u undoes the last hide
            self.assertEqual(state(), ([], []))
            key("d")                                                  # hide again
            self.assertEqual(state(), (["hida0001"], []))
            key("H")                                                  # show hidden (dimmed, with marker)
            self.assertIn("\u2298".encode(), buf)
            key("k")                                                  # back onto the hidden session
            key("d")                                                  # d un-hides a shown hidden session
            self.assertEqual(state(), ([], []))
            key("H")                                                  # hide hidden again
            key("j"); key("\t")                                      # session hidb0002, focus the right pane
            key("d")                                                  # hide its selected item (newest = T#2)
            self.assertEqual(state(), ([], ["hidb0002:txt:2"]))
            key("\x1b[<2;40;3M\x1b[<2;40;3m")                       # right-click a thumbnail: opens the menu, hides nothing
            self.assertEqual(len(state()[1]), 1)
            key("\x1b")                                              # Esc closes the menu
            os.write(m, b"q"); pump(0.8)
            self.assertEqual(p.wait(timeout=5), 0)
            self.assertNotIn(b"Traceback", buf)
            self.assertEqual(state()[0], [])
            self.assertIn("hidb0002:txt:2", state()[1])
        finally:
            shutil.rmtree(tmp)

    def test_selection_invalidated_when_item_changes(self):
        import unittest.mock as um
        with um.patch.object(ccpaste, "winsize", return_value=(40, 120, 0, 0)), um.patch.object(ccpaste, "out_write"), \
             um.patch.object(ccpaste, "copy_text", return_value="osc52") as cp:
            app = self._app_with_items(3)
            app.items[1] = ccpaste.Item("img", 2, path="/nonexistent.png", sig=(1, 1), sid="s1", ts=1.0)
            app.focus = "main"
            L = app.L()
            x, y = app.x0 + 2, L["body_top"]
            # wheel over the strip while the button is held: selection moves to an image -> no crash, no selection
            app.on_down(x, y)
            app.on_drag(x + 6, y + 1, force=True)
            self.assertEqual(app.sel_state["state"], "extending")
            app.move(1)
            self.assertIsNone(app.sel_state)
            app.on_drag(x + 7, y + 2, force=True)
            app.sel_tick()
            app.on_up(x + 7, y + 2)
            cp.assert_not_called()
            # selection moves to ANOTHER TEXT item without going through move() (e.g. the 1 s refresh): no copy
            app.sel = 0
            app.on_down(x, y)
            app.on_drag(x + 6, y + 1, force=True)
            app.sel = 2
            app.on_up(x + 6, y + 1)
            cp.assert_not_called()
            self.assertIsNone(app.sel_state)
            # refresh with changed items / session switch / focus switch / resize clear it too
            saved = list(app.items)
            for action in (lambda: app.load_selected(), lambda: app.set_focus("side"), lambda: app.draw_all()):
                app.items = list(saved)
                app.focus = "main"
                app.sel = 0
                app.sel_state = dict(region="text", anchor=(0, 0), cursor=(1, 1), state="extending", key=app.items[0].key)
                action()
                self.assertIsNone(app.sel_state)

    def test_selection_maps_back_to_original_text(self):
        text = "aaaa bbbb cccc\tdd\nsecond line"
        spans = ccpaste.wrap_spans(text, 6)
        self.assertEqual(ccpaste.wrap_text(text, 6), [sp[0] for sp in spans])
        w = 6
        # soft wraps add no newline; the tab stays a tab; the real line break stays a newline
        full = ccpaste.selection_text_src(text, w, (0, 0), (len(spans) - 1, 99))
        self.assertEqual(full, text)
        part = ccpaste.selection_text_src("hello wide world", 6, (0, 2), (2, 1))
        self.assertEqual(part, "llo wide wor")
        self.assertEqual(ccpaste.selection_text_src("a\tb", 80, (0, 0), (0, 4)), "a\t")      # tab covers cols 1-4
        self.assertEqual(ccpaste.selection_text_src("a\tb", 80, (0, 5), (0, 5)), "b")
        self.assertEqual(ccpaste.selection_text_src("\u4e2d\u6587\u5b57\u7b26\u6587", 4, (0, 1), (1, 3)), "\u4e2d\u6587\u5b57\u7b26")
        self.assertEqual(ccpaste.selection_text_src("one\r\ntwo", 80, (0, 0), (1, 2)), "one\ntwo")
        self.assertEqual(ccpaste.selection_text_src("x\n\ny", 80, (0, 0), (2, 0)), "x\n\ny")
        self.assertEqual(ccpaste.selection_text_src("abc", 80, (0, 2), (0, 0)), "abc")           # either direction

    def test_extended_buttons_ignored_and_blank_sidebar_line(self):
        import unittest.mock as um
        for cb, kind in ((128, "other"), (129, "other"), (64, "scroll"), (67, "scroll"), (0, "down")):
            ev = ccpaste.InputParser().feed("\x1b[<%d;5;5M" % cb)[0][1]
            self.assertEqual(ev["kind"], kind, cb)
        self.assertNotEqual(ccpaste.InputParser().feed("\x1b[<128;5;5M")[0][1]["kind"], "scroll")
        with um.patch.object(ccpaste, "winsize", return_value=(39, 120, 0, 0)):      # rows-2 odd: last line is blank
            app = ccpaste.App()
            app.rows = [dict(sid="s%d" % i, proj="p", title="t", ts=0, nimg=1, ntxt=0, prompt="") for i in range(25)]
            self.assertEqual(app.hit(2, 36), ("side", 17))
            self.assertEqual(app.hit(2, 35), ("side", 17))
            self.assertEqual(app.hit(2, 37), ("side_blank",))
            app.sfirst = 3
            self.assertEqual(app.hit(2, 36), ("side", 20))

    def test_clipboard_tool_runs_off_the_ui_thread(self):
        import unittest.mock as um
        q = queue.Queue()
        out = []
        with um.patch.object(ccpaste, "clipboard_backend", return_value=("xclip", ["sleep", "5"])):
            t0 = time.time()
            self.assertEqual(ccpaste.copy_text("x" * 100000, out.append, post=q.put, timeout=0.3), "xclip")
            self.assertLess(time.time() - t0, 0.2)                        # returns immediately
            self.assertEqual(q.get(timeout=3), ("clip_fail", "x" * 100000))
        self.assertEqual(out, [])                                          # the UI thread writes the OSC 52 fallback
        with um.patch.object(ccpaste, "clipboard_backend", return_value=("xclip", ["definitely-not-a-tool-xyz"])):
            self.assertEqual(ccpaste.copy_text("hi", out.append, post=q.put), "xclip")
            self.assertEqual(q.get(timeout=3), ("clip_fail", "hi"))
            self.assertEqual(ccpaste.copy_text("hi", out.append), "osc52")          # synchronous path falls back directly
        self.assertEqual(out, ["\x1b]52;c;aGk=\x07"])
        with um.patch.object(ccpaste, "winsize", return_value=(40, 120, 0, 0)), um.patch.object(ccpaste, "out_write") as ow:
            app = ccpaste.App()
            app.on_clip_fail("hi")
            self.assertEqual((app.toast["text"], app.toast["color"]), ("copied", "green"))   # never mentions OSC 52
            self.assertTrue(any("52;c;" in str(c) for c in ow.call_args_list))

    def test_fatal_reader_event_and_restore_flush(self):
        import inspect, tempfile, shutil, unittest.mock as um
        self.assertIn("TCSAFLUSH", inspect.getsource(ccpaste.UnixTerm._restore))
        tmp = tempfile.mkdtemp()
        old_root = ccpaste.TMP_ROOT
        cm = cfgdirs(tmp)
        cm.__enter__()
        try:
            ccpaste.TMP_ROOT = os.path.join(tmp, "none")
            class FakeTerm:
                q = queue.Queue()
            FakeTerm.q.put(("fatal", "boom trace"))
            with um.patch.object(ccpaste, "winsize", return_value=(40, 120, 0, 0)), um.patch.object(ccpaste, "out_write"):
                with self.assertRaises(RuntimeError) as cm2:
                    ccpaste.App().run(FakeTerm)
            self.assertIn("boom trace", str(cm2.exception))
            # a failing reader thread posts ("fatal", traceback) instead of dying silently
            t = ccpaste.UnixTerm()
            t._read_loop = lambda: (_ for _ in ()).throw(ValueError("reader broke"))
            t._reader()
            kind, payload = t.q.get(timeout=1)
            self.assertEqual(kind, "fatal")
            self.assertIn("reader broke", payload)
        finally:
            ccpaste.TMP_ROOT = old_root
            cm.__exit__(None, None, None)
            shutil.rmtree(tmp)

    def test_toast_geometry_and_expiry(self):
        import unittest.mock as um
        with um.patch.object(ccpaste, "winsize", return_value=(40, 120, 0, 0)), um.patch.object(ccpaste, "out_write") as ow:
            app = ccpaste.App()
            app.toast = dict(text="copied 22 chars", color="green", until=0)
            self.assertEqual(app.toast_geom(), (36, 120 - 21 - 1, 21, 3))      # right-aligned, width = text + marker + 4, just above the footer
            b = app.toast_bytes().decode()
            for piece in ("\u256d", "\u2713", "copied 22 chars", "\x1b[32m", "\u256f"):
                self.assertIn(piece, b)
            app.toast["color"] = "yellow"
            self.assertIn("\x1b[33m", app.toast_bytes().decode())
            app.toast["text"] = "\u5df2\u8907\u88fd"                                    # CJK counts two columns each
            self.assertEqual(app.toast_geom()[2], 12)
            t = [100.0]
            app.clock = lambda: t[0]
            app.toast = None
            app.show_toast("x", "green", 1.5)
            self.assertIsNotNone(app.toast)
            t[0] = 101.4
            app.toast_tick()
            self.assertIsNotNone(app.toast)
            t[0] = 101.6
            app.toast_tick()
            self.assertIsNone(app.toast)
        with um.patch.object(ccpaste, "winsize", return_value=(24, 12, 0, 0)):          # narrow window: clamped
            app = ccpaste.App()
            app.toast = dict(text="copied 22 chars", color="green", until=0)
            row, col, w, h = app.toast_geom()
            self.assertEqual((col, w, h, row + h <= 23), (0, 12, 3, True))
            self.assertIn("\u256d", app.toast_bytes().decode())

    def test_menu_geometry_items_and_actions(self):
        import unittest.mock as um
        self.assertEqual(ccpaste.menu_geom(10, 5, 14, 5, 120, 40), (5, 10))
        self.assertEqual(ccpaste.menu_geom(115, 5, 14, 5, 120, 40), (5, 102))           # flips left at the right edge
        self.assertEqual(ccpaste.menu_geom(10, 37, 14, 5, 120, 40), (33, 10))           # flips up at the bottom
        self.assertEqual(ccpaste.menu_geom(115, 38, 14, 5, 120, 40), (34, 102))
        for px in (0, 50, 119):
            for py in range(0, 40):
                row, col = ccpaste.menu_geom(px, py, 14, 5, 120, 40)
                self.assertTrue(row >= 0 and col >= 0 and row + 5 <= 39 and col + 14 <= 120, (px, py))   # footer row free
        self.assertEqual(ccpaste.menu_geom(0, 0, 14, 5, 10, 4), (0, 0))                 # tiny window: clamped, no crash
        with um.patch.object(ccpaste, "winsize", return_value=(40, 120, 0, 0)), um.patch.object(ccpaste, "out_write") as ow, \
             um.patch.object(ccpaste, "copy_text", return_value="xclip") as cp:
            app = self._app_with_items(2)
            app.items[0] = ccpaste.Item("img", 5, path="/nonexistent.png", sig=(1, 1), sid="s1", ts=1.0)
            app.items[1].path = "/c/paste-cache/abc.txt"
            labels = lambda tg: [(l, a, e) for l, a, e in app.menu_items(tg)]
            self.assertEqual([x[0] for x in labels(("item", 0))], ["Copy path", "Copy object", "Hide"])     # same for image...
            self.assertEqual([x[0] for x in labels(("item", 1))], ["Copy path", "Copy object", "Hide"])     # ...and text
            app.items[0].hidden = True
            self.assertEqual(labels(("item", 0))[2][0], "Unhide")
            self.assertEqual([x[0] for x in labels(("session", "s1"))], ["Hide session", "Copy session id"])
            app.rows[1]["hidden"] = True
            self.assertEqual(labels(("session", "s1"))[0][0], "Unhide session")
            self.assertEqual(labels(("session", "*")), [])                                  # All cannot be hidden / has no menu
            # open at the pointer, keyboard: down + Enter on "Copy object" (text -> full text), Esc closes
            app.focus = "main"
            app.sel = 1
            app.open_menu(("item", 1), 60, 20)
            self.assertIsNotNone(app.menu)
            self.assertIn(b"\x1b[?1003h", b"".join(c[0][0] for c in ow.call_args_list if isinstance(c[0][0], bytes)))
            app.handle_key("down")
            app.handle_key("\r")
            self.assertIsNone(app.menu)
            self.assertEqual(cp.call_args[0][0], app.items[1].text)
            self.assertEqual(app.toast["text"], "copied %d chars" % len(app.items[1].text))
            # Copy path of a text paste = its paste-cache file; mouse: hover then click row 0
            app.open_menu(("item", 1), 60, 20)
            m = app.menu
            app.on_mouse(dict(kind="move", btn=3, x=m["col"] + 3, y=m["row"] + 2, shift=False, alt=False, ctrl=False))
            self.assertEqual(app.menu["sel"], 1)                                            # hover highlights a row
            app.on_mouse(dict(kind="down", btn=0, x=m["col"] + 3, y=m["row"] + 1, shift=False, alt=False, ctrl=False))
            self.assertIsNone(app.menu)
            self.assertEqual(cp.call_args[0][0], "/c/paste-cache/abc.txt")
            self.assertEqual(app.toast["text"], "copied path")
            # click outside closes without acting; Esc / q close
            n = cp.call_count
            app.open_menu(("item", 1), 60, 20)
            app.on_mouse(dict(kind="down", btn=0, x=2, y=30, shift=False, alt=False, ctrl=False))
            self.assertIsNone(app.menu)
            app.open_menu(("item", 1), 60, 20)
            app.handle_key("\x1b")
            self.assertIsNone(app.menu)
            app.open_menu(("item", 1), 60, 20)
            app.handle_key("q")                                                             # q closes the menu, never quits
            self.assertIsNone(app.menu)
            self.assertEqual(cp.call_count, n)
            # motion tracking goes back to 1002 once the menu is closed
            sent = b"".join(c[0][0] for c in ow.call_args_list if isinstance(c[0][0], bytes))
            self.assertIn(b"\x1b[?1003l\x1b[?1002h", sent)

    def test_image_clipboard_plans_and_fallback(self):
        import base64, unittest.mock as um
        wh = lambda *have: (lambda n: "/bin/" + n if n in have else None)
        plan = ccpaste.image_clip_plan
        self.assertEqual(plan("/p/a.png", "win32", False, {}, wh())["name"], "win32")
        p = plan("/p/it's.png", "linux", False, {"WAYLAND_DISPLAY": "w", "DISPLAY": ":0"}, wh("wl-copy", "xclip"))
        self.assertEqual((p["argv"], p["stdin_png"]), (["wl-copy", "--type", "image/png"], True))
        p = plan("/p/a.png", "linux", False, {"DISPLAY": ":0"}, wh("xclip"))
        self.assertEqual(p["argv"], ["xclip", "-selection", "clipboard", "-t", "image/png", "-i"])
        self.assertIsNone(plan("/p/a.png", "linux", False, {}, wh()))
        self.assertIsNone(plan("/p/a.png", "darwin", False, {}, wh()))
        p = plan('/p/a "q".png', "darwin", False, {}, wh("osascript"))
        self.assertEqual(p["argv"][:2], ["osascript", "-e"])
        self.assertIn('POSIX file "/p/a \\"q\\".png"', p["argv"][2])
        self.assertIn("\u00abclass PNGf\u00bb", p["argv"][2])
        # WSL: PowerShell via -EncodedCommand; a single quote in the Windows path is doubled
        p = plan("/mnt/c/x/O'Brien.png", "linux", True, {}, wh(), win_path="C:\\Users\\O'Brien\\a.png")
        self.assertEqual(p["argv"][:4], ["powershell.exe", "-NoProfile", "-STA", "-EncodedCommand"])
        script = base64.b64decode(p["argv"][4]).decode("utf-16-le")
        self.assertIn("FromFile('C:\\Users\\O''Brien\\a.png')", script)
        self.assertIn("System.Windows.Forms", script)
        self.assertIn("Clipboard]::SetImage", script)
        self.assertEqual(ccpaste.ps_quote("a'b"), "'a''b'")
        # dispatch: png bytes on stdin for wl-copy, argv recorded, failure -> False
        import tempfile, shutil
        from PIL import Image
        tmp = tempfile.mkdtemp()
        try:
            img = os.path.join(tmp, "a.jpg")
            Image.new("RGB", (4, 4), (1, 2, 3)).save(img)
            calls = []
            with um.patch.object(ccpaste, "run_tool", side_effect=lambda argv, data, timeout: calls.append((argv, data)) or True):
                self.assertTrue(ccpaste.copy_image_blocking(img, "linux", {"WAYLAND_DISPLAY": "w"}, False, wh("wl-copy")))
            self.assertEqual(calls[0][0], ["wl-copy", "--type", "image/png"])
            self.assertTrue(calls[0][1].startswith(b"\x89PNG"))                              # converted from JPEG
            with um.patch.object(ccpaste, "run_tool", return_value=False):
                self.assertFalse(ccpaste.copy_image_blocking(img, "linux", {"WAYLAND_DISPLAY": "w"}, False, wh("wl-copy")))
            self.assertFalse(ccpaste.copy_image_blocking(img, "linux", {}, False, wh()))       # no backend
            # App: image failure posts img_fail; the handler copies the path and shows a yellow toast
            with um.patch.object(ccpaste, "winsize", return_value=(40, 120, 0, 0)), um.patch.object(ccpaste, "out_write"), \
                 um.patch.object(ccpaste, "copy_image_blocking", return_value=False), \
                 um.patch.object(ccpaste, "copy_text", return_value="xclip") as cp:
                app = ccpaste.App()
                class T:
                    q = queue.Queue()
                app.term = T
                app.items = [ccpaste.Item("img", 1, path=img, sig=(1, 1), sid="s", ts=1.0)]
                app.copy_object(0)
                self.assertEqual(T.q.get(timeout=3), ("img_fail", img))
                app.on_img_fail(img)
                self.assertEqual(cp.call_args[0][0], img)
                self.assertEqual((app.toast["text"], app.toast["color"]), ("couldn't copy image \u2014 copied its path instead", "yellow"))
            with um.patch.object(ccpaste, "copy_image_blocking", return_value=True):
                app.copy_object(0)
                self.assertEqual(T.q.get(timeout=3), ("toast", ("copied image", "green")))
        finally:
            shutil.rmtree(tmp)

    def _pty_session(self, tmp, env_extra=None, args=()):
        m, sl = pty.openpty()
        fcntl.ioctl(sl, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
        env = dict(os.environ, CCPASTE_CELL="10x20", TERM="xterm", CCPASTE_TMP_ROOT=os.path.join(tmp, "root"),
                   CCPASTE_CONFIG_DIRS=os.path.join(tmp, "cfg"), XDG_CONFIG_HOME=os.path.join(tmp, "xdg"))
        for k in ("DISPLAY", "WAYLAND_DISPLAY"):
            env.pop(k, None)
        env["WSL_DISTRO_NAME"] = "test"                           # clipboard -> OSC 52, image copy -> fails -> path
        env.update(env_extra or {})
        p = subprocess.Popen([PATH, *args], stdin=sl, stdout=sl, stderr=sl, env=env, close_fds=True, start_new_session=True,
                             preexec_fn=lambda: fcntl.ioctl(0, termios.TIOCSCTTY, 0))
        os.close(sl)
        return m, p

    def test_pty_context_menu_and_toast(self):
        import tempfile, shutil
        from PIL import Image
        tmp = tempfile.mkdtemp()
        try:
            d = os.path.join(tmp, "root", "-x-m", "menu0001", "images"); os.makedirs(d)
            os.makedirs(os.path.join(tmp, "cfg"))
            Image.new("RGB", (600, 600), (30, 120, 200)).save(os.path.join(d, "1.png"))
            os.utime(os.path.join(d, "1.png"), (time.time() - 100, time.time() - 100))
            text = "\n".join("line %02d alpha beta gamma" % i for i in range(60))
            with open(os.path.join(tmp, "cfg", "history.jsonl"), "w") as f:
                f.write(json.dumps(dict(display="m", pastedContents={"2": dict(id=2, type="text", content=text)},
                                        timestamp=int(time.time() * 1000), project="/x/m", sessionId="menu0001")) + "\n")
            m, p = self._pty_session(tmp, args=("-s", "menu"))
            buf = b""
            def pump(t):
                nonlocal buf
                end = time.time() + t
                while time.time() < end:
                    r, _, _ = select.select([m], [], [], 0.05)
                    if r:
                        try:
                            buf += os.read(m, 65536)
                        except OSError:
                            return
            def send(s, t=0.5):
                os.write(m, s.encode()); pump(t)
            hidden = os.path.join(tmp, "xdg", "ccpaste", "hidden.json")
            x0 = ccpaste.sidebar_geom(120)[1]
            pump(2.5)
            # --- drag-select text: toast with box characters, shown above the footer
            body = 12
            mark = len(buf)
            send("\x1b[<0;%d;%dM" % (x0 + 5, body)); send("\x1b[<32;%d;%dM" % (x0 + 9, body + 1))
            send("\x1b[<32;%d;%dM" % (x0 + 12, body + 2)); send("\x1b[<0;%d;%dm" % (x0 + 12, body + 2), 0.5)
            self.assertIn("\u256d".encode(), buf[mark:])
            self.assertIn(b"copied", buf[mark:])
            self.assertIn("\u2713".encode(), buf[mark:])
            self.assertIn(b"\x1b[32m", buf[mark:])                        # green: the terminal route is not a warning
            self.assertNotIn(b"\x1b[33m", buf[mark:])
            self.assertNotIn(b"OSC", buf[mark:])
            pump(2.5)                                                     # toast expires; text preview repainted
            # --- right-click the image thumbnail, Esc: nothing changes
            send("\x1b[<2;%d;3M\x1b[<2;%d;3m" % (x0 + 4, x0 + 4), 0.6)
            self.assertIn(b"\x1b[?1003h", buf)                            # hover tracking only while the menu is open
            mark = len(buf)
            send("\x1b", 0.6)
            self.assertIn(b"\x1b[?1003l", buf[mark:])
            self.assertFalse(os.path.exists(hidden))
            # --- m, then Enter on "Copy path": OSC 52 + toast
            mark = len(buf)
            send("m", 0.6); send("\r", 0.6)
            self.assertIn(b"\x1b]52;c;", buf[mark:])
            self.assertIn(b"copied", buf[mark:])
            # --- the toast overlapped the big image preview: after it expires the preview Sixel is re-emitted
            pump(0.5)
            mark = len(buf)
            pump(2.8)
            self.assertIn(b"\x1bP9;1q", buf[mark:])
            # --- right-click -> hover -> click "Hide": hidden.json updated
            send("\x1b[<2;%d;3M\x1b[<2;%d;3m" % (x0 + 4, x0 + 4), 0.6)
            send("\x1b[<35;%d;6M" % (x0 + 6), 0.3)                       # hover the third row (Hide)
            send("\x1b[<0;%d;6M\x1b[<0;%d;6m" % (x0 + 6, x0 + 6), 0.8)
            d = json.load(open(hidden))
            self.assertEqual(d["items"], ["menu0001:img:1"])
            os.write(m, b"q"); pump(0.8)
            self.assertEqual(p.wait(timeout=5), 0)
            self.assertNotIn(b"Traceback", buf)
        finally:
            shutil.rmtree(tmp)

    def test_menu_review_fixes(self):
        import unittest.mock as um, tempfile, shutil
        wh = lambda *have: (lambda n: "/bin/" + n if n in have else None)
        ev = lambda **k: dict(dict(shift=False, alt=False, ctrl=False), **k)
        with um.patch.object(ccpaste, "winsize", return_value=(40, 120, 0, 0)), um.patch.object(ccpaste, "out_write") as ow, \
             um.patch.object(ccpaste, "copy_text", return_value="xclip") as cp:
            def sent():
                return b"".join(c[0][0] for c in ow.call_args_list if isinstance(c[0][0], bytes))
            # D1: the menu holds the item KEY; a refresh that rebuilds / shrinks the list neither crashes nor misfires
            app = self._app_with_items(3)
            app.focus = "main"
            for i, it in enumerate(app.items):
                it.path = "/p/%d" % i
            app.open_menu(("item", 2), 60, 20)
            self.assertEqual(app.menu["target"], ("item", app.items[2].key))
            app.items.insert(0, ccpaste.Item("txt", 99, text="new", sid="s1", ts=2.0))        # list rebuilt: index shifts
            app.menu["sel"] = 0
            app.menu_run()
            self.assertEqual(cp.call_args[0][0], "/p/2")                                       # still the original item
            app.open_menu(("item", 2), 60, 20)
            gone = app.items[2].key
            app.items = [it for it in app.items if it.key != gone]                              # the item disappeared
            app.menu["sel"] = 1
            app.menu_run()                                                                      # must not raise
            self.assertIsNone(app.menu)
            self.assertEqual(app.toast["text"], "item no longer exists")
            app.open_menu(("session", "s1"), 20, 5)
            app.rows = app.rows[:1]
            app.menu_run()
            self.assertEqual(app.toast["text"], "session no longer exists")
            # D3: window shrinks under an open menu -> it is dropped instead of being drawn over the footer
            app = self._app_with_items(2)
            app.open_menu(("item", 0), 100, 30)
            self.assertIsNotNone(app.menu)
            with um.patch.object(ccpaste, "winsize", return_value=(20, 80, 0, 0)):
                app.flush(force=True)
                self.assertIsNone(app.menu)
            self.assertIn(b"\x1b[?1003l\x1b[?1002h", sent())
            app.open_menu(("item", 0), 60, 20)
            app.draw_all()
            self.assertIsNone(app.menu)                                                         # layout change closes it
            # D4: a menu that covered the separator row repaints it on close
            app = self._app_with_items(2)
            app._drawn = True
            L = app.L()
            ow.reset_mock()
            app.restore_rect(L["sep"], app.x0 + 2, 10, 3)
            self.assertIn(("\u2500" * L["cols"]).encode(), sent())
            # drag-select + right-click cancels the drag before the menu opens
            app = self._app_with_items(2)
            app.focus = "main"
            L = app.L()
            x, y = app.x0 + 2, L["body_top"]
            app.on_down(x, y)
            app.on_drag(x + 6, y + 1, force=True)
            self.assertEqual(app.sel_state["state"], "extending")
            app.on_rclick(app.x0 + 4, L["strip_top"] + 2)
            self.assertIsNone(app.sel_state)
            self.assertIsNotNone(app.menu)
            app.on_mouse(ev(kind="up", btn=0, x=x + 6, y=y + 1))                               # the release does not copy
        # D6: menus never wrap onto the footer / the last column
        with um.patch.object(ccpaste, "winsize", return_value=(4, 12, 0, 0)), um.patch.object(ccpaste, "out_write"):
            app = self._app_with_items(2)
            app.open_menu(("item", 0), 5, 1)
            self.assertIsNone(app.menu)
            self.assertIn("too small", app.toast["text"])
        with um.patch.object(ccpaste, "winsize", return_value=(30, 12, 0, 0)), um.patch.object(ccpaste, "out_write"):
            app = self._app_with_items(2)
            app.open_menu(("item", 0), 11, 28)
            m = app.menu
            self.assertTrue(m["col"] + m["w"] <= 11 and m["row"] + m["h"] <= 29 and m["col"] >= 0)
            self.assertTrue(app.menu_fits())
        # D5: PowerShell treats U+2018..U+201B as single quotes
        self.assertEqual(ccpaste.ps_quote("a\u2019b\u2018c\u201ad\u201be'f"),
                         "'a\u2019\u2019b\u2018\u2018c\u201a\u201ad\u201b\u201be''f'")
        # D2: the macOS temporary PNG is removed once the backend finished
        tmp = tempfile.mkdtemp()
        try:
            from PIL import Image
            img = os.path.join(tmp, "a.jpg")
            Image.new("RGB", (3, 3), (9, 9, 9)).save(img)
            seen = []
            with um.patch.object(ccpaste, "run_tool", side_effect=lambda argv, data, timeout: seen.append(argv) or True):
                self.assertTrue(ccpaste.copy_image_blocking(img, "darwin", {}, False, wh("osascript")))
            tmp_png = re.search(r'POSIX file "([^"]+)"', seen[0][2]).group(1)
            self.assertTrue(tmp_png.endswith(".png") and tmp_png != img)
            self.assertFalse(os.path.exists(tmp_png))
            self.assertTrue(os.path.exists(img))
        finally:
            shutil.rmtree(tmp)
        # timeout: tool killed -> "timeout" (no path fallback), clear failure -> False (path fallback)
        self.assertEqual(ccpaste.run_tool(["sleep", "5"], b"", 0.2), "timeout")
        self.assertIs(ccpaste.run_tool(["definitely-not-a-tool-xyz"], b"", 1), False)
        with um.patch.object(ccpaste, "winsize", return_value=(40, 120, 0, 0)), um.patch.object(ccpaste, "out_write"):
            app = self._app_with_items(1)
            app.items = [ccpaste.Item("img", 1, path="/x.png", sig=(1, 1), sid="s", ts=1.0)]
            class T:
                q = queue.Queue()
            app.term = T
            with um.patch.object(ccpaste, "copy_image_blocking", return_value="timeout"):
                app.copy_object(0)
                self.assertEqual(T.q.get(timeout=3), ("toast", ("image copy timed out", "yellow")))
            with um.patch.object(ccpaste, "copy_image_blocking", return_value=False):
                app.copy_object(0)
                self.assertEqual(T.q.get(timeout=3), ("img_fail", "/x.png"))

    def test_short_footer_and_help_overlay(self):
        import unittest.mock as um
        with um.patch.object(ccpaste, "winsize", return_value=(40, 120, 0, 0)), um.patch.object(ccpaste, "out_write") as ow:
            def last_footer(app):
                app.out = []
                app.footer(app.L())
                return b"".join(app.out).decode()
            app = self._app_with_items(3)
            app.focus = "main"
            app.sel = 1
            f = last_footer(app)
            self.assertIn("T#2 \u00b7 2/3", f)                       # position; no session prefix outside the All view
            self.assertIn("\u2190\u2192 item  m menu  ? help", f)
            for gone in ("cell ", "Tab/Esc", "arrows/hjkl", "quit"):
                self.assertNotIn(gone, f)
            app.sid = "*"
            self.assertIn("s1 T#2 \u00b7 2/3", last_footer(app))      # All view keeps the 4-char prefix
            app.focus = "side"
            app.sel_sid = "s2"
            f = last_footer(app)
            self.assertIn("session 2/2", f)
            self.assertIn("\u2191\u2193 session  Enter open  ? help", f)
            with um.patch.object(ccpaste, "winsize", return_value=(40, 30, 0, 0)):      # narrow: still one exact-width line
                app.out = []
                app.footer(app.L())
                txt = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", b"".join(app.out).decode())
                self.assertEqual(sum(ccpaste.cwidth(ch) for ch in txt), 30)
            # help overlay: ? opens, drawn over everything (cell size shown there), any key closes + restores the area
            app = self._app_with_items(2)
            app.cellpx, app.cellsrc = (10, 20), "CCPASTE_CELL"
            app._drawn = True
            app.handle_key("?")
            self.assertTrue(app.help)
            drawn = b"".join(c[0][0] for c in ow.call_args_list if isinstance(c[0][0], bytes)).decode()
            for piece in ("Keys", "Mouse", "cell 10x20 (CCPASTE_CELL)", "adjust image scale", "any key or click closes", "\u250c"):
                self.assertIn(piece, drawn)
            row, col, w, h, wl, wr = app.help_geom()
            self.assertEqual((col, row), ((120 - w) // 2, (39 - h) // 2))              # centred, clear of the footer row
            self.assertLessEqual(row + h, 39)
            with um.patch.object(ccpaste.App, "restore_rect") as rr:
                app.handle_key("x")
                self.assertFalse(app.help)
                rr.assert_called_once_with(row, col, w, h)
            app.handle_key("?")
            app.on_mouse(dict(kind="down", btn=0, x=5, y=5, shift=False, alt=False, ctrl=False))
            self.assertFalse(app.help)
            with um.patch.object(ccpaste, "winsize", return_value=(10, 20, 0, 0)):    # tiny window: clamped, no crash
                app.open_help()
                row, col, w, h, wl, wr = app.help_geom()
                self.assertTrue(w <= 20 and row + h <= 10 and col >= 0)
                app.help_bytes()
                app.help = False

    def test_pty_help_overlay_restores_image(self):
        import tempfile, shutil
        from PIL import Image
        tmp = tempfile.mkdtemp()
        try:
            d = os.path.join(tmp, "root", "-x-hp", "help0001", "images"); os.makedirs(d)
            os.makedirs(os.path.join(tmp, "cfg"))
            Image.new("RGB", (800, 800), (30, 120, 200)).save(os.path.join(d, "1.png"))
            m, p = self._pty_session(tmp, args=("-s", "help"))
            buf = b""
            def pump(t):
                nonlocal buf
                end = time.time() + t
                while time.time() < end:
                    r, _, _ = select.select([m], [], [], 0.05)
                    if r:
                        try:
                            buf += os.read(m, 65536)
                        except OSError:
                            return
            pump(2.5)
            self.assertIn(b"? help", buf)                                   # short footer
            self.assertNotIn(b"Tab/Esc", buf)
            mark = len(buf)
            os.write(m, b"?"); pump(0.6)
            self.assertIn(b"Mouse", buf[mark:])
            self.assertIn(b"cell 10x20", buf[mark:])
            mark = len(buf)
            os.write(m, b"x"); pump(0.8)                                    # any key closes; the image under it is re-placed
            self.assertIn(b"\x1bP9;1q", buf[mark:])
            os.write(m, b"q"); pump(0.8)
            self.assertEqual(p.wait(timeout=5), 0)
            self.assertNotIn(b"Traceback", buf)
        finally:
            shutil.rmtree(tmp)

    def test_rel_time(self):
        n = 1_000_000
        f = lambda d: ccpaste.rel_time(n - d, n)
        self.assertEqual([f(5), f(180), f(7300), f(100000), f(3 * 86400 + 5)],
                         ["just now", "3m ago", "2h ago", "yesterday", "3d ago"])

    def test_ambiguous_prefix(self):
        sids = ["abcd1111-0000-4000-8000-000000000001", "abcd2222-0000-4000-8000-000000000002",
                "f0f0f0f0-0000-4000-8000-000000000003"]
        with fake_tree(sids, nimg=1) as (fenv, tmp, cfg, root):
            old_root = ccpaste.TMP_ROOT
            ccpaste.TMP_ROOT = root
            try:
                with cfgdirs(cfg):
                    sess = [e["sid"] for e in ccpaste.build_sessions(True)]
            finally:
                ccpaste.TMP_ROOT = old_root
            for n in range(1, 4):
                groups = {}
                for k in sess:
                    groups.setdefault(k[:n], []).append(k)
                amb = [p for p, v in groups.items() if len(v) > 1]
                if amb:
                    break
            self.assertTrue(amb, "need >=2 sessions sharing a prefix")
            r = subprocess.run([PATH, "-s", amb[0], "--list"], capture_output=True, text=True,
                               env=dict(os.environ, **fenv))
            self.assertEqual(r.returncode, 1)
            self.assertIn("ambiguous", r.stderr)
            self.assertIn("last activity", r.stderr)
            self.assertEqual(r.stdout, "")

    def test_selftest_flag(self):
        r = subprocess.run([PATH, "--selftest"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "selftest ok")

    def test_pty_sidebar(self):
        with fake_tree(("e6b6aaaa-0000-4000-8000-000000000001", "c3c3c3c3-0000-4000-8000-000000000002")) as (fenv, *_):
            self._pty_sidebar(fenv)

    def _pty_sidebar(self, fenv):
        m, sl = pty.openpty()
        fcntl.ioctl(sl, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
        env = dict(os.environ, CCPASTE_CELL="10x20", TERM="xterm", **fenv)
        p = subprocess.Popen([PATH], stdin=sl, stdout=sl, stderr=sl, env=env, close_fds=True, start_new_session=True,
                             preexec_fn=lambda: fcntl.ioctl(0, termios.TIOCSCTTY, 0))
        os.close(sl)
        buf = b""

        def pump(t):
            nonlocal buf
            end = time.time() + t
            while time.time() < end:
                r, _, _ = select.select([m], [], [], 0.1)
                if r:
                    try:
                        buf += os.read(m, 65536)
                    except OSError:
                        return
        pump(3.0)
        self.assertIn(b"sessions", buf)
        self.assertIn(b"\x1bP9;1q", buf)               # newest session's thumbnails render on start
        xs = [int(x) for x in re.findall(rb"\x1b7\x1b\[\d+;(\d+)H\x1bP9;1q", buf)]
        sbw, x0 = ccpaste.sidebar_geom(120)
        self.assertTrue(xs and min(xs) >= x0 + 1, (min(xs), x0))   # never drawn over sidebar/divider
        for k in (b"j", b"k", b"j", b"G", b"g"):
            os.write(m, k); pump(0.5)                    # sidebar focus: session changes live
        os.write(m, b"\t"); pump(0.5)                    # focus right pane
        for k in (b"\x1b[D", b"\x1b[C", b"\x1b[B", b"\x1b[A"):
            os.write(m, k); pump(0.3)
        os.write(m, b"\x1b"); pump(0.8)                  # lone Esc back to the sidebar (must not hang)
        mark = len(buf)
        os.write(m, b"b"); pump(1.5)                     # hide sidebar
        os.write(m, b"\t"); pump(0.3)
        xs2 = [int(x) for x in re.findall(rb"\x1b7\x1b\[\d+;(\d+)H\x1bP9;1q", buf[mark:])]
        self.assertTrue(xs2 and min(xs2) < x0, xs2)      # right pane now starts at column 0
        os.write(m, b"q"); pump(0.5)
        self.assertEqual(p.wait(timeout=5), 0)
        self.assertNotIn(b"Traceback", buf)

    def test_key_latency_noise_png(self):
        import tempfile, shutil
        from PIL import Image
        base = __import__("tempfile").gettempdir()
        tmp = tempfile.mkdtemp(dir=base)
        try:
            d = os.path.join(tmp, "root", "-x-lat", "lat00001", "images")
            os.makedirs(d); os.makedirs(os.path.join(tmp, "cfg"))
            Image.frombytes("RGB", (2000, 1500), os.urandom(2000 * 1500 * 3)).save(os.path.join(d, "1.png"))
            Image.new("RGB", (300, 200), (10, 80, 160)).save(os.path.join(d, "2.png"))
            now = time.time()
            os.utime(os.path.join(d, "1.png"), (now - 100, now - 100))
            with open(os.path.join(tmp, "cfg", "history.jsonl"), "w") as f:
                f.write(json.dumps(dict(display="x", pastedContents={}, timestamp=int(now * 1000), project="/x/lat", sessionId="lat00001")) + "\n")
            m, sl = pty.openpty()
            fcntl.ioctl(sl, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
            env = dict(os.environ, CCPASTE_CELL="10x20", TERM="xterm", CCPASTE_TMP_ROOT=os.path.join(tmp, "root"),
                       CCPASTE_CONFIG_DIRS=os.path.join(tmp, "cfg"))
            p = subprocess.Popen([PATH, "-s", "lat0"], stdin=sl, stdout=sl, stderr=sl, env=env, close_fds=True,
                                 start_new_session=True, preexec_fn=lambda: fcntl.ioctl(0, termios.TIOCSCTTY, 0))
            os.close(sl)
            total = []
            def drain(t):
                end, b = time.time() + t, b""
                while time.time() < end:
                    r, _, _ = select.select([m], [], [], 0.05)
                    if r:
                        try:
                            b += os.read(m, 65536)
                        except OSError:
                            break
                total.append(b)
                return b
            def timed(key):
                t0 = time.time()
                os.write(m, key)
                while True:
                    r, _, _ = select.select([m], [], [], 2)
                    self.assertTrue(r, "no output after key")
                    total.append(os.read(m, 65536))
                    dt = time.time() - t0
                    return dt
            drain(0.4)                                   # worker is now busy on the 2000x1500 noise
            lat = [timed(b"h")]                          # select noise image (preview job starts)
            drain(0.15)
            lat.append(timed(b"l"))                      # move while the encode is still running
            drain(0.1)
            lat.append(timed(b"h"))
            drain(0.1)
            lat.append(timed(b"l"))
            lat.append(timed(b"h"))                      # finally stay on the noise image until it is delivered
            out = drain(4.0)
            os.write(m, b"q"); drain(0.5)
            self.assertEqual(p.wait(timeout=5), 0)
            print("\nKEY->FRAME latency ms:", [round(x * 1000, 1) for x in lat], file=sys.stderr)
            self.assertLess(max(lat), 0.1)
            self.assertGreater(len(b''.join(total)), 200000)        # the noise preview really was encoded and delivered
        finally:
            shutil.rmtree(tmp)

    def test_pty(self):
        with fake_tree(("e6b6aaaa-0000-4000-8000-000000000001", "c3c3c3c3-0000-4000-8000-000000000002")) as (fenv, *_):
            self._pty(fenv)

    def _pty(self, fenv):
        m, s = pty.openpty()
        fcntl.ioctl(s, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
        env = dict(os.environ, CCPASTE_CELL="10x20", TERM="xterm", **fenv)
        p = subprocess.Popen([PATH, "-s", "e6b6"], stdin=s, stdout=s, stderr=s, env=env, close_fds=True, start_new_session=True,
                             preexec_fn=lambda: fcntl.ioctl(0, termios.TIOCSCTTY, 0))
        os.close(s)
        buf = b""

        def pump(t):
            nonlocal buf
            end = time.time() + t
            while time.time() < end:
                r, _, _ = select.select([m], [], [], 0.1)
                if r:
                    try:
                        buf += os.read(m, 65536)
                    except OSError:
                        return
        pump(2.5)
        for k in (b"\x1b[D", b"\x1b[D", b"\x1b[C", b"j", b"G", b"g", b"\x1b[6~", b"r"):
            os.write(m, k)
            pump(0.4)
        os.write(m, b"q")
        pump(1.0)
        rc = p.wait(timeout=5)
        self.assertEqual(rc, 0)
        self.assertNotIn(b"Traceback", buf)
        self.assertIn(b"\x1bP9;1q", buf)
        self.assertIn(b"\x1b[?1049l", buf)
        self.assertTrue(buf.rstrip().endswith(b"\x1b[?25h\x1b[?1049l") or b"\x1b[?25h" in buf[-40:])


if __name__ == "__main__":
    unittest.main(verbosity=2)
