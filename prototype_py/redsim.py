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

# --- Config & Latency ---

ROUNDS = config["simulation_parameters"]["rounds"]
LOG_FILE = config["simulation_parameters"]["log_file"]
TIMEOUT_MIN_AUTH = config["consensus_parameters"]["authority_timeout_range"][0]
TIMEOUT_MAX_AUTH = config["consensus_parameters"]["authority_timeout_range"][1]

LATENCY_XLSX = config["network_setup"]["latency_file_path"]
df = pd.read_excel(LATENCY_XLSX, engine='openpyxl')
long_df = df.melt(id_vars=['Source'], var_name='Target', value_name='RTT_ms')
latency_map = {
    (r.Source.strip(), r.Target.strip()): float(r.RTT_ms)
    for _, r in long_df.dropna().iterrows()
}

def get_delay(r1, r2, default=300):
    rtt = latency_map.get((r1, r2), latency_map.get((r2, r1), default))
    return rtt / 200.0  # 1 simpy unit = 200 ms

PUBLIC_REGIONS   = [r.strip() for r in df['Source'].tolist()[:10]]
VERIFIER_REGIONS = ['France Central', 'Japan East', 'South India']
AUTHORITY_REGION = config["network_setup"]["authority_region"]
all_publics = []

# --- Classes ---

