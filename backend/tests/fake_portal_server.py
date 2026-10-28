"""Fake customer-portal server for integration tests (item 25).

Run with: ``uvicorn tests.fake_portal_server:app`` from /backend.
"""

from __future__ import annotations

from fastapi import FastAPI

app = FastAPI(title="Fake Portal")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/admin/auth/login")
async def login():
    return {"statusCode": 200, "data": {"access_token": "fake-jwt"}, "message": "ok"}


@app.get("/appointments/getAllProviders")
async def providers():
    return {"statusCode": 200, "data": {"providers": [{"id": 1, "providerName": "Dr. Test"}]}}
EOF
echo done