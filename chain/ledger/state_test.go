package ledger

import (
	"crypto/ed25519"
	"crypto/sha256"
	"encoding/hex"
	"testing"
)

const chainID = "aurix-test"

func key(seed string) ed25519.PrivateKey {
	h := sha256.Sum256([]byte(seed))
	return ed25519.NewKeyFromSeed(h[:])
}

func addr(k ed25519.PrivateKey) string { return AddressFromPubKey(k.Public().(ed25519.PublicKey)) }

type env struct {
	t                     *testing.T
	s                     *State
	alice, bob, arb, gate ed25519.PrivateKey
}

func newEnv(t *testing.T) *env {
	e := &env{t: t, alice: key("alice"), bob: key("bob"), arb: key("arbiter"), gate: key("gateway")}
	e.s = NewState(Genesis{ChainID: chainID,
		Balances:      map[string]uint64{addr(e.alice): 100 * MicroPerAUR, addr(e.gate): 1 * MicroPerAUR},
		AnchorSigners: []string{addr(e.gate)}})
	return e
}

func (e *env) tx(k ed25519.PrivateKey, m Msg) Tx {
	tx := Tx{Msg: m, Nonce: e.s.Account(addr(k)).Nonce, Fee: DefaultFee}
	tx.Sign(k, chainID)
	return tx
}

func (e *env) commit(txs ...Tx) Block { return e.s.CommitBlock(txs, 0) }

func (e *env) mustOK(b Block) {
	e.t.Helper()
	for _, r := range b.Txs {
		if r.Code != 0 {
			e.t.Fatalf("tx failed: %s", r.Log)
		}
	}
}

func (e *env) mustFail(b Block) {
	e.t.Helper()
	if b.Txs[0].Code == 0 {
		e.t.Fatalf("tx should have failed")
	}
}

// Invariants 1, 2, 5: supply constant, locked funds consistent, balances >= 0 (uint).
func (e *env) invariants() {
	e.t.Helper()
	if got := e.s.SumBalances() + e.s.LockedInEscrow(); got != e.s.TotalSupply() {
		e.t.Fatalf("supply invariant broken: %d != %d", got, e.s.TotalSupply())
	}
}

func TestSend(t *testing.T) {
	e := newEnv(t)
	e.mustOK(e.commit(e.tx(e.alice, Msg{Type: TypeSend, To: addr(e.bob), Amount: 10 * MicroPerAUR})))
	if b := e.s.Account(addr(e.bob)).Balance; b != 10*MicroPerAUR {
		t.Fatalf("bob balance %d", b)
	}
	if a := e.s.Account(addr(e.alice)); a.Balance != 90*MicroPerAUR-DefaultFee || a.Nonce != 1 {
		t.Fatalf("alice %+v", a)
	}
	e.invariants()
}

func TestReplayRejected(t *testing.T) { // invariant 4
	e := newEnv(t)
	tx := e.tx(e.alice, Msg{Type: TypeSend, To: addr(e.bob), Amount: 1})
	e.mustOK(e.commit(tx))
	e.mustFail(e.commit(tx))
	e.invariants()
}

func TestInsufficientFunds(t *testing.T) { // invariant 5
	e := newEnv(t)
	e.mustFail(e.commit(e.tx(e.bob, Msg{Type: TypeSend, To: addr(e.alice), Amount: 1})))
	e.mustFail(e.commit(e.tx(e.alice, Msg{Type: TypeSend, To: addr(e.bob), Amount: 100 * MicroPerAUR})))
	e.invariants()
}

func TestBadSignature(t *testing.T) {
	e := newEnv(t)
	tx := e.tx(e.alice, Msg{Type: TypeSend, To: addr(e.bob), Amount: 1})
	tx.Msg.Amount = 50 * MicroPerAUR // tampered after signing
	e.mustFail(e.commit(tx))
}

func TestEscrowRelease(t *testing.T) { // invariants 2, 3
	e := newEnv(t)
	e.mustOK(e.commit(e.tx(e.alice, Msg{Type: TypeCreateEscrow, To: addr(e.bob), Arbiter: addr(e.arb), Amount: 5 * MicroPerAUR, DeadlineHeight: 10})))
	e.invariants()
	if e.s.LockedInEscrow() != 5*MicroPerAUR {
		t.Fatal("not locked")
	}
	e.s.CommitBlock(nil, 0) // fund the arbiter's fee
	e.mustOK(e.commit(e.tx(e.alice, Msg{Type: TypeSend, To: addr(e.arb), Amount: MicroPerAUR})))
	e.mustOK(e.commit(e.tx(e.arb, Msg{Type: TypeReleaseEscrow, EscrowID: 1})))
	if b := e.s.Account(addr(e.bob)).Balance; b != 5*MicroPerAUR {
		t.Fatalf("bob %d", b)
	}
	e.mustFail(e.commit(e.tx(e.arb, Msg{Type: TypeReleaseEscrow, EscrowID: 1})))  // no double release
	e.mustFail(e.commit(e.tx(e.alice, Msg{Type: TypeRefundEscrow, EscrowID: 1}))) // no refund after release
	e.invariants()
}

func TestEscrowRefundAfterDeadline(t *testing.T) {
	e := newEnv(t)
	e.mustOK(e.commit(e.tx(e.alice, Msg{Type: TypeCreateEscrow, To: addr(e.bob), Amount: 5 * MicroPerAUR, DeadlineHeight: 4})))
	e.mustFail(e.commit(e.tx(e.alice, Msg{Type: TypeRefundEscrow, EscrowID: 1}))) // height 2 < 4
	e.commit()
	e.mustOK(e.commit(e.tx(e.alice, Msg{Type: TypeRefundEscrow, EscrowID: 1}))) // height 4
	e.mustFail(e.commit(e.tx(e.bob, Msg{Type: TypeReleaseEscrow, EscrowID: 1})))
	if s, _ := e.s.Escrow(1); s.Status != Refunded {
		t.Fatal(s.Status)
	}
	e.invariants()
}

func TestStrangerCannotRelease(t *testing.T) {
	e := newEnv(t)
	e.mustOK(e.commit(e.tx(e.alice, Msg{Type: TypeCreateEscrow, To: addr(e.bob), Amount: 1, DeadlineHeight: 10})))
	e.mustOK(e.commit(e.tx(e.alice, Msg{Type: TypeSend, To: addr(e.bob), Amount: MicroPerAUR})))
	e.mustFail(e.commit(e.tx(e.bob, Msg{Type: TypeReleaseEscrow, EscrowID: 1})))
}

func TestAnchorProof(t *testing.T) {
	e := newEnv(t)
	h := sha256.Sum256([]byte("order-1|capture-1"))
	hx := hex.EncodeToString(h[:])
	supply := e.s.TotalSupply()
	e.mustOK(e.commit(e.tx(e.gate, Msg{Type: TypeAnchorProof, Hash: hx, ExternalRef: "paypal"})))
	if _, ok := e.s.Proof(hx); !ok {
		t.Fatal("proof missing")
	}
	if e.s.TotalSupply() != supply {
		t.Fatal("anchoring must not create AUR")
	}
	e.mustFail(e.commit(e.tx(e.gate, Msg{Type: TypeAnchorProof, Hash: hx})))  // duplicate
	e.mustFail(e.commit(e.tx(e.alice, Msg{Type: TypeAnchorProof, Hash: hx}))) // unauthorised
	e.invariants()
}
