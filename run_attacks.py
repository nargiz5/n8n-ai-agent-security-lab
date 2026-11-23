#!/usr/bin/env python3
"""Send every scenario in attacks/attacks.json to an n8n chat webhook and record the results.

Usage:
  python3 run_attacks.py --url https://<your-n8n>/webhook/<webhook-id>/chat --label vulnerable
  python3 run_attacks.py --url https://<your-n8n>/webhook/<webhook-id>/chat --label hardened --runs 3

Each scenario runs in a fresh chat session. Results are written to results/<label>.json and
results/<label>.md after every scenario, so an interrupted run keeps what it has measured.
"""
import argparse, json, os, re, sys, time, urllib.request, uuid

HERE = os.path.dirname(os.path.abspath(__file__))


def send(url, prompt, timeout):
    body = json.dumps({"action": "sendMessage", "sessionId": str(uuid.uuid4()), "chatInput": prompt}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8") or "{}")
    if isinstance(data, list):
        data = data[0] if data else {}
    return data if isinstance(data, dict) else {"output": str(data)}


def normalise(text):
    """Lower-case and drop everything except letters and digits."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


def matches(rules, output, steps):
    for rule in rules:
        hay = output if rule["type"] == "output_contains" else steps
        if rule["value"].lower() in hay.lower():
            return True
        # Model text is also compared after normalisation, so trivial reformatting
        # ("CANARY 7F3A", "4,100", a rule re-wrapped across lines) is still caught.
        # Tool steps are exact strings produced by code and are matched as-is.
        if rule["type"] == "output_contains" and normalise(rule["value"]) in normalise(hay):
            return True
    return False


def tools_called(steps):
    """Tool names from n8n intermediate steps, ignoring entries of an unexpected shape."""
    names = []
    for step in steps if isinstance(steps, list) else []:
        action = step.get("action") if isinstance(step, dict) else None
        tool = action.get("tool") if isinstance(action, dict) else None
        if tool:
            names.append(str(tool))
    return names


def evaluate(scenario, data):
    """Verdict for one scenario, given the webhook response."""
    output = str(data.get("output", ""))
    raw_steps = data.get("intermediateSteps", [])
    steps = json.dumps(raw_steps, ensure_ascii=False, default=str)
    if "compromised_if" in scenario:
        verdict = "COMPROMISED" if matches(scenario["compromised_if"], output, steps) else "DEFENDED"
    else:
        verdict = "WORKS" if matches(scenario["expected_if"], output, steps) else "BROKEN"
    return verdict, output, tools_called(raw_steps)


def summarise(results):
    """Per scenario: how often each verdict occurred across runs, in first-seen order."""
    summary = {}
    for r in results:
        entry = summary.setdefault(r["id"], {"title": r["title"], "owasp": r["owasp"], "verdicts": {}})
        entry["verdicts"][r["verdict"]] = entry["verdicts"].get(r["verdict"], 0) + 1
    return summary


def write_reports(results, label, runs, out_dir, started):
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, f"{label}.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    with open(os.path.join(out_dir, f"{label}.md"), "w", encoding="utf-8") as f:
        f.write(f"# Results: {label}\n\nRun on {started}, {runs} run(s) per scenario.\n\n")
        f.write("| ID | Scenario | OWASP | Verdicts |\n|---|---|---|---|\n")
        for sid, entry in summarise(results).items():
            counts = ", ".join(f"**{v}** {n}/{sum(entry['verdicts'].values())}" for v, n in entry["verdicts"].items())
            f.write(f"| {sid} | {entry['title']} | {entry['owasp']} | {counts} |\n")
        for r in results:
            run = f" (run {r['run']})" if runs > 1 else ""
            tools = ", ".join(r["tools_called"]) or "-"
            reply = r["output"].replace("\n", "\n> ")
            f.write(f"\n## {r['id']}{run}: {r['title']}\n\n**Verdict:** {r['verdict']}  \n"
                    f"**Tools called:** {tools}\n\n**Prompt:** {r['prompt']}\n\n**Response:**\n\n> {reply}\n")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True, help="n8n chat webhook URL (ends with /chat)")
    ap.add_argument("--label", required=True, help="e.g. vulnerable or hardened")
    ap.add_argument("--runs", type=int, default=1, help="how many times to run each scenario (default 1)")
    ap.add_argument("--timeout", type=int, default=120, help="seconds to wait for each response")
    ap.add_argument("--delay", type=float, default=1.0, help="seconds to wait between requests")
    ap.add_argument("--scenarios", default=os.path.join(HERE, "attacks", "attacks.json"))
    ap.add_argument("--out", default=os.path.join(HERE, "results"), help="directory for the reports")
    args = ap.parse_args(argv)
    if args.runs < 1:
        ap.error("--runs must be at least 1")

    with open(args.scenarios, encoding="utf-8") as f:
        scenarios = json.load(f)
    started = time.strftime("%Y-%m-%d %H:%M")
    results = []
    for run in range(1, args.runs + 1):
        for s in scenarios:
            prefix = f"[run {run}] " if args.runs > 1 else ""
            print(f"{prefix}[{s['id']}] {s['title']} ... ", end="", flush=True)
            try:
                verdict, output, tools = evaluate(s, send(args.url, s["prompt"], args.timeout))
            except Exception as e:  # network, workflow or response-format error
                verdict, output, tools = "ERROR", f"ERROR: {e}", []
            print(verdict)
            results.append({**{k: s[k] for k in ("id", "title", "owasp", "prompt")}, "run": run,
                            "verdict": verdict, "tools_called": tools, "output": output})
            # Save after every scenario, so an interrupted run keeps its results.
            write_reports(results, args.label, args.runs, args.out, started)
            time.sleep(args.delay)

    print(f"\nSaved {os.path.join(args.out, args.label)}.md and .json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
