import json
import os
import sys
import time


def main() -> None:
    if len(sys.argv) > 1:
        raw_input = " ".join(sys.argv[1:])
    else:
        raw_input = os.getenv("AGENT_INPUT", "no-input")

    delay_before = float(os.getenv("AGENT_DELAY_BEFORE_OUTPUT", "0"))
    if delay_before > 0:
        time.sleep(delay_before)

    result = f"[dummy-agent] received: {raw_input}"

    # Single JSON line for easy parsing
    print(json.dumps({"output": result}), flush=True)

    hold_after = float(os.getenv("AGENT_HOLD_SECONDS", "0"))
    if hold_after > 0:
        time.sleep(hold_after)


if __name__ == "__main__":
    main()
