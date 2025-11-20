import re, statistics, datetime

logfile = "~/.config/srsran/enb_ctx.log"

re_conn = re.compile(r"\[(\d+\.\d+)\].*User (0x[0-9a-fA-F]+) connected")
re_disc = re.compile(r"\[(\d+\.\d+)\].*Disconnecting rnti=(0x[0-9a-fA-F]+)")

start = {}
durations = []

with open(logfile, "r") as f:
    for line in f:
        m1 = re_conn.search(line)
        if m1:
            t = float(m1.group(1))
            rnti = m1.group(2)
            start[rnti] = t
            continue
        m2 = re_disc.search(line)
        if m2:
            t = float(m2.group(1))
            rnti = m2.group(2)
            if rnti in start:
                dt = (t - start[rnti]) * 1000.0  # ms
                durations.append(dt)
                print(f"{rnti}: {dt:.1f} ms")
                del start[rnti]

print("\n총 UE 세션 개수:", len(durations))
if durations:
    print(f"평균: {statistics.mean(durations):.1f} ms")
    print(f"최소: {min(durations):.1f} ms,  최대: {max(durations):.1f} ms")
