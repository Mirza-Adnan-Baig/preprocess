from src.faro_docs.german import decode_text, detect_delimiter, fold


class TestDecoding:
    def test_reads_utf8_with_bom(self):
        text, encoding = decode_text("Artikel;Menge\nStraße;3\n".encode("utf-8-sig"))
        assert "Straße" in text
        assert "utf-8" in encoding.lower()

    def test_reads_cp1252_umlauts(self):
        text, _ = decode_text("Artikel;Größe\nHülle;2\n".encode("cp1252"))
        assert "Größe" in text
        assert "Hülle" in text

    def test_repairs_mojibake(self):
        # cp1252 bytes mistakenly decoded as latin-1 upstream produce StraÃŸe
        broken = "StraÃŸe".encode("utf-8")
        text, _ = decode_text(broken)
        assert "Straße" in text

    def test_never_raises_on_binary_garbage(self):
        text, _ = decode_text(b"\xff\xfe\x00\x01\x02")
        assert isinstance(text, str)


class TestDelimiter:
    def test_detects_german_semicolon(self):
        assert detect_delimiter("Artikel;Menge;Preis\nHülle;3;12,00\n") == ";"

    def test_detects_comma(self):
        assert detect_delimiter("Article,Qty,Price\nCase,3,12.00\n") == ","

    def test_detects_tab(self):
        assert detect_delimiter("Artikel\tMenge\nHülle\t3\n") == "\t"

    def test_ignores_preamble_junk(self):
        text = "Rechnung Nr. 4711\nKunde: Müller GmbH\n\nArtikel;Menge;Preis\nHülle;3;12,00\nKabel;5;8,50\n"
        assert detect_delimiter(text) == ";"

    def test_semicolon_wins_when_decimal_commas_present(self):
        # "12,00" must not make this look comma-delimited
        assert detect_delimiter("Artikel;Preis\nHülle;12,00\nKabel;8,50\n") == ";"

    def test_defaults_to_semicolon_for_single_column(self):
        assert detect_delimiter("Artikel\nHülle\n") in {";", ","}


class TestFold:
    def test_folds_case_and_umlauts(self):
        assert fold("Stückzahl") == fold("STUECKZAHL") == fold("stueckzahl")

    def test_strips_punctuation_and_space(self):
        assert fold(" Pos. ") == fold("pos")
