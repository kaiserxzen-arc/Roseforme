import struct
import tempfile
import unittest
from pathlib import Path

import xxhash

from injection.loadingname.loading_name import _read_entry, _xxh64
from injection.mods import skin_audio
from injection.mods.skin_audio import event_id


def fnv(name: str) -> int:
    h = 0x811C9DC5
    for c in name.lower().encode():
        h = ((h ^ c) * 0x01000193) & 0xFFFFFFFF
    return h


def string(text: str) -> bytes:
    data = text.encode()
    return struct.pack("<H", len(data)) + data


def field(name: str, kind: int, value: bytes) -> bytes:
    return struct.pack("<IB", fnv(name), kind) + value


def embed(cls: str, fields) -> bytes:
    body = struct.pack("<H", len(fields)) + b"".join(fields)
    return struct.pack("<II", fnv(cls), len(body)) + body


def string_list(items) -> bytes:
    body = struct.pack("<I", len(items)) + b"".join(string(i) for i in items)
    return struct.pack("<BI", 0x10, len(body)) + body


def bank_unit(name, paths, events=()) -> bytes:
    return embed("BankUnit", [
        field("name", 0x10, string(name)),
        field("bankPath", 0x80, string_list(paths)),
        field("events", 0x80, string_list(list(events))),
    ])


def skin_bin(units, tags=("Yasuo",), extra="tail") -> bytes:
    """A skin bin: one SkinCharacterDataProperties holding the audio, and one other entry."""
    unit_list = struct.pack("<I", len(units)) + b"".join(units)
    audio = embed("SkinAudioProperties", [
        field("tagEventList", 0x80, string_list(list(tags))),
        field("bankUnits", 0x81, struct.pack("<BI", 0x83, len(unit_list)) + unit_list),
    ])
    skin = [
        field("championSkinName", 0x10, string("Yasuo")),
        field("skinAudioProperties", 0x83, audio),
        field("skinScale", 0x0A, struct.pack("<f", 1.0)),
    ]
    entries = [
        (fnv("SkinCharacterDataProperties"), struct.pack("<IH", 0x1234, len(skin)) + b"".join(skin)),
        (fnv("Other"), struct.pack("<IH", 0x5678, 1) + field("note", 0x10, string(extra))),
    ]
    out = b"PROP" + struct.pack("<II", 3, 1) + string("DATA/Characters/Yasuo/Skins/Skin9.bin")
    out += struct.pack("<I", len(entries)) + b"".join(struct.pack("<I", t) for t, _ in entries)
    for _, body in entries:
        out += struct.pack("<I", len(body)) + body
    return out


def bank(events, aliases=()) -> bytes:
    """A Wwise events bank: each event plays one action (named after it); an alias (name, event) plays that
    event's action."""
    pairs = [(name, name) for name in events] + list(aliases)
    objects = b"".join(struct.pack("<BII", 4, 9, event_id(name)) + struct.pack("<BI", 1, event_id(source + "/a"))
                       for name, source in pairs)
    hirc = struct.pack("<I", len(pairs)) + objects
    bkhd = struct.pack("<II", 145, 1234)
    return b"BKHD" + struct.pack("<I", len(bkhd)) + bkhd + b"HIRC" + struct.pack("<I", len(hirc)) + hirc


def bank_events(data: bytes):
    """{event id: its action id} of a bank."""
    at = 8 + struct.unpack_from("<I", data, 4)[0] + 8
    out, p = {}, at + 4
    for _ in range(struct.unpack_from("<I", data, at)[0]):
        kind, size, oid = struct.unpack_from("<BII", data, p)
        if kind == 4:
            out[oid] = struct.unpack_from("<I", data, p + 10)[0]
        p += 5 + size
    return out


def wad(files) -> bytes:
    """A wad v3.4 of stored entries, sorted by path hash like the game's."""
    items = sorted((_xxh64(path.lower().encode()), data) for path, data in files.items())
    out = bytearray(b"RW\x03\x04" + bytes(264) + struct.pack("<I", len(items)))
    offset = len(out) + 32 * len(items)
    blobs = b""
    for path_hash, data in items:
        out += struct.pack("<QIIIBBHQ", path_hash, offset + len(blobs), len(data), len(data), 0, 0, 0,
                           xxhash.xxh3_64_intdigest(data))
        blobs += data
    return bytes(out + blobs)


