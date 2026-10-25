#!/usr/bin/env python3
"""Warm‑up script for IcyCache.

Loads a set of common prompts (system messages & templates) from the Hermes
configuration and sends each one once to the IcyCache /api/complete endpoint.
This populates the cache at startup so the first real request is already a hit.
"""
import json, os, sys
from urllib import request

CONFIG_PATH = r"C:\\Users\\Tech\\AppData\\Local\\hermes\\config.yaml"
ICY_URL = "http://127.0.0.1:8080/api/complete"

def load_all_prompts():
    """Load every prompt from Hermes config and optional prompts.txt file."""
    prompts = set(load_prompts())
    txt_path = r'D:\IcyCache\prompts.txt'
    try:
        with open(txt_path, 'r', encoding='utf-8') as f:
            for line in f:
                line=line.strip()
                if line:
                    prompts.add(line)
    except FileNotFoundError:
        pass
    return list(prompts)

def load_prompts():
    """Load prompts from Hermes config (system/template lines)."""
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = f.read()
    except Exception as e:
        print(f"[warmup] cannot read Hermes config: {e}", file=sys.stderr)
        return []
    prompts = []
    for line in data.splitlines():
        l = line.lstrip()
        if l.startswith("system:") or l.startswith("template:"):
            _, txt = l.split(":", 1)
            prompts.append(txt.strip())
    # dedupe, limit to first 20
    return list(dict.fromkeys(prompts))[:20]

def send_prompt(prompt):
    payload = json.dumps({
        "model": "hermes-free",
        "provider": "icycache",
        "prompt": prompt,
        "max_tokens": 0,
        "temperature": 0,
        "stream": False
    }).encode("utf-8")
    req = request.Request(ICY_URL, data=payload,
                         headers={"Content-Type": "application/json"})
    try:
        with request.urlopen(req, timeout=5) as resp:
            resp.read()
    except Exception as e:
        print(f"[warmup] error for prompt '{prompt[:30]}…': {e}", file=sys.stderr)

def main():
    prompts = load_all_prompts()
    if not prompts:
        print("[warmup] No prompts found – nothing to seed.")
        return
    print(f"[warmup] Seeding {len(prompts)} prompts into IcyCache…")
    for p in prompts:
        send_prompt(p)
    print("[warmup] Done.")

if __name__ == "__main__":
    main()
