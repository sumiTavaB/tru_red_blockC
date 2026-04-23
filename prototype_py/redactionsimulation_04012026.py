import simpy
import random
import pandas as pd
import networktopology  # visualize after simulation
import json
import sys
import hashlib
import base64
import re
import hashlib, json
import os

# at top of file, add
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

def anonymize_identity(pk_bytes: bytes, seed: bytes) -> str:
    """
    Implements X(PK, seed) from the paper.
    Returns a hex string representing the pseudonym.
    """
    h = hmac.HMAC(seed, hashes.SHA256())
    h.update(pk_bytes)
    return base64.b64encode(h.finalize()).decode()

def stable_tx_id(tx: dict) -> str:
    # exclude non-deterministic fields if any (e.g., locally added flags)
    return hashlib.md5(json.dumps(tx, sort_keys=True).encode()).hexdigest()

def stable_block_id(block: dict) -> str:
    # if you want stable message ids for blocks too
    return hashlib.md5(json.dumps(block, sort_keys=True).encode()).hexdigest()

def stable_msg_id(msg: dict) -> str:
    return hashlib.md5(json.dumps(msg, sort_keys=True).encode()).hexdigest()



#helper: compute a Merkle root (MD5) over a list of string leaves
def compute_merkle_root(leaves):
# first level: MD5 of each leaf string
    level = [hashlib.md5(leaf.encode()).hexdigest() for leaf in leaves]
    if not level:
        return hashlib.md5(b"").hexdigest()
# build up until one hash remains
    while len(level) > 1:
        if len(level) % 2 == 1:
            level.append(level[-1])
        next_level = []
        for i in range(0, len(level), 2):
            combined = level[i] + level[i+1]
            next_level.append(hashlib.md5(combined.encode()).hexdigest())
        level = next_level
    return level[0]

ROUNDS = config["simulation_parameters"]["rounds"]
LOG_FILE = config["simulation_parameters"]["log_file"]
# in ms (or your sim‐time units)
TIMEOUT_MIN_AUTH = config["consensus_parameters"]["authority_timeout_range"][0]
TIMEOUT_MAX_AUTH = config["consensus_parameters"]["authority_timeout_range"][1]


# -------------------------------
# Load Inter-Region Latency Matrix
# -------------------------------
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

# -------------------------------
# Region pools
# -------------------------------
PUBLIC_REGIONS   = [r.strip() for r in df['Source'].tolist()[:10]]
VERIFIER_REGIONS = ['France Central', 'Japan East', 'South India']
AUTHORITY_REGION = config["network_setup"]["authority_region"]

# Global tables & storage
all_publics = []

# -------------------------------
# Entity classes
# -------------------------------

