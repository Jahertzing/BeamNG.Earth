"""gcpatchwin.py: the grass patch's handler found in a Windows executable, and the byte toggled by the level flag."""
import os
import struct
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import gcpatchwin  # noqa: E402

# BeamNG.drive 0.40.3's handler (BeamNG.drive.x64.exe, file offset 0x9d6d80): prologue, the lightmap test and its tail
# jump, the height / layer test
HANDLER_0403 = bytes.fromhex("40534156" "4881ec98000000" "498bd9" "4c8bf1" "f6c201" "740f" "4881c498000000" "415e" "5b"
                             "e96bb2ffff" "f6c20e" "0f8497010000" "498b00")
TEXT_VA, TEXT_RAW = 0x1000, 0x400


def pe_image(code, execute=True):
    """A minimal PE32+ image: headers and one section holding code."""
    pe = 0x80
    opt = 0xF0
    img = bytearray(TEXT_RAW + len(code))
    img[:2] = b"MZ"
    struct.pack_into("<I", img, 0x3C, pe)
    img[pe:pe + 4] = b"PE\0\0"
    struct.pack_into("<HH", img, pe + 4, 0x8664, 1)
    struct.pack_into("<H", img, pe + 20, opt)
    chars = 0x60000020 if execute else 0x40000040
    struct.pack_into("<8sIIIIIIHHI", img, pe + 24 + opt, b".text", len(code), TEXT_VA, len(code), TEXT_RAW,
                     0, 0, 0, 0, chars)
    img[TEXT_RAW:] = code
    return bytes(img)


class FindTest(unittest.TestCase):
    def test_the_0403_handler(self):
        code = b"\xcc" * 0x200 + HANDLER_0403 + b"\xcc" * 0x40
        found, why = gcpatchwin.find_imm(pe_image(code))
        self.assertIsNone(why)
        rva, ctx, ctx_rva = found
        self.assertEqual(ctx_rva, TEXT_VA + 0x200 + HANDLER_0403.index(b"\xf6\xc2\x01"))
        self.assertEqual(rva, TEXT_VA + 0x200 + HANDLER_0403.index(b"\xf6\xc2\x0e") + 2)
        self.assertEqual(code[rva - TEXT_VA], 0x0E)

    def test_an_already_patched_game(self):
        # the executable on disk is never patched (only the running game's memory), so the signature needs the 0Eh
        code = HANDLER_0403.replace(b"\xf6\xc2\x0e", b"\xf6\xc2\x00")
        self.assertIsNone(gcpatchwin.find_imm(pe_image(code))[0])

    def test_two_matches_or_none_leave_the_game_stock(self):
        found, why = gcpatchwin.find_imm(pe_image(HANDLER_0403 * 2))
        self.assertIsNone(found)
        self.assertIn("2 matches", why)
        found, why = gcpatchwin.find_imm(pe_image(b"\x90" * 64))
        self.assertIsNone(found)
        self.assertIn("0 matches", why)

    def test_only_code_sections(self):
        found, why = gcpatchwin.find_imm(pe_image(HANDLER_0403, execute=False))
        self.assertIsNone(found)

    def test_not_an_executable(self):
        self.assertIsNone(gcpatchwin.find_imm(b"\x7fELF" + b"\0" * 100)[0])
        self.assertIsNone(gcpatchwin.find_imm(b"MZ" + b"\0" * 10)[0])

    def test_the_real_game_when_present(self):
        exe = os.environ.get("BNGE_GAME_EXE")
        if not exe or not os.path.isfile(exe):
            self.skipTest("BNGE_GAME_EXE: the game's BeamNG.drive.x64.exe")
        with open(exe, "rb") as f:
            found, why = gcpatchwin.find_imm(f.read())
        self.assertIsNone(why)


