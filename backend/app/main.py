from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.core.database import init_db
from app.core.storage import minio_client
from app.api import endpoints, auth, settings, orgs, projects, admin

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize DB
    await init_db()
    # Ensure MinIO bucket exists
    minio_client.ensure_bucket()
    yield

from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Quality Intelligence Platform", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, replace with specific origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(endpoints.router, prefix="/api")
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(settings.router, prefix="/api/settings", tags=["settings"])
app.include_router(orgs.router, prefix="/api", tags=["organizations"])
app.include_router(projects.router, prefix="/api", tags=["projects"])
app.include_router(admin.router, prefix="/api/admin", tags=["admin"])

@app.get("/health")
def health_check():
    return {"status": "ok"}

@app.get("/mock/bihar-election")
def mock_bihar_election():
    return {
        "id": 60985414,
        "qna": [
            {
                "question": "Summary",
                "answer": "The National Democratic Alliance (NDA), led by Chief Minister Nitish Kumar, secured a significant victory in the Bihar State Assembly election on November 14, 2025, surpassing 200 seats in the 243-member legislature. This win, attributed partly to Mr. Kumar's Mahila Rozgar Yojna, saw the NDA achieve a 44% vote share and 202 seats. In contrast, the Opposition INDIA bloc garnered nearly 36% of the votes but only 35 seats, indicating a substantial gap. Prime Minister Narendra Modi lauded the win, highlighting a new 'MY — Mahila and Youth' formula for success."
            },
            {
                "question": "What was the outcome of the Bihar State Assembly election on November 14, 2025?",
                "answer": "The National Democratic Alliance (NDA), led by Chief Minister Nitish Kumar, achieved a significant victory in the Bihar State Assembly election, securing over 200 seats in the 243-strong legislature."
            },
            {
                "question": "Which political alliance won the Bihar Assembly elections?",
                "answer": "The National Democratic Alliance (NDA) won the Bihar Assembly elections."
            },
            {
                "question": "How many seats did the NDA win in the Bihar Assembly elections?",
                "answer": "The NDA won 202 seats in the Bihar Assembly elections."
            },
            {
                "question": "What was the NDA's total vote share in the Bihar elections?",
                "answer": "The NDA's total vote share stood at 44% as of 11:30 p.m. on Friday, November 14, 2025."
            },
            {
                "question": "What was the seat distribution among the NDA constituents?",
                "answer": "The NDA's 202 seats were distributed as follows: BJP 89, JD(U) 85, LJP (RV) 19, HAM (S) 5, and RLM 4."
            },
            {
                "question": "What was the performance of the Opposition INDIA bloc?",
                "answer": "The Opposition INDIA bloc had a vote share of almost 36% but won only 35 seats."
            },
            {
                "question": "Which scheme is credited with playing a significant role in Nitish Kumar's victory?",
                "answer": "Mr. Kumar’s Mahila Rozgar Yojna, which provided ₹10,000 to approximately 1.25 crore women over the last two months, seemed to have played an important role in his victory."
            },
            {
                "question": "What was Prime Minister Narendra Modi's reaction to the NDA's victory?",
                "answer": "Prime Minister Narendra Modi hailed the NDA’s massive win, stating that the victory has given a new 'MY — Mahila and Youth' formula, which he contrasted with the \"communal MY formula of the jungle raj people.\""
            },
            {
                "question": "How many seats did the Congress and RJD win within the Mahagathbandhan?",
                "answer": "The Congress won 6 seats out of the 62 it contested, and the RJD won 25 seats."
            },
            {
                "question": "What was the performance of the Left parties in the Bihar elections?",
                "answer": "The performance of all three Left parties was poor; the CPI (ML-Liberation) won two seats out of 20 contested, and the CPI(M) won one seat."
            }
        ]
    }