class PublicParticipant:
    def __init__(self, env, label, region, authority, verifiers, asset_types, config):
        self.config = config # Store config for later use
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
        # self.balance = random.randint(9000, 15000)
        self.seen_signed = set()
        
        # Initialize keypair and balance per asset        
        # asset_types=["INR","RUB","EUR","USD","GBP"]
        self.keys, self.balances = {}, {}
        self.asset_types = asset_types
        self.tx_locks = {a: False for a in asset_types}
        self.last_tx_refs = {a: None for a in asset_types}
        self.confirmation_events = {a: self.env.event() for a in self.asset_types}
        
        # # Temporary bridge so legacy behavior() runs until Step 3 is applied:
        # _default = self.asset_types[0]
        # self._sk = self.keys[_default]["sk"]
        # self.public_key_bytes = self.keys[_default]["pk_bytes"]
        # self.balance = self.balances[_default]


        
        for asset in asset_types:
            sk = ec.generate_private_key(ec.SECP256R1())
            pk = sk.public_key()
            pk_bytes = pk.public_bytes(encoding=Encoding.DER, format=PublicFormat.SubjectPublicKeyInfo)
            self.keys[asset] = {"sk": sk, "pk_bytes": pk_bytes}
            # self.balances[asset] = random.randint(9000, 15000)
            balance_range = self.config["economic_parameters"]["public_balance_range"]
            self.balances[asset] = random.randint(*balance_range)
            self.authority.register_node(self.label, pk_bytes, self.region, asset_types=[asset])
            
        # === STEP 3: per-asset control plane ===
        # one token event per asset, one cached SignedToken per asset
        self.token_events = {a: self.env.event() for a in self.asset_types}
        self.signed_tokens = {a: None for a in self.asset_types}
        # serialization: only one in-flight tx per asset
        self.tx_locks = {a: False for a in self.asset_types}
        # remember last tx-id we initiated per asset, to unlock when committed
        self.last_tx_refs = {a: None for a in self.asset_types}

            
        _default = self.asset_types[0]
        # self._sk = self.keys[_default]["sk"]
        # self.public_key_bytes = self.keys[_default]["pk_bytes"]
        self.balance = self.balances[_default]
        # ensure Authority’s pubkeys[label] matches the default asset key used by legacy Tx path
        # self.authority.pubkeys[self.label] = self.public_key_bytes


        self.signed_token = None
        # Events reset each round
        # self.token_event = env.event()
        # self.block_event = env.event()

        # Start behavior process
        env.process(self.behavior())
        
    def get_balance(self):
        return self.balance
        
    def behavior(self):
        for _ in range(ROUNDS):  # limit to 10 rounds
            yield self.env.timeout(random.uniform(1, 5))
            yield from self.transactionRequest()
            
    def transactionRequest(self, receiver=None, asset=None,
                           timeout1=TIMEOUT_MIN_AUTH, timeout2=TIMEOUT_MAX_AUTH):
        """
        Implements Algorithm 1 (transactionRequest):
        - Request token from Authority
        - Form and sign Tx
        - Gossip to verifiers
        - Await confirmation/block
        """
        env = self.env

        # ---------------------------
        # Step 1: Select asset & peer
        # ---------------------------
        if asset is None:
            free_assets = [a for a in self.asset_types if not self.tx_locks[a]]
            if not free_assets:
                return  # no free assets available
            a = random.choice(free_assets)
        else:
            a = asset

        if receiver is None:
            candidates = [p for p in all_publics if p.label != self.label]
            if not candidates:
                return
            receiver_node = random.choice(candidates)
        else:
            receiver_node = receiver

        # ---------------------------
        # Step 2: Construct RToken
        # ---------------------------
        pks_b64 = base64.b64encode(self.keys[a]["pk_bytes"]).decode()
        rtoken = {
            "type": "RToken",
            "from": self.label,
            "PKS": pks_b64,
            "PKR": base64.b64encode(self.authority.public_key_bytes).decode(),
            "asset_type": a,
            "origin": self.region,
            "toAuth": self.authority.label
        }
        data = json.dumps(rtoken, sort_keys=True).encode()
        sig  = self.keys[a]["sk"].sign(data, ec.ECDSA(hashes.SHA256()))
        rtoken["signature"] = base64.b64encode(sig).decode()

        print(f"[{env.now:.1f}] {self.label} → gossip RToken ({a})")
        yield from self.propagate_message(rtoken, stable_msg_id(rtoken))

        # ---------------------------
        # Step 3: Wait for SignedToken
        # ---------------------------
        # Correctly wait for the token event for this specific asset to be triggered
        yield self.token_events[a]

        # ---------------------------
        # Step 4: Form Tx
        # ---------------------------
        receiver_pkr = base64.b64encode(receiver_node.keys[a]["pk_bytes"]).decode()
        amount_range = self.config["economic_parameters"]["transaction_amount_range"]
        fee_range = self.config["economic_parameters"]["transaction_fee_range"]
        amount = random.randint(*amount_range)
        fee = random.randint(*fee_range)

        if self.balances[a] < amount + fee:
            print(f"[{env.now:.1f}] {self.label} ⚠ insufficient {a} funds, skipping Tx")
            return

        tx = {
            "type": "Tx",
            "PKS": pks_b64,
            "PKR": receiver_pkr,
            "asset_type": a,
            "amount": amount,
            "fee": fee,
            "origin": self.region,
            "SignedToken": self.signed_tokens[a].copy()
        }
        
        # Step 2: Create the data to be signed from the dictionary
        tx_data = json.dumps({
            k: tx[k] for k in ("type","PKS","PKR","asset_type","amount","fee","origin","SignedToken")
        }, sort_keys=True).encode()
        
        # Step 3: Calculate the signature
        tx_sig  = self.keys[a]["sk"].sign(tx_data, ec.ECDSA(hashes.SHA256()))
        
        # Step 4: Add the final signature to the transaction object
        tx["signature"] = base64.b64encode(tx_sig).decode()

        # Step 5: NOW that the transaction is complete, calculate its ID and activate the lock
        tx_id = stable_tx_id(tx)
        self.tx_locks[a] = True
        self.last_tx_refs[a] = tx_id

        print(f"[{env.now:.1f}] {self.label} → gossip Tx ({a}) amount={amount}, fee={fee}")
        yield from self.propagate_message(tx, tx_id)

        # ---------------------------
        # Step 5: Wait for Block confirmation
        # ---------------------------
        yield env.timeout(random.uniform(timeout1, timeout2))
        # (balances + unlocks are updated in receive_message when Block arrives)
        print(f"[{env.now:.1f}] {self.label} ✅ finished Algorithm 1 flow for asset={a}")

    # def single_activity_round(self):
    #     # pick any asset that is not locked
    #     free_assets = [a for a in self.asset_types if not self.tx_locks[a]]
    #     if not free_assets:
    #         return
    #     asset = random.choice(free_assets)

    #     # 1) request a SignedToken for this asset if we don't have a fresh one
    #     if self.signed_tokens.get(asset) is None:
    #         pks_b64 = base64.b64encode(self.keys[asset]["pk_bytes"]).decode()
    #         rtoken = {
    #             "type":       "RToken",
    #             "from":       self.label,
    #             "PKS":        pks_b64,  # sender's ASSET key
    #             "PKR":        base64.b64encode(self.authority.public_key_bytes).decode(),
    #             "asset_type": asset,
    #             "origin":     self.region,
    #             "toAuth":     self.authority.label
    #         }
    #         data = json.dumps(rtoken, sort_keys=True).encode()
    #         sig  = self.keys[asset]["sk"].sign(data, ec.ECDSA(hashes.SHA256()))
    #         rtoken["signature"] = base64.b64encode(sig).decode()

    #         print(f"[{self.env.now:.1f}] Public {self.label} ➜ gossip RToken ({asset})")
    #         yield from self.propagate_message(rtoken, stable_msg_id(rtoken))

    #         if not self.token_events[asset].triggered:
    #             yield self.token_events[asset]

    #     if self.signed_tokens.get(asset) is None:
    #         return  # still no token; bail

    #     # 2) craft a Tx for this asset (only if still free)
    #     if self.tx_locks[asset]:
    #         return

    #     candidates = [p for p in all_publics if p.label != self.label]
    #     if not candidates:
    #         return
    #     receiver = random.choice(candidates)

    #     pks_b64 = base64.b64encode(self.keys[asset]["pk_bytes"]).decode()
    #     pkr_b64 = base64.b64encode(receiver.keys[asset]["pk_bytes"]).decode()

    #     amount, fee = random.randint(10, 100), random.randint(5, 15)
    #     if self.balances[asset] < amount + fee:
    #         print(f"[{self.env.now:.1f}] Public {self.label} ⚠️ insufficient {asset} balance, skipping Tx")
    #         return

    #     tx = {
    #         "type":        "Tx",
    #         "PKS":         pks_b64,
    #         "PKR":         pkr_b64,
    #         "asset_type":  asset,
    #         "amount":      amount,
    #         "fee":         fee,
    #         "origin":      self.region,
    #         "SignedToken": self.signed_tokens[asset].copy()
    #     }

    #     tx_data = json.dumps({
    #         k: tx[k] for k in ("type","PKS","PKR","asset_type","amount","fee","origin","SignedToken")
    #     }, sort_keys=True).encode()
    #     tx_sig  = self.keys[asset]["sk"].sign(tx_data, ec.ECDSA(hashes.SHA256()))
    #     tx["signature"] = base64.b64encode(tx_sig).decode()

    #     tx_id = stable_tx_id(tx)
    #     self.tx_locks[asset] = True
    #     self.last_tx_refs[asset] = tx_id
        
    #     if self.signed_tokens.get(asset) is None:
    #         print(f"[{self.env.now:.1f}] Public {self.label} ❌ did not receive SignedToken for {asset}")
    #         return

    #     print(f"[{self.env.now:.1f}] Public {self.label} ➜ gossip Tx ({asset}) amount={amount} fee={fee}")
    #     yield from self.propagate_message(tx, tx_id)

    def receive_message(self, msg, h, delay):
        # Entry point: respect link delay then handle
        yield self.env.timeout(delay)
        yield from self.propagate_message(msg, h)

    def propagate_message(self, msg, h):
        """
        Flood the full signed message to peers and, if gateway, to verifiers.
        Ensures the signature field (and everything else) is preserved.
        """
        # 0) zero‐delay to allow all same‐timestamp events to interleave
        yield self.env.timeout(0)

        # Work on a shallow copy so we never mutate the caller’s dict
        message = dict(msg)

        # 1) Dedupe non‐Block messages
        if message.get("type") != "Block":
            if h in self.seen_messages:
                return
            self.seen_messages.add(h)
            
        # 1b) Dedupe SignedTokens network‐wide at the public
        if message.get("type") == "SignedToken":
            st_hash = stable_msg_id(message)
            if st_hash in self.seen_signed:
                return                # drop duplicates immediately
            self.seen_signed.add(st_hash)

        # Dedupe Block floods
        if message.get("type") == "Block":
            if h in self.propagated_blocks:
                return
            self.propagated_blocks.add(h)

        # 2) Type‐specific handling
        t = message["type"]
        if t == "RToken" and message["from"] != self.label:
            print(f"[{self.env.now:.1f}] Public {self.label} received RToken of Public {message['from']}")
        elif t == "Block":
            block = message["block"]
            print(f"[{self.env.now:.1f}] Public {self.label} 🔔 got Block {block['index']}")
            self.blockchain.append(block)

            # my PKS per asset
            my_pks = {a: base64.b64encode(self.keys[a]["pk_bytes"]).decode() for a in self.asset_types}

            # credit/debit amounts from ACKs (per asset)
            for ack in block.get("acks", []):
                a = ack.get("asset_type")
                if a in my_pks:
                    if ack["to"] == my_pks[a]:
                        self.balances[a] += ack["amount"]
                        print(f"[{self.env.now:.1f}] Public {self.label} 💰 +{ack['amount']} {a} (recv)")
                    if ack["from"] == my_pks[a]:
                        self.balances[a] -= ack["amount"]
                        print(f"[{self.env.now:.1f}] Public {self.label} 🏷️ -{ack['amount']} {a} (send)")
                        
            # subtract fees for our sent txs (per asset)
            for tx in block.get("txs", []):
                a = tx.get("asset_type")
                if a in my_pks and tx.get("PKS") == my_pks[a]:
                    fee = tx.get("fee", 0)
                    if fee:
                        self.balances[a] -= fee
                        print(f"[{self.env.now:.1f}] Public {self.label} 🧾 -{fee} {a} (fee)")
                        
            # unlock assets whose last tx got committed
            committed_ids = { stable_tx_id(tx) for tx in block.get("txs", []) }
            for a in self.asset_types:
                last_id = self.last_tx_refs.get(a)
                if self.tx_locks.get(a) and last_id is not None and last_id in committed_ids:
                    self.tx_locks[a] = False
                    self.last_tx_refs[a] = None
                    print(f"[{self.env.now:.1f}] Public {self.label} ✅ {a} unlocked (tx committed)")
            return

        elif t == "SignedToken" and message["to"] == self.label:
            sig_b64 = message.get("signature", "")
            try:
                sig = base64.b64decode(sig_b64)
            except Exception as e:
                print(f"[{self.env.now:.1f}] Public {self.label} ❌ invalid SignedToken base64: {e}")
                return

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
                print(f"[{self.env.now:.1f}] Public {self.label} ✅ verified SignedToken")
            except InvalidSignature:
                print(f"[{self.env.now:.1f}] Public {self.label} ❌ invalid SignedToken signature")
                return
            
            asset = message.get("asset_type")
            if asset in self.asset_types:
                self.signed_tokens[asset] = message.copy()
                if not self.token_events[asset].triggered:
                    self.token_events[asset].succeed()
                # kick off a tx now that we hold a valid token
                # if not self.tx_locks.get(asset, False):
                    # self.env.process(self.transactionRequest(asset=asset))
            return

        # 3) Gossip to ALL peers (using our copy)
        for peer in self.peers:
            delay = get_delay(self.region, peer.region)
            def _forward(p=peer, msg_copy=message, d=delay):
                yield self.env.timeout(d)
                yield from p.receive_message(msg_copy, h, d)
            self.env.process(_forward())

        # 4) If this node is a gateway, also forward RToken/Tx to each Verifier
        if self.is_gateway and message["type"] in ("RToken", "Tx"):
            for peer in self.peers:
                if isinstance(peer, VerifierNode):
                    d = get_delay(self.region, peer.region)
                    # again, forward the same `message` copy (with signature intact)
                    self.env.process(peer.receive_message(message, h, d))

    def receive_signed_token(self):
        if not self.token_event.triggered:
            self.token_event.succeed()

    def receive_final_block(self):
        if not self.block_event.triggered:
            self.block_event.succeed()


