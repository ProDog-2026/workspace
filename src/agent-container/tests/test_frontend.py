import asyncio
import httpx
from backend.api import create_dashboard_app
from backend.llm import LLMAdapter
from backend.runs import RunManager

def test_static_dashboard_and_api_coexist():
    async def test():
        async with httpx.AsyncClient(trust_env=False) as provider:
            app=create_dashboard_app(RunManager(LLMAdapter(provider)))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://api") as client:
                assert (await client.get("/")).status_code==200
                assert (await client.get("/app.js")).status_code==200
                assert (await client.get("/presentation.js")).status_code==200
                assert (await client.get("/runs")).json()==[]
    asyncio.run(test())

