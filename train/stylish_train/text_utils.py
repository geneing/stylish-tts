# IPA Phonemizer: https://github.com/bootphon/phonemizer
# from config_loader import SymbolConfig
import logging

logger = logging.getLogger(__name__)


class TextCleaner:
    def __init__(self, symbols):
        self._pad = symbols.pad  # "$"
        self._punctuation = symbols.punctuation  # ';:,.!?¡¿—…"()“” '
        self._letters = (
            symbols.letters
        )  # 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz'
        self._letters_ipa = (
            symbols.letters_ipa
        )  # "ɑɐɒæɓʙβɔɕçɗɖðʤəɘɚɛɜɝɞɟʄɡɠɢʛɦɧħɥʜɨɪʝɭɬɫɮʟɱɯɰŋɳɲɴøɵɸθœɶʘɹɺɾɻʀʁɽʂʃʈʧʉʊʋⱱʌɣɤʍχʎʏʑʐʒʔʡʕʢǀǁǂǃˈˌːˑʼʴʰʱʲʷˠˤ˞↓↑→↗↘'̩'ᵻ"
        self.word_index_dictionary = self.build_text_cleaner()
        self.unvoiced_dictionary = {
            symbols.unvoiced[i] for i in range(len(symbols.unvoiced))
        }
        # logger.debug(len(dicts))

    def __call__(self, text):
        indexes = []
        for char in text:
            try:
                indexes.append(self.word_index_dictionary[char])
            except KeyError:
                #ei logger.error("Meld " + char + ": " + text)
                pass
        return indexes

    def build_text_cleaner(self):
        # Export all symbols:
        symbols = (
            [self._pad]
            + list(self._punctuation)
            + list(self._letters)
            + list(self._letters_ipa)
        )
        symbol_dict = {}
        for i in range(len((symbols))):
            symbol_dict[symbols[i]] = i
        return symbol_dict

    def is_voiced(self, text):
        return [
            1.0 if text[i] not in self.unvoiced_dictionary else 0.0
            for i in range(len(text))
        ]
