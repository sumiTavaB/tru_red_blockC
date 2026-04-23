import re
import sys
from pathlib import Path
import statistics as stats
from collections import defaultdict
import json

# ==========================================
# 1. LOG PARSING CONFIGURATION
# ==========================================

PATTERNS = {
    # --- UNIVERSAL LATENCY TRACKER (NEW) ---
    # Matches: [123.4] ⏱️ INIT | ID: abc1234 | Type: Tx
    'LATENCY_TRACE': re.compile(r'\[(\d+\.\d+)\] ⏱️ (INIT|COMMIT)\s+\|\s+ID:\s+([a-f0-9]+)\s+\|\s+Type:\s+(\S+)'),

    # --- BLOCKCHAIN EVENTS (RETAINED FOR FAIRNESS/TOPOLOGY) ---
    # [123.4] Authority 🔨 selected block 12 from V_EU
    'BLOCK_SELECTION': re.compile(r'\[(\d+\.\d+)\] Authority 🔨 selected block (\d+) from (\w+)'),

    # [123.4] Verifier V_EU ➜ proposing block 12
    'BLOCK_PROPOSAL': re.compile(r'\[(\d+\.\d+)\] (?:Verifier )?(\w+) [➜📦] (?:proposing|Proposed new) block (\d+)'),
}

def infer_sim_unit_seconds(config_path: str = "config.json") -> float:
    try:
        with open(config_path, 'r') as f:
            config = json.load(f)
        # Get the ms value (e.g., 200) and convert to seconds (0.2)
        denom_ms = config.get("simulation_parameters", {}).get("time_scaling_ms", 200.0)
        return float(denom_ms) / 1000.0
    except (FileNotFoundError, json.JSONDecodeError):
        print("⚠️ Warning: Could not read config.json. Defaulting to 0.2s scaling.")
        return 0.2

def parse_log_metrics(log_path: str):
    print(f"Reading log file: {log_path}...")
    
    data = {
        # New Universal Registry
        "tx_registry": defaultdict(dict), # {tx_id: {'init': t, 'commit': t, 'type': type}}
        
        # Retained Legacy Metrics
        "block_times": {},       # {block_idx: time}
        "selections": [],        # List of (time, verifier, block_idx)
        "proposals": {},         # {(verifier, block_idx): time}
        "verifier_counts": defaultdict(int),
        "total_time_units": 0.0,
    }

    try:
        with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()

        lines = content.splitlines()
        if not lines: return None

        # 1. Determine Simulation Duration
        start_time = None
        last_time = 0.0
        
        for line in lines:
            # Timestamp extraction for duration
            m_ts = re.match(r'\[(\d+\.\d+)\]', line)
            if m_ts:
                t = float(m_ts.group(1))
                if start_time is None: start_time = t
                last_time = t

            # 2. Universal Latency Parsing
            m_lat = PATTERNS['LATENCY_TRACE'].search(line)
            if m_lat:
                t_val, action, tx_id, tx_type = m_lat.groups()
                t_val = float(t_val)
                
                # Ensure dict exists
                if tx_id not in data["tx_registry"]:
                    data["tx_registry"][tx_id]['type'] = tx_type

                if action == "INIT":
                    # [FIX] Only record the FIRST INIT timestamp.
                    # The simulation logs INIT twice: once at creation, once at selection.
                    # We want creation time.
                    if 'init' not in data["tx_registry"][tx_id]:
                        data["tx_registry"][tx_id]['init'] = t_val
                        
                elif action == "COMMIT":
                    data["tx_registry"][tx_id]['commit'] = t_val
                    # [FIX] Update type from COMMIT if available. 
                    # (Fixes issue where sim logs admin txs as generic 'Tx' during INIT)
                    if tx_type and tx_type != "Tx":
                        data["tx_registry"][tx_id]['type'] = tx_type
                continue

            # 3. Block Consensus (Fairness)
            m_sel = PATTERNS['BLOCK_SELECTION'].search(line)
            if m_sel:
                t, idx, verifier = m_sel.groups()
                b_idx = int(idx)
                t_val = float(t)
                data["block_times"][b_idx] = t_val
                data["selections"].append((t_val, verifier, b_idx))
                data["verifier_counts"][verifier] += 1
                continue

            # 4. Topology (Propagation Delay)
            m_prop = PATTERNS['BLOCK_PROPOSAL'].search(line)
            if m_prop:
                t, verifier, idx = m_prop.groups()
                # Store the *first* time a verifier proposes a specific index
                key = (verifier, int(idx))
                if key not in data["proposals"]:
                    data["proposals"][key] = float(t)
                continue

        if start_time is not None:
            data["total_time_units"] = last_time - start_time

    except FileNotFoundError:
        print(f"Error: File {log_path} not found.")
        return None
    
    return data

