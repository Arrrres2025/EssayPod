from __future__ import annotations

import math
import re
from collections import Counter


def tokenize(text: str) -> list[str]:
    lowered = text.lower()
    words = re.findall(r"[a-z0-9_+-]+", lowered)
    cjk = re.findall(r"[\u3400-\u9fff]", lowered)
    cjk_bigrams = [cjk[index] + cjk[index + 1] for index in range(len(cjk) - 1)]
    return words + cjk + cjk_bigrams


class BM25:
    def __init__(self, documents: list[str], k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.term_frequencies = [Counter(tokenize(document)) for document in documents]
        self.lengths = [sum(frequencies.values()) for frequencies in self.term_frequencies]
        self.average_length = sum(self.lengths) / max(1, len(self.lengths))
        document_frequency: Counter[str] = Counter()
        for frequencies in self.term_frequencies:
            document_frequency.update(frequencies.keys())
        total = max(1, len(documents))
        self.idf = {
            term: math.log(1 + (total - frequency + 0.5) / (frequency + 0.5))
            for term, frequency in document_frequency.items()
        }

    def scores(self, query: str) -> list[float]:
        terms = tokenize(query)
        result: list[float] = []
        for frequencies, length in zip(self.term_frequencies, self.lengths):
            score = 0.0
            for term in terms:
                frequency = frequencies.get(term, 0)
                if not frequency:
                    continue
                denominator = frequency + self.k1 * (
                    1 - self.b + self.b * length / max(1.0, self.average_length)
                )
                score += self.idf.get(term, 0.0) * frequency * (self.k1 + 1) / denominator
            result.append(score)
        return result

