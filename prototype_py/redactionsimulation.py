import simpy
import random
import pandas as pd
import networktopology  # visualize after simulation
import json
import sys
import hashlib
import base64
import re
import os
import math

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization, hashes, hmac
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, PrivateFormat, NoEncryption
from cryptography.exceptions import InvalidSignature

try:
    with open('config.json', 'r') as f:
        config = json.load(f)
except FileNotFoundError:
    print("FATAL ERROR: config.json not found. Please create it.")
    sys.exit(1)
except json.JSONDecodeError:
    print("FATAL ERROR: config.json is not a valid JSON file.")
    sys.exit(1) 

# --- Helpers ---

def anonymize_identity(pk_bytes: bytes, seed: bytes) -> str:
    """
    Implements X(PK, seed) from the paper.
    Returns a hex string representing the pseudonym.
    """
    h = hmac.HMAC(seed, hashes.SHA256())
    h.update(pk_bytes)
    return base64.b64encode(h.finalize()).decode()

def short_id(anon_id: str) -> str:
    """Helper to print short anonymous ID for logs"""
    if not anon_id: return "UNKNOWN"
    return anon_id[:8] + "..."

def stable_tx_id(tx: dict) -> str:
    return hashlib.sha256(json.dumps(tx, sort_keys=True).encode()).hexdigest()

def stable_block_id(block: dict) -> str:
    return hashlib.sha256(json.dumps(block, sort_keys=True).encode()).hexdigest()

def stable_msg_id(msg: dict) -> str:
    return hashlib.sha256(json.dumps(msg, sort_keys=True).encode()).hexdigest()

def compute_merkle_root(leaves):
    level = [hashlib.sha256(leaf.encode()).hexdigest() for leaf in leaves]
    if not level:
        return hashlib.sha256(b"").hexdigest()
    while len(level) > 1:
        if len(level) % 2 == 1:
            level.append(level[-1])
        next_level = []
        for i in range(0, len(level), 2):
            combined = level[i] + level[i+1]
            next_level.append(hashlib.sha256(combined.encode()).hexdigest())
        level = next_level
    return level[0]

def broadcast_to_peers(env, sender_node, message):
    """
    Simulates a network broadcast from a node to its direct physical neighbors.
    Calculates specific latency for each link.
    """
    # 1. Generate a stable ID for deduplication
    if message.get("type") == "Block":
        # Blocks usually use a specific hash format in this simulation
        msg_hash = stable_block_id({"block": message["block"]})
    else:
        msg_hash = stable_msg_id(message)

    # 2. Mark as seen by sender to prevent immediate echo-back
    if hasattr(sender_node, 'seen_messages'):
        sender_node.seen_messages.add(msg_hash)

    # 3. Send to direct peers only (No jumping topology)
    for peer in sender_node.peers:
        delay = get_delay(sender_node.region, peer.region)
        env.process(peer.receive_message(message, msg_hash, delay))

# --- Config & Latency ---

ROUNDS = config["simulation_parameters"]["rounds"]
LOG_FILE = config["simulation_parameters"]["log_file"]
TIMEOUT_MIN_AUTH = config["consensus_parameters"]["authority_timeout_range"][0]
TIMEOUT_MAX_AUTH = config["consensus_parameters"]["authority_timeout_range"][1]

LATENCY_XLSX = config["network_setup"]["latency_file_path"]
try:
    df = pd.read_excel(LATENCY_XLSX, engine='openpyxl')
except Exception as e:
    print(f"FATAL: Could not load latency file: {e}")
    sys.exit(1)

# Create the Latency Map
long_df = df.melt(id_vars=['Source'], var_name='Target', value_name='RTT_ms')
latency_map = {
    (str(r.Source).strip(), str(r.Target).strip()): float(r.RTT_ms)
    for _, r in long_df.dropna().iterrows()
}

# 1. Extract all available valid regions from the Excel file
ALL_EXCEL_REGIONS = list(set(df['Source'].dropna().astype(str).str.strip().tolist()))

REGION_KEYWORDS = {
    "AU": ["Australia"],
    "IN": ["India"],
    "US": ["US", "Central US", "East US", "West US"],
    "NA": ["Canada", "US"], # North America fallback
    "SA": ["Brazil", "South America"],
    "AF": ["Africa"],
    "EU": ["Europe", "France", "Germany", "Norway", "Switzerland", "Poland", "Sweden", "Italy", "UK"],
    "UK": ["UK"],
    "AS": ["Asia", "Japan", "Korea", "China", "India", "Singapore"], # General Asia
    "JP": ["Japan"],
    "KR": ["Korea"],
    "CN": ["China", "East Asia"],
    "ME": ["UAE", "Qatar", "Israel"] # Middle East
}

def get_region_for_label(label):
    label = label.upper()
    candidate_regions = []
    
    for prefix, keywords in REGION_KEYWORDS.items():
        if label.startswith(prefix) or f"_{prefix}" in label or f"{prefix}_" in label:
            for region in ALL_EXCEL_REGIONS:
                for kw in keywords:
                    if kw.upper() in region.upper():
                        candidate_regions.append(region)
            break 
    
    if candidate_regions:
        return random.choice(candidate_regions)
    return random.choice(ALL_EXCEL_REGIONS)

def get_delay(r1, r2, default=300):
    # Lookup tuple (r1, r2) or (r2, r1)
    rtt = latency_map.get((r1, r2), latency_map.get((r2, r1), default))
    # Load scaling factor from config, default to 200 if missing
    scale = config["simulation_parameters"].get("time_scaling_ms", 200.0)
    
    return rtt / float(scale)

# --- Classes ---