class PublicParticipant:
    def __init__(self, env, label, region, authority, verifiers, asset_types, config):
        self.config = config 
        self.env = env
        self.label = label.upper()
        self.region = region
        self.authority = authority
        self.verifiers = verifiers
        self.peers = []
        self.is_gateway = False
        self.seen_messages = set()
        self.propagated_blocks = set()
        self.blockchain = []
        self.seen_signed = set()
        
        self.keys, self.balances = {}, {}
        self.asset_types = asset_types
        self.tx_locks = {a: False for a in asset_types}
        self.last_tx_refs = {a: None for a in asset_types}
        self.confirmation_events = {a: self.env.event() for a in self.asset_types}
        
        # --- NEW: Nonce Counters ---
        # Tracks the sequence number for each asset type
        self.nonces = {a: 0 for a in asset_types}
        
        # --- Threshold Setup ---
        self.s1_shares = {} # Stores User's secret half (S1)
        self.anonymized_ids = {} # Stores X(PK)
        
        for asset in asset_types:
            sk = ec.generate_private_key(ec.SECP256R1())
            pk = sk.public_key()
            pk_bytes = pk.public_bytes(encoding=Encoding.DER, format=PublicFormat.SubjectPublicKeyInfo)
            self.keys[asset] = {"sk": sk, "pk_bytes": pk_bytes}
            
            # 1. Generate Master Seed S (32 bytes)
            master_seed = os.urandom(32)
            
            # 2. Split: S1 (User) and S2 (Authority)
            s1 = master_seed[:16] 
            s2 = master_seed[16:]
            self.s1_shares[asset] = s1
            
            # 3. Compute Anonymized ID: X = HMAC(PK, MasterSeed)
            anon_id = anonymize_identity(pk_bytes, master_seed)
            self.anonymized_ids[asset] = anon_id

            balance_range = self.config["economic_parameters"]["public_balance_range"]
            self.balances[asset] = random.randint(*balance_range)
            
            # 4. Securely Register: Send S2 and Anon_ID to Authority
            self.authority.register_node(
                self.label, 
                pk_bytes, 
                self.region, 
                asset_types=[asset],
                share_s2=s2,          # <--- Send Authority's Share
                canon_id=anon_id      # <--- Register the Anonymized ID
            )

        _default = self.asset_types[0]
        self.balance = self.balances[_default]
            
        self.token_events = {a: self.env.event() for a in self.asset_types}
        self.signed_tokens = {a: None for a in self.asset_types}
        
        env.process(self.behavior())
        
    def get_balance(self):
        return self.balance
        
    def behavior(self):
        for _ in range(ROUNDS):
            yield self.env.timeout(random.uniform(1, 5))
            yield from self.transactionRequest()
            
    def transactionRequest(self, receiver=None, asset=None,
                           timeout1=TIMEOUT_MIN_AUTH, timeout2=TIMEOUT_MAX_AUTH):
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
            "PKR": base64.b64encode(self.authority.public_key_bytes).decode(),
            "asset_type": a,
            "origin": self.region,
            "toAuth": self.authority.label
        }
        data = json.dumps(rtoken, sort_keys=True).encode()
        sig  = self.keys[a]["sk"].sign(data, ec.ECDSA(hashes.SHA256()))
        rtoken["signature"] = base64.b64encode(sig).decode()

        yield from self.propagate_message(rtoken, stable_msg_id(rtoken))

        # Wait for SignedToken
        yield self.token_events[a] 
        self.token_events[a] = self.env.event() # reset
        
        token = self.signed_tokens[a]
        if not token: return

        # --- Generate Proof 1 (P1) for Tx ---
        s1 = self.s1_shares[a]
        h_p1 = hmac.HMAC(s1, hashes.SHA256())
        # Context: Label:Asset
        h_p1.update(f"{self.label}:{a}".encode()) 
        proof1 = base64.b64encode(h_p1.finalize()).decode()

        # Step 4: Form Tx
        amount_range = self.config["economic_parameters"]["transaction_amount_range"]
        fee_range = self.config["economic_parameters"]["transaction_fee_range"]
        amount = random.randint(*amount_range)
        fee = random.randint(*fee_range)

        if self.balances[a] < amount + fee:
            return
        
        # Increment Nonce locally
        self.nonces[a] += 1
        current_nonce = self.nonces[a]

        tx = {
            "type": "Tx",
            "nonce": current_nonce, # <--- NEW: Unique Sequence Number
            "PKS": self.anonymized_ids[a], # <--- USING ANON ID
            "PKR": receiver_node.anonymized_ids[a],
            "asset_type": a,
            "amount": amount,
            "fee": fee,
            "origin": self.region,
            "SignedToken": token,
            "proof1": proof1 # <--- Attaching User's Share
        }
        
        tx_data = json.dumps({
            k: tx[k] for k in ("type","nonce","PKS","PKR","asset_type","amount","fee","origin","SignedToken")
        }, sort_keys=True).encode()
        
        tx_sig  = self.keys[a]["sk"].sign(tx_data, ec.ECDSA(hashes.SHA256()))
        tx["signature"] = base64.b64encode(tx_sig).decode()

        tx_id = stable_tx_id(tx)
        self.tx_locks[a] = True
        self.last_tx_refs[a] = tx_id

        print(f"[{env.now:.1f}] {short_id(tx['PKS'])} sending {amount} {a} to {short_id(tx['PKR'])} (Nonce: {current_nonce})")
        yield from self.propagate_message(tx, tx_id)

        # Step 5: Wait for Block confirmation
        yield env.timeout(random.uniform(timeout1, timeout2))

    def receive_message(self, msg, h, delay):
        yield self.env.timeout(delay)
        yield from self.propagate_message(msg, h)

    def propagate_message(self, msg, h):
        yield self.env.timeout(0)
        message = dict(msg)

        if message.get("type") != "Block":
            if h in self.seen_messages: return
            self.seen_messages.add(h)
        if message.get("type") == "SignedToken":
            st_hash = stable_msg_id(message)
            if st_hash in self.seen_signed: return
            self.seen_signed.add(st_hash)
        if message.get("type") == "Block":
            if h in self.propagated_blocks: return
            self.propagated_blocks.add(h)

        t = message["type"]
        if t == "RToken" and message["from"] != self.label:
            pass 
        elif t == "Block":
            block = message["block"]
            self.blockchain.append(block)

            # --- Balance Update Logic via Anon ID (Scanning TXs directly) ---
            # Pre-calculate my Anon IDs for fast lookup
            my_anon_ids = {self.anonymized_ids[a]: a for a in self.asset_types}

            for tx in block.get("txs", []):
                tx_asset = tx.get("asset_type")
                amount = tx.get("amount", 0)
                fee = tx.get("fee", 0)
                sender = tx.get("PKS")
                receiver = tx.get("PKR")

                # 1. If I am the Receiver (Credit)
                if receiver in my_anon_ids:
                    my_asset = my_anon_ids[receiver]
                    if my_asset == tx_asset:
                        self.balances[my_asset] += amount
                        # print(f"[{self.env.now:.1f}] {self.label} 💰 +{amount} {my_asset} (recv)")
                
                # 2. If I am the Sender (Debit Amount + Fee)
                if sender in my_anon_ids:
                    my_asset = my_anon_ids[sender]
                    if my_asset == tx_asset:
                        self.balances[my_asset] -= (amount + fee)
                        # print(f"[{self.env.now:.1f}] {self.label} 🏷️ -{amount+fee} {my_asset} (send)")
                        
            # Unlock assets
            committed_ids = { stable_tx_id(tx) for tx in block.get("txs", []) }
            for a in self.asset_types:
                last_id = self.last_tx_refs.get(a)
                if self.tx_locks.get(a) and last_id is not None and last_id in committed_ids:
                    self.tx_locks[a] = False
                    self.last_tx_refs[a] = None
            return

        elif t == "SignedToken":
            # [PRIVACY FIX]: Check if 'to' matches any of my AnonIDs
            target = msg.get("to")
            my_anon_ids = set(self.anonymized_ids.values())
            
            if target not in my_anon_ids:
                return # Not for me

            # (Signature check logic remains same)
            sig_b64 = msg.get("signature", "")
            try:
                sig = base64.b64decode(sig_b64)
            except: return

            data = json.dumps({
                "type":       message["type"],
                "to":         message["to"],
                "from":       message["from"],
                "asset_type": message.get("asset_type"),
                "PKS":        message.get("PKS")
            }, sort_keys=True).encode()
            auth_pub = serialization.load_der_public_key(self.authority.public_key_bytes)
            try:
                auth_pub.verify(sig, data, ec.ECDSA(hashes.SHA256()))
                # print(f"[{self.env.now:.1f}] Public {self.label} ✅ verified SignedToken")
            except InvalidSignature:
                print(f"[{self.env.now:.1f}] Public {self.label} ❌ invalid SignedToken signature")
                return
            
            asset = message.get("asset_type")
            if asset in self.asset_types:
                self.signed_tokens[asset] = message.copy()
                if not self.token_events[asset].triggered:
                    self.token_events[asset].succeed()
            return

        for peer in self.peers:
            delay = get_delay(self.region, peer.region)
            def _forward(p=peer, msg_copy=message, d=delay):
                yield self.env.timeout(d)
                yield from p.receive_message(msg_copy, h, d)
            self.env.process(_forward())

        if self.is_gateway and message["type"] in ("RToken", "Tx"):
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
        
        tx_range = config["consensus_parameters"]["verifier_tx_target_range"]
        self.target_tx_count = random.randint(*tx_range)
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
        
    def get_balance(self):
        return self.balance
                    
    def start(self):
        self.block_event = self.env.event()
        self.env.process(self.consensus_loop())

    def consensus_loop(self):
        print(f"[{self.env.now:.1f}] Verifier {self.label} entering consensus_loop")
        for round_no in range(1, ROUNDS + 1):
            self.proposed    = False
            self.block_event = self.env.event()
            yield self.block_event

    def validate_tx(self, tx):
        """
        ASYNC generator. 
        1. Checks P1+P2 threshold.
        2. Checks Origin/Policy/Fee.
        3. Checks Solvency by scanning chain for PKS (Anon ID).
        """
        asset = tx.get("asset_type")
        claimed_TO = tx.get("origin")
        sender_anon_id = tx.get("PKS")
        proof1 = tx.get("proof1")
        tx_nonce = tx.get("nonce", 0) # <--- NEW
        
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

        # --- 3. Solvency Check (Scanning Chain via PKS) ---
        genesis = self.blockchain[0]
        balance = 0
        last_nonce = 0 # <--- NEW: Track highest nonce seen on-chain
        found_genesis = False
        
        for entry in genesis["state"]:
            if entry.get("role") == "PUBLIC":
                if entry.get("id") == sender_anon_id:
                     balance = entry.get("balance", 0)
                     found_genesis = True
                     break
        
        if not found_genesis: pass 

        for block in self.blockchain:
            if block["index"] == 0: continue 

            for past_tx in block.get("txs", []):
                if past_tx.get("asset_type") != asset: continue
                # Debit
                if past_tx.get("PKS") == sender_anon_id:
                    balance -= (past_tx.get("amount", 0) + past_tx.get("fee", 0))
                    # Track Highest Nonce
                    n = past_tx.get("nonce", 0)
                    if n > last_nonce:
                        last_nonce = n
                # Credit
                if past_tx.get("PKR") == sender_anon_id:
                    balance += past_tx.get("amount", 0)

        for pending in self.pending_txs:
            if pending.get("PKS") == sender_anon_id and pending.get("asset_type") == asset:
                balance -= (pending.get("amount", 0) + pending.get("fee", 0))

                # Check for Nonce Collision in Pending
                n = pending.get("nonce", 0)
                if n == tx_nonce:
                    print(f"[{self.env.now:.1f}] Verifier {self.label} ❌ Nonce {tx_nonce} already in pending")
                    return False
                if n > last_nonce:
                    last_nonce = n
                    
        if balance < tx.get("amount", 0) + tx.get("fee", 0):
            print(f"[{self.env.now:.1f}] Verifier {self.label} ❌ insufficient {asset} funds (Bal: {balance})")
            return False
        
        # Nonce Sequence Check
        # We expect strictly Last + 1
        if tx_nonce != last_nonce + 1:
            print(f"[{self.env.now:.1f}] Verifier {self.label} ❌ Invalid Nonce: Expected {last_nonce + 1}, Got {tx_nonce}")
            return False

        return True


    def receive_message(self, msg, h, delay):
        """Handle incoming messages."""
        yield self.env.timeout(delay)

        t = msg.get("type")

        # SignedToken handling
        if t == "SignedToken":
            h = stable_msg_id(msg)
            if h in self.seen_signed: return
            self.seen_signed.add(h)
            # print(f"[{self.env.now:.1f}] Verifier {self.label} ➜ gossip SignedToken")
            yield from self.propagate_message(msg, h)
            return

        if h in self.seen_messages: return
        self.seen_messages.add(h)

        if t == "RToken":
            # RToken Signature Verification
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
                "origin": msg["origin"]  # <--- ADD THIS LINE
            }
            d = get_delay(self.region, self.authority.region)
            self.env.process(self.authority.receive_rtoken(req, d))
            return


        if t == "Tx":
            tx = msg
            
            # 1. Verify SignedToken sig
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

            # 2. Verify Tx Signature
            resolved = self.authority.pkindex.get(tx["PKS"])
            if not resolved: return
            sender_label = resolved["label"]
            asset = tx["asset_type"]
            
            raw_pk_bytes = self.authority.OMT[sender_label]["pubkeys"][asset]
            sender_pub = serialization.load_der_public_key(raw_pk_bytes)

            tx_data = json.dumps({
                k: msg[k] for k in ("type","PKS","PKR","asset_type","amount","fee","origin","SignedToken")
            }, sort_keys=True).encode()
            try:
                tx_sig = base64.b64decode(tx.get("signature", ""))
                sender_pub.verify(tx_sig, tx_data, ec.ECDSA(hashes.SHA256()))
            except InvalidSignature:
                print(f"[{self.env.now:.1f}] Verifier {self.label} ❌ Invalid Tx Signature")
                return
            
            # --- ASYNC VALIDATION CALL ---
            is_valid = yield from self.validate_tx(tx)
            
            if not is_valid:
                return

            tx_id = stable_tx_id(tx)
            if tx_id in self.authority.finalized_tx_ids:
                return
            
            for p in self.pending_txs:
                if p["PKS"] == tx["PKS"] and p["asset_type"] == tx["asset_type"]:
                     print(f"[{self.env.now:.1f}] Verifier {self.label} ❌ double-spend in pending")
                     return

            self.pending_txs.append(tx)
            self.accumulated_fee += tx.get('fee', 0)
            
            if (not self.proposed) and (len(self.pending_txs) >= self.target_tx_count):
                new_block = self.createBlock(list(self.pending_txs))
                self.proposed = True
                self.last_proposed_block = new_block
                # print(f"[{self.env.now:.1f}] Verifier {self.label} ➜ proposing block {new_block['index']}")
                self.env.process(self.authority.receive_candidate_block(self.label, new_block))
                self.pending_txs = []
                self.accumulated_fee = 0
            return

        if t == "Block":
            block = msg.get("block")
            block_msg = {"type":"Block", "block":block}
            h_block   = stable_block_id(block_msg)
            for pub in self.get_connected_peers():
                d = get_delay(self.region, pub.region)
                self.env.process(pub.receive_message(block_msg, h_block, d))
            # [CRITICAL FIX]: Update local blockchain first
            self.blockchain.append(block)

            # [CRITICAL FIX]: Trigger Round End ONLY after receiving the block
            if self.block_event and not self.block_event.triggered:
                self.block_event.succeed()
                # Yield tiny amount to let consensus_loop reset 'self.proposed'
                yield self.env.timeout(0.01)
            yield from self.process_finalized_block(block)

    def propagate_message(self, msg, h):
        yield self.env.timeout(0)
        if msg.get("type") != "Block":
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
        prev_hash = self.compute_last_block_hash()
        index = (self.blockchain[-1]["index"] + 1) if self.blockchain else 1
        tx_leaves = [json.dumps(tx, sort_keys=True) for tx in txs_valid]
        merkle = compute_merkle_root(tx_leaves)
        total_fees = sum(tx.get("fee", 0) for tx in txs_valid)

        return {
            "index":       index,
            "prev_hash":   prev_hash,
            "txs":         list(txs_valid),
            "merkle_root": merkle,
            "proposer":    self.label,
            "total_fees":  total_fees,
        }

    def get_connected_peers(self):
        return [p for p in all_publics if self in p.peers]

    def process_finalized_block(self, block):
        # self.blockchain.append(block)
        
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
                if stable_tx_id(tx) not in committed
            ]
            
            new_txs = list(leftovers)
            
            while (len(new_txs) < self.target_tx_count) and self.pending_txs:
                tx = self.pending_txs.pop(0)
                if stable_tx_id(tx) not in committed:
                    is_valid = yield from self.validate_tx(tx)
                    if is_valid:
                        new_txs.append(tx)

            if new_txs and len(new_txs) >= self.target_tx_count:
                candidate = self.createBlock(new_txs)
                self.last_proposed_block = candidate
                # [FIX]: Mark as proposed so we don't duplicate proposal in receive_message
                self.proposed = True
                self.env.process(
                    self.authority.receive_candidate_block(self.label, candidate)
                )
                print(f"[{self.env.now:.1f}] {self.label} 📦 Proposed new block {candidate['index']}")

            self.pending_txs = [
                tx for tx in self.pending_txs
                if stable_tx_id(tx) not in self.authority.finalized_tx_ids
            ]