class VerifierNode:
    def __init__(self, env, label, region, authority=None, target_fee=None, config=None):
        self.env                = env
        self.label              = label.upper()
        self.region             = region
        self.authority          = authority  # will be set after instantiation
        self.allowed_asset_types = ["INR","USD","EUR","GBP","RUB"]
        self.seen_signed = set()
        self.leftover_reproposed = False
        self.balance = 0
        self.credited_blocks = set()  # block indexes we've already credited for
        
        tx_range = config["consensus_parameters"]["verifier_tx_target_range"]
        self.target_tx_count = random.randint(*tx_range) # The * unpacks the list [3, 8]

        # assign a random target if not provided
        # self.target_fee = 90 #target_fee or random.randint(5, 15)
        self.accumulated_fee = 0
        
        # generate ECC keypair
        self._sk = ec.generate_private_key(ec.SECP256R1())  # private key—never shared
        pub = self._sk.public_key()
        # serialize public key (compressed form)
        self.public_key_bytes = pub.public_bytes(
            encoding=Encoding.DER,
            format=PublicFormat.SubjectPublicKeyInfo
        )
        self.authority.register_node(
            self.label,
            public_key_bytes=self.public_key_bytes,
            region=self.region,
            asset_types=self.allowed_asset_types   # or a list of multi-assets
        )        

        # Transaction proposal state
        self.pending_txs        = []
        self.last_proposed_block = None

        # Blockchain and dedupe
        self.blockchain         = []
        self.seen_messages      = set()

        # Event for waiting on the next finalized block
        self.block_event        = None
        
    def get_balance(self):
        return self.balance
                    
    def start(self):
        """Call this after setting self.authority to kick off the block‐wait loop."""
        # initialize and schedule the consensus loop
        self.block_event = self.env.event()
        self.env.process(self.consensus_loop())

    def consensus_loop(self):
        print(f"[{self.env.now:.1f}] Verifier {self.label} entering consensus_loop with ROUNDS={ROUNDS}")
        for round_no in range(1, ROUNDS + 1):
            # reset per‐round state
            # self.pending_txs = []
            self.proposed    = False
            self.leftover_reproposed = False
            # self.accumulated_fee = 0
            # self.target_fee = 15 #random.randint(1, 5)

            # wait for the finalized block from the Authority
            self.block_event = self.env.event()
            yield self.block_event
        # after ROUNDS, the process ends   
        
    def validate_tx(self, tx):
        """
        Per-asset validation: origin, asset authorization, min fee, solvency by scanning chain deltas
        """
        asset = tx.get("asset_type")
        claimed_TO = tx.get("origin")
        sender_pk_b64 = tx.get("PKS")

        # 1) resolve PKS -> (label, asset_type)
        resolved = self.authority.pkindex.get(sender_pk_b64)
        if not resolved:
            print(f"[{self.env.now:.1f}] Verifier {self.label} ❌ unknown PKS")
            return False
        sender_label = resolved["label"]
        resolved_asset = resolved["asset_type"]
        if resolved_asset != asset:
            print(f"[{self.env.now:.1f}] Verifier {self.label} ❌ asset mismatch for PKS ({resolved_asset}!={asset})")
            return False

        # 2) origin
        registered_TO = self.authority.OMT.get(sender_label, {}).get("region")
        if claimed_TO != registered_TO:
            print(f"[{self.env.now:.1f}] Verifier {self.label} ❌ origin mismatch: {claimed_TO} != {registered_TO}")
            return False

        # 3) asset authorization
        if asset not in self.allowed_asset_types:
            print(f"[{self.env.now:.1f}] Verifier {self.label} ❌ unsupported asset {asset}")
            return False

        # 4) min fee
        min_fee = config["economic_parameters"]["minimum_fee"]
        if tx.get("fee", 0) < min_fee:
            print(f"[{self.env.now:.1f}] Verifier {self.label} ❌ fee too low ({tx.get('fee')})")
            return False

        # 5) solvency: start from genesis per-asset balance, then apply chain deltas for this asset
        genesis = self.blockchain[0]
        entry = next((e for e in genesis["state"] if e["label"] == sender_label and e["role"] == "PUBLIC"), None)
        if not entry:
            print(f"[{self.env.now:.1f}] Verifier {self.label} ❌ no genesis entry for {sender_label}")
            return False
        balances0 = entry.get("balances", {})
        if asset not in balances0:
            print(f"[{self.env.now:.1f}] Verifier {self.label} ❌ no {asset} balance in genesis for {sender_label}")
            return False
        balance = balances0[asset]

        for block in self.blockchain:
            for past_tx in block.get("txs", []):
                if past_tx.get("asset_type") != asset:
                    continue
                if past_tx.get("PKS") == sender_pk_b64:
                    balance -= (past_tx.get("amount", 0) + past_tx.get("fee", 0))
                if past_tx.get("PKR") == sender_pk_b64:
                    balance += past_tx.get("amount", 0)

        if balance < tx.get("amount", 0) + tx.get("fee", 0):
            print(f"[{self.env.now:.1f}] Verifier {self.label} ❌ insufficient {asset} funds ({balance})")
            return False

        return True


    def receive_message(self, msg, h, delay):
        """Handle incoming messages: SignedToken, RToken, Tx, Block."""
        # 1) network delay
        yield self.env.timeout(delay)

        t = msg.get("type")

        # 2) SignedToken → gossip into public mesh
        if t == "SignedToken":
            h = stable_msg_id(msg)
            if h in self.seen_signed:
                     return     # drop duplicates silently
            self.seen_signed.add(h)
            print(f"[{self.env.now:.1f}] Verifier {self.label} ➜ gossip SignedToken to {msg['to']}")
            yield from self.propagate_message(msg, h)
            return

        # 3) dedupe
        if h in self.seen_messages:
            return
        self.seen_messages.add(h)

        # 4) RToken → forward to Authority
        if t == "RToken":
            sig_b64 = msg.get("signature")
            if not sig_b64:
                print(f"[{self.env.now:.1f}] Verifier {self.label} ❌ missing RToken signature")
                return

            try:
                sig = base64.b64decode(sig_b64)
                pk_bytes = base64.b64decode(msg["PKS"])
                sender_pub = serialization.load_der_public_key(pk_bytes)
            except Exception as e:
                print(f"[{self.env.now:.1f}] Verifier {self.label} ❌ invalid PKS/signature: {e}")
                return

            payload = {k: msg[k] for k in ("type","from","toAuth","PKS","PKR","asset_type","origin")}
            data = json.dumps(payload, sort_keys=True).encode()
            try:
                sender_pub.verify(sig, data, ec.ECDSA(hashes.SHA256()))
            except InvalidSignature:
                print(f"[{self.env.now:.1f}] Verifier {self.label} ❌ invalid RToken signature")
                return

            req = {"sender": msg["from"], "asset_type": msg["asset_type"], "PKS": msg["PKS"]}
            print(f"[{self.env.now:.1f}] Verifier {self.label} ✅ verified RToken → Authority ({req['asset_type']})")
            d = get_delay(self.region, self.authority.region)
            self.env.process(self.authority.receive_rtoken(req, d))
            return


        # 5) Tx → validate & propose block when threshold met
        if t == "Tx":
            tx = msg
            # --- FIX: Check for double-spends in pending_txs queue ---
            sender_pk_b64 = tx.get("PKS")
            asset_type = tx.get("asset_type")
            for pending_tx in self.pending_txs:
                if pending_tx.get("PKS") == sender_pk_b64 and pending_tx.get("asset_type") == asset_type:
                    print(f"[{self.env.now:.1f}] Verifier {self.label} ❌ double-spend detected for PKS {sender_pk_b64} for asset {asset_type}")
                    return # Reject this transaction
            # --- END FIX ---

            # 1) verify embedded SignedToken
            st = tx.get("SignedToken", {})
            st_sig_b64 = st.get("signature", "")
            if not st_sig_b64:
                print("❌ missing signature on embedded SignedToken")
                return

            st_data = json.dumps({
                "type": "SignedToken",
                "to":   st.get("to"),
                "from": st.get("from"),
                "asset_type": st.get("asset_type"),
                "PKS":  st.get("PKS")
            }, sort_keys=True).encode()
            try:
                st_sig = base64.b64decode(st_sig_b64)
                auth_pub = serialization.load_der_public_key(self.authority.public_key_bytes)
                auth_pub.verify(st_sig, st_data, ec.ECDSA(hashes.SHA256()))
            except InvalidSignature:
                print(f"[{self.env.now:.1f}] Verifier {self.label} ❌ invalid Tsigned in Tx")
                return

            if st.get("asset_type") != tx.get("asset_type") or st.get("PKS") != tx.get("PKS"):
                print(f"[{self.env.now:.1f}] Verifier {self.label} ❌ SignedToken mismatch (asset/PKS)")
                return

            # 2) verify Tx signature with PKS
            try:
                sender_pk_bytes = base64.b64decode(tx["PKS"])
                sender_pub = serialization.load_der_public_key(sender_pk_bytes)
            except Exception:
                return

            tx_data = json.dumps({
                k: msg[k] for k in ("type","PKS","PKR","asset_type","amount","fee","origin","SignedToken")
            }, sort_keys=True).encode()
            try:
                tx_sig_b64 = tx.get("signature", "")
                tx_sig = base64.b64decode(tx_sig_b64)
                sender_pub.verify(tx_sig, tx_data, ec.ECDSA(hashes.SHA256()))
            except InvalidSignature:
                print(f"[{self.env.now:.1f}] Verifier {self.label} ❌ invalid Tx signature")
                return

            # 3) policy + per-asset solvency
            if not self.validate_tx(tx):
                return

            tx_id = stable_tx_id(tx)
            if tx_id in self.authority.finalized_tx_ids:
                # already finalized—drop it
                return
            self.pending_txs.append(tx)
            self.accumulated_fee += tx.get('fee', 0)
            # example threshold: propose when 2 txs collected
            # if len(self.pending_txs) >= 2:
            # if not self.proposed and self.accumulated_fee >= self.target_fee or len(self.pending_txs) >= 3:
            if (not self.proposed) and (len(self.pending_txs) >= self.target_tx_count):
                new_block = self.createBlock(list(self.pending_txs))
                self.proposed = True
                self.last_proposed_block = new_block
                print(f"[{self.env.now:.1f}] Verifier {self.label} ➜ proposing block "
                    f"{new_block['index']} (fees={self.accumulated_fee})")
                self.env.process(self.authority.receive_candidate_block(self.label, new_block))
                self.pending_txs = []
                self.accumulated_fee = 0
            return


        # 6) Block → process, append, gossip
        if t == "Block":
            block = msg.get("block")
                        
            # compute a fresh hash for the block flood
            block_msg = {"type":"Block", "block":block}
            h_block   = stable_block_id(block_msg)
            for pub in self.get_connected_peers():
                d = get_delay(self.region, pub.region)
                self.env.process(pub.receive_message(block_msg, h_block, d))
                
            self.process_finalized_block(block)

    def propagate_message(self, msg, h):
        # zero‐delay for interleaving
        yield self.env.timeout(0)
        # only dedupe RToken/Tx/SignedToken — let Blocks through exactly once
        if msg.get("type") != "Block":
            if h in self.seen_messages:
                return
            self.seen_messages.add(h)  

        for peer in self.get_connected_peers():
            delay = get_delay(self.region, peer.region)
            def _forward(p=peer, d=delay):
                yield self.env.timeout(d)
                # only print on the Block path
                if msg.get("type") == "Block":
                    print(f"[{self.env.now:.1f}] Verifier {self.label} ➜ flood Block {msg['block']['index']} to {p.label}")
                yield from p.receive_message(msg, h, d)
            self.env.process(_forward())        


    def receive_final_block(self):
        """Unblock the consensus_loop when final block arrives."""
        if self.block_event and not self.block_event.triggered:
            self.block_event.succeed()

    def compute_last_block_hash(self):
        if not self.blockchain:
            return '0' * 64
        return self.blockchain[-1].get('block_hash', '0' * 64)
    
    def createBlock(self, txs_valid):
        """
        Algorithm 3 (createBlock): build a candidate block from validated txs.
        1) h_prev  ← H(B_last)
        2) cnt     ← next index (sim: Authority.current_index)
        3) h_merkle← computeMerkleRoot(Txs_valid)
        4) return  ⟨h_prev, cnt, Txs_valid, h_merkle⟩
        """
        # 1) previous block hash (uses your helper)
        prev_hash = self.compute_last_block_hash()  # uses last appended block on this verifier
        # 2) next block index
        index = (self.blockchain[-1]["index"] + 1) if self.blockchain else 1
        # 3) Merkle over JSON-stable leaves (same format Authority uses)
        tx_leaves = [json.dumps(tx, sort_keys=True) for tx in txs_valid]
        merkle = compute_merkle_root(tx_leaves)
        # (informational) total fee, proposer tag
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
        """Gateway publics to which this verifier sends blocks."""
        return [p for p in all_publics if self in p.peers]

    def process_finalized_block(self, block):
        """
        1) Prune out committed txs from our last proposal,
        2) carry leftovers into next candidate,
        3) top up with new pending_txs (re-validated) until threshold,
        4) send that new candidate immediately.
        """
        self.blockchain.append(block)
        # --- Credit fees to proposer (once per block) ------------------------------
        if block.get("proposer") == self.label and block.get("index") not in self.credited_blocks:
            # Aggregate fees per-asset in this block
            fee_by_asset = {}
            for tx in block.get("txs", []):
                a = tx.get("asset_type")
                f = tx.get("fee", 0)
                if a is None or f is None:
                    continue
                fee_by_asset[a] = fee_by_asset.get(a, 0) + f

            # Update per-asset fee balances
            for a, fsum in fee_by_asset.items():
                self.fee_balances[a] = self.fee_balances.get(a, 0) + fsum

            # (Optional) keep legacy scalar balance for your fairness score S=α·G+β·T
            self.balance += sum(fee_by_asset.values())

            self.credited_blocks.add(block["index"])
            print(f"[{self.env.now:.1f}] Verifier {self.label} 💰 credited fees {fee_by_asset} for block {block['index']}")
        # --------------------------------------------------------------------------

        if self.last_proposed_block and block["index"] < ROUNDS:
            # a) compute which txs were committed
            committed = {stable_tx_id(tx) for tx in block["txs"]}
            # b) leftovers from last proposal
            leftovers = [
                tx for tx in self.last_proposed_block["txs"]
                if stable_tx_id(tx) not in committed
            ]
            
            new_txs = list(leftovers)
            while (len(new_txs) < self.target_tx_count) and self.pending_txs:
                tx = self.pending_txs.pop(0)
                if stable_tx_id(tx) not in committed and self.validate_tx(tx):
                    new_txs.append(tx)

            # optional (informational) total fees in candidate
            new_fee = sum(tx.get("fee", 0) for tx in new_txs)


            if new_txs and len(new_txs) >= self.target_tx_count:
                candidate = self.createBlock(new_txs)
                print(f"[{self.env.now:.1f}] Verifier {self.label}"
                      f" → carry‐over {len(leftovers)} + top‐up to {len(new_txs)} txs,"
                      f" sending next candidate")
                # remember for round n+2
                self.last_proposed_block = candidate
                self.env.process(
                    self.authority.receive_candidate_block(self.label, candidate)
                )
                print(f"[{self.env.now}] {self.label} 📦 Proposed new block with {len(new_txs)} txs for round {candidate['index']}")
                # clear the old one so we don’t repeat
                # self.last_proposed_block = None
            else:
                # Not enough transactions to form a new candidate
                # Do not clear last_proposed_block; keep it for future retry
                print(f"[{self.env.now}] {self.label} 📭 Not enough TXs to form new candidate — retaining last proposal.")

            # e) finally append the finalized block to our own chain
            # self.blockchain.append(block)
            # and purge those committed from our pending queue
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
        self.pubkeys = {}    # maps node label → public_key_bytes
        self.seen_rtokens = set()    # track each RToken’s hash so we only sign once
        self.received_round = set()
        self.pkindex = {}
        
        # ← new: Authority’s own ECC identity
        self._sk = ec.generate_private_key(ec.SECP256R1())
        self.public_key_bytes = self._sk.public_key().public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        )
        
        # store each node’s key‐pair and metadata here:
        self.OMT       = {}    # Onchain Metadata Table
        self.ALT       = []    # Asset List Table

        # Buffers and state
        self.candidate_blocks = {}      # proposals from verifiers
        self.current_index    = 1      # global block counter
        self.seen_messages    = set()  # dedupe if needed
        self.blockchain       = []     # committed blocks

        # Start the limited consensus loop
        env.process(self.consensus_loop())
        
    def select_block(self):
        """
        Choose one candidate from self.candidate_blocks using
        combined throughput+generosity scoring.
        Returns (label, block).
        """
        labels    = list(self.candidate_blocks.keys())
        proposals = self.candidate_blocks

        # Throughput score T_i = tx_count / max_tx_count
        tx_counts = {lbl: len(proposals[lbl]["txs"]) for lbl in labels}
        max_tx    = max(tx_counts.values(), default=1)
        T         = {lbl: tx_counts[lbl] / max_tx for lbl in labels}

        # Generosity score G_i = (max_balance - balance_i) / (max_balance - min_balance)
        balances = {v.label: v.get_balance() for v in self.verifiers}
        min_b, max_b = min(balances.values()), max(balances.values())
        span = (max_b - min_b) or 1
        G    = {lbl: (max_b - balances[lbl]) / span for lbl in labels}

        # Combined score S_i = α·G_i + β·T_i
        S = {lbl: config["consensus_parameters"]["block_selection_weights"]["alpha_generosity"] * G[lbl] + config["consensus_parameters"]["block_selection_weights"]["beta_throughput"] * T[lbl] for lbl in labels}

        # Sort by highest S, tie‐break by lowest label
        best = sorted(labels, key=lambda lbl: (-S[lbl], lbl))
        chosen_label = best[0]
        return chosen_label, proposals[chosen_label]
        
    def register_node(self, label: str, public_key_bytes: bytes, region: str, asset_types: list):
        """Called by each node at startup to populate OMT, ALT, and pubkey map."""
        # record the public key under this label (latest one—OK for label->last pk cache)
        self.pubkeys[label] = public_key_bytes

        # OMT entry keyed by label; ensure 'pubkeys' exists
        if label not in self.OMT:
            self.OMT[label] = {"region": region, "active": True, "pubkeys": {}}
        else:
            # keep region in sync if needed
            self.OMT[label]["region"] = region
            self.OMT[label].setdefault("pubkeys", {})

        # For each asset, store its pubkey in OMT and add an ALT row
        for asset in asset_types:
            seed = os.urandom(32)
            # store asset->pubkey (bytes) inside OMT
            pk_b64 = base64.b64encode(public_key_bytes).decode()
            self.OMT[label]["pubkeys"][asset] = public_key_bytes
            self.pkindex[pk_b64] = {"label": label, "asset_type": asset}


            # ALT entries (one per asset, per label)
            self.ALT.append({
                "public_key":  public_key_bytes,
                "asset_type":  asset,
                "region":      region,
                "seed":        seed,
                "active":      True
            })
            
        print(f"[{self.env.now:.1f}] Authority ✅ registered node {public_key_bytes.hex()} in {region} assets={asset_types}")


    def receive_rtoken(self, request, delay):
        """
        request = {"sender": <label>, "asset_type": <asset>, "PKS": <base64>}
        """
        yield self.env.timeout(delay)
        sender = request["sender"]
        asset  = request["asset_type"]
        pks_b64 = request["PKS"]

        rhash = f"rtok|{sender}|{self.current_index}|{asset}"
        if rhash in self.seen_rtokens:
            return
        self.seen_rtokens.add(rhash)

        active = self.OMT.get(sender, {}).get('active', True)
        action = '✅ issues' if active else '❌ rejects'
        print(f"[{self.env.now:.1f}] Authority {action} token for {sender} ({asset})")

        st = {'type': 'SignedToken', 'to': sender, 'from': self.label, 'asset_type': asset, 'PKS': pks_b64}
        data = json.dumps(st, sort_keys=True).encode()
        sig  = self._sk.sign(data, ec.ECDSA(hashes.SHA256()))
        st["signature"] = base64.b64encode(sig).decode()

        h = stable_msg_id(st)
        for v in self.verifiers:
            d = get_delay(self.region, v.region)
            self.env.process(v.receive_message(st, h, d))



    def receive_candidate_block(self, label, block):
        """Collect proposals from verifiers, dropping any whose TXs are all finalized."""
        yield self.env.timeout(0)
        
        # ignore duplicates from the same label for the same round
        rid = (label, block['index'])
        if rid in self.received_round:
            return
        self.received_round.add(rid)

            # 1) drop any old proposals that are now fully finalized
        #    (keep it a dict mapping label->block)
        self.candidate_blocks = {
            lbl: blk
            for lbl, blk in self.candidate_blocks.items()
            if any(stable_tx_id(tx) not in self.finalized_tx_ids for tx in blk['txs'])
        }

            # 2) buffer the new one
            # self.candidate_blocks.append(block)
        self.candidate_blocks[label] = block
        print(f"[{self.env.now:.1f}] Authority received candidate block for index {block['index']} from {block['proposer']}")    

    def consensus_loop(self):
        print(f"[{self.env.now:.1f}] Authority entering consensus_loop with ROUNDS={ROUNDS}")
        for current_round in range(1, ROUNDS + 1):
            # Clear last round
            # Keep any early proposals that already arrived for the index we are about to finalize
            self.candidate_blocks = {
                lab: blk for lab, blk in self.candidate_blocks.items()
                if blk.get("index") == self.current_index
            }
            # It’s fine to reset the per-round dedupe
            self.received_round.clear()


            # Wait until *every* verifier has proposed once this round
            target = len(self.verifiers)
            # start = self.env.now
            timeout = random.uniform(TIMEOUT_MIN_AUTH, TIMEOUT_MAX_AUTH)
            deadline = self.env.now + timeout
            while len(self.candidate_blocks) < target and self.env.now < deadline:
                yield self.env.timeout(1)
            # while len(self.candidate_blocks) < target:
            #     yield self.env.timeout(1)

            # # Choose one block at random among exactly those bids
            # chosen_label, chosen_block = random.choice(list(self.candidate_blocks.items()))
            
            # if not self.candidate_blocks:
            #     print(f"[{self.env.now:.1f}] Authority ⚠️ No candidate blocks received for round {current_round}")
            #     yield self.env.timeout(1000)
            #     continue
            
            if not self.candidate_blocks:
                print(f"[{self.env.now:.1f}] Authority ⚠️ No candidate blocks received for round {current_round}")
                # # Wake verifiers so they can move to the next round
                # for v in self.verifiers:
                #     v.receive_final_block()
                # continue
                yield self.env.timeout(5)     # small backoff
                # do NOT advance the round; just loop back and wait again
                continue


            # ── select via custom scoring function ──
            chosen_label, chosen_block = self.select_block()

            
            
            print(f"[{self.env.now:.1f}] Authority 🔨 selected block from {chosen_label} for round {current_round}")

            # Compute Merkle, build acks, form block, hash it…
            tx_leaves = [json.dumps(tx, sort_keys=True) for tx in chosen_block['txs']]
            merkle = compute_merkle_root(tx_leaves)
            acks = [
                {"from": tx["PKS"], "to": tx["PKR"], "amount": tx["amount"], "asset_type": tx["asset_type"]}
                for tx in chosen_block["txs"]
            ]

            block = {
                "index":       self.current_index,
                "prev_hash":   self.blockchain[-1]["block_hash"],
                "txs":         chosen_block["txs"],
                "merkle_root": merkle,
                "proposer":    chosen_block["proposer"],
                "total_fees":  chosen_block["total_fees"],
                "acks":        acks
            }
            block_bytes = json.dumps(block, sort_keys=True).encode()
            block["block_hash"] = hashlib.md5(block_bytes).hexdigest()
            
            # fee_totals by asset (informational)
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
                "fee_totals":  fee_totals,
                "acks":        acks
            }
            block_bytes = json.dumps(block, sort_keys=True).encode()
            block["block_hash"] = hashlib.md5(block_bytes).hexdigest()

            # record finalized txs, distribute, append & advance
            for tx in block["txs"]:
                self.finalized_tx_ids.add(stable_tx_id(tx))
            self.distribute_block(block)
            for v in self.verifiers:
                v.receive_final_block()
            self.blockchain.append(block)
            self.current_index += 1

        # done after ROUNDS rounds
        self.seen_rtokens.clear()


    def distribute_block(self, block):
        """Send the finalized block to all verifiers."""
        # Prepare message
        msg = {'type': 'Block', 'block': block}
        h   = stable_block_id(msg)

        # # Gossip to verifiers only; they will flood into publics
        # for v in self.verifiers:
        #     d = get_delay(self.region, v.region)
        #     self.env.process(v.receive_message(msg, h, d))
        # 1) Flood to verifiers
        for v in self.verifiers:
            d = get_delay(self.region, v.region)
            self.env.process(v.receive_message(msg, h, d))

        # 2) Also flood directly to every public
        # for p in all_publics:
        #     d = get_delay(self.region, p.region)
        #     self.env.process(p.receive_message(msg, h, d))