class PublicParticipant:
    def __init__(self, env, label, region, authority, verifiers, asset_types, config):
        self.env = env
        self.label = label.upper()
        self.region = region
        self.authority = authority
        self.verifiers = verifiers
        self.config = config
        self.asset_types = asset_types
        
        # --- Network & Topology State ---
        self.peers = []
        self.is_gateway = False
        self.seen_messages = set()
        self.seen_signed = set()
        self.propagated_blocks = set()
        self.blockchain = []
        
        # --- Transaction State ---
        self.active = True  # Controls the main behavior loop
        self.simulation_finished = False 
        self.keys = {}      # {asset: {sk, pk_bytes}}
        self.balances = {}
        self.s1_shares = {}       # User's secret half
        self.anonymized_ids = {}  # Public Identity X(PK)
        self.nonces = {a: 0 for a in asset_types}
        
        # Locks & Events
        self.tx_locks = {a: False for a in asset_types}
        self.last_tx_refs = {a: None for a in asset_types}
        self.token_events = {a: self.env.event() for a in self.asset_types}
        self.signed_tokens = {a: None for a in self.asset_types}

        # --- Persistence for Re-joining ---
        self.saved_credentials = {} 

        # Initialize Identity & Register
        self._init_identities()
        
        # Start Life Cycle
        self.env.process(self.behavior())

    def _init_identities(self):
        """Generates keys/seeds and registers with Authority."""
        for asset in self.asset_types:
            # 1. Generate Crypto Material
            sk = ec.generate_private_key(ec.SECP256R1())
            pk = sk.public_key()
            pk_bytes = pk.public_bytes(encoding=Encoding.DER, format=PublicFormat.SubjectPublicKeyInfo)
            
            # Master Seed Generation
            master_seed = os.urandom(32)
            s1 = master_seed[:16] 
            s2 = master_seed[16:]
            anon_id = anonymize_identity(pk_bytes, master_seed)
            
            # 2. Store in Memory (Active State)
            self.keys[asset] = {"sk": sk, "pk_bytes": pk_bytes}
            self.s1_shares[asset] = s1
            self.anonymized_ids[asset] = anon_id
            
            # 3. Persist Credentials (simulating offline storage)
            self.saved_credentials[asset] = {
                "sk": sk, 
                "pk_bytes": pk_bytes, 
                "master_seed": master_seed,
                "s1": s1, 
                "s2": s2, 
                "anon_id": anon_id
            }

            # 4. Initial Balance & Registration
            balance_range = self.config["economic_parameters"]["public_balance_range"]
            self.balances[asset] = random.randint(*balance_range)
            
            # Register with Authority (sends S2)
            self.authority.register_node(
                self.label, pk_bytes, self.region, [asset],
                share_s2=s2, canon_id=anon_id
            )
        
        # Set default balance view
        self.balance = self.balances[self.asset_types[0]]

    def get_balance(self):
        return self.balance

    def behavior(self):
        # Configuration for Simulation behavior
        PROB_REDACT = 0.1   # 10% chance a node will request redaction
        PROB_REJOIN = 0.3   # 30% chance a redacted node will return
        
        will_redact = (random.random() < PROB_REDACT)
        # Random time to leave (between 20% and 80% of sim duration)
        redact_time = random.uniform(ROUNDS * 0.2, ROUNDS * 0.8) if will_redact else 999999
        
        while True: # Outer loop to handle re-activation
            if self.simulation_finished:
                break
            if not self.active:
                if will_redact and (random.random() < PROB_REJOIN):
                    wait_time = random.uniform(10, 30)
                    yield self.env.timeout(wait_time)
                    if self.simulation_finished: break # Check again after wait
                    yield from self.rejoin_network()
                else:
                    break 

            while self.active:
                if self.simulation_finished:
                    self.active = False
                    break
                # 1. Sleep briefly between txs
                yield self.env.timeout(random.uniform(1, 5))
                
                # Check 1: Did we get stopped while sleeping?
                if not self.active or self.simulation_finished:
                    break
                
                # 2. Try to transact
                yield from self.transactionRequest()
                
                # Check 2: Did we get stopped (or flagged inactive) during the transaction attempt?
                if not self.active or self.simulation_finished:
                    break
                
                # 3. Check Redaction Trigger
                if will_redact and self.env.now > redact_time:
                    # Requirement: Must have produced at least one transaction
                    has_transacted = any(self.nonces[a] > 0 for a in self.asset_types)
                    
                    if has_transacted:
                        yield from self.request_redaction()
                        break
                    else:
                        pass
            
            if not self.active and not will_redact:
                break # Safety break

    def request_redaction(self):
        asset = self.asset_types[0] # Redact primary identity
        anon_id = self.anonymized_ids[asset]
        sk = self.keys[asset]["sk"]
        
        print(f"[{self.env.now:.1f}] 🛑 {self.label} initiating REDACTION request for {short_id(anon_id)}...")
        
        # Payload construction
        req = {"type": "RedactReq", "PKS": anon_id}
        data = json.dumps(req, sort_keys=True).encode()
        sig = sk.sign(data, ec.ECDSA(hashes.SHA256()))
        
        msg = {
            "type": "RedactReq", 
            "PKS": anon_id, 
            "signature": base64.b64encode(sig).decode()
        }
        
        # Propagate to peers (Standard P2P routing)
        yield from self.propagate_message(msg, stable_msg_id(msg))

    def rejoin_network(self):
        print(f"[{self.env.now:.1f}] 🔄 {self.label} RE-JOINING network with restored secrets...")
        yield self.env.timeout(random.uniform(1, 3))
        
        for asset in self.asset_types:
            creds = self.saved_credentials[asset]
            
            # 1. Restore Memory State from Persistence
            self.keys[asset] = {"sk": creds["sk"], "pk_bytes": creds["pk_bytes"]}
            self.s1_shares[asset] = creds["s1"]
            self.anonymized_ids[asset] = creds["anon_id"]
            
            # 2. Re-Register with Authority
            self.authority.register_node(
                self.label, 
                creds["pk_bytes"], 
                self.region, 
                [asset],
                share_s2=creds["s2"],     # RESTORED SECRET
                canon_id=creds["anon_id"] # RESTORED ID
            )
        
        self.active = True
        print(f"[{self.env.now:.1f}] ✅ {self.label} is active again.")
        

    def transactionRequest(self, receiver=None, asset=None, timeout1=TIMEOUT_MIN_AUTH, timeout2=TIMEOUT_MAX_AUTH):
        if not self.active: return
        env = self.env

        # Step 1: Select asset & peer
        if asset is None:
            free_assets = [a for a in self.asset_types if not self.tx_locks[a]]
            if not free_assets: return 
            a = random.choice(free_assets)
        else:
            a = asset

        if receiver is None:
            candidates = [p for p in all_publics if p.label != self.label]
            if not candidates: return
            receiver_node = random.choice(candidates)
        else:
            receiver_node = receiver

        # Step 2: Construct RToken
        rtoken = {
            "type": "RToken",
            "from": self.label,
            "PKS": self.anonymized_ids[a], 
            "PKR": receiver_node.anonymized_ids[a], 
            "asset_type": a,
            "origin": self.region,
            "toAuth": self.authority.label
        }
        data = json.dumps(rtoken, sort_keys=True).encode()
        sig  = self.keys[a]["sk"].sign(data, ec.ECDSA(hashes.SHA256()))
        rtoken["signature"] = base64.b64encode(sig).decode()
        
        # [LOG] RToken Generation
        print(f"[{self.env.now:.1f}] 🎟️ {self.label} Generated RToken request for {a} -> Target: {receiver_node.label}")

        yield from self.propagate_message(rtoken, stable_msg_id(rtoken))

        # Wait for SignedToken
        yield self.token_events[a] 
        self.token_events[a] = self.env.event() # reset
        
        token = self.signed_tokens[a]
        if not token: return

        # --- Generate Proof 1 (P1) for Tx ---
        s1 = self.s1_shares[a]
        h_p1 = hmac.HMAC(s1, hashes.SHA256())
        h_p1.update(f"{self.label}:{a}".encode()) 
        proof1 = base64.b64encode(h_p1.finalize()).decode()

        # Step 4: Form Tx
        amount_range = self.config["economic_parameters"]["transaction_amount_range"]
        fee_range = self.config["economic_parameters"]["transaction_fee_range"]
        amount = random.randint(*amount_range)
        fee = random.randint(*fee_range)

        if self.balances[a] < amount + fee:
            return
        
        current_nonce = self.nonces[a] + 1

        tx = {
            "type": "Tx",
            "nonce": current_nonce,
            "PKS": self.anonymized_ids[a],
            "PKR": receiver_node.anonymized_ids[a],
            "asset_type": a,
            "amount": amount,
            "fee": fee,
            "origin": self.region,
            "SignedToken": token,
            "proof1": proof1 
        }
        
        tx_data = json.dumps({
            k: tx[k] for k in ("type","nonce","PKS","PKR","asset_type","amount","fee","origin","SignedToken")
        }, sort_keys=True).encode()
        
        tx_sig  = self.keys[a]["sk"].sign(tx_data, ec.ECDSA(hashes.SHA256()))
        tx["signature"] = base64.b64encode(tx_sig).decode()

        tx_id = stable_tx_id(tx)
        self.tx_locks[a] = True
        self.last_tx_refs[a] = tx_id
        
        # <--- INSERT THIS BLOCK --->
        print(f"[{self.env.now:.1f}] ⏱️ INIT   | ID: {tx_id} | Type: Tx")
        # <--- END INSERT --->
        
        # [LOG] Transaction Creation
        print(f"[{self.env.now:.1f}] 💸 {self.label} Created Tx {tx_id[:8]}... (Nonce {current_nonce}) | {amount} {a} -> {receiver_node.label}")

        yield from self.propagate_message(tx, tx_id)

        # Step 5: Wait for Block confirmation
        yield env.timeout(random.uniform(timeout1, timeout2))

    def receive_message(self, msg, h, delay):
        yield self.env.timeout(delay)
        yield from self.propagate_message(msg, h)
        
        msg_type = msg.get("type")
        
        # --- Handle Block ---
        if msg_type == "Block":
            block = msg["block"]
            
            if block["index"] >= self.config["simulation_parameters"]["rounds"]:
                 if not self.simulation_finished:
                     print(f"[{self.env.now:.1f}] 🏁 {self.label} received Final Block {block['index']}. Stopping.")
                     self.simulation_finished = True
                     self.active = False
                     
            if not self.blockchain or block["index"] > self.blockchain[-1]["index"]:
                self.blockchain.append(block)

            # 1. Update Balances & Nonces
            my_anon_ids = {self.anonymized_ids[a]: a for a in self.asset_types}
            
            for tx in block.get("txs", []):
                # Handle Normal Txs
                if tx.get("type") == "Tx":
                    tx_asset = tx.get("asset_type")
                    amount = tx.get("amount", 0)
                    fee = tx.get("fee", 0)
                    sender = tx.get("PKS")
                    receiver = tx.get("PKR")

                    if receiver in my_anon_ids:
                        my_asset = my_anon_ids[receiver]
                        if my_asset == tx_asset:
                            self.balances[my_asset] += amount
                    
                    if sender in my_anon_ids:
                        my_asset = my_anon_ids[sender]
                        if my_asset == tx_asset:
                            self.balances[my_asset] -= (amount + fee)
                            self.nonces[my_asset] = max(self.nonces[my_asset], tx.get("nonce", 0))

                # Handle Admin Txs (Redaction Detection)
                if tx.get("type") == "Tx_admin" and tx["op"] == "DEL":
                    for asset in self.asset_types:
                        creds = self.saved_credentials[asset]
                        # Reconstruct leaf: Hash(AnonID + Hex(S2))
                        s2_hex = base64.b64encode(creds["s2"]).decode()
                        my_leaf = hashlib.sha256(f"{creds['anon_id']}:{s2_hex}".encode()).hexdigest()
                        
                        if tx["target_hash"] == my_leaf:
                            print(f"[{self.env.now:.1f}] ⚠️ {self.label} DETECTED REDACTION in Block {block['index']}. Shutting down.")
                            self.active = False
                            return 
            
            committed_ids = { stable_tx_id(tx) for tx in block.get("txs", []) }
            for a in self.asset_types:
                last_id = self.last_tx_refs.get(a)
                if self.tx_locks.get(a) and last_id is not None and last_id in committed_ids:
                    self.tx_locks[a] = False
                    self.last_tx_refs[a] = None
            return

        # --- Handle SignedToken ---
        if msg_type == "SignedToken":
            if not self.active: return 
            
            target = msg.get("to")
            my_anon_ids = set(self.anonymized_ids.values())
            if target not in my_anon_ids: return 

            sig_b64 = msg.get("signature", "")
            try:
                sig = base64.b64decode(sig_b64)
                data = json.dumps({
                    "type": "SignedToken", "to": msg["to"], "from": msg["from"],
                    "asset_type": msg.get("asset_type"), "PKS": msg.get("PKS")
                }, sort_keys=True).encode()
                auth_pub = serialization.load_der_public_key(self.authority.public_key_bytes)
                auth_pub.verify(sig, data, ec.ECDSA(hashes.SHA256()))
            except (InvalidSignature, Exception):
                return
            
            asset = msg.get("asset_type")
            if asset in self.asset_types:
                self.signed_tokens[asset] = msg.copy()
                if not self.token_events[asset].triggered:
                    self.token_events[asset].succeed()
            return

    def propagate_message(self, msg, h):
        """Standard Gossip Protocol"""
        yield self.env.timeout(0)
        message = dict(msg)

        if message.get("type") != "Block":
            if h in self.seen_messages: return
            self.seen_messages.add(h)
        else:
            if h in self.propagated_blocks: return
            self.propagated_blocks.add(h)
            
        if message.get("type") == "SignedToken":
            st_hash = stable_msg_id(message)
            if st_hash in self.seen_signed: return
            self.seen_signed.add(st_hash)

        for peer in self.peers:
            delay = get_delay(self.region, peer.region)
            self.env.process(peer.receive_message(message, h, delay))

        if self.is_gateway and message["type"] in ("RToken", "Tx", "RedactReq"):
            for peer in self.peers:
                if isinstance(peer, VerifierNode):
                    d = get_delay(self.region, peer.region)
                    self.env.process(peer.receive_message(message, h, d))

