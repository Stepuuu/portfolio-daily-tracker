"""Production routes with external service initialization disabled for browser fixtures."""
from contextlib import asynccontextmanager
from backend.main import app


@asynccontextmanager
async def fixture_lifespan(app):
    yield


app.router.lifespan_context = fixture_lifespan
