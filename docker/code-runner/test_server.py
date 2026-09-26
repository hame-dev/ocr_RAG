"""Tests for the sandbox runner. Stdlib unittest, so they run in the runner image:

    make test-runner
"""
from __future__ import annotations

import base64
import http.client
import os
import sys
import threading
import time
import unittest
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import server  # noqa: E402


class RunJobTests(unittest.TestCase):
    def test_prints_come_back_as_stdout(self):
        result = server.run_job("print(12 + 5)\nprint(2 ** 10)")
        self.assertTrue(result["ok"])
        self.assertEqual(result["stdout"], "17\n1024\n")
        self.assertEqual(result["files"], [])

    def test_errors_are_reported_not_raised(self):
        result = server.run_job("1 / 0")
        self.assertFalse(result["ok"])
        self.assertIn("ZeroDivisionError", result["stderr"])

    def test_saved_files_are_collected_and_disallowed_types_skipped(self):
        code = (
            "open('notes.txt', 'w').write('hi')\n"
            "open('run.sh', 'w').write('echo no')\n"
            "import os; os.makedirs('sub', exist_ok=True)\n"
            "open('sub/data.csv', 'w').write('a,b')\n"
        )
        result = server.run_job(code)
        names = sorted(f["name"] for f in result["files"])
        self.assertEqual(names, ["data.csv", "notes.txt"])
        notes = next(f for f in result["files"] if f["name"] == "notes.txt")
        self.assertEqual(base64.b64decode(notes["b64"]), b"hi")
        self.assertIn("run.sh (file type not allowed)", result["stderr"])

    def test_runaway_code_is_killed_at_the_timeout(self):
        result = server.run_job("while True:\n    pass", timeout_s=2)
        self.assertFalse(result["ok"])
        self.assertIn("TimeoutError", result["stderr"])
        self.assertLess(result["duration_ms"], 10_000)

    def test_memory_is_capped(self):
        result = server.run_job("x = bytearray(4 * 1024 ** 3)")
        self.assertFalse(result["ok"])
        self.assertIn("MemoryError", result["stderr"])

    def test_long_output_is_truncated(self):
        result = server.run_job("print('x' * 50000)")
        self.assertLess(len(result["stdout"]), server.MAX_OUTPUT_CHARS + 200)
        self.assertIn("output truncated", result["stdout"])

    def test_the_example_libraries_are_importable(self):
        code = (
            "import numpy, sympy, scipy, pandas, openpyxl, docx, pptx, arabic_reshaper, bidi\n"
            "import matplotlib.pyplot as plt\n"
            "plt.bar(['A', 'B'], [1, 2]); plt.savefig('chart.png')\n"
            "wb = openpyxl.Workbook(); wb.save('book.xlsx')\n"
            "docx.Document().save('doc.docx')\n"
            "pptx.Presentation().save('deck.pptx')\n"
            "print(sympy.solve(sympy.Symbol('x') ** 2 - 4))\n"
        )
        result = server.run_job(code)
        self.assertTrue(result["ok"], result["stderr"])
        self.assertEqual(result["stdout"].strip(), "[-2, 2]")
        # The prebuilt font cache is used: no "MPLCONFIGDIR not writable" warning.
        self.assertNotIn("MPLCONFIGDIR", result["stderr"])
        self.assertEqual(
            sorted(f["name"] for f in result["files"]),
            ["book.xlsx", "chart.png", "deck.pptx", "doc.docx"],
        )

    def _alive(self, pid: int) -> bool:
        try:
            with open(f"/proc/{pid}/stat") as fh:
                return fh.read().rsplit(")", 1)[1].split()[0] != "Z"  # zombie = dead
        except FileNotFoundError:
            return False

    def _spawn_and_exit(self, detach: bool) -> int:
        code = (
            "import subprocess\n"
            f"p = subprocess.Popen(['sleep', '300'], start_new_session={detach},\n"
            "    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
            "print(p.pid)\n"
        )
        result = server.run_job(code)
        self.assertTrue(result["ok"], result["stderr"])
        return int(result["stdout"])

    @unittest.skipUnless(os.path.isdir("/proc"), "needs Linux /proc")
    def test_background_children_die_with_the_job(self):
        pid = self._spawn_and_exit(detach=False)
        time.sleep(0.2)
        self.assertFalse(self._alive(pid))

    @unittest.skipUnless(os.path.isdir("/proc"), "needs Linux /proc")
    def test_detached_processes_are_swept_after_the_job(self):
        pid = self._spawn_and_exit(detach=True)
        time.sleep(0.2)
        self.assertFalse(self._alive(pid))


class HandlerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def _post(self, body: bytes, length: str) -> int:
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=5)
        conn.putrequest("POST", "/run")
        conn.putheader("Content-Length", length)
        conn.endheaders()
        conn.send(body)
        status = conn.getresponse().status
        conn.close()
        return status

    def test_negative_content_length_is_rejected_not_waited_on(self):
        self.assertEqual(self._post(b"{}", "-1"), 400)

    def test_run_endpoint(self):
        self.assertEqual(self._post(b'{"code": "print(1)"}', "20"), 200)


if __name__ == "__main__":
    unittest.main()