class VerifierNode:
    def __init__(self, env, label, region, authority=None, target_fee=None, config=None):
        self.env                = env
        self.label              = label.upper()
        self.region             = region
        self.authority          = authority 
        self.allowed_asset_types = ["INR","USD","EUR","GBP","RUB"]
        self.seen_signed = set()
        self.balance = 0
        self.credited_blocks = set()
        
        self.local_alt_root = '0'*64 # Verifier's view of the Accumulator
        self.admin_buffer = []       # Buffer for admin Txs (Add/Del/SysUpd)
        
        # Store the range, do not calculate static target here
        self.tx_target_range = config["consensus_parameters"]["verifier_tx_target_range"]
        
        self.target_tx_count = random.randint(*self.tx_target_range)
        
        self.current_round_target = 0 
        self.accumulated_fee = 0
        
        self._sk = ec.generate_private_key(ec.SECP256R1()) 
        pub = self._sk.public_key()
        self.public_key_bytes = pub.public_bytes(
            encoding=Encoding.DER,
            format=PublicFormat.SubjectPublicKeyInfo
        )
        self.authority.register_node(
            self.label,
            public_key_bytes=self.public_key_bytes,
            region=self.region,
            asset_types=self.allowed_asset_types
        )        
        self.pending_txs        = []
        self.last_proposed_block = None
        self.blockchain         = []
        self.seen_messages      = set()
        self.block_event        = None
        
        # Instance 3: State & Sync
        self.balance_cache = {}    # Consensus-Linked State Cache
        self.peers = []            # Network Discovery: Other Verifiers + Authority
        self.sync_mode = False
        self.sync_target_index = 0
        self.sync_buffer = {}      # Buffer for future blocks received during sync
        self.future_tx_buffer = [] # [TX BUFFER FIX] Buffer for out-of-sequence txs
        
    def get_balance(self):
        return self.balance
                    
    def start(self):
        self.block_event = self.env.event()
        self.env.process(self.consensus_loop())

    def consensus_loop(self):
        print(f"[{self.env.now:.1f}] Verifier {self.label} entering consensus_loop")
        for round_no in range(1, ROUNDS + 1):
            self.proposed    = False
            self.target_tx_count = random.randint(*self.tx_target_range)
            self.block_event = self.env.event()
            yield self.block_event

    def validate_tx(self, tx):
        asset = tx.get("asset_type")
        claimed_TO = tx.get("origin")
        sender_anon_id = tx.get("PKS")
        proof1 = tx.get("proof1")
        tx_nonce = tx.get("nonce", 0) 
        
        # --- 1. Threshold Verification ---
        if not proof1: return False
        rtt = get_delay(self.region, self.authority.region) * 2
        yield self.env.timeout(rtt)
        
        response = self.authority.request_proof_share(sender_anon_id, asset)
        if not response:
            print(f"[{self.env.now:.1f}] Verifier {self.label} ❌ Authority refused P2 (Redacted/Unknown ID)")
            return False
            
        proof2 = response["proof2"]
        sig_b64 = response["signature"]

        auth_pub = serialization.load_der_public_key(self.authority.public_key_bytes)
        try:
            auth_pub.verify(base64.b64decode(sig_b64), proof2.encode(), ec.ECDSA(hashes.SHA256()))
        except InvalidSignature:
            print(f"[{self.env.now:.1f}] Verifier {self.label} ❌ Invalid P2 Signature")
            return False

        # --- 2. Standard Checks ---
        resolved = self.authority.pkindex.get(sender_anon_id)
        if not resolved: return False
        sender_label = resolved["label"]
        resolved_asset = resolved["asset_type"]
        if resolved_asset != asset: return False

        registered_TO = self.authority.OMT.get(sender_label, {}).get("region")
        if claimed_TO != registered_TO: return False

        if asset not in self.allowed_asset_types: return False

        min_fee = config["economic_parameters"]["minimum_fee"]
        if tx.get("fee", 0) < min_fee: return False

        # --- 3. Solvency + PBFT-committed Nonce Check (state cache) ---
        if self.sync_mode:
            return False

        key = (sender_anon_id, asset)
        info = self.balance_cache.get(key)

        if not isinstance(info, dict):
            print(f"[{self.env.now:.1f}] Verifier {self.label} ❌ Unknown sender state for {key}")
            return False

        expected_nonce = info["nonce"] + 1
        if tx_nonce != expected_nonce:
            print(f"[{self.env.now:.1f}] Verifier {self.label} ❌ Invalid Nonce: Expected {expected_nonce}, Got {tx_nonce}")
            return False

        for pending in self.pending_txs:
            if pending.get("PKS") == sender_anon_id and pending.get("asset_type") == asset:
                print(f"[{self.env.now:.1f}] Verifier {self.label} ❌ In-flight tx exists for sender on {asset}")
                return False

        balance = info["balance"]
        if balance < tx.get("amount", 0) + tx.get("fee", 0):
            print(f"[{self.env.now:.1f}] Verifier {self.label} ❌ insufficient {asset} funds (Bal: {balance})")
            return False

        return True

    def receive_message(self, msg, h, delay):
        """Handle incoming messages."""
        yield self.env.timeout(delay)

        t = msg.get("type")

        # --- NEW: Handle Tx_admin (From Authority) ---
        # Handles Merkle transition (ADD/DEL) AND System Coin Updates (SYS_UPD)
        if t == "Tx_admin":
            tx = msg["tx"]
            
            # 1. Verify Auth Sig
            auth_pub = serialization.load_der_public_key(self.authority.public_key_bytes)
            sig = base64.b64decode(tx["signature"])
            raw_tx = {k:tx[k] for k in tx if k!="signature"}
            try:
                auth_pub.verify(sig, json.dumps(raw_tx, sort_keys=True).encode(), ec.ECDSA(hashes.SHA256()))
            except Exception:
                print(f"[{self.label}] ❌ Invalid Tx_admin Signature")
                return 

            # 2. Distinguish Operation Type
            op = tx.get("op")
            
            if op in ["SYS_UPD", "EDIT"]:
                # For System Coin updates, we don't check Merkle roots, 
                # but we verify the signature (done above) and buffer it for inclusion.
                self.admin_buffer.append(tx)
                # print(f"[{self.label}] ✅ Buffered System Coin Update for {tx.get('target_verifier')}")
                return

            if op in ["ADD", "DEL"]:
                # Verify Cryptographic Transition for Merkle Tree ops
                is_valid = verify_admin_transition(
                    tx["prev_root"], tx["new_root"], 
                    tx["target_hash"], tx["proof"], tx["op"]
                )
                
                if is_valid:
                    self.local_alt_root = tx["new_root"]
                    self.admin_buffer.append(tx)
                else:
                    print(f"[{self.label}] ❌ Invalid Admin Proof! Prev: {tx['prev_root'][:6]} Local: {self.local_alt_root[:6]}")
            return

        # --- Handle RedactReq ---
        if t == "RedactReq":
            if h in self.seen_messages: return
            self.seen_messages.add(h)
            yield from self.propagate_message(msg, h)
            d = get_delay(self.region, self.authority.region)
            self.env.process(self.authority.receive_message(msg, h, d))
            return

        # SignedToken handling
        if t == "SignedToken":
            h = stable_msg_id(msg)
            if h in self.seen_signed: return
            self.seen_signed.add(h)
            yield from self.propagate_message(msg, h)
            return

        if h in self.seen_messages: return
        self.seen_messages.add(h)

        if t == "RToken":
            sig_b64 = msg.get("signature")
            if not sig_b64: return
            try:
                sig = base64.b64decode(sig_b64)
                pass 
            except Exception: return

            req = {
                "sender": msg["from"], 
                "asset_type": msg["asset_type"], 
                "PKS": msg["PKS"],
                "origin": msg["origin"] 
            }
            d = get_delay(self.region, self.authority.region)
            self.env.process(self.authority.receive_rtoken(req, d))
            return


        if t == "Tx":
            tx = msg
            
            # [FIX START] ----------------------------------------------------------
            # 1. IMMEDIATE DEDUPLICATION CHECKS
            # ----------------------------------------------------------------------
            tx_id = stable_tx_id(tx)

            # A. Global History Check: Has this Tx been committed already?
            # (Checks local history first, then Authority oracle as backup)
            if hasattr(self, 'finalized_tx_ids') and tx_id in self.finalized_tx_ids:
                return
            if hasattr(self.authority, 'finalized_tx_ids') and tx_id in self.authority.finalized_tx_ids:
                return

            # B. Local Mempool Check: Do we already have this EXACT transaction?
            # This prevents adding the same Tx twice when heard from multiple peers.
            for p in self.pending_txs:
                if stable_tx_id(p) == tx_id:
                    return  # Silently ignore exact duplicate
            # [FIX END] ------------------------------------------------------------

            # 2. Verify SignedToken sig
            st = tx.get("SignedToken", {})
            st_sig_b64 = st.get("signature", "")
            if not st_sig_b64: return
            st_data = json.dumps({
                "type": "SignedToken", "to": st.get("to"), "from": st.get("from"),
                "asset_type": st.get("asset_type"), "PKS": st.get("PKS")
            }, sort_keys=True).encode()
            try:
                auth_pub = serialization.load_der_public_key(self.authority.public_key_bytes)
                auth_pub.verify(base64.b64decode(st_sig_b64), st_data, ec.ECDSA(hashes.SHA256()))
            except InvalidSignature:
                print(f"[{self.env.now:.1f}] Verifier {self.label} ❌ Invalid SignedToken")
                return

            # 3. Verify Tx Signature
            resolved = self.authority.pkindex.get(tx["PKS"])
            if not resolved: return
            sender_label = resolved["label"]
            asset = tx["asset_type"]
            
            raw_pk_bytes = self.authority.OMT[sender_label]["pubkeys"][asset]
            sender_pub = serialization.load_der_public_key(raw_pk_bytes)

            tx_data = json.dumps({
                k: msg[k] for k in ("type","nonce","PKS","PKR","asset_type","amount","fee","origin","SignedToken")
            }, sort_keys=True).encode()
            try:
                tx_sig = base64.b64decode(tx.get("signature", ""))
                sender_pub.verify(tx_sig, tx_data, ec.ECDSA(hashes.SHA256()))
            except InvalidSignature:
                print(f"[{self.env.now:.1f}] Verifier {self.label} ❌ Invalid Tx Signature")
                return
            
            # [TX BUFFER FIX] Nonce Check & Buffering
            s_key = (tx.get("PKS"), tx.get("asset_type"))
            s_info = self.balance_cache.get(s_key)
            expected_nonce = (s_info["nonce"] + 1) if s_info else 1
            got_nonce = tx.get("nonce", 0)

            if got_nonce > expected_nonce:
                print(f"[{self.env.now:.1f}] {self.label} ⏳ BUFFER tx{got_nonce} {short_id(tx.get('PKS'))}:{tx.get('asset_type')} (exp={expected_nonce})")
                self.future_tx_buffer.append(tx)
                return
            elif got_nonce < expected_nonce:
                return

            is_valid = yield from self.validate_tx(tx)
            
            if not is_valid:
                return

            # [FIX] CONFLICT CHECK (Double Spend)
            # We already checked for exact ID match above.
            # This loop now strictly checks for "Same User + Same Asset" conflicts
            # (e.g. Trying to spend twice in one block with different IDs/Nonces)
            for p in self.pending_txs:
                if p["PKS"] == tx["PKS"] and p["asset_type"] == tx["asset_type"]:
                     print(f"[{self.env.now:.1f}] Verifier {self.label} ❌ double-spend / conflict in pending")
                     return

            self.pending_txs.append(tx)
            self.accumulated_fee += tx.get('fee', 0)
            
            if (not self.proposed) and (len(self.pending_txs) >= self.target_tx_count):
                txs_to_propose = self.pending_txs[:self.target_tx_count]
                remaining_txs = self.pending_txs[self.target_tx_count:]
                new_block = self.createBlock(txs_to_propose)
                self.proposed = True
                self.last_proposed_block = new_block
                print(f"[{self.env.now:.1f}] Verifier {self.label} ➜ proposing block {new_block['index']}")
                # Create a network message wrapping the block
                proposal_msg = {
                    "type": "CandidateBlock",
                    "block": new_block,
                    "proposer": self.label
                }
                # Gossip to neighbors using the new helper
                broadcast_to_peers(self.env, self, proposal_msg)
                
                self.pending_txs = remaining_txs
                self.accumulated_fee = sum(t.get('fee', 0) for t in self.pending_txs)
            return

        if t == "Block":
            block = msg.get("block")
            idx = block["index"]
            next_idx = len(self.blockchain)
            
            block_msg = {"type":"Block", "block":block}
            h_block   = stable_block_id(block_msg)
            for pub in self.get_connected_peers():
                d = get_delay(self.region, pub.region)
                self.env.process(pub.receive_message(block_msg, h_block, d))

            if idx < next_idx:
                return 
            
            if idx == next_idx:
                self.commit_block(block)
                while True:
                    next_buffered = self.sync_buffer.pop(len(self.blockchain), None)
                    if next_buffered:
                        self.commit_block(next_buffered)
                    else:
                        break
                
                if self.sync_mode and not self.sync_buffer:
                    print(f"[{self.env.now:.1f}] Verifier {self.label} 🔄 Sync Complete. Resuming normal op.")
                    self.sync_mode = False

                if self.block_event and not self.block_event.triggered:
                    self.block_event.succeed()
                    yield self.env.timeout(0.01)
                yield from self.process_finalized_block(block)

            elif idx > next_idx:
                if not self.sync_mode:
                    print(f"[{self.env.now:.1f}] Verifier {self.label} ⚠️ GAP DETECTED (Have {next_idx-1}, Got {idx}). Entering SYNC mode.")
                    self.sync_mode = True
                    self.env.process(self.run_synchronization(next_idx, idx))
                self.sync_buffer[idx] = block
                
        # Add this inside VerifierNode.receive_message
        if t == "CandidateBlock":
            if h in self.seen_messages: return
            self.seen_messages.add(h)
            
            # Forward to my neighbors (Gossip Protocol)
            broadcast_to_peers(self.env, self, msg)
            return

    def commit_block(self, block):
        self.blockchain.append(block)

        if block["index"] == 0:
            for entry in block["state"]:
                if entry.get("role") == "PUBLIC":
                    key = (entry["id"], entry["asset_type"])
                    self.balance_cache[key] = {
                        "balance": entry.get("balance", 0),
                        "nonce": entry.get("nonce", 0)
                    }
            return
        
        # --- FIX START: Clean up Admin Buffer ---
        # Identify Admin Txs in this block and remove them from our local buffer
        # so we don't try to propose them again in the next block.
        committed_admin_ids = set()
        for tx in block.get("txs", []):
            if tx.get("type") == "Tx_admin":
                committed_admin_ids.add(stable_tx_id(tx))
        
        if committed_admin_ids and self.admin_buffer:
            self.admin_buffer = [
                tx for tx in self.admin_buffer
                if stable_tx_id(tx) not in committed_admin_ids
            ]
        # --- FIX END ---

        for tx in block.get("txs", []):
            if tx.get("type") == "Tx_admin": continue
            
            asset = tx.get("asset_type")
            sender = tx.get("PKS")
            receiver = tx.get("PKR")
            amount = tx.get("amount", 0)
            fee = tx.get("fee", 0)
            tx_nonce = tx.get("nonce", 0)

            s_key = (sender, asset)
            if s_key in self.balance_cache:
                self.balance_cache[s_key]["balance"] -= (amount + fee)
                self.balance_cache[s_key]["nonce"] = tx_nonce

            r_key = (receiver, asset)
            if r_key in self.balance_cache:
                self.balance_cache[r_key]["balance"] += amount
            else:
                self.balance_cache[r_key] = {"balance": amount, "nonce": 0}
        
        if self.future_tx_buffer:
            self.env.process(self.replay_buffered_txs())
            
    def replay_buffered_txs(self):
        still_future = []
        self.future_tx_buffer.sort(key=lambda t: t.get("nonce", 0))
        working_buffer = list(self.future_tx_buffer)
        
        for tx in working_buffer:
            s_key = (tx.get("PKS"), tx.get("asset_type"))
            s_info = self.balance_cache.get(s_key)
            expected_nonce = (s_info["nonce"] + 1) if s_info else 1
            got_nonce = tx.get("nonce", 0)

            if got_nonce == expected_nonce:
                is_valid = yield from self.validate_tx(tx)
                if is_valid:
                    tx_id = stable_tx_id(tx)
                    if tx_id in self.authority.finalized_tx_ids:
                        continue 

                    conflict = False
                    for p in self.pending_txs:
                        if p["PKS"] == tx["PKS"] and p["asset_type"] == tx["asset_type"]:
                            conflict = True; break
                    
                    if not conflict:
                        print(f"[{self.env.now:.1f}] {self.label} ✅ REPLAY tx{got_nonce} {short_id(tx['PKS'])}:{tx['asset_type']}")
                        self.pending_txs.append(tx)
                        self.accumulated_fee += tx.get('fee', 0)
                        
                        if (not self.proposed) and (len(self.pending_txs) >= self.target_tx_count):
                            new_block = self.createBlock(list(self.pending_txs))
                            self.proposed = True
                            self.last_proposed_block = new_block
                            self.env.process(self.authority.receive_candidate_block(self.label, new_block))
                            self.pending_txs = []
                            self.accumulated_fee = 0
                    else:
                        pass
                else:
                    pass
            elif got_nonce > expected_nonce:
                still_future.append(tx)

        self.future_tx_buffer = still_future

    def run_synchronization(self, start_index, target_index):
        poll_interval = self.config["consensus_parameters"].get("sync_poll_interval", 5)
        current_req = start_index
        
        while current_req < target_index:
            for peer in self.peers:
                d = get_delay(self.region, peer.region)
                yield self.env.timeout(d * 2) 
                
                if len(peer.blockchain) > current_req:
                    missing_block = peer.blockchain[current_req]
                    self.env.process(self.receive_message({"type": "Block", "block": missing_block}, "SYNC", 0))
                    current_req += 1
                    break
            
            yield self.env.timeout(poll_interval)
            
    def propagate_message(self, msg, h):
        yield self.env.timeout(0)
        if msg.get("type") not in ("Block", "RedactReq"):
             if h in self.seen_messages: return
             self.seen_messages.add(h)
             
        for peer in self.get_connected_peers():
            delay = get_delay(self.region, peer.region)
            def _forward(p=peer, d=delay):
                yield self.env.timeout(d)
                if msg.get("type") == "Block":
                    print(f"[{self.env.now:.1f}] Verifier {self.label} ➜ flood Block {msg['block']['index']}")
                yield from p.receive_message(msg, h, d)
            self.env.process(_forward())        

    def receive_final_block(self):
        if self.block_event and not self.block_event.triggered:
            self.block_event.succeed()

    def compute_last_block_hash(self):
        if not self.blockchain: return '0' * 64
        return self.blockchain[-1].get('block_hash', '0' * 64)
    
    def createBlock(self, txs_valid):
        # [NEW] Include Admin Txs at the top of the block
        # Admin Txs (Add/Del/SysUpd) take priority
        final_txs = self.admin_buffer + txs_valid 
        self.admin_buffer = [] # Clear buffer after inclusion
        
        prev_hash = self.compute_last_block_hash()
        index = (self.blockchain[-1]["index"] + 1) if self.blockchain else 1
        tx_leaves = [json.dumps(tx, sort_keys=True) for tx in final_txs]
        merkle = compute_merkle_root(tx_leaves)
        total_fees = sum(tx.get("fee", 0) for tx in txs_valid)

        return {
            "index":       index,
            "prev_hash":   prev_hash,
            "txs":         list(final_txs),
            "merkle_root": merkle,
            "proposer":    self.label,
            "total_fees":  total_fees,
        }

    def get_connected_peers(self):
        return [p for p in all_publics if self in p.peers]

    def process_finalized_block(self, block):
        # Credit fees
        if block.get("proposer") == self.label and block.get("index") not in self.credited_blocks:
            fee_sum = sum(tx.get("fee",0) for tx in block.get("txs", []))
            self.balance += fee_sum
            self.credited_blocks.add(block["index"])
            print(f"[{self.env.now:.1f}] Verifier {self.label} 💰 credited fees {fee_sum}")

        if self.last_proposed_block and block["index"] < ROUNDS:
            committed = {stable_tx_id(tx) for tx in block["txs"]}
            
            leftovers = [
                tx for tx in self.last_proposed_block["txs"]
                if stable_tx_id(tx) not in committed and tx.get("type") != "Tx_admin" 
            ]
            
            new_txs = list(leftovers)
            
            if len(new_txs) > self.target_tx_count:
                excess = new_txs[self.target_tx_count:]
                new_txs = new_txs[:self.target_tx_count]
                self.pending_txs = excess + self.pending_txs
            
            while (len(new_txs) < self.target_tx_count) and self.pending_txs:
                tx = self.pending_txs.pop(0)
                if stable_tx_id(tx) not in committed:
                    is_valid = yield from self.validate_tx(tx)
                    if is_valid:
                        new_txs.append(tx)

            if new_txs and len(new_txs) >= self.target_tx_count:
                candidate = self.createBlock(new_txs)
                self.last_proposed_block = candidate
                self.proposed = True
                self.env.process(
                    self.authority.receive_candidate_block(self.label, candidate)
                )
                print(f"[{self.env.now:.1f}] {self.label} 📦 Proposed new block {candidate['index']}")

            self.pending_txs = [
                tx for tx in self.pending_txs
                if stable_tx_id(tx) not in self.authority.finalized_tx_ids
            ]

