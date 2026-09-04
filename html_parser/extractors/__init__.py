"""HTML Parser V2 - Extractors Package"""
from html_parser.extractors.canonical_url import CanonicalURLExtractor
from html_parser.extractors.metadata import MetadataExtractor
from html_parser.extractors.title import TitleExtractor
from html_parser.extractors.main_content import MainContentExtractor
from html_parser.extractors.snippet import SnippetGenerator
from html_parser.extractors.language import LanguageDetector

__all__ = [
    "CanonicalURLExtractor",
    "MetadataExtractor",
    "TitleExtractor",
    "MainContentExtractor",
    "SnippetGenerator",
    "LanguageDetector",
]