# -------------------------------
# Simulation Setup
# -------------------------------
# def setup_simulation():
#     global all_publics
        
#     # 1) Read and parse user topology
#     n            = int(input("Enter number of nodes in the network: "))
#     labels       = input("Enter node labels (comma-separated): ")
#     nodes        = [l.strip().upper() for l in labels.split(',') if l.strip()]
#     adjacency    = {nd: [] for nd in nodes}

#     print("Define node relationship (neighbors comma-separated, or blank):")
#     for nd in nodes:
#         line = input(f"{nd}: ").strip()
#         if not line:
#             continue
#         for nbr in line.split(','):
#             u = nbr.strip().upper()
#             if u in nodes:
#                 adjacency[nd].append(u)

#     # --- make every link bidirectional ---
#     for u, nbrs in list(adjacency.items()):
#         for v in nbrs:
#             if u not in adjacency[v]:
#                 adjacency[v].append(u)
                
#     authority_lbl = input("Mention which node is Authority (one): ").strip().upper()
#     raw_v         = input("Mention Verifiers (comma-separated): ").strip()
#     verifiers_lbl = [v.strip().upper() for v in raw_v.split(',') if v.strip()]

#     # 2) Instantiate Environment & Entities
#     env = simpy.Environment()
#     # auth = AuthorityNode(env, authority_lbl, AUTHORITY_REGION)
    
