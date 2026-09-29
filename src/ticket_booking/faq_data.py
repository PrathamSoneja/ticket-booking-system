import re
from dataclasses import dataclass

from nltk.corpus import stopwords

from .seed import catalog


@dataclass(frozen=True)
class Topic:
    key: str
    title: str
    words: tuple
    body: str


class FaqIndex:
    def __init__(self, topics=None):
        if topics is not None:
            self.topics = list(topics)
        else:
            self.topics = []
            for row in catalog.execute("SELECT topic_key, display_title, trigger_words, body_text FROM faq"):
                self.topics.append(Topic(row[0], row[1], tuple(row[2].split(",")), row[3]))

    def set_topics(self, topics):
        self.topics = list(topics)

    def retrieve(self, query, top_k=2):
        qset = set(re.findall(r"\b[a-zA-Z0-9_-]+\b", query.lower()))
        stops = set(stopwords.words("english"))
        qwords = qset - stops
        if not qwords or not self.topics:
            return "", []
        scored = []
        low = query.lower()
        for t in self.topics:
            score = 0
            for kw in t.words:
                if kw.lower() in low:
                    score = score + 10
            title_w = set(re.findall(r"\b[a-zA-Z0-9_-]+\b", t.title.lower())) - stops
            score = score + 3 * len(qwords & title_w)
            body_w = set(re.findall(r"\b[a-zA-Z0-9_-]+\b", t.body.lower())) - stops
            score = score + len(qwords & body_w)
            if score:
                scored.append((score, t))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        top = []
        i = 0
        for _, t in scored:
            if i >= top_k:
                break
            top.append(t)
            i = i + 1
        chunks = []
        ids = []
        for t in top:
            chunks.append(f"[{t.title}]\n{t.body}")
            ids.append(t.key)
        return "\n\n".join(chunks), ids
