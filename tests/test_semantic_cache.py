"""Test B1-B5: cache sémantique hybride."""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from icy_semantic import (
    normalize_numbers, extract_hard, extract_soft, accepts,
)


def test_b1_normalize_numbers():
    """B1: 'two plus two' → '2+2' avant hash (hit exact sans sémantique)."""
    r = normalize_numbers("What is two plus two?")
    assert "2+2" in r, f"normalize_numbers failed: {r!r}"
    print(f"  B1 PASS: 'two plus two' → {r!r}")


def test_b4_grounding_ancres():
    """B4: Montreal ≠ Quebec City (ancres soft ≠ → miss même si cosinus high)."""
    query = "What is the weather in Montreal?"
    cand_mtl = {"anchors_hard": [], "anchors_soft": ["montreal"]}
    cand_qc = {"anchors_soft": ["quebec", "city"]}

    assert accepts(query, cand_mtl, 0.99), "Montreal should match Montreal"
    assert not accepts(query, cand_qc, 0.99), "Montreal should NOT match Quebec City"
    print("  B4 PASS: Montreal ≠ Quebec City (grounding works)")


def test_b5_qdrant_down():
    """B5: Qdrant indispo → miss silencieux, pas de crash."""
    from icy_semantic import SemanticStore
    store = SemanticStore("http://127.0.0.1:6333")
    if store.available:
        print("  B5 SKIP: Qdrant is running (test would need mock)")
    else:
        result = store.search("sig123", [0.1] * 128, 0.95)
        assert result is None, "should return None when Qdrant down"
        print("  B5 PASS: Qdrant down → miss silencieux (None)")


def test_anchors():
    """Test extraction ancres."""
    t = "Check port 8080 on host 10.0.0.5"
    h = extract_hard(t)
    s = extract_soft(t)
    assert "8080" in h, f"hard anchors missing port: {h}"
    assert "10.0" in h, f"hard anchors missing IP parts: {h}"
    print(f"  Anchors: hard={h}, soft={s}")
    print("  PASS: anchors extracted correctly")


if __name__ == "__main__":
    print("=== Test B1: normalize_numbers ===")
    test_b1_normalize_numbers()
    print("=== Test B4: grounding ancres ===")
    test_b4_grounding_ancres()
    print("=== Test B5: Qdrant down ===")
    test_b5_qdrant_down()
    print("=== Test anchors ===")
    test_anchors()
    print("=== done ===")
