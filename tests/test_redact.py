from neonseek.redact import MASK, Redactor


def test_cuts_plain_json_and_url_forms():
    cut = Redactor(['p@ss "wörd"\\x'])
    plain = 'login p@ss "wörd"\\x done'
    as_json = '{"pass": "p@ss \\"w\\u00f6rd\\"\\\\x"}'
    as_url = "pass=p%40ss%20%22w%C3%B6rd%22%5Cx"
    for text in (plain, as_json, as_url):
        assert cut.hits(text) >= 1
        assert "rd" not in cut(text).replace(MASK, "")


def test_longest_first_and_empty_secrets():
    cut = Redactor(["abc", "abcdef", ""])
    assert cut("xabcdefx abc") == f"x{MASK}x {MASK}"
    assert Redactor([""])("nothing") == "nothing"
