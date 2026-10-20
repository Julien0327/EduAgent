"""P2-8: Real Agent-to-Agent Business Closed-Loop Verification.
EduMate receives a teaching task → Discovery → Select Agent → RPC → Consume Result.
"""
import sys, os
sys.path.insert(0, "/opt/EduAgent")
os.chdir("/opt/EduAgent")

# Inject mTLS cert paths for discovery & RPC
os.environ["ACP_CERT_PATH"] = "/opt/EduAgent/certs/agent-server-cert.pem"
os.environ["ACP_KEY_PATH"] = "/opt/EduAgent/certs/agent-server-key.pem"
os.environ["ACP_TRUST_BUNDLE_PATH"] = "/opt/EduAgent/certs/trust-bundle.pem"
os.environ["ACP_AIC"] = "1.2.156.3088.1.BUPT.1LW3RI.PLJ4AG.CPXIZB.0559"

from backend.interop.orchestrator import orchestrate_sync

TEACHING_TASK = "请为初中二年级学生设计一节关于勾股定理的互动教学方案，包含课堂活动与课后练习。"

print("[P2-8] === EduMate Agent-to-Agent Business Closed-Loop ===")
print(f"[P2-8] Teaching Task: {TEACHING_TASK}")
print("-" * 70)

try:
    result = orchestrate_sync(
        query=TEACHING_TASK,
        timeout=45.0,
    )
    print("[OK] taskId   =", result.get("taskId"))
    print("[OK] status   =", result.get("status"))
    print("[OK] products =", result.get("products"))
    print("[OK] raw keys =", list(result.get("raw", {}).keys()) if isinstance(result.get("raw"), dict) else type(result.get("raw")))
    print("-" * 70)
    print("[P2-8] CLOSED-LOOP SUCCESS: EduMate consumed external Agent result.")
except Exception as e:
    print(f"[FAIL] {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()