class MerkleLite:
    """Implements the cryptographic accumulator for ALT/OMT state."""
    def __init__(self):
        self.leaves = [] 
        self.tree = []   

    def _hash(self, data):
        return hashlib.sha256(data.encode()).hexdigest()

    def add_leaf(self, leaf_hash):
        self.leaves.append(leaf_hash)
        self.leaves.sort() 
        self.rebuild_tree()
        return self.get_root(), self.get_proof(leaf_hash)

    def remove_leaf(self, leaf_hash):
        if leaf_hash in self.leaves:
            proof = self.get_proof(leaf_hash)
            self.leaves.remove(leaf_hash)
            self.rebuild_tree()
            return self.get_root(), proof
        return None, None

    def rebuild_tree(self):
        if not self.leaves:
            self.tree = [['0'*64]]
            return

        current_level = self.leaves[:]
        tree = [current_level]
        
        while len(current_level) > 1:
            next_level = []
            for i in range(0, len(current_level), 2):
                a = current_level[i]
                b = current_level[i+1] if i+1 < len(current_level) else current_level[i]
                if a > b: a, b = b, a
                next_level.append(self._hash(a + b))
            current_level = next_level
            tree.append(current_level)
        self.tree = tree

    def get_root(self):
        if not self.leaves: return '0'*64
        return self.tree[-1][0]

    def get_proof(self, leaf_hash):
        if leaf_hash not in self.leaves: return []
        proof = []
        try:
            idx = self.leaves.index(leaf_hash)
            for level in self.tree[:-1]: 
                sibling_idx = idx + 1 if idx % 2 == 0 else idx - 1
                if sibling_idx < len(level):
                    proof.append({"val": level[sibling_idx]})
                else:
                    proof.append({"val": level[idx]}) 
                idx //= 2
        except ValueError: pass
        return proof

