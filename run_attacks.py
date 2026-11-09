#!/usr/bin/env python3
"""Send every scenario in attacks/attacks.json to an n8n chat webhook and record the results.

Usage:
  python3 run_attacks.py --url https://<your-n8n>/webhook/<webhook-id>/chat --label vulnerable
  python3 run_attacks.py --url https://<your-n8n>/webhook/<webhook-id>/chat --label hardened

Each scenario runs in a fresh chat session. Results are written to results/<label>.json and results/<label>.md.
"""
import argparse, json, os, sys, time, urllib.request, uuid

HERE = os.path.dirname(os.path.abspath(__file__))


def send(url, prompt, timeout):
    body = json.dumps({"action": "sendMessage", "sessionId": str(uuid.uuid4()), "chatInput": prompt}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode() or "{}")
    if isinstance(data, list):
        data = data[0] if data else {}
    return data


def matches(rules, output, steps):
    for rule in rules:
        hay = output if rule["type"] == "output_contains" else steps
        if rule["value"].lower() in hay.lower():
            return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True, help="n8n chat webhook URL (ends with /chat)")
    ap.add_argument("--label", required=True, help="e.g. vulnerable or hardened")
    ap.add_argument("--timeout", type=int, default=120)
    args = ap.parse_args()

    scenarios = json.load(open(os.path.join(HERE, "attacks", "attacks.json")))
    results = []
    for s in scenarios:
        print(f"[{s['id']}] {s['title']} ... ", end="", flush=True)
        try:
            data = send(args.url, s["prompt"], args.timeout)
            output = str(data.get("output", ""))
            steps = json.dumps(data.get("intermediateSteps", []), ensure_ascii=False)
            if "compromised_if" in s:
                verdict = "COMPROMISED" if matches(s["compromised_if"], output, steps) else "DEFENDED"
            else:
                verdict = "WORKS" if matches(s["expected_if"], output, steps) else "BROKEN"
        except Exception as e:  # network or workflow error
            output, steps, verdict = f"ERROR: {e}", "[]", "ERROR"
        tools = [st.get("action", {}).get("tool") for st in json.loads(steps)] if steps.startswith("[") else []
        print(verdict)
        results.append({**{k: s[k] for k in ("id", "title", "owasp", "prompt")}, "verdict": verdict,
                        "tools_called": tools, "output": output})
        time.sleep(1)

    os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
    with open(os.path.join(HERE, "results", f"{args.label}.json"), "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    with open(os.path.join(HERE, "results", f"{args.label}.md"), "w") as f:
        f.write(f"# Results: {args.label}\n\nRun on {time.strftime('%Y-%m-%d %H:%M')}.\n\n")
        f.write("| ID | Scenario | OWASP | Verdict | Tools called |\n|---|---|---|---|---|\n")
        for r in results:
            f.write(f"| {r['id']} | {r['title']} | {r['owasp']} | **{r['verdict']}** | {', '.join(t for t in r['tools_called'] if t) or '-'} |\n")
        for r in results:
            f.write(f"\n## {r['id']}: {r['title']}\n\n**Prompt:** {r['prompt']}\n\n**Response:**\n\n> {r['output'].replace(chr(10), chr(10) + '> ')}\n")
    print(f"\nSaved results/{args.label}.md and results/{args.label}.json")


if __name__ == "__main__":
    sys.exit(main())
