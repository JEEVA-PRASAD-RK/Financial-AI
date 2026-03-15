import os
import httpx
from fastapi import FastAPI, Form
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from pypdf import PdfReader
import docx

from backend.rag import add_document, build_index, search

# -----------------------------
# Load API key
# -----------------------------
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

API_KEY = os.getenv("OPENROUTER_API_KEY")
print("Loaded API KEY:", API_KEY)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

MODEL = "openai/gpt-4o-mini"

# -----------------------------
# FastAPI
# -----------------------------
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------------
# Conversation memory
# -----------------------------
conversation = [
    {"role": "system", "content": "You are a helpful financial assistant."}
]

# -----------------------------
# Load documents
# -----------------------------
DOC_FOLDER = "documents"
print("Using API key:", API_KEY[:20])
for file in os.listdir(DOC_FOLDER):

    path = os.path.join(DOC_FOLDER, file)

    text = ""

    try:

        if file.lower().endswith(".pdf"):

            reader = PdfReader(path)

            for page in reader.pages:
                if page.extract_text():
                    text += page.extract_text()

        elif file.lower().endswith(".docx"):

            doc = docx.Document(path)

            text = "\n".join([p.text for p in doc.paragraphs])

        elif file.lower().endswith(".txt"):

            with open(path, "r", encoding="utf-8") as f:
                text = f.read()

    except Exception as e:

        print(f"Error loading {file}: {e}")

    if text:

        add_document(text)

        print(f"Loaded document: {file}")

# Build FAISS index
index = build_index()

print("FAISS index built successfully.")

# -----------------------------
# Chat endpoint
# -----------------------------
@app.post("/chat")
async def chat(message: str = Form(...)):

    # Search relevant context
    context = search(message, index, k=1)
    print("RAG context:", context)

    if context and context[0] != "No documents available.":

        prompt = f"""
Use the following financial document to answer the question.

Document:
{context[0]}

Question:
{message}

Answer clearly based on the document.
"""

    else:

        prompt = f"Answer the question: {message}"

    conversation.append({"role": "user", "content": prompt})

    # Limit conversation history
    if len(conversation) > 10:
        conversation.pop(1)

    data = {
        "model": MODEL,
        "messages": conversation,
        "max_tokens": 400
    }

    async with httpx.AsyncClient(timeout=20) as client:

        try:

            response = await client.post(
                OPENROUTER_URL,
                headers={
                    "Authorization": f"Bearer {API_KEY}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "http://localhost:8000",
                    "X-Title": "AI Financial Advisor"
                },
                json=data
            )

            result = response.json()

            if "choices" in result:
                reply = result["choices"][0]["message"]["content"]
            else:
                reply = f"AI server error: {result}"

        except Exception as e:

            reply = f"AI server error: {str(e)}"

    conversation.append({"role": "assistant", "content": reply})

    return {"reply": reply}