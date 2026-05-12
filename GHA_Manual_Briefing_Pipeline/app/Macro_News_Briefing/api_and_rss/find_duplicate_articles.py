from sentence_transformers import SentenceTransformer, util
from settings import SIM_THRESHOLD, SIM_MODEL

model = SentenceTransformer(SIM_MODEL)

_korean_model = None
_KOREAN_MODEL_NAME = "BAAI/bge-m3"


def _get_korean_model():
    global _korean_model
    if _korean_model is None:
        _korean_model = SentenceTransformer(_KOREAN_MODEL_NAME)
    return _korean_model


def get_embedding(text: str):
    return model.encode(text, convert_to_tensor=True, show_progress_bar=False)


def get_korean_embedding(text: str):
    return _get_korean_model().encode(text, convert_to_tensor=True, show_progress_bar=False)


def find_similarity(emb1, emb2) -> float:
    return util.cos_sim(emb1, emb2).item()


def is_article_in_list(emb1, list_of_article_embeddings):
    for base_article_emb in list_of_article_embeddings:
        if find_similarity(emb1, base_article_emb) > SIM_THRESHOLD:
            return (True, base_article_emb)
    return (False, None)