#     sys.stdout = open(LOG_FILE, "w", encoding="utf-8")
    
#     auth = AuthorityNode(env, authority_lbl, AUTHORITY_REGION, [])

#     # 2) Instantiate Verifiers, passing in auth so they can register immediately
#     verifier_objs = []
#     for i, lbl in enumerate(verifiers_lbl):
#         region = VERIFIER_REGIONS[i % len(VERIFIER_REGIONS)]
#         v = VerifierNode(env, lbl, region, authority=auth)
#         verifier_objs.append(v)

#     # 3) Hook them back into the Authority
#     auth.verifiers = verifier_objs
#     for v in verifier_objs:
#         v.start()

#     public_labels = [l for l in nodes if l not in verifiers_lbl and l != authority_lbl]
#     all_publics = []
#     for i, lbl in enumerate(public_labels):
#         region = PUBLIC_REGIONS[i % len(PUBLIC_REGIONS)]
#         p = PublicParticipant(env, lbl, region, auth, verifier_objs)
#         all_publics.append(p)

#     pub_map = {p.label: p for p in all_publics}

#     # 3) Wire peers & gateways strictly from adjacency
#     for lbl, nbrs in adjacency.items():
#         if lbl in pub_map:
#             p = pub_map[lbl]
#             for u in nbrs:
#                 if u in pub_map:
#                     p.peers.append(pub_map[u])
#                 if u in verifiers_lbl:
#                     # ✅ Add actual VerifierNode object to p.peers
#                     v_obj = next(v for v in verifier_objs if v.label == u)
#                     p.peers.append(v_obj)
#                     p.is_gateway = True
                    
