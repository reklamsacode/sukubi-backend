from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.routers import auth, upload, jobs, listings, payments, analytics

settings = get_settings()

app = FastAPI(
    title="Nescora API",
    description="AI-powered photo enhancement platform for real estate agents",
    version="0.1.0",
)

# CORS — allow frontend origins
origins = [
    settings.FRONTEND_URL,
    "http://localhost:3000",
    "http://localhost:3001",
]
# Add Vercel preview/production URLs
if settings.FRONTEND_URL and "vercel" in settings.FRONTEND_URL:
    origins.append(settings.FRONTEND_URL)

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_origin_regex=r"https://.*\.vercel\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routes
app.include_router(auth.router, prefix="/api/v1")
app.include_router(upload.router, prefix="/api")
app.include_router(jobs.router, prefix="/api")
app.include_router(listings.router, prefix="/api/v1")
app.include_router(payments.router, prefix="/api/v1")
app.include_router(analytics.router, prefix="/api")


@app.get("/api/health")
def health_check():
    return {"status": "healthy", "service": "nescora-api", "version": "0.1.0"}


# Vercel serverless handler
handler = app
