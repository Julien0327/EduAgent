"""P2-7B: Minimal real Agent-to-Agent call verification."""
import sys
import os
sys.path.insert(0, "/opt/EduAgent")
os.chdir("/opt/EduAgent")

from backend.interop.agent_caller import call_agent_sync

TARGET_URL = "https://189-24-70-179.sslip.io/agents/01-task-orchestrator/v1/jsonrpc"
TARGET_AIC = "1.2.156.3088.1.BUPT.KFLNBC.R8E4R0.DU37EZ.07DV"
LOCAL_AIC  = "1.2.156.3088.1.BUPT.1LW3RI.PLJ4AG.CPXIZB.0559"
TEST_INPUT = "请识别以下问题的类别并分配智能体：如何提升初中数学课堂的学生参与度？"

print(f"[P2-7B] Target URL : {TARGET_URL}")
print(f"[P2-7B] Target AIC : {TARGET_AIC}")
print(f"[P2-7B] Local  AIC : {LOCAL_AIC}")
print(f"[P2-7B] Input      : {TEST_INPUT}")
print("-" * 60)

try:
    result = call_agent_sync(
        user_input=TEST_INPUT,
        partner_url=TARGET_URL,
        expected_partner_aic=TARGET_AIC,
        leader_id=LOCAL_AIC,
        timeout=30.0,
    )
    print(f"[OK] taskId   = {result.taskId}")
    print(f"[OK] status   = {result.status}")
    print(f"[OK] products = {result.products}")
    print(f"[OK] raw      = {result}")
except Exception as e:
    print(f"[FAIL] {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()
