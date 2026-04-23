import simpy
import random
import pandas as pd
import networktopology  # visualize after simulation
import json
import sys
import hashlib
import base64
import re

# at top of file, add
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, PrivateFormat, NoEncryption
from cryptography.exceptions import InvalidSignature

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

LOG_FILE = "simulation_output.txt"
ROUNDS = 2000
# in ms (or your sim‐time units)
TIMEOUT_MIN_AUTH = 20
TIMEOUT_MAX_AUTH = 40


# -------------------------------
# Load Inter-Region Latency Matrix
# -------------------------------
LATENCY_XLSX = r'C:\Users\sumit\Downloads\Untitled spreadsheet.xlsx'
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
AUTHORITY_REGION = 'West Europe'

# Global tables & storage
all_publics = []

# -------------------------------
# Entity classes
# -------------------------------

class PublicParticipant:
    def __init__(self, env, label, region, authority, verifiers):
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
        self.balance = random.randint(9000, 15000)
        self.seen_signed = set()
        
        # generate ECC keypair
        self._sk = ec.generate_private_key(ec.SECP256R1())  # private key—never shared
        pub = self._sk.public_key()
        # serialize public key (compressed form)
        self.public_key_bytes = pub.public_bytes(
            encoding=Encoding.DER,
            format=PublicFormat.SubjectPublicKeyInfo
        )
        # now register *this* key with the Authority
        self.authority.register_node(
            self.label,
            public_key_bytes=self.public_key_bytes,
            region=self.region,
            asset_types=["CURRENCY"]   # or a list of multi-assets
        )

        self.signed_token = None
        # Events reset each round
        self.token_event = env.event()
        self.block_event = env.event()

        # Start behavior process
        env.process(self.behavior())
        
    def get_balance(self):
        return self.balance
        
    def behavior(self):
        for _ in range(ROUNDS):  # limit to 10 rounds
            yield self.env.timeout(random.uniform(1, 5))
            yield from self.single_activity_round()

    def single_activity_round(self):
        # 1) Prepare to receive a SignedToken
        self.token_event = self.env.event()
        # Full payload: ⟨PKS, PKR, asset_type, origin, to⟩
        # 2) Construct & gossip a signed RToken
        rtoken = {
            "type":       "RToken",
            "from":       self.label,                                    # ← add this
            "PKS":        base64.b64encode(self.public_key_bytes).decode(),
            "PKR":        base64.b64encode(self.authority.public_key_bytes).decode(),
            "asset": "CURRENCY",
            "origin":     self.region,
            "toAuth":     self.authority.label
        }
        # ─── SIGN IT ───────────────────────────────
        # deterministically serialize and sign
        data = json.dumps(rtoken, sort_keys=True).encode()
        sig  = self._sk.sign(data, ec.ECDSA(hashes.SHA256()))
        # attach signature
        rtoken["signature"] = base64.b64encode(sig).decode()

        # hash for dedupe
        h = hash(repr(rtoken))
        print(f"[{self.env.now:.1f}] Public {self.label} ➜ gossip RToken")
        yield from self.propagate_message(rtoken, h)
        # ----------------------------------------------------------------

        # 3) Wait for SignedToken
        yield self.token_event
        # only one print here for receipt
        print(f"[{self.env.now:.1f}] Public {self.label} ✅ received token")
        
        # # pick a recipient and create the Tx metadata (balances *not* updated yet)
        receiver = random.choice([p for p in all_publics if p.label != self.label])
        amount, fee = random.randint(10, 100), random.randint(5, 15)
        if self.balance < amount+fee:
            print(f"[{self.env.now:.1f}] Public {self.label} ⚠️ insufficient balance, skipping Tx")
            return
        # form the full Tx, embedding the SignedToken
        tx = {
                "type":          "Tx",
                "PKS":           base64.b64encode(self.public_key_bytes).decode(),
                "PKR":           base64.b64encode(receiver.public_key_bytes).decode(),
                "asset_type":    "CURRENCY",
                "amount":        amount,
                "fee":           fee,
                "origin":        self.region,
                "SignedToken": self.signed_token.copy()     # the full SignedToken dict
            }
            
        # sign the transaction with our ECC key
        tx_data = json.dumps(tx, sort_keys=True).encode()
        tx_sig  = self._sk.sign(tx_data, ec.ECDSA(hashes.SHA256()))
        tx["signature"] = base64.b64encode(tx_sig).decode()
        
        htx = hash(repr(tx))
        print(f"[{self.env.now:.1f}] Public {self.label} ➜ gossip Tx")
        yield from self.propagate_message(tx, htx)
        

        # 5) Prepare to receive the final Block
        self.block_event = self.env.event()

        # 6) Wait for final Block
        yield self.block_event
        print(f"[{self.env.now:.1f}] Public {self.label} 📰 appended block")

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
            st_hash = hash(repr(message))
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
            for ack in block.get("acks", []):
                if ack["to"] == base64.b64encode(self.public_key_bytes).decode():
                    self.balance += ack["amount"]
                    print(f"[{self.env.now:.1f}] Public {self.label} 💰 received {ack['amount']} from {ack['from']}")
                if ack["from"] == base64.b64encode(self.public_key_bytes).decode():
                    self.balance -= ack['amount']
                    print(f"[{self.env.now:.1f}] Public {self.label} 🏷️ sent {ack['amount']} to {ack['to']}")
            self.receive_final_block()
            # return
        elif t == "SignedToken" and message["to"] == self.label:
            sig_b64 = message.get("signature", "")
            try:
                sig = base64.b64decode(sig_b64)
            except Exception as e:
                print(f"[{self.env.now:.1f}] Public {self.label} ❌ invalid SignedToken base64: {e}")
                return

            data = json.dumps({
                "type": message["type"],
                "to":   message["to"],
                "from": message["from"]
            }, sort_keys=True).encode()
            auth_pub = serialization.load_der_public_key(self.authority.public_key_bytes)
            try:
                auth_pub.verify(sig, data, ec.ECDSA(hashes.SHA256()))
                print(f"[{self.env.now:.1f}] Public {self.label} ✅ verified SignedToken signature")
            except InvalidSignature:
                print(f"[{self.env.now:.1f}] Public {self.label} ❌ invalid SignedToken signature")
                return

            self.signed_token = message.copy()
            self.receive_signed_token()
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
    def __init__(self, env, label, region, authority=None, target_fee=None):
        self.env                = env
        self.label              = label.upper()
        self.region             = region
        self.authority          = authority  # will be set after instantiation
        self.allowed_asset_types = ["CURRENCY"]
        self.seen_signed = set()
        self.leftover_reproposed = False
        self.balance = 0
        self.credited_blocks = set()  # block indexes we've already credited for

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
            asset_types=["CURRENCY"]   # or a list of multi-assets
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
            self.pending_txs = []
            self.proposed    = False
            self.leftover_reproposed = False
            self.accumulated_fee = 0
            self.target_fee = 15 #random.randint(1, 5)

            # wait for the finalized block from the Authority
            self.block_event = self.env.event()
            yield self.block_event
        # after ROUNDS, the process ends   
        
    def validate_tx(self, tx):
        """
        Implements Algorithm 4 (validateTransactionAuth) plus balance check.
        Returns True if tx is well-formed, authorized, and sender solvent.
        """

        # 1. Origin Consistency
        #    check that tx["TO"] (claimed origin) matches our GeoLocTable entry
        claimed_TO = tx.get("origin")
        # look up which label owns this PKS
        registered_TO = None
        for lbl, pk_bytes in self.authority.pubkeys.items():
            if base64.b64encode(pk_bytes).decode() == tx["PKS"]:
                registered_TO = self.authority.OMT[lbl]["region"]
                break
        if claimed_TO != registered_TO:
            print(f"[{self.env.now:.1f}] Verifier {self.label} ❌ origin mismatch: {claimed_TO} != {registered_TO}")
            return False
        # :contentReference[oaicite:0]{index=0}

        # 2. Asset Type Authorization
        #    ensure tx["asset_type"] is one we handle
        if tx.get("asset_type") not in self.allowed_asset_types:
            print(f"[{self.env.now:.1f}] Verifier {self.label} ❌ unsupported asset {tx.get('asset_type')}")
            return False

        # 3. Token Authenticity
        #    (you already do this by verifying SignedToken signature above)

        # 4. Fee Sufficiency
        MIN_FEE = 5  # or pull from config
        if tx.get("fee", 0) < MIN_FEE:
            print(f"[{self.env.now:.1f}] Verifier {self.label} ❌ fee too low ({tx.get('fee')})")
            return False

        # 5. Balance Verification
        #    start with the genesis‐assigned balance for this sender
        sender_pk_b64 = tx["PKS"]
        # find the sender’s label via the Authority’s pubkeys
        sender_label = next(
            lbl for lbl, pk in self.authority.pubkeys.items()
            if base64.b64encode(pk).decode() == sender_pk_b64
        )
        # lookup initial balance in genesis state (blockchain[0]['state'])
        genesis = self.blockchain[0]
        init = next(e for e in genesis["state"] if e["label"] == sender_label)
        balance = init["balance"]
        for block in self.blockchain:
            for past_tx in block.get("txs", []):
                # debit when this node was sender
                if past_tx.get("PKS") == sender_pk_b64:
                    balance -= past_tx.get("amount", 0)
                # credit when this node was recipient
                if past_tx.get("PKR") == sender_pk_b64:
                    balance += past_tx.get("amount", 0)
        if balance < tx.get("amount", 0) + tx.get("fee", 0):
            print(f"[{self.env.now:.1f}] Verifier {self.label} ❌ insufficient funds ({balance})")
            return False

        return True

    def receive_message(self, msg, h, delay):
        """Handle incoming messages: SignedToken, RToken, Tx, Block."""
        # 1) network delay
        yield self.env.timeout(delay)

        t = msg.get("type")

        # 2) SignedToken → gossip into public mesh
        if t == "SignedToken":
            h = hash(repr(msg))
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
            # Extract and remove the signature to verify
            local = dict(msg)
            sig_b64 = local.get("signature", None)
            if sig_b64 is None:
                print(f"[{self.env.now:.1f}] Verifier {self.label} ❌ missing signature on RToken")
                return
            sig = base64.b64decode(sig_b64)
            
            # 2) rebuild signed payload
            payload = {k: msg[k] for k in ("type","from","toAuth","asset","origin")}
            data = json.dumps(payload, sort_keys=True).encode()

            # 3) load sender's pubkey from Authority
            sender_pk_bytes = self.authority.pubkeys.get(msg["from"])
            if sender_pk_bytes is None:
                return  # unknown sender
            sender_pk = serialization.load_der_public_key(sender_pk_bytes)

            # Reconstruct the signed payload (must match exactly what the public signed)
            data = json.dumps({
                "type":       msg["type"],
                "from":    msg["from"],
                "toAuth":  msg["toAuth"],
                "PKS":        msg["PKS"],
                "PKR":        msg["PKR"],
                "asset":   msg["asset"],
                "origin":     msg["origin"],
            }, sort_keys=True).encode()

            # Load the sender’s public key from the base64-encoded DER
            try:
                pk_bytes = base64.b64decode(msg["PKS"])
                sender_pub = serialization.load_der_public_key(pk_bytes)
            except Exception as e:
                print(f"[{self.env.now:.1f}] Verifier {self.label} ❌ invalid PKS format: {e}")
                return

            # Verify ECDSA signature
            try:
                sender_pub.verify(sig, data, ec.ECDSA(hashes.SHA256()))
            except InvalidSignature:
                print(f"[{self.env.now:.1f}] Verifier {self.label} ❌ invalid RToken signature from {msg.get('from')}")
                return

            # All good—forward to Authority by sender label only
            sender = msg["from"]
            print(f"[{self.env.now:.1f}] Verifier {self.label} ✅ verified and forwarding RToken for {sender}")
            d = get_delay(self.region, self.authority.region)
            # pass just the sender label, not the whole msg dict
            self.env.process(self.authority.receive_rtoken(sender, d))
            return

        # 5) Tx → validate & propose block when threshold met
        if t == "Tx":
            tx = msg
            
            # 1) verify the embedded SignedToken first
            st = tx.get("SignedToken", {})
            if not isinstance(st, dict):
                print("❌ missing SignedToken dict in Tx")
                return
            st_sig_b64 = st.get("signature", "")
            if not st_sig_b64:
                print("❌ missing signature on embedded SignedToken")
                return
            # st_sig = base64.b64decode(st_sig_b64)
            # st_data = json.dumps(st, sort_keys=True).encode()
            sig = base64.b64decode(st_sig_b64)
            data = json.dumps({
                "type": "SignedToken",
                "to":   st["to"],
                "from": st["from"]
            }, sort_keys=True).encode()
            auth_pub = serialization.load_der_public_key(self.authority.public_key_bytes)
            try:
                auth_pub.verify(sig, data, ec.ECDSA(hashes.SHA256()))
            except InvalidSignature:
                print(f"[{self.env.now:.1f}] Verifier {self.label} ❌ invalid Tsigned in Tx from {st.get('to')}")
                return

            # 2) verify the Tx’s own signature
            tx_sig_b64 = tx.get("signature", "")
            if not tx_sig_b64:
                print("❌ missing signature on Tx")
                return
            tx_sig     = base64.b64decode(tx_sig_b64)
            tx_data = json.dumps({
                k: msg[k] for k in ("type","PKS","PKR","asset_type","amount","fee","origin","SignedToken")
            }, sort_keys=True).encode()
            sender_pk_bytes = base64.b64decode(tx["PKS"])
            sender_pub = serialization.load_der_public_key(sender_pk_bytes)
            try:
                sender_pub.verify(tx_sig, tx_data, ec.ECDSA(hashes.SHA256()))
            except InvalidSignature:
                print(f"[{self.env.now:.1f}] Verifier {self.label} ❌ invalid Tx signature from {tx['PKS'][:8]}")
                return
            
            # 3) run full paper‐style validation
            if not self.validate_tx(tx):
                # invalid by one of the paper’s rules: drop it
                return
                
            tx_id = hash(repr(tx))
            if tx_id in self.authority.finalized_tx_ids:
                # already finalized—drop it
                return
            self.pending_txs.append(tx)
            self.accumulated_fee += tx.get('fee', 0)
            # example threshold: propose when 2 txs collected
            # if len(self.pending_txs) >= 2:
            # if not self.proposed and self.accumulated_fee >= self.target_fee or len(self.pending_txs) >= 3:
            if not self.proposed and self.accumulated_fee >= self.target_fee:
                new_block = {
                    'index':       self.authority.current_index,
                    'prev_hash':   self.compute_last_block_hash(),
                    'txs':         list(self.pending_txs),
                    'merkle_root': None,
                    'proposer':    self.label,
                    'total_fees':  self.accumulated_fee
                }
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
            h_block   = hash(repr(block_msg))
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
        """Hash of the most recent block in local chain."""
        if not self.blockchain:
            return '0' * 64
        return str(hash(repr(self.blockchain[-1])))

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
        # --- NEW: credit fees to proposer (once) ------------------------------
        # Credit only if: (a) I proposed this block, and (b) I haven't credited this index yet
        if block.get("proposer") == self.label and block.get("index") not in self.credited_blocks:
            # Prefer the block's recorded total_fees; fallback to recomputing if missing
            fee_to_credit = block.get("total_fees")
            if fee_to_credit is None:
                fee_to_credit = sum(tx.get("fee", 0) for tx in block.get("txs", []))

            if fee_to_credit:
                self.balance += fee_to_credit
                self.credited_blocks.add(block["index"])
                print(f"[{self.env.now:.1f}] Verifier {self.label} 💰 credited fees {fee_to_credit} "
                    f"for finalized block {block['index']}")
        # ---------------------------------------------------------------------     
        if self.last_proposed_block and block["index"] < ROUNDS:
            # a) compute which txs were committed
            committed = {hash(repr(tx)) for tx in block["txs"]}
            # b) leftovers from last proposal
            leftovers = [
                tx for tx in self.last_proposed_block["txs"]
                if hash(repr(tx)) not in committed
            ]

            # c) now form the next candidate with those leftovers
            new_txs = list(leftovers)
            new_fee = sum(tx["fee"] for tx in new_txs)

            # d) top up from pending_txs (which holds all other seen txs),
            #    but only if they re-validate and aren’t already in committed
            while (new_fee < self.target_fee and self.pending_txs):
                tx = self.pending_txs.pop(0)
                if hash(repr(tx)) not in committed and self.validate_tx(tx):
                    new_txs.append(tx)
                    new_fee += tx["fee"]

            if new_txs:
                candidate = {
                    "index":      self.authority.current_index,
                    "prev_hash":  self.compute_last_block_hash(),
                    "txs":        new_txs,
                    "merkle_root": None,
                    "proposer":   self.label,
                    "total_fees": new_fee
                }
                print(f"[{self.env.now:.1f}] Verifier {self.label}"
                      f" → carry‐over {len(leftovers)} + top‐up to {len(new_txs)} txs,"
                      f" sending next candidate")
                # remember for round n+2
                self.last_proposed_block = candidate
                self.env.process(
                    self.authority.receive_candidate_block(self.label, candidate)
                )
            # clear the old one so we don’t repeat
            self.last_proposed_block = None

        # e) finally append the finalized block to our own chain
        self.blockchain.append(block)
        # and purge those committed from our pending queue
        self.pending_txs = [
            tx for tx in self.pending_txs
            if hash(repr(tx)) not in self.authority.finalized_tx_ids
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
        
        # weights for combined scoring (α on generosity, β on throughput)
        self.alpha = 0.5
        self.beta  = 0.5
        
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
        S = {lbl: self.alpha * G[lbl] + self.beta * T[lbl] for lbl in labels}

        # Sort by highest S, tie‐break by lowest label
        best = sorted(labels, key=lambda lbl: (-S[lbl], lbl))
        chosen_label = best[0]
        return chosen_label, proposals[chosen_label]
        
    def register_node(self, label: str, public_key_bytes: bytes, region: str, asset_types: list):
        """Called by each node at startup to populate OMT, ALT, and pubkey map."""
        # record the public key under this label
        self.pubkeys[label] = public_key_bytes
        # OMT entry keyed by label
        self.OMT[label] = {"region": region, "active": True}
        
        # ALT entries (one per asset)
        for asset in asset_types:
            self.ALT.append({
                "public_key":  public_key_bytes,
                "asset_type":  asset,
                "region":      region,
                "active":      True
            })
        print(f"[{self.env.now:.1f}] Authority ✅ registered node {public_key_bytes.hex()} in {region} assets={asset_types}")

    def receive_rtoken(self, sender, delay):
        """Handle an RToken from a public, issue SignedToken, gossip to verifiers."""
        # 0) Wait network delay
        yield self.env.timeout(delay)

        # 1) Compute a unique key for this request—based on sender (and any other fields you care about)
        # rhash = hash((sender, self.current_index))  # or hash(repr(rtoken)) if you carry more data
        rhash = hash((sender, self.current_index))

        # 2) If we’ve already processed it, drop it *before* logging
        if rhash in self.seen_rtokens:
            return
        self.seen_rtokens.add(rhash)

        # 3) Now check active status and log
        active = self.OMT.get(sender, {}).get('active', True)
        action = '✅ issues' if active else '❌ rejects'
        print(f"[{self.env.now:.1f}] Authority {action} token for {sender}")

        # 4) Build and sign the SignedToken
        st = {'type': 'SignedToken', 'to': sender, 'from': self.label}
        data = json.dumps(st, sort_keys=True).encode()
        sig  = self._sk.sign(data, ec.ECDSA(hashes.SHA256()))
        st["signature"] = base64.b64encode(sig).decode()

        # 5) Gossip to all verifiers
        h = hash(repr(st))
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
            if any(hash(repr(tx)) not in self.finalized_tx_ids for tx in blk['txs'])
        }

            # 2) buffer the new one
            # self.candidate_blocks.append(block)
        self.candidate_blocks[label] = block
        print(f"[{self.env.now:.1f}] Authority received candidate block for index {block['index']} from {block['proposer']}")    

    def consensus_loop(self):
        print(f"[{self.env.now:.1f}] Authority entering consensus_loop with ROUNDS={ROUNDS}")
        for current_round in range(1, ROUNDS + 1):
            # Clear last round
            self.candidate_blocks.clear()
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
            
            if not self.candidate_blocks:
                print(f"[{self.env.now:.1f}] Authority ⚠️ No candidate blocks received for round {current_round}")
                yield self.env.timeout(1000)
                continue

            # ── select via custom scoring function ──
            chosen_label, chosen_block = self.select_block()

            
            
            print(f"[{self.env.now:.1f}] Authority 🔨 selected block from {chosen_label} for round {current_round}")

            # Compute Merkle, build acks, form block, hash it…
            tx_leaves = [json.dumps(tx, sort_keys=True) for tx in chosen_block['txs']]
            merkle = compute_merkle_root(tx_leaves)
            acks = [
                {"from": tx["PKS"], "to": tx["PKR"], "amount": tx["amount"]}
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

            # record finalized txs, distribute, append & advance
            for tx in block["txs"]:
                self.finalized_tx_ids.add(hash(repr(tx)))
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
        h   = hash(repr(msg))

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

    # --- instantiate environment/entities (same as before) ---
    env = simpy.Environment()
    sys.stdout = open(LOG_FILE, "w", encoding="utf-8")

    auth = AuthorityNode(env, authority_lbl, AUTHORITY_REGION, [])

    verifier_objs = []
    for i, lbl in enumerate(verifiers_lbl):
        region = VERIFIER_REGIONS[i % len(VERIFIER_REGIONS)]
        v = VerifierNode(env, lbl, region, authority=auth)
        verifier_objs.append(v)

    auth.verifiers = verifier_objs
    for v in verifier_objs:
        v.start()

    public_labels = [l for l in nodes if l not in verifiers_lbl and l != authority_lbl]
    all_publics = []
    for i, lbl in enumerate(public_labels):
        region = PUBLIC_REGIONS[i % len(PUBLIC_REGIONS)]
        p = PublicParticipant(env, lbl, region, auth, verifier_objs)
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
    for p in all_publics + verifier_objs:
        entry = {
            'label': p.label,
            'region': p.region,
            'role': 'VERIFIER' if isinstance(p, VerifierNode) else 'PUBLIC',
            'balance': p.balance
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
        print(f"{node.label}: {node.get_balance()}")

    print("\n🧾 Blockchain Length Summary:")
    for node in all_publics + auth.verifiers:
        print(f"{node.label}: {len(node.blockchain)} blocks")

    print("\n=== Authority’s Full Blockchain ===")
    print(json.dumps(auth.blockchain, indent=4))
    
if __name__ == '__main__':
    setup_simulation()
    sys.stdout.close()

