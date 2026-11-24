# Sonic Frontiers shader extraction

Title ID: `01004AD014BF0000`. Android bring-up is experimental; a successful
shader split does not establish SDK compatibility or runtime playability.

## Zero shaders at Stage 5

The reported base v1.0.0 build stopped with `shaders: 0` and many unmatched
`main control section` messages. The splitter previously had no reader for
Needle's `HNSHV002` containers. Its raw-main fallback requires pointer records
or address-building patterns that do not describe these containers.

The Needle reader is backported from the user-supplied branch:

- https://github.com/mymes1/droidplay-core/tree/arena%2F16159649-droidplay-core
- Relevant fix: https://github.com/mymes1/droidplay-core/commit/5b34f894d3be710be011b61d6fa7fe35a7c2f7df
- Source: `dm/gfx/tools/shader_split.py`, `dm/gfx/tests/test_needle.py`

It reads bounded HNSHV002 archives embedded in main or scanned RomFS files,
including HHNEEDLE shader-file wrappers such as Frontiers' NeedleShader.pac.
NVN records can have a 0x90 descriptor, a different self-consistent descriptor
size, or a control section directly followed by code. Descriptor offsets and
lengths must fit the containing archive; existing shader-stage and control/code
length validation still applies. Unreadable sections are reported without
throwing away other readable sections in the file. If no section is readable,
the file still fails. Independent vertex programs use the existing separable
pixel-input-cover pairing; arbitrary vertex/pixel pairs are not invented.

The normal blob scan now runs the reader before the raw-main fallback. It
therefore also excludes successfully extracted controls from raw rediscovery.
No zero-shader guard has been removed and no shader or game data is bundled.

## Verification and remaining work

Synthetic tests in `tests/test_shader_needle.py` cover embedded vertex/pixel/
compute variants, alternate and broken descriptors, descriptorless payloads,
HHNEEDLE wrappers, bounds and partially unreadable files. The existing ADRP,
CPK, spill, pool, mapping, CMP and BND3 regressions also pass (42 tests total).

The user's NSP is not available in the development environment. Repeat the
full port with an APK containing this change and check Stage 5's shader count.
This backport addresses a missing reader and the specific Frontiers layout
fix in the supplied branch; it does not prove all of the game's material
shaders are extracted, that compressed outer containers are supported, or
that later translation/runtime stages work. If the build still stops, share
the log bundle including `shader_split.log` and survey metadata.
