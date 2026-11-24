"""Needle's embedded NVN shader records, without game data."""
from pathlib import Path
from contextlib import redirect_stderr
import io
import struct
import sys
import unittest

import importlib.util
import os
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1] / 'nxnative/dm/gfx/tools'
sys.path.insert(0, str(TOOLS))
spec = importlib.util.spec_from_file_location('shader_split_needle', TOOLS / 'shader_split.py')
split = importlib.util.module_from_spec(spec)
with patch.dict(os.environ, {'DM_GAME': '0100000000010000'}):
    spec.loader.exec_module(split)


def payload(stage, desc=0x90):
    """A control section and its code area, optionally behind a descriptor of desc bytes (its +0x30 words: control offset, code offset, code length, control length)"""
    ctl, code = bytearray(0x850), bytearray(0x100)
    ctl[:4] = split.CTL_MAGIC
    struct.pack_into('<I', ctl, 0x20, len(ctl))
    struct.pack_into('<I', ctl, 0x704, len(code))
    if stage == 'cs':
        struct.pack_into('<3I', ctl, 0x748, 8, 4, 1)
    else:
        code[:4] = split.GFX_MAGIC
        struct.pack_into('<I', code, 0x30, (1 if stage == 'vs' else 5) << 10 | 1)
        if stage == 'ps': struct.pack_into('<I', code, 0x48, 1)   # pixel shader reads generic location 0
    if not desc: return bytes(ctl + code), bytes(ctl), bytes(code)
    header = bytearray(desc)
    struct.pack_into('<4I', header, 0x30, desc, desc + len(ctl), len(code), len(ctl))
    return bytes(header + ctl + code), bytes(ctl), bytes(code)


def shader(stage, desc=0x90):
    return payload(stage, desc)


def container(*stages, desc=0x90):
    # Reflection/variant metadata can precede and separate the NVN records.
    d = bytearray(b'HNSHV002' + bytes(24) + b'variant metadata')
    for stage in stages:
        d += shader(stage, desc)[0] + bytes(36)
    struct.pack_into('>I', d, 8, len(d))
    return bytes(d)


def shader_file(stage):
    """A Hedgehog Engine 2 shader file: HHNEEDLE, its length, the shader's path, then the HNSHV002 archive whose variants are size-prefixed payloads with no descriptor"""
    d = bytearray(b'HHNEEDLE' + struct.pack('>I', 0) + b'needle/ps_test\0')
    while len(d) % 4: d += b'\0'
    at = len(d)
    body, ctl, code = payload(stage, 0)
    d += b'HNSHV002' + struct.pack('>I', 0) + bytes(8)
    struct.pack_into('>I', d, at + 8, 0)   # the archive's length, below once it is whole
    d += struct.pack('<I', len(body) + 4) + body   # a variant's shader data (its size includes that field)
    struct.pack_into('>I', d, at + 8, len(d) - at)
    struct.pack_into('>I', d, 8, len(d))
    return bytes(d)


class NeedleTests(unittest.TestCase):
    def test_embedded_variants_and_headerless_compute(self):
        data = b'prefix\0' + container('vs', 'ps', 'cs') + b'suffix'
        found = []
        split.scan_blob(data, 'main', found)
        self.assertEqual([s for _, s, _, _ in found], ['vs', 'ps', 'cs'])
        for (_, stage, ctl, code) in found:
            self.assertEqual((ctl, code), shader(stage)[1:])
        keys = split.uniq_keys(found)
        # These stages are independent programs; the main/GSC wrapper is not a pair.
        self.assertEqual(split.var_pairs(found), set())
        self.assertEqual(len(split.separable_pairs(keys)[0]), 1)
        self.assertTrue(all('#NEEDLE@' in label for label, *_ in found))

    def test_container_bounds_reject_truncated_or_string_magic(self):
        self.assertEqual(split.needle_shaders(b'HNSHV002', 'short'), [])
        self.assertEqual(split.needle_shaders(container('vs')[:-1], 'truncated'), [])
        data = bytearray(container('vs'))
        struct.pack_into('>I', data, 8, 32)
        self.assertEqual(split.needle_shaders(data, 'short envelope'), [])

    def test_a_broken_descriptor_falls_back_to_the_sections_own_lengths(self):
        # A descriptor whose code area runs past the container is not used: the section's own lengths (+0x20, +0x704) and the code area after it are.
        data = bytearray(container('vs'))
        c = data.index(split.CTL_MAGIC)
        struct.pack_into('<I', data, c - 0x90 + 0x34, len(data))
        found = split.needle_shaders(bytes(data) + container('ps'), 'bad code offset')
        self.assertEqual([s for _, s, _, _ in found], ['vs', 'ps'])
        self.assertEqual(found[0][3], shader('vs')[2])   # the shader's own code area, never the next record's bytes

    def test_a_descriptor_that_disagrees_with_the_control_section_is_not_used(self):
        data = bytearray(container('vs'))
        c = data.index(split.CTL_MAGIC)
        struct.pack_into('<I', data, c - 0x90 + 0x3c, 0x880)   # a control length the control section's own +0x20 contradicts
        found = split.needle_shaders(bytes(data), 'bad control length')
        self.assertEqual([(s, len(ctl), len(code)) for _, s, ctl, code in found], [('vs', 0x850, 0x100)])

    def test_a_descriptor_of_another_size_is_found_by_its_own_offset(self):
        # Hedgehog Engine 2's shader payloads: the descriptor is not 0x90 bytes, and its +0x30 says how big it is
        for desc in (0xa0, 0x80, 0x100):
            with self.subTest(desc=desc):
                found = split.needle_shaders(container('vs', 'ps', desc=desc), f'descriptor {desc:#x}')
                self.assertEqual([(s, len(ctl)) for _, s, ctl, _ in found], [('vs', 0x850), ('ps', 0x850)])
                self.assertEqual(found[1][2:], shader('ps', desc)[1:])

    def test_a_payload_with_no_descriptor_is_a_control_section_and_its_code(self):
        d = bytearray(b'HNSHV002' + bytes(24))
        d += payload('ps', 0)[0]   # 0x850 bytes of control, then its code area
        struct.pack_into('>I', d, 8, len(d))
        found = split.needle_shaders(bytes(d), 'no descriptor')
        self.assertEqual([s for _, s, _, _ in found], ['ps'])
        self.assertEqual(found[0][2:], payload('ps', 0)[1:])

    def test_a_shader_file_of_hedgehog_engine_2(self):
        found = split.needle_shaders(shader_file('ps'), 'NeedleShader.pac')
        self.assertEqual([s for _, s, _, _ in found], ['ps'])
        self.assertEqual(found[0][2:], payload('ps', 0)[1:])

    def test_one_unreadable_section_leaves_the_files_others_in(self):
        good = payload('ps', 0)[0]
        d = bytearray(b'HNSHV002' + bytes(24))
        d += good + bytes(0x40) + split.CTL_MAGIC + bytes(0x758)   # a control section cut short: no code area in the container
        struct.pack_into('>I', d, 8, len(d))
        err = io.StringIO()
        with redirect_stderr(err): found = split.needle_shaders(bytes(d), 'half readable')
        self.assertEqual([s for _, s, _, _ in found], ['ps'])
        self.assertEqual(found[0][2:], payload('ps', 0)[1:])
        self.assertIn('could not be read, the rest are in', err.getvalue())   # the log says so; the file is not marked unreadable


if __name__ == '__main__':
    unittest.main()
