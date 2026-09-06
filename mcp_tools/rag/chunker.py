# Splits a document into overlapping word chunks so it can be embedded and
# indexed for search. The overlap matters: without it, a sentence that
# straddles a chunk boundary would get cut in half and lose its meaning in
# both chunks. Overlapping means the tail of one chunk repeats as the head
# of the next, so context near the edges isn't lost.


def chunk_text(
    text: str,
    chunk_size: int = 60,
    chunk_overlap: int = 15,
) -> list[str]:
    words = text.split()

    if not words:
        return []

    if chunk_overlap >= chunk_size:
        raise ValueError(
            "chunk_overlap must be smaller than chunk_size"
        )

    chunks = []
    step = chunk_size - chunk_overlap

    for start in range(0, len(words), step):
        chunk = words[start:start + chunk_size]

        if not chunk:
            break

        chunks.append(" ".join(chunk))

        if start + chunk_size >= len(words):
            break

    return chunks