from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="Croatia Job Agent",
    description="Backend for searching jobs in Croatia",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def home():
    return {
        "status": "ok",
        "message": "Croatia Job Agent is running"
    }


@app.get("/health")
def health():
    return {
        "status": "healthy"
    }


@app.get("/api/search")
def search_jobs():
    return {
        "status": "ok",
        "message": "Job search engine ready",
        "jobs": []
    } 
