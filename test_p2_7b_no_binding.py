"""P2-7B: Test with identity_binding disabled to check raw JSON-RPC reachability."""
import sys, os, asyncio, ssl, logging
sys.path.insert(0, "/opt/EduAgent")
os.chdir("/opt/EduAgent")
logging.basicConfig(level=logging.INFO)

from acps_sdk.aip.aip_rpc_client import AipRpcClient
from backend.config import settings
from pathlib import Path

TARGET_URL = "https://189-24-70-179.sslip.io/agents/01-task-orchestrator/v1/jsonrpc"
LOCAL_AIC  = "1.2.156.3088.1.BUPT.1LW3RI.PLJ4AG.CPXIZB.0559"

# Build mTLS context manually
cert_path = Path(settings.ACP_CLIENT_CERT_PATH)
key_path = Path(settings.ACP_CLIENT_KEY_PATH)
ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_REQUIRED
ctx.load_cert_chain(str(cert_path), str(key_path))
ctx.load_default_certs()

# Create client WITHOUT identity binding
client = AipRpcClient(
    partner_url=TARGET_URL,
    leader_id=LOCAL_AIC,
    ssl_context=ctx,
    caller_service=settings.SERVICE_NAME,
    expected_partner_aic=None,
    identity_binding_enabled=False,
)

async def main():
    try:
        result = await asyncio.wait_for(
            client.start_task(
                session_id="sess-p2-7b-test",
                user_input="请识别以下问题的类别并分配智能体：如何提升初中数学课堂的学生参与度？",
            ),
            timeout=30.0,
        )
        print(f"[OK] taskId   = {result.taskId}")
        print(f"[OK] status   = {result.status}")
        print(f"[OK] products = {result.products}")
    except Exception as e:
        print(f"[FAIL] {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await client.close()

asyncio.run(main())
