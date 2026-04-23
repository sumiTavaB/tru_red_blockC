import sys
import json
import ast
import hashlib
from collections import defaultdict

# ==========================================
# CONFIGURATION
# ==========================================
LOG_FILE = "simulation_output.txt"
START_MARKER = "=== Authority’s Full Blockchain ==="

def calculate_tx_id(tx_dict):
    """
    Replicates the simulation's ID generation logic.
    Hashes the sorted JSON content of the transaction.
    """
    # Create a copy to ensure we don't modify the original during iteration
    clean_tx = tx_dict.copy()
    
    # 1. Remove fields that shouldn't be hashed (if any exist in your specific implementation)
    # Based on your logs, the simulation hashes the entire dictionary as-is.
    # If your simulation excludes signatures from the hash, add: clean_tx.pop('signature', None)
    
    # 2. Serialize and Hash
    # sort_keys=True is CRITICAL. It ensures {"a":1, "b":2} hashes the same as {"b":2, "a":1}
    tx_str = json.dumps(clean_tx, sort_keys=True)
    return hashlib.sha256(tx_str.encode()).hexdigest()

def get_searchable_snippet(tx_dict):
    """Returns a string snippet from the tx to help find it in the text file."""
    # Try to find unique-ish fields
    if "nonce" in tx_dict and "amount" in tx_dict:
        return f'"nonce": {tx_dict["nonce"]}, "amount": {tx_dict["amount"]}, "asset": "{tx_dict.get("asset_type","?")}"'
    elif "op" in tx_dict:
        return f'"op": "{tx_dict["op"]}", "target": "{tx_dict.get("target_label", "?")}"'
    else:
        # Fallback to a chunk of the PKS
        pks = tx_dict.get("PKS", "")
        return f'"PKS": "{pks[:20]}..."'

def audit_final_chain(file_path):
    print(f"🔍 Auditing Final Blockchain State in: {file_path}")
    print(f"   Target Section: '{START_MARKER}'")
    print("=" * 60)

    raw_data = ""
    found_marker = False

    # 1. EXTRACT DATA
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                if not found_marker:
                    if START_MARKER in line:
                        found_marker = True
                        print("✅ Found Start Marker. Reading chain dump...")
                    continue
                raw_data += line
    except FileNotFoundError:
        print(f"❌ Error: File '{file_path}' not found.")
        sys.exit(1)

    if not found_marker or not raw_data.strip():
        print("❌ Error: Marker not found or no data.")
        sys.exit(1)

    # 2. PARSE JSON
    try:
        blockchain = json.loads(raw_data)
        print(f"✅ Parsed {len(blockchain)} blocks.")
    except json.JSONDecodeError:
        try:
            blockchain = ast.literal_eval(raw_data)
            print(f"✅ Parsed {len(blockchain)} blocks (via eval).")
        except:
            print("❌ Error: Could not parse blockchain data.")
            sys.exit(1)

    # 3. DETECT DUPLICATES
    # Maps tx_id -> list of {block_index, snippet}
    tx_tracker = defaultdict(list)
    
    in_block_dupes = []
    global_dupes = []

    for block in blockchain:
        b_idx = block.get('index', 'Unknown')
        txs = block.get('txs', [])
        
        # Track IDs local to this block (for in-block dupe detection)
        block_local_ids = set()

        for tx in txs:
            # CALCULATE ID
            tid = calculate_tx_id(tx)
            snippet = get_searchable_snippet(tx)

            # A. Check In-Block Duplicate
            if tid in block_local_ids:
                in_block_dupes.append({
                    "id": tid, 
                    "block": b_idx, 
                    "snippet": snippet
                })
            block_local_ids.add(tid)

            # B. Track Global History
            tx_tracker[tid].append(b_idx)

    # C. Check Global Duplicates
    for tid, block_indices in tx_tracker.items():
        unique_blocks = sorted(list(set(block_indices)))
        if len(unique_blocks) > 1:
            # Get the snippet from the first occurrence (doesn't matter which, they are identical)
            # We need to find the transaction object again or store it. 
            # For efficiency, we just re-construct the snippet based on lookup not implemented here to save memory,
            # but we can grab it if we iterate again. 
            # Ideally, we stored it in the tracker. Let's update tracker to store simple tuples.
            pass

    # Refined Global Check
    for tid, block_indices in tx_tracker.items():
        unique_blocks = sorted(list(set(block_indices)))
        if len(unique_blocks) > 1:
             global_dupes.append({
                "id": tid,
                "blocks": unique_blocks
            })

    # 4. REPORTING
    print("-" * 60)
    
    if in_block_dupes:
        print(f"❌ CRITICAL FAIL: {len(in_block_dupes)} IN-BLOCK DUPLICATES")
        print("   (These transactions appear multiple times inside the SAME block)")
        print(f"   {'TX HASH (Search this in LOG)':<66} | {'BLOCK':<5} | {'SNIPPET (Search this in DUMP)'}")
        print("   " + "-"*110)
        for item in in_block_dupes[:10]:
            print(f"   {item['id']} | {item['block']:<5} | {item['snippet']}")
        if len(in_block_dupes) > 10: print(f"   ... and {len(in_block_dupes)-10} more.")
    else:
        print("✅ In-Block Integrity: PASS")

    print("-" * 60)

    if global_dupes:
        print(f"❌ CRITICAL FAIL: {len(global_dupes)} GLOBAL DUPLICATES")
        print("   (These transactions appear in MULTIPLE different blocks)")
        print(f"   {'TX HASH (Search this in LOG)':<66} | {'BLOCKS'}")
        print("   " + "-"*110)
        for item in global_dupes[:10]:
            print(f"   {item['id']} | {item['blocks']}")
        if len(global_dupes) > 10: print(f"   ... and {len(global_dupes)-10} more.")
    else:
        print("✅ Global Integrity:   PASS")

    print("=" * 60)

if __name__ == "__main__":
    audit_final_chain(LOG_FILE)