class AuthorityNode:
    def __init__(self, env, label, region, verifiers):
        self.env              = env
        self.label            = label.upper()
        self.region           = region
        self.verifiers        = verifiers
        self.config           = config # Ensure config is accessible
        
        # --- Consensus & Ledger State ---
        self.finalized_tx_ids = set()
        self.seen_rtokens     = set()
        self.received_round   = set()
        self.candidate_blocks = {}
        self.blockchain       = []
        self.current_index    = 1 
        
        # --- Identity & Registry State ---
        self.OMT       = {}    
        self.ALT       = []    
        self.pubkeys   = {}    
        self.pkindex   = {}    
        
        # --- [NEW] System Coin State ---
        self.verifier_system_balances = {} # Cache: {label: system_coin_value}
        
        # --- Cryptographic Identity ---
        self._sk = ec.generate_private_key(ec.SECP256R1())
        self.public_key_bytes = self._sk.public_key().public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        )
        
        # --- Redaction & Verification State ---
        self.alt_tree = MerkleLite()   
        self.processed_redactions = set() 
        self.revoked_users = set()
        
        # Start the consensus engine
        self.main_process = self.env.process(self.consensus_loop())

    def calculate_leaf(self, anon_id, s2_share):
        s2_hex = base64.b64encode(s2_share).decode()
        return hashlib.sha256(f"{anon_id}:{s2_hex}".encode()).hexdigest()

    def register_node(self, label: str, public_key_bytes: bytes, region: str, asset_types: list, share_s2=None, canon_id=None):
        self.pubkeys[label] = public_key_bytes

        if label not in self.OMT:
            self.OMT[label] = {"region": region, "active": True, "pubkeys": {}}
        else:
            self.OMT[label]["region"] = region
            self.OMT[label].setdefault("pubkeys", {})

        for asset in asset_types:
            if canon_id:
                self.pkindex[canon_id] = {"label": label, "asset_type": asset}

            self.OMT[label]["pubkeys"][asset] = public_key_bytes

            existing = next((e for e in self.ALT if e.get("canon_id") == canon_id), None)
            if existing:
                self.ALT.remove(existing)

            self.ALT.append({
                "public_key":  public_key_bytes,
                "asset_type":  asset,
                "region":      region,
                "active":      True,
                "share_s2":    share_s2,  
                "canon_id":    canon_id
            })
            
            if canon_id and share_s2:
                leaf = self.calculate_leaf(canon_id, share_s2)
                old_root = self.alt_tree.get_root()
                new_root, proof = self.alt_tree.add_leaf(leaf)
                
                tx_admin = {
                    "type": "Tx_admin",
                    "op": "ADD",
                    "target_hash": leaf, 
                    "prev_root": old_root,
                    "new_root": new_root,
                    "proof": proof,
                    "timestamp": self.env.now
                }
                self.sign_and_broadcast_admin(tx_admin)
                print(f"[{self.env.now:.1f}] Auth 📝 Registered {label} ({asset}). New Root Hash Value: {new_root[:8]}")
                
    def localizedRedaction(self, region, asset_type):
        """
        Twist: Directly modifies the OMT active flag for all users in a region.
        This effectively bans them from receiving new tokens (R-Tokens) henceforth.
        """
        affected_count = 0
        affected_labels = []
        
        for label, info in self.OMT.items():
            # Check Region match and if they are currently active
            if info.get("region") == region and info.get("active", True):
                # Check if they hold the specific asset (Targeted Local Ban)
                if asset_type in info.get("pubkeys", {}):
                    self.OMT[label]["active"] = False # Direct Modification per twist
                    affected_labels.append(label)
                    affected_count += 1
        
        if affected_count == 0:
            print(f"[{self.env.now:.1f}] Authority ⚠️ Local Redaction: No active nodes found in {region} for {asset_type}")
            return

        # Broadcast EDIT transaction for audit trail and Verifier buffering
        tx_admin = {
            "type": "Tx_admin",
            "op": "EDIT",
            "region": region,
            "asset_type": asset_type,
            "affected_users": affected_labels,
            "timestamp": self.env.now
        }
        self.sign_and_broadcast_admin(tx_admin)
        print(f"[{self.env.now:.1f}] Authority 🌍 Localized Redaction (EDIT): Banned {asset_type} in {region}. {affected_count} nodes deactivated.")
        
    def receive_message(self, msg, h, delay):
        yield self.env.timeout(delay)
        
        if msg.get("type") == "RedactReq":
            req_id = stable_msg_id(msg)
            if req_id in self.processed_redactions: return
            self.processed_redactions.add(req_id)
            
            sender_anon = msg.get("PKS")
            sig_b64 = msg.get("signature")
            
            user_entry = next((e for e in self.ALT if e["canon_id"] == sender_anon), None)
            
            if user_entry:
                try:
                    user_pk_bytes = user_entry["public_key"]
                    user_pub = serialization.load_der_public_key(user_pk_bytes)
                    data = json.dumps({"type": "RedactReq", "PKS": sender_anon}, sort_keys=True).encode()
                    user_pub.verify(base64.b64decode(sig_b64), data, ec.ECDSA(hashes.SHA256()))
                    
                    print(f"[{self.env.now:.1f}] Authority 🛡️ Valid Redaction Req from {short_id(sender_anon)}")
                    self.globalizedDelete(sender_anon)
                except Exception as e:
                    print(f"[{self.env.now:.1f}] Authority ❌ Invalid Redaction Signature: {e}")
            else:
                print(f"[{self.env.now:.1f}] Authority ❓ Redaction Req for unknown/already-deleted ID")
                
        if msg.get("type") == "CandidateBlock":
        # Extract the payload and call internal processing logic
            proposer = msg["proposer"]
            block = msg["block"]
            self.env.process(self.receive_candidate_block(proposer, block))
            return

    def globalizedDelete(self, anon_id):
        entry = next((e for e in self.ALT if e["canon_id"] == anon_id), None)
        if not entry: return
        
        leaf = self.calculate_leaf(entry["canon_id"], entry["share_s2"])
        old_root = self.alt_tree.get_root()
        
        self.ALT.remove(entry)
        self.revoked_users.add(anon_id)
        
        label_info = self.pkindex.get(anon_id)
        if label_info:
            del self.pkindex[anon_id]
        
        new_root, proof = self.alt_tree.remove_leaf(leaf)
        
        tx_admin = {
            "type": "Tx_admin",
            "op": "DEL",
            "target_hash": leaf,
            "prev_root": old_root,
            "new_root": new_root,
            "proof": proof
        }
        self.sign_and_broadcast_admin(tx_admin)
        print(f"[{self.env.now:.1f}] Authority ✂️ REDACTED {short_id(anon_id)}. New Root: {new_root[:8]}")

    def sign_and_broadcast_admin(self, tx):
        blob = json.dumps(tx, sort_keys=True).encode()
        sig = self._sk.sign(blob, ec.ECDSA(hashes.SHA256()))
        tx["signature"] = base64.b64encode(sig).decode()
        
        # Log tracer INIT *before* signing (at tx creation time)
        tx = self.register_tx_for_latency(tx)
                        
        # <--- INSERT THIS BLOCK --->
        # Determine specific type for granular analysis
        # sub_type = f"Tx_admin_{tx.get('op', 'UNK')}"
        # t_id = stable_tx_id(tx)
        # print(f"[{self.env.now:.1f}] ⏱️ INIT   | ID: {t_id} | Type: {sub_type}")
        # <--- END INSERT --->
        
        msg = {"type": "Tx_admin", "tx": tx}
        h = stable_msg_id(tx)
        
        for v in self.verifiers:
            d = get_delay(self.region, v.region)
            self.env.process(v.receive_message(msg, h, d))
            
    def register_tx_for_latency(self, tx):
        """Log ⏱️ INIT for every tx that can reach block finalization."""
        tid = stable_tx_id(tx)
        if tx.get("type") == "Tx_admin":
            subtype = f"Tx_admin_{tx.get('op', 'UNK')}"
        else:
            subtype = "Tx"
        print(f"[{self.env.now:.1f}] ⏱️ INIT | ID: {tid} | Type: {subtype}")
        return tx


    def request_proof_share(self, anon_id, asset_type):
        s2 = None
        user_label = None
        for entry in self.ALT:
            if entry["canon_id"] == anon_id and entry["asset_type"] == asset_type:
                if not entry["active"]:
                    return None 
                s2 = entry["share_s2"]
                user_label = self.pkindex.get(anon_id, {}).get("label") 
                if not user_label:
                     pass
                break
        
        if not s2: return None

        h_p2 = hmac.HMAC(s2, hashes.SHA256())
        if not user_label: return None
        
        h_p2.update(f"{user_label}:{asset_type}".encode()) 
        proof2 = base64.b64encode(h_p2.finalize()).decode()

        proof2_sig = self._sk.sign(proof2.encode(), ec.ECDSA(hashes.SHA256()))
        
        return {
            "proof2": proof2,
            "signature": base64.b64encode(proof2_sig).decode()
        }

    def receive_rtoken(self, request, delay):
        yield self.env.timeout(delay)
        sender = request["sender"]
        asset  = request["asset_type"]
        
        sender_entry = self.OMT.get(sender, {})
    
        # 1. Check OMT Active Status (Line 1 in Paper Algo 4)
        if not sender_entry.get("active", False): 
            print(f"Refused: {sender} is INACTIVE/LOCALLY REDACTED in OMT")
            return

        # 2. Check Region (Line 3 in Paper Algo 4)
        if sender_entry.get("region") != request["origin"]:
            return

        rhash = f"rtok|{sender}|{self.current_index}|{asset}"
        if rhash in self.seen_rtokens: return
        self.seen_rtokens.add(rhash)
        
        st = {
            'type': 'SignedToken', 
            'to': request["PKS"], 
            'from': self.label, 
            'asset_type': asset, 
            'PKS': request["PKS"], 
        }
        
        data = json.dumps(st, sort_keys=True).encode()
        sig  = self._sk.sign(data, ec.ECDSA(hashes.SHA256()))
        st["signature"] = base64.b64encode(sig).decode()
        
        # [LOG] Authority Issuance
        print(f"[{self.env.now:.1f}] Auth ✍️  Issued SignedToken for {sender} ({asset})")

        h = stable_msg_id(st)
        for v in self.verifiers:
            d = get_delay(self.region, v.region)
            self.env.process(v.receive_message(st, h, d))

    def receive_candidate_block(self, label, block):
        yield self.env.timeout(0)
        rid = (label, block['index'])
        if rid in self.received_round: return
        self.received_round.add(rid)

        self.candidate_blocks = {
            lbl: blk for lbl, blk in self.candidate_blocks.items()
            if any(stable_tx_id(tx) not in self.finalized_tx_ids for tx in blk['txs'])
        }
        self.candidate_blocks[label] = block

    def select_block(self):
        labels    = list(self.candidate_blocks.keys())
        proposals = self.candidate_blocks
        
        tx_counts = {lbl: len(proposals[lbl]["txs"]) for lbl in labels}
        max_tx    = max(tx_counts.values(), default=1)
        T         = {lbl: tx_counts[lbl] / max_tx for lbl in labels}

        # --- [NEW] Calculate Generosity using System Coin Balance ---
        # Instead of calling get_balance() (which might be single asset or raw), 
        # we use the normalized 'verifier_system_balances' cache.
        balances = self.verifier_system_balances
        
        # Safe min/max logic handling empty dicts
        vals = list(balances.values()) if balances else [0.0]
        min_b, max_b = min(vals), max(vals)
        span = (max_b - min_b) or 1
        
        # If a label isn't in the cache, default to 0.0
        G = {lbl: (max_b - balances.get(lbl, 0.0)) / span for lbl in labels}

        alpha = config["consensus_parameters"]["block_selection_weights"]["alpha_generosity"]
        beta = config["consensus_parameters"]["block_selection_weights"]["beta_throughput"]
        S = {lbl: alpha * G[lbl] + beta * T[lbl] for lbl in labels}

        best = sorted(labels, key=lambda lbl: (-S[lbl], lbl))
        # --- FIX: Define variables first ---
        chosen_label = best[0]
        chosen_block = proposals[chosen_label]

        # Now you can safely log, because 'chosen_block' is defined
        # Note: If you keep this here, you should REMOVE the similar lines 
        # in consensus_loop to avoid double-printing "INIT" logs.
        chosen_block["txs"] = [self.register_tx_for_latency(tx) for tx in chosen_block["txs"]]
        
        return chosen_label, chosen_block
        # return best[0], proposals[best[0]]

    # --- [NEW] Process Fee Update & Broadcast ---
    def process_fee_update(self, label, fee_dict):
        # 1. Calculate Value in System Coins
        total_val = 0.0
        weights = self.config["economic_parameters"]["asset_weights"]
        for asset, amount in fee_dict.items():
            w = weights.get(asset, 0.0) # Conservative default if weight missing
            total_val += amount * w
        
        if total_val == 0: return

        # 2. Update Local Cache
        prev_bal = self.verifier_system_balances.get(label, 0.0)
        new_bal = prev_bal + total_val
        self.verifier_system_balances[label] = new_bal
        
        # 3. Broadcast Tx_admin(SYS_UPD)
        # We don't need Merkle Proofs here, as we aren't changing the Identity Tree.
        # We just need the signature to prove the Authority authorized this balance view.
        tx_admin = {
            "type": "Tx_admin",
            "op": "SYS_UPD",
            "target_verifier": label,
            "fee_breakdown": fee_dict,
            "added_system_coin": total_val,
            "new_system_balance": new_bal,
            "timestamp": self.env.now
        }
        self.sign_and_broadcast_admin(tx_admin)
        print(f"[{self.env.now:.1f}] Auth ⚖️ Updated System Coin for {label}: +{total_val:.2f} (Total: {new_bal:.2f})")

    def consensus_loop(self):
        print(f"[{self.env.now:.1f}] Authority entering consensus_loop")
        for current_round in range(1, ROUNDS + 1):
            # 1. Filter proposals: STRICTLY MATCH CURRENT ROUND
            # This ensures we discard any delayed blocks from previous rounds immediately.
            self.candidate_blocks = {
                lab: blk for lab, blk in self.candidate_blocks.items()
                if blk.get("index") == self.current_index
            }
            self.received_round.clear()

            # Wait for proposals...
            target = math.ceil(len(self.verifiers) * 0.51)
            timeout = random.uniform(TIMEOUT_MIN_AUTH, TIMEOUT_MAX_AUTH)
            deadline = self.env.now + timeout
            
            while len(self.candidate_blocks) < target and self.env.now < deadline:
                yield self.env.timeout(1)
            
            if not self.candidate_blocks:
                print(f"[{self.env.now:.1f}] Authority ⚠️ No candidate blocks. Skipping round.")
                yield self.env.timeout(5)
                continue

            # 2. Select the best block
            chosen_label, chosen_block = self.select_block()
            
            # ✅ NEW: Log ⏱️ INIT for ALL txs (including genesis/SYSUPD/admin)
            all_chosen_txs = [self.register_tx_for_latency(tx) for tx in chosen_block['txs']]
            chosen_block['txs'] = all_chosen_txs  # Use traced versions

            print(f"[{self.env.now:.1f}] Authority 🔨 selected block {chosen_block['index']} from {chosen_label} (Txs: {len(chosen_block['txs'])})")
            
            # ==============================================================================
            # 🛡️ AUTHORITY FIREWALL: Sanitize the Block
            # ==============================================================================
            unique_txs = []
            block_local_ids = set() 

            for tx in chosen_block['txs']:
                t_id = stable_tx_id(tx)
                
                # CHECK A: Is this a Zombie? (Already in the chain history)
                if t_id in self.finalized_tx_ids:
                    # REJECT: This Verifier failed to prune their pool. We drop it here.
                    continue 
                
                # CHECK B: Is it a Duplicate? (Appears twice in this same block)
                if t_id in block_local_ids:
                    # REJECT: The Verifier sent a block with the same tx multiple times.
                    continue 
                
                block_local_ids.add(t_id)
                unique_txs.append(tx)
            
            # Update the block with the CLEANED list
            chosen_block['txs'] = unique_txs
            # ==============================================================================
            
            print(f"[{self.env.now:.1f}] Authority 🔨 selected block {chosen_block['index']} from {chosen_label} (Txs: {len(unique_txs)})")

            # Log COMMIT for all transactions (Now guaranteed unique)
            for tx in chosen_block['txs']:
                t_id = stable_tx_id(tx)
                if tx.get("type") == "Tx_admin":
                    t_type = f"Tx_admin_{tx.get('op', 'UNK')}"
                else:
                    t_type = "Tx"
                print(f"[{self.env.now:.1f}] ⏱️ COMMIT | ID: {t_id} | Type: {t_type}")

            # 3. Finalize (Merkle Root must be re-calculated for the cleaned list)
            tx_leaves = [json.dumps(tx, sort_keys=True) for tx in chosen_block['txs']]
            merkle = compute_merkle_root(tx_leaves)
            
            fee_totals = {}
            for tx in chosen_block["txs"]:
                if tx.get("type") == "Tx_admin": continue
                a = tx.get("asset_type")
                if a: 
                    fee_totals[a] = fee_totals.get(a, 0) + tx.get("fee", 0)

            self.process_fee_update(chosen_label, fee_totals)

            block = {
                "index":       self.current_index,
                "prev_hash":   self.blockchain[-1]["block_hash"],
                "txs":         chosen_block["txs"],
                "merkle_root": merkle,
                "proposer":    chosen_block["proposer"],
                "total_fees":  sum(fee_totals.values()),
                "fee_totals":  fee_totals
            }
            block_bytes = json.dumps(block, sort_keys=True).encode()
            block["block_hash"] = hashlib.sha256(block_bytes).hexdigest()

            # Update Global History to prevent future replays
            for tx in block["txs"]:
                self.finalized_tx_ids.add(stable_tx_id(tx))
            
            self.distribute_block(block)
            self.blockchain.append(block)
            self.current_index += 1

        self.seen_rtokens.clear()

    def distribute_block(self, block):
        msg = {'type': 'Block', 'block': block}
        h   = stable_block_id(msg)
        for v in self.verifiers:
            d = get_delay(self.region, v.region)
            self.env.process(v.receive_message(msg, h, d))

