"""Test extraction ancres — grounding anti-faux-positif."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from icy_semantic import extract_hard, extract_soft, accepts


def test_numbers():
    h = extract_hard("2+2 equals 4")
    assert "2" in h and "4" in h, f"numbers missing: {h}"


def test_paths():
    h = extract_hard("read D:/test/file.txt")
    assert any("file" in x for x in h), f"path missing: {h}"


def test_urls():
    h = extract_hard("fetch https://example.com/api")
    assert any("example" in x for x in h), f"url missing: {h}"


def test_proper_nouns():
    s = extract_soft("Weather in Montreal today")
    assert "montreal" in s, f"proper noun missing: {s}"


def test_grounding_reject():
    # Montrez → Montreal, mais Quebec City doit échouer
    assert accepts("weather in Montreal", {"anchors_hard": [], "anchors_soft": ["montreal"]}, 0.99)
    assert not accepts("weather in Montreal", {"anchors_hard": [], "anchors_soft": ["quebec", "city"]}, 0.99)
    assert not accepts("port 8080", {"anchors_hard": ["9090"], "anchors_soft": []}, 0.99)  # hard mismatch


if __name__ == "__main__":
    tests = [test_numbers, test_paths, test_urls, test_proper_nouns, test_grounding_reject]
    for t in tests:
        t()
        print(f"  {t.__name__}: PASS")
    print("=== all anchor tests passed ===")
