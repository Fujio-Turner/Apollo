from __future__ import annotations

import networkx as nx

_EMBED_TYPES = {"function", "method", "class", "document", "section"}
_MIN_TEXT_LENGTH = 40


class Embedder:
    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        self.model_name = model_name
        self._model = None

    def _load_model(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError:
                raise ImportError(
                    "sentence-transformers is required for embedding. "
                    "Install it with: pip install sentence-transformers"
                )
            # Newer transformers releases initialize weights on the "meta" device
            # when low_cpu_mem_usage is on; passing device="cpu" then triggers a
            # .to("cpu") on meta tensors which raises NotImplementedError.
            # Load without forcing a device — SentenceTransformer picks CPU when
            # no CUDA/MPS is available.
            self._model = SentenceTransformer(self.model_name)
        return self._model

    def embed_texts(self, texts: list[str], batch_size: int = 256) -> list[list[float]]:
        model = self._load_model()
        embeddings = model.encode(texts, batch_size=batch_size, show_progress_bar=True)
        return [emb.tolist() for emb in embeddings]

    def embed_single(self, text: str) -> list[float]:
        return self.embed_texts([text])[0]

    def embed_graph(self, graph: nx.DiGraph) -> None:
        node_ids = []
        texts = []
        for node_id, data in graph.nodes(data=True):
            if data.get("type") not in _EMBED_TYPES:
                continue
            source = data.get("source")
            if source and len(source.strip()) >= _MIN_TEXT_LENGTH:
                node_ids.append(node_id)
                texts.append(source)

        if not texts:
            return

        embeddings = self.embed_texts(texts)
        for node_id, embedding in zip(node_ids, embeddings):
            graph.nodes[node_id]["embedding"] = embedding
