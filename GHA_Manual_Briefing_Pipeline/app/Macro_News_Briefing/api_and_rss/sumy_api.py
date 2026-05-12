from __future__ import absolute_import
from __future__ import division, print_function, unicode_literals

import nltk
from sumy.parsers.plaintext import PlaintextParser
from sumy.nlp.tokenizers import Tokenizer
from sumy.summarizers.lsa import LsaSummarizer as Summarizer
from sumy.nlp.stemmers import Stemmer
from sumy.utils import get_stop_words
import settings


for _resource in ("tokenizers/punkt_tab", "tokenizers/punkt"):
    try:
        nltk.data.find(_resource)
    except LookupError:
        nltk.download(_resource.split("/")[1], quiet=True)


LANGUAGE = "english"
SENTENCES_COUNT = settings.SUMY_SENTENCE


def _fallback_truncate(text: str, count: int) -> str:
    sentences = [s.strip() for s in text.replace("\n", " ").split(". ") if s.strip()]
    return ". ".join(sentences[:count]) + ("." if sentences else "")


def summarize_sumy(text, sentences_count=SENTENCES_COUNT):
    """
    Summarize the given text string using Sumy's LSA summarizer.
    :param text: Input text string to summarize
    :param sentences_count: Number of sentences in the summary
    :return: List of summarized sentences
    """
    try:
        parser = PlaintextParser.from_string(text, Tokenizer(LANGUAGE))
        stemmer = Stemmer(LANGUAGE)

        summarizer = Summarizer(stemmer)
        summarizer.stop_words = get_stop_words(LANGUAGE)

        summary_sentences = [str(sentence) for sentence in summarizer(parser.document, sentences_count)]
        return " ".join(summary_sentences)
    except LookupError:
        return _fallback_truncate(text, sentences_count)

