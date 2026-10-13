# Fin AI — AI-Powered Financial Assistant

Fin AI is a financial assistant with a FastAPI backend and a static HTML/CSS/JavaScript frontend. It includes document-based financial question answering, financial-rate data, account authentication, and personal finance features.

## Features

- Financial question answering backed by financial documents and retrieval
- FastAPI backend
- MySQL database integration
- Static frontend pages for authentication, dashboard, investments, net worth, notifications, and profile
- PDF/document parsing utilities
- Financial-rate endpoints

## Technology

- Python and FastAPI
- MySQL
- FAISS and Sentence Transformers for document retrieval
- HTML, CSS, and JavaScript
- OpenRouter-compatible LLM API integration

## Repository Structure

```text
backend/
  main.py
  config.py
  rag.py
  rates.py
  requirements.txt
  documents/
documents/
frontend/
README.md