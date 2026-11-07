package keeper_test

import (
	"errors"
	"testing"

	"github.com/joeylukoji/aurix/chain/x/smartmoney/types"
)

func (e *env) setTTL(ttl uint64, threshold uint32, signers ...string) {
	e.t.Helper()
	if _, err := e.srv.UpdateParams(e.ctx, &types.MsgUpdateParams{Authority: govAuthority,
		Params: types.Params{AnchorSigners: signers, AnchorThreshold: threshold, PendingTtlBlocks: ttl}}); err != nil {
		e.t.Fatal(err)
	}
}

func (e *env) attestAmt(signer, hash, amount, currency string) (*types.MsgAnchorProofResponse, error) {
	return e.srv.AnchorProof(e.ctx, &types.MsgAnchorProof{Signer: signer, Hash: hash, ExternalRef: "O", CaptureRef: "C",
		Amount: amount, Currency: currency})
}

func TestAnchorAmountValidation(t *testing.T) {
	e := setup(t)
	e.setTTL(0, 1, e.gate)
	hx := proofHash("O", "C")
	bad := [][2]string{{"", "USD"}, {"12.50", ""}, {"0", "USD"}, {"0.00", "USD"}, {"-1", "USD"}, {"1.234", "USD"},
		{"abc", "USD"}, {"1,00", "USD"}, {".5", "USD"}, {"12.50", "usd"}, {"12.50", "US"}, {"12.50", "USDT"}}
	for _, b := range bad {
		if _, err := e.attestAmt(e.gate, hx, b[0], b[1]); !errors.Is(err, types.ErrInvalidAmount) {
			t.Fatalf("%v accepted: %v", b, err)
		}
	}
	for i, ok := range []string{"12.50", "1", "0.01", "100.5"} {
		h := proofHash("O", string(rune('a'+i)))
		if _, err := e.attestAmt(e.gate, h, ok, "USD"); err != nil {
			t.Fatal(ok, err)
		}
	}
	p, _ := e.k.Proofs.Get(e.ctx, proofHash("O", "a"))
	if p.Amount != "12.50" || p.Currency != "USD" {
		t.Fatal(p)
	}
}

func TestAnchorAmountMismatch(t *testing.T) {
	e := setup(t)
	a2 := addr("attestor2")
	e.setTTL(0, 2, e.gate, a2)
	hx := proofHash("O", "C")
	if _, err := e.attestAmt(e.gate, hx, "12.50", "USD"); err != nil {
		t.Fatal(err)
	}
	if _, err := e.attestAmt(a2, hx, "12.51", "USD"); !errors.Is(err, types.ErrProofMismatch) {
		t.Fatal("amount mismatch accepted", err)
	}
	if _, err := e.attestAmt(a2, hx, "12.50", "EUR"); !errors.Is(err, types.ErrProofMismatch) {
		t.Fatal("currency mismatch accepted", err)
	}
	if r, err := e.attestAmt(a2, hx, "12.50", "USD"); err != nil || r.Status != types.PROOF_STATUS_CONFIRMED {
		t.Fatal(r, err)
	}
}

func hasEvent(e *env, typ string) bool {
	for _, x := range e.ctx.EventManager().Events() {
		if x.Type == typ {
			return true
		}
	}
	return false
}

func TestPendingExpiry(t *testing.T) {
	e := setup(t)
	a2 := addr("attestor2")
	e.setTTL(10, 2, e.gate, a2)
	hx := proofHash("O", "C")
	e.height(5)
	if _, err := e.attestAmt(e.gate, hx, "12.50", "USD"); err != nil {
		t.Fatal(err)
	}
	p, _ := e.k.Proofs.Get(e.ctx, hx)
	if p.ExpiresHeight != 15 {
		t.Fatal(p)
	}
	e.height(14)
	if err := e.k.EndBlocker(e.ctx); err != nil {
		t.Fatal(err)
	}
	if ok, _ := e.k.Proofs.Has(e.ctx, hx); !ok {
		t.Fatal("expired too early")
	}
	e.height(15)
	if err := e.k.EndBlocker(e.ctx); err != nil {
		t.Fatal(err)
	}
	if ok, _ := e.k.Proofs.Has(e.ctx, hx); ok {
		t.Fatal("not expired")
	}
	if !hasEvent(e, "proof_expired") {
		t.Fatal("no proof_expired event")
	}
	// re-attest after expiry recreates a fresh proof
	e.height(20)
	r, err := e.attestAmt(a2, hx, "12.50", "USD")
	if err != nil || r.Attestations != 1 || r.Status != types.PROOF_STATUS_PENDING {
		t.Fatal(r, err)
	}
	p, _ = e.k.Proofs.Get(e.ctx, hx)
	if p.FirstSeenHeight != 20 || p.ExpiresHeight != 30 || p.Submitter != a2 {
		t.Fatal(p)
	}
}

func TestConfirmedNeverExpires(t *testing.T) {
	e := setup(t)
	e.setTTL(10, 1, e.gate)
	hx := proofHash("O", "C")
	e.height(5)
	if r, err := e.attestAmt(e.gate, hx, "12.50", "USD"); err != nil || r.Status != types.PROOF_STATUS_CONFIRMED {
		t.Fatal(r, err)
	}
	e.height(1000)
	if err := e.k.EndBlocker(e.ctx); err != nil {
		t.Fatal(err)
	}
	if ok, _ := e.k.Proofs.Has(e.ctx, hx); !ok {
		t.Fatal("confirmed proof deleted")
	}
}

func TestTTLZeroNeverExpires(t *testing.T) {
	e := setup(t)
	a2 := addr("attestor2")
	e.setTTL(0, 2, e.gate, a2)
	hx := proofHash("O", "C")
	if _, err := e.attestAmt(e.gate, hx, "12.50", "USD"); err != nil {
		t.Fatal(err)
	}
	p, _ := e.k.Proofs.Get(e.ctx, hx)
	if p.ExpiresHeight != 0 {
		t.Fatal(p)
	}
	e.height(1_000_000)
	if err := e.k.EndBlocker(e.ctx); err != nil {
		t.Fatal(err)
	}
	if ok, _ := e.k.Proofs.Has(e.ctx, hx); !ok {
		t.Fatal("ttl 0 proof deleted")
	}
}
