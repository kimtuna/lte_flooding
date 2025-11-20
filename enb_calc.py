import re, statistics

logfile = "/home/parklab/.config/srsran/enb_ctx.log"   # enb_calc.py랑 같은 폴더에 있다고 가정

# [1763659271] 또는 [1763659271.123] 둘 다 잡기
ts_re = re.compile(r"\[(\d+(?:\.\d+)?)\]")

# 시작 이벤트: RACH에서 temp_crnti 사용
re_conn_rach = re.compile(r"RACH:.*temp_crnti=(0x[0-9a-fA-F]+)")

# (예전 버전에서 썼던) User connected 패턴도 같이 지원하고 싶으면:
re_conn_user = re.compile(r"User (0x[0-9a-fA-F]+) connected")

# 종료 이벤트
re_disc = re.compile(r"Disconnecting rnti=(0x[0-9a-fA-F]+)")

start = {}
durations = []

with open(logfile, "r") as f:
    for line in f:
        ts_m = ts_re.search(line)
        if not ts_m:
            continue
        t = float(ts_m.group(1))

        # 1) User 0x.. connected 패턴 (있으면 이걸 우선 사용)
        m_user = re_conn_user.search(line)
        if m_user:
            rnti = m_user.group(1)
            start[rnti] = t
            continue

        # 2) RACH: ... temp_crnti=0x.. 패턴
        m_rach = re_conn_rach.search(line)
        if m_rach:
            rnti = m_rach.group(1)
            # 이미 start에 있으면 덮어쓰지 않음
            start.setdefault(rnti, t)
            continue

        # 3) Disconnecting rnti=0x..
        m_disc = re_disc.search(line)
        if m_disc:
            rnti = m_disc.group(1)
            if rnti in start:
                dt = (t - start[rnti]) * 1000.0  # ms
                durations.append(dt)
                print(f"{rnti}: {dt:.1f} ms")
                del start[rnti]

print("\n총 UE 세션 개수:", len(durations))
if durations:
    print(f"평균: {statistics.mean(durations):.1f} ms")
    print(f"최소: {min(durations):.1f} ms,  최대: {max(durations):.1f} ms")