SKIN0 = "data/characters/yasuo/skins/skin0.bin"
SKIN9 = "data/characters/yasuo/skins/skin9.bin"
SOUNDS = "ASSETS/Sounds/Wwise2016/SFX/Characters/Yasuo/Skins"
BASE_SFX = [f"{SOUNDS}/Base/Yasuo_Base_SFX_audio.bnk", f"{SOUNDS}/Base/Yasuo_Base_SFX_events.bnk"]
SKIN09_SFX = [f"{SOUNDS}/Skin09/Yasuo_Skin09_SFX_audio.bnk", f"{SOUNDS}/Skin09/Yasuo_Skin09_SFX_events.bnk"]
SKIN87_SFX = [f"{SOUNDS}/Skin87/Yasuo_Skin87_SFX_audio.bnk", f"{SOUNDS}/Skin87/Yasuo_Skin87_SFX_events.bnk"]
VO = ["ASSETS/Sounds/Wwise2016/VO/en_US/Characters/Yasuo/Skins/Skin09/Yasuo_Skin09_VO_audio.bnk"]
BASETAG = "ASSETS/Sounds/Wwise2016/VO/Characters/Yasuo/Skins/Skin56/Yasuo_Skin56_VO_basetag_events.bnk"

DASH_SKIN = "Play_sfx_YasuoSkin09_YasuoDashWrapper_cast"
DASH_BASE = "Play_sfx_Yasuo_YasuoDashWrapper_cast"
EQ_HIT_BASE = "Play_sfx_Yasuo_YasuoEQComboSoundHit_OnHit"


