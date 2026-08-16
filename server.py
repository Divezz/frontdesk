"""
AI Receptionist — live backend.

This is what you deploy once a client actually pays. The browser demo is the
sales tool; this is the product.

Design decisions worth explaining to a client (and in an Upwork proposal):
  * The assistant may ONLY use the business's own knowledge file. If the answer
    isn't in there, it says so and captures the enquiry. It is not allowed to
    invent prices or opening hours — that's the failure mode that makes owners
    distrust chatbots, so we engineer it out.
  * Retrieval first, then generation. We pass only the relevant chunks, which
    keeps token cost per conversation low (fractions of a penny) and the
    answers on-topic.
  * Unanswered questions are logged. That log is a recurring-revenue engine:
    every month you show the owner what customers asked that the bot couldn't
    answer, and you update the knowledge base for them.

Run:
    pip install fastapi uvicorn anthropic
    export ANTHROPIC_API_KEY=sk-ant-...
    uvicorn server:app --reload
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from anthropic import Anthropic
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

MODEL = "claude-sonnet-4-5"
KB_PATH = Path(os.getenv("KB_PATH", "knowledge.json"))
LEADS_PATH = Path(os.getenv("LEADS_PATH", "leads.jsonl"))
UNANSWERED_PATH = Path(os.getenv("UNANSWERED_PATH", "unanswered.jsonl"))
MAX_CHUNKS = 5

client = Anthropic()
app = FastAPI(title="AI Receptionist")

# Lock this down to the client's domain before going live.
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("ALLOWED_ORIGINS", "*").split(","),
    allow_methods=["POST"],
    allow_headers=["*"],
)

STOPWORDS = {
    "the", "a", "an", "is", "are", "do", "does", "did", "i", "you", "we", "to",
    "of", "for", "and", "or", "in", "on", "at", "it", "my", "your", "can",
    "could", "would", "please", "hi", "hello", "hey", "there", "me", "have",
    "has", "get", "got", "what", "whats", "how", "when", "where", "which",
    "if", "be", "am", "was", "were", "this", "that", "with", "about", "any",
}


def tokenize(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9']+", text.lower())
    return [w for w in words if w not in STOPWORDS]


def load_kb() -> list[dict]:
    """knowledge.json: [{"title": "...", "text": "...", "keywords": ["..."]}, ...]"""
    if not KB_PATH.exists():
        raise HTTPException(500, f"Knowledge base not found at {KB_PATH}")
    return json.loads(KB_PATH.read_text(encoding="utf-8"))


def retrieve(question: str, kb: list[dict]) -> list[dict]:
    """Keyword retrieval with IDF-ish weighting.

    Deliberately not embeddings: a single small business has 10-40 knowledge
    chunks, where keyword scoring is just as accurate, has no vector-DB
    dependency, costs nothing, and is debuggable when the owner says
    'why did it answer that?'. Swap in embeddings only if a client's KB
    grows past a few hundred chunks.
    """
    q_tokens = tokenize(question)
    if not q_tokens:
        return []

    # A word appearing in every chunk carries little signal; weight it down.
    doc_freq: dict[str, int] = {}
    chunk_tokens = []
    for chunk in kb:
        toks = set(tokenize(chunk["title"] + " " + chunk["text"] + " " + " ".join(chunk.get("keywords", []))))
        chunk_tokens.append(toks)
        for t in toks:
            doc_freq[t] = doc_freq.get(t, 0) + 1

    n = max(len(kb), 1)
    scored = []
    for chunk, toks in zip(kb, chunk_tokens):
        score = 0.0
        for qt in q_tokens:
            if qt in toks:
                score += n / (1 + doc_freq.get(qt, 0))
            else:
                # partial/stem match, e.g. "booking" vs "book"
                for t in toks:
                    if len(qt) > 3 and len(t) > 3 and (t.startswith(qt) or qt.startswith(t)):
                        score += 0.4 * (n / (1 + doc_freq.get(t, 0)))
                        break
        # explicit keywords the owner set are a strong signal
        for kw in chunk.get("keywords", []):
            if kw.lower() in question.lower():
                score += 3.0
        if score > 0:
            scored.append((score, chunk))

    scored.sort(key=lambda p: p[0], reverse=True)
    return [c for _, c in scored[:MAX_CHUNKS]]


SYSTEM_TEMPLATE = """You are the front-desk assistant for {business}, answering \
customers on the business's website.

Rules you must follow exactly:
1. Answer ONLY from the BUSINESS INFORMATION below. It is the single source of truth.
2. If the information needed is not there, do NOT guess, estimate, or use general \
knowledge about similar businesses. Say you're not sure and offer to take the \
customer's details for the team. Never invent a price, time, phone number or policy.
3. Be warm, brief and practical — two or three sentences unless listing prices or hours.
4. Write like a helpful person at the desk, not a corporate chatbot. No emoji.
5. If someone wants to book, point them to the booking method in the information below.
6. If a customer is upset or wants to complain, don't argue or promise anything — \
apologise briefly and offer to pass it to a manager.

When you cannot answer from the information, end your reply with the exact token \
[NEEDS_HUMAN] on its own line. The website removes this token before display and \
uses it to show a contact form.

BUSINESS INFORMATION
--------------------
{context}"""


class Turn(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    history: list[Turn] = Field(default_factory=list, max_length=20)


class ChatResponse(BaseModel):
    reply: str
    needs_human: bool
    sources: list[str]


def append_jsonl(path: Path, record: dict) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    kb = load_kb()
    business = os.getenv("BUSINESS_NAME", "the business")
    chunks = retrieve(req.message, kb)

    if chunks:
        context = "\n\n".join(f"### {c['title']}\n{c['text']}" for c in chunks)
    else:
        context = "(No relevant information found for this question.)"

    messages = [{"role": t.role, "content": t.content} for t in req.history]
    messages.append({"role": "user", "content": req.message})

    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=500,
            system=SYSTEM_TEMPLATE.format(business=business, context=context),
            messages=messages,
        )
    except Exception as exc:  # network/quota/etc — never show a stack trace to a customer
        append_jsonl(UNANSWERED_PATH, {
            "at": datetime.now(timezone.utc).isoformat(),
            "question": req.message,
            "error": str(exc),
        })
        return ChatResponse(
            reply="Sorry — I'm having trouble right now. Leave your details and the team will come back to you.",
            needs_human=True,
            sources=[],
        )

    text = "".join(block.text for block in resp.content if block.type == "text").strip()
    needs_human = "[NEEDS_HUMAN]" in text
    reply = text.replace("[NEEDS_HUMAN]", "").strip()

    if needs_human:
        # The gold mine: what customers ask that the business hasn't answered yet.
        append_jsonl(UNANSWERED_PATH, {
            "at": datetime.now(timezone.utc).isoformat(),
            "question": req.message,
        })

    return ChatResponse(
        reply=reply,
        needs_human=needs_human,
        sources=[c["title"] for c in chunks],
    )


class Lead(BaseModel):
    name: str = Field(default="", max_length=100)
    contact: str = Field(min_length=3, max_length=200)
    question: str = Field(default="", max_length=1000)


@app.post("/lead")
def capture_lead(lead: Lead) -> dict:
    append_jsonl(LEADS_PATH, {
        "at": datetime.now(timezone.utc).isoformat(),
        **lead.model_dump(),
    })
    # In production, also email the owner here (Resend/SendGrid/SMTP) —
    # owners want it in their inbox, not in a dashboard they never open.
    return {"ok": True}


@app.get("/health")
def health() -> dict:
    return {"ok": True, "chunks": len(load_kb()) if KB_PATH.exists() else 0}