#     for p in all_publics:
#         print(f"Public {p.label} peers → {[peer.label for peer in p.peers]}")
        
        
#     try:
#         networktopology.draw_topology(nodes, adjacency, authority_lbl, verifiers_lbl)
#     except Exception as e:
#         print("⚠️ topology render failed:", e)
        
#     # # 4) Populate OMT & ALT
#     # for p in all_publics:
#     #     OMT[p.label] = {'region': p.region, 'active': True}
#     #     ALT.append({
#     #         'public_key': p.label,
#     #         'asset_type': 'CURRENCY',
#     #         'region':     p.region,
#     #         'active':     True
#     #     })
        
#     # 5) Genesis Block Creation
#     def hash_pk(pk, seed="SALT"):
#         return f"H({pk}+{seed})"
        
#     genesis_state = []
#     for p in all_publics + verifier_objs:
#         entry = {
#             'label': p.label,
#             'region': p.region,
#             'role': 'VERIFIER' if isinstance(p, VerifierNode) else 'PUBLIC',
#             'balance': p.balance
#         }
#         genesis_state.append(entry)

#     #compute a true Merkle root over the JSON‐stable serialization of each state entry
#     leaves = [json.dumps(e, sort_keys=True) for e in genesis_state]
#     genesis_merkle = compute_merkle_root(leaves)
#     genesis_block = {
#         'index':      0,
#         'prev_hash':  '0' * 64,
#         'state':      genesis_state,
#         'merkle_root': genesis_merkle,
#         'proposer':   'GENESIS'
#     }
#     # now compute this block’s own MD5 hash
#     gb_str = json.dumps(genesis_block, sort_keys=True).encode()
#     genesis_block['block_hash'] = hashlib.md5(gb_str).hexdigest()
   
