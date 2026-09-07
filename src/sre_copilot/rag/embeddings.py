from FlagEmbedding import BGEM3FlagModel

# Thin wrapper around the BGE-M3 embedding model. The interesting bit is
# that BGE-M3 can produce two kinds of vectors from the same text in one
# pass: "dense" vectors (for semantic/meaning-based similarity search) and
# "sparse" vectors (basically weighted keywords, for exact-term matching).
# The retriever uses both - dense for the vector search side and sparse-ish
# keyword search on the Postgres side - to cover both "similar meaning" and
# "exact words" queries.


class BGE3Embeddings:

    def __init__(self):
        self.model = BGEM3FlagModel(
            "BAAI/bge-m3",
            use_fp16=False,
        )

    def encode(self, texts):
        output = self.model.encode(
            texts,
            return_dense=True,
            return_sparse=True,
            return_colbert_vecs=False,
        )

        return {
            "dense": output["dense_vecs"],
            "sparse": output["lexical_weights"],
        }