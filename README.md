# AI Financial Chatbot

An AI-powered financial assistant that answers user queries about financial policies such as SIP, SWP, mutual funds, and fixed deposits using a Retrieval-Augmented Generation (RAG) approach.

## Features

- Financial policy question answering
- Document-based knowledge retrieval
- FastAPI backend
- FAISS vector search
- PDF document ingestion

## Tech Stack

- Python
- FastAPI
- FAISS
- LangChain
- OpenAI / LLM
- React (frontend if used)

## Project Structure

```
ai-financial-chatbot
│
├── backend
│   ├── main.py
│   ├── rag.py
│   ├── documents
│   └── requirements.txt
│
├── frontend
│
└── README.md
```

## Installation

Clone the repository:

```
git clone https://github.com/JEEVA-PRASAD-RK/Financial-AI.git
```

Go to the project directory:

```
cd ai-financial-chatbot
```

Install dependencies:

```
pip install -r requirements.txt
```

## Run the Application

Start the backend server:

```
uvicorn main:app --reload
```

The API will run at:

```
http://127.0.0.1:8000
```

## Example Query

```
What is a fixed deposit?
```

The chatbot retrieves relevant information from financial documents and generates an answer.

## Future Improvements

- Add financial recommendation engine
- Integrate real-time financial data
- Deploy using Docker or cloud services

## Author

JEEVA PRASAD  
GitHub: https://github.com/JEEVA-PRASAD-RK