#     # Append to each node's blockchain
#     for node in all_publics + verifier_objs:
#         node.blockchain.append(genesis_block)

#     auth.blockchain.append(genesis_block)
    
#     # sys.stdout = open(LOG_FILE, "w", encoding="utf-8")

#     print("✅ Genesis Block Created and Distributed\n")
#     print("🔗 Genesis Block Contents:")
#     print(json.dumps(genesis_block, indent=4))
#     print()
#     env.run()

#     # # 7) Final balances after ROUNDS rounds
#     # print("\n=== Final Balances ===")
#     # for p in all_publics:
#     #     print(f"Public {p.label}: {p.balance}")
#     # for v in auth.verifiers:
#     #     print(f"Verifier {v.label}: {v.balance}")
    
#     # 7) Final balances after ROUNDS rounds
#     print("\n=== Final Balances ===")
#     # exclude Authority itself, so only publics + verifiers
#     for node in all_publics + auth.verifiers:
#         print(f"{node.label}: {node.get_balance()}")


#     # 8) How many blocks each node received
#     print("\n🧾 Blockchain Length Summary:")
#     for node in all_publics + auth.verifiers:
#         print(f"{node.label}: {len(node.blockchain)} blocks")

#     # 9) Print the Authority’s full blockchain
#     print("\n=== Authority’s Full Blockchain ===")
#     print(json.dumps(auth.blockchain, indent=4))


# if __name__ == '__main__':
#     setup_simulation()
#     sys.stdout.close()


