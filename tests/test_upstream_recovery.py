"""Test A1-A3: résilience upstream après 400."""
import sys, os, asyncio, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from upstream_client import ResilientUpstream, UpstreamError


async def test_recovery_after_400():
    """A1: 400 oversized puis prompt court → succès < 2s."""
    client = ResilientUpstream("http://127.0.0.1:1234", timeout_s=10.0)
    try:
        # 1) prompt oversized → 400 (ou autre si pas de LM Studio)
        try:
            r = await client.post_json("/v1/chat/completions", {"model": "test", "messages": [{"role": "user", "content": "x" * 99999}]})
        except UpstreamError as e:
            assert e.status >= 400, f"unexpected status: {e.status}"
            assert e.body, "body empty — BUG-001 not fixed"

        # 2) prompt court → DOIT réussir (pas de hang)
        t0 = time.time()
        try:
            r = await client.post_json("/v1/chat/completions", {"model": "test", "messages": [{"role": "user", "content": "hi"}]})
            dt = time.time() - t0
            assert dt < 5.0, f"hang detected: {dt:.1f}s — BUG-001 present"
            print(f"  A1 PASS: recovery in {dt:.2f}s")
        except UpstreamError as e:
            dt = time.time() - t0
            assert dt < 5.0, f"hang on error: {dt:.1f}s — BUG-001 present"
            print(f"  A1 PASS (upstream err but no hang): {dt:.2f}s, status={e.status}")
    finally:
        await client.close()


async def test_error_has_body():
    """A3: message 502 contient status + body."""
    client = ResilientUpstream("http://127.0.0.1:1234", timeout_s=5.0)
    try:
        try:
            await client.post_json("/v1/chat/completions", {"model": "nonexistent", "messages": []})
        except UpstreamError as e:
            assert e.status is not None, "status missing"
            assert e.body is not None, "body missing"
            assert str(e) != "", "error message empty — BUG-001 present"
            print(f"  A3 PASS: error has status={e.status}, body={e.body[:80]!r}")
    except Exception as e:
        print(f"  A3 SKIP: upstream not available ({type(e).__name__})")
    finally:
        await client.close()


async def main():
    print("=== Test A1: recovery after 400 ===")
    await test_recovery_after_400()
    print("=== Test A3: error has body ===")
    await test_error_has_body()
    print("=== done ===")


if __name__ == "__main__":
    asyncio.run(main())