class FakeWin:
    """The game's process: its memory a bytearray at BASE, alive until `exits` is set."""
    BASE = 0x140000000

    def __init__(self, image, exe):
        self.mem = bytearray(image)     # indexed by RVA
        self.exe = exe
        self.exits = threading.Event()
        self.writes = []

    def pids(self, name):
        return [] if self.exits.is_set() else [42]

    def main_module(self, pid):
        return self.BASE, self.exe

    def open(self, pid):
        return "h"

    def alive(self, h):
        return not self.exits.is_set()

    def read(self, h, addr, n):
        o = addr - self.BASE
        return bytes(self.mem[o:o + n])

    def write_byte(self, h, addr, value):
        self.mem[addr - self.BASE] = value
        self.writes.append(value)
        return True

    def close(self, h):
        pass


class RunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.user = os.path.join(self.tmp.name, "current")
        os.makedirs(os.path.join(self.user, "temp"))
        code = b"\xcc" * 0x100 + HANDLER_0403
        self.image = pe_image(code)
        self.exe = os.path.join(self.tmp.name, "BeamNG.drive.x64.exe")
        with open(self.exe, "wb") as f:
            f.write(self.image)
        found, _ = gcpatchwin.find_imm(self.image)
        self.rva = found[0]
        mem = bytearray(TEXT_VA + len(code))
        mem[TEXT_VA:] = code
        self.win = FakeWin(mem, self.exe)       # the loaded image: the section at its RVA

    def tearDown(self):
        self.tmp.cleanup()

    def status(self):
        try:
            with open(os.path.join(self.user, "temp", gcpatchwin.STATUS), encoding="utf-8") as f:
                return f.read()
        except OSError:
            return ""

    def wait_for(self, cond, s=3.0):
        import time
        t0 = time.time()
        while time.time() - t0 < s:
            if cond():
                return True
            time.sleep(0.02)
        return False

    def test_toggled_by_the_level_flag(self):
        flag = os.path.join(self.user, "temp", gcpatchwin.FLAG)
        old = gcpatchwin.POLL_S, gcpatchwin.ATTACH_S
        gcpatchwin.POLL_S = gcpatchwin.ATTACH_S = 0.01
        stop = threading.Event()
        t = threading.Thread(target=gcpatchwin.run, args=(self.user, stop, self.win), daemon=True)
        try:
            t.start()
            self.assertTrue(self.wait_for(lambda: "ready" in self.status()))
            self.assertEqual(self.win.mem[self.rva], gcpatchwin.STOCK)     # another level: stock
            open(flag, "w").close()
            self.assertTrue(self.wait_for(lambda: self.win.mem[self.rva] == gcpatchwin.PATCHED))
            self.assertIn("applied", self.status())
            os.remove(flag)
            self.assertTrue(self.wait_for(lambda: self.win.mem[self.rva] == gcpatchwin.STOCK))
            self.assertIn("stock", self.status())
            open(flag, "w").close()
            self.assertTrue(self.wait_for(lambda: self.win.mem[self.rva] == gcpatchwin.PATCHED))
        finally:
            stop.set()
            t.join(3)
            gcpatchwin.POLL_S, gcpatchwin.ATTACH_S = old
        # the launcher stopping while the game runs: the game is left stock
        self.assertEqual(self.win.mem[self.rva], gcpatchwin.STOCK)
        self.assertEqual(self.win.writes, [0, 0x0E, 0, 0x0E])

    def test_code_that_differs_in_memory_is_left_alone(self):
        self.win.mem[self.rva - 4] ^= 0xFF          # (the je's opcode)
        old = gcpatchwin.ATTACH_S
        gcpatchwin.ATTACH_S = 0.01
        stop = threading.Event()
        open(os.path.join(self.user, "temp", gcpatchwin.FLAG), "w").close()
        t = threading.Thread(target=gcpatchwin.run, args=(self.user, stop, self.win), daemon=True)
        try:
            t.start()
            self.assertTrue(self.wait_for(lambda: os.path.exists(os.path.join(self.user, "temp", gcpatchwin.STATUS))))
            self.assertIn("differs", self.status())
        finally:
            stop.set()
            t.join(3)
            gcpatchwin.ATTACH_S = old
        self.assertEqual(self.win.writes, [])

    def test_off_outside_windows_or_when_asked(self):
        if os.name != "nt":
            self.assertIsNone(gcpatchwin.start(self.user))


if __name__ == "__main__":
    unittest.main()