def _parse_topology_file(path):
    """
    Parse a text file that looks like the interactive transcript, e.g.:

        Enter number of nodes in the network: 97
        Enter node labels (comma-separated): A,V_AF,...,AU15
        Define node relationship (neighbors comma-separated, or blank):
        A: V_AF,V_AS,...
        V_AF: A,AF01,...,AF15
        ...
        Mention which node is Authority (one): A
        Mention Verifiers (comma-separated): V_AF,V_AS,...

    Returns: nodes (list[str]), adjacency (dict[str,list[str]]),
             authority_lbl (str), verifiers_lbl (list[str])
    """
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()

    # 1) node labels
    m_labels = re.search(r"Enter node labels\s*\(comma-separated\)\s*:\s*(.+)", text, flags=re.I)
    if not m_labels:
        raise ValueError("Could not find node labels line.")
    nodes = [t.strip().upper() for t in m_labels.group(1).split(",") if t.strip()]
    adjacency = {nd: [] for nd in nodes}

    # 2) relationships block: collect lines like "X: a,b,c"
    #    Stop when we hit the authority prompt line.
    rel_block_match = re.search(
        r"Define node relationship.*?\n(.*?)(?=\n\s*Mention which node is Authority)", 
        text, flags=re.I | re.S
    )
    if not rel_block_match:
        raise ValueError("Could not find relationships block.")
    rel_block = rel_block_match.group(1)

    for line in rel_block.splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^([A-Za-z0-9_]+)\s*:\s*(.*)$", line)
        if not m:
            continue
        src, nbrs = m.group(1).upper(), m.group(2).strip()
        if src not in adjacency:
            # ignore unknown labels silently (or raise if you prefer)
            continue
        if nbrs:
            for nbr in nbrs.split(","):
                u = nbr.strip().upper()
                if u and u in adjacency:  # keep only known nodes
                    adjacency[src].append(u)

    # 3) authority & verifiers
    m_auth = re.search(r"Mention which node is Authority\s*\(one\)\s*:\s*([A-Za-z0-9_]+)", text, flags=re.I)
    if not m_auth:
        raise ValueError("Could not find authority line.")
    authority_lbl = m_auth.group(1).upper()

    m_ver = re.search(r"Mention Verifiers\s*\(comma-separated\)\s*:\s*(.+)", text, flags=re.I)
    if not m_ver:
        raise ValueError("Could not find verifiers line.")
    verifiers_lbl = [v.strip().upper() for v in m_ver.group(1).split(",") if v.strip()]

    # 4) make links bidirectional
    for u, nbrs in list(adjacency.items()):
        for v in nbrs:
            if u not in adjacency.get(v, []):
                adjacency[v].append(u)

    return nodes, adjacency, authority_lbl, verifiers_lbl


def setup_simulation():
    global all_publics
    
    # If a filepath is passed as argv[1], parse file; else use interactive prompts (old behavior)
    if len(sys.argv) >= 2 and sys.argv[1].strip():
        topo_path = sys.argv[1]
        nodes, adjacency, authority_lbl, verifiers_lbl = _parse_topology_file(topo_path)
    else:
        # ---- Original interactive path (unchanged) ----
        n            = int(input("Enter number of nodes in the network: "))
        labels       = input("Enter node labels (comma-separated): ")
        nodes        = [l.strip().upper() for l in labels.split(',') if l.strip()]
        adjacency    = {nd: [] for nd in nodes}

        print("Define node relationship (neighbors comma-separated, or blank):")
        for nd in nodes:
            line = input(f"{nd}: ").strip()
            if not line:
                continue
            for nbr in line.split(','):
                u = nbr.strip().upper()
                if u in nodes:
                    adjacency[nd].append(u)

        for u, nbrs in list(adjacency.items()):  # bidirectional
            for v in nbrs:
                if u not in adjacency[v]:
                    adjacency[v].append(u)

        authority_lbl = input("Mention which node is Authority (one): ").strip().upper()
        raw_v         = input("Mention Verifiers (comma-separated): ").strip()
        verifiers_lbl = [v.strip().upper() for v in raw_v.split(',') if v.strip()]
        
    # --- collect asset types ---
    ASSET_TYPES = []
    if len(sys.argv) >= 2 and sys.argv[1].strip():
        # Optional: try to parse an "Asset types" line from the same topology file
        # If not present, fall back to default 5
        try:
            with open(sys.argv[1], "r", encoding="utf-8") as _f:
                _text = _f.read()
            # import re
            m_assets = re.search(r"Asset types\s*:\s*(.+)", _text, flags=re.I)
            if m_assets:
                ASSET_TYPES = [a.strip().upper() for a in m_assets.group(1).split(",") if a.strip()]
        except Exception:
            pass

    if not ASSET_TYPES:
        # Interactive or fallback default
        try:
            raw_assets = input("Enter asset types (comma-separated, e.g., INR,USD,EUR,GBP,RUB): ").strip()
            ASSET_TYPES = [a.strip().upper() for a in raw_assets.split(",") if a.strip()]
        except Exception:
            # hard default to your five if no input available
            ASSET_TYPES = ["INR", "EUR", "USD", "GBP", "RUB"]

    print(f"✅ Using asset types: {ASSET_TYPES}")


    # --- instantiate environment/entities (same as before) ---
    env = simpy.Environment()
    sys.stdout = open(LOG_FILE, "w", encoding="utf-8")

    auth = AuthorityNode(env, authority_lbl, AUTHORITY_REGION, [])

    verifier_objs = []
    for i, lbl in enumerate(verifiers_lbl):
        region = VERIFIER_REGIONS[i % len(VERIFIER_REGIONS)]
        v = VerifierNode(env, lbl, region, authority=auth, config=config)
        v.allowed_asset_types = list(ASSET_TYPES)  # allow all configured assets
        v.fee_balances = {a: 0 for a in ASSET_TYPES}
        # if "CURRENCY" not in v.allowed_asset_types:  # Step-2 bridge for legacy Tx
        #     v.allowed_asset_types.append("CURRENCY")
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

    # Wire peers & gateways strictly from adjacency
    for lbl, nbrs in adjacency.items():
        if lbl in pub_map:
            p = pub_map[lbl]
            for u in nbrs:
                if u in pub_map:
                    p.peers.append(pub_map[u])
                if u in verifiers_lbl:
                    v_obj = next(v for v in verifier_objs if v.label == u)
                    p.peers.append(v_obj)
                    p.is_gateway = True

    for p in all_publics:
        print(f"Public {p.label} peers → {[peer.label for peer in p.peers]}")

    try:
        networktopology.draw_topology(nodes, adjacency, authority_lbl, verifiers_lbl)
    except Exception as e:
        print("⚠️ topology render failed:", e)

    # Genesis (unchanged)
    genesis_state = []
    
    def _b64(b: bytes) -> str:
        import base64
        return base64.b64encode(b).decode()
    
    for p in all_publics:
        pubkeys_by_asset = {asset: _b64(p.keys[asset]["pk_bytes"]) for asset in p.asset_types}
        entry = {
            "label": p.label,
            "region": p.region,
            "role": "PUBLIC",
            "balances": dict(p.balances),
            "pubkeys": {asset: _b64(p.keys[asset]["pk_bytes"]) for asset in p.asset_types},
            "balance": p.balances[p.asset_types[0]]  # ← Step-2 bridge for Verifier.validate_tx()
        }
        genesis_state.append(entry)
        
    for v in verifier_objs:
        entry = {
            "label": v.label,
            "region": v.region,
            "role": "VERIFIER",
            "balance": v.balance,
            # optionally:
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
    genesis_block['block_hash'] = hashlib.md5(gb_str).hexdigest()

    for node in all_publics + verifier_objs:
        node.blockchain.append(genesis_block)
    auth.blockchain.append(genesis_block)

    print("✅ Genesis Block Created and Distributed\n")
    print("🔗 Genesis Block Contents:")
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

    print("\n=== Authority’s Full Blockchain ===")
    print(json.dumps(auth.blockchain, indent=4))
    
if __name__ == '__main__':
    setup_simulation()
    sys.stdout.close()

