import json
from pathlib import Path

from sre_copilot.rag.chunker import chunk_text
from sre_copilot.rag.embeddings import BGE3Embeddings
from sre_copilot.rag.store import get_connection, create_table

# One-off script that loads data/incidents.json, turns each incident into a
# readable text document, splits those documents into overlapping chunks,
# embeds all the chunks with BGE-M3, and upserts everything into the
# rag_chunks table in Postgres. Run it with `python -m sre_copilot.rag.ingest`
# whenever the incidents dataset changes, so the RAG retriever has fresh
# data to search over.


INCIDENT_FILE = (
    Path(__file__).parent.parent.parent.parent
    / "data"
    / "incidents.json"
)

DEFAULT_TENANT = "default"


def load_incidents():
    with INCIDENT_FILE.open(encoding="utf-8") as file:
        return json.load(file)


def build_documents(incidents):
    documents = []

    for incident in incidents:
        text = (
            f"Service: {incident['service']}\n"
            f"Incident: {incident['incident_id']}\n"
            f"Summary: {incident['summary']}\n"
            f"Root cause: {incident['root_cause']}\n"
            f"Resolution: {incident['resolution']}"
        )

        documents.append(
            {
                "document_id": incident["incident_id"],
                "service": incident["service"],
                "title": incident["summary"],
                "content": text,
            }
        )

    return documents


def main():
    create_table()

    incidents = load_incidents()
    documents = build_documents(incidents)

    chunks = []

    for document in documents:
        document_chunks = chunk_text(
            document["content"],
            chunk_size=60,
            chunk_overlap=15,
        )

        for chunk_index, content in enumerate(document_chunks):
            chunks.append(
                {
                    "document_id": document["document_id"],
                    "chunk_id": f"{document['document_id']}-chunk-{chunk_index}",
                    "chunk_index": chunk_index,
                    "service": document["service"],
                    "document_type": "incident",
                    "title": document["title"],
                    "content": content,
                }
            )

    print(f"Loaded {len(documents)} incidents")
    print(f"Created {len(chunks)} chunks")

    embedder = BGE3Embeddings()
    embeddings = embedder.encode([chunk["content"] for chunk in chunks])
    dense_vectors = embeddings["dense"]

    with get_connection() as connection:
        for index, chunk in enumerate(chunks):
            connection.execute(
                """
                INSERT INTO rag_chunks (
                    tenant,
                    document_id,
                    chunk_id,
                    chunk_index,
                    service,
                    document_type,
                    title,
                    content,
                    embedding,
                    search_vector
                )
                VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    to_tsvector('english', %s)
                )
                ON CONFLICT (chunk_id)
                DO UPDATE SET
                    tenant = EXCLUDED.tenant,
                    document_id = EXCLUDED.document_id,
                    service = EXCLUDED.service,
                    document_type = EXCLUDED.document_type,
                    title = EXCLUDED.title,
                    content = EXCLUDED.content,
                    embedding = EXCLUDED.embedding,
                    search_vector = EXCLUDED.search_vector
                """,
                (
                    DEFAULT_TENANT,
                    chunk["document_id"],
                    chunk["chunk_id"],
                    chunk["chunk_index"],
                    chunk["service"],
                    chunk["document_type"],
                    chunk["title"],
                    chunk["content"],
                    dense_vectors[index],
                    chunk["content"],
                ),
            )

        connection.commit()

    print(f"Inserted {len(chunks)} chunks into PostgreSQL")


if __name__ == "__main__":
    main()