class AuthorityNode:
    def __init__(self, env, label, region, verifiers):
        self.env              = env
        self.label            = label.upper()
        self.region           = region
        self.verifiers        = verifiers
        self.finalized_tx_ids = set()
        self.pubkeys = {} 
        self.seen_rtokens = set()
        self.received_round = set()
        self.pkindex = {}
        
        self._sk = ec.generate_private_key(ec.SECP256R1())
        self.public_key_bytes = self._sk.public_key().public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        )
        
        self.OMT       = {}    
        self.ALT       = []    

        self.candidate_blocks = {}
        self.current_index    = 1 
        self.blockchain       = []     

        env.process(self.consensus_loop())
        
    def select_block(self):
        labels    = list(self.candidate_blocks.keys())
        proposals = self.candidate_blocks
        tx_counts = {lbl: len(proposals[lbl]["txs"]) for lbl in labels}
        max_tx    = max(tx_counts.values(), default=1)
        T         = {lbl: tx_counts[lbl] / max_tx for lbl in labels}

        balances = {v.label: v.get_balance() for v in self.verifiers}
        min_b, max_b = min(balances.values()), max(balances.values())
        span = (max_b - min_b) or 1
        G    = {lbl: (max_b - balances[lbl]) / span for lbl in labels}

        S = {lbl: config["consensus_parameters"]["block_selection_weights"]["alpha_generosity"] * G[lbl] + config["consensus_parameters"]["block_selection_weights"]["beta_throughput"] * T[lbl] for lbl in labels}

        best = sorted(labels, key=lambda lbl: (-S[lbl], lbl))
        return best[0], proposals[best[0]]
        
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

            # --- Store S2 in ALT ---
            self.ALT.append({
                "public_key":  public_key_bytes,
                "asset_type":  asset,
                "region":      region,
                "active":      True,
                "share_s2":    share_s2,  
                "canon_id":    canon_id
            })
            
        print(f"[{self.env.now:.1f}] Authority ✅ registered {label} with Threshold S2 for {asset_types}")

    def request_proof_share(self, anon_id, asset_type):
        """Called by Verifiers. Returns Signed P2 if active."""
        # 1. Lookup in ALT
        s2 = None
        user_label = None
        for entry in self.ALT:
            if entry["canon_id"] == anon_id and entry["asset_type"] == asset_type:
                if not entry["active"]:
                    return None # REDACTED / Inactive
                s2 = entry["share_s2"]
                user_label = self.pkindex.get(anon_id, {}).get("label")
                break
        
        if not s2: return None

        # 2. Generate P2
        h_p2 = hmac.HMAC(s2, hashes.SHA256())
        h_p2.update(f"{user_label}:{asset_type}".encode()) 
        proof2 = base64.b64encode(h_p2.finalize()).decode()

        # 3. Sign P2
        proof2_sig = self._sk.sign(proof2.encode(), ec.ECDSA(hashes.SHA256()))
        
        return {
            "proof2": proof2,
            "signature": base64.b64encode(proof2_sig).decode()
        }

    def receive_rtoken(self, request, delay):
        yield self.env.timeout(delay)
        sender = request["sender"]
        asset  = request["asset_type"]
        
        # [REGION CHECK FIX]: Ensure sender is actually in the claimed region
        registered_region = self.OMT.get(sender, {}).get("region")
        if registered_region != request["origin"]:
            print(f"[{self.env.now:.1f}] Authority ❌ REJECTED RToken from {sender}: Region Mismatch ({registered_region} vs {request['origin']})")
            return

        rhash = f"rtok|{sender}|{self.current_index}|{asset}"
        if rhash in self.seen_rtokens: return
        self.seen_rtokens.add(rhash)
        
        # Issue Standard SignedToken
        st = {
            'type': 'SignedToken', 
            'to': request["PKS"], # <--- CHANGED: Uses AnonID
            'from': self.label, 
            'asset_type': asset, 
            'PKS': request["PKS"], 
        }
        
        data = json.dumps(st, sort_keys=True).encode()
        sig  = self._sk.sign(data, ec.ECDSA(hashes.SHA256()))
        st["signature"] = base64.b64encode(sig).decode()

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
        print(f"[{self.env.now:.1f}] Authority received candidate block {block['index']} from {block['proposer']}")    

    def consensus_loop(self):
        print(f"[{self.env.now:.1f}] Authority entering consensus_loop")
        for current_round in range(1, ROUNDS + 1):
            self.candidate_blocks = {
                lab: blk for lab, blk in self.candidate_blocks.items()
                if blk.get("index") == self.current_index
            }
            self.received_round.clear()

            target = len(self.verifiers)
            timeout = random.uniform(TIMEOUT_MIN_AUTH, TIMEOUT_MAX_AUTH)
            deadline = self.env.now + timeout
            while len(self.candidate_blocks) < target and self.env.now < deadline:
                yield self.env.timeout(1)
            
            if not self.candidate_blocks:
                print(f"[{self.env.now:.1f}] Authority ⚠️ No candidate blocks. Skipping round.")
                yield self.env.timeout(5)
                continue

            chosen_label, chosen_block = self.select_block()
            print(f"[{self.env.now:.1f}] Authority 🔨 selected block {chosen_block['index']} from {chosen_label}")

            tx_leaves = [json.dumps(tx, sort_keys=True) for tx in chosen_block['txs']]
            merkle = compute_merkle_root(tx_leaves)
            
            # --- ACKS REMOVED HERE ---
            # Instead of generating acks, we just calculate fee totals for the block header
            
            fee_totals = {}
            for tx in chosen_block["txs"]:
                a = tx.get("asset_type")
                fee_totals[a] = fee_totals.get(a, 0) + tx.get("fee", 0)

            block = {
                "index":       self.current_index,
                "prev_hash":   self.blockchain[-1]["block_hash"],
                "txs":         chosen_block["txs"],
                "merkle_root": merkle,
                "proposer":    chosen_block["proposer"],
                "total_fees":  sum(fee_totals.values()),
                "fee_totals":  fee_totals
                # "acks": acks  <-- REMOVED
            }
            block_bytes = json.dumps(block, sort_keys=True).encode()
            block["block_hash"] = hashlib.sha256(block_bytes).hexdigest()

            for tx in block["txs"]:
                self.finalized_tx_ids.add(stable_tx_id(tx))
            self.distribute_block(block)
            # for v in self.verifiers:
            #     v.receive_final_block()
            self.blockchain.append(block)
            self.current_index += 1

        self.seen_rtokens.clear()

    def distribute_block(self, block):
        msg = {'type': 'Block', 'block': block}
        h   = stable_block_id(msg)
        for v in self.verifiers:
            d = get_delay(self.region, v.region)
            self.env.process(v.receive_message(msg, h, d))

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

    auth = AuthorityNode(env, authority_lbl, AUTHORITY_REGION, [])

    verifier_objs = []
    for i, lbl in enumerate(verifiers_lbl):
        region = VERIFIER_REGIONS[i % len(VERIFIER_REGIONS)]
        v = VerifierNode(env, lbl, region, authority=auth, config=config)
        v.allowed_asset_types = list(ASSET_TYPES)
        v.fee_balances = {a: 0 for a in ASSET_TYPES}
        verifier_objs.append(v)

    auth.verifiers = verifier_objs
    for v in verifier_objs:
        v.start()

    public_labels = [l for l in nodes if l not in verifiers_lbl and l != authority_lbl]
    all_publics = []
    for i, lbl in enumerate(public_labels):
        region = PUBLIC_REGIONS[i % len(PUBLIC_REGIONS)]
        p = PublicParticipant(env, lbl, region, auth, verifier_objs, asset_types=ASSET_TYPES, config=config)
        all_publics.append(p)

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

    try:
        networktopology.draw_topology(nodes, adjacency, authority_lbl, verifiers_lbl)
    except Exception as e:
        print("⚠️ topology render failed:", e)

    # --- 2. Genesis Block Anonymization ---
    genesis_state = []
    
    def _b64(b: bytes) -> str:
        return base64.b64encode(b).decode()
    
    for p in all_publics:
        # Create separate entries for each asset using Anon ID, NOT label
        for asset in p.asset_types:
            entry = {
                "id": p.anonymized_ids[asset], # The Key for lookup
                "asset_type": asset,
                "balance": p.balances[asset],
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

    print("✅ Genesis Block Created and Distributed\n")
    print(json.dumps(genesis_block, indent=4))
    print()

    env.run()

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

    print("\n🧾 Blockchain Length Summary:")
    for node in all_publics + auth.verifiers:
        print(f"{node.label}: {len(node.blockchain)} blocks")

    # --- 4. Print Entire Blockchain ---
    print("\n=== Authority’s Full Blockchain ===")
    print(json.dumps(auth.blockchain, indent=4))

if __name__ == '__main__':
    setup_simulation()
    sys.stdout.close()