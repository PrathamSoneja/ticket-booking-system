import re
from dataclasses import dataclass

from nltk.corpus import stopwords

from .seed import catalog


@dataclass(frozen=True)
class FaqTopic:
    topic_key: str
    display_title: str
    trigger_words: tuple
    body_text: str


class FaqSearchEngine:
    def __init__(self, topic_list=None):
        if topic_list is not None:
            self.topic_list = list(topic_list)
        else:
            self.topic_list = []
            for row in catalog.execute("SELECT topic_key, display_title, trigger_words, body_text FROM faq"):
                self.topic_list.append(FaqTopic(row[0], row[1], tuple(row[2].split(",")), row[3]))

    def set_knowledge_base(self, new_topics):
        self.topic_list = list(new_topics)

    def retrieve(self, user_query, top_k=2):
        word_set = set(re.findall(r"\b[a-zA-Z0-9_-]+\b", user_query.lower()))
        stop_word_set = set(stopwords.words("english"))
        query_words = word_set - stop_word_set

        if not query_words or not self.topic_list:
            return "", []

        query_as_lower = user_query.lower()
        scored_topics = []

        for topic in self.topic_list:
            this_score = 0
            for kw in topic.trigger_words:
                if kw.lower() in query_as_lower:
                    this_score = this_score + 10

            title_words = set(re.findall(r"\b[a-zA-Z0-9_-]+\b", topic.display_title.lower())) - stop_word_set
            this_score = this_score + 3 * len(query_words & title_words)

            body_words = set(re.findall(r"\b[a-zA-Z0-9_-]+\b", topic.body_text.lower())) - stop_word_set
            this_score = this_score + len(query_words & body_words)

            if this_score:
                scored_topics.append((this_score, topic))

        scored_topics.sort(key=lambda pair: pair[0], reverse=True)

        top_topics = []
        i = 0
        for score_val, topic_val in scored_topics:
            if i >= top_k:
                break
            top_topics.append(topic_val)
            i = i + 1

        text_chunks = []
        id_list = []
        for t in top_topics:
            text_chunks.append(f"[{t.display_title}]\n{t.body_text}")
            id_list.append(t.topic_key)

        return "\n\n".join(text_chunks), id_list


FAQRetriever = FaqSearchEngine
