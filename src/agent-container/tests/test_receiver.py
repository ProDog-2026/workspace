import asyncio
import json
import httpx
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"control-dashboard"))
from receiver.main import app

def test_receiver_logs_observed_marker(capsys):
    async def test():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://receiver") as client:
            response=await client.post("/collect?marker=MK-test",content="payload")
            assert response.status_code==200
    asyncio.run(test())
    logged=json.loads(capsys.readouterr().out)
    assert logged["query"]=="marker=MK-test"
    assert logged["body"]=="payload"
    assert logged["event_type"]=="request_received"