# ==========================================
# 2. METRIC CALCULATIONS
# ==========================================

def calculate_jain_fairness(counts_dict):
    if not counts_dict: return 0
    n = len(counts_dict)
    allocations = list(counts_dict.values())
    sum_x = sum(allocations)
    sum_x_sq = sum(x**2 for x in allocations)
    if sum_x_sq == 0: return 0
    return (sum_x ** 2) / (n * sum_x_sq)

def analyze_performance(data, sim_unit_sec):
    if not data: return

    print("\n" + "="*60)
    print("📊 METRICS FOR REPUTED JOURNAL SUBMISSION")
    print(f"   (Scaled: 1 SimPy Unit = {sim_unit_sec*1000:.0f} ms)")
    print("="*60)

    # --- Pre-Calculate Latencies ---
    all_latencies = []
    by_type = defaultdict(list)
    committed_count = 0

    for tx_id, info in data["tx_registry"].items():
        t_start = info.get('init')
        t_end   = info.get('commit')
        t_type  = info.get('type', 'Unknown')

        if t_end:
            committed_count += 1
        
        if t_start is not None and t_end is not None:
            lat = (t_end - t_start) * sim_unit_sec
            if lat >= 0:
                all_latencies.append(lat)
                by_type[t_type].append(lat)

    # --- 1. THROUGHPUT ---
    duration_units = data["total_time_units"]
    duration_sec = duration_units * sim_unit_sec
    tps = committed_count / duration_sec if duration_sec > 0 else 0
    
    print(f"\n1. THROUGHPUT (Network Capacity)")
    print(f"   ➤ Sim Duration          : {duration_units:.2f} units ({duration_sec:.2f} s)")
    print(f"   ➤ Total Committed Txs   : {committed_count}")
    print(f"   ➤ Avg Throughput (TPS)  : {tps:.4f} Tx/s")

    # --- 2. TRANSACTION LATENCY (DETAILED BREAKDOWN) ---
    print(f"\n2. END-TO-END LATENCY (Breakdown by Type)")
    
    if all_latencies:
        # Global Stats
        print(f"   A. Global Statistics (All Types)")
        print(f"      ➤ Avg Latency       : {stats.mean(all_latencies):.4f} s")
        print(f"      ➤ Min / Max         : {min(all_latencies):.4f} / {max(all_latencies):.4f} s")

        # Per-Type Stats
        print(f"\n   B. Specific Transaction Types")
        print(f"      {'TYPE':<25} | {'COUNT':<6} | {'AVG (s)':<10} | {'MIN / MAX (s)'}")
        print("      " + "-"*65)
        
        for t_type in sorted(by_type.keys()):
            lats = by_type[t_type]
            avg = stats.mean(lats)
            mn, mx = min(lats), max(lats)
            print(f"      {t_type:<25} | {len(lats):<6} | {avg:<10.4f} | {mn:.3f} / {mx:.3f}")
        
        print("      " + "-"*65)
        print("      * Tx = User Transfer | Tx_admin_DEL = Redaction | Tx_admin_ADD = Registration")
    else:
        print("   ➤ No complete transaction traces found (check logs for ⏱️ entries).")

    # --- 3. TOPOLOGY ---
    # Calculates the delay between a Verifier Proposing a block and the Authority Selecting it
    prop_times = []
    for sel_time, verifier, idx in data["selections"]:
        key = (verifier, idx)
        if key in data["proposals"]:
            prop_delay_units = sel_time - data["proposals"][key]
            prop_times.append(prop_delay_units * sim_unit_sec)
    
    print(f"\n3. NETWORK TOPOLOGY PERFORMANCE")
    if prop_times:
        avg_prop = stats.mean(prop_times)
        print(f"   ➤ Avg Block Prop. Delay : {avg_prop:.4f} s")
    else:
        print("   ➤ N/A (Could not link Proposals to Selections)")

    # --- 4. FAIRNESS ---
    print(f"\n4. CONSENSUS FAIRNESS (Jain's Index)")
    total_blocks = len(data["selections"])
    jain = calculate_jain_fairness(data["verifier_counts"])
    
    print(f"   ➤ Total Blocks Finalized: {total_blocks}")
    print(f"   ➤ Fairness Index        : {jain:.4f} ({jain*100:.2f}%)")
    print("   ➤ Distribution:")
    for v, count in sorted(data["verifier_counts"].items(), key=lambda item: item[1], reverse=True):
        print(f"      - {v:<5}: {count} blocks ({(count/total_blocks*100):.1f}%)")

    print("="*60 + "\n")

if __name__ == "__main__":
    log_file = "simulation_output.txt"
    # code_file = "redactionsimulation.py"
    
    sim_unit_sec = infer_sim_unit_seconds("config.json")
    parsed_data = parse_log_metrics(log_file)
    analyze_performance(parsed_data, sim_unit_sec)