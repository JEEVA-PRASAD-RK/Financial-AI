from sentence_transformers import SentenceTransformer
import faiss
import numpy as np

# Load embedding model once
model = SentenceTransformer("all-MiniLM-L6-v2")

documents = []
vectors = []

# -----------------------------
# Split text into chunks
# -----------------------------
def split_text(text, chunk_size=1000):

    chunks = []
    for i in range(0, len(text), chunk_size):
        chunks.append(text[i:i+chunk_size])

    return chunks


# -----------------------------
# Add document
# -----------------------------
def add_document(text):

    chunks = split_text(text)

    embeddings = model.encode(
        chunks,
        convert_to_numpy=True,
        normalize_embeddings=True
    )

    for chunk, emb in zip(chunks, embeddings):
        documents.append(chunk)
        vectors.append(emb)


# -----------------------------
# Build FAISS index
# -----------------------------
def build_index():

    if len(vectors) == 0:
        return None

    dimension = len(vectors[0])

    index = faiss.IndexFlatIP(dimension)

    index.add(np.array(vectors, dtype=np.float32))

    return index


# -----------------------------
# Search documents
# -----------------------------
def search(query, index, k=1):

    if index is None:
        return ["No documents available."]

    query_vector = model.encode(
        [query],
        convert_to_numpy=True,
        normalize_embeddings=True
    )

    distances, indices = index.search(query_vector, k)

    results = [documents[i] for i in indices[0]]

    return results