def verify_admin_transition(prev_root, new_root, leaf_hash, proof, op):
    """
    Verifies that 'prev_root' transitions to 'new_root' via 'op' on 'leaf_hash'.
    """
    computed = leaf_hash
    for node in proof:
        sibling = node["val"]
        if computed > sibling: computed = hashlib.sha256((sibling + computed).encode()).hexdigest()
        else: computed = hashlib.sha256((computed + sibling).encode()).hexdigest()
            
    if op == "ADD": return computed == new_root
    if op == "DEL": return computed == prev_root 
    return False

def _parse_topology_file(path):
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()

    m_labels = re.search(r"Enter node labels\s*\(comma-separated\)\s*:\s*(.+)", text, flags=re.I)
    if not m_labels: raise ValueError("Could not find node labels line.")
    nodes = [t.strip().upper() for t in m_labels.group(1).split(",") if t.strip()]
    adjacency = {nd: [] for nd in nodes}

    rel_block_match = re.search(
        r"Define node relationship.*?\n(.*?)(?=\n\s*Mention which node is Authority)", 
        text, flags=re.I | re.S
    )
    if not rel_block_match: raise ValueError("Could not find relationships block.")
    rel_block = rel_block_match.group(1)

    for line in rel_block.splitlines():
        line = line.strip()
        if not line: continue
        m = re.match(r"^([A-Za-z0-9_]+)\s*:\s*(.*)$", line)
        if not m: continue
        src, nbrs = m.group(1).upper(), m.group(2).strip()
        if src not in adjacency: continue
        if nbrs:
            for nbr in nbrs.split(","):
                u = nbr.strip().upper()
                if u and u in adjacency: adjacency[src].append(u)

    m_auth = re.search(r"Mention which node is Authority\s*\(one\)\s*:\s*([A-Za-z0-9_]+)", text, flags=re.I)
    if not m_auth: raise ValueError("Could not find authority line.")
    authority_lbl = m_auth.group(1).upper()

    m_ver = re.search(r"Mention Verifiers\s*\(comma-separated\)\s*:\s*(.+)", text, flags=re.I)
    if not m_ver: raise ValueError("Could not find verifiers line.")
    verifiers_lbl = [v.strip().upper() for v in m_ver.group(1).split(",") if v.strip()]

    for u, nbrs in list(adjacency.items()):
        for v in nbrs:
            if u not in adjacency.get(v, []):
                adjacency[v].append(u)

    return nodes, adjacency, authority_lbl, verifiers_lbl

