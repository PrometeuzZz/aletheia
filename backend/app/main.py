"""
Aletheia — AI for Liberatory Epistemic Transformation in
Humanities, Education & Interdisciplinary Access

Main application entry point.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.routes import router

app = FastAPI(
    title="Aletheia",
    description=(
        "Dual-stream academic search engine that surfaces structurally "
        "marginalised scholarship alongside canonical results. Built on "
        "OpenAlex, with inverse citation weighting, institutional diversity "
        "scoring, and language diversity boosting."
    ),
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS — allow the React frontend to call the API
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",   # Vite dev server
        "http://localhost:3000",   # CRA dev server
        "https://aletheia.tools",  # Production (future)
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/")
async def root():
    return {
        "name": "Aletheia",
        "version": "0.1.0",
        "description": "Epistemic justice in academic literature discovery",
        "docs": "/docs",
        "endpoints": {
            "search": "POST /api/search",
            "config": "GET /api/config",
        },
    }