class SkinAudioTests(unittest.TestCase):
    def setUp(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        root = Path(temp_dir.name)
        self.game_dir = root / "Game"
        (self.game_dir / "DATA" / "FINAL" / "Champions").mkdir(parents=True)
        self.mod = root / "mods" / "157009"
        (self.mod / "WAD").mkdir(parents=True)
        self.mod_wad = self.mod / "WAD" / "Yasuo.wad.client"
        # Nightbringer Yasuo in the game: the default skin's banks and its own, played under its own tag
        self.game_files = {
            SKIN9: skin_bin([
                bank_unit("Yasuo_Base_SFX", BASE_SFX, [DASH_BASE, EQ_HIT_BASE]),
                bank_unit("Yasuo_Skin09_SFX", SKIN09_SFX, [DASH_SKIN]),
                bank_unit("Yasuo_Skin09_VO", VO),
            ], tags=("YasuoSkin09",)),
            BASE_SFX[1]: bank([DASH_BASE, EQ_HIT_BASE]),
            SKIN09_SFX[1]: bank([DASH_SKIN]),
        }
        self._game(self.game_files)
        # its package: another skin's sound effects, its own voice
        self.broken = skin_bin([
            bank_unit("Yasuo_Base_VO", VO),
            bank_unit("Yasuo_Base_SFX", SKIN87_SFX, ["Play_sfx_YasuoSkin87_YasuoDashWrapper_cast", DASH_BASE]),
        ])

    def _game(self, files):
        (self.game_dir / "DATA" / "FINAL" / "Champions" / "Yasuo.wad.client").write_bytes(wad(files))

    def _entry(self, path):
        return _read_entry(self.mod_wad, _xxh64(path.lower().encode()))

    def _units(self):
        return [(u.name, u.paths) for u in skin_audio.bank_units(self._entry(SKIN0))]

    def test_another_skins_sound_effects_become_the_skins_own(self):
        self.mod_wad.write_bytes(wad({SKIN0: self.broken}))

        self.assertEqual(skin_audio.repair(self.game_dir, self.mod, [157009]), ["Yasuo"])

        # the game's sound effect banks for skin 9; the voice the package had right is kept
        self.assertEqual(self._units(), [("Yasuo_Base_VO", VO), ("Yasuo_Base_SFX", BASE_SFX + SKIN09_SFX)])
        unit = skin_audio.bank_units(self._entry(SKIN0))[1]
        self.assertEqual(unit.events, [DASH_BASE, EQ_HIT_BASE, DASH_SKIN])
        # still played under the default skin's tag
        self.assertEqual(skin_audio.tag_events(self._entry(SKIN0)), ["Yasuo"])

    def test_the_default_skins_events_play_the_skins_sounds(self):
        self.mod_wad.write_bytes(wad({SKIN0: self.broken}))

        skin_audio.repair(self.game_dir, self.mod, [157009])

        skin_events = bank_events(self._entry(SKIN09_SFX[1]))
        base_events = bank_events(self._entry(BASE_SFX[1]))
        # the E plays Skin09's dash under the default name...
        self.assertEqual(skin_events[event_id(DASH_BASE)], skin_events[event_id(DASH_SKIN)])
        self.assertNotIn(event_id(DASH_BASE), base_events)
        # ...and what Skin09 has no sound of keeps the default skin's
        self.assertIn(event_id(EQ_HIT_BASE), base_events)
        self.assertNotIn(event_id(EQ_HIT_BASE), skin_events)

    def test_the_rest_of_the_package_is_kept(self):
        self.mod_wad.write_bytes(wad({SKIN0: self.broken, "assets/other.tex": b"texture"}))

        skin_audio.repair(self.game_dir, self.mod, [157009])

        self.assertEqual(self._entry("assets/other.tex"), b"texture")
        repaired = self._entry(SKIN0)
        self.assertTrue(repaired.endswith(string("tail")))
        self.assertEqual(repaired[:60], self.broken[:60])

    def test_the_rewritten_wad_is_valid(self):
        self.mod_wad.write_bytes(wad({SKIN0: self.broken, "assets/other.tex": b"texture"}))

        skin_audio.repair(self.game_dir, self.mod, [157009])

        data = self.mod_wad.read_bytes()
        count = struct.unpack_from("<I", data, 268)[0]
        hashes = []
        for i in range(count):
            path_hash, offset, packed, _, _, _, _, checksum = struct.unpack_from("<QIIIBBHQ", data, 272 + 32 * i)
            hashes.append(path_hash)
            self.assertEqual(checksum, xxhash.xxh3_64_intdigest(data[offset:offset + packed]))
        self.assertEqual(hashes, sorted(hashes))
        self.assertEqual(count, 4)  # skin0, the texture, and the two events banks

    def test_a_skin_playing_its_own_banks_is_left_alone(self):
        packed = wad({SKIN0: skin_bin([bank_unit("Yasuo_Base_SFX", BASE_SFX + SKIN09_SFX)])})
        self.mod_wad.write_bytes(packed)

        self.assertEqual(skin_audio.repair(self.game_dir, self.mod, [157009]), [])
        self.assertEqual(self.mod_wad.read_bytes(), packed)

    def test_a_bank_the_package_ships_itself_is_its_own(self):
        # a voice basetag bank built for the package, named after the skin it took the voice from
        packed = wad({SKIN0: skin_bin([bank_unit("Yasuo_Base_SFX", BASE_SFX + SKIN09_SFX),
                                       bank_unit("Yasuo_Base_VO", VO + [BASETAG])]),
                      BASETAG: bank([])})
        self.mod_wad.write_bytes(packed)

        self.assertEqual(skin_audio.repair(self.game_dir, self.mod, [157009]), [])
        self.assertEqual(self.mod_wad.read_bytes(), packed)

    def test_a_chroma_uses_its_skins_banks_when_the_game_has_none_of_its_own(self):
        self.mod_wad.write_bytes(wad({SKIN0: self.broken}))

        self.assertEqual(skin_audio.repair(self.game_dir, self.mod, [157012, 157009]), ["Yasuo"])

    def test_nothing_changes_when_the_game_does_not_have_the_skin(self):
        files = dict(self.game_files)
        files["data/characters/yasuo/skins/skin1.bin"] = files.pop(SKIN9)
        self._game(files)
        packed = wad({SKIN0: self.broken})
        self.mod_wad.write_bytes(packed)

        self.assertEqual(skin_audio.repair(self.game_dir, self.mod, [157009]), [])
        self.assertEqual(self.mod_wad.read_bytes(), packed)

    def test_an_unreadable_skin_never_fails_the_injection(self):
        self.mod_wad.write_bytes(wad({SKIN0: b"PROP garbage"}))

        self.assertEqual(skin_audio.repair(self.game_dir, self.mod, [157009]), [])

    def test_the_default_skin_is_left_alone(self):
        packed = wad({SKIN0: self.broken})
        self.mod_wad.write_bytes(packed)

        self.assertEqual(skin_audio.repair(self.game_dir, self.mod, [157000]), [])
        self.assertEqual(self.mod_wad.read_bytes(), packed)

    def _genesis(self):
        """Genesis Nightbringer Yasuo: the game gives skin 87 its own sound effects only, and the package (right
        about its banks) plays them under the default skin's names, without the default skin's bank."""
        skin87_bank = f"{SOUNDS}/Skin87/Yasuo_Skin87_SFX_events.bnk"
        dash_87 = "Play_sfx_YasuoSkin87_YasuoDashWrapper_cast"
        files = dict(self.game_files)
        files["data/characters/yasuo/skins/skin0.bin"] = skin_bin(
            [bank_unit("Yasuo_Base_SFX", BASE_SFX, [DASH_BASE, EQ_HIT_BASE])])
        files["data/characters/yasuo/skins/skin87.bin"] = skin_bin(
            [bank_unit("Yasuo_Skin87_SFX", SKIN87_SFX, [dash_87])], tags=("Yasuo", "YasuoSkin87"))
        files[skin87_bank] = bank([dash_87])
        self._game(files)
        # the package's copy of Skin87's bank: its dash under the default name too
        shipped = bank([dash_87], aliases=[(DASH_BASE, dash_87)])
        self.mod = self.mod.with_name("157087")
        (self.mod / "WAD").mkdir(parents=True)
        self.mod_wad = self.mod / "WAD" / "Yasuo.wad.client"
        self.mod_wad.write_bytes(wad({
            SKIN0: skin_bin([bank_unit("Yasuo_Base_SFX", SKIN87_SFX, [dash_87, DASH_BASE])]),
            skin87_bank: shipped,
        }))
        return skin87_bank

    def test_the_default_skins_banks_play_what_the_skin_has_no_sound_of(self):
        skin87_bank = self._genesis()
        self.assertEqual(bank_events(self._entry(skin87_bank)).keys(), {event_id(DASH_BASE),
                                                                        event_id("Play_sfx_YasuoSkin87_YasuoDashWrapper_cast")})

        self.assertEqual(skin_audio.repair(self.game_dir, self.mod, [157087]), ["Yasuo"])

        self.assertEqual(self._units(), [("Yasuo_Base_SFX", BASE_SFX + SKIN87_SFX)])
        base_events = bank_events(self._entry(BASE_SFX[1]))
        # the E-Q hit Skin87 has no sound of now plays the default skin's...
        self.assertIn(event_id(EQ_HIT_BASE), base_events)
        self.assertIn(EQ_HIT_BASE, skin_audio.bank_units(self._entry(SKIN0))[0].events)
        # ...while the dash stays Skin87's: the default skin's would be loaded first and played instead
        self.assertNotIn(event_id(DASH_BASE), base_events)
        self.assertIn(event_id(DASH_BASE), bank_events(self._entry(skin87_bank)))

    def test_a_skin_with_a_sound_for_every_default_event_needs_no_default_bank(self):
        self._genesis()
        files = dict(self.game_files)
        files["data/characters/yasuo/skins/skin0.bin"] = skin_bin(
            [bank_unit("Yasuo_Base_SFX", BASE_SFX, [DASH_BASE])])
        files["data/characters/yasuo/skins/skin87.bin"] = skin_bin(
            [bank_unit("Yasuo_Skin87_SFX", SKIN87_SFX)], tags=("Yasuo", "YasuoSkin87"))
        self._game(files)
        packed = self.mod_wad.read_bytes()

        self.assertEqual(skin_audio.repair(self.game_dir, self.mod, [157087]), [])
        self.assertEqual(self.mod_wad.read_bytes(), packed)

    def test_without_a_game_folder_nothing_is_done(self):
        self.mod_wad.write_bytes(wad({SKIN0: self.broken}))

        self.assertEqual(skin_audio.repair(None, self.mod, [157009]), [])


if __name__ == "__main__":
    unittest.main()