def setup_simulation():
    global all_publics
    
    if len(sys.argv) >= 2 and sys.argv[1].strip():
        topo_path = sys.argv[1]
        nodes, adjacency, authority_lbl, verifiers_lbl = _parse_topology_file(topo_path)
    else:
        n            = int(input("Enter number of nodes in the network: "))
        labels       = input("Enter node labels (comma-separated): ")
        nodes        = [l.strip().upper() for l in labels.split(',') if l.strip()]
        adjacency    = {nd: [] for nd in nodes}

        print("Define node relationship (neighbors comma-separated, or blank):")
        for nd in nodes:
            line = input(f"{nd}: ").strip()
            if not line: continue
            for nbr in line.split(','):
                u = nbr.strip().upper()
                if u in nodes: adjacency[nd].append(u)

        for u, nbrs in list(adjacency.items()):
            for v in nbrs:
                if u not in adjacency[v]: adjacency[v].append(u)

        authority_lbl = input("Mention which node is Authority (one): ").strip().upper()
        raw_v         = input("Mention Verifiers (comma-separated): ").strip()
        verifiers_lbl = [v.strip().upper() for v in raw_v.split(',') if v.strip()]
        
    ASSET_TYPES = []
    if len(sys.argv) >= 2 and sys.argv[1].strip():
        try:
            with open(sys.argv[1], "r", encoding="utf-8") as _f:
                _text = _f.read()
            m_assets = re.search(r"Asset types\s*:\s*(.+)", _text, flags=re.I)
            if m_assets:
                ASSET_TYPES = [a.strip().upper() for a in m_assets.group(1).split(",") if a.strip()]
        except Exception: pass

    if not ASSET_TYPES:
        ASSET_TYPES = ["INR", "EUR", "USD", "GBP", "RUB"]

    print(f"✅ Using asset types: {ASSET_TYPES}")

    env = simpy.Environment()
    sys.stdout = open(LOG_FILE, "w", encoding="utf-8")

    auth_region = config["network_setup"].get("authority_region", "West Europe")
    if auth_region not in ALL_EXCEL_REGIONS:
        print(f"⚠️ Configured Authority region '{auth_region}' not in Excel. Using fallback.")
        auth_region = "West Europe"
        
    auth = AuthorityNode(env, authority_lbl, auth_region, [])
    
    verifier_objs = []
    for lbl in verifiers_lbl:
        region = get_region_for_label(lbl)
        print(f"🌍 Node {lbl} assigned to {region}") 
        
        v = VerifierNode(env, lbl, region, authority=auth, config=config)
        v.allowed_asset_types = list(ASSET_TYPES)
        verifier_objs.append(v)
        
    for v in verifier_objs:
        v.peers = [peer for peer in verifier_objs if peer != v]
        v.peers.append(auth)
        v.fee_balances = {a: 0 for a in ASSET_TYPES}

    auth.verifiers = verifier_objs
    for v in verifier_objs:
        v.start()

    public_labels = [l for l in nodes if l not in verifiers_lbl and l != authority_lbl]
    all_publics = []
    for lbl in public_labels:
        region = get_region_for_label(lbl)
        print(f"🌍 Node {lbl} assigned to {region}")
        
        p = PublicParticipant(env, lbl, region, auth, verifier_objs, asset_types=ASSET_TYPES, config=config)
        all_publics.append(p)
        
    print("\n" + "="*60)
    print(f"{'🔎 TOPOLOGY CONFIGURATION CHECK':^60}")
    print("="*60)
    print(f"{'NODE':<15} | {'REGION ASSIGNED':<30} | {'STATUS'}")
    print("-" * 60)
    
    all_nodes = all_publics + auth.verifiers + [auth]
    for node in all_nodes:
        status = "✅ OK"
        print(f"{node.label:<15} | {node.region:<30} | {status}")
    
    print("\n" + "="*60)
    print(f"{'⏱️ LATENCY TEST (Random Sample)':^60}")
    print("="*60)
    scale = config["simulation_parameters"].get("time_scaling_ms", 200.0)
    for _ in range(5):
        n1 = random.choice(all_nodes)
        n2 = random.choice(all_nodes)
        if n1 == n2: continue

        r1, r2 = n1.region, n2.region
        latency_val = latency_map.get((r1, r2), latency_map.get((r2, r1), "N/A"))
        
        print(f"🔗 {n1.label} ({r1}) <--> {n2.label} ({r2})")
        print(f"   ➤ Excel Latency: {latency_val} ms")
        # Load scaling factor from config, default to 200 if missing
           
        # return rtt / float(scale)
        if isinstance(latency_val, (int, float)):
            print(f"   ➤ SimPy Delay:   {latency_val / scale:.4f} units")
        print("-" * 30)
    print("="*60 + "\n")

    pub_map = {p.label: p for p in all_publics}

    for lbl, nbrs in adjacency.items():
        if lbl in pub_map:
            p = pub_map[lbl]
            for u in nbrs:
                if u in pub_map: p.peers.append(pub_map[u])
                if u in verifiers_lbl:
                    v_obj = next(v for v in verifier_objs if v.label == u)
                    p.peers.append(v_obj)
                    p.is_gateway = True

    # try:
    #     networktopology.draw_topology(nodes, adjacency, authority_lbl, verifiers_lbl)
    # except Exception as e:
    #     print("⚠️ topology render failed:", e)

    genesis_state = []
    
    def _b64(b: bytes) -> str:
        return base64.b64encode(b).decode()
    
    for p in all_publics:
        for asset in p.asset_types:
            entry = {
                "id": p.anonymized_ids[asset], 
                "asset_type": asset,
                "balance": p.balances[asset],
                "nonce": 0,  
                "region": p.region,
                "role": "PUBLIC"
            }
            genesis_state.append(entry)
        
    for v in verifier_objs:
        entry = {
            "label": v.label,
            "region": v.region,
            "role": "VERIFIER",
            "balance": v.balance,
            "pubkeys": {"NODE": _b64(v.public_key_bytes)}
        }
        genesis_state.append(entry)

    leaves = [json.dumps(e, sort_keys=True) for e in genesis_state]
    genesis_merkle = compute_merkle_root(leaves)
    genesis_block = {
        'index':      0,
        'prev_hash':  '0' * 64,
        'state':      genesis_state,
        'merkle_root': genesis_merkle,
        'proposer':   'GENESIS'
    }
    gb_str = json.dumps(genesis_block, sort_keys=True).encode()
    genesis_block['block_hash'] = hashlib.sha256(gb_str).hexdigest()

    for node in all_publics + verifier_objs:
        node.blockchain.append(genesis_block)
    auth.blockchain.append(genesis_block)
    for v in verifier_objs:
        v.commit_block(genesis_block)

    print("✅ Genesis Block Created and Distributed\n")
    print(json.dumps(genesis_block, indent=4))
    print()

    def run_with_network_flush():
        yield auth.main_process
        print(f"\n[{env.now:.1f}] 🛑 Authority finished rounds. Flushing network (waiting for propagation)...")
        yield env.timeout(40) 
    
    env.run(until=env.process(run_with_network_flush()))

    print("\n=== Final Balances ===")
    for node in all_publics + auth.verifiers:
        if hasattr(node, "balances"):
            print(f"{node.label}: " + ", ".join([f"{a}={node.balances[a]}" for a in node.balances]))
        else:
            print(f"{node.label}: {node.get_balance()}")
            
    print("\n=== Verifier Fee Balances (per-asset) ===")
    for v in auth.verifiers:
        pairs = ", ".join([f"{a}={v.fee_balances.get(a,0)}" for a in ASSET_TYPES])
        print(f"{v.label}: {pairs}")
    
    # --- Print Final System Coin Balances ---
    print("\n=== Authority's Cached System Coin Balances ===")
    for label, bal in auth.verifier_system_balances.items():
        print(f"{label}: {bal:.2f} System Coins")

    print("\n🧾 Blockchain Length Summary:")
    for node in all_publics + auth.verifiers:
        print(f"{node.label}: {len(node.blockchain)} blocks")

    print("\n=== Authority’s Full Blockchain ===")
    print(json.dumps(auth.blockchain, indent=4))

if __name__ == '__main__':
    setup_simulation()
    sys.stdout.close()