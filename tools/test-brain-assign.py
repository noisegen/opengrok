#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ASSIGN = os.path.join(HERE, "brain-assign.py")


class AssignTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.bindings = os.path.join(self.td.name, "brain-bindings.json")
        self.env = os.environ.copy()
        self.env["BRAIN_BINDINGS"] = self.bindings
        self.env["PYTHONUTF8"] = "1"

    def tearDown(self):
        self.td.cleanup()

    def run_assign(self, *args):
        r = subprocess.run(
            [sys.executable, ASSIGN, *args],
            env=self.env,
            capture_output=True,
            text=True,
        )
        return r

    def test_on_off(self):
        uid = "71b408bd-0c94-494b-8a45-754bc0ef2d73"
        r = self.run_assign("on", uid, "--name", "Long Run")
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(self.bindings, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["agents"][uid]["brain"], "deepseek")
        r = self.run_assign("off", uid)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(self.bindings, encoding="utf-8") as f:
            data = json.load(f)
        self.assertNotIn(uid, data["agents"])

    def test_rejects_kimi(self):
        r = self.run_assign("on", "aaaaaaaa-0000-4000-8000-000000000001")
        # on always deepseek; no kimi path
        self.assertEqual(r.returncode, 0)
        # unknown command
        r = subprocess.run(
            [sys.executable, ASSIGN, "set", "x", "kimi"],
            env=self.env,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
