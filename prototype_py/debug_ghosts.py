import re

LOG_FILE = "simulation_output.txt"

# Regex to capture the events
commit_pattern = re.compile(r'COMMIT\s+\|\s+ID:\s+([a-f0-9]+)\s+\|\s+Type:\s+(\S+)')
init_pattern   = re.compile(r'INIT\s+\|\s+ID:\s+([a-f0-9]+)')

inits = set()
commits = []

print(f"Scanning {LOG_FILE}...")

try:
    with open(LOG_FILE, 'r', encoding='utf-8') as f:
        for line in f:
            if "⏱️" not in line: continue
            
            # Capture INITs
            m_init = init_pattern.search(line)
            if m_init:
                inits.add(m_init.group(1))
                continue
                
            # Capture COMMITs
            m_commit = commit_pattern.search(line)
            if m_commit:
                tx_id, tx_type = m_commit.groups()
                commits.append((tx_id, tx_type))

    missing = []
    for tx_id, tx_type in commits:
        if tx_id not in inits:
            missing.append((tx_id, tx_type))

    print(f"Total Commits: {len(commits)}")
    print(f"Total Inits:   {len(inits)}")
    print(f"Missing Inits: {len(missing)}")

    if missing:
        print("\n--- SAMPLE MISSING TRANSACTIONS (First 10) ---")
        print(f"{'ID (First 12 chars)':<20} | {'TYPE':<20}")
        print("-" * 45)
        for i in range(min(10, len(missing))):
            print(f"{missing[i][0][:12]}... | {missing[i][1]}")
            
        print("\n🔎 HINT: Search your code for where this 'TYPE' is created.")
        print("   If the Type is 'Tx', check if you pre-populate the mempool before the loop starts.")
        print("   If the Type is 'Reward' or 'Fee', check AuthorityNode.select_block().")
    else:
        print("\n✅ Data Integrity Perfect! No missing logs.")

except FileNotFoundError:
    print(f"❌ Error: Could not find {LOG_FILE}")