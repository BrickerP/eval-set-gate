import json, os, subprocess, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import eval_set_gate as g

W, E, L = g.split_globs(g.DEFAULT_WATCH), g.split_globs(g.DEFAULT_EVALS), g.DEFAULT_LOCK

class Pure(unittest.TestCase):
    def test_match(self):
        self.assertTrue(g.match("prompts/sys.txt", W))
        self.assertTrue(g.match("app/agent.prompt.md", W))
        self.assertTrue(g.match("config/model.yaml", W))
        self.assertFalse(g.match("src/main.py", W))
        self.assertTrue(g.match("evals/cases.jsonl", E))
    def test_prompt_change_without_bump_fails(self):
        ok, r, _ = g.gate(["prompts/a.txt"], W, E, L, {"sha256": "x"}, "x")
        self.assertFalse(ok); self.assertIn("not bumped", r[0])
    def test_prompt_change_with_bump_passes(self):
        ok, _, _ = g.gate(["prompts/a.txt", L], W, E, L, {"sha256": "x"}, "x")
        self.assertTrue(ok)
    def test_stale_lock_fails(self):
        ok, r, _ = g.gate(["evals/a.jsonl"], W, E, L, {"sha256": "old"}, "new")
        self.assertFalse(ok); self.assertIn("stale", r[0])
    def test_unrelated_passes(self):
        self.assertTrue(g.gate(["README.md"], W, E, L, None, "x")[0])

class EndToEnd(unittest.TestCase):
    def sh(self, *a): subprocess.run(a, cwd=self.d, check=True, capture_output=True)
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.sh("git", "init", "-q", "-b", "main")
        self.sh("git", "config", "user.email", "t@t"); self.sh("git", "config", "user.name", "t")
        os.makedirs(f"{self.d}/prompts"); os.makedirs(f"{self.d}/evals")
        open(f"{self.d}/prompts/sys.txt", "w").write("be helpful")
        open(f"{self.d}/evals/cases.jsonl", "w").write('{"q":1}\n')
        self.sh("git", "add", "."); g.write_lock(E, L, self.d)
        self.sh("git", "add", "."); self.sh("git", "commit", "-qm", "init")
    def run_check(self):
        return g.main(["check", "--base", "main", "--root", self.d])
    def test_flow(self):
        self.sh("git", "checkout", "-qb", "pr")
        open(f"{self.d}/prompts/sys.txt", "w").write("be terse")
        self.sh("git", "commit", "-qam", "prompt")
        self.assertEqual(self.run_check(), 1)
        open(f"{self.d}/evals/cases.jsonl", "a").write('{"q":2}\n')
        g.write_lock(E, L, self.d)
        self.assertEqual(json.load(open(f"{self.d}/{L}"))["version"], 2)
        self.sh("git", "commit", "-qam", "bump evals")
        self.assertEqual(self.run_check(), 0)

if __name__ == "__main__":
    unittest.main()
