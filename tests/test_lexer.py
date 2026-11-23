import struct
import unittest

from compiler.errors import LexicalError
from compiler.lexer import tokenize
from compiler.source import decode_utf8, encode_utf8
from compiler.tokens import TokenKind
from tests.helpers import source


def toks(code: str):
    return [t for t in tokenize(source(code)) if t.kind is not TokenKind.EOF]


def f32(x: float) -> int:
    return struct.unpack("<I", struct.pack("<f", x))[0]


class SourceTests(unittest.TestCase):
    """Раздел 11: исходный текст — строгий UTF-8, BOM необязателен."""

    def test_utf8_with_and_without_bom(self):
        self.assertEqual(decode_utf8("a", encode_utf8('class A { String s = "Ж"; }')).text,
                         'class A { String s = "Ж"; }')
        self.assertEqual(decode_utf8("a", encode_utf8("class A {}", bom=True)).text, "class A {}")

    def test_utf16_rejected(self):
        for data in (b"\xff\xfe" + "class A {}".encode("utf-16-le"), b"\xfe\xff" + "x".encode("utf-16-be")):
            with self.assertRaises(LexicalError) as cm:
                decode_utf8("a", data)
            self.assertIn("UTF-16", cm.exception.message)

    def test_invalid_utf8(self):
        bad = [b"a\x80b", b"\xc0\xaf", b"\xe0\x80\xaf", b"\xed\xa0\x80", b"\xf4\x90\x80\x80", b"ab\n\xe2\x82"]
        for data in bad:
            with self.assertRaises(LexicalError, msg=repr(data)):
                decode_utf8("a", data)

    def test_invalid_utf8_position(self):
        with self.assertRaises(LexicalError) as cm:
            decode_utf8("a", b"ab\ncd\xff")
        self.assertEqual((cm.exception.pos.line, cm.exception.pos.column), (2, 3))

    def test_supplementary_characters(self):
        self.assertEqual(decode_utf8("a", encode_utf8("\U0001F600")).text, "\U0001F600")

    def test_positions(self):
        src = source("a\r\n  b\rc")
        self.assertEqual((src.pos(4).line, src.pos(4).column), (2, 2))
        self.assertEqual(src.pos(7).line, 3)


class LexerTests(unittest.TestCase):
    def test_keywords_identifiers(self):
        t = toks("class Box extends _a$1 implements x throw throws")
        self.assertEqual([x.kind for x in t], [TokenKind.KEYWORD, TokenKind.IDENT, TokenKind.KEYWORD,
                                               TokenKind.IDENT, TokenKind.KEYWORD, TokenKind.IDENT,
                                               TokenKind.KEYWORD, TokenKind.KEYWORD])
        self.assertEqual(t[3].text, "_a$1")

    def test_non_ascii_identifier_rejected(self):
        for bad in ("class Ящик {}", "int переменная = 1;", "int xé = 1;"):
            with self.assertRaises(LexicalError, msg=bad) as cm:
                toks(bad)
            self.assertIn("ASCII", cm.exception.message)
        with self.assertRaises(LexicalError):        # не буква вовсе: недопустимый символ
            toks("int a\U0001F600;")

    def test_unicode_allowed_in_literals_and_comments(self):
        t = toks('// комментарий ✓\n"строка ✓" \'Ж\' /* ещё */')
        self.assertEqual(t[0].value, tuple(ord(c) for c in "строка ✓"))
        self.assertEqual(t[1].value, ord("Ж"))

    def test_token_positions(self):
        t = toks("int\n  x = 1;")
        self.assertEqual((t[1].line, t[1].column), (2, 3))

    def test_operators_longest_match(self):
        self.assertEqual([x.text for x in toks("a>>>=b>>>c>>d>=e==f&&g||h++")],
                         ["a", ">>>=", "b", ">>>", "c", ">>", "d", ">=", "e", "==", "f", "&&", "g", "||",
                          "h", "++"])

    def test_integer_literals(self):
        t = toks("0 42 0x7fffffff 0xFFFFFFFF 7L 0xFFFFFFFFFFFFFFFFL 2147483648")
        self.assertEqual([x.value for x in t], [0, 42, 2147483647, -1, 7, -1, 2147483648])
        self.assertEqual(t[4].kind, TokenKind.LONG_LIT)

    def test_integer_range(self):
        for bad in ("2147483649", "0x100000000", "9223372036854775809L"):
            with self.assertRaises(LexicalError, msg=bad):
                toks(bad)

    def test_unsupported_number_forms(self):
        for bad, msg in (("012", "восьмеричные"), ("0b101", "двоичные"), ("1_000", "_"),
                         ("0x1.0p3", "шестнадцатеричные"), ("12abc", "некорректный")):
            with self.assertRaises(LexicalError) as cm:
                toks(bad)
            self.assertIn(msg, cm.exception.message)

    def test_float_literals(self):
        t = toks("1.5 .5 2. 1e3 1.5f 3D 1e-45f 0.1f")
        self.assertEqual([x.kind for x in t], [TokenKind.DOUBLE_LIT] * 4 + [TokenKind.FLOAT_LIT, TokenKind.DOUBLE_LIT,
                                                                         TokenKind.FLOAT_LIT, TokenKind.FLOAT_LIT])
        self.assertEqual(t[0].value.value, 1.5)
        self.assertEqual(t[3].value.value, 1000.0)
        self.assertEqual(t[6].value.bits, 1)              # наименьший субнормальный float
        self.assertEqual(t[7].value.bits, f32(0.1))       # точное округление, не через double

    def test_float_range(self):
        for bad in ("1e39f", "1e309", "1e-50f", "1e-400"):
            with self.assertRaises(LexicalError, msg=bad):
                toks(bad)

    def test_char_and_string_literals(self):
        t = toks(r"'a' '\n' 'A' '\\' '\'' " + '"x\\ty\\"\\u00e9" "\U0001F600" \'\U0001F600\'')
        self.assertEqual([x.value for x in t[:5]], [97, 10, 65, 92, 39])
        self.assertEqual(t[5].value, (0x78, 9, 0x79, 0x22, 0xE9))
        self.assertEqual(t[6].value, (0x1F600,))         # кодовые точки, не UTF-16
        self.assertEqual(t[7].value, 0x1F600)            # char — Unicode scalar value

    def test_surrogate_escapes_rejected(self):
        for bad in ('"\\ud800"', "'\\udfff'", '"\\ud83d\\ude00"'):
            with self.assertRaises(LexicalError, msg=bad):
                toks(bad)

    def test_bad_escapes_and_literals(self):
        for bad in (r"'\r'", r'"\b"', r"'\u12'", "''", "'ab'", '"abc', '"a\nb"'):
            with self.assertRaises(LexicalError, msg=bad):
                toks(bad)

    def test_comments(self):
        self.assertEqual([x.text for x in toks("a // c\n /* x\n y */ b")], ["a", "b"])
        with self.assertRaises(LexicalError):
            toks("a /* never")

    def test_invalid_character(self):
        with self.assertRaises(LexicalError) as cm:
            toks("int x = #;")
        self.assertIn("U+0023", cm.exception.message)
        self.assertEqual(cm.exception.pos.column, 9)

    def test_literals_kinds(self):
        t = toks("true false null")
        self.assertEqual([x.kind for x in t], [TokenKind.BOOL_LIT, TokenKind.BOOL_LIT, TokenKind.NULL_LIT])

    def test_underscore_identifier(self):
        with self.assertRaises(LexicalError):
            toks("int _ = 1;")


if __name__ == "__main__":
    unittest.main()
